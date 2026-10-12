"""Set-based apply: staged rows -> inventory_stock, a batch per transaction.

The first version called `upsert_stock` once per row: correct, and 90 rows a
second on a creating run (measured: 20,000 rows in 223 s), i.e. three hours
for a million-row catalogue. This module does the same thing in a handful of
statements per batch of `BATCH` keys:

1. the batch is copied into a temp table (ON COMMIT DROP);
2. keys another profile owns with precedence are dropped (and counted);
3. new keys over the plan's SKU / location ceiling are refused (and counted)
   — existing keys still update;
4. new keys are inserted with their quantity, every row then gets ONE UPDATE
   that writes only the fields whose value actually differs (float noise
   ignored), stamping provenance for the fields that carry it;
5. a snapshot row is written only where the quantity changed (or is new);
6. manual edits the source overwrote are counted from the key ledger;
7. the ledger and the staging `applied` flags are updated.

Everything runs under the tenant's limit lock (`limit_guard`) with a local
`lock_timeout` and `statement_timeout`, so a batch never holds locks for long
and never waits forever behind another writer; a lock timeout is retried, then
reported as `inventory_sync_our_db_busy`. No statement locks a whole table.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from backend.config import settings
from backend.db.connection import query, query_one, execute
from backend.errors import AppError
from backend.inventory_sync import fields as F

log = logging.getLogger(__name__)

BATCH = 5000
MAX_SAMPLE = 50
LOCK_RETRIES = 3

_NUM = ("current_stock", "min_stock", "unit_cost", "sale_price")
_INT = ("lead_time_days",)
_TXT = ("supplier", "category", "brand", "unit_of_measure", "barcode", "display_name")
_PROVENANCE = {"unit_cost": "unit_cost_set_by", "lead_time_days": "lead_time_set_by"}


def _typed(field: str) -> str:
    """`b.data->>'field'` cast to the column's type."""
    if field in _NUM:
        return f"(b.data->>'{field}')::float8"
    if field in _INT:
        return f"(b.data->>'{field}')::int"
    return f"(b.data->>'{field}')"


def _differs(field: str, left: str, right: str) -> str:
    """SQL: the two values differ (numbers beyond float noise)."""
    if field in _NUM or field in _INT:
        return (f"({left} IS DISTINCT FROM {right} AND NOT ({left} IS NOT NULL AND {right} IS NOT NULL "
                f"AND abs(({left})::float8 - ({right})::float8) <= 1e-9 * greatest(1, abs(({left})::float8), abs(({right})::float8))))")
    return f"({left} IS DISTINCT FROM {right})"


def _ceilings(tenant_id: str, conn) -> tuple[Optional[int], Optional[int]]:
    """(SKU rows still allowed, locations still allowed); None = no ceiling."""
    if settings.testing_mode:
        return None, None
    from backend.entitlements.service import tenant_limits
    from backend.inventory import warehouse_service as wh_svc
    tenant = query_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,), conn=conn) or {"quota": {}}
    limits = tenant_limits(tenant)
    max_skus, max_locs = limits.get("max_skus"), limits.get("max_locations")
    sku_left = None
    if max_skus is not None:
        n = query_one("SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id = %s", (tenant_id,), conn=conn)["n"]
        sku_left = max(0, int(max_skus) - int(n))
    loc_left = None
    if max_locs is not None:
        loc_left = max(0, int(max_locs) - int(wh_svc.count_warehouses(tenant_id)))
    return sku_left, loc_left


def apply_batch(tenant_id: str, profile: dict, run_id: str, after: Optional[tuple[str, str]],
                counters: dict) -> Optional[tuple[str, str]]:
    """Apply the next batch after key `after`. Returns the last key applied,
    or None when nothing was left. Retries a lock timeout."""
    for attempt in range(LOCK_RETRIES + 1):
        try:
            return _apply_batch_once(tenant_id, profile, run_id, after, counters)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "pgcode", None) in ("55P03", "57014", "40P01") and attempt < LOCK_RETRIES:
                time.sleep(0.5 * (attempt + 1))
                continue
            if getattr(exc, "pgcode", None) in ("55P03", "57014", "40P01"):
                raise AppError("inventory_sync_our_db_busy",
                               "StockAI's database was too busy to apply this batch.",
                               status_code=503) from None
            if getattr(exc, "pgcode", None) in ("53100", "53200"):
                raise AppError("inventory_sync_storage_full",
                               "StockAI's database is out of space.", status_code=503) from None
            raise
    return None  # pragma: no cover


