"""
Inventory intelligence service.

Combines inventory_stock (current levels) with session forecast data
to produce per-SKU signals, ABC-XYZ classification, and order recommendations.
"""

import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.db.connection import query, query_one, execute
from backend.formatting import money, format_days as _format_days
from backend.inventory.defaults import (
    DEFAULT_LEAD_TIME_DAYS,
    DEFAULT_MOQ,
    SOURCE_DEFAULT,
    SOURCE_FILE,
    SOURCE_LEARNED,
    SOURCE_USER,
)

log = logging.getLogger(__name__)

# Z-scores for common service levels
_Z = {0.90: 1.282, 0.95: 1.645, 0.97: 1.881, 0.99: 2.326}
_SIGNAL_PRIORITY = {"PEDIR_YA": 0, "PEDIR_PRONTO": 1, "OK": 2, "SOBRESTOCK": 3, "SIN_DATOS": 4}


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_stock(
    tenant_id: str,
    sku: str,
    data: dict,
    conn: Optional[Any] = None,
    source: str = SOURCE_USER,
) -> dict:
    """
    `conn`: optional shared connection from db.connection.transaction(). When
    provided, every DB call this function makes runs on THAT connection and
    does not commit — the caller's transaction() commits once for the whole
    block (used by reception_service.receive_po to make a reception
    atomic). When omitted (the default), behavior is exactly as before: each
    DB call opens its own pooled connection and auto-commits immediately.

    `source`: WHO supplied these values, one of backend.inventory.defaults'
    VALUE_SOURCES. Only the provenance columns of the fields actually present in
    `data` are stamped, so a reception that updates current_stock never claims
    authorship of the lead time. Defaults to 'user' because this function is the
    chokepoint every human-driven write path funnels through; the dataset sync
    and the CSV import pass 'file' explicitly.

    This stamp is the whole point of the provenance migration: with
    `lead_time_days INT NOT NULL DEFAULT 15` alone, a SKU the buyer deliberately
    set to 15 and a SKU nobody ever opened were the same row. Now the first
    reports 'user' and the second 'default', and the explanation stops claiming
    a lead time was configured when it was assumed.
    """
    allowed = {
        "display_name", "current_stock", "min_stock",
        "lead_time_days", "unit_cost", "moq", "supplier", "notes",
        "service_level",
        "sale_price", "category", "family", "brand", "unit_of_measure", "barcode",
        "warehouse",
    }

    # warehouse is NOT NULL with a DB default of 'principal', but the ON CONFLICT
    # target now includes it, so the INSERT must always supply a value.
    if "warehouse" not in data:
        data = {**data, "warehouse": "principal"}

    safe = {k: v for k, v in data.items() if k in allowed}
    if not safe:
        raise ValueError("No valid fields to update")

    # Numeric floor guard — mirrors the _DATASET_STOCK_MIN sanitization applied
    # in sync_stock_from_dataset. upsert_stock is the one chokepoint every
    # direct (non-HTTP) caller (bulk_upsert, receive_po, demo/seed scripts)
    # funnels through; PUT/PATCH /stock already reject out-of-range values via
    # Pydantic's ge=0, but a direct call bypasses that entirely. Without this,
    # a 0/negative lead_time_days/current_stock/moq would corrupt the
    # reorder-point math the same way an unvalidated dataset column would (see
    # _DATASET_STOCK_MIN's docstring above). Out-of-range fields are dropped
    # (not the whole call rejected) so a partially-bad payload still saves the
    # fields that are valid, same graceful-degradation behavior as the sync path.
    for col, floor in _DATASET_STOCK_MIN.items():
        if col not in safe or safe[col] is None:
            continue
        try:
            numeric_val = float(safe[col])
        except (TypeError, ValueError):
            continue
        if numeric_val < floor:
            log.warning(
                "upsert_stock: dropped out-of-range %s=%r (floor=%s) sku=%s tenant=%s",
                col, safe[col], floor, sku, tenant_id,
            )
            del safe[col]
    if not safe:
        raise ValueError("No valid fields to update")

    # CHOKEPOINT: max_skus and max_locations must be enforced here, not
    # per-caller. Every write path (PUT /stock, PATCH /stock, POST /bulk,
    # receive_po, dataset sync, demo/seed scripts) funnels through this
    # function before it can create a new (tenant_id, sku, warehouse) row and
    # auto-create a warehouse via _ensure_warehouse below. Checking per-caller
    # was whack-a-mole — PATCH /stock/{sku} was missed for BOTH limits because
    # it 404-checks get_stock() without a warehouse filter, so it never knew
    # the target (sku, warehouse) pair was new. This runs BEFORE any
    # INSERT/UPDATE for this row, so a blocked call leaves the DB completely
    # unchanged (no partial write).
    from backend.entitlements.service import enforce_limit
    from backend.inventory import warehouse_service as wh_svc
    # Normalize-at-write, BEFORE the max_locations check below: ' norte ' must
    # resolve to an existing 'Norte' so it neither counts as a new location
    # nor creates case-variant duplicate warehouse/stock rows. This also
    # canonicalizes the name _ensure_warehouse (only reachable through here)
    # auto-creates further down.
    safe["warehouse"] = wh_svc.resolve_canonical_name(tenant_id, safe["warehouse"])
    # These chokepoint reads intentionally do NOT take `conn`: warehouse_service
    # is a separate module this fix doesn't own, and count_stock's role here is
    # only a pre-write sanity check, not a value this call's own writes below
    # depend on for correctness. When receive_po calls this inside a
    # transaction(), the outer pre-checks in receive_po already enforced the
    # limits for the WHOLE batch of writes before the transaction opened, so
    # this per-row check staying on its own connection is redundant-but-safe,
    # not a bypass.
    is_new_row = not get_stock(tenant_id, sku, warehouse=safe["warehouse"], conn=conn)
    if is_new_row:
        enforce_limit(tenant_id, "max_skus", count_stock(tenant_id))
    if not wh_svc.get_warehouse_by_name(tenant_id, safe["warehouse"]):
        enforce_limit(tenant_id, "max_locations", wh_svc.count_warehouses(tenant_id))

    # Stamp provenance for exactly the tracked fields this call actually writes.
    # Added to `safe` (not written separately) so the value and its provenance
    # land in the SAME statement — they can never disagree, not even if the
    # process dies between two writes.
    from backend.inventory.defaults import PROVENANCE_FIELDS, VALUE_SOURCES
    from backend.inventory.stock_defaults_service import _provenance_column

    if source not in VALUE_SOURCES:
        raise ValueError(f"source must be one of {sorted(VALUE_SOURCES)}")
    for field in PROVENANCE_FIELDS:
        if field in safe:
            safe[_provenance_column(field)] = source

    cols   = ", ".join(safe.keys())
    values = list(safe.values())
    phs    = ", ".join(["%s"] * len(safe))
    # warehouse is the conflict target, never itself assignable in the update
    # clause; when it's the ONLY field supplied, upd_parts is empty and the
    # SET clause must fall back to just touching updated_at (an empty
    # "SET , updated_at = NOW()" is a SQL syntax error).
    upd_parts = [f"{k} = EXCLUDED.{k}" for k in safe if k != "warehouse"]
    upd = (", ".join(upd_parts) + ", ") if upd_parts else ""

    execute(
        f"""INSERT INTO inventory_stock (tenant_id, sku, {cols}, updated_at)
            VALUES (%s, %s, {phs}, NOW())
            ON CONFLICT (tenant_id, sku, warehouse) DO UPDATE
            SET {upd}updated_at = NOW()""",
        (tenant_id, sku, *values),
        conn=conn,
    )
    _ensure_warehouse(tenant_id, safe["warehouse"], conn=conn)

    row = get_stock(tenant_id, sku, warehouse=safe["warehouse"], conn=conn)

    # Auto-snapshot when current_stock is updated
    if "current_stock" in safe and row:
        _record_snapshot(tenant_id, sku, float(safe["current_stock"]), conn=conn)

    return row


def _ensure_warehouse(tenant_id: str, name: str, conn: Optional[Any] = None) -> None:
    """Auto-create a `warehouses` row the first time a warehouse name is seen for
    this tenant. Best-effort: a warehouse-insert hiccup must never fail the
    stock write it's attached to.

    `name` is expected to already be canonical: the only caller (upsert_stock)
    runs it through warehouse_service.resolve_canonical_name before the
    chokepoint checks, so a case-variant of an existing warehouse never
    reaches this INSERT.

    `conn`: see upsert_stock's docstring — when provided, runs on the caller's
    shared transaction connection instead of its own auto-committing one.
    """
    try:
        execute(
            "INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s) "
            "ON CONFLICT (tenant_id, name) DO NOTHING",
            (tenant_id, name),
            conn=conn,
        )
    except Exception as e:
        log.warning("_ensure_warehouse: failed to upsert warehouse=%s tenant=%s err=%s", name, tenant_id, e)


def count_stock(tenant_id: str) -> int:
    row = query_one("SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id = %s", (tenant_id,))
    return row["c"] if row else 0


def list_stock_keys(tenant_id: str) -> set:
    """(sku, warehouse) pairs already present for this tenant — the same
    conflict target `upsert_stock` writes to, used to tell how many rows a
    bulk import would actually ADD (vs. update in place)."""
    rows = query(
        "SELECT sku, warehouse FROM inventory_stock WHERE tenant_id = %s",
        (tenant_id,),
    )
    return {(r["sku"], r["warehouse"]) for r in rows}


def get_stock(
    tenant_id: str, sku: str, warehouse: Optional[str] = None, conn: Optional[Any] = None
) -> Optional[dict]:
    """
    `conn`: see upsert_stock's docstring — pass the transaction() connection
    to read back a row this SAME transaction just wrote (needed because an
    uncommitted write is invisible to any other connection).
    """
    if warehouse is not None:
        return query_one(
            "SELECT * FROM inventory_stock WHERE tenant_id = %s AND sku = %s AND warehouse = %s",
            (tenant_id, sku, warehouse),
            conn=conn,
        )
    return query_one(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
        conn=conn,
    )


def list_stock(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s ORDER BY sku",
        (tenant_id,),
    )


