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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Optional

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
# Rows per staging INSERT statement: round trips, not rows, dominated a 1M-row
# run (1 747 statements for 200k rows at 1 000 per page).
STAGE_PAGE = 5000
MAX_SAMPLE = 50
# Rejected rows kept per run: enough for the run-detail CSV a developer
# downloads, bounded so a run log never grows with the source.
MAX_REJECTED_SAMPLE = 500
LEASE_MINUTES = 30
# A cell larger than this is not a stock value: it is a blob, a corrupt export
# or an attack. The row is rejected by name; the cell is never stored.
MAX_CELL_CHARS = 65_536


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
        self.seen_columns: set[str] = set()
        # What the reader reports back (ledger cursor, drift, movements).
        self.extras: dict = {}
        # Warehouse / supplier masters (masters.py): code -> name, remembered links.
        from backend.inventory_sync import masters as _masters
        self._masters = _masters
        self.unknown_policy = _masters.effective_unknown_policy(profile)
        self.wh_master: dict = {}
        self.wh_collisions: list = []
        self.sup_master: dict = {}
        self.sup_collisions: list = []
        self.wh_links: dict = {}
        self.sup_links: dict = {}
        if (profile.get("masters") or {}).get("warehouses"):
            self.wh_master, self.wh_collisions = _masters.load(tenant_id, profile, "warehouses")
        if (profile.get("masters") or {}).get("suppliers"):
            self.sup_master, self.sup_collisions = _masters.load(tenant_id, profile, "suppliers")
        if profile.get("id"):
            self.wh_links = _masters.aliases(profile["id"], "warehouses") if self.wh_master else {}
            self.sup_links = _masters.aliases(profile["id"], "suppliers") if self.sup_master else {}
        self.wh_seen: dict[str, dict] = {}
        self.wh_new: set[str] = set()
        self.wh_renamed: dict[str, dict] = {}
        self.wh_inactive: set[str] = set()
        self.wh_defaulted = 0
        self.wh_totals: dict[str, list] = {}

    def _reject(self, code: str, params: dict, sku: Any, row_number: int) -> None:
        self.rejected += 1
        self.by_code[code] = self.by_code.get(code, 0) + 1
        if len(self.rejected_sample) < MAX_REJECTED_SAMPLE:
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
            self.seen_columns.update(raw.keys())
            big = next((k for k, v in raw.items()
                        if isinstance(v, (str, bytes)) and len(v) > MAX_CELL_CHARS), None)
            if big is not None:
                self._reject("cell_too_large", {"column": str(big)[:80], "max": MAX_CELL_CHARS},
                             mapper.sku_hint({k: v for k, v in raw.items() if k != big}, self.mapping), line)
                continue
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
            listed = False
            if self.wh_master:
                code_key = self._masters.norm_code(row["warehouse"])
                listed = code_key in self.wh_master or code_key in self.wh_links
                row["warehouse"] = self._master_warehouse(row["warehouse"])
            if self.sup_master and row.get("supplier") is not None:
                name = self._masters.supplier_name_for(self.sup_master, self.sup_links, row["supplier"])
                if name is None and (self.profile.get("masters") or {}).get("suppliers", {}).get("unknown") == "reject":
                    self._reject("unknown_supplier", {"supplier": str(row["supplier"])[:80]}, row["sku"], line)
                    continue
                row["supplier"] = name or row["supplier"]
            row["warehouse"] = self.warehouse(row["warehouse"])
            if row["warehouse"].lower() not in self.known_warehouses:
                # A code the warehouse master lists is a real warehouse of the
                # customer: it is created whatever the policy (the policy is for
                # codes nobody vouches for), within the plan's ceiling.
                if listed:
                    self.wh_new.add(row["warehouse"])
                elif self.unknown_policy == "default":
                    from backend.inventory import warehouse_service as wh_svc
                    fallback = (self.profile.get("default_warehouse") or "").strip() \
                        or wh_svc.get_default_warehouse_name(self.tenant_id) or mapper.DEFAULT_WAREHOUSE
                    row["warehouse"] = self.warehouse(fallback)
                    self.wh_defaulted += 1
                elif self.unknown_policy == "create":
                    self.wh_new.add(row["warehouse"])
                else:
                    self.unknown_warehouses.add(row["warehouse"])
                    self._reject("unknown_warehouse", {"warehouse": row["warehouse"]}, row["sku"], line)
                    continue
            tot = self.wh_totals.setdefault(row["warehouse"], [0, 0.0])
            tot[0] += 1
            tot[1] += float(row.get("current_stock") or 0)
            row["_line"] = line
            out.append(row)
        return out

    def _master_warehouse(self, value: Any) -> str:
        """A row's warehouse code -> the StockAI warehouse name, through the
        remembered link first (a source rename keeps StockAI's name), then the
        master; an unlisted code stays as it came (the unknown policy decides)."""
        key = self._masters.norm_code(value)
        m = self.wh_master.get(key)
        link = self.wh_links.get(key)
        if link:
            name = link["name"]
            if m and self._masters.norm_code(m["name"]) != self._masters.norm_code(link.get("source_name") or name):
                self.wh_renamed[key] = {"code": m["code"], "stockai": name, "source": m["name"]}
        elif m:
            name = m["name"]
        else:
            return str(value)
        if m and not m["active"]:
            self.wh_inactive.add(name)
        if key not in self.wh_seen:
            self.wh_seen[key] = {"name": name, "source_name": m["name"] if m else None,
                                 "kind": (m or {}).get("kind"), "city": (m or {}).get("city"),
                                 "active": (m or {}).get("active", True)}
        return name

    def masters_summary(self) -> dict:
        return {
            "unknown_policy": self.unknown_policy,
            "warehouses": {
                "master_rows": len(self.wh_master), "new": sorted(self.wh_new)[:50],
                "renamed_in_source": list(self.wh_renamed.values())[:50],
                "inactive": sorted(self.wh_inactive)[:50], "defaulted": self.wh_defaulted,
                "collisions": self.wh_collisions[:20],
                "totals": [{"warehouse": w, "rows": t[0], "quantity": round(t[1], 3)}
                           for w, t in sorted(self.wh_totals.items())[:200]],
            },
            "suppliers": {"master_rows": len(self.sup_master), "collisions": self.sup_collisions[:20]},
        }

    def missing_columns(self) -> list[str]:
        """Mapped / filter / watermark columns no row of the source had."""
        if not self.seen_columns:
            return []
        low = {c.lower() for c in self.seen_columns}
        if self.profile.get("source_kind") == "push":
            # A pushed batch may carry only what changed: only the columns of
            # the required fields must be there.
            wanted = [self.mapping[f]["column"] for f in F.REQUIRED_FIELDS
                      if self.mapping.get(f, {}).get("column")]
            if (self.profile.get("stock_model") or "snapshot") == "ledger":
                return []
            return sorted({c for c in wanted if c.lower() not in low})
        wanted = [m.get("column") for m in self.mapping.values() if m.get("column")]
        wanted += [f["column"] for f in self.filters]
        if (self.profile.get("stock_model") or "snapshot") == "ledger":
            # Ledger rows are the source's per-key sums: only the mapped columns survive.
            return sorted({c for c in wanted if c and c.lower() not in low}
                          - {f["column"] for f in self.filters})
        if self.profile.get("watermark_column"):
            wanted.append(self.profile["watermark_column"])
        return sorted({c for c in wanted if c and c.lower() not in low})

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