def _apply_batch_once(tenant_id: str, profile: dict, run_id: str,
                      after: Optional[tuple[str, str]], counters: dict) -> Optional[tuple[str, str]]:
    from backend.entitlements.service import limit_guard
    from backend.inventory import service as inv_svc

    owned = F.owned_fields(profile.get("mapping") or {}) or ["current_stock"]
    if "current_stock" not in owned:
        owned = ["current_stock"] + owned
    pid = profile["id"]
    with limit_guard(tenant_id) as conn:
        execute("SET LOCAL lock_timeout = '10s'", conn=conn)
        execute("SET LOCAL statement_timeout = '120s'", conn=conn)
        where_after = "AND (sku, warehouse) > (%s, %s)" if after else ""
        params: tuple = (run_id, *after) if after else (run_id,)
        execute(
            "CREATE TEMP TABLE isb ON COMMIT DROP AS "
            "SELECT sku, warehouse, data FROM inventory_sync_staging "
            f"WHERE run_id = %s AND NOT duplicate {where_after} "
            f"ORDER BY sku, warehouse LIMIT {BATCH}", params, conn=conn)
        last = query_one("SELECT sku, warehouse FROM isb ORDER BY sku DESC, warehouse DESC LIMIT 1",
                         conn=conn)
        if not last:
            return None
        last_key = (last["sku"], last["warehouse"])
        execute("CREATE INDEX ON isb (sku, warehouse)", conn=conn)
        execute("ANALYZE isb", conn=conn)   # a temp table has no statistics until told

        # 2. precedence: another live profile with a better (priority, age) owns the key.
        owned_rows = query(
            "DELETE FROM isb b USING inventory_sync_keys k "
            "JOIN inventory_sync_profiles p ON p.id = k.profile_id "
            "WHERE k.tenant_id = %s AND k.sku = b.sku AND k.warehouse = b.warehouse "
            "AND k.profile_id <> %s AND NOT k.inactive "
            "AND (p.priority, p.created_at) < (%s, %s) RETURNING b.sku, b.warehouse, p.name",
            (tenant_id, pid, int(profile.get("priority") or 100), profile.get("created_at")), conn=conn)
        if owned_rows:
            counters["owned_elsewhere"] += len(owned_rows)
            for r in owned_rows[: max(0, MAX_SAMPLE - len(counters["warnings_sample"]))]:
                counters["warnings_sample"].append({"code": "owned_by_other_profile", "sku": r["sku"],
                                                    "warehouse": r["warehouse"], "profile": r["name"]})

        # 3. ceilings: new keys / new locations beyond what the plan allows.
        execute(
            "CREATE TEMP TABLE isn ON COMMIT DROP AS SELECT b.sku, b.warehouse FROM isb b "
            "WHERE NOT EXISTS (SELECT 1 FROM inventory_stock s WHERE s.tenant_id = %s "
            "AND s.sku = b.sku AND s.warehouse = b.warehouse)", (tenant_id,), conn=conn)
        sku_left, loc_left = _ceilings(tenant_id, conn)
        if loc_left is not None:
            new_locs = [r["warehouse"] for r in query(
                "SELECT DISTINCT n.warehouse FROM isn n WHERE NOT EXISTS (SELECT 1 FROM warehouses w "
                "WHERE w.tenant_id = %s AND w.name = n.warehouse) ORDER BY 1", (tenant_id,), conn=conn)]
            refused_locs = new_locs[loc_left:]
            if refused_locs:
                gone = query("DELETE FROM isb WHERE warehouse = ANY(%s) AND (sku, warehouse) IN "
                             "(SELECT sku, warehouse FROM isn) RETURNING sku", (refused_locs,), conn=conn)
                execute("DELETE FROM isn WHERE warehouse = ANY(%s)", (refused_locs,), conn=conn)
                counters["refused_ceiling"] += len(gone)
                counters["warnings_codes"].add("inventory_sync_location_ceiling")
        if sku_left is not None:
            gone = query(
                "DELETE FROM isb WHERE (sku, warehouse) IN (SELECT sku, warehouse FROM isn "
                "ORDER BY sku, warehouse OFFSET %s) RETURNING sku", (sku_left,), conn=conn)
            if gone:
                execute("DELETE FROM isn WHERE (sku, warehouse) NOT IN (SELECT sku, warehouse FROM isb)",
                        conn=conn)
                counters["refused_ceiling"] += len(gone)
                counters["warnings_codes"].add("inventory_sync_sku_ceiling")

        # warehouses the new rows name (canonical names were resolved at staging)
        for r in query("SELECT DISTINCT warehouse FROM isn", conn=conn):
            inv_svc._ensure_warehouse(tenant_id, r["warehouse"], conn=conn)

        # 4a. new keys: inserted with their quantity (the defaults hold for the rest)
        created = query(
            "INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock, updated_at) "
            "SELECT %s, b.sku, b.warehouse, COALESCE((b.data->>'current_stock')::float8, 0), NOW() "
            "FROM isb b JOIN isn n ON n.sku = b.sku AND n.warehouse = b.warehouse "
            "ON CONFLICT (tenant_id, sku, warehouse) DO NOTHING RETURNING sku",
            (tenant_id,), conn=conn)
        counters["created"] += len(created)

        # 6. manual edits about to be overwritten (before the UPDATE changes them)
        overwrite_terms = []
        for f in owned:
            col = f"s.{f}"
            prev = (f"(k.synced_values->>'{f}')::float8" if f in _NUM or f in _INT
                    else f"(k.synced_values->>'{f}')")
            overwrite_terms.append(
                f"SELECT b.sku, b.warehouse, '{f}' AS field, to_jsonb({col}) AS manual_value, "
                f"(b.data->'{f}') AS source_value FROM isb b "
                f"JOIN inventory_stock s ON s.tenant_id = %s AND s.sku = b.sku AND s.warehouse = b.warehouse "
                f"JOIN inventory_sync_keys k ON k.profile_id = %s AND k.sku = b.sku AND k.warehouse = b.warehouse "
                f"WHERE b.data ? '{f}' AND k.synced_values ? '{f}' "
                f"AND {_differs(f, col, _typed(f))} AND {_differs(f, col, prev)}")
        if overwrite_terms:
            rows = query(" UNION ALL ".join(overwrite_terms) + f" LIMIT {MAX_SAMPLE * 20}",
                         tuple(x for _ in owned for x in (tenant_id, pid)), conn=conn)
            counters["manual_overwritten"] += len(rows)
            for r in rows[: max(0, MAX_SAMPLE - len(counters["overwritten_sample"]))]:
                counters["overwritten_sample"].append(dict(r))

        # snapshots: rows whose quantity changes (computed before the UPDATE)
        execute(
            "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse) "
            "SELECT %s, b.sku, (b.data->>'current_stock')::float8, b.warehouse FROM isb b "
            "JOIN inventory_stock s ON s.tenant_id = %s AND s.sku = b.sku AND s.warehouse = b.warehouse "
            "LEFT JOIN isn n ON n.sku = b.sku AND n.warehouse = b.warehouse "
            "WHERE b.data ? 'current_stock' AND (n.sku IS NOT NULL OR "
            + _differs("current_stock", "s.current_stock", "(b.data->>'current_stock')::float8") + ")",
            (tenant_id, tenant_id), conn=conn)

        # 4b. one UPDATE: only fields that differ
        sets, any_diff = [], []
        for f in owned:
            new = _typed(f)
            diff = _differs(f, f"s.{f}", new)
            sets.append(f"{f} = CASE WHEN b.data ? '{f}' THEN {new} ELSE s.{f} END")
            any_diff.append(f"(b.data ? '{f}' AND {diff})")
            if f in _PROVENANCE:
                sets.append(f"{_PROVENANCE[f]} = CASE WHEN b.data ? '{f}' AND {diff} THEN 'file' "
                            f"ELSE s.{_PROVENANCE[f]} END")
        updated = query(
            f"UPDATE inventory_stock s SET {', '.join(sets)}, updated_at = NOW() FROM isb b "
            "WHERE s.tenant_id = %s AND s.sku = b.sku AND s.warehouse = b.warehouse "
            f"AND ({' OR '.join(any_diff)}) "
            "RETURNING s.sku, s.warehouse, (s.sku, s.warehouse) IN (SELECT sku, warehouse FROM isn) AS is_new",
            (tenant_id,), conn=conn)
        counters["updated"] += sum(1 for r in updated if not r["is_new"])
        total = query_one("SELECT COUNT(*) AS n FROM isb", conn=conn)["n"]
        counters["unchanged"] += int(total) - len(created) - sum(1 for r in updated if not r["is_new"])

        # 7. the key ledger and the staging flags
        # Staged data holds exactly the mapped value fields (the owned set).
        execute(
            "INSERT INTO inventory_sync_keys (tenant_id, profile_id, sku, warehouse, synced_values, "
            "last_seen_run, last_seen_at, inactive) "
            "SELECT %s, %s, b.sku, b.warehouse, b.data, %s, NOW(), FALSE FROM isb b "
            "ON CONFLICT (profile_id, sku, warehouse) DO UPDATE SET "
            "synced_values = inventory_sync_keys.synced_values || EXCLUDED.synced_values, "
            "last_seen_run = EXCLUDED.last_seen_run, last_seen_at = NOW(), inactive = FALSE",
            (tenant_id, pid, run_id), conn=conn)
        # No per-row "applied" flag: the run row's checkpoint (the last key
        # applied, written after each batch) is what a resume continues from.
    return last_key


