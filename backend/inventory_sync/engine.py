"""The dry run and the sync run.

A run has three phases and writes inventory only in the last one:

1. **Stage.** Read the source in batches (bounded memory), map and validate
   each row (`mapper`), resolve warehouses, and write the valid rows to
   `inventory_sync_staging`. A source that fails here — unreachable, timed
   out, too many rows, a query error — fails the run with inventory untouched.
2. **Brake.** Judge the staged set before applying it (see the package doc).
   A braked run changes nothing and raises a critical alert.
3. **Apply.** In batches, one transaction per batch under the tenant's limit
   lock: create new (sku, warehouse) rows, update only the fields whose value
   actually changed, leave identical rows alone (no write, no snapshot). Then
   the missing-SKU policy, then the watermark — which only ever moves on
   success.

The dry run is phases 1-2 on a bounded sample, in memory, writing nothing.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from backend.config import settings
from backend.db.connection import _json, execute, query, query_one
from backend.errors import AppError
from backend.inventory_sync import fields as F
from backend.inventory_sync import mapper
from backend.inventory_sync import reader
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

DRY_RUN_SAMPLE = 500
APPLY_BATCH = 500
MAX_SAMPLE = 50
LEASE_MINUTES = 30


def max_rows_per_run() -> int:
    return max(1, int(getattr(settings, "inventory_sync_max_rows_per_run", 200_000) or 200_000))


# ── Shared preparation ────────────────────────────────────────────────────────

class _Prep:
    """Turns raw rows into canonical rows + rejections, with the warehouse
    resolution cached per distinct spelling (one lookup per name, not per row)."""

    def __init__(self, tenant_id: str, profile: dict):
        from backend.inventory import warehouse_service as wh_svc
        self.tenant_id = tenant_id
        self.profile = profile
        self.mapping = profile.get("mapping") or {}
        self.filters = profile.get("filters") or []
        self._wh_svc = wh_svc
        self._canonical: dict[str, str] = {}
        self.known_warehouses = {n.lower() for n in wh_svc.list_warehouse_names(tenant_id)}
        self.rows_read = 0
        self.filtered_out = 0
        self.rejected = 0
        self.by_code: dict[str, int] = {}
        self.rejected_sample: list[dict] = []
        self.unknown_warehouses: set[str] = set()
        self.watermark: Optional[dict] = None

    def _reject(self, code: str, params: dict, sku: Any, row_number: int) -> None:
        self.rejected += 1
        self.by_code[code] = self.by_code.get(code, 0) + 1
        if len(self.rejected_sample) < MAX_SAMPLE:
            self.rejected_sample.append(mapper.rejection(code, params, sku=sku, row_number=row_number))

    def warehouse(self, name: str) -> str:
        if name not in self._canonical:
            self._canonical[name] = self._wh_svc.resolve_canonical_name(self.tenant_id, name)
        return self._canonical[name]

    def rows(self, batch: list[dict]) -> list[dict]:
        out = []
        wm_col = self.profile.get("watermark_column")
        for raw in batch:
            self.rows_read += 1
            line = self.rows_read
            if wm_col:
                self.watermark = reader.max_watermark(self.watermark,
                                                      reader.watermark_of(mapper._lookup(raw, wm_col)))
            if not mapper.passes_filters(raw, self.filters):
                self.filtered_out += 1
                continue
            try:
                row = mapper.map_row(raw, self.mapping,
                                     default_warehouse=self.profile.get("default_warehouse"),
                                     negative_policy=self.profile.get("negative_policy") or "reject")
            except mapper.Rejected as exc:
                self._reject(exc.code, exc.params, mapper.sku_hint(raw, self.mapping), line)
                continue
            row["warehouse"] = self.warehouse(row["warehouse"])
            if (not self.profile.get("allow_new_warehouses")
                    and row["warehouse"].lower() not in self.known_warehouses):
                self.unknown_warehouses.add(row["warehouse"])
                self._reject("unknown_warehouse", {"warehouse": row["warehouse"]}, row["sku"], line)
                continue
            row["_line"] = line
            out.append(row)
        return out

    def summary(self) -> dict:
        return {
            "rows_read": self.rows_read, "filtered_out": self.filtered_out,
            "rejected": self.rejected, "rejected_by_code": dict(self.by_code),
            "rejected_sample": self.rejected_sample,
            "unknown_warehouses": sorted(self.unknown_warehouses)[:50],
        }


def _existing(tenant_id: str, keys: list[tuple[str, str]], conn=None) -> dict[tuple[str, str], dict]:
    if not keys:
        return {}
    skus = [k[0] for k in keys]
    whs = [k[1] for k in keys]
    rows = query(
        "SELECT s.* FROM inventory_stock s "
        "JOIN unnest(%s::text[], %s::text[]) AS k(sku, warehouse) "
        "  ON s.sku = k.sku AND s.warehouse = k.warehouse "
        "WHERE s.tenant_id = %s",
        (skus, whs, tenant_id), conn=conn)
    return {(r["sku"], r["warehouse"]): r for r in rows}


def _changes(new: dict, current: Optional[dict]) -> dict:
    """Fields of `new` whose value differs from the stored row."""
    if current is None:
        return {k: v for k, v in new.items() if k not in ("sku", "_line")}
    out = {}
    for field in F.owned_fields(new):
        if field in new and not mapper.same_value(new[field], current.get(field)):
            out[field] = new[field]
    return out


def _merge_duplicate(first: dict, second: dict, policy: str) -> Optional[dict]:
    """Two rows for one key: summed under `sum`, a conflict (None) under `reject`."""
    if policy != "sum":
        return None
    merged = {**first, **{k: v for k, v in second.items() if k != "current_stock"}}
    merged["current_stock"] = float(first.get("current_stock") or 0) + float(second.get("current_stock") or 0)
    return merged


# ── Dry run ───────────────────────────────────────────────────────────────────

def dry_run(tenant_id: str, profile: dict, *, sample: int = DRY_RUN_SAMPLE) -> dict:
    """What a run would do with the first `sample` rows. Writes nothing."""
    started = time.monotonic()
    prep = _Prep(tenant_id, profile)
    valid: dict[tuple[str, str], dict] = {}
    conflicts: set[tuple[str, str]] = set()
    policy = profile.get("duplicate_policy") or "reject"
    for batch in reader.read_batches(tenant_id, profile, limit=sample, batch_size=min(sample, 1000)):
        for row in prep.rows(batch):
            key = (row["sku"], row["warehouse"])
            if key in conflicts:
                prep._reject("duplicate_key", {"warehouse": key[1]}, key[0], row["_line"])
                continue
            if key in valid:
                merged = _merge_duplicate(valid[key], row, policy)
                if merged is None:
                    conflicts.add(key)
                    first = valid.pop(key)
                    prep._reject("duplicate_key", {"warehouse": key[1]}, key[0], first["_line"])
                    prep._reject("duplicate_key", {"warehouse": key[1]}, key[0], row["_line"])
                else:
                    valid[key] = merged
                continue
            valid[key] = row

    existing = _existing(tenant_id, list(valid.keys()))
    creates, updates, unchanged = 0, 0, 0
    changes_sample: list[dict] = []
    for key, row in valid.items():
        current = existing.get(key)
        changed = _changes(row, current)
        if current is None:
            creates += 1
        elif changed:
            updates += 1
        else:
            unchanged += 1
        if (current is None or changed) and len(changes_sample) < MAX_SAMPLE:
            changes_sample.append({
                "sku": key[0], "warehouse": key[1], "action": "create" if current is None else "update",
                "fields": {f: {"before": None if current is None else _plain(current.get(f)),
                               "after": _plain(v)} for f, v in changed.items() if f != "warehouse"},
            })
    preview = [{k: _plain(v) for k, v in r.items() if k != "_line"} for r in list(valid.values())[:20]]
    return {
        **prep.summary(),
        "valid": len(valid), "would_create": creates, "would_update": updates,
        "would_leave_unchanged": unchanged, "changes_sample": changes_sample,
        "preview_rows": preview, "sample_limit": sample,
        "sample_truncated": prep.rows_read >= sample,
        "owned_fields": F.owned_fields(profile.get("mapping") or {}),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _plain(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


# ── The run ───────────────────────────────────────────────────────────────────

def start_run(tenant_id: str, profile_id: str, trigger: str, *, user_id: Optional[str] = None,
              worker: Optional[str] = None) -> str:
    run_id = generate_id("isr")
    execute(
        "INSERT INTO inventory_sync_runs (id, tenant_id, profile_id, trigger_kind, status, "
        "triggered_by, worker_id) VALUES (%s,%s,%s,%s,'running',%s,%s)",
        (run_id, tenant_id, profile_id, trigger, user_id, worker))
    execute("UPDATE inventory_sync_profiles SET last_run_id = %s WHERE id = %s", (run_id, profile_id))
    return run_id


class _Brake(Exception):
    def __init__(self, code: str, **params: Any):
        super().__init__(code)
        self.code = code
        self.params = params


def _renew_lease(profile_id: str, owner: Optional[str]) -> None:
    if owner:
        execute("UPDATE inventory_sync_profiles SET lease_until = NOW() + %s * INTERVAL '1 minute' "
                "WHERE id = %s AND lease_owner = %s", (LEASE_MINUTES, profile_id, owner))


def _stage_batch(tenant_id: str, run_id: str, prep: "_Prep", rows: list[dict], policy: str) -> None:
    """Write one batch of canonical rows to staging in a single statement.

    Duplicates inside the batch are folded in Python first (one INSERT may not
    touch a row twice); duplicates across batches are caught by ON CONFLICT."""
    from psycopg2.extras import execute_values
    from backend.db.connection import transaction

    unique: dict[tuple[str, str], dict] = {}
    dup_keys: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["sku"], row["warehouse"])
        data = {k: v for k, v in row.items() if k not in ("sku", "warehouse", "_line")}
        if key in unique:
            if policy == "sum":
                merged = _merge_duplicate(unique[key], data, "sum")
                unique[key] = merged
            else:
                dup_keys.add(key)
                prep._reject("duplicate_key", {"warehouse": key[1]}, key[0], row["_line"])
            continue
        unique[key] = data
    if not unique:
        return
    values = [(run_id, tenant_id, k[0], k[1], _json(v)) for k, v in unique.items()]
    with transaction() as conn:
        with conn.cursor() as cur:
            if policy == "sum":
                execute_values(
                    cur,
                    "INSERT INTO inventory_sync_staging (run_id, tenant_id, sku, warehouse, data) "
                    "VALUES %s ON CONFLICT (run_id, sku, warehouse) DO UPDATE SET "
                    "data = inventory_sync_staging.data || EXCLUDED.data || jsonb_build_object("
                    "'current_stock', COALESCE((inventory_sync_staging.data->>'current_stock')::float8, 0) "
                    "+ COALESCE((EXCLUDED.data->>'current_stock')::float8, 0))",
                    values, page_size=1000)
                return
            hits = execute_values(
                cur,
                "INSERT INTO inventory_sync_staging (run_id, tenant_id, sku, warehouse, data) "
                "VALUES %s ON CONFLICT (run_id, sku, warehouse) DO UPDATE SET duplicate = TRUE "
                "RETURNING sku, warehouse, (xmax <> 0) AS was_duplicate",
                values, page_size=1000, fetch=True)
            for hit in hits:
                hit = dict(hit) if not isinstance(hit, dict) else hit
                if hit["was_duplicate"]:
                    prep._reject("duplicate_key", {"warehouse": hit["warehouse"]}, hit["sku"], None)
            if dup_keys:
                cur.execute(
                    "UPDATE inventory_sync_staging s SET duplicate = TRUE "
                    "FROM unnest(%s::text[], %s::text[]) AS x(sku, warehouse) "
                    "WHERE s.run_id = %s AND s.sku = x.sku AND s.warehouse = x.warehouse",
                    ([k[0] for k in dup_keys], [k[1] for k in dup_keys], run_id))


def _stage(tenant_id: str, profile: dict, run_id: str, lease_owner: Optional[str]) -> tuple[_Prep, int]:
    prep = _Prep(tenant_id, profile)
    policy = profile.get("duplicate_policy") or "reject"
    watermark = profile.get("watermark_value") if profile.get("mode") == "incremental" else None
    for batch in reader.read_batches(tenant_id, profile, watermark=watermark,
                                     max_rows=max_rows_per_run()):
        _stage_batch(tenant_id, run_id, prep, prep.rows(batch), policy)
        _renew_lease(profile["id"], lease_owner)
    # Under `reject`, the FIRST row of every duplicated key is rejected too:
    # nothing about the source says which of the two is right.
    first_rows = 0
    if policy != "sum":
        first_rows = query_one("SELECT COUNT(*) AS n FROM inventory_sync_staging "
                               "WHERE run_id = %s AND duplicate", (run_id,))["n"]
        prep.rejected += first_rows
        if first_rows:
            prep.by_code["duplicate_key"] = prep.by_code.get("duplicate_key", 0) + first_rows
    valid = query_one("SELECT COUNT(*) AS n FROM inventory_sync_staging "
                      "WHERE run_id = %s AND NOT duplicate", (run_id,))["n"]
    return prep, int(valid)


def _vanished_count(profile_id: str, run_id: str) -> tuple[int, int]:
    row = query_one(
        "SELECT COUNT(*) AS known, "
        "       COUNT(*) FILTER (WHERE s.sku IS NULL) AS vanished "
        "FROM inventory_sync_keys k "
        "LEFT JOIN inventory_sync_staging s "
        "  ON s.run_id = %s AND s.sku = k.sku AND s.warehouse = k.warehouse AND NOT s.duplicate "
        "WHERE k.profile_id = %s AND NOT k.inactive",
        (run_id, profile_id))
    return int(row["known"]), int(row["vanished"])


def _check_brake(profile: dict, prep: _Prep, valid: int, run_id: str) -> dict:
    """Raise `_Brake` when the staged set looks like a broken source. Returns
    the vanished/known figures for the run log either way."""
    considered = prep.rows_read - prep.filtered_out
    full = profile.get("mode") != "incremental"
    known, vanished = _vanished_count(profile["id"], run_id) if full else (0, 0)
    stats = {"known": known, "vanished": vanished}
    if full and prep.rows_read == 0:
        raise _Brake("source_empty")
    if considered > 0 and prep.rejected * 100.0 / considered > float(profile.get("brake_max_rejected_pct") or 50):
        raise _Brake("too_many_rejected", rejected=prep.rejected, rows=considered,
                     max_pct=float(profile.get("brake_max_rejected_pct") or 50))
    if full and valid == 0:
        raise _Brake("no_valid_rows", rows=considered)
    if full and known > 0:
        pct = vanished * 100.0 / known
        if pct > float(profile.get("brake_max_vanished_pct") or 0):
            raise _Brake("skus_vanished", vanished=vanished, known=known, pct=round(pct, 1),
                         max_pct=float(profile.get("brake_max_vanished_pct") or 0))
    if full:
        prev = query_one(
            "SELECT rows_valid FROM inventory_sync_runs WHERE profile_id = %s AND status = 'success' "
            "AND id <> %s ORDER BY started_at DESC LIMIT 1", (profile["id"], run_id))
        ratio = float(profile.get("brake_min_row_ratio") or 0)
        if prev and prev["rows_valid"] and valid < prev["rows_valid"] * ratio:
            raise _Brake("row_count_drop", rows=valid, previous=int(prev["rows_valid"]), min_ratio=ratio)
    return stats


def _precheck_limits(tenant_id: str, run_id: str) -> None:
    """Refuse the whole run BEFORE the first write when it would cross the
    plan's SKU or warehouse ceiling, instead of failing halfway."""
    from backend.entitlements.service import enforce_limit, limit_guard
    from backend.inventory import service as svc
    from backend.inventory import warehouse_service as wh_svc
    new_keys = query_one(
        "SELECT COUNT(*) AS n FROM inventory_sync_staging s WHERE s.run_id = %s AND NOT s.duplicate "
        "AND NOT EXISTS (SELECT 1 FROM inventory_stock i WHERE i.tenant_id = s.tenant_id "
        "AND i.sku = s.sku AND i.warehouse = s.warehouse)", (run_id,))["n"]
    new_wh = query_one(
        "SELECT COUNT(DISTINCT s.warehouse) AS n FROM inventory_sync_staging s "
        "WHERE s.run_id = %s AND NOT s.duplicate AND NOT EXISTS (SELECT 1 FROM warehouses w "
        "WHERE w.tenant_id = s.tenant_id AND w.name = s.warehouse)", (run_id,))["n"]
    with limit_guard(tenant_id) as conn:
        if new_keys:
            enforce_limit(tenant_id, "max_skus", svc.count_stock(tenant_id, conn=conn),
                          adding=int(new_keys), conn=conn)
        if new_wh:
            enforce_limit(tenant_id, "max_locations", wh_svc.count_warehouses(tenant_id),
                          adding=int(new_wh), conn=conn)