def _owned_elsewhere(tenant_id: str, profile: dict,
                     keys: list[tuple[str, str]]) -> tuple[set[tuple[str, str]], list[str]]:
    """Keys another profile owns with a better (priority, age) — the rule the
    apply step enforces (apply.py, "precedence"). The preview must skip them
    too, or it promises updates the run will not make. A draft that is not
    saved yet counts as the newest profile."""
    if not keys:
        return set(), []
    rows = query(
        "SELECT k.sku, k.warehouse, p.name FROM inventory_sync_keys k "
        "JOIN inventory_sync_profiles p ON p.id = k.profile_id "
        "JOIN unnest(%s::text[], %s::text[]) AS x(sku, warehouse) "
        "  ON k.sku = x.sku AND k.warehouse = x.warehouse "
        "WHERE k.tenant_id = %s AND k.profile_id <> %s AND NOT k.inactive "
        "AND (p.priority, p.created_at) < (%s, COALESCE(%s::timestamptz, NOW()))",
        ([k[0] for k in keys], [k[1] for k in keys], tenant_id, profile.get("id") or "",
         int(profile.get("priority") or 100), profile.get("created_at")))
    return {(r["sku"], r["warehouse"]) for r in rows}, sorted({r["name"] for r in rows})


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

def _policy(profile: dict) -> str:
    """Duplicate keys: an aggregate profile SUMS them (client-side streaming
    aggregation where the source could not group); otherwise the profile says."""
    if (profile.get("stock_model") or "snapshot") == "aggregate":
        return "sum"
    return profile.get("duplicate_policy") or "reject"