def apply_missing(tenant_id: str, profile: dict, run_id: str, counters: dict) -> None:
    """The missing-SKU policy (full snapshots only), set-based. Never deletes."""
    from backend.entitlements.service import limit_guard
    pid = profile["id"]
    # An anti-join the planner can hash (a NOT EXISTS with stale statistics
    # on a just-filled staging table was measured at 399 s for 50k keys).
    missing_sql = (
        "FROM inventory_sync_keys k LEFT JOIN inventory_sync_staging s ON s.run_id = %s "
        "AND s.sku = k.sku AND s.warehouse = k.warehouse AND NOT s.duplicate "
        "WHERE k.profile_id = %s AND NOT k.inactive AND s.sku IS NULL")
    counters["missing"] = int(query_one(f"SELECT COUNT(*) AS n {missing_sql}", (run_id, pid))["n"])
    policy = profile.get("missing_policy") or "ignore"
    if not counters["missing"] or policy == "ignore":
        return
    with limit_guard(tenant_id) as conn:
        execute("SET LOCAL lock_timeout = '10s'", conn=conn)
        execute(f"CREATE TEMP TABLE ism ON COMMIT DROP AS SELECT k.sku, k.warehouse {missing_sql}",
                (run_id, pid), conn=conn)
        if policy == "zero":
            execute(
                "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse) "
                "SELECT %s, s.sku, 0, s.warehouse FROM inventory_stock s JOIN ism m "
                "ON m.sku = s.sku AND m.warehouse = s.warehouse WHERE s.tenant_id = %s AND s.current_stock <> 0",
                (tenant_id, tenant_id), conn=conn)
            done = query(
                "UPDATE inventory_stock s SET current_stock = 0, updated_at = NOW() FROM ism m "
                "WHERE s.tenant_id = %s AND s.sku = m.sku AND s.warehouse = m.warehouse "
                "AND s.current_stock <> 0 RETURNING s.sku", (tenant_id,), conn=conn)
            counters["missing_applied"] += len(done)
        else:
            counters["missing_applied"] += counters["missing"]
        execute(
            "UPDATE inventory_sync_keys k SET inactive = %s FROM ism m WHERE k.profile_id = %s "
            "AND k.sku = m.sku AND k.warehouse = m.warehouse", (policy == "mark_inactive", pid), conn=conn)