def delete_stock(tenant_id: str, sku: str) -> None:
    execute(
        "DELETE FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )


# Dataset columns we recognize as inventory data when present in an uploaded file.
_DATASET_STOCK_FLOAT_COLS = {"current_stock", "min_stock", "unit_cost", "moq", "service_level", "sale_price"}
_DATASET_STOCK_INT_COLS   = {"lead_time_days"}
_DATASET_STOCK_STR_COLS   = {"supplier", "notes", "display_name", "category", "family", "brand", "unit_of_measure", "barcode"}
_DATASET_STOCK_COLS = _DATASET_STOCK_FLOAT_COLS | _DATASET_STOCK_INT_COLS | _DATASET_STOCK_STR_COLS

# Minimum valid value per numeric dataset column, mirroring the ge=0/ge=1
# bounds StockUpsert/StockPatch enforce on every OTHER inventory write path
# (PUT /stock, PATCH /stock, POST /bulk). sync_stock_from_dataset is the one
# path that parses a numeric column straight out of a user's sales-history
# file with no Pydantic validation in front of it — without this floor, a
# stray 0 in a "lead_time_days" column would collapse every _calc_signal
# threshold to 0 (lead_time * 0.5/1.2/3 are all 0), permanently misreporting
# the SKU as SOBRESTOCK regardless of real coverage and silently hiding a
# stockout risk. A stray negative current_stock/moq would similarly corrupt
# the reorder-point math. Columns not listed here (e.g. service_level) have
# no hard floor: an out-of-range value just falls back to the default z-score
# (see get_inventory_status), which is a documented, harmless fallback.
_DATASET_STOCK_MIN = {
    "current_stock": 0.0, "min_stock": 0.0, "unit_cost": 0.0, "sale_price": 0.0,
    "moq": 1.0, "lead_time_days": 1,
}


# Quick Start wizard field -> inventory_stock column. The wizard collects these
# under CANONICAL_FIELDS names ("inventory", "lead_time", "cost", "price") while
# inventory_stock stores them under its own. Nothing translated between the two,
# so for every canonical_v1 session — i.e. the whole onboarding path — this sync
# read the file looking for column names the wizard never produces and seeded
# nothing. Verified on a completed training whose mapping had price mapped:
# inventory_stock came out with 0 rows.
_CANONICAL_TO_STOCK = {
    "inventory": "current_stock",
    "lead_time": "lead_time_days",
    "cost":      "unit_cost",
    "price":     "sale_price",
}


def _mapped_canonical_columns(canonical_mapping: Optional[dict]) -> dict:
    """{canonical column -> stock column} for fields the user ACTUALLY mapped.

    The filter is the whole point, not a nicety. `apply_canonical_defaults`
    broadcasts a default into every unmapped canonical column — inventory 0,
    lead_time 7 — so those columns are always present in the DataFrame. Reading
    them unconditionally would write current_stock=0 across the entire catalogue
    of every tenant and fire PEDIR_YA on all of it. A field counts only when the
    mapping names a real source column for it.
    """
    if not canonical_mapping:
        return {}
    return {
        field: stock_col
        for field, stock_col in _CANONICAL_TO_STOCK.items()
        if canonical_mapping.get(field)
    }


def sync_stock_from_dataset(
    tenant_id: str,
    df,
    group_col: Optional[str],
    date_col: str,
    canonical_mapping: Optional[dict] = None,
) -> int:
    """
    If the uploaded dataset contains recognized inventory columns (current_stock,
    lead_time_days, unit_cost, moq, supplier, notes, display_name,
    min_stock, service_level), seed/update inventory_stock with the most
    recent value per SKU. This is what lets a Quick Start upload actually
    control what /inventory shows, instead of /inventory silently falling back
    to whatever was entered manually in a previous session.
    """
    from fastapi import HTTPException
    from backend.dataframes.stock import last_row_per_group

    # Pandas extraction lives at the boundary: latest row per SKU with raw
    # (unfloored) values, NaN cells dropped. Empty / no-recognized-columns
    # datasets come back as [].
    canonical_cols = _mapped_canonical_columns(canonical_mapping)
    # A file whose own header already says "current_stock" keeps winning: the
    # canonical alias is only consulted for a field the native name did not
    # supply, so this can add data but never override it.
    wanted = set(_DATASET_STOCK_COLS) | set(canonical_cols)
    raw_entries = last_row_per_group(df, group_col, date_col, wanted)

    # Resolve the per-SKU payload with the numeric floors up front (before the
    # max_skus check), exactly as before — only the pandas extraction moved out.
    entries: list[tuple[str, dict]] = []
    for sku, raw in raw_entries:
        data: dict = {}
        renamed = {
            canonical_cols[k]: v for k, v in raw.items()
            if k in canonical_cols and canonical_cols[k] not in raw
        }
        for col, val in {**renamed, **{k: v for k, v in raw.items()
                                       if k in _DATASET_STOCK_COLS}}.items():
            if col in _DATASET_STOCK_FLOAT_COLS:
                parsed_float = float(val)
                floor = _DATASET_STOCK_MIN.get(col)
                if floor is not None and parsed_float < floor:
                    continue
                data[col] = parsed_float
            elif col in _DATASET_STOCK_INT_COLS:
                parsed_int = int(val)
                floor = _DATASET_STOCK_MIN.get(col)
                if floor is not None and parsed_int < floor:
                    continue
                data[col] = parsed_int
            else:
                data[col] = str(val)
        if not data:
            continue
        entries.append((sku, data))

    if not entries:
        return 0

    # CHOKEPOINT (pre-loop): this is the PRIMARY way SKUs enter Faro (Quick
    # Start upload), yet unlike PUT /stock and POST /bulk it had no max_skus
    # check at all — a Starter tenant could seed thousands of SKUs in one
    # upload. Computed BEFORE the loop, atomically over the WHOLE dataset, so
    # a blocked sync leaves inventory_stock completely unchanged rather than
    # inserting rows until the per-row upsert_stock chokepoint finally objects
    # partway through (mirrors POST /bulk's pre-loop max_locations/max_skus
    # checks). Dataset rows never carry an explicit warehouse (not in
    # _DATASET_STOCK_COLS), so every new key lands in "principal".
    existing_keys = list_stock_keys(tenant_id)
    new_keys = {(sku, "principal") for sku, _ in entries} - existing_keys
    from backend.entitlements.service import enforce_limit
    enforce_limit(tenant_id, "max_skus", count_stock(tenant_id), adding=len(new_keys))

    count = 0
    for sku, data in entries:
        try:
            # 'file' provenance: these values came off the user's upload, which
            # is their data — not our assumption — but also not something they
            # typed on the SKU card. The distinction matters for the copy: "el
            # lead time que venía en tu archivo" is a claim we can actually back.
            upsert_stock(tenant_id, sku, data, source=SOURCE_FILE)
            count += 1
        except HTTPException:
            # A plan-limit 403 from the per-row chokepoint must propagate, not
            # be swallowed as a skipped row — see the bare-except note this
            # replaces. The pre-loop check above should make this unreachable
            # in practice; this is defense in depth for future callers.
            raise
        except Exception as e:
            log.warning("sync_stock_from_dataset: skipped sku=%s err=%s", sku, e)
    return count


# Columns a re-import ALWAYS refreshes, even under only_fill_missing.
#
# current_stock is a measurement, not a configuration: refreshing it is the
# entire reason a distributor re-uploads their ERP export, and a stale quantity
# is strictly worse than a fresh one. Protecting it would have made
# only_fill_missing turn the stock importer into a no-op for every SKU that
# already exists — a silent failure worse than the overwrite it prevents.
# Everything else under only_fill_missing is protected: see _fields_to_fill.
_ALWAYS_REFRESHED_ON_IMPORT = frozenset({"current_stock"})


def _fields_to_fill(existing: Optional[dict], data: dict, source: str) -> dict:
    """The subset of `data` a fill-missing import is allowed to write.

    "Did the user set this?" is exactly what the provenance columns answer, so
    they are the decision input rather than a bare NULL check — which could not
    have worked anyway for lead_time_days / moq / service_level, since all three
    are NOT NULL with a schema default and are therefore *never* null.

      - a value whose `<field>_set_by` is 'user' survives a file import: a lead
        time the buyer corrected by hand in March must not be silently reverted
        by April's ERP export;
      - a value that came from a previous 'file' import, or that nobody ever set,
        IS refreshed — that is the import doing its job;
      - a write whose own source is 'user' (a human editing) may overwrite
        anything, so this guard never blocks the buyer themselves;
      - fields with no provenance column (supplier, category, notes, …) fall back
        to the plain "only if currently empty" rule.
    """
    if existing is None:
        return data

    from backend.inventory.defaults import PROVENANCE_FIELDS
    from backend.inventory.stock_defaults_service import _provenance_column

    out: dict = {}
    for field, value in data.items():
        if field in _ALWAYS_REFRESHED_ON_IMPORT or field == "warehouse":
            out[field] = value
            continue
        if field in PROVENANCE_FIELDS:
            owner = existing.get(_provenance_column(field))
            if owner == SOURCE_USER and source != SOURCE_USER:
                continue
            out[field] = value
            continue
        current = existing.get(field)
        if current is None or (isinstance(current, str) and not current.strip()):
            out[field] = value
    return out


def bulk_upsert(
    tenant_id: str,
    rows: list[dict],
    source: str = SOURCE_FILE,
    only_fill_missing: bool = False,
) -> int:
    """Upsert multiple SKUs from a CSV/bulk import. Returns count saved.

    `source` defaults to 'file' because that is what this function is for — a
    stock CSV the user uploaded. Callers that are replaying values the user
    typed (none today) can override it.

    `only_fill_missing` makes a re-import additive: it fills what is unset and
    refreshes what a previous import set, but never overwrites a value the buyer
    edited by hand. Without it a monthly ERP re-export silently erases every
    manual correction accumulated since the last one — the same class of quiet
    data loss the provenance columns exist to make visible. Defaults to False so
    every existing caller behaves exactly as before.
    """
    from fastapi import HTTPException

    count = 0
    for row in rows:
        sku = row.get("sku", "").strip()
        if not sku:
            continue
        try:
            data = {k: v for k, v in row.items() if k != "sku"}
            if only_fill_missing:
                # Read the row ONCE here rather than inside upsert_stock: the
                # decision needs the provenance columns, and skipping the write
                # entirely when nothing survives the filter keeps updated_at
                # honest (an import that changed nothing did not touch the row).
                # Resolve the warehouse name the same way upsert_stock will, or
                # a case-variant (' norte ') would miss the existing row and be
                # treated as brand new — i.e. overwrite everything.
                from backend.inventory import warehouse_service as _wh_svc
                warehouse = _wh_svc.resolve_canonical_name(
                    tenant_id, data.get("warehouse") or "principal")
                data = _fields_to_fill(
                    get_stock(tenant_id, sku, warehouse=warehouse), data, source)
                if not {k for k in data if k != "warehouse"}:
                    continue
            upsert_stock(tenant_id, sku, data, source=source)
            count += 1
        except HTTPException:
            # A plan-limit 403 from the per-row chokepoint must propagate, not
            # be swallowed as a skipped row. The caller (POST /bulk) already
            # runs a pre-loop max_skus/max_locations check, so this should be
            # unreachable in practice — defense in depth for future callers.
            raise
        except Exception as e:
            log.warning("bulk_upsert: skipped sku=%s err=%s", sku, e)
    return count


# ── Stock snapshots ───────────────────────────────────────────────────────────

def _record_snapshot(
    tenant_id: str, sku: str, current_stock: float, conn: Optional[Any] = None
) -> None:
    """Record a point-in-time stock level. Called automatically on upsert.

    `conn`: see upsert_stock's docstring.
    """
    try:
        execute(
            "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock) VALUES (%s, %s, %s)",
            (tenant_id, sku, current_stock),
            conn=conn,
        )
    except Exception as e:
        log.warning("snapshot record failed sku=%s: %s", sku, e)


def get_stock_history(tenant_id: str, sku: str, days: int = 30) -> list[dict]:
    """Returns daily stock snapshots for the last N days, most recent last."""
    from datetime import timezone
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = query(
        """SELECT current_stock, recorded_at
           FROM inventory_snapshots
           WHERE tenant_id = %s AND sku = %s AND recorded_at >= %s
           ORDER BY recorded_at ASC""",
        (tenant_id, sku, since),
    )
    return [{"stock": r["current_stock"], "date": r["recorded_at"].isoformat()} for r in rows]


# ── ABC-XYZ classification ────────────────────────────────────────────────────

def _classify_xyz(cv: Optional[float]) -> str:
    """
    X = low variability (predictable), Y = moderate, Z = high (erratic).
    Uses coefficient of variation from the series analysis.
    """
    if cv is None:
        return "?"
    if cv < 0.5:
        return "X"
    if cv < 1.0:
        return "Y"
    return "Z"


def _classify_abc(items: list[dict]) -> dict[str, str]:
    """
    A = top 80% cumulative revenue proxy, B = next 15%, C = rest.
    Revenue proxy = daily_demand * unit_cost (or just daily_demand if no cost).
    """
    scored = []
    for item in items:
        demand = item.get("daily_demand") or 0.0
        cost   = item.get("unit_cost") or 1.0
        scored.append((item["sku"], demand * cost))

    scored.sort(key=lambda x: x[1], reverse=True)
    total = sum(v for _, v in scored)

    if total == 0:
        return {sku: "C" for sku, _ in scored}

    result: dict[str, str] = {}
    cumulative = 0.0
    for sku, val in scored:
        # Assign tier based on cumulative BEFORE adding this item,
        # so a single dominant SKU (e.g. 99% revenue) gets classified as A not C.
        if cumulative < 0.80:
            result[sku] = "A"
        elif cumulative < 0.95:
            result[sku] = "B"
        else:
            result[sku] = "C"
        cumulative += val / total
    return result


# ── Signal calculation ────────────────────────────────────────────────────────

def _avg_daily_forecast(model_forecasts: dict, lead_time: int) -> tuple[float, float]:
    """
    Returns (avg_daily_demand, avg_daily_std) across all models,
    using the first `lead_time` forecast steps.
    """
    all_values: list[float] = []
    all_stds:   list[float] = []

    for model_data in model_forecasts.values():
        pts = model_data.get("forecast", [])[:lead_time]
        if not pts:
            continue
        vals = [p.get("value") or 0.0 for p in pts]
        stds = [
            ((p.get("upper") or p.get("value") or 0) - (p.get("value") or 0))
            for p in pts
        ]
        all_values.extend(vals)
        all_stds.extend(stds)

    if not all_values:
        return 0.0, 0.0

    avg_daily = sum(all_values) / len(all_values)
    avg_std   = sum(all_stds) / len(all_stds) if all_stds else 0.0
    return max(0.0, avg_daily), max(0.0, avg_std)


def _avg_forecast_curve(model_forecasts: dict, max_steps: int = 90) -> list[dict]:
    """
    Averages the per-step forecast value across all models, aligned by step
    index, returning a chronological [{step, date, value}] curve. Dates come
    from whichever model provides them (all models share the same horizon).
    """
    step_values: dict[int, list[float]] = {}
    step_dates:  dict[int, str] = {}
    for model_data in model_forecasts.values():
        pts = model_data.get("forecast", []) or []
        for idx, p in enumerate(pts[:max_steps]):
            v = p.get("value")
            if v is None:
                continue
            step_values.setdefault(idx, []).append(float(v))
            if idx not in step_dates and p.get("date"):
                step_dates[idx] = str(p["date"])[:10]

    curve: list[dict] = []
    for idx in sorted(step_values):
        vals = step_values[idx]
        if vals:
            curve.append({"step": idx, "date": step_dates.get(idx), "value": sum(vals) / len(vals)})
    return curve


# ── Period-aware planning (multi-period Phase C) ──────────────────────────────
# A period-trained session (Phase A) forecasts PER-PERIOD demand: a weekly
# session's forecast values are units/week, a monthly session's are units/month.
# Coverage therefore comes out in periods, and the signal must be judged against
# the lead time expressed in that SAME period. This map is the only conversion
# factor; every helper below is plain arithmetic (no pandas).
_DAYS_PER_PERIOD = {"daily": 1, "weekly": 7, "monthly": 30}

# The coverage unit the API exposes for each period, mirroring _COVERAGE_UNIT in
# backend/api/v1/inventory.py. Kept here (not imported) so the service layer
# never depends on the API layer.
_COVERAGE_UNIT = {"daily": "day", "weekly": "week", "monthly": "month"}


def _days_per_period(period: Optional[str]) -> int:
    """Calendar days in one bucket of `period`. Unknown/legacy -> 1 (daily), so
    a bad value degrades to today's day-based math rather than raising."""
    return _DAYS_PER_PERIOD.get(period or "daily", 1)


def _coverage_unit(period: Optional[str]) -> str:
    """The active period's coverage unit (day/week/month). Unknown/legacy ->
    'day', matching how _days_per_period degrades to daily."""
    return _COVERAGE_UNIT.get(period or "daily", "day")


def _lead_time_in_periods(lead_time_days: float, period: str) -> float:
    """Lead time expressed in the active period's units. Kept float so the
    signal thresholds stay precise (a 15-day lead time is 2.14 weeks). For
    `daily` this is exactly float(lead_time_days) — the identity that keeps the
    daily semáforo byte-identical to before Phase C."""
    return float(lead_time_days) / _days_per_period(period)


def _steps_for_lead_time(lead_time_days: float, period: str) -> int:
    """How many forecast buckets to average when estimating per-period demand:
    the lead time rounded UP to whole periods, at least one (a sub-period lead
    time still needs one bucket to average). For `daily` this equals
    int(lead_time_days) for any positive integer lead time."""
    return max(1, math.ceil(float(lead_time_days) / _days_per_period(period)))


def _calc_signal(coverage_days: float, lead_time: int) -> str:
    if coverage_days < lead_time * 0.5:
        return "PEDIR_YA"
    if coverage_days < lead_time * 1.2:
        return "PEDIR_PRONTO"
    if coverage_days < lead_time * 3:
        return "OK"
    return "SOBRESTOCK"


def _calc_recommended(
    current_stock: float,
    avg_daily: float,
    avg_std: float,
    lead_time: int,
    moq: float,
    service_level: float = 0.95,
) -> float:
    z = _Z.get(service_level, 1.645)
    lead_time_demand = avg_daily * lead_time
    safety_stock = z * avg_std * math.sqrt(lead_time)
    raw = max(0.0, lead_time_demand + safety_stock - current_stock)
    if moq and moq > 0:
        raw = math.ceil(raw / moq) * moq
    return float(round(raw, 2))


# Signals for which recommending an order is meaningful. On any other signal
# (OK / SOBRESTOCK / SIN_DATOS) the semáforo says stock is sufficient, so the
# suggested quantity MUST be 0 — otherwise a healthy SKU shows "order N".
_ORDERING_SIGNALS = ("PEDIR_YA", "PEDIR_PRONTO")


def _gate_recommended_by_signal(signal: str, recommended: float) -> float:
    """Zero the recommendation unless the signal actually calls for ordering."""
    if signal in _ORDERING_SIGNALS:
        return float(recommended)
    return 0.0


# Minimum receptions before the learned average is allowed to replace the
# configured lead time. With a single observation, one freak delivery (a public
# holiday, a strike, a stranded truck) would rewrite that supplier's lead time
# for ALL of their SKUs, moving the signal because of an accident. Three is the
# point where the average starts describing the supplier, not the incident.
#
# Consistency note: the deviation alert (`supplier_health_service`) requires >=6
# receptions before accusing a supplier of running late. It is right to be
# stricter — accusing costs more than adjusting. But trusting at n=1 while
# accusing at n=6 was a contradiction.
MIN_LEAD_TIME_OBSERVATIONS = 3


def get_learned_lead_times(tenant_id: str) -> dict[str, float]:
    """
    Average REAL lead time per supplier, learned from recorded PO receptions
    (`supplier_lead_time_obs`, written by reception_service.receive_po).
    Keys are lower-cased supplier names so callers can match case-insensitively.
    Suppliers with fewer than MIN_LEAD_TIME_OBSERVATIONS receptions are absent
    from the map — the caller then falls back to the lead time configured on the
    SKU, which is the honest answer while the evidence is still thin.
    """
    rows = query(
        """SELECT LOWER(supplier) AS supplier, AVG(lead_time_days) AS avg_days
           FROM supplier_lead_time_obs
           WHERE tenant_id = %s
           GROUP BY LOWER(supplier)
           HAVING COUNT(*) >= %s""",
        (tenant_id, MIN_LEAD_TIME_OBSERVATIONS),
    )
    return {
        r["supplier"]: float(r["avg_days"])
        for r in rows
        if r.get("supplier") and r.get("avg_days") is not None
    }


def get_supplier_observation_counts(tenant_id: str) -> dict[str, int]:
    """
    Recorded receptions per supplier, INCLUDING the ones still below
    MIN_LEAD_TIME_OBSERVATIONS.

    `get_learned_lead_times` deliberately drops those — thin evidence must not
    move a recommendation — but that is exactly why the UI needs this map: to
    show progress towards the threshold it has to see the counts the planner is
    still ignoring, otherwise "we will adjust the lead time on our own" is a
    promise with no visible progress bar.

    Keyed on the lower-cased free-text supplier name from the PO lines, like
    `supplier_lead_time_obs` itself — a supplier with no ficha in `suppliers`
    still accumulates observations and still gets its lead time learned.
    """
    rows = query(
        """SELECT LOWER(supplier) AS supplier, COUNT(*)::int AS n
           FROM supplier_lead_time_obs
           WHERE tenant_id = %s
           GROUP BY LOWER(supplier)""",
        (tenant_id,),
    )
    return {r["supplier"]: int(r["n"]) for r in rows if r.get("supplier")}


def resolve_lead_time(
    configured: int,
    supplier: Optional[str],
    learned_by_supplier: dict[str, float],
    configured_source: str = SOURCE_USER,
) -> tuple[int, str, Optional[float]]:
    """
    The lead time a recommendation is actually built on.

    Prefers the lead time LEARNED from this supplier's real receptions over the
    one typed into the SKU card — a supplier who says 7 days but consistently
    delivers in 12 must not keep producing recommendations that assume 7.
    Evidence outranks declaration; that ordering is unchanged.

    `configured_source` is where the fallback value came from, already resolved
    by the SKU > supplier-rule > category > global > system cascade
    (`stock_defaults_service.resolve_field`). It is passed through untouched
    when the evidence does not fire.

    Returns (lead_time_days, source, learned_raw). `source` is one of the five
    values in `backend.inventory.defaults` — user | file | supplier_rule |
    learned | default. It used to be 'learned' | 'configured', where
    'configured' was a lie for every tenant who had configured nothing: the DB
    could not tell a chosen 15 from an untouched one, so the UI said
    "configurado por ti" about a number we invented.
    """
    if supplier:
        learned = learned_by_supplier.get(supplier.strip().lower())
        if learned is not None and learned > 0:
            return max(1, int(round(learned))), SOURCE_LEARNED, round(learned, 1)
    return configured, configured_source, None


def calc_unit_margin(
    sale_price: Optional[float],
    unit_cost: Optional[float],
) -> Optional[float]:
    """
    Gross margin per unit. None (not 0) when either side is missing — the cart
    summary must be able to tell "this SKU contributes 0 margin" apart from
    "we don't know this SKU's margin", and report the second as excluded.
    A negative margin (selling below cost) is reported as-is, never clamped:
    hiding it would make a loss-making order look profitable.
    """
    if sale_price is None or unit_cost is None:
        return None
    return round(float(sale_price) - float(unit_cost), 2)


def _english_days(n: float) -> str:
    """Day count that agrees in number, in English. `formatting.format_days` is
    the Spanish sibling; the explanation's fallback text must stay English (see
    CLAUDE.md — no Spanish string literals in backend logic)."""
    rounded = round(n)
    return "1 day" if rounded == 1 else f"{rounded:,.0f} days"


# Which of the five provenance values the explanation is describing decides the
# clause about the lead time. 'default' is the one that used to lie.
_LEAD_TIME_CLAUSE_EN = {
    SOURCE_LEARNED: "your supplier takes {days} to deliver (learned from their real deliveries)",
    SOURCE_USER:    "your supplier takes {days} to deliver (lead time you configured)",
    SOURCE_FILE:    "your supplier takes {days} to deliver (lead time from your file)",
    "supplier_rule": "your supplier takes {days} to deliver (rule for this {scope})",
    SOURCE_DEFAULT: "we assume {days} because you have not configured this supplier yet",
}


def build_explanation(
    current_stock: float,
    daily_demand: float,
    coverage_days: Optional[float],
    lead_time: int,
    lead_time_source: str,
    reorder_point: float,
    signal: str,
    lead_time_rule_scope: Optional[str] = None,
) -> dict:
    """
    The reasoning behind a recommendation, as a STRUCTURED value:
    ``{"code": ..., "params": {...}, "text": "<English fallback>"}``.

    Two things changed here and both matter.

    1. It no longer claims the lead time was configured when it was assumed.
       When `lead_time_source` is 'default' the sentence says "we assume 15 days
       because you have not configured this supplier yet" — the honest version of
       what the product used to assert. This sentence is the one we use to earn
       the buyer's trust; getting it wrong is worse than not showing it.

    2. It no longer returns a hardcoded Spanish sentence. The business reasoning
       still lives here (it IS business logic, not presentation), but it leaves
       as a stable code plus its parameters, and the frontend renders the Spanish
       from `translations.ts` — the pattern `AppError` established for errors,
       and what CLAUDE.md requires of every user-facing string.

    `text` is the English fallback, shown only by a client that has no mapping
    for `code`.
    """
    scope = lead_time_rule_scope or "supplier"

    if daily_demand <= 0:
        # No projected sales at all: coverage is effectively unlimited, saying
        # "it lasts you N days" would be nonsense.
        params = {"current_stock": round(float(current_stock), 2)}
        return {
            "code": "inventory_explain_no_demand",
            "params": params,
            "text": (
                f"You have {current_stock:,.0f} units and the forecast projects no sales "
                f"for this product, so there is nothing to replenish right now."
            ),
        }

    params = {
        "current_stock": round(float(current_stock), 2),
        "daily_demand": round(float(daily_demand), 2),
        # None means "coverage runs past the forecast horizon" — a real state,
        # not a missing value, and the copy has its own clause for it.
        "coverage_days": round(float(coverage_days), 1) if coverage_days is not None else None,
        "lead_time_days": int(lead_time),
        "lead_time_source": lead_time_source,
        # Which level of the SKU > supplier > category > global cascade won, so
        # the copy can state the precedence explicitly instead of leaving the
        # buyer to guess why this SKU shows this number.
        "lead_time_rule_scope": lead_time_rule_scope,
        "reorder_point": round(float(reorder_point), 2),
        "signal": signal,
    }

    coverage_en = (
        f"it lasts you {_english_days(coverage_days)}"
        if coverage_days is not None
        else "coverage runs past the forecast horizon"
    )
    lead_en = _LEAD_TIME_CLAUSE_EN.get(
        lead_time_source, _LEAD_TIME_CLAUSE_EN[SOURCE_DEFAULT]
    ).format(days=_english_days(lead_time), scope=scope)
    base = (
        f"You have {current_stock:,.0f} units and sell {daily_demand:,.1f} per day, "
        f"so {coverage_en}. Since {lead_en}, you should reorder when stock drops to "
        f"{reorder_point:,.0f} units"
    )
    if signal == "PEDIR_YA":
        text = base + " — you are already below that point, which is why it shows as urgent."
    elif signal == "PEDIR_PRONTO":
        text = base + " — you are getting close to that point, so it is worth ordering this week."
    else:
        text = base + "."

    return {"code": "inventory_explain_reorder", "params": params, "text": text}


def _aggregate_stock_rows_by_sku(stock_rows: list[dict]) -> dict[str, dict]:
    """
    Collapse per-warehouse inventory_stock rows into one summary row per SKU:
    current_stock is SUMMED across warehouses (true total stock the tenant
    holds); every other field (lead_time_days, unit_cost, supplier,
    etc.) is taken from a single deterministic representative row (the
    default warehouse if present, else the casefolded-alphabetically-first
    warehouse — warehouse_service.name_precedence_key) — those are per-SKU
    catalog attributes, not per-warehouse quantities, so picking one is
    correct as long as it's deterministic.

    This is a hot path fed ONLY stock rows: precedence is decided by NAME
    (shared name_precedence_key), deliberately without a DB query for
    warehouses.is_default — see the key's docstring.
    """
    from backend.inventory.warehouse_service import name_precedence_key

    by_sku: dict[str, list[dict]] = {}
    for r in stock_rows:
        by_sku.setdefault(r["sku"], []).append(r)

    result: dict[str, dict] = {}
    for sku, rows in by_sku.items():
        rows_sorted = sorted(rows, key=lambda r: name_precedence_key(r.get("warehouse")))
        representative = dict(rows_sorted[0])
        representative["current_stock"] = sum(float(r["current_stock"] or 0) for r in rows)
        result[sku] = representative
    return result


# ── Main status calculation ───────────────────────────────────────────────────

def get_inventory_status(tenant_id: str, session_id: str, service_level: float = 0.95,
                         period: str = "daily") -> list[dict]:
    """
    Merges inventory_stock with session forecast.
    Includes ABC-XYZ classification, stock trend, and order recommendation.

    `period` (multi-period Phase C): the active planning grain. The session's
    forecast values are per-period demand at that grain, so coverage comes out
    in periods and the signal is judged against the lead time in periods. The
    default "daily" reproduces today's output byte-for-byte (the period helpers
    are the identity for daily).

    Thin public wrapper (positional signature frozen — API + alert/snapshot
    callers): the actual work, including preloaded-data reuse, lives in
    _compute_inventory_status below.
    """
    return _compute_inventory_status(tenant_id, session_id, service_level, period=period)


def _compute_inventory_status(
    tenant_id: str, session_id: str, service_level: float = 0.95,
    *,
    forecasts: Optional[dict] = None,
    stock_rows: Optional[list] = None,
    learned_lead_times: Optional[dict] = None,
    period: str = "daily",
) -> list[dict]:
    """
    Implementation of get_inventory_status. The keyword-only args accept
    preloaded data (raw get_forecasts blob, list_stock rows,
    get_learned_lead_times map) so run_daily_inventory_alerts can fetch each
    ONCE per tenant and share them with get_inventory_status_by_warehouse
    instead of double-fetching; None means fetch here as always. Inputs are
    never mutated (rollup_by_sku copies).
    """
    from backend.db import session_store
    from backend.inventory.series import rollup_by_sku

    if forecasts is None:
        forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
    # Store-keyed sessions ("sku│store") collapse to per-SKU totals here — this
    # view is the whole-tenant aggregate; the per-warehouse view is
    # get_inventory_status_by_warehouse. Legacy dicts pass through unchanged.
    forecasts = rollup_by_sku(forecasts)

    # Try to pull CV per SKU from the quality report stored in training_result
    cv_by_sku: dict[str, Optional[float]] = {}
    try:
        result = session_store.get_training_result(tenant_id, session_id) or {}
        quality: dict = result.get("data_quality") or {}
        for sku_key, q in quality.items():
            if isinstance(q, dict):
                cv_by_sku[str(sku_key)] = q.get("cv")
    except Exception as e:
        log.debug("cv_by_sku lookup failed for session=%s: %s", session_id, e)

    if stock_rows is None:
        stock_rows = list_stock(tenant_id)
    stock_map = _aggregate_stock_rows_by_sku(stock_rows)

    # Scope strictly to the SKUs forecast in THIS session. inventory_stock is a
    # tenant-wide table (no session_id column) that accumulates rows from every
    # session ever run for this tenant, so it must never be the source of which
    # SKUs to display — only of the stock fields to enrich a SKU already present
    # in the active session's forecasts. Otherwise, stale/unrelated SKUs from
    # past sessions leak into sessions that never uploaded them.
    all_skus = sorted(forecasts.keys())

    # Real lead times learned from recorded receptions, one query for the whole
    # tenant (never per SKU inside the loop).
    if learned_lead_times is None:
        learned_lead_times = get_learned_lead_times(tenant_id)

    # How many receptions each supplier has recorded so far — including the
    # ones still short of the threshold, which `learned_lead_times` filters out.
    # Without it the UI can say "we assumed this lead time" but not "and here is
    # what has to happen for us to stop assuming it".
    try:
        observation_counts = get_supplier_observation_counts(tenant_id)
    except Exception as e:
        log.debug("supplier observation counts failed tenant=%s: %s", tenant_id, e)
        observation_counts = {}

    # Per-SKU primary supplier (sku_suppliers), one query for the whole tenant.
    # The stock row's free-text supplier still wins when set — it is what the
    # buyer typed on the SKU card — but a SKU with no name there now inherits
    # its configured primary instead of showing "sin proveedor".
    from backend.inventory import supplier_service as _sup_svc
    from backend.inventory import stock_defaults_service as _sd_svc
    try:
        primary_suppliers = _sup_svc.get_primary_suppliers_map(tenant_id)
    except Exception as e:
        log.debug("primary supplier map lookup failed tenant=%s: %s", tenant_id, e)
        primary_suppliers = {}

    # Supplier/category/global planning rules, one query for the whole tenant.
    # A distributor configures 12 suppliers, not 2.000 SKUs — this is where that
    # configuration enters the recommendation.
    rule_index = _sd_svc.build_rule_index(tenant_id)

    items: list[dict] = []

    for sku in all_skus:
        stock = stock_map.get(sku)
        model_forecasts = forecasts.get(sku, {})

        primary           = primary_suppliers.get(sku) or {}
        supplier          = (stock.get("supplier") if stock else None) or primary.get("supplier_name")
        supplier_id       = primary.get("supplier_id") if supplier == primary.get("supplier_name") else None
        category          = stock.get("category") if stock else None
        # SKU > supplier rule > category rule > global rule > system default,
        # reporting which level won. A stock row whose lead_time_days is 15
        # only because that is the schema default does NOT count as configured —
        # that is exactly what the provenance columns exist to distinguish.
        _lt_cfg, lead_time_config_source, lead_time_rule_scope = _sd_svc.resolve_field(
            "lead_time_days", stock, rule_index, supplier=supplier, category=category,
        )
        lead_time_config = int(_lt_cfg if _lt_cfg is not None else DEFAULT_LEAD_TIME_DAYS)
        lead_time, lead_time_source, lead_time_learned = resolve_lead_time(
            lead_time_config, supplier, learned_lead_times, lead_time_config_source,
        )
        if lead_time_source == SOURCE_LEARNED:
            # Receptions beat the cascade outright, so the rule scope no longer
            # describes where the number came from.
            lead_time_rule_scope = None
        current_stock = float(stock["current_stock"]) if stock else None
        _moq_val, moq_source, moq_rule_scope = _sd_svc.resolve_field(
            "moq", stock, rule_index, supplier=supplier, category=category,
        )
        moq = float(_moq_val if _moq_val is not None else DEFAULT_MOQ)

        has_forecast = bool(model_forecasts)
        has_stock    = stock is not None and current_stock is not None

        _sl_val, service_level_source, service_level_rule_scope = _sd_svc.resolve_field(
            "service_level", stock, rule_index, supplier=supplier, category=category,
        )
        _cost_val, unit_cost_source, _ = _sd_svc.resolve_field(
            "unit_cost", stock, rule_index, supplier=supplier, category=category,
        )
        # The caller-supplied `service_level` is the tenant-wide preference; it
        # outranks the hardcoded system default but not a per-SKU value or a
        # rule, so it only applies when the cascade fell all the way through.
        # Preserves the pre-provenance behavior exactly.
        sku_service_level = (
            float(service_level) if service_level_source == SOURCE_DEFAULT
            else float(_sl_val)
        )

        if has_forecast and has_stock:
            z = _Z.get(sku_service_level, 1.645)
            # Per-period demand: average over as many forecast buckets as the
            # lead time spans in periods, and judge the signal against the lead
            # time expressed in the same period. For daily all three helpers are
            # the identity, so this path is byte-identical to before Phase C.
            lt_periods = _lead_time_in_periods(lead_time, period)
            steps = _steps_for_lead_time(lead_time, period)
            avg_daily, avg_std = _avg_daily_forecast(model_forecasts, steps)
            coverage_days = current_stock / avg_daily if avg_daily > 0 else 9999.0
            signal = _calc_signal(coverage_days, lt_periods)
            recommended = _calc_recommended(
                current_stock, avg_daily, avg_std, lt_periods, moq, sku_service_level
            )
            recommended = _gate_recommended_by_signal(signal, recommended)
            inventory_value = (
                round(current_stock * float(stock["unit_cost"]), 2)
                if stock.get("unit_cost") is not None else None
            )
            _demand_lt  = round(avg_daily * lt_periods, 2)
            _safety      = round(z * avg_std * math.sqrt(lt_periods), 2)
            _antes_moq   = round(max(0.0, _demand_lt + _safety - current_stock), 2)
            calc_explanation = {
                "daily_demand":    round(avg_daily, 2),
                "lead_time_days":    lead_time,
                # Where the lead time came from, so the breakdown labels it the
                # same way /hoy does — now across all five real sources, not the
                # old learned/configured pair that called an untouched row
                # "configured".
                "lead_time_source":  lead_time_source,
                "lead_time_rule_scope": lead_time_rule_scope,
                "lead_time_demand": _demand_lt,
                "safety_stock":      _safety,
                "current_stock":      current_stock,
                "antes_moq":         _antes_moq,
                "moq":               moq,
                "final_qty":    recommended,
            }
            if recommended <= 0:
                # Enough stock: keep the numbers (the what-if simulator needs
                # them) but flag it so the tooltip shows "no ordering needed".
                calc_explanation["suficiente"] = True

            # Reorder point: the stock level at which an order must be placed so
            # the shipment arrives before the shelf empties (lead-time demand
            # plus the safety cushion).
            reorder_point = round(_demand_lt + _safety, 2)
            explanation_obj = build_explanation(
                current_stock=current_stock,
                daily_demand=avg_daily,
                coverage_days=round(coverage_days, 1) if coverage_days < 9990 else None,
                lead_time=lead_time,
                lead_time_source=lead_time_source,
                reorder_point=reorder_point,
                signal=signal,
                lead_time_rule_scope=lead_time_rule_scope,
            )
        else:
            avg_daily = avg_std = None
            coverage_days = None
            signal = "SIN_DATOS"
            recommended = None
            inventory_value = None
            calc_explanation = None
            reorder_point = None
            explanation_obj = None

        # Recent stock history (last 14 days, at most 10 points for sparkline)
        history: list[dict] = []
        if has_stock:
            try:
                history = get_stock_history(tenant_id, sku, days=14)[-10:]
            except Exception as e:
                log.debug("stock history sparkline failed sku=%s: %s", sku, e)

        # "__all__" is the internal sentinel used when the dataset has no SKU/group
        # column (single-series session) — it must never surface unexplained as a SKU
        # name in the UI, so give it a friendly label traceable to its real cause.
        display_name = stock.get("display_name") if stock else None
        if sku == "__all__" and not display_name:
            display_name = "Serie única (sin columna SKU)"

        items.append({
            "sku":                sku,
            "display_name":       display_name,
            "current_stock":       current_stock,
            "min_stock":       float(stock["min_stock"]) if stock else 0.0,
            "lead_time_days":     lead_time,
            # Which lead time the recommendation actually used, and where it came
            # from: user | file | supplier_rule | learned | default. 'default'
            # is the honest answer the schema could not express before, and the
            # one the UI badges as an assumption of ours rather than the
            # tenant's data.
            "lead_time_source":     lead_time_source,
            # supplier | category | global — set only when a stock_defaults rule
            # won, so the copy can state the precedence instead of leaving the
            # buyer to guess why this SKU shows this number.
            "lead_time_rule_scope": lead_time_rule_scope,
            "lead_time_configured": lead_time_config,
            "lead_time_learned":  lead_time_learned,
            # State of the lead-time learning for THIS SKU's supplier: how many
            # of their deliveries we have recorded and how many we need before
            # the learned average replaces the configured value. Shipping the
            # threshold with the data keeps the UI from hardcoding a number that
            # could disagree with the one the planner actually applies.
            "lead_time_observations": observation_counts.get(
                (supplier or "").strip().lower(), 0),
            "lead_time_observations_needed": MIN_LEAD_TIME_OBSERVATIONS,
            "reorder_point":        reorder_point,
            # English fallback sentence; the frontend renders Spanish from
            # `explanation_code` + `explanation_params` (CLAUDE.md — no Spanish
            # string literals in backend logic).
            "explanation":          (explanation_obj or {}).get("text"),
            "explanation_code":     (explanation_obj or {}).get("code"),
            "explanation_params":   (explanation_obj or {}).get("params"),
            "unit_cost":     float(stock["unit_cost"]) if stock and stock.get("unit_cost") is not None else None,
            "unit_cost_source":   unit_cost_source,
            "moq":                moq,
            "moq_source":         moq_source,
            "moq_rule_scope":     moq_rule_scope,
            "service_level":      sku_service_level,
            "service_level_source": service_level_source,
            "service_level_rule_scope": service_level_rule_scope,
            "supplier":          supplier,
            "notes":              stock.get("notes") if stock else None,
            "sale_price":       float(stock["sale_price"]) if stock and stock.get("sale_price") is not None else None,
            # Per-unit gross margin — None when price or cost is missing, which
            # is what lets the cart report "N SKUs sin precio/costo" instead of
            # silently counting them as zero-margin.
            "unit_margin":    calc_unit_margin(
                float(stock["sale_price"]) if stock and stock.get("sale_price") is not None else None,
                float(stock["unit_cost"]) if stock and stock.get("unit_cost") is not None else None,
            ),
            # Set only when the supplier came from the SKU's configured primary;
            # a free-text name on the stock row has no id to resolve to.
            "supplier_id":       supplier_id,
            "category":          stock.get("category") if stock else None,
            # Grouping between category and SKU; event multipliers can target it.
            "family":             stock.get("family") if stock else None,
            "brand":              stock.get("brand") if stock else None,
            "unit_of_measure":      stock.get("unit_of_measure") if stock else None,
            "barcode":      stock.get("barcode") if stock else None,
            "has_forecast":       has_forecast,
            "has_stock":          has_stock,
            "daily_demand":     round(avg_daily, 4) if avg_daily is not None else None,
            "lead_time_demand":  round(avg_daily * lead_time, 2) if avg_daily is not None else None,
            "coverage_days":     round(coverage_days, 1) if coverage_days is not None and coverage_days < 9990 else None,
            "signal":             signal,
            "recommended_qty": recommended,
            "inventory_value":   inventory_value,
            "n_models":           len(model_forecasts),
            "xyz":               _classify_xyz(cv_by_sku.get(sku)),
            "stock_history":     history,
            "calc_explanation":  calc_explanation,
            "demand_trend_pct":  None,  # populated by morning_briefing; None by default in status
        })

    # ABC classification across all items (needs demand info so done after building list)
    abc_map = _classify_abc(items)
    for item in items:
        item["abc"] = abc_map.get(item["sku"], "?")
        item["abc_xyz"] = f"{item['abc']}{item['xyz']}" if item["xyz"] != "?" else item["abc"]

    items.sort(key=lambda x: (_SIGNAL_PRIORITY.get(x["signal"], 5), x["coverage_days"] or 9999))
    return items


# ── Per-warehouse status + network transfer pass (feature 5.4) ───────────────

# Minimum days of coverage a donor warehouse must keep AFTER donating for the
# network pass to suggest a transfer instead of a purchase (spec 5.4 §2).
TRANSFER_MIN_DONOR_COVERAGE_DAYS = 30.0


def get_inventory_status_by_warehouse(
    tenant_id: str, session_id: str, service_level: float = 0.95, period: str = "daily",
    *,
    forecasts: Optional[dict] = None,
    stock_rows: Optional[list] = None,
    learned_lead_times: Optional[dict] = None,
    lanes: Optional[dict] = None,
) -> list[dict]:
    """
    Per-(sku, warehouse) semaphore rows (feature 5.4).

    Demand per warehouse comes from, in order of preference:
      1. store-keyed session forecasts ("sku│store"), store matched to the
         warehouse name case-insensitively;
      2. the SKU-global forecast split by warehouses.demand_share fractions.

    Each row gets the same signal/recommendation math as the aggregated
    status, then _network_transfer_pass() converts purchases into transfer
    suggestions where another warehouse can donate.

    `forecasts` / `stock_rows` / `learned_lead_times` / `lanes`: optional
    preloaded data (raw get_forecasts blob, list_stock rows,
    get_learned_lead_times map, transfer_lane_service.lane_map).
    When provided, the corresponding fetch is skipped — the daily alert
    loop computes the aggregated AND per-warehouse status for the same
    tenant/session back-to-back, and without this the forecasts blob (can be
    MBs) plus both DB reads were fetched twice per tenant. Inputs are never
    mutated, so a caller can safely share them across both calls.
    """
    from backend.db import session_store
    from backend.inventory import warehouse_service as wh_svc
    from backend.inventory import stock_defaults_service as _sd_svc
    from backend.inventory.series import stores_in, for_store, split_key

    if forecasts is None:
        forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
    if stock_rows is None:
        stock_rows = list_stock(tenant_id)
    if learned_lead_times is None:
        learned_lead_times = get_learned_lead_times(tenant_id)
    rule_index = _sd_svc.build_rule_index(tenant_id)

    warehouses = ([w["name"] for w in wh_svc.list_warehouses(tenant_id)]
                  or [wh_svc.DEFAULT_WAREHOUSE])
    store_names = stores_in(forecasts)
    wh_by_lower = {w.lower().strip(): w for w in warehouses}

    shares: dict[str, float] = {}
    per_wh_forecasts: dict[str, dict] = {}
    if store_names:
        demand_mode = "store"
        for store in store_names:
            wh = wh_by_lower.get(store.lower().strip(), store)
            per_wh_forecasts[wh] = for_store(forecasts, store)
        # Only the SKU names are needed here — per-warehouse rows read their
        # forecasts from per_wh_forecasts, so a full rollup_by_sku (which
        # deep-copies every forecast point) would be pure waste on this path.
        sku_forecasts = {split_key(k)[0]: True for k in forecasts}
    else:
        demand_mode = "share"
        shares = wh_svc.get_demand_shares(tenant_id)
        sku_forecasts = forecasts

    stock_by_pair = {(r["sku"], r.get("warehouse") or wh_svc.DEFAULT_WAREHOUSE): r
                     for r in stock_rows}
    all_skus = sorted(sku_forecasts.keys())

    items: list[dict] = []
    for sku in all_skus:
        for wh in warehouses:
            stock = stock_by_pair.get((sku, wh))
            if demand_mode == "store":
                model_forecasts = per_wh_forecasts.get(wh, {}).get(sku, {})
                share = 1.0
            else:
                model_forecasts = sku_forecasts.get(sku, {})
                share = shares.get(wh, 0.0)
            # Pairs with neither stock nor demand don't exist for this tenant.
            if stock is None and (not model_forecasts or share == 0.0):
                continue

            supplier = stock.get("supplier") if stock else None
            category = stock.get("category") if stock else None
            # Same SKU > supplier > category > global > system cascade as the
            # aggregated view; the per-warehouse rows must not disagree with it.
            _lt_cfg, lt_cfg_source, lead_time_rule_scope = _sd_svc.resolve_field(
                "lead_time_days", stock, rule_index, supplier=supplier, category=category)
            lead_time_config = int(_lt_cfg if _lt_cfg is not None else DEFAULT_LEAD_TIME_DAYS)
            lead_time, lead_time_source, _ = resolve_lead_time(
                lead_time_config, supplier, learned_lead_times, lt_cfg_source)
            if lead_time_source == SOURCE_LEARNED:
                lead_time_rule_scope = None
            current_stock = float(stock["current_stock"]) if stock else 0.0
            _moq_val, _, _ = _sd_svc.resolve_field(
                "moq", stock, rule_index, supplier=supplier, category=category)
            moq = float(_moq_val if _moq_val is not None else DEFAULT_MOQ)

            if model_forecasts and share > 0.0:
                _sl_val, sl_source, _ = _sd_svc.resolve_field(
                    "service_level", stock, rule_index, supplier=supplier, category=category)
                sku_service_level = (
                    float(service_level) if sl_source == SOURCE_DEFAULT else float(_sl_val))
                z = _Z.get(sku_service_level, 1.645)
                # Per-period demand + period lead time (identity for daily).
                lt_periods = _lead_time_in_periods(lead_time, period)
                steps = _steps_for_lead_time(lead_time, period)
                avg_daily, avg_std = _avg_daily_forecast(model_forecasts, steps)
                avg_daily *= share
                avg_std *= share
                coverage_days = current_stock / avg_daily if avg_daily > 0 else 9999.0
                signal = _calc_signal(coverage_days, lt_periods)
                recommended = _calc_recommended(
                    current_stock, avg_daily, avg_std, lt_periods, moq,
                    sku_service_level)
                recommended = _gate_recommended_by_signal(signal, recommended)
                reorder_point = round(
                    avg_daily * lt_periods
                    + z * avg_std * math.sqrt(lt_periods), 2)
            else:
                avg_daily = avg_std = None
                coverage_days = None
                signal = "SIN_DATOS"
                recommended = None
                reorder_point = None

            items.append({
                "sku": sku,
                "warehouse": wh,
                "display_name": stock.get("display_name") if stock else None,
                "supplier": supplier,
                "current_stock": current_stock if stock else None,
                "lead_time_days": lead_time,
                "lead_time_source": lead_time_source,
                "lead_time_rule_scope": lead_time_rule_scope,
                "moq": moq,
                "daily_demand": round(avg_daily, 4) if avg_daily is not None else None,
                "coverage_days": (round(coverage_days, 1)
                                  if coverage_days is not None and coverage_days < 9990
                                  else None),
                "reorder_point": reorder_point,
                "signal": signal,
                "recommended_qty": recommended,
                "recommended_action": None,
                "transfer_suggestion": None,
                # Why a possible transfer LOST against buying (structured
                # {reason_code, params}; the frontend renders the sentence).
                "transfer_rejected_reason": None,
                "unit_cost": (float(stock["unit_cost"])
                              if stock and stock.get("unit_cost") is not None else None),
            })

    if lanes is None:
        from backend.inventory import transfer_lane_service as lane_svc
        lanes = lane_svc.lane_map(tenant_id)
    _network_transfer_pass(items, period, lanes=lanes)
    items.sort(key=lambda x: (_SIGNAL_PRIORITY.get(x["signal"], 5),
                              x["coverage_days"] or 9999))
    return items


def _evaluate_transfer_lane(
    lane: dict, qty: float, needy: dict, donor: dict,
) -> tuple[bool, str, dict]:
    """
    Time- and money-aware verdict for ONE candidate transfer (PENDIENTES #2).
    Returns (accepted, reason_code, params) — never a rendered sentence: the
    frontend turns the structured reason into Spanish via i18n.

    A transfer only wins when it beats BUYING on both axes:
      1. Time — the lane must arrive strictly sooner than the supplier would
         (`lane_days < purchase_days`). A move that takes as long as (or longer
         than) the purchase solves nothing: reason `transfer_too_slow`.
      2. Money — total lane cost (qty * cost_per_unit + fixed_cost) must be
         strictly below what buying the same qty costs. Only checked when a
         positive unit cost is known (the needy row's, else the donor's);
         with no cost on file the money test is skipped rather than guessed.
         Reason when it loses: `transfer_more_expensive`.

    `saving` is None when no unit cost is known — the transfer is then accepted
    on the time argument alone and the UI must not claim a figure it doesn't
    have.
    """
    lane_days = int(lane["lead_time_days"])
    purchase_days = int(needy.get("lead_time_days") or 0)
    params: dict = {
        "from_warehouse": donor["warehouse"],
        "qty": round(qty, 2),
        "lane_days": lane_days,
        "purchase_days": purchase_days,
    }
    if lane_days >= purchase_days:
        return False, "transfer_too_slow", params

    transfer_cost = qty * float(lane["cost_per_unit"]) + float(lane["fixed_cost"])
    unit_cost = needy.get("unit_cost")
    if unit_cost is None:
        unit_cost = donor.get("unit_cost")
    saving = None
    if unit_cost is not None and float(unit_cost) > 0:
        purchase_cost = qty * float(unit_cost)
        if transfer_cost >= purchase_cost:
            return False, "transfer_more_expensive", {
                **params,
                "transfer_cost": round(transfer_cost, 2),
                "purchase_cost": round(purchase_cost, 2),
            }
        saving = round(purchase_cost - transfer_cost, 2)
    return True, "transfer_faster_and_cheaper", {**params, "saving": saving}


def _network_transfer_pass(
    items: list[dict], period: str = "daily",
    lanes: Optional[dict] = None,
) -> None:
    """
    Convert purchase recommendations into transfer suggestions where another
    warehouse of the same SKU can donate (spec 5.4 §2). Mutates items in place.

    A donor qualifies iff after donating qty = min(need, surplus):
      - its stock stays >= its own reorder point,
      - its remaining coverage stays >= the post-donation floor, and
      - it can cover >= 80% of the need (below that the purchase stands).
    Donors are then tried best-coverage-first against the LANE rules
    (_evaluate_transfer_lane): the first one whose lane arrives sooner than the
    supplier and costs less than buying wins. When every candidate loses, the
    row stays an "order" and carries `transfer_rejected_reason`
    ({reason_code, params}) explaining why — the losing verdict of the
    best-coverage candidate.

    `lanes`: preloaded transfer_lane_service.lane_map ({(from,to): lane}).
    Pairs absent from it resolve to the documented default lane (1 day, free),
    which is what keeps tenants that never configured a lane on exactly the
    pre-feature behavior.

    `period` (multi-period Phase C): items carry per-period demand
    (`daily_demand`) and thus period-unit coverage, exactly like the rest of the
    status path. The TRANSFER_MIN_DONOR_COVERAGE_DAYS floor is a *day* threshold,
    so it is converted into the active period here (30 days -> ~4.3 weeks) to
    keep the "don't strand the donor" guard physically equivalent across
    periods. For daily _days_per_period is 1, so `min_cov` == 30.0 and this path
    is byte-identical to before. The exposed `coverage_unit` lets the UI label
    the value in the active period's unit instead of hardcoding "days".
    Lane lead times stay in DAYS on purpose: they are compared against the
    SKU's purchase lead time, which is also a day count on these rows.
    """
    from backend.inventory.transfer_lane_service import lane_for

    lanes = lanes or {}
    min_cov = TRANSFER_MIN_DONOR_COVERAGE_DAYS / _days_per_period(period)
    unit = _coverage_unit(period)

    by_sku: dict[str, list[dict]] = {}
    for it in items:
        by_sku.setdefault(it["sku"], []).append(it)

    for sku, rows in by_sku.items():
        needy = [r for r in rows
                 if r["signal"] in ("PEDIR_YA", "PEDIR_PRONTO")
                 and (r.get("recommended_qty") or 0) > 0]
        for r in needy:
            r["recommended_action"] = "order"
            need = float(r["recommended_qty"])
            candidates: list[dict] = []
            for d in rows:
                if d is r or not d.get("current_stock"):
                    continue
                daily = d.get("daily_demand") or 0.0
                reorder = d.get("reorder_point") or 0.0
                donatable = min(need, float(d["current_stock"]) - reorder)
                if daily > 0:
                    donatable = min(
                        donatable,
                        float(d["current_stock"]) - daily * min_cov)
                if donatable <= 0:
                    continue
                after = float(d["current_stock"]) - donatable
                cov_after = after / daily if daily > 0 else 9999.0
                if cov_after < min_cov:
                    continue
                # A donation that doesn't materially cover the need never
                # replaced the order (pre-feature rule, unchanged).
                if donatable < 0.8 * need:
                    continue
                candidates.append({"donor": d, "qty": donatable, "cov_after": cov_after})

            # Best post-donation coverage first: the donor least hurt by the
            # move gets the first shot at the lane rules.
            candidates.sort(key=lambda c: -c["cov_after"])
            rejection: Optional[dict] = None
            for c in candidates:
                lane = lane_for(lanes, c["donor"]["warehouse"], r["warehouse"])
                accepted, reason_code, params = _evaluate_transfer_lane(
                    lane, c["qty"], r, c["donor"])
                if not accepted:
                    if rejection is None:
                        rejection = {"reason_code": reason_code, "params": params}
                    continue
                r["recommended_action"] = "transfer"
                r["transfer_suggestion"] = {
                    "from_warehouse": c["donor"]["warehouse"],
                    "qty": round(c["qty"], 2),
                    # None = donor has no measurable demand ("ample coverage"),
                    # matching how coverage_days is nulled at this boundary —
                    # the 9999 sentinel must never cross the API.
                    "donor_coverage_days_after": (
                        round(c["cov_after"], 1)
                        if c["cov_after"] < 9990 else None
                    ),
                    # The unit the value above is expressed in (day/week/month),
                    # mirroring the status envelope's `coverage_unit` so the UI
                    # renders "N semanas" under a weekly horizon, not "N días".
                    "coverage_unit": unit,
                    # Structured explanation ({reason_code, params}); the
                    # frontend renders the Spanish sentence.
                    "reason_code": reason_code,
                    "params": params,
                    "lane_days": lane["lead_time_days"],
                }
                rejection = None
                break
            if rejection is not None:
                r["transfer_rejected_reason"] = rejection


# ── Per-product event multipliers ────────────────────────────────────────────
# A single multiplier per event is a false simplification: on Black
# Friday electronics spike and milk does not move. These overrides
# allow tuning per SKU, per product family or per category; resolution order
# is sku > family > category > the event's multiplier — narrowest wins.

_MULTIPLIER_SCOPES = ("sku", "family", "category")


def get_event_multipliers(tenant_id: str, event_id: str) -> list[dict]:
    return query(
        """SELECT * FROM inventory_event_multipliers
           WHERE tenant_id = %s AND event_id = %s
           ORDER BY scope, scope_value""",
        (tenant_id, event_id),
    )


def set_event_multiplier(
    tenant_id: str, event_id: str, scope: str, scope_value: str, multiplier: float,
) -> dict:
    """Upsert one override. `scope` is one of 'sku', 'family' or 'category'."""
    if scope not in _MULTIPLIER_SCOPES:
        raise ValueError(f"scope must be one of {sorted(_MULTIPLIER_SCOPES)}")
    if multiplier <= 0:
        raise ValueError("multiplier must be greater than 0")
    value = (scope_value or "").strip()
    # Categories and families are deliberately stored lower-cased. The unique
    # index is case-sensitive but `_index_overrides` compares in lower case:
    # without this, "Lacteos" and "lacteos" create TWO rows that collide on read
    # and one is silently lost (which one depends on the Postgres collation).
    # Normalising on write is what makes the ON CONFLICT genuinely idempotent.
    if scope in ("category", "family"):
        value = value.lower()
    if not value:
        raise ValueError("scope_value cannot be empty")

    mid = f"em_{__import__('uuid').uuid4().hex[:12]}"
    execute(
        """INSERT INTO inventory_event_multipliers
             (id, tenant_id, event_id, scope, scope_value, multiplier)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, event_id, scope, scope_value)
             DO UPDATE SET multiplier = EXCLUDED.multiplier""",
        (mid, tenant_id, event_id, scope, value, multiplier),
    )
    return query_one(
        """SELECT * FROM inventory_event_multipliers
           WHERE tenant_id = %s AND event_id = %s AND scope = %s AND scope_value = %s""",
        (tenant_id, event_id, scope, value),
    )


def delete_event_multiplier(tenant_id: str, override_id: str) -> bool:
    existing = query_one(
        "SELECT id FROM inventory_event_multipliers WHERE id = %s AND tenant_id = %s",
        (override_id, tenant_id),
    )
    if not existing:
        return False
    execute(
        "DELETE FROM inventory_event_multipliers WHERE id = %s AND tenant_id = %s",
        (override_id, tenant_id),
    )
    return True


def _index_overrides(rows: list[dict]) -> dict:
    """Overrides indexed for O(1) resolution. Family/category are
    case-insensitive; unknown scopes are ignored rather than crashing a whole
    event because one legacy row carries a retired scope."""
    idx: dict = {s: {} for s in _MULTIPLIER_SCOPES}
    for r in rows:
        scope = r["scope"]
        if scope not in idx:
            continue
        key = r["scope_value"]
        if scope in ("category", "family"):
            key = key.strip().lower()
        idx[scope][key] = float(r["multiplier"])
    return idx


def _resolve_multiplier(item: dict, base: float, idx: dict) -> tuple[float, str]:
    """Returns (multiplier, source): the narrowest override that matches wins,
    sku > family > category, falling back to the event's own multiplier."""
    sku = item.get("sku")
    if sku is not None and sku in idx.get("sku", {}):
        return idx["sku"][sku], "sku"
    fam = (item.get("family") or "").strip().lower()
    if fam and fam in idx.get("family", {}):
        return idx["family"][fam], "family"
    cat = (item.get("category") or "").strip().lower()
    if cat and cat in idx.get("category", {}):
        return idx["category"][cat], "category"
    return base, "event"


def build_multiplier_explanation(event: Optional[dict], base: float,
                                 overrides: list[dict]) -> dict:
    """
    The "why" behind the multiplier, so the UI never shows a x2.2 with no
    justification. Returned structured (not as a backend-assembled sentence)
    so the frontend can translate and lay it out however it wants.
    """
    from_catalog = bool(event and event.get("catalog_key"))
    return {
        "base_multiplier": base,
        # 'catalog' = estimate preloaded by Faro; 'user' = set by the
        # administrator (or edited on top of the estimate).
        "source": "catalog" if from_catalog else "user",
        "reason": (event or {}).get("notes"),
        "editable": True,
        "es_estimacion": from_catalog,
        "active_overrides": len(overrides),
        "overrides_by_sku": sum(1 for o in overrides if o["scope"] == "sku"),
        "overrides_by_family": sum(1 for o in overrides if o["scope"] == "family"),
        "overrides_by_category": sum(1 for o in overrides if o["scope"] == "category"),
    }


# ── Promotion / event impact simulator (feature 2.3) ─────────────────────────

def simulate_event_impact(
    tenant_id: str,
    session_id: str,
    start_date,
    end_date,
    multiplier: float,
    event_name: Optional[str] = None,
    event_id: Optional[str] = None,
) -> dict:
    """
    Project what a demand event (promo, season) does to each SKU:
    extra units to sell, whether current stock survives it, how much to order
    and the latest date to place that order (event start − lead time).

    When the event has per-SKU or per-category overrides, each product uses its
    own multiplier and the row reports which one applied and where it came
    from, so that the recomendación siempre se pueda explicar.

    Pure read — nothing is persisted. Uses the same per-SKU daily demand the
    semáforo uses, so the simulation is consistent with the rest of the app.
    """
    from datetime import date as _date, timedelta

    if isinstance(start_date, str):
        start_date = _date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = _date.fromisoformat(end_date)
    if end_date < start_date:
        raise ValueError("end_date no puede ser anterior a start_date")
    if multiplier <= 0:
        raise ValueError("multiplier debe ser mayor que 0")

    today = _date.today()
    event_days = (end_date - start_date).days + 1
    days_until_start = max(0, (start_date - today).days)

    # Per-SKU / per-category overrides, when the simulation runs off a saved event.
    override_rows = get_event_multipliers(tenant_id, event_id) if event_id else []
    idx = _index_overrides(override_rows)

    items = get_inventory_status(tenant_id, session_id)
    rows: list[dict] = []

    for it in items:
        daily = it.get("daily_demand")
        if not daily or daily <= 0:
            continue  # sin forecast no hay nada que simular

        lead_time = int(it.get("lead_time_days") or DEFAULT_LEAD_TIME_DAYS)
        moq       = float(it.get("moq") or 1)
        stock     = it.get("current_stock")
        cost      = it.get("unit_cost")

        # Cada product puede tener su propio multiplier.
        sku_mult, mult_source = _resolve_multiplier(it, multiplier, idx)

        baseline_units = daily * event_days
        event_units    = baseline_units * sku_mult
        extra_units    = event_units - baseline_units

        # Stock projected to the event start: today's stock minus normal
        # consumption until then (floored at 0).
        stock_at_start = None
        deficit = None
        order = None
        if stock is not None:
            stock_at_start = max(0.0, float(stock) - daily * days_until_start)
            deficit = max(0.0, event_units - stock_at_start)
            if deficit > 0:
                order = math.ceil(deficit / moq) * moq

        order_by = start_date - timedelta(days=lead_time)
        late = order_by < today  # ordering today would still arrive mid/after event

        rows.append({
            "sku":               it["sku"],
            "display_name":      it.get("display_name"),
            "supplier":         it.get("supplier"),
            "category":         it.get("category"),
            # Which multiplier applied to THIS product and why: without this
            # la fila no se puede explicar cuando hay overrides.
            "multiplier":     round(sku_mult, 2),
            "multiplier_source": mult_source,
            "daily_demand":    round(daily, 2),
            "baseline_units":    round(baseline_units, 1),
            "event_units":       round(event_units, 1),
            "extra_units":       round(extra_units, 1),
            "current_stock":      stock,
            "stock_al_inicio":   round(stock_at_start, 1) if stock_at_start is not None else None,
            "deficit":           round(deficit, 1) if deficit is not None else None,
            "qty_to_order":    order,
            "order_value":      round(order * float(cost), 2) if (order and cost is not None) else None,
            "lead_time_days":    lead_time,
            "order_by":          order_by.isoformat(),
            "llega_tarde":       late,
            "en_risk":         bool(deficit and deficit > 0),
        })

    # Riskiest first: SKUs that need an order, largest deficit on top
    rows.sort(key=lambda r: (not r["en_risk"], -(r["deficit"] or 0)))

    at_risk = [r for r in rows if r["en_risk"]]
    total_to_order = sum(r["qty_to_order"] or 0 for r in at_risk)
    total_value = sum(r["order_value"] or 0 for r in at_risk)
    earliest_order_by = min((r["order_by"] for r in at_risk), default=None)

    event_row = get_event(tenant_id, event_id) if event_id else None
    # How many SKUs ran with each multiplier: shows at a glance whether
    # the catalog's x2.2 hit everything or only what it should.
    desglose: dict[str, dict] = {}
    for r in rows:
        k = f"{r['multiplier']}|{r['multiplier_source']}"
        d = desglose.setdefault(k, {
            "multiplier": r["multiplier"],
            "source":        r["multiplier_source"],
            "skus":          0,
        })
        d["skus"] += 1

    return {
        "event_name":  event_name,
        "event_id":    event_id,
        "start_date":  start_date.isoformat(),
        "end_date":    end_date.isoformat(),
        "event_days":  event_days,
        "multiplier":  multiplier,
        "explanation": build_multiplier_explanation(event_row, multiplier, override_rows),
        "multipliers_applied": sorted(
            desglose.values(), key=lambda d: -d["skus"]
        ),
        "items":       rows,
        "summary": {
            "skus_simulados":     len(rows),
            "skus_at_risk":     len(at_risk),
            "extra_units":     round(sum(r["extra_units"] for r in rows), 1),
            "total_to_order":        round(total_to_order, 1),
            "total_order_value": round(total_value, 2),
            "order_before":     earliest_order_by,
            "any_order_late": any(r["llega_tarde"] for r in at_risk),
        },
    }


# ── Events (temporadas / promociones) ────────────────────────────────────────

def list_events(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM inventory_events WHERE tenant_id = %s ORDER BY start_date",
        (tenant_id,),
    )


def get_event(tenant_id: str, event_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM inventory_events WHERE tenant_id = %s AND id = %s",
        (tenant_id, event_id),
    )


def get_upcoming_events(tenant_id: str, days_ahead: int = 60) -> list[dict]:
    """
    Events starting within the next N days (for dashboard banner).
    Only *active* events — a switched-off calendar event must not raise alerts.
    """
    return query(
        """SELECT * FROM inventory_events
           WHERE tenant_id = %s AND start_date <= CURRENT_DATE + %s
             AND end_date >= CURRENT_DATE
             AND active IS TRUE
           ORDER BY start_date""",
        (tenant_id, days_ahead),
    )


def create_event(tenant_id: str, data: dict) -> dict:
    eid = f"ev_{__import__('uuid').uuid4().hex[:12]}"
    execute(
        """INSERT INTO inventory_events (id, tenant_id, name, start_date, end_date, multiplier, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (eid, tenant_id, data["name"], data["start_date"], data["end_date"],
         data.get("multiplier", 1.0), data.get("notes")),
    )
    return query_one("SELECT * FROM inventory_events WHERE id = %s", (eid,))


def update_event(tenant_id: str, event_id: str, data: dict) -> Optional[dict]:
    allowed = {"name", "start_date", "end_date", "multiplier", "notes", "active"}
    safe = {k: v for k, v in data.items() if k in allowed}
    if not safe:
        return query_one("SELECT * FROM inventory_events WHERE id = %s AND tenant_id = %s", (event_id, tenant_id))
    cols = ", ".join(f"{k} = %s" for k in safe)
    execute(
        f"UPDATE inventory_events SET {cols} WHERE id = %s AND tenant_id = %s",
        (*safe.values(), event_id, tenant_id),
    )
    return query_one("SELECT * FROM inventory_events WHERE id = %s AND tenant_id = %s", (event_id, tenant_id))


def delete_event(tenant_id: str, event_id: str) -> None:
    execute("DELETE FROM inventory_events WHERE id = %s AND tenant_id = %s", (event_id, tenant_id))


# ── LatAm commercial calendar seeding (feature 3.4) ──────────────────────────

def seed_calendar_events(
    tenant_id: str,
    country: str = "CR",
    years: Optional[list[int]] = None,
) -> dict:
    """
    Materialise the LatAm commercial-events catalog into `inventory_events`
    for this tenant. Idempotent: re-running inserts only the occurrences that
    are missing (unique index on tenant_id + catalog_key), and never touches
    the `active` flag of rows the user already switched off.

    Returns a summary so the caller can tell "seeded 40" from "already there".
    """
    from backend.inventory import calendar_catalog as cat

    country = (country or "CR").upper()
    if country not in cat.SUPPORTED_COUNTRIES:
        raise ValueError(
            f"País '{country}' sin catálogo. Disponibles: {', '.join(cat.SUPPORTED_COUNTRIES)}"
        )

    occurrences = cat.build_occurrences(country, years)
    existing = {
        r["catalog_key"] for r in query(
            "SELECT catalog_key FROM inventory_events "
            "WHERE tenant_id = %s AND catalog_key IS NOT NULL",
            (tenant_id,),
        )
    }

    inserted = 0
    for occ in occurrences:
        if occ.catalog_key in existing:
            continue
        eid = f"ev_{__import__('uuid').uuid4().hex[:12]}"
        execute(
            """INSERT INTO inventory_events
                 (id, tenant_id, name, start_date, end_date, multiplier, notes,
                  catalog_key, country, source, active)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'catalog', TRUE)
               ON CONFLICT (tenant_id, catalog_key)
                 WHERE catalog_key IS NOT NULL
                 DO NOTHING""",
            (eid, tenant_id, occ.name, occ.start_date, occ.end_date,
             occ.multiplier, occ.notes, occ.catalog_key, country),
        )
        inserted += 1

    return {
        "country":       country,
        "inserted":      inserted,
        "already_present": len(occurrences) - inserted,
        "total_catalog": len(occurrences),
    }


def get_catalog_state(tenant_id: str, country: str = "CR") -> dict[str, dict]:
    """
    Per catalog entry: how many occurrences are seeded, how many are active and
    when the next one starts. Keyed by the catalog entry key (the part of
    `catalog_key` before the first colon) so the UI can render one row per
    event rather than one per occurrence.
    """
    rows = query(
        """SELECT split_part(catalog_key, ':', 1) AS entry_key,
                  COUNT(*)                                         AS total,
                  COUNT(*) FILTER (WHERE active IS TRUE)           AS active,
                  MIN(start_date) FILTER (WHERE end_date >= CURRENT_DATE
                                            AND active IS TRUE)    AS next_start
             FROM inventory_events
            WHERE tenant_id = %s AND catalog_key IS NOT NULL
              AND (country = %s OR country IS NULL)
            GROUP BY 1""",
        (tenant_id, (country or "CR").upper()),
    )
    return {
        r["entry_key"]: {
            "total":      int(r["total"]),
            "active":     int(r["active"]),
            "next_start": r["next_start"].isoformat() if r["next_start"] else None,
        }
        for r in rows
    }


def set_event_active(tenant_id: str, event_id: str, active: bool) -> Optional[dict]:
    """Switch a calendar event on/off without deleting it."""
    execute(
        "UPDATE inventory_events SET active = %s WHERE id = %s AND tenant_id = %s",
        (bool(active), event_id, tenant_id),
    )
    return get_event(tenant_id, event_id)


def set_catalog_group_active(tenant_id: str, catalog_prefix: str, active: bool) -> int:
    """
    Switch every occurrence of one catalog entry (e.g. all 24 `co_quincena_15`
    rows across both seeded years) at once — toggling 24 rows one by one would
    be a miserable UI. Returns how many rows matched.
    """
    rows = query(
        "SELECT id FROM inventory_events "
        "WHERE tenant_id = %s AND catalog_key LIKE %s",
        (tenant_id, f"{catalog_prefix}:%"),
    )
    if not rows:
        return 0
    execute(
        "UPDATE inventory_events SET active = %s "
        "WHERE tenant_id = %s AND catalog_key LIKE %s",
        (bool(active), tenant_id, f"{catalog_prefix}:%"),
    )
    return len(rows)


# ── PDF report ────────────────────────────────────────────────────────────────

def generate_inventory_pdf(tenant_id: str, session_id: str, service_level: float = 0.95) -> bytes:
    """
    Generates a one-page executive summary PDF in Spanish.
    Returns raw bytes ready for StreamingResponse.
    """
    from io import BytesIO
    from datetime import date
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    items = get_inventory_status(tenant_id, session_id, service_level)

    # ── Color palette ──────────────────────────────────────────────────────
    RED    = colors.HexColor("#ef4444")
    AMBER  = colors.HexColor("#f59e0b")
    GREEN  = colors.HexColor("#22c55e")
    BLUE   = colors.HexColor("#3b82f6")
    INDIGO = colors.HexColor("#6366f1")
    DARK   = colors.HexColor("#0f172a")
    BORDER = colors.HexColor("#e2e8f0")

    SIGNAL_COLORS = {
        "PEDIR_YA": RED, "PEDIR_PRONTO": AMBER,
        "OK": GREEN, "SOBRESTOCK": BLUE, "SIN_DATOS": colors.grey,
    }
    SIGNAL_LABELS = {
        "PEDIR_YA": "🔴 PEDIR YA", "PEDIR_PRONTO": "🟡 Pedir pronto",
        "OK": "🟢 OK", "SOBRESTOCK": "🔵 Sobrestock", "SIN_DATOS": "Sin datos",
    }

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=1.8*cm, bottomMargin=1.5*cm,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
    )

    H1 = ParagraphStyle("H1", fontSize=18, fontName="Helvetica-Bold",
                        textColor=DARK, spaceAfter=2)
    H2 = ParagraphStyle("H2", fontSize=10, fontName="Helvetica-Bold",
                        textColor=INDIGO, spaceAfter=6, spaceBefore=12,
                        textTransform="uppercase")
    SMALL = ParagraphStyle("SMALL", fontSize=8, fontName="Helvetica",
                           textColor=colors.HexColor("#64748b"))
    CELL  = ParagraphStyle("CELL", fontSize=8, fontName="Helvetica", textColor=DARK)
    CELL_BOLD = ParagraphStyle("CELL_BOLD", fontSize=8, fontName="Helvetica-Bold", textColor=DARK)

    story = []

    # ── Header ─────────────────────────────────────────────────────────────
    today_str = date.today().strftime("%d de %B de %Y")
    header_data = [[
        Paragraph("<b>RESUMEN DE INVENTARIO</b>", H1),
        Paragraph(f"<font color='#64748b'>Generado el {today_str}</font>", SMALL),
    ]]
    header_table = Table(header_data, colWidths=["70%", "30%"])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("ALIGN",  (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2, color=INDIGO, spaceAfter=12))

    # ── KPI row ─────────────────────────────────────────────────────────────
    total   = len(items)
    urgent  = sum(1 for i in items if i["signal"] == "PEDIR_YA")
    warning = sum(1 for i in items if i["signal"] == "PEDIR_PRONTO")
    ok      = sum(1 for i in items if i["signal"] == "OK")
    over    = sum(1 for i in items if i["signal"] == "SOBRESTOCK")
    value   = sum(i["inventory_value"] for i in items if i.get("inventory_value") or 0)

    kpi_table_data = [[
        [Paragraph(f"<b><font color='#{c}'>{n}</font></b>",
                   ParagraphStyle("kn", fontSize=20, fontName="Helvetica-Bold", alignment=TA_CENTER)),
         Paragraph(l, ParagraphStyle("kl", fontSize=7.5, alignment=TA_CENTER,
                                     textColor=colors.HexColor("#64748b")))]
        for n, l, c in [
            (total,   "Total SKUs",      "6366f1"),
            (urgent,  "Pedir YA",        "ef4444"),
            (warning, "Pedir pronto",    "f59e0b"),
            (ok,      "OK",              "22c55e"),
            (over,    "Sobrestock",      "3b82f6"),
            (money(value) if value else "—", "Valor bodega", "6366f1"),
        ]
    ]]
    kpi_row = [[Table([[v] for v in cell], colWidths=["100%"]) for cell in kpi_table_data[0]]]
    kpi_t = Table(kpi_row, colWidths=[doc.width / 6] * 6)
    kpi_t.setStyle(TableStyle([
        ("BOX",    (0, 0), (-1, -1), 0.5, BORDER),
        ("GRID",   (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kpi_t)
    story.append(Spacer(1, 10))

    # ── Urgent SKUs table ──────────────────────────────────────────────────
    critical_items = [i for i in items if i["signal"] in ("PEDIR_YA", "PEDIR_PRONTO")][:20]
    if critical_items:
        story.append(Paragraph("Productos que requieren acción", H2))
        tdata = [[
            Paragraph("<b>SKU</b>", CELL_BOLD),
            Paragraph("<b>Nombre</b>", CELL_BOLD),
            Paragraph("<b>Señal</b>", CELL_BOLD),
            Paragraph("<b>Stock actual</b>", CELL_BOLD),
            Paragraph("<b>Días cobertura</b>", CELL_BOLD),
            Paragraph("<b>Pedir</b>", CELL_BOLD),
            Paragraph("<b>Proveedor</b>", CELL_BOLD),
        ]]
        row_styles = []
        for idx, item in enumerate(critical_items):
            sig_color = SIGNAL_COLORS.get(item["signal"], colors.grey)
            sig_label = SIGNAL_LABELS.get(item["signal"], item["signal"])
            tdata.append([
                Paragraph(item["sku"], CELL),
                Paragraph(item.get("display_name") or "—", CELL),
                Paragraph(sig_label, ParagraphStyle("sig", fontSize=8,
                          fontName="Helvetica-Bold", textColor=sig_color)),
                Paragraph(f"{item['current_stock']:,.0f}" if item.get("current_stock") is not None else "—", CELL),
                Paragraph(f"{item['coverage_days']:.0f} días" if item.get("coverage_days") is not None else "—", CELL),
                Paragraph(f"<b>{item['recommended_qty']:,.0f}</b>" if item.get("recommended_qty") else "—",
                          ParagraphStyle("qty", fontSize=8, fontName="Helvetica-Bold", textColor=GREEN)),
                Paragraph(item.get("supplier") or "—", CELL),
            ])
            if idx % 2 == 0:
                row_styles.append(("BACKGROUND", (0, idx+1), (-1, idx+1), colors.HexColor("#f8fafc")))

        cws = [2.2*cm, 3.5*cm, 2.5*cm, 2.2*cm, 2.4*cm, 2.0*cm, 3.2*cm]
        t = Table(tdata, colWidths=cws)
        ts = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
        for s in row_styles:
            ts.add(*s)
        t.setStyle(ts)
        story.append(t)
        story.append(Spacer(1, 8))

    # ── All SKUs compact table ─────────────────────────────────────────────
    remaining = [i for i in items if i["signal"] not in ("PEDIR_YA", "PEDIR_PRONTO")]
    if remaining:
        story.append(Paragraph("Resto del inventario", H2))
        small_data = [[
            Paragraph("<b>SKU</b>", CELL_BOLD),
            Paragraph("<b>Nombre</b>", CELL_BOLD),
            Paragraph("<b>Señal</b>", CELL_BOLD),
            Paragraph("<b>Días cobertura</b>", CELL_BOLD),
            Paragraph("<b>ABC-XYZ</b>", CELL_BOLD),
        ]]
        for item in remaining[:30]:
            small_data.append([
                Paragraph(item["sku"], CELL),
                Paragraph(item.get("display_name") or "—", CELL),
                Paragraph(SIGNAL_LABELS.get(item["signal"], "—"),
                          ParagraphStyle("s2", fontSize=8, textColor=SIGNAL_COLORS.get(item["signal"], colors.grey))),
                Paragraph(f"{item['coverage_days']:.0f}d" if item.get("coverage_days") is not None else "—", CELL),
                Paragraph(item.get("abc_xyz") or "—", CELL),
            ])
        st = Table(small_data, colWidths=[3*cm, 5*cm, 3.2*cm, 3*cm, 2*cm])
        st.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(st)

    # ── Footer ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Paragraph(
        f"Generado automáticamente · Sesión {session_id[:8]} · Nivel de servicio {service_level*100:.0f}%",
        ParagraphStyle("footer", fontSize=7, textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER),
    ))

    doc.build(story)
    return buf.getvalue()


# ── Decision Centre helpers ───────────────────────────────────────────────────

def _calc_demand_trend(tenant_id: str, sku: str, avg_daily: float, days: int = 14) -> Optional[float]:
    """
    Returns % change in actual demand vs forecast.
    Positive = demand is running above forecast (risk of stockout).
    Negative = demand is below forecast (risk of overstock).
    Uses stock snapshot history to estimate actual consumption.
    Returns None if insufficient data or change is not significant (< 15%).
    """
    if avg_daily <= 0:
        return None

    history = get_stock_history(tenant_id, sku, days=days)
    if len(history) < 4:
        return None

    # Actual depletion (stock went down)
    first_stock = history[0]['stock']
    last_stock  = history[-1]['stock']
    actual_depletion = first_stock - last_stock

    # Expected depletion based on forecast
    period_days      = min(len(history), days)
    expected_depletion = avg_daily * period_days

    if expected_depletion <= 0 or actual_depletion < 0:
        return None

    trend_pct = ((actual_depletion - expected_depletion) / expected_depletion) * 100

    # Only flag if significant (>= 15%)
    return round(trend_pct, 1) if abs(trend_pct) >= 15 else None


def get_excluded_skus(tenant_id: str, session_id: str) -> list[dict]:
    """SKUs uploaded but left out of the forecast (recorded at training time)."""
    from backend.db import session_store
    result = session_store.get_training_result(tenant_id, session_id) or {}
    return result.get("excluded_skus") or []


def get_demand_spikes(
    tenant_id: str,
    session_id: str,
    service_level: float = 0.95,
    uplift_threshold: float = 0.25,
    items: Optional[list[dict]] = None,
    forecasts: Optional[dict] = None,
) -> list[dict]:
    """
    Proactive demand alerts — the value Excel can't give.

    Detects a demand peak the model projects within the forecast horizon and,
    given each SKU's lead time, computes the *latest date to order* so the spike
    is covered. Lets the buyer act on a peak the forecast sees BEFORE the stock
    semaphore turns red.

    Honest about its limits: only fires for peaks still in the future (relative
    to today) and within whatever horizon the session was trained for. If the
    forecast horizon is shorter than the lead time, the alert says "the peak
    arrives inside your lead time — order now if you haven't".
    """
    from datetime import date as _date

    if items is None:
        items = get_inventory_status(tenant_id, session_id, service_level)
    if forecasts is None:
        from backend.db import session_store
        forecasts = session_store.get_forecasts(tenant_id, session_id) or {}

    items_by_sku = {i["sku"]: i for i in items}
    today = _date.today()
    alerts: list[dict] = []

    for sku, model_forecasts in forecasts.items():
        item = items_by_sku.get(sku)
        if not item or not item.get("has_forecast"):
            continue

        curve = _avg_forecast_curve(model_forecasts)
        if len(curve) < 2:
            continue

        baseline = item.get("daily_demand")
        if not baseline or baseline <= 0:
            baseline = sum(c["value"] for c in curve) / len(curve)
        if not baseline or baseline <= 0:
            continue

        peak = max(curve, key=lambda c: c["value"])
        uplift = (peak["value"] - baseline) / baseline
        # Require both a relative and a small absolute jump (avoids noise on
        # tiny-volume SKUs where +30% is still < 1 unit).
        if uplift < uplift_threshold or (peak["value"] - baseline) < 1:
            continue

        lead = int(item.get("lead_time_days") or DEFAULT_LEAD_TIME_DAYS)

        peak_date: Optional[object] = None
        days_until = peak["step"] + 1
        if peak.get("date"):
            try:
                peak_date = _date.fromisoformat(peak["date"])
                days_until = (peak_date - today).days
            except Exception as e:
                log.debug("peak date parse failed sku=%s date=%r: %s", sku, peak.get("date"), e)
                peak_date = None

        # Skip peaks already in the past (stale session run long after training).
        if days_until <= 0:
            continue

        order_by = (peak_date - timedelta(days=lead)) if peak_date else None
        already_late = bool(order_by and order_by <= today)

        alerts.append({
            "sku":             sku,
            "display_name":    item.get("display_name") or sku,
            "supplier":       item.get("supplier"),
            "baseline_diaria": round(baseline, 1),
            "peak_value":      round(peak["value"], 1),
            "uplift_pct":      round(uplift * 100),
            "peak_date":       peak_date.isoformat() if peak_date else None,
            "days_until_peak": days_until,
            "lead_time_days":  lead,
            "order_by_date":   order_by.isoformat() if order_by else None,
            "already_late":    already_late,
            "signal":          item.get("signal"),
        })

    # Most actionable first: ones you're already late for, then soonest deadline.
    alerts.sort(key=lambda a: (not a["already_late"], a["days_until_peak"]))
    return alerts[:8]


def generate_recommendations(items: list[dict], period: str = "daily") -> list[dict]:
    """
    Generates plain-language, actionable recommendations from inventory status items.
    Each recommendation has: priority (1=critical), sku, name, rec_type, text, action.

    `period` (multi-period Phase C): the active planning grain. A period-trained
    session reports coverage in that grain's unit (a weekly session's
    coverage_days of 3 means 3 WEEKS), so the coverage figures are formatted in
    that unit and the "óptimo" ceiling is compared in the same unit. Lead time
    stays in real calendar days — a supplier takes N days regardless of the
    planning grain. "daily" reproduces the prior output byte-for-byte.
    """
    from backend.formatting import format_coverage
    days_per_period = _days_per_period(period)
    recs: list[dict] = []

    for item in items:
        sku      = item['sku']
        name     = item.get('display_name') or sku
        signal   = item['signal']
        days     = item.get('coverage_days')
        lead     = item.get('lead_time_days', DEFAULT_LEAD_TIME_DAYS)
        qty      = item.get('recommended_qty') or 0
        prov     = item.get('supplier') or 'el proveedor'
        abc      = item.get('abc', '?')
        trend    = item.get('demand_trend_pct')
        value    = item.get('inventory_value')

        if signal == 'PEDIR_YA' and days is not None:
            recs.append({
                'priority': 1, 'sku': sku, 'name': name,
                'rec_type': 'STOCKOUT_RISK',
                'text': (
                    f"Emite la orden de {name} HOY — tienes {format_coverage(days, period)} de stock "
                    f"y {prov} tarda {_format_days(lead)} en entregar. "
                    f"Si no actúas hoy, habrá quiebre antes de recibir el pedido."
                ),
                'action': f"Pedir {qty:.0f} unidades a {prov}" if qty > 0 else "Emitir orden urgente",
                'signal': signal,
            })

        elif signal == 'PEDIR_PRONTO' and days is not None:
            recs.append({
                'priority': 2, 'sku': sku, 'name': name,
                'rec_type': 'REORDER_SOON',
                'text': (
                    f"{name} tiene {format_coverage(days, period)} de cobertura frente a un lead time de {_format_days(lead)}. "
                    f"Emite el pedido esta semana para mantener el colchón de seguridad."
                ),
                'action': f"Pedir {qty:.0f} unidades antes del viernes" if qty > 0 else "Planificar pedido",
                'signal': signal,
            })

        if trend is not None:
            if trend >= 15:
                recs.append({
                    'priority': 3, 'sku': sku, 'name': name,
                    'rec_type': 'DEMAND_UP',
                    'text': (
                        f"La demanda real de {name} está corriendo {trend:.0f}% por encima del pronóstico. "
                        f"Considera aumentar el stock de seguridad o anticipar el próximo pedido."
                    ),
                    'action': "Revisar stock de seguridad",
                    'signal': signal,
                })
            elif trend <= -15:
                recs.append({
                    'priority': 4, 'sku': sku, 'name': name,
                    'rec_type': 'DEMAND_DOWN',
                    'text': (
                        f"La demanda de {name} está {abs(trend):.0f}% por debajo del pronóstico. "
                        f"Verifica si perdiste un cliente clave o hay un cambio de tendencia real."
                    ),
                    'action': "Revisar con el equipo de ventas",
                    'signal': signal,
                })

        if signal == 'SOBRESTOCK' and abc in ('A', 'B') and days is not None and value:
            # `days` is coverage in the active period's unit; the "óptimo" ceiling
            # is 3× the lead time expressed in that SAME unit, so the excess is a
            # coherent period figure (mixing weeks against day-count lead was the
            # weekly-mode bug that produced "-12 días más de lo óptimo").
            lead_periods = lead / days_per_period
            excess = days - lead_periods * 3
            recs.append({
                'priority': 5, 'sku': sku, 'name': name,
                'rec_type': 'OVERSTOCK',
                'text': (
                    f"{name} tiene {format_coverage(days, period)} de cobertura ({format_coverage(excess, period)} más de lo óptimo). "
                    f"Pausar el próximo pedido liberaría {money(value)} en capital de trabajo."
                ),
                'action': "Pausar próximo pedido",
                'signal': signal,
            })

    # Deduplicate by sku+rec_type, keep highest priority
    seen: dict = {}
    for r in sorted(recs, key=lambda x: x['priority']):
        key = f"{r['sku']}_{r['rec_type']}"
        if key not in seen:
            seen[key] = r

    return list(seen.values())[:20]  # top 20 recommendations


def compute_session_accuracy(rows: list[dict], items: list[dict]) -> Optional[float]:
    """Session-level accuracy: 1 - WAPE of each SKU's best real model.

    Baseline rows (naive & friends) are scored for reference only and must not
    drag the headline number down. The aggregate is weighted by each SKU's
    daily demand so low-volume SKUs don't dominate; falls back to a plain mean
    when no demand weights are available. Clamped at 0 (WAPE can exceed 1).
    """
    best_wape: dict[str, float] = {}
    for r in rows:
        if r.get('type') == 'baseline':
            continue
        wape = r.get('wape')
        if wape is None:
            continue
        sku = str(r.get('sku'))
        if sku not in best_wape or wape < best_wape[sku]:
            best_wape[sku] = wape
    if not best_wape:
        return None
    weights = {str(i.get('sku')): float(i.get('daily_demand') or 0.0) for i in items}
    total_weight = sum(weights.get(s, 0.0) for s in best_wape)
    if total_weight > 0:
        avg_wape = sum(w * weights.get(s, 0.0) for s, w in best_wape.items()) / total_weight
    elif items and all(i.get('daily_demand') is not None for i in items):
        # Demand is known for every SKU and it is zero everywhere. WAPE divides
        # by total real demand, so it collapses to 0 and this would report a
        # triumphant 100% over a catalog that never sold anything — there is no
        # scale to be accurate against. Only claimed when the information is
        # COMPLETE: a single unknown (None) means we cannot rule out real sales,
        # so those fall through to the plain mean instead of hiding a number.
        return None
    else:
        avg_wape = sum(best_wape.values()) / len(best_wape)
    return round(max(0.0, 1.0 - avg_wape), 4)


def get_morning_briefing(tenant_id: str, session_id: str, service_level: float = 0.95,
                         period: str = "daily") -> dict:
    """
    Returns everything needed for the daily operations briefing:
    - risks: SKUs with PEDIR_YA signal
    - warnings: SKUs with PEDIR_PRONTO signal
    - overstocked: SKUs with SOBRESTOCK, ordered by value
    - demand_changes: SKUs with significant demand trend
    - recommendations: plain-language action items
    - kpis: summary metrics

    `period` (multi-period Phase C): the active planning period the session was
    trained at. Coverage and the signal are judged in that unit — `/hoy` must
    pass it or a weekly/monthly session's per-period demand is misread as daily
    and everything flags PEDIR_YA. Default "daily" is byte-identical to before.
    """
    items = get_inventory_status(tenant_id, session_id, service_level, period)

    # Compute demand trend for each item that has forecast + stock data
    for item in items:
        avg = item.get('daily_demand')
        if avg and avg > 0 and item.get('has_stock') and item.get('has_forecast'):
            item['demand_trend_pct'] = _calc_demand_trend(
                tenant_id, item['sku'], avg, days=14
            )
        else:
            item['demand_trend_pct'] = None

    risks      = [i for i in items if i['signal'] == 'PEDIR_YA']
    warnings   = [i for i in items if i['signal'] == 'PEDIR_PRONTO']
    overstocked = sorted(
        [i for i in items if i['signal'] == 'SOBRESTOCK' and i.get('inventory_value')],
        key=lambda x: x.get('inventory_value') or 0,
        reverse=True,
    )
    demand_changes = [i for i in items if i.get('demand_trend_pct') is not None]

    # The forecasts blob (can be MBs) is fetched ONCE here and shared by the
    # demand-spike scan and the transfer-suggestion pass below.
    from backend.db import session_store
    try:
        briefing_forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
    except Exception as e:
        log.warning("briefing forecasts fetch failed session=%s: %s", session_id, e)
        briefing_forecasts = {}

    # Proactive: future demand peaks the forecast sees, with order-by dates.
    demand_spikes: list[dict] = []
    try:
        demand_spikes = get_demand_spikes(
            tenant_id, session_id, service_level,
            items=items, forecasts=briefing_forecasts,
        )
    except Exception as e:
        log.warning("get_demand_spikes failed for session=%s: %s", session_id, e)

    # Network transfer suggestions (feature 5.4): folded into the briefing so
    # /hoy renders them without a second full by-warehouse status request —
    # the landing page used to double-run the heaviest inventory computation.
    transfer_suggestions: list[dict] = []
    try:
        from backend.inventory import warehouse_service as wh_svc
        if wh_svc.count_warehouses(tenant_id) >= 2:
            wh_items = get_inventory_status_by_warehouse(
                tenant_id, session_id, service_level, period,
                forecasts=briefing_forecasts,
            )
            transfer_suggestions = [
                i for i in wh_items if i.get("recommended_action") == "transfer"
            ]
    except Exception as e:
        log.warning("briefing transfer suggestions failed session=%s: %s", session_id, e)

    recs = generate_recommendations(items, period)

    # Pull session-level forecast accuracy if available
    avg_accuracy: Optional[float] = None
    try:
        result = session_store.get_training_result(tenant_id, session_id) or {}
        metrics = result.get('metrics', {})
        avg_accuracy = compute_session_accuracy(metrics.get('rows', []), items)
    except Exception as e:
        log.debug("session accuracy lookup failed session=%s: %s", session_id, e)

    total_value    = sum(i['inventory_value'] for i in items if i.get('inventory_value') or 0)
    overstock_val  = sum(i['inventory_value'] for i in overstocked if i.get('inventory_value') or 0)

    # Session name
    try:
        from backend.sessions.service import get_session
        sess = get_session(tenant_id, session_id) or {}
        session_name = sess.get('name', session_id[:8])
    except Exception as e:
        log.debug("session name lookup failed session=%s: %s", session_id, e)
        session_name = session_id[:8]

    return {
        'date':         datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'session_id':   session_id,
        'session_name': session_name,
        'has_data':     bool(items),
        # Active planning grain + its coverage unit, so every consumer (the
        # narrative, the /hoy cards) labels the per-period coverage figures in
        # the right noun instead of a hardcoded "días".
        'period':         period,
        'coverage_unit':  _coverage_unit(period),
        'risks':        risks[:10],
        'warnings':     warnings[:10],
        'overstocked':  overstocked[:10],
        'demand_changes': demand_changes[:8],
        'demand_spikes': demand_spikes,
        'transfer_suggestions': transfer_suggestions,
        'excluded_skus': get_excluded_skus(tenant_id, session_id),
        'recommendations': recs,
        'kpis': {
            'total_skus':           len(items),
            'order_now':             len(risks),
            'order_soon':         len(warnings),
            'ok':                   sum(1 for i in items if i['signal'] == 'OK'),
            'overstock':           len(overstocked),
            'sin_datos':            sum(1 for i in items if i['signal'] == 'SIN_DATOS'),
            'avg_accuracy':         avg_accuracy,
            'total_inventory_value': round(total_value, 2),
            'capital_in_overstock':  round(overstock_val, 2),
            'demand_alerts':        len(demand_changes),
            'demand_spikes':        len(demand_spikes),
        },
    }


# ── Alert logic ───────────────────────────────────────────────────────────────

def get_tenants_with_active_sessions() -> list[dict]:
    """Returns all tenants that have at least one COMPLETED session."""
    return query(
        """SELECT DISTINCT s.tenant_id, t.name AS tenant_name,
                  MAX(s.updated_at) AS last_session_at
           FROM sessions s
           JOIN tenants t ON t.id = s.tenant_id
           WHERE s.status = 'COMPLETED'
           GROUP BY s.tenant_id, t.name""",
    )


def get_latest_completed_session(tenant_id: str) -> Optional[dict]:
    return query_one(
        """SELECT id AS session_id FROM sessions
           WHERE tenant_id = %s AND status = 'COMPLETED'
           ORDER BY updated_at DESC LIMIT 1""",
        (tenant_id,),
    )


def get_tenant_admin_emails(tenant_id: str) -> list[str]:
    rows = query(
        """SELECT email FROM users
           WHERE tenant_id = %s AND role IN ('admin', 'manager')
           AND email IS NOT NULL""",
        (tenant_id,),
    )
    return [r["email"] for r in rows]


def get_tenant_admin_whatsapps(tenant_id: str) -> list[str]:
    """E.164 numbers of admins/managers who opted into WhatsApp alerts."""
    rows = query(
        """SELECT whatsapp_number FROM users
           WHERE tenant_id = %s AND role IN ('admin', 'manager')
           AND whatsapp_number IS NOT NULL AND whatsapp_number <> ''""",
        (tenant_id,),
    )
    return [r["whatsapp_number"] for r in rows]


def get_tenant_alert_recipients(tenant_id: str) -> list[dict]:
    """
    Admins/managers with the identity needed to attribute a delivery outcome.
    The email/WhatsApp lists above return bare contact strings, which cannot be
    written to activity_logs (user_id is NOT NULL) — this returns the user row.
    """
    return [
        dict(r) for r in query(
            """SELECT id, email, whatsapp_number FROM users
               WHERE tenant_id = %s AND role IN ('admin', 'manager')""",
            (tenant_id,),
        )
    ]


def record_notification_delivery(
    tenant_id: str,
    user_id: str,
    action: str,
    delivered: bool,
    context: Optional[dict] = None,
) -> None:
    """
    Write the outcome of one outbound alert to the recipient's activity log.

    Chosen over a log-only warning because the failure mode is invisible by
    construction: when SMTP credentials expire the alerts simply stop, and the
    user reads "no alerts" as "no problems" — server logs are not something
    they can see. activity_logs is already per-user, already queryable from
    /me/activity, and already carries a `status` column, so a failed delivery
    surfaces in a screen the user opens. Successes are recorded too: without
    them the absence of a failed row is ambiguous (nothing wrong vs. loop never
    ran). Never raises — an unwritable audit row must not abort the alert loop.
    """
    from backend.activity.service import log_action
    try:
        log_action(
            tenant_id, user_id, action,
            context=context or {},
            status="success" if delivered else "failed",
        )
    except Exception as e:  # pragma: no cover - audit write must never break alerting
        log.warning("notification activity log failed user=%s action=%s: %s", user_id, action, e)


def run_daily_inventory_alerts() -> None:
    """
    Called once per day by the scheduler.
    For each tenant with a completed session, checks for PEDIR_YA SKUs
    and sends an alert email to admin/manager users.
    """
    from backend.notifications.email import send_inventory_alert_email
    from backend.config import settings

    tenants = get_tenants_with_active_sessions()
    log.info("inventory_alert: checking %d tenants", len(tenants))

    from backend.db import session_store
    from backend.sessions.planning_service import resolve_active_session

    for tenant in tenants:
        tid = tenant["tenant_id"]
        try:
            # Alert on the same session the app shows: the newest family's
            # active-period session (falls back to latest-completed for
            # family-less tenants — identical to the old behavior for them).
            sid = resolve_active_session(tid)
            if not sid:
                continue

            # Fetch the shared inputs ONCE per tenant: the aggregated status
            # and (for multi-warehouse tenants) the per-warehouse status both
            # need the same forecasts blob (can be MBs), stock rows and
            # learned lead times — before this, each call re-fetched all
            # three itself.
            forecasts = session_store.get_forecasts(tid, sid) or {}
            stock_rows = list_stock(tid)
            learned_lead_times = get_learned_lead_times(tid)

            items = _compute_inventory_status(
                tid, sid,
                forecasts=forecasts, stock_rows=stock_rows,
                learned_lead_times=learned_lead_times,
            )
            critical = [i for i in items if i["signal"] == "PEDIR_YA"]
            warning  = [i for i in items if i["signal"] == "PEDIR_PRONTO"]

            if not critical and not warning:
                continue

            # Transfer suggestions (feature 5.4): only meaningful — and only
            # computed — for tenants with 2+ warehouses.
            from backend.inventory import warehouse_service as wh_svc
            transfer_count = 0
            if wh_svc.count_warehouses(tid) >= 2:
                try:
                    wh_items = get_inventory_status_by_warehouse(
                        tid, sid,
                        forecasts=forecasts, stock_rows=stock_rows,
                        learned_lead_times=learned_lead_times,
                    )
                    transfer_count = sum(
                        1 for i in wh_items if i.get("recommended_action") == "transfer")
                except Exception as e:
                    log.debug("alert transfer count failed tenant=%s: %s", tid, e)

            app_url = getattr(settings, "frontend_url", "http://localhost:3000")
            inventory_url = f"{app_url}/hoy"

            # Full lists on purpose: the notification layer trims the rendered
            # rows itself, after counting, so the digest reports every SKU at
            # risk instead of the ten it had room to list.
            from backend.notifications import email as email_mod
            recipients = get_tenant_alert_recipients(tid)
            for r in recipients:
                if not r.get("email"):
                    continue
                delivered = send_inventory_alert_email(
                    to=r["email"],
                    critical_items=critical,
                    warning_items=warning,
                    inventory_url=inventory_url,
                )
                if not delivered:
                    log.warning("alert email not delivered to=%s", r["email"])
                record_notification_delivery(
                    tid, r["id"], "inventory_alert_email", delivered,
                    context={
                        "channel": "email",
                        "recipient": r["email"],
                        "critical": len(critical),
                        "warning": len(warning),
                        **({} if delivered else {"reason": email_mod.failure_reason()}),
                    },
                )

            # WhatsApp channel — highest open-rate in LatAm; opt-in per user
            # via users.whatsapp_number. No-op when Twilio isn't configured.
            # Also gated by plan: WHATSAPP_ALERTS is a Professional+ feature.
            # `tenant` here only carries tenant_id/tenant_name/last_session_at
            # (from get_tenants_with_active_sessions), not plan/trial_ends_at,
            # so the full tenant row must be fetched to check entitlement.
            from backend.entitlements.service import has_feature
            from backend.entitlements.plans import Feature
            from backend.tenants.service import get_tenant
            full_tenant = get_tenant(tid) or {}
            if has_feature(full_tenant, Feature.WHATSAPP_ALERTS):
                from backend.notifications import whatsapp as wa_mod
                from backend.notifications.whatsapp import build_inventory_alert_text, send_whatsapp
                text = build_inventory_alert_text(
                    critical, warning, inventory_url,
                    transfer_count=transfer_count,
                )
                for r in recipients:
                    number = (r.get("whatsapp_number") or "").strip()
                    if not number:
                        continue
                    delivered = send_whatsapp(number, text)
                    if not delivered:
                        log.warning("alert whatsapp not delivered to=%s", number)
                    record_notification_delivery(
                        tid, r["id"], "inventory_alert_whatsapp", delivered,
                        context={
                            "channel": "whatsapp",
                            "recipient": number,
                            "critical": len(critical),
                            "warning": len(warning),
                            **({} if delivered else {"reason": wa_mod.failure_reason()}),
                        },
                    )

        except Exception as e:
            log.error("inventory_alert: tenant=%s error=%s", tid, e)


def _sum_overstock_value(items: list[dict]) -> float:
    """Total inventory_value of SKUs currently flagged SOBRESTOCK."""
    return sum(
        (i.get("inventory_value") or 0)
        for i in items
        if i.get("signal") == "SOBRESTOCK"
    )


def run_monthly_overstock_snapshot() -> None:
    """
    Called once a month by the scheduler (day 1). For each tenant with a
    completed session, records the current total SOBRESTOCK value so the
    ROI monthly view can compute capital freed month over month.
    """
    tenants = get_tenants_with_active_sessions()
    log.info("overstock_snapshot: checking %d tenants", len(tenants))

    from backend.sessions.planning_service import resolve_active_session

    for tenant in tenants:
        tid = tenant["tenant_id"]
        try:
            sid = resolve_active_session(tid)
            if not sid:
                continue

            items = get_inventory_status(tid, sid)
            overstock_value = _sum_overstock_value(items)

            execute(
                """INSERT INTO inventory_overstock_snapshots
                       (tenant_id, session_id, overstock_value)
                   VALUES (%s, %s, %s)""",
                (tid, sid, overstock_value),
            )
        except Exception as e:
            log.error("overstock_snapshot: tenant=%s error=%s", tid, e)