def dry_run(tenant_id: str, profile: dict, *, sample: int = DRY_RUN_SAMPLE,
            scope: Optional[dict] = None, batches: Any = None) -> dict:
    """What a run would do with the first `sample` rows. Writes nothing."""
    started = time.monotonic()
    prep = _Prep(tenant_id, profile)
    valid: dict[tuple[str, str], dict] = {}
    conflicts: set[tuple[str, str]] = set()
    policy = _policy(profile)
    from backend.inventory_sync import strategies
    if callable(batches):
        batches = batches(prep.extras)
    source = batches if batches is not None else strategies.read_for_run(
        tenant_id, profile, limit=sample, batch_size=min(sample, 1000), scope=scope)
    with _closing(source) as it:
        all_batches = list(it)
    for batch in all_batches:
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
    owned, owned_by = _owned_elsewhere(tenant_id, profile, list(valid.keys()))
    creates, updates, unchanged = 0, 0, 0
    changes_sample: list[dict] = []
    for key, row in valid.items():
        if key in owned:
            continue
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
    examples: list = []
    model = profile.get("stock_model") or "snapshot"
    sample_skus = [k[0] for k in list(valid)[:3]]
    try:
        if model == "ledger" and profile.get("data_source_id"):
            from backend.inventory_sync import ledger
            examples = ledger.explain(tenant_id, profile, sample_skus)
        elif model == "aggregate" and profile.get("data_source_id"):
            from backend.inventory_sync import strategies as _st
            examples = _st.aggregate_examples(tenant_id, profile, sample_skus)
    except Exception:  # noqa: BLE001 - the worked example is a help, never a reason to fail the preview
        log.exception("inventory sync: worked example failed")
    from backend.inventory_sync import advice as _advice
    hints = _advice.lint(profile, set(prep.seen_columns) | set(profile.get("_source_columns") or []))
    masters_out = prep.masters_summary()
    if prep.sup_master:
        masters_out["suppliers"].update(prep._masters.preview_suppliers(tenant_id, profile, prep.sup_master))
    return {
        "masters": masters_out,
        "advice": hints,
        **prep.summary(),
        "valid": len(valid), "would_create": creates, "would_update": updates,
        "would_leave_unchanged": unchanged, "changes_sample": changes_sample,
        "owned_elsewhere": len(owned), "owned_by": owned_by,
        "preview_rows": preview, "sample_limit": sample,
        "sample_truncated": prep.rows_read >= sample,
        "owned_fields": F.owned_fields(profile.get("mapping") or {}),
        "stock_model": model, "examples": examples,
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
              worker: Optional[str] = None, kind: Optional[str] = None,
              scope: Optional[dict] = None) -> str:
    """Create the run row. Without `kind`: incremental for an incremental
    profile, else full (a ledger's first run is always full)."""
    run_id = generate_id("isr")
    execute(
        "INSERT INTO inventory_sync_runs (id, tenant_id, profile_id, trigger_kind, status, "
        "triggered_by, worker_id, run_kind, scope, config_hash) "
        "SELECT %s,%s,%s,%s,'running',%s,%s, COALESCE(%s, CASE "
        "  WHEN stock_model = 'ledger' AND cursor_value IS NULL THEN 'full' "
        "  WHEN mode = 'incremental' OR stock_model = 'ledger' THEN 'incremental' "
        "  WHEN strategy IN ('change_tracking', 'oracle_rowscn', 'change_stream') AND cursor_value IS NOT NULL "
        "    THEN 'incremental' ELSE 'full' END), "
        "%s, config_hash FROM inventory_sync_profiles WHERE id = %s",
        (run_id, tenant_id, profile_id, trigger, user_id, worker, kind,
         _json(scope) if scope else None, profile_id))
    execute("UPDATE inventory_sync_profiles SET last_run_id = %s WHERE id = %s", (run_id, profile_id))
    return run_id


class _Brake(Exception):
    def __init__(self, code: str, **params: Any):
        super().__init__(code)
        self.code = code
        self.params = params


class _Stop(Exception):
    """The run cannot continue (profile changed, deleted or disabled under it)."""

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
                unique[key] = _merge_duplicate(unique[key], data, "sum")
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
                    values, page_size=STAGE_PAGE)
                return
            # Only the rows that hit an earlier batch's key come back (not every
            # staged row: 200k rows returned per 200k staged cost seconds).
            hits = execute_values(
                cur,
                "WITH ins AS (INSERT INTO inventory_sync_staging (run_id, tenant_id, sku, warehouse, data) "
                "VALUES %s ON CONFLICT (run_id, sku, warehouse) DO UPDATE SET duplicate = TRUE "
                "RETURNING sku, warehouse, (xmax <> 0) AS was_duplicate) "
                "SELECT sku, warehouse FROM ins WHERE was_duplicate",
                values, page_size=STAGE_PAGE, fetch=True)
            for hit in hits:
                prep._reject("duplicate_key", {"warehouse": hit["warehouse"]}, hit["sku"], None)
            if dup_keys:
                cur.execute(
                    "UPDATE inventory_sync_staging s SET duplicate = TRUE "
                    "FROM unnest(%s::text[], %s::text[]) AS x(sku, warehouse) "
                    "WHERE s.run_id = %s AND s.sku = x.sku AND s.warehouse = x.warehouse",
                    ([k[0] for k in dup_keys], [k[1] for k in dup_keys], run_id))


def _apply_masters(tenant_id: str, profile: dict, prep: "_Prep", counters: dict, scoped: bool) -> None:
    """After the stock is applied: remember which source code became which
    warehouse, mark codes the master stopped listing inactive (never delete),
    and create / update suppliers from the supplier master. Each outcome that
    needs a person's eye becomes a named warning on the run."""
    m = prep._masters
    if prep.wh_master:
        m.record_warehouses(tenant_id, profile["id"], prep.wh_seen)
        if not scoped:
            m.mark_unlisted_inactive(profile["id"], "warehouses", set(prep.wh_master))
        if prep.wh_renamed:
            counters["warnings_codes"].add("inventory_sync_warehouse_renamed_in_source")
        if prep.wh_inactive:
            counters["warnings_codes"].add("inventory_sync_warehouse_inactive")
    if prep.wh_defaulted:
        counters["warnings_codes"].add("inventory_sync_warehouse_defaulted")
    if prep.sup_master and not scoped:
        out = m.apply_suppliers(tenant_id, profile, prep.sup_master)
        if out["lead_time_kept"]:
            counters["warnings_codes"].add("inventory_sync_supplier_lead_time_kept")
        if out["renamed_in_source"]:
            counters["warnings_codes"].add("inventory_sync_supplier_renamed_in_source")
    if prep.wh_collisions or prep.sup_collisions:
        counters["warnings_codes"].add("inventory_sync_master_code_collision")