def _apply(tenant_id: str, profile: dict, run_id: str, counters: dict,
           lease_owner: Optional[str]) -> None:
    from backend.entitlements.service import limit_guard
    from backend.inventory import service as svc
    from backend.inventory.defaults import SOURCE_FILE

    owned = F.owned_fields(profile.get("mapping") or {})
    while True:
        batch = query(
            "SELECT sku, warehouse, data FROM inventory_sync_staging "
            "WHERE run_id = %s AND NOT duplicate AND NOT applied "
            "ORDER BY sku, warehouse LIMIT %s", (run_id, APPLY_BATCH))
        if not batch:
            return
        keys = [(r["sku"], r["warehouse"]) for r in batch]
        with limit_guard(tenant_id) as conn:
            existing = _existing(tenant_id, keys, conn=conn)
            ledger = {(r["sku"], r["warehouse"]): r for r in query(
                "SELECT k.sku, k.warehouse, k.synced_values FROM inventory_sync_keys k "
                "JOIN unnest(%s::text[], %s::text[]) AS x(sku, warehouse) "
                "  ON k.sku = x.sku AND k.warehouse = x.warehouse WHERE k.profile_id = %s",
                ([k[0] for k in keys], [k[1] for k in keys], profile["id"]), conn=conn)}
            for r in batch:
                key = (r["sku"], r["warehouse"])
                new = {"sku": r["sku"], "warehouse": r["warehouse"], **(r["data"] or {})}
                current = existing.get(key)
                changed = _changes(new, current)
                if current is None:
                    data = {k: v for k, v in changed.items()}
                    svc.upsert_stock(tenant_id, r["sku"], data, conn=conn, source=SOURCE_FILE)
                    counters["created"] += 1
                elif changed:
                    previous = (ledger.get(key) or {}).get("synced_values") or {}
                    for field in changed:
                        # The field was edited since the last sync wrote it:
                        # the source wins, and the overwrite is reported.
                        if field in previous and not mapper.same_value(previous[field], current.get(field)):
                            counters["manual_overwritten"] += 1
                            if len(counters["overwritten_sample"]) < MAX_SAMPLE:
                                counters["overwritten_sample"].append({
                                    "sku": r["sku"], "warehouse": r["warehouse"], "field": field,
                                    "manual_value": _plain(current.get(field)),
                                    "source_value": _plain(changed[field])})
                    svc.upsert_stock(tenant_id, r["sku"], {**changed, "warehouse": r["warehouse"]},
                                     conn=conn, source=SOURCE_FILE)
                    counters["updated"] += 1
                else:
                    counters["unchanged"] += 1
                synced = {f: _plain(new.get(f)) for f in owned if f in new}
                execute(
                    "INSERT INTO inventory_sync_keys (tenant_id, profile_id, sku, warehouse, "
                    "synced_values, last_seen_run, last_seen_at, inactive) "
                    "VALUES (%s,%s,%s,%s,%s,%s,NOW(),FALSE) "
                    "ON CONFLICT (profile_id, sku, warehouse) DO UPDATE SET "
                    "synced_values = inventory_sync_keys.synced_values || EXCLUDED.synced_values, "
                    "last_seen_run = EXCLUDED.last_seen_run, last_seen_at = NOW(), inactive = FALSE",
                    (tenant_id, profile["id"], r["sku"], r["warehouse"], _json(synced), run_id),
                    conn=conn)
            execute(
                "UPDATE inventory_sync_staging SET applied = TRUE FROM unnest(%s::text[], %s::text[]) "
                "AS x(sku, warehouse) WHERE run_id = %s AND inventory_sync_staging.sku = x.sku "
                "AND inventory_sync_staging.warehouse = x.warehouse",
                ([k[0] for k in keys], [k[1] for k in keys], run_id), conn=conn)
        _renew_lease(profile["id"], lease_owner)


