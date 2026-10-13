"""Movement-ledger stock model: stock = SUM of signed movements.

A ledger profile reads a movements table (receipts, sales, returns,
adjustments, transfers, production, write-offs). The column mapping still
names the SKU, warehouse and quantity columns of that table; `model_config`
says how a movement's quantity is signed and which movements count.

Configuration (`model_config`, normalised by `normalize_config`):

    sign.mode        signed    the quantity column is already + / -
                     type_map  a movement-type column decides: {"REC": 1, "SALE": -1, "VOID": 0}
                     in_out    two columns, in and out: quantity = in - out
    sign.type_column, sign.type_map, sign.unknown ("zero" | "reject"), sign.in_column, sign.out_column
    id_column        unique movement id (required): exactly-once on incremental reads
    cursor_column    monotonic column to read after (id or posting time; default id_column)
    status_column + posted_values   only these movements count (posted vs draft/voided)
    as_of            only movements at or before this date (needs as_of_column)
    factor_column    per-row unit conversion multiplier (cases -> units)
    drift_policy     heal (default) | brake  — what a reconcile does when our running
                     balance and the source's SUM disagree

Two ways to compute the balance, kept honest against each other:

* **Full / reconcile** — the source computes `SUM(signed qty) GROUP BY key` for
  every movement up to a high-water cursor read first, so the result is a
  consistent cut even while movements keep arriving. The result REPLACES our
  running balances; a difference from what we had is DRIFT (late commits,
  back-dated or edited movements, deletes) — counted, sampled and alerted;
  `heal` applies the source's figure, `brake` stops the run for a person.
* **Incremental** — movements after the stored cursor, in cursor order, a
  bounded page at a time (keyset), each id applied once (an applied-ids store
  covers overlap re-reads, replays and duplicates), as deltas onto our running
  balance. New balances are pending until the run succeeds; a failed run
  leaves balances, cursor and stock exactly as they were.

Negative balances follow the profile's negative policy (reject the row, or
clamp to 0); the raw balance is kept, so the arithmetic stays exact.
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterator, Optional

from backend.db.connection import _json, execute, query, query_one
from backend.errors import AppError
from backend.inventory_sync import mapper

SIGN_MODES = ("signed", "type_map", "in_out")
DRIFT_POLICIES = ("heal", "brake")
DRIFT_TOLERANCE = 1e-6
INCREMENTAL_PAGE = 50_000
MAX_EXAMPLES = 3
MAX_EXAMPLE_MOVEMENTS = 20


def _bad(reason: str, **params: Any) -> AppError:
    return AppError("inventory_sync_model_invalid", f"The ledger configuration is not valid: {reason}.",
                    status_code=422, params={"reason": reason, **params})


def _col(cfg: dict, key: str, required: bool = False) -> Optional[str]:
    v = cfg.get(key)
    v = None if v in (None, "") else str(v).strip()
    if v is not None and (len(v) > 256 or "\x00" in v):
        raise _bad("column_invalid", field=key)
    if required and not v:
        raise _bad("column_required", field=key)
    return v


def normalize_config(cfg: dict, engine: str) -> dict:
    sign = cfg.get("sign") or {}
    if not isinstance(sign, dict):
        raise _bad("sign_not_object")
    mode = sign.get("mode") or "signed"
    if mode not in SIGN_MODES:
        raise _bad("sign_mode", value=str(mode)[:20])
    out_sign: dict[str, Any] = {"mode": mode}
    if mode == "type_map":
        out_sign["type_column"] = _col(sign, "type_column", True)
        tmap = sign.get("type_map") or {}
        if not isinstance(tmap, dict) or not tmap or len(tmap) > 500:
            raise _bad("type_map")
        clean = {}
        for k, v in tmap.items():
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise _bad("type_map_value", type=str(k)[:40])
            if f not in (-1.0, 0.0, 1.0):
                raise _bad("type_map_value", type=str(k)[:40])
            clean[str(k)[:100]] = int(f)
        out_sign["type_map"] = clean
        out_sign["unknown"] = "reject" if sign.get("unknown") == "reject" else "zero"
    elif mode == "in_out":
        out_sign["in_column"] = _col(sign, "in_column", True)
        out_sign["out_column"] = _col(sign, "out_column", True)
    posted = cfg.get("posted_values") or []
    if not isinstance(posted, list) or len(posted) > 50:
        raise _bad("posted_values")
    drift = cfg.get("drift_policy") or "heal"
    if drift not in DRIFT_POLICIES:
        raise _bad("drift_policy", value=str(drift)[:20])
    as_of = cfg.get("as_of")
    if as_of not in (None, ""):
        import re
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(as_of)):
            raise _bad("as_of", value=str(as_of)[:20])
    out = {
        "sign": out_sign,
        "id_column": _col(cfg, "id_column", True),
        "cursor_column": _col(cfg, "cursor_column") or _col(cfg, "id_column", True),
        "status_column": _col(cfg, "status_column"),
        "posted_values": [str(v)[:100] for v in posted],
        "as_of_column": _col(cfg, "as_of_column"),
        "as_of": str(as_of) if as_of not in (None, "") else None,
        "factor_column": _col(cfg, "factor_column"),
        "drift_policy": drift,
    }
    if out["as_of"] and not out["as_of_column"]:
        raise _bad("as_of_needs_column")
    if out["status_column"] and not out["posted_values"]:
        raise _bad("posted_values_required")
    return out


# ── The sign rule, once (Python side: incremental, push, examples, tests) ────

def signed_quantity(row: dict, mapping: dict, cfg: dict) -> float:
    """The signed contribution of one movement. Raises `mapper.Rejected`."""
    sign = cfg["sign"]
    spec = mapping.get("current_stock") or {}
    decimal = spec.get("decimal") or "auto"
    if sign["mode"] == "in_out":
        def n(col):
            v = mapper._lookup(row, col)
            return 0.0 if mapper._is_empty(v) else mapper.number(v, decimal)
        qty = n(sign["in_column"]) - n(sign["out_column"])
    else:
        raw = mapper._lookup(row, spec.get("column") or "")
        if mapper._is_empty(raw):
            raise mapper.Rejected("quantity_missing", field="current_stock")
        qty = mapper.number(raw, decimal)
        if sign["mode"] == "type_map":
            t = mapper._lookup(row, sign["type_column"])
            key = "" if t is None else mapper._as_text(t).strip()
            if key in sign["type_map"]:
                qty = qty * sign["type_map"][key]
            elif sign.get("unknown") == "reject":
                raise mapper.Rejected("unknown_movement_type", value=key[:40])
            else:
                qty = 0.0
    if spec.get("multiplier"):
        qty *= float(spec["multiplier"])
    if cfg.get("factor_column"):
        f = mapper._lookup(row, cfg["factor_column"])
        if not mapper._is_empty(f):
            qty *= mapper.number(f, decimal, "factor")
    if math.isnan(qty) or math.isinf(qty):
        raise mapper.Rejected("not_a_number", field="current_stock")
    return qty


def counts(row: dict, cfg: dict) -> bool:
    """Whether a movement counts (posted, not after the as-of date)."""
    if cfg.get("status_column"):
        v = mapper._lookup(row, cfg["status_column"])
        if ("" if v is None else mapper._as_text(v).strip()) not in cfg["posted_values"]:
            return False
    if cfg.get("as_of"):
        v = mapper._lookup(row, cfg["as_of_column"])
        if v is None or mapper._as_text(v)[:10] > cfg["as_of"]:
            return False
    return True


# ── SQL ──────────────────────────────────────────────────────────────────────

def _sql_parts(profile: dict, engine: str):
    from backend.datasources import catalog
    from backend.inventory_sync import reader, strategies
    cfg = profile["model_config"]
    mapping = profile["mapping"]
    q = lambda c: catalog.quote_ident(engine, c)  # noqa: E731
    lit = strategies._literal

    def num(col):
        return f"COALESCE(CAST(src.{q(col)} AS DECIMAL(38, 10)), 0)" if engine != "oracle" \
            else f"NVL(CAST(src.{q(col)} AS NUMBER), 0)"

    sign = cfg["sign"]
    qty_col = mapping["current_stock"]["column"]
    if sign["mode"] == "in_out":
        signed = f"({num(sign['in_column'])} - {num(sign['out_column'])})"
    elif sign["mode"] == "type_map":
        whens = []
        for k, v in sign["type_map"].items():
            l = lit(k)
            if l is None:
                raise _bad("type_map_value", type=k[:40])
            whens.append(f"WHEN {l} THEN {int(v)}")
        signed = f"(CASE src.{q(sign['type_column'])} {' '.join(whens)} ELSE 0 END) * {num(qty_col)}"
    else:
        signed = num(qty_col)
    if mapping["current_stock"].get("multiplier"):
        signed = f"({signed}) * {float(mapping['current_stock']['multiplier'])!r}"
    if cfg.get("factor_column"):
        signed = f"({signed}) * {num(cfg['factor_column'])}"
    preds = []
    if cfg.get("status_column"):
        vals = [lit(v) for v in cfg["posted_values"]]
        if any(v is None for v in vals):
            raise _bad("posted_values")
        preds.append(f"src.{q(cfg['status_column'])} IN ({', '.join(vals)})")
    if cfg.get("as_of"):
        preds.append(f"src.{q(cfg['as_of_column'])} <= {reader._sql_literal({'type': 'datetime', 'value': cfg['as_of'] + 'T23:59:59'}, engine)}")
    return q, signed, preds, qty_col


def _cursor_literal(cur: dict, engine: str) -> str:
    from backend.inventory_sync import reader
    return reader._sql_literal(cur, engine)


def full_sql(profile: dict, engine: str, cursor_hi: Optional[dict], scope: Optional[dict]) -> str:
    from backend.inventory_sync import reader, strategies
    q, signed, preds, qty_col = _sql_parts(profile, engine)
    mapping = profile["mapping"]
    cfg = profile["model_config"]
    if cursor_hi:
        preds.append(f"src.{q(cfg['cursor_column'])} <= {_cursor_literal(cursor_hi, engine)}")
    scoped = strategies.scope_predicate(profile, engine, scope)
    if scoped:
        preds.append("src." + scoped)
    keys = [mapping[f]["column"] for f in ("sku", "warehouse") if mapping.get(f, {}).get("column")]
    others = sorted({m["column"] for f, m in mapping.items()
                     if m.get("column") and m["column"] not in keys and m["column"] != qty_col
                     and f != "current_stock"})
    sel = [f"src.{q(k)} AS {q(k)}" for k in keys] + [f"SUM({signed}) AS {q(qty_col)}"]
    sel += [f"MAX(src.{q(c)}) AS {q(c)}" for c in others]
    where = f" WHERE {' AND '.join(preds)}" if preds else ""
    return (f"SELECT {', '.join(sel)} FROM ({reader.raw_select(profile, engine)}) src{where} "
            f"GROUP BY {', '.join(f'src.{q(k)}' for k in keys)}")


def max_cursor_sql(profile: dict, engine: str) -> str:
    from backend.inventory_sync import reader
    q, _signed, preds, _ = _sql_parts(profile, engine)
    where = f" WHERE {' AND '.join(preds)}" if preds else ""
    return (f"SELECT MAX(src.{q(profile['model_config']['cursor_column'])}) AS hi "
            f"FROM ({reader.raw_select(profile, engine)}) src{where}")


def movements_sql(profile: dict, engine: str, after: Optional[dict], limit: int,
                  scope: Optional[dict] = None, sku_values: Optional[list] = None,
                  include_uncounted: bool = False, until: Optional[dict] = None) -> str:
    """Movements after `after` in cursor order, one bounded page (keyset).

    The read never goes below the floor of the last full / reconcile run: what
    that run summed is in the balance already (a rewound cursor is clamped)."""
    from backend.inventory_sync import reader, strategies
    q, _signed, preds, _ = _sql_parts(profile, engine)
    if include_uncounted:
        preds = []
    cfg = profile["model_config"]
    cur = q(cfg["cursor_column"])
    floor = profile.get("ledger_floor")
    if floor and (after is None or (after.get("type") == floor.get("type")
                                    and reader._key(after) < reader._key(floor))):
        after = floor
    if until:
        preds.append(f"src.{cur} <= {_cursor_literal(until, engine)}")
    if after:
        if after.get("type") == "datetime":
            # Timestamps are not unique and commit out of order: re-read an
            # overlap window; the applied-ids store makes the overlap harmless.
            start = reader._start_point(after, int(profile.get("overlap_minutes") or 0))
            preds.append(f"src.{cur} >= {_cursor_literal(start, engine)}")
        else:
            preds.append(f"src.{cur} > {_cursor_literal(after, engine)}")
    if sku_values:
        col = q(profile["mapping"]["sku"]["column"])
        lits = [strategies._literal(v) for v in sku_values]
        preds.append(f"src.{col} IN ({', '.join(l for l in lits if l)})")
    where = f" WHERE {' AND '.join(preds)}" if preds else ""
    base = f"SELECT src.* FROM ({reader.raw_select(profile, engine)}) src{where} ORDER BY src.{cur}"
    if engine == "mssql":
        return base.replace("SELECT src.*", f"SELECT TOP ({int(limit)}) src.*", 1)
    if engine == "oracle":
        return f"{base} FETCH FIRST {int(limit)} ROWS ONLY"
    return f"{base} LIMIT {int(limit)}"


def _rows(tenant_id: str, profile: dict, sql: str, batch: int = 1000, max_rows: Optional[int] = None):
    from backend.datasources.service import _classified, _read_only_rows
    from backend.inventory_sync import reader
    cfg = reader._cfg(tenant_id, profile["data_source_id"])
    read = 0
    try:
        with _read_only_rows(cfg, sql, stream=True, tenant_id=tenant_id, bulk=True) as result:
            cols = [str(c) for c in result.keys()]
            while True:
                chunk = result.fetchmany(batch)
                if not chunk:
                    break
                read += len(chunk)
                if max_rows is not None and read > max_rows:
                    raise AppError("inventory_sync_too_many_rows",
                                   f"The source returned more than {max_rows} rows.", status_code=422,
                                   params={"max_rows": max_rows})
                yield [dict(zip(cols, r)) for r in chunk]
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _classified(exc, cfg) from None


# ── Running balances ─────────────────────────────────────────────────────────

class _Keys:
    """Raw -> canonical key resolution, cached (warehouse names canonicalised
    the same way staging does)."""

    def __init__(self, tenant_id: str, profile: dict):
        from backend.inventory import warehouse_service as wh_svc
        self.tenant_id, self.profile, self.wh = tenant_id, profile, wh_svc
        self.cache: dict[str, str] = {}

    def key(self, row: dict) -> tuple[str, str]:
        sku, wh = mapper.map_key(row, self.profile["mapping"],
                                 default_warehouse=self.profile.get("default_warehouse"))
        if wh not in self.cache:
            self.cache[wh] = self.wh.resolve_canonical_name(self.tenant_id, wh)
        return sku, self.cache[wh]


def _set_pending(tenant_id: str, profile_id: str, run_id: str, balances: dict) -> None:
    if not balances:
        return
    from psycopg2.extras import execute_values
    from backend.db.connection import transaction
    with transaction() as conn:
        with conn.cursor() as cur:
            execute_values(cur,
                           "INSERT INTO inventory_sync_ledger_balances (tenant_id, profile_id, sku, warehouse, "
                           "balance, pending_balance, pending_run) VALUES %s ON CONFLICT (profile_id, sku, warehouse) "
                           "DO UPDATE SET pending_balance = EXCLUDED.pending_balance, pending_run = EXCLUDED.pending_run",
                           [(tenant_id, profile_id, k[0], k[1], 0.0, v, run_id) for k, v in balances.items()],
                           template="(%s, %s, %s, %s, %s, %s, %s)", page_size=1000)


def commit(profile_id: str, run_id: str) -> None:
    """The run succeeded: pending balances and applied ids become real."""
    execute("UPDATE inventory_sync_ledger_balances SET balance = pending_balance, pending_balance = NULL, "
            "pending_run = NULL, updated_at = NOW() WHERE profile_id = %s AND pending_run = %s",
            (profile_id, run_id))
    execute("UPDATE inventory_sync_applied_movements SET pending_run = NULL WHERE profile_id = %s "
            "AND pending_run = %s", (profile_id, run_id))


def rollback(profile_id: str, run_id: str) -> None:
    """The run failed or braked: nothing it computed is kept."""
    execute("UPDATE inventory_sync_ledger_balances SET pending_balance = NULL, pending_run = NULL "
            "WHERE profile_id = %s AND pending_run = %s", (profile_id, run_id))
    execute("DELETE FROM inventory_sync_applied_movements WHERE profile_id = %s AND pending_run = %s",
            (profile_id, run_id))


def _out_row(profile: dict, raw: dict, balance: float) -> dict:
    """A row shaped like the mapping expects, carrying the balance as the quantity."""
    mapping = profile["mapping"]
    out = dict(raw)
    out[mapping["current_stock"]["column"]] = balance
    return out


def read_balances(tenant_id: str, profile: dict, *, run_id: Optional[str] = None,
                  scope: Optional[dict] = None, incremental: bool = False,
                  max_rows: Optional[int] = None, limit: Optional[int] = None,
                  report: Optional[dict] = None) -> Iterator[list[dict]]:
    """Yield mapping-shaped rows (one per key) carrying balances. Records the
    new cursor and drift in `report` (the engine's run state)."""
    from backend.inventory_sync import reader
    report = report if report is not None else {}
    engine = reader._cfg(tenant_id, profile["data_source_id"]).get("engine") or "postgresql"
    if engine == "mongodb":
        yield from _mongo_full(tenant_id, profile, run_id=run_id, report=report, limit=limit)
        return
    keys = _Keys(tenant_id, profile)
    # An unsaved draft (the form's preview) has no id: it reads, and writes
    # nothing — every use of `pid` below is behind `run_id`.
    pid = profile.get("id")
    if incremental and profile.get("cursor_value") and not scope:
        yield from _incremental(tenant_id, profile, engine, keys, run_id, report)
        return

    hi_rows = list(_rows(tenant_id, profile, max_cursor_sql(profile, engine)))
    hi_val = hi_rows[0][0]["hi"] if hi_rows and hi_rows[0] else None
    cursor_hi = reader.watermark_of(hi_val)
    stored = {} if run_id is None else {
        (r["sku"], r["warehouse"]): float(r["balance"])
        for r in query("SELECT sku, warehouse, balance FROM inventory_sync_ledger_balances "
                       "WHERE profile_id = %s AND pending_run IS NULL", (pid,))}
    first_full = not stored
    seen: set[tuple[str, str]] = set()
    drift, sample = 0, []
    sql = full_sql(profile, engine, cursor_hi, scope)
    yielded = 0
    for batch in _rows(tenant_id, profile, sql, max_rows=max_rows):
        out, pending = [], {}
        for raw in batch:
            qty = raw.get(profile["mapping"]["current_stock"]["column"])
            bal = 0.0 if qty is None else float(qty)
            try:
                k = keys.key(raw)
            except mapper.Rejected:
                out.append(raw)          # the mapper rejects it again, with its reason
                continue
            seen.add(k)
            pending[k] = bal
            if not first_full and abs(stored.get(k, 0.0) - bal) > DRIFT_TOLERANCE:
                drift += 1
                if len(sample) < 20:
                    sample.append({"sku": k[0], "warehouse": k[1], "stockai": stored.get(k, 0.0), "source": bal})
            out.append(_out_row(profile, raw, bal))
        if run_id:
            _set_pending(tenant_id, pid, run_id, pending)
        yielded += len(out)
        yield out
        if limit and yielded >= limit:
            return
    if not scope:
        # Keys we hold a balance for with no counted movement any more: zero.
        gone = {k: 0.0 for k in stored if k not in seen}
        for k in gone:
            if abs(stored[k]) > DRIFT_TOLERANCE:
                drift += 1
                if len(sample) < 20:
                    sample.append({"sku": k[0], "warehouse": k[1], "stockai": stored[k], "source": 0.0})
        if gone and run_id:
            _set_pending(tenant_id, pid, run_id, gone)
            m = profile["mapping"]
            rows = []
            for (sku, wh) in gone:
                r = {m["sku"]["column"]: sku, m["current_stock"]["column"]: 0.0}
                if m.get("warehouse", {}).get("column"):
                    r[m["warehouse"]["column"]] = wh
                rows.append(r)
            yield rows
    if cursor_hi and not scope:
        # The floor: incremental reads never re-read what this sum counted.
        report["floor"] = cursor_hi
        if cursor_hi.get("type") == "datetime" and run_id:
            # Timestamp cursors re-read an overlap window; the movements of the
            # window this sum already counted are marked applied, so the next
            # incremental does not count them twice.
            from backend.inventory_sync import reader as _r
            start = _r._start_point(cursor_hi, int(profile.get("overlap_minutes") or 0))
            ids = []
            for batch in _rows(tenant_id, profile, movements_sql(profile, engine, start, 1_000_000,
                                                                 include_uncounted=True, until=cursor_hi)):
                ids += [str(mapper._lookup(r, profile["model_config"]["id_column"])) for r in batch]
            if ids:
                from psycopg2.extras import execute_values
                from backend.db.connection import transaction
                with transaction() as conn:
                    with conn.cursor() as cur:
                        execute_values(cur, "INSERT INTO inventory_sync_applied_movements (tenant_id, profile_id, "
                                       "movement_id, pending_run) VALUES %s ON CONFLICT DO NOTHING",
                                       [(tenant_id, pid, m, run_id) for m in ids], page_size=1000)
    report["cursor_after"] = cursor_hi or profile.get("cursor_value")
    report["drift"] = drift
    report["drift_sample"] = sample


def _incremental(tenant_id, profile, engine, keys, run_id, report):
    """Movements after the cursor -> deltas on the running balance."""
    batches = _rows(tenant_id, profile, movements_sql(profile, engine, profile.get("cursor_value"),
                                                      INCREMENTAL_PAGE))
    yield from apply_movements(tenant_id, profile, batches, run_id, report, keys=keys)


def apply_movements(tenant_id, profile, batches, run_id, report, keys=None):
    """THE delta path, shared by incremental reads and push ingest: every
    movement id once (applied-ids store + this call), counted movements only,
    signed by the profile's rule, summed per key onto the running balance as
    PENDING balances (committed only if the run succeeds). Order does not
    matter: a sum is commutative; duplicates and replays are absorbed."""
    from backend.inventory_sync import reader
    keys = keys or _Keys(tenant_id, profile)
    pid = profile["id"]
    cfg = profile["model_config"]
    mapping = profile["mapping"]
    after = profile.get("cursor_value")
    deltas: dict[tuple[str, str], float] = {}
    raw_of: dict[tuple[str, str], dict] = {}
    rejected_rows: list[dict] = []
    new_ids: list[str] = []
    max_cur = after
    for batch in batches:
        ids = [str(mapper._lookup(r, cfg["id_column"])) for r in batch]
        done = {r["movement_id"] for r in query(
            "SELECT movement_id FROM inventory_sync_applied_movements WHERE profile_id = %s "
            "AND movement_id = ANY(%s) AND (pending_run IS NULL OR pending_run = %s)", (pid, ids, run_id))}
        for raw, mid in zip(batch, ids):
            max_cur = reader.max_watermark(max_cur, reader.watermark_of(mapper._lookup(raw, cfg["cursor_column"])))
            if mid in done or mid == "None":
                continue
            done.add(mid)
            if not counts(raw, cfg):
                continue
            try:
                k = keys.key(raw)
                d = signed_quantity(raw, mapping, cfg)
            except mapper.Rejected:
                rejected_rows.append(raw)
                continue
            new_ids.append(mid)
            deltas[k] = deltas.get(k, 0.0) + d
            raw_of.setdefault(k, raw)
    if new_ids and run_id:
        from psycopg2.extras import execute_values
        from backend.db.connection import transaction
        with transaction() as conn:
            with conn.cursor() as cur:
                execute_values(cur, "INSERT INTO inventory_sync_applied_movements (tenant_id, profile_id, "
                               "movement_id, pending_run) VALUES %s ON CONFLICT DO NOTHING",
                               [(tenant_id, pid, m, run_id) for m in new_ids], page_size=1000)
    stored = {}
    if deltas:
        ks = list(deltas)
        stored = {(r["sku"], r["warehouse"]): float(r["balance"]) for r in query(
            "SELECT b.sku, b.warehouse, b.balance FROM inventory_sync_ledger_balances b "
            "JOIN unnest(%s::text[], %s::text[]) AS x(sku, warehouse) ON b.sku = x.sku AND b.warehouse = x.warehouse "
            "WHERE b.profile_id = %s", ([k[0] for k in ks], [k[1] for k in ks], pid))}
    balances = {k: stored.get(k, 0.0) + d for k, d in deltas.items()}
    if run_id:
        _set_pending(tenant_id, pid, run_id, balances)
    out = [_out_row(profile, raw_of[k], b) for k, b in balances.items()] + rejected_rows
    report["cursor_after"] = max_cur
    report["movements"] = len(new_ids)
    if out:
        yield out


def _mongo_full(tenant_id, profile, *, run_id, report, limit):
    """MongoDB ledger: summed client-side while streaming (MongoDB's $sum skips
    non-numbers silently; a bad movement must be rejected, not dropped)."""
    from backend.inventory_sync import reader
    cfg = profile["model_config"]
    keys = _Keys(tenant_id, profile)
    sums: dict[tuple[str, str], float] = {}
    raw_of: dict[tuple[str, str], dict] = {}
    rejected = []
    max_cur = None
    for batch in reader.read_batches(tenant_id, {**profile, "stock_model": "snapshot", "mode": "full"}):
        for raw in batch:
            max_cur = reader.max_watermark(max_cur, reader.watermark_of(mapper._lookup(raw, cfg["cursor_column"])))
            if not counts(raw, cfg):
                continue
            try:
                k = keys.key(raw)
                sums[k] = sums.get(k, 0.0) + signed_quantity(raw, profile["mapping"], cfg)
                raw_of.setdefault(k, raw)
            except mapper.Rejected:
                rejected.append(raw)
    if run_id:
        _set_pending(tenant_id, profile["id"], run_id, sums)
    report["cursor_after"] = max_cur
    rows = [_out_row(profile, raw_of[k], v) for k, v in sums.items()] + rejected
    for i in range(0, len(rows), 1000):
        yield rows[i:i + 1000]


# ── Explaining the arithmetic ─────────────────────────────────────────────────

def explain(tenant_id: str, profile: dict, skus: list[str]) -> list[dict]:
    """For a few SKUs: their movements, each signed, and the total — what the
    dry run shows so the person can check it against the ERP."""
    from backend.inventory_sync import reader
    engine = reader._cfg(tenant_id, profile["data_source_id"]).get("engine") or "postgresql"
    cfg, mapping = profile["model_config"], profile["mapping"]
    out = []
    if engine == "mongodb" or not skus:
        return out
    rows: list[dict] = []
    for batch in _rows(tenant_id, profile, movements_sql(profile, engine, None, 500, sku_values=skus[:MAX_EXAMPLES],
                                                         include_uncounted=True)):
        rows.extend(batch)
    by: dict[str, list] = {}
    for r in rows:
        try:
            sku, wh = mapper.map_key(r, mapping, default_warehouse=profile.get("default_warehouse"))
        except mapper.Rejected:
            continue
        by.setdefault(f"{sku}\u0000{wh}", []).append(r)
    for key, mv in list(by.items())[:MAX_EXAMPLES]:
        sku, wh = key.split("\u0000")
        lines, total = [], 0.0
        for r in mv:
            counted = counts(r, cfg)
            try:
                s = signed_quantity(r, mapping, cfg) if counted else 0.0
            except mapper.Rejected as exc:
                lines.append({"id": str(mapper._lookup(r, cfg["id_column"])), "rejected": exc.code})
                continue
            total += s
            if len(lines) < MAX_EXAMPLE_MOVEMENTS:
                lines.append({"id": str(mapper._lookup(r, cfg["id_column"])),
                              "type": None if cfg["sign"]["mode"] != "type_map"
                              else mapper._as_text(mapper._lookup(r, cfg["sign"]["type_column"])),
                              "signed": s, "counted": counted})
        out.append({"sku": sku, "warehouse": wh, "movements": lines, "total": total,
                    "movement_count": len(mv)})
    return out


def reconcile_view(tenant_id: str, profile: dict, sku: str, warehouse: Optional[str]) -> dict:
    """StockAI says X, our running balance says Y, the source's SUM says Z,
    and the movements not applied yet (after our cursor)."""
    from backend.inventory_sync import reader
    engine = reader._cfg(tenant_id, profile["data_source_id"]).get("engine") or "postgresql"
    wh_filter = warehouse or ""
    stock = query_one("SELECT current_stock, warehouse FROM inventory_stock WHERE tenant_id = %s AND sku = %s "
                      "AND (%s = '' OR warehouse = %s) ORDER BY warehouse LIMIT 1",
                      (tenant_id, sku, wh_filter, wh_filter))
    wh = (stock or {}).get("warehouse") or warehouse
    bal = query_one("SELECT balance FROM inventory_sync_ledger_balances WHERE profile_id = %s AND sku = %s "
                    "AND warehouse = %s", (profile["id"], sku, wh)) if wh else None
    source_sum = None
    if engine != "mongodb":
        for batch in _rows(tenant_id, profile, full_sql(profile, engine, None, {"skus": [sku]})):
            for raw in batch:
                try:
                    k = _Keys(tenant_id, profile).key(raw)
                except mapper.Rejected:
                    continue
                if wh is None or k[1] == wh:
                    source_sum = float(raw.get(profile["mapping"]["current_stock"]["column"]) or 0)
    pending = explain(tenant_id, profile, [sku])
    stockai = float(stock["current_stock"]) if stock else None
    return {"sku": sku, "warehouse": wh, "stockai": stockai,
            "running_balance": float(bal["balance"]) if bal else None, "source_sum": source_sum,
            "difference": None if (stockai is None or source_sum is None) else source_sum - stockai,
            "cursor": profile.get("cursor_value"), "movements": pending}