@contextmanager
def _closing(batches: Any) -> Iterator[Any]:
    """Iterate a source and ALWAYS close it, also when the run stops halfway
    (brake, schema drift, profile deleted, an error). A read generator holds
    an open cursor inside a read transaction on the CUSTOMER's database; left
    to the garbage collector — which a traceback can postpone for minutes — it
    keeps a snapshot open and a share lock that blocks their DDL."""
    it = iter(batches)
    try:
        yield it
    finally:
        close = getattr(it, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001 - closing a broken connection may raise; nothing to keep
                log.debug("inventory sync: closing the source read failed", exc_info=True)


def _stage(tenant_id: str, profile: dict, run_id: str, lease_owner: Optional[str],
           batches: Optional[Iterable[list[dict]]] = None, scope: Optional[dict] = None,
           incremental: bool = False) -> tuple[_Prep, int]:
    """Read (or take `batches`, e.g. a push) and stage. Schema drift is caught
    here: a mapped column the source no longer has pauses the profile."""
    prep = _Prep(tenant_id, profile)
    policy = _policy(profile)
    if callable(batches):
        # A factory that needs the run's report (ledger push: cursor, movements).
        batches = batches(prep.extras)
    if batches is None:
        from backend.inventory_sync import strategies
        batches = strategies.read_for_run(tenant_id, profile, run_id=run_id, scope=scope,
                                          incremental=incremental, max_rows=max_rows_per_run(),
                                          report=prep.extras)
    is_sql = (profile.get("engine") or "") != "mongodb" and profile.get("source_kind") in ("table", "query")
    checked = False
    with _closing(batches) as source:
        for batch in source:
            rows = prep.rows(batch)
            if (incremental or profile.get("mode") == "incremental") and prep.watermark \
                    and prep.watermark.get("type") not in ("datetime", "number"):
                # Caught on the FIRST read, before anything is applied: a text
                # column cannot be followed, and accepting it would make this run
                # succeed and every later one fail.
                raise AppError("inventory_sync_watermark_unsupported",
                               "An incremental sync needs a date/time or numeric column to follow.",
                               status_code=422, params={"type": prep.watermark.get("type"),
                                                        "column": profile.get("watermark_column")})
            if is_sql and not checked and prep.seen_columns:
                # A SQL result has the same columns on every row: check the first batch.
                checked = True
                missing = prep.missing_columns()
                if missing:
                    raise AppError("inventory_sync_column_missing",
                                   f"The source no longer has column {missing[0]}.",
                                   status_code=422, params={"column": missing[0], "columns": ", ".join(missing)})
            _stage_batch(tenant_id, run_id, prep, rows, policy)
            _renew_lease(profile["id"], lease_owner)
    if not is_sql and prep.rows_read:
        # Documents are polymorphic: a field is "missing" only if no document had it.
        missing = prep.missing_columns()
        if missing:
            raise AppError("inventory_sync_column_missing",
                           f"No document of the source has field {missing[0]}.",
                           status_code=422, params={"column": missing[0], "columns": ", ".join(missing)})
    first_rows = 0
    if policy != "sum":
        first_rows = query_one("SELECT COUNT(*) AS n FROM inventory_sync_staging "
                               "WHERE run_id = %s AND duplicate", (run_id,))["n"]
        prep.rejected += first_rows
        if first_rows:
            prep.by_code["duplicate_key"] = prep.by_code.get("duplicate_key", 0) + first_rows
    valid = query_one("SELECT COUNT(*) AS n FROM inventory_sync_staging "
                      "WHERE run_id = %s AND NOT duplicate", (run_id,))["n"]
    _fresh_stats(int(valid))
    return prep, int(valid)


def _fresh_stats(valid: int) -> None:
    """Fresh statistics before the brake and apply joins over what was just
    staged: with the empty-table estimate the planner picks nested loops with
    sequential scans (a 20 000-row push snapshot measured 59 s in the missing
    count alone; ~0.3 s after ANALYZE). ANALYZE samples, so it stays cheap."""
    if valid > 1_000:
        execute("ANALYZE inventory_sync_staging")
        execute("ANALYZE inventory_sync_keys")


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


MIN_KEYS_FOR_FINGERPRINT = 50
MIN_ROWS_FOR_JUMP = 20
JUMP_FACTOR = 10.0


def _check_brake(profile: dict, prep: _Prep, valid: int, run_id: str, *, kind: str) -> dict:
    """Raise `_Brake` when the staged set looks like a broken or wrong source.

    Full snapshots get every check. Incremental, scoped and push-partial runs
    only see part of the catalogue, so "vanished" and "row count" do not apply
    to them; the rejected share and the quantity-jump check still do."""
    considered = prep.rows_read - prep.filtered_out
    full = kind in ("full", "reconcile")
    known, vanished = _vanished_count(profile["id"], run_id) if full else (0, 0)
    stats = {"known": known, "vanished": vanished}
    if full and prep.rows_read == 0:
        raise _Brake("source_empty")
    max_rej = float(profile.get("brake_max_rejected_pct") or 50)
    if considered > 0 and prep.rejected * 100.0 / considered > max_rej:
        raise _Brake("too_many_rejected", rejected=prep.rejected, rows=considered, max_pct=max_rej)
    if full and valid == 0:
        raise _Brake("no_valid_rows", rows=considered)
    if full and known >= MIN_KEYS_FOR_FINGERPRINT and (known - vanished) * 100.0 / known < 20:
        # Fewer than a fifth of the SKUs this profile knows are in the source:
        # this looks like a different database (a test copy, another company).
        raise _Brake("source_looks_different", known=known, overlap=known - vanished,
                     overlap_pct=round((known - vanished) * 100.0 / known, 1))
    if full and known > 0:
        pct = vanished * 100.0 / known
        if pct > float(profile.get("brake_max_vanished_pct") or 0):
            raise _Brake("skus_vanished", vanished=vanished, known=known, pct=round(pct, 1),
                         max_pct=float(profile.get("brake_max_vanished_pct") or 0))
    if full:
        prev = query_one(
            "SELECT rows_valid FROM inventory_sync_runs WHERE profile_id = %s AND status = 'success' "
            "AND run_kind IN ('full', 'reconcile') AND id <> %s ORDER BY started_at DESC LIMIT 1",
            (profile["id"], run_id))
        ratio = float(profile.get("brake_min_row_ratio") or 0)
        if prev and prev["rows_valid"] and valid < prev["rows_valid"] * ratio:
            raise _Brake("row_count_drop", rows=valid, previous=int(prev["rows_valid"]), min_ratio=ratio)
    jump = query_one(
        "SELECT COUNT(*) AS matched, COUNT(*) FILTER (WHERE greatest(s.current_stock, x.q) >= %s "
        "  AND greatest(s.current_stock, x.q) > %s * least(s.current_stock, x.q)) AS jumped "
        "FROM (SELECT sku, warehouse, (data->>'current_stock')::float8 AS q FROM inventory_sync_staging "
        "      WHERE run_id = %s AND NOT duplicate) x "
        "JOIN inventory_stock s ON s.tenant_id = %s AND s.sku = x.sku AND s.warehouse = x.warehouse "
        "JOIN inventory_sync_keys k ON k.profile_id = %s AND k.sku = x.sku AND k.warehouse = x.warehouse",
        (JUMP_FACTOR, JUMP_FACTOR, run_id, profile["tenant_id"], profile["id"]))
    max_jump = float(profile.get("brake_max_jump_pct") or 20)
    if jump and jump["matched"] >= MIN_ROWS_FOR_JUMP and jump["jumped"] * 100.0 / jump["matched"] > max_jump:
        # Many quantities this profile wrote before changed by more than 10x
        # at once: a unit change (boxes -> units), a decimal shift, a wrong column.
        raise _Brake("mass_quantity_change", jumped=int(jump["jumped"]), matched=int(jump["matched"]),
                     factor=JUMP_FACTOR, max_pct=max_jump)
    return stats


def _guard_profile(tenant_id: str, profile: dict, trigger: str) -> dict:
    """The profile as it is NOW. Stops the run when it was deleted, edited
    (its configuration hash moved) or, for a scheduled run, disabled."""
    now = query_one("SELECT * FROM inventory_sync_profiles WHERE id = %s AND tenant_id = %s",
                    (profile["id"], tenant_id))
    if not now:
        raise _Stop("inventory_sync_profile_changed", reason="deleted")
    if now.get("config_hash") != profile.get("config_hash"):
        raise _Stop("inventory_sync_profile_changed", reason="edited")
    if trigger in ("schedule", "continuous") and not now.get("enabled"):
        raise _Stop("inventory_sync_profile_changed", reason="disabled")
    return now


def _error_of(exc: BaseException) -> tuple[str, dict]:
    from backend.inventory_sync import failures
    if isinstance(exc, AppError):
        code, params = exc.code, dict(exc.params or {})
    else:
        code, params = None, {}
        try:
            from fastapi import HTTPException
            if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
                detail = dict(exc.detail)
                code = str(detail.pop("code", "") or "plan_limit_reached")
                params = {k: v for k, v in detail.items() if isinstance(v, (int, float, str))}
        except Exception:  # noqa: BLE001
            pass
        if code is None:
            pgcode = getattr(exc, "pgcode", None)
            if pgcode in ("53100", "53200"):
                code = "inventory_sync_storage_full"
            elif pgcode in ("55P03", "57014", "40P01"):
                code = "inventory_sync_our_db_busy"
            else:
                code, params = "inventory_sync_internal_error", {"type": type(exc).__name__}
    code, params = failures.refine(code, params)
    return code, failures.public_params(params)


def _event(tenant_id: str, action: str, profile: dict, *, details: dict,
           reason: Optional[str] = None, reason_params: Optional[dict] = None,
           user_id: Optional[str] = None) -> None:
    from backend.activity.events import record_event
    record_event(tenant_id, user_id or "system", action, resource=profile["id"],
                 details={"profile_name": profile.get("name"), **details},
                 reason=reason, reason_params=reason_params,
                 status="success" if action.endswith(("succeeded", "recovered")) else "error")


def _webhook(tenant_id: str, event_type: str, data: dict) -> None:
    try:
        from backend.webhooks.service import emit
        emit(tenant_id, event_type, data)
    except Exception:  # noqa: BLE001 - a webhook never fails a sync; the run row is the record
        log.exception("inventory sync: webhook %s not queued", event_type)


def _transition(tenant_id: str, profile: dict, run: dict, health: str, reason: Optional[str],
                params: dict, user_id: Optional[str]) -> None:
    """Move the profile's health and say so ONCE per change: entering a bad
    state raises one event (a down source does not alert every five minutes),
    and leaving it for healthy raises a recovery notice."""
    before = profile.get("health") or "new"
    before_reason = profile.get("health_reason")
    changed = (health != before) or (reason != before_reason)
    execute(
        "UPDATE inventory_sync_profiles SET health = %s, health_reason = %s, health_params = %s, "
        "health_changed_at = CASE WHEN %s THEN NOW() ELSE health_changed_at END WHERE id = %s",
        (health, reason, _json(params) if params else None, changed, profile["id"]))
    execute("UPDATE inventory_sync_runs SET health_after = %s WHERE id = %s", (health, run["id"]))
    if not changed:
        return
    base = {"profile_id": profile["id"], "profile_name": profile.get("name"), "run_id": run["id"],
            "health": health, "reason_code": reason}
    if health == "healthy" and before not in ("new", "healthy"):
        _event(tenant_id, "inventory_sync.recovered", profile, details={}, user_id=user_id)
        _webhook(tenant_id, "inventory_sync.recovered", base)
    elif health in ("paused", "blocked"):
        action = "inventory_sync.braked" if (reason or "").startswith("brake_") else "inventory_sync.blocked"
        _event(tenant_id, action, profile, details={},
               reason="inventory_sync_safety_brake" if action.endswith("braked") else "inventory_sync_needs_action",
               reason_params={"code": reason, **(params or {})}, user_id=user_id)
        _webhook(tenant_id, "inventory_sync.blocked", base)
    elif health == "degraded" and before != "degraded":
        _event(tenant_id, "inventory_sync.run_failed", profile, details={},
               reason="inventory_sync_run_failed", reason_params={"error_code": reason, **(params or {})},
               user_id=user_id)


def execute_run(tenant_id: str, profile_id: str, run_id: str, *, override_brake: bool = False,
                lease_owner: Optional[str] = None, user_id: Optional[str] = None,
                batches: Any = None, resume: bool = False, staged: bool = False) -> dict:
    """Run one sync. Always finishes the run row (success / failed / braked)
    and never raises for a source or data problem — the outcome is the row.

    `resume`: the run's staging is complete and its brake passed (a worker died
    while applying); continue applying from the first unapplied key instead of
    reading the source again. `batches`: rows handed in (push ingest) instead
    of read from the source."""
    from backend.inventory_sync import apply as applier, failures

    started = time.monotonic()
    profile = query_one("SELECT p.*, d.sql_config->>'engine' AS engine FROM inventory_sync_profiles p "
                        "LEFT JOIN datasets d ON d.id = p.data_source_id "
                        "WHERE p.id = %s AND p.tenant_id = %s", (profile_id, tenant_id))
    if not profile:
        raise AppError("inventory_sync_profile_not_found", "Sync profile not found.", status_code=404)
    run = query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (run_id,)) or {}
    trigger = run.get("trigger_kind") or "manual"
    kind = run.get("run_kind") or ("incremental" if profile.get("mode") == "incremental" else "full")
    scope = run.get("scope")
    counters: dict[str, Any] = {
        "created": int(run.get("created") or 0), "updated": int(run.get("updated") or 0),
        "unchanged": int(run.get("unchanged") or 0), "missing": 0, "missing_applied": 0,
        "manual_overwritten": int(run.get("manual_overwritten") or 0), "overwritten_sample": [],
        "refused_ceiling": int(run.get("refused_ceiling") or 0),
        "owned_elsewhere": int(run.get("owned_elsewhere") or 0), "warnings_sample": [],
        "warnings_codes": set()}
    prep: Optional[_Prep] = None
    valid = int(run.get("rows_valid") or 0)
    status, error_code, error_params = "success", None, None
    watermark_after = profile.get("watermark_value")
    cursor_after = profile.get("cursor_value")
    try:
        if staged:
            # A multi-part push snapshot: the parts were staged as they arrived.
            prep = _Prep(tenant_id, profile)
            prep.rows_read = int(run.get("rows_read") or 0)
            prep.rejected = int(run.get("rejected") or 0)
            prep.rejected_sample = list(run.get("rejected_sample") or [])
            dups = query_one("SELECT COUNT(*) AS n FROM inventory_sync_staging WHERE run_id = %s AND duplicate",
                             (run_id,))["n"]
            prep.rejected += int(dups)
            valid = int(query_one("SELECT COUNT(*) AS n FROM inventory_sync_staging WHERE run_id = %s "
                                  "AND NOT duplicate", (run_id,))["n"])
            execute("UPDATE inventory_sync_runs SET stage_complete = TRUE, rows_valid = %s, rejected = %s "
                    "WHERE id = %s", (valid, prep.rejected, run_id))
            _fresh_stats(valid)
            if not override_brake:
                _check_brake(profile, prep, valid, run_id, kind="full")
            execute("UPDATE inventory_sync_runs SET brake_passed = TRUE WHERE id = %s", (run_id,))
        elif not (resume and run.get("stage_complete") and run.get("brake_passed")):
            prep, valid = _stage(tenant_id, profile, run_id, lease_owner, batches=batches, scope=scope,
                                 incremental=kind == "incremental")
            execute("UPDATE inventory_sync_runs SET stage_complete = TRUE, rows_read = %s, rows_valid = %s, "
                    "rejected = %s, rejected_sample = %s WHERE id = %s",
                    (prep.rows_read, valid, prep.rejected, _json(prep.rejected_sample), run_id))
            if not override_brake:
                _check_brake(profile, prep, valid, run_id, kind=kind)
            if prep.extras.get("capture_fallback"):
                # The change cursor had expired (retention / oplog window): this
                # run read everything instead, and says so.
                counters["warnings_codes"].add("inventory_sync_capture_fallback")
            drift = int(prep.extras.get("drift") or 0)
            if drift:
                execute("UPDATE inventory_sync_runs SET drift_count = %s, drift_sample = %s WHERE id = %s",
                        (drift, _json(prep.extras.get("drift_sample") or []), run_id))
                cfg = profile.get("model_config") or {}
                if cfg.get("drift_policy") == "brake" and not override_brake:
                    raise _Brake("ledger_drift", count=drift)
            execute("UPDATE inventory_sync_runs SET brake_passed = TRUE WHERE id = %s", (run_id,))
        _guard_profile(tenant_id, profile, trigger)
        after = None
        if resume and isinstance(run.get("checkpoint"), dict) and run["checkpoint"].get("after"):
            after = tuple(run["checkpoint"]["after"])
        while True:
            after = applier.apply_batch(tenant_id, profile, run_id, after, counters)
            if after is None:
                break
            execute("UPDATE inventory_sync_runs SET created = %s, updated = %s, unchanged = %s, "
                    "manual_overwritten = %s, refused_ceiling = %s, owned_elsewhere = %s, "
                    "checkpoint = %s WHERE id = %s",
                    (counters["created"], counters["updated"], counters["unchanged"],
                     counters["manual_overwritten"], counters["refused_ceiling"],
                     counters["owned_elsewhere"], _json({"after": list(after)}), run_id))
            _renew_lease(profile_id, lease_owner)
        if kind in ("full", "reconcile") and not (scope or {}).get("skus"):
            applier.apply_missing(tenant_id, profile, run_id, counters)
        scoped = bool((scope or {}).get("skus") or (scope or {}).get("warehouses"))
        if prep is not None and profile.get("watermark_column") and not scoped:
            # Every unscoped read records how far it saw, full runs included, so
            # switching a profile to incremental (or a continuous poll after a
            # reconcile) starts there instead of re-reading everything.
            # A SCOPED read never moves it: it saw only some SKUs, and their
            # newest change can be later than another SKU's unread change.
            watermark_after = reader.max_watermark(profile.get("watermark_value"), prep.watermark)
            capped = reader.cap_future(watermark_after)
            if capped is not watermark_after:
                # A row stamped in the future (clock skew, a typo'd date) would
                # park the watermark ahead of every real change from then on.
                counters["warnings_codes"].add("inventory_sync_future_timestamps")
                watermark_after = capped
        if prep is not None and prep.extras.get("cursor_after") is not None and not scoped:
            cursor_after = prep.extras["cursor_after"]
        if prep is not None and (prep.wh_master or prep.sup_master or prep.wh_defaulted):
            _apply_masters(tenant_id, profile, prep, counters, scoped)
        if (profile.get("stock_model") or "snapshot") == "ledger":
            from backend.inventory_sync import ledger
            ledger.commit(profile_id, run_id)
            if prep is not None and prep.extras.get("floor"):
                execute("UPDATE inventory_sync_profiles SET ledger_floor = %s WHERE id = %s",
                        (_json(prep.extras["floor"]), profile_id))
    except _Brake as brake:
        status, error_code, error_params = "braked", f"brake_{brake.code}", failures.public_params(brake.params)
    except _Stop as stop:
        status, error_code, error_params = "failed", stop.code, failures.public_params(stop.params)
    except Exception as exc:  # noqa: BLE001 - recorded on the run row, never swallowed silently
        status = "failed"
        error_code, error_params = _error_of(exc)
        if error_code == "inventory_sync_internal_error":
            log.exception("inventory sync run %s failed", run_id)
        else:
            log.warning("inventory sync run %s failed: %s", run_id, error_code)

    if status != "success" and (profile.get("stock_model") or "snapshot") == "ledger":
        try:
            from backend.inventory_sync import ledger
            ledger.rollback(profile_id, run_id)
        except Exception:  # noqa: BLE001
            log.exception("could not roll back ledger state of run %s", run_id)
    if not query_one("SELECT 1 FROM inventory_sync_profiles WHERE id = %s", (profile_id,)):
        # Deleted (or the tenant erased) while running: the cascade took the run
        # row with it; there is nothing left to report on.
        return {"id": run_id, "status": "failed", "error_code": "inventory_sync_profile_changed"}

    duration = int((time.monotonic() - started) * 1000)
    summary = prep.summary() if prep else {"rows_read": int(run.get("rows_read") or 0),
                                           "rejected": int(run.get("rejected") or 0),
                                           "rejected_sample": run.get("rejected_sample") or []}
    try:
        execute("DELETE FROM inventory_sync_staging WHERE run_id = %s", (run_id,))
    except Exception:  # noqa: BLE001
        log.exception("could not clear staging for run %s", run_id)
    warnings = sorted(counters["warnings_codes"])
    if counters["owned_elsewhere"]:
        warnings.append("owned_by_other_profile")
    execute(
        """UPDATE inventory_sync_runs SET status=%s, finished_at=NOW(), duration_ms=%s,
             rows_read=%s, rows_valid=%s, created=%s, updated=%s, unchanged=%s, rejected=%s,
             missing=%s, missing_applied=%s, manual_overwritten=%s, error_code=%s,
             error_params=%s, watermark_before=%s, watermark_after=%s, rejected_sample=%s,
             overwritten_sample=%s, brake_overridden=%s, refused_ceiling=%s, owned_elsewhere=%s,
             warnings=%s
           WHERE id=%s""",
        (status, duration, summary["rows_read"], valid, counters["created"], counters["updated"],
         counters["unchanged"], summary["rejected"], counters["missing"],
         counters["missing_applied"], counters["manual_overwritten"], error_code,
         _json(error_params) if error_params is not None else None,
         _json(profile.get("watermark_value")) if profile.get("watermark_value") else None,
         _json(watermark_after) if (status == "success" and watermark_after) else None,
         _json(summary.get("rejected_sample") or []), _json(counters["overwritten_sample"]),
         bool(override_brake), counters["refused_ceiling"], counters["owned_elsewhere"],
         _json({"codes": warnings, "sample": counters["warnings_sample"]}) if warnings else None, run_id),
    )
    run_row = query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (run_id,))
    if status == "success":
        execute(
            "UPDATE inventory_sync_profiles SET last_status='success', last_error_code=NULL, "
            "last_success_at=NOW(), data_as_of=NOW(), consecutive_failures=0, stale_alerted_at=NULL, "
            "watermark_value=%s, cursor_value=%s, updated_at=NOW() WHERE id=%s",
            (_json(watermark_after) if watermark_after else None,
             _json(cursor_after) if cursor_after else None, profile_id))
        _event(tenant_id, "inventory_sync.run_succeeded", profile, user_id=user_id,
               details={"created": counters["created"], "updated": counters["updated"],
                        "unchanged": counters["unchanged"], "rejected": summary["rejected"]})
        drift = int(run_row.get("drift_count") or 0)
        if drift:
            _event(tenant_id, "inventory_sync.ledger_drift", profile, user_id=user_id,
                   details={"keys_drifted": drift}, reason="inventory_sync_ledger_drift_healed",
                   reason_params={"count": drift})
        if counters["manual_overwritten"]:
            _event(tenant_id, "inventory_sync.manual_edits_overwritten", profile, user_id=user_id,
                   details={"edits_replaced": counters["manual_overwritten"]},
                   reason="inventory_sync_source_wins",
                   reason_params={"count": counters["manual_overwritten"]})
        _webhook(tenant_id, "inventory_sync.run_finished", {
            "profile_id": profile_id, "profile_name": profile.get("name"), "run_id": run_id,
            "status": status, "created": counters["created"], "updated": counters["updated"],
            "unchanged": counters["unchanged"], "rejected": summary["rejected"]})
        if counters["refused_ceiling"]:
            code = warnings[0] if warnings else "inventory_sync_sku_ceiling"
            _transition(tenant_id, profile, run_row, "degraded", code,
                        {"refused": counters["refused_ceiling"]}, user_id)
        else:
            _transition(tenant_id, profile, run_row, "healthy", None, {}, user_id)
    else:
        outcome = failures.outcome_of(error_code)
        last_status = "braked" if status == "braked" else "failed"
        execute(
            "UPDATE inventory_sync_profiles SET last_status=%s, last_error_code=%s, "
            "consecutive_failures = consecutive_failures + CASE WHEN %s THEN 1 ELSE 0 END, "
            "updated_at=NOW() WHERE id=%s",
            (last_status, error_code, status == "failed", profile_id))
        _transition(tenant_id, profile, run_row, outcome.health, error_code, error_params or {}, user_id)
    return query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (run_id,))


def stage_part(tenant_id: str, profile: dict, run_id: str, rows: list[dict]) -> dict:
    """Stage one part of a multi-part push snapshot; the counts accumulate
    on the run row until the last part applies the whole snapshot."""
    prep = _Prep(tenant_id, profile)
    staged = prep.rows(rows)
    missing = prep.missing_columns()
    if missing:
        raise AppError("inventory_sync_column_missing", f"The batch has no column {missing[0]}.",
                       status_code=422, params={"column": missing[0], "columns": ", ".join(missing)})
    _stage_batch(tenant_id, run_id, prep, staged, _policy(profile))
    run = query_one("SELECT rejected_sample FROM inventory_sync_runs WHERE id = %s", (run_id,)) or {}
    sample = (list(run.get("rejected_sample") or []) + prep.rejected_sample)[:MAX_SAMPLE]
    execute("UPDATE inventory_sync_runs SET rows_read = rows_read + %s, rejected = rejected + %s, "
            "rejected_sample = %s WHERE id = %s", (prep.rows_read, prep.rejected, _json(sample), run_id))
    return prep.summary()