def _apply_missing(tenant_id: str, profile: dict, run_id: str, counters: dict) -> None:
    """The missing-SKU policy (full snapshots only). Never deletes."""
    from backend.entitlements.service import limit_guard
    from backend.inventory import service as svc
    from backend.inventory.defaults import SOURCE_FILE
    missing = query(
        "SELECT k.sku, k.warehouse FROM inventory_sync_keys k "
        "WHERE k.profile_id = %s AND NOT k.inactive AND NOT EXISTS ("
        "  SELECT 1 FROM inventory_sync_staging s WHERE s.run_id = %s AND s.sku = k.sku "
        "  AND s.warehouse = k.warehouse AND NOT s.duplicate)",
        (profile["id"], run_id))
    counters["missing"] = len(missing)
    policy = profile.get("missing_policy") or "ignore"
    if not missing or policy == "ignore":
        return
    for start in range(0, len(missing), APPLY_BATCH):
        chunk = missing[start:start + APPLY_BATCH]
        with limit_guard(tenant_id) as conn:
            if policy == "zero":
                existing = _existing(tenant_id, [(m["sku"], m["warehouse"]) for m in chunk], conn=conn)
                for m in chunk:
                    cur = existing.get((m["sku"], m["warehouse"]))
                    if cur is not None and not mapper.same_value(cur.get("current_stock"), 0):
                        svc.upsert_stock(tenant_id, m["sku"], {"current_stock": 0.0,
                                                               "warehouse": m["warehouse"]},
                                         conn=conn, source=SOURCE_FILE)
                        counters["missing_applied"] += 1
            else:  # mark_inactive
                counters["missing_applied"] += len(chunk)
            execute(
                "UPDATE inventory_sync_keys SET inactive = %s FROM unnest(%s::text[], %s::text[]) "
                "AS x(sku, warehouse) WHERE profile_id = %s AND inventory_sync_keys.sku = x.sku "
                "AND inventory_sync_keys.warehouse = x.warehouse",
                (policy == "mark_inactive", [m["sku"] for m in chunk], [m["warehouse"] for m in chunk],
                 profile["id"]), conn=conn)


def _error_of(exc: BaseException) -> tuple[str, dict]:
    if isinstance(exc, AppError):
        return exc.code, dict(exc.params or {})
    try:
        from fastapi import HTTPException
        if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
            detail = dict(exc.detail)
            code = str(detail.pop("code", "") or "plan_limit_reached")
            return code, {k: v for k, v in detail.items() if isinstance(v, (int, float, str))}
    except Exception:  # noqa: BLE001
        pass
    return "inventory_sync_internal_error", {"type": type(exc).__name__}


def _event(tenant_id: str, action: str, profile: dict, run_id: str, *, details: dict,
           reason: Optional[str] = None, reason_params: Optional[dict] = None,
           user_id: Optional[str] = None) -> None:
    from backend.activity.events import record_event
    record_event(tenant_id, user_id or "system", action, resource=profile["id"],
                 details={"profile_name": profile.get("name"), **details},
                 reason=reason, reason_params=reason_params,
                 status="success" if action.endswith("succeeded") else "error")


def execute_run(tenant_id: str, profile_id: str, run_id: str, *, override_brake: bool = False,
                lease_owner: Optional[str] = None, user_id: Optional[str] = None) -> dict:
    """Run one sync. Always finishes the run row (success / failed / braked)
    and never raises for a source or data problem — the outcome is the row."""
    started = time.monotonic()
    profile = query_one("SELECT * FROM inventory_sync_profiles WHERE id = %s AND tenant_id = %s",
                        (profile_id, tenant_id))
    if not profile:
        raise AppError("inventory_sync_profile_not_found", "Sync profile not found.", status_code=404)
    counters: dict[str, Any] = {"created": 0, "updated": 0, "unchanged": 0, "missing": 0,
                                "missing_applied": 0, "manual_overwritten": 0,
                                "overwritten_sample": []}
    prep: Optional[_Prep] = None
    valid = 0
    status, error_code, error_params = "success", None, None
    watermark_after = profile.get("watermark_value")
    try:
        prep, valid = _stage(tenant_id, profile, run_id, lease_owner)
        if not override_brake:
            _check_brake(profile, prep, valid, run_id)
        _precheck_limits(tenant_id, run_id)
        _apply(tenant_id, profile, run_id, counters, lease_owner)
        if profile.get("mode") != "incremental":
            _apply_missing(tenant_id, profile, run_id, counters)
        if profile.get("mode") == "incremental":
            watermark_after = reader.max_watermark(profile.get("watermark_value"), prep.watermark)
    except _Brake as brake:
        status, error_code, error_params = "braked", f"brake_{brake.code}", brake.params
    except Exception as exc:  # noqa: BLE001 - recorded on the run row, never swallowed silently
        status = "failed"
        error_code, error_params = _error_of(exc)
        if error_code == "inventory_sync_internal_error":
            log.exception("inventory sync run %s failed", run_id)
        else:
            log.warning("inventory sync run %s failed: %s", run_id, error_code)

    duration = int((time.monotonic() - started) * 1000)
    summary = prep.summary() if prep else {"rows_read": 0, "rejected": 0, "rejected_sample": []}
    try:
        execute("DELETE FROM inventory_sync_staging WHERE run_id = %s", (run_id,))
    except Exception:  # noqa: BLE001
        log.exception("could not clear staging for run %s", run_id)
    execute(
        """UPDATE inventory_sync_runs SET status=%s, finished_at=NOW(), duration_ms=%s,
             rows_read=%s, rows_valid=%s, created=%s, updated=%s, unchanged=%s, rejected=%s,
             missing=%s, missing_applied=%s, manual_overwritten=%s, error_code=%s,
             error_params=%s, watermark_before=%s, watermark_after=%s, rejected_sample=%s,
             overwritten_sample=%s, brake_overridden=%s
           WHERE id=%s""",
        (status, duration, summary["rows_read"], valid, counters["created"], counters["updated"],
         counters["unchanged"], summary["rejected"], counters["missing"],
         counters["missing_applied"], counters["manual_overwritten"], error_code,
         _json(error_params) if error_params is not None else None,
         _json(profile.get("watermark_value")) if profile.get("watermark_value") else None,
         _json(watermark_after) if (status == "success" and watermark_after) else None,
         _json(summary.get("rejected_sample") or []), _json(counters["overwritten_sample"]),
         bool(override_brake), run_id),
    )
    previous_status = profile.get("last_status")
    if status == "success":
        execute(
            "UPDATE inventory_sync_profiles SET last_status='success', last_error_code=NULL, "
            "last_success_at=NOW(), consecutive_failures=0, stale_alerted_at=NULL, "
            "watermark_value=%s, updated_at=NOW() WHERE id=%s",
            (_json(watermark_after) if watermark_after else None, profile_id))
        _event(tenant_id, "inventory_sync.run_succeeded", profile, run_id, user_id=user_id,
               details={"created": counters["created"], "updated": counters["updated"],
                        "unchanged": counters["unchanged"], "rejected": summary["rejected"]})
        if counters["manual_overwritten"]:
            _event(tenant_id, "inventory_sync.manual_edits_overwritten", profile, run_id,
                   user_id=user_id, details={"count": counters["manual_overwritten"]},
                   reason="inventory_sync_source_wins",
                   reason_params={"count": counters["manual_overwritten"]})
    elif status == "braked":
        execute("UPDATE inventory_sync_profiles SET last_status='braked', last_error_code=%s, "
                "updated_at=NOW() WHERE id=%s", (error_code, profile_id))
        # One alert per episode, not one per scheduled attempt.
        if previous_status != "braked" or profile.get("last_error_code") != error_code:
            _event(tenant_id, "inventory_sync.braked", profile, run_id, user_id=user_id,
                   details={},
                   reason="inventory_sync_safety_brake",
                   reason_params={"brake": error_code, **(error_params or {})})
    else:
        execute("UPDATE inventory_sync_profiles SET last_status='failed', last_error_code=%s, "
                "consecutive_failures = consecutive_failures + 1, updated_at=NOW() WHERE id=%s",
                (error_code, profile_id))
        if previous_status != "failed" or profile.get("last_error_code") != error_code:
            _event(tenant_id, "inventory_sync.run_failed", profile, run_id, user_id=user_id,
                   details={},
                   reason="inventory_sync_run_failed",
                   reason_params={"error_code": error_code, **(error_params or {})})
    return query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (run_id,))
