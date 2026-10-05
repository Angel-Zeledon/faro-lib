"""
Inventory intelligence service.

Combines inventory_stock (current levels) with session forecast data
to produce per-SKU signals, ABC-XYZ classification, and order recommendations.
"""

import math
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from backend.db.connection import query, query_one, execute
from backend.errors import AppError
from backend.formatting import money, format_days as _format_days, format_coverage, format_coverage_en
from backend.inventory.defaults import (
    DEFAULT_LEAD_TIME_DAYS,
    DEFAULT_MOQ,
    SOURCE_DEFAULT,
    SOURCE_FILE,
    SOURCE_LEARNED,
    SOURCE_USER,
)
from backend.inventory import signal_thresholds as _sig_th

log = logging.getLogger(__name__)


def _tenant_currency(tenant_id: str) -> dict:
    """The tenant's currency setting, for the money this module writes into
    Spanish sentences and PDFs. Imported lazily: the reader lives in the API
    layer and this module is imported by it.

    Every caller resolves this ONCE per document or briefing and passes the dict
    down — it is a DB read, so calling it inside a per-SKU loop would add one
    query per row.
    """
    from backend.api.v1.currency import currency_of
    return currency_of(tenant_id)


# Z-scores for common service levels, kept as the exact values the product has
# always used at those four points so a tenant's numbers do not move under it.
_Z = {0.90: 1.282, 0.95: 1.645, 0.97: 1.881, 0.99: 2.326}


def _z_for(service_level: float) -> float:
    """The normal quantile for a service level — for ANY service level.

    This was a dict lookup with a default, and the default was 1.645. The API
    accepts any level in [0.5, 0.999] (`inventory.py` Query bounds), the
    defaults cascade lets a tenant store 0.98 per SKU, and every one of those
    values that was not one of the four keys silently got the z of 95%. A
    buyer who deliberately raised a critical SKU to 98% got a 95% cushion and
    no way to notice: the number is correct-looking, just smaller than asked.

    Now: the four known points are returned verbatim, and anything else is
    computed with the Acklam rational approximation of the inverse normal CDF
    (|error| < 1.15e-9 over the whole domain). It stays pure Python on purpose
    — `backend/` may not import numpy or scipy outside the three modules the
    layering test allows, and the engine is not reachable from here.
    """
    if service_level in _Z:
        return _Z[service_level]
    # Outside the meaningful range the answer is not a cushion, it is a bug
    # upstream. Clamped rather than raised: this runs inside the 08:00 alert
    # loop, where an exception would cost a tenant their whole digest.
    p = min(max(float(service_level), 0.5), 0.999999)
    return _inverse_normal_cdf(p)


# Acklam's algorithm. The coefficients are the published ones; they are not
# derived here and must not be "tidied".
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)
_P_LOW = 0.02425


def _inverse_normal_cdf(p: float) -> float:
    """Φ⁻¹(p) for 0 < p < 1."""
    if p < _P_LOW:
        q = math.sqrt(-2 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
    if p <= 1 - _P_LOW:
        q = p - 0.5
        r = q * q
        return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
               (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
             ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
_SIGNAL_PRIORITY = {"PEDIR_YA": 0, "PEDIR_PRONTO": 1, "OK": 2, "SOBRESTOCK": 3, "SIN_DATOS": 4}


# ── Status ordering (server-side paging) ──────────────────────────────────────

STATUS_SORT_PATTERN = (
    "^(urgency|sku|coverage|value|recommended|signal|name|stock|demand_lt|qty|"
    "lead_time|moq|abc_xyz|decision|supplier_urgency)$"
)


def _num(field: str):
    """Sort key for a numeric field: a missing value counts as -inf, so it leads
    an ascending sort and trails a descending one, as the table always did."""
    return lambda i: i.get(field) if i.get(field) is not None else float("-inf")


_COLUMN_SORT_KEYS = {
    "signal":      lambda i: _SIGNAL_PRIORITY.get(i["signal"], 99),
    "name":        lambda i: (i.get("display_name") or i.get("sku") or "").lower(),
    "sku":         lambda i: str(i.get("sku") or ""),
    "stock":       _num("current_stock"),
    "coverage":    _num("coverage_days"),
    "demand_lt":   _num("lead_time_demand"),
    "qty":         _num("recommended_qty"),
    "recommended": _num("recommended_qty"),
    "lead_time":   _num("lead_time_days"),
    "moq":         _num("moq"),
    "abc_xyz":     lambda i: i.get("abc_xyz") or "ZZ",
    "value":       _num("inventory_value"),
}


def sort_status_items(items: list[dict], sort: str, order: Optional[str] = None) -> list[dict]:
    """Orders the (already filtered) status rows for a page.

    `urgency` is the service's own order. `decision` puts everything that needs
    a purchasing decision ahead of OK / SIN_DATOS rows (the simple view).
    `supplier_urgency` keeps each supplier's rows together, suppliers holding the
    most urgent product first, so a page boundary never scatters a group. Column
    sorts take `order`; with `order` omitted the original defaults hold
    (coverage ascending, value and recommended descending).
    """
    if sort == "urgency":
        return items
    if sort == "decision":
        return sorted(items, key=lambda i: i["signal"] in ("OK", "SIN_DATOS"))
    if sort == "supplier_urgency":
        best: dict[str, int] = {}
        for i in items:
            k = i.get("supplier") or ""
            best[k] = min(best.get(k, 99), _SIGNAL_PRIORITY.get(i["signal"], 99))
        return sorted(items, key=lambda i: (best[i.get("supplier") or ""],
                                            (i.get("supplier") or "").casefold(),
                                            i.get("supplier") or ""))
    if order is None:
        if sort == "coverage":
            return sorted(items, key=lambda i: (i.get("coverage_days") is None, i.get("coverage_days") or 0))
        if sort == "value":
            return sorted(items, key=lambda i: -(i.get("inventory_value") or 0))
        if sort == "recommended":
            return sorted(items, key=lambda i: -(i.get("recommended_qty") or 0))
    by_sku = sorted(items, key=lambda i: str(i.get("sku") or ""))   # stable tiebreak, always ascending
    return sorted(by_sku, key=_COLUMN_SORT_KEYS[sort], reverse=(order == "desc"))


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
    # A SKU of nothing but spaces is not a SKU. `PUT /inventory/stock/%20%20%20`
    # answered 200 and left a row whose code renders as nothing at all: it
    # cannot be found in the list, cannot be searched for, and cannot be deleted
    # from the UI. Refused at the chokepoint every write path funnels through,
    # so the bulk import and the transfer path get the same answer as the
    # screen. Non-blank SKUs are NOT trimmed — that would silently merge
    # " SKU-1 " into an existing "SKU-1" and take its stock with it.
    if not str(sku or "").strip():
        raise AppError("sku_blank", "SKU cannot be blank", status_code=422)

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
        # On `conn` when there is one: under a limit_guard, the count, the check
        # and the INSERT below are one transaction holding one lock, which is
        # the only arrangement in which this ceiling actually holds. Without a
        # conn it degrades to the old read-then-write, which is correct for one
        # caller at a time and beatable by two.
        enforce_limit(tenant_id, "max_skus", count_stock(tenant_id, conn=conn), conn=conn)
    if not wh_svc.get_warehouse_by_name(tenant_id, safe["warehouse"]):
        enforce_limit(tenant_id, "max_locations", wh_svc.count_warehouses(tenant_id),
                      conn=conn)

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

    # Auto-snapshot when current_stock is updated, stamped with the warehouse
    # the level belongs to — `safe["warehouse"]` is the canonical name this
    # write landed on, not the spelling the caller sent.
    if "current_stock" in safe and row:
        _record_snapshot(tenant_id, sku, float(safe["current_stock"]), conn=conn,
                         warehouse=safe["warehouse"])

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
        # is_default on the tenant's FIRST warehouse, decided inside the same
        # statement so this stays one round-trip on the stock-write path. Without
        # it every row kept is_default = false and "which is the default" fell
        # through to name precedence — an answer that moves when a warehouse is
        # renamed. See warehouse_service.create_warehouse for the other path.
        execute(
            "INSERT INTO warehouses (tenant_id, name, is_default) "
            "SELECT %s, %s, NOT EXISTS ("
            "    SELECT 1 FROM warehouses WHERE tenant_id = %s"
            ") "
            "ON CONFLICT (tenant_id, name) DO NOTHING",
            (tenant_id, name, tenant_id),
            conn=conn,
        )
    except Exception as e:
        log.warning("_ensure_warehouse: failed to upsert warehouse=%s tenant=%s err=%s", name, tenant_id, e)


def count_stock(tenant_id: str, conn: Optional[Any] = None) -> int:
    """`conn` matters when this count is about to be enforced as a ceiling: it
    has to be read on the same connection that holds the tenant's limit_guard
    lock and will perform the write, or the count and the write are two
    different moments again."""
    row = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id = %s",
        (tenant_id,), conn=conn,
    )
    return row["c"] if row else 0


def list_stock_keys(tenant_id: str, conn: Optional[Any] = None) -> set:
    """(sku, warehouse) pairs already present for this tenant — the same
    conflict target `upsert_stock` writes to, used to tell how many rows a
    bulk import would actually ADD (vs. update in place).

    `conn` when the answer is about to be enforced as a ceiling: read outside
    the lock, "how many of these are new" is already stale by the time it is
    checked."""
    rows = query(
        "SELECT sku, warehouse FROM inventory_stock WHERE tenant_id = %s",
        (tenant_id,), conn=conn,
    )
    return {(r["sku"], r["warehouse"]) for r in rows}


def list_stock_warehouses(
    tenant_id: str, sku: str, conn: Optional[Any] = None
) -> list[str]:
    """Every warehouse this SKU actually has a stock row in, ordered.

    Exists because "does this SKU exist?" and "which row am I about to write?"
    are two different questions, and PATCH /stock/{sku} used to answer the
    second with the first: it 404-checked `get_stock()` with no warehouse
    filter — which finds the row wherever it lives — and then wrote through
    `upsert_stock`, which falls back to `principal` when no warehouse is
    supplied. A SKU that only lived in 'Norte' therefore passed the existence
    check on the Norte row and CREATED a second row in 'principal'. The Norte
    row stayed put, the consolidated view summed both, and the coverage the
    semáforo reads was inflated by units that do not exist — a SKU that should
    have read PEDIR_YA reads OK and never gets bought.
    """
    rows = query(
        "SELECT warehouse FROM inventory_stock WHERE tenant_id = %s AND sku = %s "
        "ORDER BY warehouse",
        (tenant_id, sku),
        conn=conn,
    )
    return [r["warehouse"] for r in rows]


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


def list_stock_page(
    tenant_id: str, limit: int = 50, offset: int = 0,
    q: Optional[str] = None, warehouse: Optional[str] = None,
) -> dict:
    """One page of stock rows. `total` counts the filtered set, so a screen can
    say "51-100 of 4,812" without having loaded the other 4,700."""
    where, params = "tenant_id = %s", [tenant_id]
    if q and q.strip():
        where += " AND (sku ILIKE %s OR display_name ILIKE %s OR category ILIKE %s OR supplier ILIKE %s)"
        like = f"%{q.strip()}%"
        params += [like, like, like, like]
    if warehouse:
        where += " AND warehouse = %s"
        params.append(warehouse)
    rows = query(
        f"SELECT * FROM inventory_stock WHERE {where} ORDER BY sku, warehouse LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    total = query_one(f"SELECT COUNT(*) AS n FROM inventory_stock WHERE {where}", tuple(params))
    return {"items": rows, "total": int(total["n"]) if total else 0,
            "limit": limit, "offset": offset}


def delete_stock(tenant_id: str, sku: str) -> None:
    execute(
        "DELETE FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )


def get_incoming_detail(tenant_id: str) -> list[dict]:
    """THE definition of "stock already on its way" — one row per open source.

    Every consumer (the semáforo's recommended quantity, /compras, /inventario,
    the optimizer's opening position, the daily alerts and the exports) reads
    this through `get_incoming_qty`, so there is exactly one rule:

      · Purchase orders: every line the buyer ordered ('approved'/'modified')
        on a PO that can still take goods in (`RECEIVABLE_STATES`: pending,
        partial, not_received), counting only what has NOT arrived yet
        (final_qty − received_qty, never below 0 per line).
      · Transfers in transit from another of the tenant's own warehouses,
        credited to the DESTINATION only (the origin already lost the units at
        send time inside `transfer_service.create_transfer`'s transaction).

    Whether the PO was ever *sent through StockAI* (`sent_at`) is deliberately
    NOT part of the rule. It used to be: only POs stamped by the in-app send
    counted. But the everyday path is "Descargar orden de compra" — a CSV the
    buyer mails or WhatsApps from their own phone — which never stamps
    `sent_at`. Mobile QA, 2026-10-01: 426 units of SKU-001 on OC-000001/2,
    downloaded and pending, and the panel still said "Pedir pronto, 63
    unidades". The buyer orders the same units twice, which is real money.
    /pedidos, the reception nudge and the overdue list (`get_overdue_receptions`)
    already treated a generated PO as a commitment — this was the one reader
    that disagreed with them.

    `not_received` stays included: it means "nothing had arrived when I
    looked", not "this will never arrive".

    A CANCELLED PO (`cancelled_at` set, `po_cancel_service`) is not on its way
    and never counts, whatever its reception status says.

    Rows: {sku, warehouse, qty, kind: 'po'|'transfer', reference, source_id}.
    `reference` is the human order number (OC-000123) for a PO and the origin
    warehouse for a transfer.
    """
    from backend.inventory.reception_service import RECEIVABLE_STATES
    from backend.inventory.roi_service import format_po_number
    from backend.inventory.warehouse_service import DEFAULT_WAREHOUSE

    out: list[dict] = []

    for r in query(
        """SELECT poi.sku, poi.warehouse, pol.id AS po_log_id, pol.po_number,
                  SUM(GREATEST(poi.final_qty - COALESCE(poi.received_qty, 0), 0)) AS qty
             FROM inventory_po_items poi
             JOIN inventory_po_log pol ON pol.id = poi.po_log_id
            WHERE poi.tenant_id = %s
              AND pol.tenant_id = %s
              AND pol.reception_status IN %s
              AND pol.cancelled_at IS NULL
              AND poi.status IN ('approved', 'modified')
            GROUP BY poi.sku, poi.warehouse, pol.id, pol.po_number
            ORDER BY pol.po_number NULLS LAST, pol.id""",
        (tenant_id, tenant_id, tuple(RECEIVABLE_STATES)),
    ):
        qty = float(r["qty"] or 0)
        if qty > 0:
            out.append({
                "sku": r["sku"],
                "warehouse": r["warehouse"] or DEFAULT_WAREHOUSE,
                "qty": qty,
                "kind": "po",
                "reference": format_po_number(r["po_number"], str(r["po_log_id"])),
                "source_id": str(r["po_log_id"]),
            })

    for r in query(
        """SELECT tri.sku, trl.to_warehouse AS warehouse, trl.id AS transfer_id,
                  trl.from_warehouse,
                  SUM(GREATEST(tri.qty_sent - COALESCE(tri.qty_received, 0), 0)) AS qty
             FROM inventory_transfer_items tri
             JOIN inventory_transfer_log trl ON trl.id = tri.transfer_id
            WHERE tri.tenant_id = %s
              AND trl.status IN ('in_transit', 'partial')
            GROUP BY tri.sku, trl.to_warehouse, trl.id, trl.from_warehouse
            ORDER BY trl.id""",
        (tenant_id,),
    ):
        qty = float(r["qty"] or 0)
        if qty > 0:
            out.append({
                "sku": r["sku"],
                "warehouse": r["warehouse"] or DEFAULT_WAREHOUSE,
                "qty": qty,
                "kind": "transfer",
                "reference": r["from_warehouse"],
                "source_id": str(r["transfer_id"]),
            })

    return out


def sum_incoming(detail: list[dict]) -> dict[tuple[str, str], float]:
    """Collapse `get_incoming_detail` rows to {(sku, warehouse): qty}."""
    incoming: dict[tuple[str, str], float] = {}
    for d in detail:
        key = (d["sku"], d["warehouse"])
        incoming[key] = incoming.get(key, 0.0) + float(d["qty"])
    return incoming


def incoming_sources_by_key(
    detail: list[dict],
) -> dict[tuple[str, str], list[dict]]:
    """{(sku, warehouse): [{kind, reference, qty}, ...]} — what the screen
    prints next to the quantity ("426 en camino: OC-000001, OC-000002")."""
    out: dict[tuple[str, str], list[dict]] = {}
    for d in detail:
        out.setdefault((d["sku"], d["warehouse"]), []).append({
            "kind": d["kind"], "reference": d["reference"],
            "qty": round(float(d["qty"]), 2),
        })
    return out


def get_incoming_qty(tenant_id: str) -> dict[tuple[str, str], float]:
    """Units already on their way, per (sku, warehouse). The rule lives in
    `get_incoming_detail`; this is its per-key total."""
    return sum_incoming(get_incoming_detail(tenant_id))


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
# threshold to 0 (every lead-time multiple is 0), permanently misreporting
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

    # CHOKEPOINT (pre-loop): this is the PRIMARY way SKUs enter StockAI (Quick
    # Start upload), yet unlike PUT /stock and POST /bulk it had no max_skus
    # check at all — a Starter tenant could seed thousands of SKUs in one
    # upload. Computed BEFORE the loop, atomically over the WHOLE dataset, so
    # a blocked sync leaves inventory_stock completely unchanged rather than
    # inserting rows until the per-row upsert_stock chokepoint finally objects
    # partway through (mirrors POST /bulk's pre-loop max_locations/max_skus
    # checks). Dataset rows never carry an explicit warehouse (not in
    # _DATASET_STOCK_COLS), so every new key lands in "principal".
    existing_keys = list_stock_keys(tenant_id)

    # A price is not an inventory count.
    #
    # `current_stock` is NOT NULL DEFAULT 0, so CREATING a stock row for a SKU
    # whose dataset said nothing about stock materialises a 0 — and the semáforo
    # cannot tell that 0 from an empty shelf. Measured on a real upload: a file
    # whose only inventory-ish mapping was `precio_unitario` seeded 200 rows with
    # a sale_price and current_stock = 0, and every one of the 200 came out
    # "PEDIR YA" for a catalogue nobody had ever counted.
    #
    # So a row is only CREATED when the dataset says something about what is on
    # the shelf or how it is replenished. Price-only data still UPDATES a row
    # that already exists — that is useful and invents nothing.
    _STOCK_DEFINING = {"current_stock", "lead_time_days", "min_stock", "moq"}
    filtered: list[tuple[str, dict]] = []
    skipped_no_stock_signal = 0
    for sku, data in entries:
        if (sku, "principal") in existing_keys or (_STOCK_DEFINING & data.keys()):
            filtered.append((sku, data))
        else:
            skipped_no_stock_signal += 1
    if skipped_no_stock_signal:
        log.info(
            "sync_stock_from_dataset: %d SKU(s) not created — the file carried no "
            "stock, lead time, min stock or MOQ for them (tenant=%s)",
            skipped_no_stock_signal, tenant_id,
        )
    entries = filtered
    if not entries:
        return 0

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
    failures: Optional[list[dict]] = None,
) -> int:
    """Upsert multiple SKUs from a CSV/bulk import. Returns count saved.

    `failures`, when a list is passed, collects one entry per row that was read
    from the file and did NOT reach the database: `{sku, warehouse, error}`.
    The count already shrank for those rows, so the number was never a lie —
    but "83 products imported" after a clean 120-row preview was the only
    signal the user got, and it named neither the 37 rows nor a reason
    (stability 11.34). The caller decides what to do with them; passing
    nothing keeps the old behaviour for every other caller.

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
            if failures is not None:
                # The reason is a CODE, not the driver's sentence: the exception
                # text is English prose from psycopg2 and would land on a
                # Spanish screen verbatim. The row and its warehouse are what
                # the user needs to find it in their file.
                # Same shape as every other row error in this channel
                # (`_row_error` in api/v1/inventory.py): a stable `code` the UI
                # renders through i18n, its params, and an English fallback.
                # The driver's own sentence is never the message — it is
                # English prose that would land on a Spanish screen verbatim.
                failures.append({
                    "row":    None,
                    "sku":    sku,
                    "code":   "inventory_import_row_write_failed",
                    "params": {"warehouse": (row.get("warehouse") or "").strip() or None},
                    "error":  "inventory_import_row_write_failed",
                })
    return count


# ── Stock snapshots ───────────────────────────────────────────────────────────

def _record_snapshot(
    tenant_id: str, sku: str, current_stock: float, conn: Optional[Any] = None,
    warehouse: Optional[str] = None,
) -> None:
    """Record a point-in-time stock level. Called automatically on upsert.

    `warehouse` is the location this level belongs to. It was missing until
    2026-09-16, and without it one SKU's rows were a single interleaved series
    across every warehouse (stability 11.15). Rows written before that carry
    NULL and are read as tenant-wide totals, which is what they are.

    `conn`: see upsert_stock's docstring.
    """
    try:
        execute(
            "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse) "
            "VALUES (%s, %s, %s, %s)",
            (tenant_id, sku, current_stock, warehouse),
            conn=conn,
        )
    except Exception as e:
        log.warning("snapshot record failed sku=%s: %s", sku, e)


def get_stock_history(
    tenant_id: str, sku: str, days: int = 30, warehouse: Optional[str] = None,
) -> list[dict]:
    """Stock snapshots for the last N days, oldest first.

    Two readings, and the difference is the whole point of 11.15:

    * `warehouse` given — only that location's rows, in the order they were
      written. A location's own history starts when the column landed
      (2026-09-16); older rows have no warehouse and are not attributed to one.
    * `warehouse` omitted — the TENANT-WIDE level, which is a sum across
      locations and not a list of their rows interleaved. Per-warehouse rows are
      collapsed to one value per location per day (the last of that day) and
      summed; legacy rows, which were already tenant-wide totals, join that
      series unchanged. Before this, principal at 500 and Norte at 20 produced
      `500, 20, 500, 20` and `_calc_demand_trend` read the difference as real
      consumption.
    """
    from datetime import timezone
    since = datetime.now(timezone.utc) - timedelta(days=days)

    if warehouse:
        rows = query(
            """SELECT current_stock, recorded_at
               FROM inventory_snapshots
               WHERE tenant_id = %s AND sku = %s AND recorded_at >= %s
                 AND warehouse = %s
               ORDER BY recorded_at ASC""",
            (tenant_id, sku, since, warehouse),
        )
        return [{"stock": r["current_stock"], "date": r["recorded_at"].isoformat()}
                for r in rows]

    return [{"stock": level, "date": at.isoformat()}
            for at, level in tenant_wide_history(tenant_id, sku, since)]


def tenant_wide_history(
    tenant_id: str, sku: str, since: datetime,
) -> list[tuple[datetime, float]]:
    """The tenant-wide stock level of one SKU since `since`, oldest first, one
    point per day — see `tenant_wide_daily_levels`. The single reader every
    "how did this SKU's total stock move" question should go through."""
    # Every per-warehouse write in the window, plus each warehouse's last level
    # BEFORE the window — the level it still held when the window opened.
    rows = query(
        """SELECT warehouse, current_stock, recorded_at
           FROM inventory_snapshots
           WHERE tenant_id = %s AND sku = %s AND recorded_at >= %s
             AND warehouse IS NOT NULL
           ORDER BY recorded_at ASC""",
        (tenant_id, sku, since),
    )
    opening = query(
        """SELECT DISTINCT ON (warehouse) warehouse, current_stock
           FROM inventory_snapshots
           WHERE tenant_id = %s AND sku = %s AND recorded_at < %s
             AND warehouse IS NOT NULL
           ORDER BY warehouse, recorded_at DESC""",
        (tenant_id, sku, since),
    )
    # Written before the column existed: already tenant-wide.
    legacy = query(
        """SELECT current_stock, recorded_at
           FROM inventory_snapshots
           WHERE tenant_id = %s AND sku = %s AND recorded_at >= %s
             AND warehouse IS NULL""",
        (tenant_id, sku, since),
    )
    points = tenant_wide_daily_levels(
        rows, {r["warehouse"]: float(r["current_stock"]) for r in opening},
    ) + [(r["recorded_at"], float(r["current_stock"])) for r in legacy]
    points.sort(key=lambda p: p[0])
    return points


def tenant_wide_daily_levels(
    rows: list[dict], opening: Optional[dict] = None,
) -> list[tuple[datetime, float]]:
    """One tenant-wide stock level per day, from per-warehouse snapshot rows.

    `rows` are `{warehouse, current_stock, recorded_at}` ordered by time;
    `opening` is each warehouse's level before the first row.

    A day's total is the sum of EVERY warehouse's latest known level as of the
    end of that day — not only of the warehouses that happened to be written
    that day. The SQL this replaced summed the latter, so principal written on
    Monday (500) and Norte on Wednesday (20) came out as the series 500, 20: a
    480-unit "fall" in a tenant whose stock never moved, read by the demand
    trend as consumption and by dead capital as movement — the very artefact
    stability 11.15 set out to remove (math audit 2026-10-01).
    """
    levels: dict[str, float] = dict(opening or {})
    out: list[tuple[datetime, float]] = []
    current_day = None
    last_at = None
    for r in rows:
        day = r["recorded_at"].date()
        if current_day is not None and day != current_day:
            out.append((last_at, sum(levels.values())))
        current_day = day
        last_at = r["recorded_at"]
        levels[r["warehouse"]] = float(r["current_stock"])
    if current_day is not None:
        out.append((last_at, sum(levels.values())))
    return out


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

# norm.ppf(0.9). The engine writes q90 = value + 1.2816 * residual_std, so
# dividing the q90 spread by this recovers the residual sigma exactly.
_Q90_Z = 1.2816


def _point_sigma(p: dict) -> float:
    """One standard deviation of the forecast error at a single step.

    `upper` is NOT a sigma. When quantile models are fitted the engine sets
    upper = p90 from the quantile model itself, which is fitted on the training
    set and is therefore far too tight: measured over the demo tenant's 8,240
    points, xgboost's upper spread is 43% narrower than its own honest q90.
    Treating that as sigma and multiplying by z(service_level) again produced a
    safety stock that depended on which model happened to win rather than on
    the service level the buyer asked for — roughly 89% effective coverage on
    xgboost against 98% on lightgbm, for the same 95% setting.

    `q90` is built from OUT-OF-FOLD residuals (trainer._wfv collects them per
    fold), so it is the honest one. Legacy sessions predate the quantile keys;
    those fall back to the old spread so they keep working.
    """
    value = float(p.get("value") or 0.0)
    q90 = p.get("q90")
    if q90 is not None:
        return max(0.0, (float(q90) - value) / _Q90_Z)
    upper = p.get("upper")
    if upper is None:
        return 0.0
    # `upper` is the TOP of a band, not a sigma. The engine has always written
    # it as roughly the 90th percentile, so returning the raw spread here handed
    # the caller ~1.28 sigma and the caller multiplied by z(service_level)
    # again — a configured 95% service level was really being served at ~98%,
    # and nothing in the product said so. Dividing by the same z the q90 branch
    # uses makes both branches return the same quantity.
    return max(0.0, (float(upper) - value) / _Q90_Z)


def _pick_model(model_forecasts: dict, preferred: Optional[str]) -> dict:
    """The models to plan from: the best one for this SKU when we know it.

    Averaging every model was pulling the purchase quantity toward the weakest
    one — on the demo tenant, prophet at 10.9% WAPE dragging on xgboost at
    3.7% — and it also averaged `ensemble` alongside the very models it is
    built from, counting them twice. It disagreed with the headline accuracy
    too, since that is computed from each SKU's BEST model.
    """
    # The chosen model must actually carry points. A metrics row can name a
    # model whose forecast failed to store, and narrowing to it would leave the
    # SKU with zero demand — which reads as "well stocked" and drops it off the
    # semáforo silently. Pooling is a worse number; disappearing is a worse bug.
    if preferred:
        chosen = model_forecasts.get(preferred)
        if chosen and (chosen.get("forecast") or []):
            return {preferred: chosen}
    return model_forecasts


def _avg_daily_forecast(
    model_forecasts: dict, lead_time: int, preferred_model: Optional[str] = None,
) -> tuple[float, float]:
    """
    Returns (avg_daily_demand, avg_daily_std) over the first `lead_time`
    forecast steps of this SKU's best model (all models when it is unknown).
    """
    all_values: list[float] = []
    all_stds:   list[float] = []

    for model_data in _pick_model(model_forecasts, preferred_model).values():
        pts = model_data.get("forecast", [])[:lead_time]
        if not pts:
            continue
        all_values.extend([p.get("value") or 0.0 for p in pts])
        all_stds.extend([_point_sigma(p) for p in pts])

    if not all_values:
        return 0.0, 0.0

    avg_daily = sum(all_values) / len(all_values)
    avg_std   = sum(all_stds) / len(all_stds) if all_stds else 0.0
    return max(0.0, avg_daily), max(0.0, avg_std)


def _avg_forecast_curve(
    model_forecasts: dict, max_steps: int = 90, preferred_model: Optional[str] = None,
) -> list[dict]:
    """
    Per-step forecast curve for this SKU's best model, aligned by step index and
    returned chronologically as [{step, date, value}]. Falls back to averaging
    every model when the best one is unknown. Dates come from whichever model
    provides them (all models share the same horizon).

    Same selection as `_avg_daily_forecast` on purpose: the curve the buyer
    reads has to be the one the recommended quantity came from.
    """
    step_values: dict[int, list[float]] = {}
    step_dates:  dict[int, str] = {}
    for model_data in _pick_model(model_forecasts, preferred_model).values():
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


def _calc_signal(coverage_days: float, lead_time: float, reorder_point_days: float,
                 thresholds: Optional[dict] = None) -> str:
    """Classifies days-of-stock-cover into the four persisted signals.

    stability.md 17c: the ordering boundary is now the reorder point itself,
    not an arbitrary multiple of the lead time. The reorder point (lead-time
    demand + safety stock) already answers "how much cover do I need to
    survive the wait for a replenishment" — a flat `1.2 * lead_time` ignored
    it and could sit BELOW the reorder point for any SKU whose safety stock
    exceeded `0.2 * lead_time * avg_daily`, i.e. most volatile/intermittent
    SKUs. In that band the old code reported OK and zeroed the recommendation
    for a SKU that was, by its own reorder point, already due to be ordered.

    Four-way split, `reorder_point_days` being the reorder point expressed in
    the same days-of-cover unit as `coverage_days` (reorder_point / avg_daily):

    - `coverage_days >= 9990` (the 9999-day sentinel for `avg_daily <= 0`, no
      measurable demand) -> SOBRESTOCK, unchanged from before this fix. A
      dead/discontinued SKU with any stock at all is overstock, never an
      ordering signal — there is nothing to order it FOR.
    - `coverage_days < 0.5 * lead_time` -> PEDIR_YA. Unchanged: PEDIR_YA has
      always meant "already in trouble" — less than half a lead time of cover
      — independent of the safety cushion. Half a lead time is always <= the
      reorder point in days (reorder_point_days = lead_time + SS/avg_daily >=
      lead_time > 0.5 * lead_time for any SS >= 0), so this sub-band always
      sits inside "at or below the reorder point" and never contradicts it.
    - `coverage_days <= reorder_point_days` -> PEDIR_PRONTO. The new boundary:
      at or below the reorder point, the incoming shipment would not land
      before the shelf runs out — "time to place the order."
    - Between the reorder point and `sobrestock_at` -> OK; at or above it ->
      SOBRESTOCK. `sobrestock_at` is the GREATER of `3 * lead_time` (the
      original flat threshold, kept as the floor for the common case of a
      small safety stock, so a stable SKU's classification does not move) and
      `2 * reorder_point_days` (so a genuinely volatile SKU, whose own reorder
      point can already sit past 3 lead times, still gets a real, non-empty OK
      band instead of an inverted one).

    The two lead-time multiples above (0.5 and 3) are the DEFAULTS of
    `signal_thresholds`; the buyer can configure them per tenant, supplier or
    category. `thresholds` is what `signal_thresholds.resolve_signal_thresholds`
    returned for this SKU — every production caller passes it. The middle
    boundary stays the reorder point whatever is configured, so no setting can
    bring back the 17c defect of an OK below the reorder point, and the bounds
    in `signal_thresholds.FACTOR_BOUNDS` (order-now < 1) keep PEDIR_YA inside
    the ordering band.
    """
    th = thresholds or _sig_th.DEFAULT_THRESHOLDS
    if coverage_days >= 9990:
        return "SOBRESTOCK"
    if coverage_days < lead_time * th["order_now_factor"]:
        return "PEDIR_YA"
    if coverage_days <= reorder_point_days:
        return "PEDIR_PRONTO"
    sobrestock_at = max(
        lead_time * th["overstock_factor"],
        reorder_point_days * _sig_th.OVERSTOCK_REORDER_POINT_MULTIPLE,
    )
    if coverage_days < sobrestock_at:
        return "OK"
    return "SOBRESTOCK"


# Series classes whose cushion was MEASURED unable to keep the service level the
# buyer configured (stability.md 17b): on intermittent demand the lead-time sum
# is zero-inflated and skewed, and four modelling attempts — per-horizon bands
# twice, stratified banks, a parametric compound model — delivered ~50% against
# a nominal 95%. Until something measures better, the row SAYS so instead of
# printing a percentage it does not keep ("degrade out loud", CLAUDE.md).
# A code, not a sentence: the frontend renders it in the reader's language.
_SERVICE_LEVEL_CAVEAT_BY_FLAG = {"intermittent": "intermittent_demand"}


def _service_level_caveats(result: dict) -> dict[str, str]:
    """{sku: caveat code} from the run's stored routing plan.

    `routing[sku]["flags"]` is the engine's multi-label series classification
    (`ForecastEngine.get_routing_plan`), stored with every training result. A
    session trained before flags were stored yields {} — no caveat is invented
    for a series nobody classified.
    """
    caveats: dict[str, str] = {}
    for sku, plan in (result.get("routing") or {}).items():
        if not isinstance(plan, dict):
            continue
        flags = set(plan.get("flags") or [])
        for flag, code in _SERVICE_LEVEL_CAVEAT_BY_FLAG.items():
            if flag in flags:
                caveats[str(sku)] = code
                break
    return caveats


def _measured_safety_stock(
    risk: Optional[dict], lead_time: float, service_level: float,
) -> Optional[float]:
    """
    Safety stock read off the engine's measured cumulative error, or None.

    `z * sigma * sqrt(L)` is an approximation of the quantile of demand over the
    lead time, and it assumes the per-bucket forecast errors are normal and
    independent. Neither holds: demand is non-negative and skewed, and a
    forecast that runs high today runs high tomorrow, so the errors compound
    faster than `sqrt(L)`. When the engine ran a rolling-origin backtest it
    measured the cumulative error at each lead time directly, and that number
    needs no assumptions at all.

    This covers DEMAND uncertainty only — the spread of cumulative demand over
    a lead time assumed FIXED at its mean. Lead-time uncertainty (the supplier
    sometimes takes longer) is a separate, independent source of variance that
    this band says nothing about; `_safety_stock` adds it back in quadrature
    rather than treating this measurement as the whole cushion.

    Returns None whenever the measurement is absent — the caller then keeps the
    classical formula rather than pretending.
    """
    if not risk:
        return None
    offsets = risk.get("cumulative_offsets") or {}
    if not offsets:
        return None

    horizons = sorted(int(h) for h in offsets if str(h).isdigit())
    if not horizons:
        return None
    wanted = max(1, int(math.ceil(float(lead_time))))
    key = wanted if wanted in horizons else max(
        [h for h in horizons if h <= wanted] or [horizons[0]]
    )
    band = offsets.get(str(key)) or {}
    if not band:
        return None

    try:
        target = float(service_level)
        levels = {float(q): float(v) for q, v in band.items()}
    except (TypeError, ValueError):
        return None

    # The quantile the buyer asked for. The engine measures a handful of levels
    # (0.5 / 0.9 / 0.95 by default) and the API accepts any level in
    # [0.5, 0.999]; this used to return the NEAREST measured level verbatim, so
    # a SKU raised to 99% got exactly the 95% cushion — the same defect the
    # `_z_for` docstring records fixing for the classical path (stability
    # 2026-09-14), re-opened on the measured one (math audit 2026-10-01).
    # An exactly measured level is used as is; otherwise the nearest measured
    # level ABOVE the median is rescaled by z(target) / z(measured), which
    # keeps the measured spread and only assumes the tail keeps its shape.
    exact = [q for q in levels if abs(q - target) < 1e-6]
    if exact:
        offset = levels[exact[0]]
    else:
        upper = [q for q in levels if q > 0.5]
        if not upper:
            return None
        nearest = min(upper, key=lambda q: abs(q - target))
        offset = levels[nearest] * (_z_for(target) / _z_for(nearest))

    # A lead time the backtest did not reach. This used to reuse the longest
    # measured horizon's offset unchanged — as if no uncertainty accumulated
    # after it, which no demand process does: a 60-day importer was cushioned
    # for 30 days of error. Cumulative error grows at LEAST like sqrt(L)
    # (exactly so for independent errors, faster for persistent ones), so
    # sqrt(wanted / key) is the conservative extension, never an inflation.
    if key != wanted:
        offset *= math.sqrt(wanted / key)
    return max(0.0, offset)


def _calc_recommended(
    current_stock: float,
    avg_daily: float,
    avg_std: float,
    lead_time: int,
    moq: float,
    service_level: float = 0.95,
    risk: Optional[dict] = None,
    risk_scale: float = 1.0,
    incoming: float = 0.0,
    lead_time_std: float = 0.0,
    review_period: float = 0.0,
) -> float:
    """How much to order, against the INVENTORY POSITION rather than the shelf.

    `incoming` is what is already on its way and not yet received: purchase
    orders the buyer has sent, and stock transferred from another warehouse of
    theirs. Without it the buyer was told to order the same units again every
    day until they physically arrived — and in a multi-warehouse tenant a single
    internal move produced TWO of those, because the origin loses the stock at
    send time and the destination does not gain it until reception.

    Measured before this: 200 units sent San José -> Cartago, and one second
    later StockAI asked for 250 more at the origin and 90 more at the destination,
    for a company that already owned 430.

    Defaults to 0.0 so every caller that has nothing on order behaves exactly as
    before.

    `lead_time_std` is the supplier's lead-time standard deviation, ALREADY in
    the active period's units (see `_lead_time_in_periods` — the same
    conversion `lead_time` itself went through). Defaults to 0.0, which makes
    `_safety_stock`'s combined-variance term vanish and reproduces exactly
    today's number — see `_safety_stock` for why.

    `review_period` is how often this buyer actually orders from this
    supplier, ALREADY in the active period's units — same conversion, same
    reason. stability.md 17/19.3: an order-up-to level sized on the lead time
    alone is exactly one review period short, because the position is back at
    the reorder point the moment a shipment lands and the next chance to react
    is not until the next review. The PROTECTION INTERVAL the order has to
    last through is therefore `lead_time + review_period`, not `lead_time`
    alone — it replaces `lead_time` in BOTH the demand term below and the
    safety-stock term `_safety_stock` computes, so the cushion also covers the
    longer wait, not only the bigger mean. Defaults to 0.0, under which
    `protection_interval == lead_time` and this reproduces exactly today's
    number — see `_resolve_review_period_days` for why 0.0 is what "no
    supplier" and "no declared cadence" both resolve to.
    """
    protection_interval = lead_time + max(0.0, review_period)
    lead_time_demand = avg_daily * protection_interval
    safety_stock = _safety_stock(
        avg_std, protection_interval, service_level, risk, risk_scale,
        avg_daily=avg_daily, lead_time_std=lead_time_std,
    )
    raw = max(0.0, lead_time_demand + safety_stock - current_stock - max(0.0, incoming))
    if moq and moq > 0 and raw > 0:
        # MOQ is a MINIMUM ORDER QUANTITY — a floor under the order — not a pack
        # multiple. It used to be applied as `ceil(raw/moq) * moq`, which is the
        # arithmetic for "the supplier only ships in boxes of this size", a
        # concept this product does not have a field for and never asked the
        # user about.
        #
        # The gap between the two is not cosmetic. Needing 520 with a MOQ of 500
        # asked for 1000 — a 92% overshoot, with a button to turn it into a
        # purchase order. Every SKU whose need lands just past a multiple was
        # over-ordered by up to a full MOQ, on money the buyer does not get back
        # until the units sell.
        #
        # `raw > 0` keeps "nothing to order" meaning nothing: the old ceil
        # returned 0 for a raw of 0 and this must too, or a well-stocked SKU
        # would be handed a full minimum order out of nowhere.
        #
        # The ceil to whole units is kept — you cannot buy 96.15 units, and the
        # old expression rounded there as a side effect of the MOQ arithmetic.
        # Dropping it here would have started emitting fractional order lines.
        raw = max(float(math.ceil(raw)), float(moq))
    return float(round(raw, 2))


def _safety_stock(
    avg_std: float, lead_time: float, service_level: float,
    risk: Optional[dict] = None, risk_scale: float = 1.0,
    avg_daily: float = 0.0, lead_time_std: float = 0.0,
) -> float:
    """The cushion above lead-time demand: measured when we have it, modelled
    when we do not. One function so the recommendation, the reorder point and
    the explanation breakdown can never disagree about the number.

    Two independent sources of variance feed the cushion — demand is
    uncertain even over a FIXED lead time, and the lead time itself is
    uncertain even for AVERAGE demand — and they combine in quadrature
    (variances add, standard deviations do not):

        sigma_LT = sqrt(L * avg_std^2 + avg_daily^2 * lead_time_std^2)
        safety_stock = z * sigma_LT

    which is algebraically `sqrt((z*avg_std*sqrt(L))^2 + (z*avg_daily*lead_time_std)^2)`
    — the classical term this function has always returned, plus a second
    term for a supplier who does not always take exactly `lead_time`.
    `lead_time_std=0.0` (no supplier, or one with neither a learned nor a
    configured spread — see `_resolve_lead_time_std`) zeroes that second term
    and reproduces the old number exactly.

    `_measured_safety_stock` replaces the DEMAND term only (it already covers
    demand uncertainty over the lead time, measured rather than assumed) —
    the lead-time term is still added in quadrature on top of it, not
    discarded, because the two protect against different things.

    `risk_scale` splits a whole-SKU band across warehouses, mirroring how the
    per-warehouse view already splits `avg_std` (and `avg_daily`, for this
    term) by that warehouse's share. The lead-time term is built from
    already-scaled inputs at the per-warehouse call site, so it needs no
    separate scaling of its own — only the measured (whole-SKU) band does.
    """
    z = _z_for(service_level)
    lead_time_term = z * avg_daily * max(0.0, lead_time_std)
    measured = _measured_safety_stock(risk, lead_time, service_level)
    if measured is not None:
        demand_term = measured * float(risk_scale)
    else:
        demand_term = z * avg_std * math.sqrt(lead_time)
    return math.sqrt(demand_term ** 2 + lead_time_term ** 2)


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


def get_learned_lead_time_stds(tenant_id: str) -> dict[str, float]:
    """
    Standard deviation of REAL lead times per supplier, learned from the same
    `supplier_lead_time_obs` receptions `get_learned_lead_times` averages —
    one more aggregate (STDDEV_SAMP instead of AVG) over a query that already
    runs, gated on the SAME MIN_LEAD_TIME_OBSERVATIONS threshold so a supplier
    without enough receptions to trust its learned MEAN lead time does not get
    a learned SPREAD either. That spread is what `_safety_stock` needs for the
    lead-time-variance term of the combined-variance formula; the mean alone
    (what this function's sibling returns) only feeds `lead_time_days` demand.

    Keys are lower-cased supplier names, like `get_learned_lead_times`. Absent
    here means "not enough evidence yet" — the caller falls back to the
    supplier's configured `lead_time_std`.
    """
    rows = query(
        """SELECT LOWER(supplier) AS supplier,
                  STDDEV_SAMP(lead_time_days) AS std_days
           FROM supplier_lead_time_obs
           WHERE tenant_id = %s
           GROUP BY LOWER(supplier)
           HAVING COUNT(*) >= %s""",
        (tenant_id, MIN_LEAD_TIME_OBSERVATIONS),
    )
    return {
        r["supplier"]: float(r["std_days"])
        for r in rows
        if r.get("supplier") and r.get("std_days") is not None
    }


def _resolve_lead_time_std(
    supplier: Optional[str],
    learned_stds: dict[str, float],
    configured_stds: dict[str, float],
) -> float:
    """The lead-time standard deviation (DAYS) the safety-stock formula uses
    for a SKU's supplier, in priority order:

      (a) the standard deviation of that supplier's real receptions, once
          MIN_LEAD_TIME_OBSERVATIONS of them exist (`learned_stds` — already
          gated on that threshold by `get_learned_lead_time_stds`, exactly
          like `learned_stds` gates the learned MEAN);
      (b) the `lead_time_std` configured on the supplier record
          (`configured_stds`, from `supplier_service.get_lead_time_std_map`);
      (c) 0.0 when the SKU has no supplier at all, or the name matches
          neither map — which collapses the combined-variance formula in
          `_safety_stock` back to exactly today's `z * avg_std * sqrt(L)`.
    """
    if not supplier:
        return 0.0
    key = supplier.strip().lower()
    learned = learned_stds.get(key)
    if learned is not None:
        return max(0.0, learned)
    configured = configured_stds.get(key)
    if configured is not None:
        return max(0.0, configured)
    return 0.0


def _resolve_review_period_days(
    supplier: Optional[str],
    review_period_map: dict[str, float],
) -> float:
    """The order cadence (DAYS) this buyer actually uses with a SKU's
    supplier — how long the order placed today has to last past the lead
    time, because the next chance to react is not until the next review.

    Unlike the lead time there is nothing to learn here: a reception tells you
    how long a shipment took, never how often you chose to ask for one. So
    there is exactly one source, `suppliers.review_period_days`
    (`supplier_service.get_review_period_map`), and no supplier or an unset
    value (the column's own DEFAULT 0) both mean "no declared cadence" —
    which is what makes the protection interval collapse back to the lead
    time alone, reproducing today's numbers exactly.
    """
    if not supplier:
        return 0.0
    return max(0.0, review_period_map.get(supplier.strip().lower(), 0.0))


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
    review_period_days: float = 0.0,
    period: str = "daily",
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

    `review_period_days` (stability.md 17/19.3) is the order cadence this
    buyer declared for the supplier, in DAYS. 0.0 (no cadence declared, the
    overwhelming majority of tenants today) keeps the exact code and sentence
    this function has always returned — a screen that has never heard of a
    review period must not change. A positive value switches to
    `inventory_explain_reorder_review` and names the protection interval —
    "covers you until your next order, expected in N days" — because a
    quantity that grew for a new reason and says nothing about it is worse
    than one that did not grow: CLAUDE.md's silent-failures lens applies to a
    NUMBER, not only to a missing send. A client that only knows the older
    code (frontend not yet updated for this — see the module's own comment on
    graceful degradation) falls back to this English `text` rather than
    rendering nothing.
    """
    scope = lead_time_rule_scope or "supplier"

    # The sentence says "you sell X a day, so it lasts you N days". On a weekly
    # or monthly tenant `daily_demand` is per WEEK/MONTH and `coverage_days` is
    # in weeks/months, so a weekly buyer read "you sell 70 a day, it lasts you
    # 3 days" about 10 a day and 3 weeks (math audit 2026-10-01). Converted to
    # calendar days here, the unit the sentence names and the lead time is in.
    dpp = _days_per_period(period)
    daily_demand = float(daily_demand) / dpp
    if coverage_days is not None:
        coverage_days = float(coverage_days) * dpp

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

    review_period_days = float(review_period_days or 0.0)
    if review_period_days <= 0:
        # No declared cadence: byte-identical to before this feature existed.
        return {"code": "inventory_explain_reorder", "params": params, "text": text}

    protection_interval = lead_time + review_period_days
    params["review_period_days"] = round(review_period_days, 2)
    params["protection_interval_days"] = round(protection_interval, 2)
    text += (
        f" That quantity is sized to last until your NEXT order, not just until this "
        f"one arrives: you order from this supplier roughly every "
        f"{_english_days(review_period_days)}, so it has to cover "
        f"{_english_days(protection_interval)} of demand in total "
        f"({_english_days(lead_time)} for this shipment to arrive, plus "
        f"{_english_days(review_period_days)} before you place the next one)."
    )
    return {"code": "inventory_explain_reorder_review", "params": params, "text": text}


def _aggregate_stock_rows_by_sku(
    stock_rows: list[dict], default_warehouse: str | None = None,
) -> dict[str, dict]:
    """
    Collapse per-warehouse inventory_stock rows into one summary row per SKU:
    current_stock is SUMMED across warehouses (true total stock the tenant
    holds); every other field (lead_time_days, unit_cost, supplier,
    etc.) is taken from a single deterministic representative row (the
    default warehouse if present, else the casefolded-alphabetically-first
    warehouse — warehouse_service.name_precedence_key) — those are per-SKU
    catalog attributes, not per-warehouse quantities, so picking one is
    correct as long as it's deterministic.

    `default_warehouse` is the tenant's ANCHORED default — `warehouses.is_default`
    — and when given it wins over the name ordering. It has to, because
    `warehouse_service.get_demand_shares` already resolves the default that way,
    and the two answers were not the same one.

    Concretely: a tenant whose first warehouse was "Bodega Sur" carries
    is_default there, while the name key puts DEFAULT_WAREHOUSE ("principal")
    first. So 100% of a SKU's demand was attributed to Bodega Sur while the
    aggregated row took that SKU's cost, lead time, MOQ and supplier — and with
    them the headline "valor en bodega" — from principal. Two warehouses, one
    row, and no way to tell from the screen which one it was describing.
    `get_demand_shares`' own comment claimed the question was "answered
    identically everywhere"; this parameter is what makes that true.

    Omitting it falls back to the name key alone, which is what callers holding
    nothing but stock rows can do — that is why it is optional rather than
    required, and why the aggregation itself still issues no query.
    """
    from backend.inventory.warehouse_service import name_precedence_key

    def _key(row: dict) -> tuple:
        wh = row.get("warehouse")
        # The anchored default sorts ahead of everything; the rest keep the
        # shared name ordering so ties stay deterministic.
        return (wh != default_warehouse,) + name_precedence_key(wh)

    by_sku: dict[str, list[dict]] = {}
    for r in stock_rows:
        by_sku.setdefault(r["sku"], []).append(r)

    result: dict[str, dict] = {}
    for sku, rows in by_sku.items():
        rows_sorted = sorted(rows, key=_key)
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
    incoming_qty: Optional[dict] = None,
    period: str = "daily",
    signal_threshold_patch: Optional[tuple] = None,
) -> list[dict]:
    """
    Implementation of get_inventory_status. The keyword-only args accept
    preloaded data (raw get_forecasts blob, list_stock rows,
    get_learned_lead_times map) so run_daily_inventory_alerts can fetch each
    ONCE per tenant and share them with get_inventory_status_by_warehouse
    instead of double-fetching; None means fetch here as always. Inputs are
    never mutated (rollup_by_sku copies).

    `signal_threshold_patch` = (scope_type, scope_value, triple-or-None) is the
    settings preview asking "what would the semáforo say if these multipliers
    were saved". It is applied to the rule index the SAME resolver reads, and a
    patched pass is never written to the recommendation log — it describes a
    hypothetical, not what the tenant was told.
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
    # And which model actually won for each SKU, so the recommendation is
    # computed from that one instead of the mean of every model trained.
    best_model: dict[str, str] = {}
    # Measured cumulative demand uncertainty per SKU, when the engine produced
    # it. Absent for older sessions and for champions that ran no rolling-origin
    # backtest — the safety stock falls back to the classical formula.
    demand_risk: dict[str, dict] = {}
    service_level_caveats: dict[str, str] = {}
    try:
        result = session_store.get_training_result(tenant_id, session_id) or {}
        service_level_caveats = _service_level_caveats(result)
        quality: dict = result.get("data_quality") or {}
        for sku_key, q in quality.items():
            if isinstance(q, dict):
                cv_by_sku[str(sku_key)] = q.get("cv")
        best_model = best_model_by_sku((result.get("metrics") or {}).get("rows") or [])
        demand_risk = {
            str(k): v for k, v in (result.get("demand_risk") or {}).items()
            if isinstance(v, dict)
        }
    except Exception as e:
        log.debug("cv_by_sku lookup failed for session=%s: %s", session_id, e)

    if stock_rows is None:
        stock_rows = list_stock(tenant_id)
    # One query for the whole request, not one per row: which warehouse
    # represents a SKU must be the same warehouse that owns its demand (see
    # _aggregate_stock_rows_by_sku).
    from backend.inventory import warehouse_service as _wh
    stock_map = _aggregate_stock_rows_by_sku(
        stock_rows, _wh.get_default_warehouse_name(tenant_id),
    )

    # What is already on its way: open purchase orders and transfers in
    # transit (see get_incoming_detail for the one rule). One query pair for
    # the whole tenant, never inside the SKU loop. The per-order breakdown is
    # only known when we load it here; a caller that preloaded the totals (the
    # alert loop) prints no references and loses nothing it would show.
    incoming_sources: dict = {}
    if incoming_qty is None:
        _incoming_detail = get_incoming_detail(tenant_id)
        incoming_qty = sum_incoming(_incoming_detail)
        incoming_sources = incoming_sources_by_key(_incoming_detail)

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

    # Lead-time VARIABILITY, for the safety-stock formula's lead-time-variance
    # term (stability.md 17a): the standard deviation of the same receptions
    # `learned_lead_times` averages, and — for suppliers thin on receptions —
    # the `lead_time_std` configured on the supplier record. Two more
    # tenant-wide queries, never per SKU.
    learned_lead_time_stds = get_learned_lead_time_stds(tenant_id)
    try:
        configured_lead_time_stds = _sup_svc.get_lead_time_std_map(tenant_id)
    except Exception as e:
        log.debug("configured lead-time std lookup failed tenant=%s: %s", tenant_id, e)
        configured_lead_time_stds = {}

    # Order cadence per supplier (stability.md 17/19.3): how often this buyer
    # actually places an order with this supplier, which the protection
    # interval needs alongside the lead time (see `_calc_recommended`). One
    # more tenant-wide query, never per SKU; absent/failed means every SKU
    # resolves review_period=0, i.e. today's arithmetic.
    try:
        review_period_map = _sup_svc.get_review_period_map(tenant_id)
    except Exception as e:
        log.debug("review period map lookup failed tenant=%s: %s", tenant_id, e)
        review_period_map = {}

    # Supplier/category/global planning rules, one query for the whole tenant.
    # A distributor configures 12 suppliers, not 2.000 SKUs — this is where that
    # configuration enters the recommendation.
    rule_index = _sd_svc.build_rule_index(tenant_id)
    if signal_threshold_patch is not None:
        rule_index = _sig_th.patch_rule_index(rule_index, *signal_threshold_patch)

    # Declared events (stability.md 19.5): a saved "Semana Santa, x1.8,
    # 24th-31st" must reach the decision itself, not just the what-if
    # simulator. One tenant-wide query for the events plus one per active
    # event for its overrides — never per SKU, same discipline as every
    # other tenant-wide map above.
    today = date.today()
    active_events = _active_events_window(tenant_id, today)
    overrides_by_event: dict[str, dict] = {
        ev["id"]: _index_overrides(get_event_multipliers(tenant_id, ev["id"]))
        for ev in active_events
    }
    # Manual forecast adjustments ("+15%, promotion", by who): one query for the
    # whole tenant, applied beside the events below and always named on the row.
    from backend.inventory import forecast_adjustment_service as _fa_svc
    adjustments_by_sku = _fa_svc.active_by_sku(tenant_id, session_id, today)

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
        adjustments_applied: list[dict] = []

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
        # The semáforo's lead-time multipliers for this SKU: supplier >
        # category > tenant > defaults, resolved as one triple.
        sku_thresholds = _sig_th.resolve_signal_thresholds(
            rule_index, supplier=supplier, category=category,
        )

        # Company-wide for this SKU: the aggregated row sums every warehouse's
        # stock, so it must sum every warehouse's incoming too. Hoisted above the
        # branch so a row without a forecast still reports what is on its way.
        sku_incoming = sum(
            q for (i_sku, _wh), q in incoming_qty.items() if i_sku == sku)

        if has_forecast and has_stock:
            # Per-period demand: average over as many forecast buckets as the
            # lead time spans in periods, and judge the signal against the lead
            # time expressed in the same period. For daily all three helpers are
            # the identity, so this path is byte-identical to before Phase C.
            lt_periods = _lead_time_in_periods(lead_time, period)
            steps = _steps_for_lead_time(lead_time, period)
            avg_daily, avg_std = _avg_daily_forecast(
                model_forecasts, steps, best_model.get(sku)
            )
            # Declared events (stability.md 19.5): when the lead-time window
            # starting TODAY overlaps a saved event, the demand that drives
            # the reorder point and the recommended quantity carries that
            # event's multiplier — blended for however much of the window
            # the event actually covers (see `_event_demand_multiplier`).
            # `avg_daily` itself stays the plain forecast (what the model
            # actually predicts, shown as "daily_demand"); `avg_daily_eff` is
            # what plans against it. Only the MEAN demand is scaled — the
            # model's own measured spread (avg_std / the demand_risk band)
            # describes ordinary conditions and scaling it would invent data
            # this product has no basis for; the lead-time-variance term
            # still grows with it because that term is already `z * avg_daily
            # * lead_time_std`, proportional to demand by construction.
            event_mult, events_applied = _event_demand_multiplier(
                {"sku": sku, "family": stock.get("family") if stock else None,
                 "category": category},
                today, lead_time, active_events, overrides_by_event,
            ) if active_events else (1.0, [])
            # A person's adjustment of this product's forecast (who/why on the
            # row): same blending over the lead-time window as an event.
            adj_mult, adjustments_applied = _fa_svc.demand_multiplier(
                adjustments_by_sku.get(sku), today, lead_time)
            avg_daily_eff = avg_daily * event_mult * adj_mult
            coverage_days = current_stock / avg_daily_eff if avg_daily_eff > 0 else 9999.0
            # The measured band belongs to ONE model's forecast. Pairing it with
            # a different model's point forecast would mix a global model's
            # error distribution with, say, prophet's numbers — a plausible
            # figure describing nothing.
            sku_risk = demand_risk.get(sku)
            if sku_risk and sku_risk.get("model") != best_model.get(sku):
                sku_risk = None
            # Lead-time variance term (stability.md 17a): DAYS, resolved per
            # supplier, then converted into the same period units as lt_periods
            # — the exact conversion `lead_time` itself already went through.
            lt_std_days = _resolve_lead_time_std(
                supplier, learned_lead_time_stds, configured_lead_time_stds,
            )
            lt_std_periods = _lead_time_in_periods(lt_std_days, period)
            # Order cadence (stability.md 17/19.3): DAYS, resolved per
            # supplier, then into this period's units — the same conversion
            # the lead time itself and its std already went through.
            review_period_days = _resolve_review_period_days(supplier, review_period_map)
            review_periods = _lead_time_in_periods(review_period_days, period)
            # The PROTECTION INTERVAL an order has to last through: the lead
            # time plus the review period. `review_periods` is 0.0 for any
            # supplier with no declared cadence, so this equals `lt_periods`
            # for every tenant who has not set one — see `_calc_recommended`.
            protection_interval = lt_periods + review_periods
            # Reorder point ahead of the signal (stability.md 17c): the signal's
            # ordering boundary IS the reorder point, so it must exist before
            # `_calc_signal` is called, not after.
            _demand_lt  = round(avg_daily_eff * protection_interval, 2)
            _safety      = round(
                _safety_stock(
                    avg_std, protection_interval, sku_service_level, sku_risk,
                    avg_daily=avg_daily_eff, lead_time_std=lt_std_periods,
                ), 2
            )
            reorder_point = round(_demand_lt + _safety, 2)
            reorder_point_days = reorder_point / avg_daily_eff if avg_daily_eff > 0 else 9999.0
            # `_calc_signal`'s own `lead_time` argument stays the PLAIN lead
            # time (not the protection interval): PEDIR_YA keeps meaning "less
            # than half a LEAD TIME of cover" regardless of order cadence, and
            # that invariant (reorder_point_days >= lead_time > 0.5*lead_time)
            # only strengthens once the reorder point also carries the review
            # period — see `_calc_signal`'s docstring.
            signal = _calc_signal(coverage_days, lt_periods, reorder_point_days,
                                  sku_thresholds)
            # Math audit 2026-10-01: no stock and no forecast demand is an
            # empty shelf nobody is selling from, not overstock. The 9999
            # coverage sentinel means "dead SKU WITH stock"; without stock there
            # is nothing to order and nothing tied up, so the signal is OK.
            if avg_daily_eff <= 0 and current_stock <= 0:
                signal = "OK"
            recommended = _calc_recommended(
                current_stock, avg_daily_eff, avg_std, lt_periods, moq,
                sku_service_level, risk=sku_risk, incoming=sku_incoming,
                lead_time_std=lt_std_periods, review_period=review_periods,
            )
            recommended = _gate_recommended_by_signal(signal, recommended)
            inventory_value = (
                round(current_stock * float(stock["unit_cost"]), 2)
                if stock.get("unit_cost") is not None else None
            )
            # The breakdown is a sum the buyer can redo by hand:
            #   daily demand x protection days = LT demand; + safety - stock
            #   - incoming = before rounding. Three things kept it from adding
            #   up (math audit 2026-10-01): `daily_demand` was per PERIOD on a
            #   weekly tenant (70/"day" x 14 days = "140"); it was the plain
            #   forecast while LT demand used the event-adjusted rate; and the
            #   units already on their way were subtracted from `final_qty`
            #   but missing from the steps, so "before rounding 150" was
            #   followed by an order of 50.
            _antes_moq   = round(max(0.0, _demand_lt + _safety - current_stock
                                     - max(0.0, sku_incoming)), 2)
            calc_explanation = {
                "daily_demand":    round(avg_daily_eff / _days_per_period(period), 2),
                "incoming":        round(max(0.0, float(sku_incoming)), 2),
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
                # Order cadence (stability.md 17/19.3), in DAYS (not periods —
                # this is what a date is built from). 0 = no declared cadence;
                # `lead_time_days + review_period_days` is the protection
                # interval this quantity was actually sized to cover, which is
                # what "Ver por qué" has to name or the number just grows with
                # nothing explaining why.
                "review_period_days":      round(review_period_days, 2),
                "protection_interval_days": round(lead_time + review_period_days, 2),
                # Which declared event(s) moved this number and by how much —
                # empty when none apply. A number that silently changed is
                # worse than one that did not change at all (CLAUDE.md /
                # silent-failures): this is what lets "Ver por qué" name the
                # event instead of leaving the buyer to notice the quantity
                # moved on its own.
                "events_applied": events_applied,
                # Manual forecast adjustments that moved this number: who, by
                # how much, why. Empty when none apply.
                "adjustments_applied": adjustments_applied,
            }
            if recommended <= 0:
                # Enough stock: keep the numbers (the what-if simulator needs
                # them) but flag it so the tooltip shows "no ordering needed".
                calc_explanation["suficiente"] = True

            # Reorder point: the stock level at which an order must be placed so
            # the shipment arrives before the shelf empties (lead-time demand
            # plus the safety cushion). Computed earlier now, ahead of the
            # signal — see the comment above `_demand_lt`.
            explanation_obj = build_explanation(
                current_stock=current_stock,
                # The effective (event-adjusted) rate: `coverage_days` and
                # `reorder_point` below were both computed from it, and the
                # sentence's own arithmetic (current_stock / daily_demand ==
                # coverage_days) must hold even when an event is moving the
                # number — a mismatched sentence would look like a second bug,
                # not the one line explaining the first.
                daily_demand=avg_daily_eff,
                coverage_days=round(coverage_days, 1) if coverage_days < 9990 else None,
                lead_time=lead_time,
                lead_time_source=lead_time_source,
                reorder_point=reorder_point,
                signal=signal,
                lead_time_rule_scope=lead_time_rule_scope,
                review_period_days=review_period_days,
                period=period,
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
        # English: the frontend recognises the sentinel and renders its own label
        # (`inventory.single_series_label`); this is the fallback for a client
        # that does not.
        display_name = stock.get("display_name") if stock else None
        if sku == "__all__" and not display_name:
            display_name = "Single series (no SKU column)"

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
            # Set when this SKU's cushion was measured unable to keep that
            # service level (see _SERVICE_LEVEL_CAVEAT_BY_FLAG); None otherwise.
            "service_level_caveat": service_level_caveats.get(sku),
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
            # `_demand_lt`, not a second computation. This line used to be
            # `avg_daily * lead_time` — per-PERIOD demand multiplied by
            # CALENDAR DAYS — while the reorder point and the "cómo se calcula"
            # breakdown both used `avg_daily * lt_periods`. Both values reached
            # the same screen from the same dict: for a weekly tenant with 10
            # units/week and a 14-day lead time the "Demanda LT" column read
            # 140 and expanding that very row read 20. Factor of 7 weekly, 30
            # monthly, and the CSV export inherited the wrong one.
            "lead_time_demand":  _demand_lt if avg_daily is not None else None,
            "coverage_days":     round(coverage_days, 1) if coverage_days is not None and coverage_days < 9990 else None,
            "signal":             signal,
            # The multipliers this signal was judged by, and which rule they
            # came from (default | global | supplier | category). Shipped with
            # the row so no screen restates a threshold it could get wrong.
            "signal_thresholds":  sku_thresholds,
            "recommended_qty": recommended,
            "adjustments_applied": adjustments_applied,
            # Already on its way: open POs + transfers in transit. Exposed so
            # the UI can say "N units arriving (OC-000123)" instead of leaving
            # the buyer to wonder why the quantity dropped.
            "incoming_qty": round(float(sku_incoming), 2),
            "incoming_sources": [
                src for (i_sku, _wh), srcs in incoming_sources.items()
                if i_sku == sku for src in srcs],
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

    # Write down what we just told this tenant. Nothing else in the product
    # does: stock is snapshotted, purchase orders are logged, overstock and
    # accuracy are snapshotted — the RECOMMENDATION was not, so "what did it
    # cost me to ignore you" and "why is today's number different" were both
    # unanswerable (docs/stability.md 19, items 4 and 7).
    #
    # This function is the one chokepoint every caller funnels through, which
    # is why the recorder rides here — and also why it is guarded. Every screen
    # load, the assistant, the MCP tools and the public API all land in this
    # function, so an unguarded write would rewrite the whole catalogue's log
    # on every read. The table's natural key is one row per tenant per SKU per
    # DAY, so the first look of the day is what gets written down.
    #
    # It can never break the read: the recommendation is the product, the log
    # is a record of it.
    if signal_threshold_patch is not None:
        # A preview of unsaved multipliers: nobody was told this.
        return items
    try:
        from backend.inventory import recommendation_log
        if not recommendation_log.already_recorded(tenant_id):
            recommendation_log.record_recommendations(
                tenant_id, session_id, items, period=period)
    except Exception:
        log.exception("recommendation log: not recorded for tenant=%s", tenant_id)

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
    incoming_qty: Optional[dict] = None,
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
    from backend.inventory import supplier_service as _sup_svc
    from backend.inventory.series import stores_in, for_store, split_key

    if forecasts is None:
        forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
    if stock_rows is None:
        stock_rows = list_stock(tenant_id)
    if learned_lead_times is None:
        learned_lead_times = get_learned_lead_times(tenant_id)
    rule_index = _sd_svc.build_rule_index(tenant_id)

    # The primary-supplier map, for the same reason the aggregated view loads
    # it: a SKU with a blank `supplier` on its stock row still has a supplier
    # configured under /proveedores, and that name is what every supplier-scoped
    # rule and the learned lead time are keyed on. Without it these rows
    # resolved `supplier = None` and silently lost the learned lead time, the
    # supplier rule for lead_time_days, moq and service_level — so one SKU in
    # one warehouse could read PEDIR_YA on the "Todas" tab and PEDIR_PRONTO on
    # the warehouse tab of the same page.
    try:
        primary_suppliers = _sup_svc.get_primary_suppliers_map(tenant_id)
    except Exception as e:
        log.debug("primary supplier map lookup failed tenant=%s: %s", tenant_id, e)
        primary_suppliers = {}

    # Same lead-time-variability maps as the aggregated view (stability.md
    # 17a) — learned spread first, configured `lead_time_std` fallback. Was
    # previously reached through `_sup_svc` with no local import in THIS
    # function, so `primary_suppliers` above silently fell back to `{}` on
    # every call (the bare NameError was swallowed by the `except Exception`
    # around it). Fixed by the import added above; this map needs that same
    # name.
    learned_lead_time_stds = get_learned_lead_time_stds(tenant_id)
    try:
        configured_lead_time_stds = _sup_svc.get_lead_time_std_map(tenant_id)
    except Exception as e:
        log.debug("configured lead-time std lookup failed tenant=%s: %s", tenant_id, e)
        configured_lead_time_stds = {}

    # Order cadence per supplier (stability.md 17/19.3), same map the
    # aggregated view loads — see `_compute_inventory_status`.
    try:
        review_period_map = _sup_svc.get_review_period_map(tenant_id)
    except Exception as e:
        log.debug("review period map lookup failed tenant=%s: %s", tenant_id, e)
        review_period_map = {}

    # Same best-model-per-SKU selection as the aggregated view. These rows must
    # not disagree with it: a warehouse row and the tenant total for the same
    # SKU would otherwise be computed from different models.
    best_model: dict[str, str] = {}
    demand_risk: dict[str, dict] = {}
    service_level_caveats: dict[str, str] = {}
    try:
        _res = session_store.get_training_result(tenant_id, session_id) or {}
        service_level_caveats = _service_level_caveats(_res)
        best_model = best_model_by_sku((_res.get("metrics") or {}).get("rows") or [])
        demand_risk = {
            str(k): v for k, v in (_res.get("demand_risk") or {}).items()
            if isinstance(v, dict)
        }
    except Exception as e:
        log.debug("best_model lookup failed for session=%s: %s", session_id, e)

    incoming_sources: dict = {}
    if incoming_qty is None:
        _incoming_detail = get_incoming_detail(tenant_id)
        incoming_qty = sum_incoming(_incoming_detail)
        incoming_sources = incoming_sources_by_key(_incoming_detail)

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

    # Does this tenant actually keep stock per warehouse, or does every unit it
    # has recorded sit in ONE location while its demand is split across several?
    #
    # The second shape is what an ERP sync produces today: `fetch_stock` in both
    # providers hardcodes `warehouse="principal"` while `fetch_sales` reads the
    # real branch off each invoice (stability 11.5). Every branch then has
    # demand and no stock row, and `current_stock or 0.0` turned "we were never
    # told" into "there are none" — PEDIR_YA at full reorder quantity for the
    # entire catalogue at every branch, with the goods sitting in principal.
    #
    # A missing row is not a zero, and this is where the product already knows
    # how to say so: SIN_DATOS. Scoped deliberately to the one-location case,
    # because a tenant who DOES maintain stock per warehouse means it when a
    # pair has no row.
    stocked_warehouses = {wh for (_s, wh) in stock_by_pair}
    stock_is_single_location = len(stocked_warehouses) == 1 and len(warehouses) > 1

    # Declared events (stability.md 19.5): the SAME rule as the aggregated
    # view (_compute_inventory_status), reusing its own helpers rather than
    # a second implementation of "does an event overlap this decision
    # window" — see the module comments above _active_events_window for why
    # that duplication is exactly how the two views drifted apart before.
    # Tenant-wide, fetched ONCE here — never per (sku, warehouse) row.
    today = date.today()
    active_events = _active_events_window(tenant_id, today)
    overrides_by_event: dict[str, dict] = {
        ev["id"]: _index_overrides(get_event_multipliers(tenant_id, ev["id"]))
        for ev in active_events
    }
    # Manual forecast adjustments ("+15%, promotion", by who): one query for the
    # whole tenant, applied beside the events below and always named on the row.
    from backend.inventory import forecast_adjustment_service as _fa_svc
    adjustments_by_sku = _fa_svc.active_by_sku(tenant_id, session_id, today)

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

            # Identical resolution to the aggregated view (see the primary map
            # above): stock row first, configured primary supplier second.
            primary  = primary_suppliers.get(sku) or {}
            supplier = (stock.get("supplier") if stock else None) or primary.get("supplier_name")
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
            # "Nobody ever recorded stock for this SKU in this warehouse", as
            # opposed to "there are none". See stock_is_single_location above.
            stock_unknown_here = stock is None and stock_is_single_location
            _moq_val, _, _ = _sd_svc.resolve_field(
                "moq", stock, rule_index, supplier=supplier, category=category)
            moq = float(_moq_val if _moq_val is not None else DEFAULT_MOQ)
            # Same resolver as the aggregated view — one source of truth for
            # the semáforo's multipliers (stability.md 3.5's lesson).
            sku_thresholds = _sig_th.resolve_signal_thresholds(
                rule_index, supplier=supplier, category=category)
            # Hoisted above the branch: a row with no demand of its own still
            # has units on the way, and the buyer needs to see them before they
            # order more into a warehouse that already has a truck coming.
            wh_incoming = incoming_qty.get((sku, wh), 0.0)

            if model_forecasts and share > 0.0 and not stock_unknown_here:
                _sl_val, sl_source, _ = _sd_svc.resolve_field(
                    "service_level", stock, rule_index, supplier=supplier, category=category)
                sku_service_level = (
                    float(service_level) if sl_source == SOURCE_DEFAULT else float(_sl_val))
                # Per-period demand + period lead time (identity for daily).
                lt_periods = _lead_time_in_periods(lead_time, period)
                steps = _steps_for_lead_time(lead_time, period)
                avg_daily, avg_std = _avg_daily_forecast(
                    model_forecasts, steps, best_model.get(sku)
                )
                avg_daily *= share
                avg_std *= share
                # Declared events (stability.md 19.5): applied HERE, AFTER
                # the share/store split, not before — reusing the aggregate
                # view's own helpers (_active_events_window,
                # _event_demand_multiplier), never a second implementation of
                # the overlap/blend math.
                #
                # Why after the split rather than before: multiplying a
                # whole-SKU total by a scalar and then splitting it equals
                # splitting first and multiplying each part by the same
                # scalar — the two orders are mathematically identical AS
                # LONG AS every warehouse uses the same multiplier. They
                # don't necessarily: the multiplier depends on `lead_time`
                # (how much of the event window falls inside the decision
                # window), and lead_time is resolved PER WAREHOUSE here — a
                # warehouse can carry its own stock row's supplier, and thus
                # its own learned/configured lead time, independent of the
                # aggregated row's. Applying the multiplier post-split, with
                # THIS row's own already-resolved `lead_time`, is what keeps
                # this row's number honest for the warehouse it actually
                # describes.
                #
                # Consequence for whether the per-warehouse rows sum exactly
                # to the aggregate: when every warehouse resolves the SAME
                # lead time as the aggregated row (the common case — one
                # supplier per SKU), the multiplier is identical everywhere,
                # it factors out of the sum, and
                # sum(avg_daily_wh) * mult == mult * sum(avg_daily_wh) ==
                # the aggregate's avg_daily_eff exactly. When warehouses
                # disagree on supplier/lead time, each row's event-window
                # overlap can differ and the rows sum only approximately —
                # that is a pre-existing property of per-warehouse lead-time
                # resolution (see the "which is the default warehouse"
                # comments elsewhere in this file), not something this
                # change introduces.
                #
                # Only the MEAN demand is scaled, matching the aggregate:
                # avg_std (the model's own measured spread) is left alone —
                # scaling it would invent data this product has no basis
                # for — while the lead-time-variance term below still grows
                # with the event because it is already `z * avg_daily *
                # lead_time_std`, proportional to demand by construction.
                event_mult, events_applied = _event_demand_multiplier(
                    {"sku": sku, "family": stock.get("family") if stock else None,
                     "category": category},
                    today, lead_time, active_events, overrides_by_event,
                ) if active_events else (1.0, [])
                adj_mult, adjustments_applied = _fa_svc.demand_multiplier(
                    adjustments_by_sku.get(sku), today, lead_time)
                avg_daily_eff = avg_daily * event_mult * adj_mult
                sku_risk = demand_risk.get(sku)
                if sku_risk and sku_risk.get("model") != best_model.get(sku):
                    sku_risk = None
                # Same resolution as the aggregated view (stability.md 17a):
                # DAYS, per supplier, then into this period's units.
                lt_std_days = _resolve_lead_time_std(
                    supplier, learned_lead_time_stds, configured_lead_time_stds,
                )
                lt_std_periods = _lead_time_in_periods(lt_std_days, period)
                # Order cadence (stability.md 17/19.3): same resolution and
                # same period conversion as the aggregated view.
                review_period_days = _resolve_review_period_days(supplier, review_period_map)
                review_periods = _lead_time_in_periods(review_period_days, period)
                protection_interval = lt_periods + review_periods
                coverage_days = current_stock / avg_daily_eff if avg_daily_eff > 0 else 9999.0
                # Reorder point ahead of the signal (stability.md 17c): the
                # signal's ordering boundary IS the reorder point.
                reorder_point = round(
                    avg_daily_eff * protection_interval
                    + _safety_stock(avg_std, protection_interval, sku_service_level,
                                    sku_risk, share,
                                    avg_daily=avg_daily_eff, lead_time_std=lt_std_periods), 2)
                reorder_point_days = reorder_point / avg_daily_eff if avg_daily_eff > 0 else 9999.0
                # `lt_periods` (plain lead time), not the protection interval —
                # see the identical comment at the aggregated call site.
                signal = _calc_signal(coverage_days, lt_periods, reorder_point_days,
                                      sku_thresholds)
                # Empty shelf with no demand: OK, not SOBRESTOCK (see the
                # aggregated call site).
                if avg_daily_eff <= 0 and current_stock <= 0:
                    signal = "OK"
                recommended = _calc_recommended(
                    current_stock, avg_daily_eff, avg_std, lt_periods, moq,
                    sku_service_level, risk=sku_risk, risk_scale=share,
                    incoming=wh_incoming, lead_time_std=lt_std_periods,
                    review_period=review_periods)
                recommended = _gate_recommended_by_signal(signal, recommended)
            else:
                avg_daily = avg_std = None
                coverage_days = None
                signal = "SIN_DATOS"
                recommended = None
                reorder_point = None
                events_applied = []
                adjustments_applied = []

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
                "signal_thresholds": sku_thresholds,
                # Why this row has no signal, when the reason is something the
                # buyer can fix. A code, not a sentence: the frontend renders it
                # (inventory.no_stock_record_here) in the reader's language.
                "sin_datos_reason": ("stock_not_recorded_in_this_warehouse"
                                     if stock_unknown_here else None),
                # Which declared event(s) moved this row and by how much — the
                # same shape as the aggregated view's
                # `calc_explanation.events_applied` (stability.md 19.5), so
                # the "Ver por qué" panel can name the event here too, not
                # just on the aggregate row. Empty when none apply.
                "events_applied": events_applied,
                "adjustments_applied": adjustments_applied,
                "recommended_qty": recommended,
                # Already on its way: open POs + transfers in transit. Exposed so
                # the UI can say "N units arriving (OC-000123)" instead of
                # leaving the buyer to wonder why the quantity dropped.
                "incoming_qty": round(float(wh_incoming), 2),
                "incoming_sources": incoming_sources.get((sku, wh), []),
                "recommended_action": None,
                "transfer_suggestion": None,
                # Why a possible transfer LOST against buying (structured
                # {reason_code, params}; the frontend renders the sentence).
                "transfer_rejected_reason": None,
                # An OPTION, not the recommendation: a donor that can cover part
                # of the need. Set by _network_transfer_pass when the full
                # transfer was refused for being too small but its lane is still
                # sound. See its "Move what there is, buy the rest" note.
                "partial_transfer": None,
                "unit_cost": (float(stock["unit_cost"])
                              if stock and stock.get("unit_cost") is not None else None),
                "service_level_caveat": service_level_caveats.get(sku),
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
        # Whether `lane_days` and the costs below were CONFIGURED or are the
        # documented fallback for an unconfigured pair — transfer_lane_service
        # resolves those to 1 day and zero cost and calls that "deliberately
        # optimistic". The flag rode on the resolved lane and never reached the
        # UI, so a measured lane and an invented one rendered identically, and
        # the optimistic default is precisely the one that wins comparisons.
        "lane_is_default": bool(lane.get("is_default")),
    }
    if lane_days >= purchase_days:
        return False, "transfer_too_slow", params

    transfer_cost = qty * float(lane["cost_per_unit"]) + float(lane["fixed_cost"])
    unit_cost = needy.get("unit_cost")
    if unit_cost is None:
        unit_cost = donor.get("unit_cost")
    if unit_cost is None or float(unit_cost) <= 0:
        # No unit cost anywhere, so the money test never ran. The transfer is
        # still accepted — arriving sooner is a real argument on its own — but
        # under its OWN code, because the caller used to return
        # "transfer_faster_and_cheaper" here and the UI duly told the buyer the
        # move "costs less than buying". It compared nothing. `saving` stays
        # None, which the copy for this code must not print.
        return True, "transfer_faster_price_unknown", {**params, "saving": None}

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
            # Why each warehouse holding this SKU was ruled out BEFORE the lane
            # rules got a say. Without this the row just said "order" while a
            # sister warehouse visibly held hundreds of units, and
            # `transfer_rejected_reason` stayed null — the buyer could see the
            # stock and not the reason, which is the one thing this feature owes
            # them. Ordered by how much the buyer needs to hear it.
            near_miss: Optional[dict] = None
            # Best donor that can cover only PART of the need (see below).
            partial: Optional[dict] = None
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
                    # It HAS stock, it just cannot spare any: lending would push
                    # the donor under its own safety floor.
                    if near_miss is None:
                        near_miss = {
                            "reason_code": "transfer_donor_would_run_short",
                            "params": {
                                "from_warehouse": d["warehouse"],
                                "donor_stock": round(float(d["current_stock"]), 2),
                                "donor_coverage_days": (
                                    round(float(d["current_stock"]) / daily, 1)
                                    if daily > 0 else None),
                                "min_coverage_days": round(min_cov, 1),
                            },
                        }
                    continue
                # Move whole units of whatever this SKU is counted in. The
                # purchase side gets integers for free (its MOQ ceiling rounds
                # up), so the same table read "Pedir 174" next to "Transferir
                # 132.95" — and nobody moves 0.95 of a bottle. FLOOR, never
                # ceil: the donor cannot lend more than it can spare. A SKU sold
                # by weight keeps its fractions through its own moq.
                #
                # The step is ONE unit. It was the SKU's `moq`, from when MOQ was
                # read as a pack multiple; since 2026-08-12 it is the SUPPLIER'S
                # minimum order, which says nothing about moving goods between
                # two of the buyer's own warehouses. With a minimum of 500, a
                # sister warehouse able to spare 450 lent 0 and the buyer was
                # told to purchase all 520 — and a spare of 900 against a need
                # of 520 moved only 500 (math audit 2026-10-01). A fractional
                # MOQ (a SKU sold by weight) still sets a finer step.
                moq_val = float(r.get("moq") or 0)
                step = moq_val if 0 < moq_val < 1 else 1.0
                donatable = math.floor(donatable / step) * step
                if donatable <= 0:
                    continue
                after = float(d["current_stock"]) - donatable
                cov_after = after / daily if daily > 0 else 9999.0
                if cov_after < min_cov:
                    if near_miss is None:
                        near_miss = {
                            "reason_code": "transfer_donor_would_run_short",
                            "params": {
                                "from_warehouse": d["warehouse"],
                                "donor_stock": round(float(d["current_stock"]), 2),
                                "donor_coverage_days": round(cov_after, 1),
                                "min_coverage_days": round(min_cov, 1),
                            },
                        }
                    continue
                # A donation that doesn't materially cover the need never
                # replaced the order (pre-feature rule, unchanged) — but the
                # buyer is told, because "move 100 of the 174 I need" is a
                # decision they may well want to take by hand.
                if donatable < 0.8 * need:
                    near_miss = {
                        "reason_code": "transfer_donation_too_small",
                        "params": {
                            "from_warehouse": d["warehouse"],
                            "qty": round(donatable, 2),
                            "need": round(need, 2),
                        },
                    }
                    # Keep the largest one: the purchase stands, but moving part
                    # of it is a decision the buyer can take, and they can only
                    # take it if we offer it. Attached after the main loop, and
                    # only if the lane rules accept it.
                    if not partial or donatable > partial["qty"]:
                        partial = {"donor": d, "qty": donatable}
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
            # A lane verdict outranks a pre-lane filter: it describes a donor
            # that could actually have lent, which is the more useful answer.
            if rejection is not None:
                r["transfer_rejected_reason"] = rejection
            elif r["recommended_action"] == "order" and near_miss is not None:
                r["transfer_rejected_reason"] = near_miss

            # "Move what there is, buy the rest." The full transfer was refused
            # because it would not close the gap, which is the right call for a
            # RECOMMENDATION — but the units next door are real and the buyer may
            # well want them. Offered as an extra, never as the recommendation:
            # `recommended_action` stays "order" and `recommended_qty` is
            # untouched. Accepting it creates a transfer, and the purchase then
            # shrinks on its own, because in-transit stock nets out of the next
            # recommendation (see _calc_recommended's `incoming`).
            if (r["recommended_action"] == "order" and partial
                    and r["transfer_suggestion"] is None):
                lane = lane_for(lanes, partial["donor"]["warehouse"], r["warehouse"])
                accepted, reason_code, params = _evaluate_transfer_lane(
                    lane, partial["qty"], r, partial["donor"])
                if accepted:
                    r["partial_transfer"] = {
                        "from_warehouse": partial["donor"]["warehouse"],
                        "qty": round(partial["qty"], 2),
                        "remaining_qty": round(max(0.0, need - partial["qty"]), 2),
                        "lane_days": lane["lead_time_days"],
                        "reason_code": reason_code,
                        "params": params,
                    }


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


# ── Declared events reaching the standing recommendation (stability.md 19.5) ─
# Until this, inventory_events / inventory_event_multipliers only fed the
# event SIMULATOR (simulate_event_impact below): a tenant could declare
# "Semana Santa, x1.8, 24th to 31st", save it, and the semáforo asked for the
# same quantity the next day regardless. The functions below let
# _compute_inventory_status see a saved event when it overlaps the window a
# decision is being made for RIGHT NOW — the lead-time window starting today.
# A recommendation is forward-looking: an event that already ended, or one
# whose window opens after the order would already have arrived, must not
# move it.

def _active_events_window(tenant_id: str, today: date) -> list[dict]:
    """Active events whose date range has not entirely passed, fetched ONCE
    per call — not per SKU. _compute_inventory_status walks hundreds of SKUs
    and every one of them checks the same small handful of declared events,
    the same shape as the other tenant-wide maps above (learned_lead_times,
    incoming_qty, rule_index). A future event is still returned here (its
    start may or may not fall inside any given SKU's lead-time window, which
    is what `_event_window_overlap_days` decides per SKU); a past one
    (end_date < today) never is, which is what keeps a lapsed event from
    ever reaching a forward-looking decision."""
    return query(
        """SELECT * FROM inventory_events
           WHERE tenant_id = %s AND active IS TRUE AND end_date >= %s
           ORDER BY start_date""",
        (tenant_id, today),
    )


def _event_window_overlap_days(today: date, lead_time_days: float, event: dict) -> int:
    """
    Calendar-day overlap between the DECISION window — [today, today +
    lead_time_days), the days an order placed right now would actually have
    to cover — and the declared event's [start_date, end_date] (inclusive).

    Deliberately calendar arithmetic, not period arithmetic: an event is a
    fact about the CALENDAR (Semana Santa runs the 24th to the 31st
    regardless of whether this tenant plans in days, weeks or months). The
    period conversion happens one level up, when this day COUNT becomes a
    FRACTION of the lead time (see `_event_demand_multiplier`) — the same
    trap `lead_time_demand`'s own comment already documents: multiplying a
    PER-PERIOD figure by a raw CALENDAR-day count silently produces a number
    off by 7x (weekly) or 30x (monthly). Working in day counts on both sides
    of the ratio, and only ever using the RATIO downstream, sidesteps that
    the same way.

    A future event whose start sits at or beyond the end of the decision
    window (start_date >= today + lead_time_days) returns 0 — that is what
    keeps "far in the future" from moving today's decision; a past event
    never reaches this call at all (`_active_events_window` excludes it).
    """
    window_start = today
    window_end = today + timedelta(days=max(0.0, lead_time_days))  # exclusive
    ev_start = event["start_date"]
    ev_end_exclusive = event["end_date"] + timedelta(days=1)
    overlap_start = max(window_start, ev_start)
    overlap_end = min(window_end, ev_end_exclusive)
    return max(0, (overlap_end - overlap_start).days)


def _event_demand_multiplier(
    item: dict, today: date, lead_time_days: float, events: list[dict],
    overrides_by_event: dict[str, dict],
) -> tuple[float, list[dict]]:
    """
    Combined demand multiplier for THIS sku's lead-time window, blended by
    how much of that window each active event actually covers.

    Partial overlap: a 15-day lead time that covers 4 event days and 11
    ordinary ones must NOT be scaled by the full multiplier for all 15 — that
    overstates the order by treating 11 ordinary days as if they were also
    Semana Santa. `fraction = overlap_days / lead_time_days` is a plain ratio
    of calendar days on both sides, so it is correct at ANY planning grain
    without a second conversion: `lead_time_days` here is the same DAYS
    figure `_lead_time_in_periods` converts for the rest of the pipeline, not
    periods, and a fraction of days stays the same fraction regardless of
    what bucket the forecast itself is expressed in.
    `blended = 1 + fraction * (multiplier - 1)` is the multiplier that,
    applied to the WHOLE window's demand, gives the same total extra units as
    applying the real multiplier to only the overlapping days and leaving the
    rest at the baseline rate — e.g. x1.8 over 4 of 15 days blends to
    ~1.213, not 1.8.

    Multiple overlapping events compound multiplicatively (each is an
    independent fact about the calendar); in the normal case a tenant
    declares one event over any given date range, so this is a correctness
    net for an edge case, not the expected path.

    Returns (multiplier, applied) where `applied` lists one entry per event
    that actually touched this window — enough for the "Ver por qué" panel to
    name it, never a silent change to the number.
    """
    if lead_time_days <= 0:
        return 1.0, []
    combined = 1.0
    applied: list[dict] = []
    for ev in events:
        overlap_days = _event_window_overlap_days(today, lead_time_days, ev)
        if overlap_days <= 0:
            continue
        idx = overrides_by_event.get(ev["id"]) or {s: {} for s in _MULTIPLIER_SCOPES}
        sku_mult, mult_source = _resolve_multiplier(item, float(ev["multiplier"]), idx)
        fraction = min(1.0, overlap_days / float(lead_time_days))
        blended = 1.0 + fraction * (sku_mult - 1.0)
        combined *= blended
        applied.append({
            "event_id":           ev["id"],
            "event_name":         ev["name"],
            # The multiplier actually resolved for THIS sku (event-wide, or
            # its sku/family/category override) and where it came from.
            "multiplier":         sku_mult,
            "multiplier_source":  mult_source,
            "overlap_days":       overlap_days,
            "window_days":        int(math.ceil(lead_time_days)),
            # What was actually applied to the window's demand, after
            # blending for partial overlap — the number that explains why the
            # recommendation moved by less than the raw multiplier suggests.
            "blended_multiplier": round(blended, 4),
        })
    return combined, applied


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
        # 'catalog' = estimate preloaded by StockAI; 'user' = set by the
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
    period: str = "daily",
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

    # These three used to be `ValueError`s that the endpoint re-raised as
    # `HTTPException(422, detail=str(e))`, which put two problems on the wire:
    # the messages were hardcoded SPANISH inside backend logic (CLAUDE.md
    # forbids it — the backend returns a code, the frontend renders the
    # Spanish), and an unparseable date escaped as Python's own
    # "Invalid isoformat string: 'ayer'", in English, naming an internal
    # function to a distributor. Structured codes fix both at once.
    def _parse(value, field: str):
        if not isinstance(value, str):
            return value
        try:
            return _date.fromisoformat(value)
        except ValueError:
            raise AppError(
                "event_date_invalid",
                f"'{field}' must be a date written as YYYY-MM-DD.",
                status_code=422,
                params={"field": field, "value": str(value)[:32]},
            )

    start_date = _parse(start_date, "start_date")
    end_date = _parse(end_date, "end_date")
    if end_date < start_date:
        raise AppError(
            "event_end_before_start",
            "The event ends before it starts.",
            status_code=422,
            params={"start": str(start_date), "end": str(end_date)},
        )
    if multiplier <= 0:
        raise AppError(
            "event_multiplier_not_positive",
            "The multiplier must be greater than 0.",
            status_code=422,
            params={"multiplier": multiplier},
        )

    today = _date.today()
    event_days = (end_date - start_date).days + 1
    days_until_start = max(0, (start_date - today).days)

    # Per-SKU / per-category overrides, when the simulation runs off a saved event.
    override_rows = get_event_multipliers(tenant_id, event_id) if event_id else []
    idx = _index_overrides(override_rows)

    # Read at the tenant's own grain, like every screen. Without it a weekly
    # tenant's per-week demand was multiplied by the event's CALENDAR days, so
    # the simulated extra units were off by the ratio between the two.
    items = get_inventory_status(tenant_id, session_id, period=period)
    rows: list[dict] = []

    for it in items:
        daily = it.get("daily_demand")
        if not daily or daily <= 0:
            continue  # nothing to simulate without a forecast

        lead_time = int(it.get("lead_time_days") or DEFAULT_LEAD_TIME_DAYS)
        moq       = float(it.get("moq") or 1)
        stock     = it.get("current_stock")
        cost      = it.get("unit_cost")

        # Each product can carry its own multiplier.
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
            # Which multiplier applied to THIS product and why: without it
            # the row cannot be explained once overrides are in play.
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
        raise AppError(
            "calendar_country_unsupported",
            f"No calendar catalog for country '{country}'. "
            f"Available: {', '.join(cat.SUPPORTED_COUNTRIES)}",
            params={"country": country,
                    "available": ", ".join(cat.SUPPORTED_COUNTRIES)},
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

def _pdf_text(value) -> str:
    """Escape user-supplied text for reportlab's Paragraph.

    Paragraph parses its argument as XML-like markup, so a product named
    "Tuerca <M8> & arandela" or a session id with "<" made the whole
    document fail ("paraparser: syntax error") — a 500 for the one file the
    buyer forwards. Every name, note or id from data goes through here;
    only the template's own <b>/<font> tags stay as markup.
    """
    from xml.sax.saxutils import escape
    return escape(str(value))


def generate_inventory_pdf(tenant_id: str, session_id: str, service_level: float = 0.95,
                           period: str = "daily") -> bytes:
    """
    Generates a one-page executive summary PDF in Spanish.
    Returns raw bytes ready for StreamingResponse.

    `period` had no parameter at all, so this document was always computed as
    daily. It is the artifact the buyer forwards to other people — the one copy
    of these numbers that leaves the app — and for a weekly or monthly tenant it
    disagreed with every screen it was printed from. Default "daily" keeps every
    existing caller byte-identical; the endpoint resolves the real one.
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

    items = get_inventory_status(tenant_id, session_id, service_level, period)

    # Resolved once for the whole document (one DB read), then handed to every
    # amount it renders — the report is read by whoever the buyer forwards it to,
    # so it must carry the currency that company actually trades in.
    currency = _tenant_currency(tenant_id)

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
    # This document is downloaded and forwarded, so the frontend never renders
    # it: its Spanish comes from the backend copy catalog, keyed in English.
    from backend.notifications.locale import render_es, render_date, coverage_short
    SIGNAL_LABELS = {
        "PEDIR_YA":     render_es("inventory_pdf_signal_order_now"),
        "PEDIR_PRONTO": render_es("inventory_pdf_signal_order_soon"),
        "OK":           render_es("inventory_pdf_signal_ok"),
        "SOBRESTOCK":   render_es("inventory_pdf_signal_overstock"),
        "SIN_DATOS":    render_es("inventory_pdf_signal_no_data"),
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
    today_str = render_date(date.today())
    header_data = [[
        Paragraph(f"<b>{render_es('inventory_pdf_title')}</b>", H1),
        Paragraph(f"<font color='#64748b'>"
                  f"{render_es('inventory_pdf_generated_on', date=today_str)}</font>", SMALL),
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
            (total,   render_es("inventory_pdf_kpi_total"),     "6366f1"),
            (urgent,  render_es("inventory_pdf_kpi_urgent"),    "ef4444"),
            (warning, render_es("inventory_pdf_kpi_warning"),   "f59e0b"),
            (ok,      render_es("inventory_pdf_kpi_ok"),        "22c55e"),
            (over,    render_es("inventory_pdf_kpi_overstock"), "3b82f6"),
            (money(value, currency=currency) if value else "—",
             render_es("inventory_pdf_kpi_value"), "6366f1"),
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
        story.append(Paragraph(render_es("inventory_pdf_section_action"), H2))
        tdata = [[
            Paragraph(f"<b>{render_es('inventory_pdf_col_sku')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_name')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_signal')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_stock')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_coverage')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_order')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_supplier')}</b>", CELL_BOLD),
        ]]
        row_styles = []
        for idx, item in enumerate(critical_items):
            sig_color = SIGNAL_COLORS.get(item["signal"], colors.grey)
            sig_label = _pdf_text(SIGNAL_LABELS.get(item["signal"], item["signal"]))
            tdata.append([
                Paragraph(_pdf_text(item["sku"]), CELL),
                Paragraph(_pdf_text(item.get("display_name") or "—"), CELL),
                Paragraph(sig_label, ParagraphStyle("sig", fontSize=8,
                          fontName="Helvetica-Bold", textColor=sig_color)),
                Paragraph(f"{item['current_stock']:,.0f}" if item.get("current_stock") is not None else "—", CELL),
                # In the planning period's unit: the figure is in weeks on a
                # weekly tenant, and this printed "4 días" for 4 weeks (math
                # audit 2026-10-01). The email already said "4 semanas".
                Paragraph(format_coverage(item["coverage_days"], period) if item.get("coverage_days") is not None else "—", CELL),
                Paragraph(f"<b>{item['recommended_qty']:,.0f}</b>" if item.get("recommended_qty") else "—",
                          ParagraphStyle("qty", fontSize=8, fontName="Helvetica-Bold", textColor=GREEN)),
                Paragraph(_pdf_text(item.get("supplier") or "—"), CELL),
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
        story.append(Paragraph(render_es("inventory_pdf_section_rest"), H2))
        small_data = [[
            Paragraph(f"<b>{render_es('inventory_pdf_col_sku')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_name')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_signal')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_coverage')}</b>", CELL_BOLD),
            Paragraph(f"<b>{render_es('inventory_pdf_col_abc_xyz')}</b>", CELL_BOLD),
        ]]
        for item in remaining[:30]:
            small_data.append([
                Paragraph(_pdf_text(item["sku"]), CELL),
                Paragraph(_pdf_text(item.get("display_name") or "—"), CELL),
                Paragraph(SIGNAL_LABELS.get(item["signal"], "—"),
                          ParagraphStyle("s2", fontSize=8, textColor=SIGNAL_COLORS.get(item["signal"], colors.grey))),
                Paragraph(coverage_short(item["coverage_days"], period) if item.get("coverage_days") is not None else "—", CELL),
                Paragraph(_pdf_text(item.get("abc_xyz") or "—"), CELL),
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
        render_es("inventory_pdf_footer", session=_pdf_text(session_id[:8]),
                  level=f"{service_level*100:.0f}"),
        ParagraphStyle("footer", fontSize=7, textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER),
    ))

    doc.build(story)
    return buf.getvalue()


# ── Decision Centre helpers ───────────────────────────────────────────────────

def _calc_demand_trend(tenant_id: str, sku: str, avg_daily: float, days: int = 14,
                       period: str = "daily") -> Optional[float]:
    """
    Returns % change in actual demand vs forecast.
    Positive = demand is running above forecast (risk of stockout).
    Negative = demand is below forecast (risk of overstock).
    Uses stock snapshot history to estimate actual consumption.
    Returns None if insufficient data or change is not significant (< 15%).

    `avg_daily` is the forecast per bucket of `period` (per WEEK on a weekly
    tenant), so it is converted to a per-day rate before being compared with
    a consumption measured over calendar days.

    Three things this used to get wrong (math audit 2026-10-01):

    * The expected consumption was `avg_daily * len(history)` — the number of
      SNAPSHOTS, not the days they span. Snapshots are written on every stock
      write, not once a day: four writes spread over 14 days expected 4 days
      of sales against 14 days of real consumption and reported "+250%".
    * The forecast was per period and the window in days, so every weekly
      tenant read "-86% below forecast" on every SKU.
    * Consumption was `first - last`, so a reception inside the window
      cancelled the sales against it. It is now the sum of the FALLS between
      consecutive levels; a rise is a reception or an adjustment, not demand.
    """
    if avg_daily <= 0:
        return None

    history = get_stock_history(tenant_id, sku, days=days)
    if len(history) < 4:
        return None

    try:
        first_at = datetime.fromisoformat(history[0]['date'])
        last_at = datetime.fromisoformat(history[-1]['date'])
    except (TypeError, ValueError):
        return None
    elapsed_days = (last_at - first_at).total_seconds() / 86400.0
    if elapsed_days < 1.0:
        return None

    levels = [float(h['stock']) for h in history]
    actual_depletion = sum(
        max(0.0, prev - cur) for prev, cur in zip(levels, levels[1:])
    )

    per_day = avg_daily / _days_per_period(period)
    expected_depletion = per_day * elapsed_days

    if expected_depletion <= 0:
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
    period: str = "daily",
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
        # Only reached by a caller that did not already have the status in hand.
        # The briefing (the one real caller) passes `items`, computed at the
        # tenant's grain — this path exists so a direct call cannot silently
        # compute a different one.
        items = get_inventory_status(tenant_id, session_id, service_level, period)
    if forecasts is None:
        from backend.db import session_store
        forecasts = session_store.get_forecasts(tenant_id, session_id) or {}

    # The spike has to be the one the recommendation will be computed from, so
    # this reads the same best model per SKU as the semáforo does.
    best_model: dict[str, str] = {}
    try:
        from backend.db import session_store as _ss
        _res = _ss.get_training_result(tenant_id, session_id) or {}
        best_model = best_model_by_sku((_res.get("metrics") or {}).get("rows") or [])
    except Exception as e:
        log.debug("best_model lookup failed for session=%s: %s", session_id, e)

    items_by_sku = {i["sku"]: i for i in items}
    today = _date.today()
    alerts: list[dict] = []

    for sku, model_forecasts in forecasts.items():
        item = items_by_sku.get(sku)
        if not item or not item.get("has_forecast"):
            continue

        curve = _avg_forecast_curve(model_forecasts, preferred_model=best_model.get(sku))
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


def generate_recommendations(items: list[dict], period: str = "daily",
                             currency: dict | None = None) -> list[dict]:
    """
    Generates plain-language, actionable recommendations from inventory status items.
    Each recommendation has: priority (1=critical), sku, name, rec_type, signal,
    plus two ways to say the same thing — `text_code`/`text_params` and
    `action_code`/`action_params`, which the frontend renders through the
    catalogue in the reader's language, and `text`/`action`, an English sentence
    kept only as the fallback for a frontend older than this API.

    `currency` is the tenant's currency setting, resolved once by the caller: the
    OVERSTOCK sentence quotes an amount, and that amount is pre-formatted here
    (in `text` and in `text_params['amount']`) because only the backend knows the
    tenant's setting. Omitting it renders the anchor market's colón.

    `period` (multi-period Phase C): the active planning grain. A period-trained
    session reports coverage in that grain's unit (a weekly session's
    coverage_days of 3 means 3 WEEKS), so the coverage figures travel in that
    unit and the optimal ceiling is compared in the same unit. Lead time stays in
    real calendar days — a supplier takes N days regardless of the planning
    grain, which is why `lead_days` is a separate param from `days`.
    """
    days_per_period = _days_per_period(period)
    recs: list[dict] = []

    for item in items:
        sku      = item['sku']
        name     = item.get('display_name') or sku
        signal   = item['signal']
        days     = item.get('coverage_days')
        lead     = item.get('lead_time_days', DEFAULT_LEAD_TIME_DAYS)
        qty      = item.get('recommended_qty') or 0
        # Raw, with no article and no preposition attached. Spanish needs the
        # contraction `a + el = al` and a named supplier takes no article at all,
        # so this used to be carried twice, pre-declined. Both forms belong to
        # the catalogue now: a supplier and no supplier get their own key.
        supplier = item.get('supplier')
        abc      = item.get('abc', '?')
        trend    = item.get('demand_trend_pct')
        value    = item.get('inventory_value')

        if signal == 'PEDIR_YA' and days is not None:
            recs.append({
                'priority': 1, 'sku': sku, 'name': name,
                'rec_type': 'STOCKOUT_RISK',
                'text': (
                    f"Order {name} TODAY — you have {format_coverage_en(days, period)} of stock "
                    f"and {supplier or 'the supplier'} takes {round(lead)} days to deliver. "
                    f"If you do not act today it runs out before the order arrives."
                ),
                # Raw numbers, never pre-formatted words: `format_coverage`
                # and `_format_days` emit Spanish nouns ("días", "semanas"), so
                # interpolating them would smuggle Spanish into whatever
                # language the reader chose. The frontend has coverageUnitLabel
                # for exactly this.
                'text_params': {
                    'name': name, 'days': round(days), 'lead_days': round(lead),
                    'supplier': supplier or '',
                },
                # Grammar belongs to the catalogue, not to the data: a param
                # carrying "a Acme" rendered as "Order 216 units a Acme" the
                # moment the UI was English, and an unnamed supplier fell back to
                # the Spanish words "el proveedor" inside an English sentence.
                # Each case gets its own key and the name travels raw.
                'text_code': 'STOCKOUT_RISK' if supplier else 'STOCKOUT_RISK_NO_SUPPLIER',
                'action': (f"Order {qty:.0f} units from {supplier or 'the supplier'}"
                           if qty > 0 else "Issue an urgent order"),
                'action_code': ('order_qty_from_supplier' if supplier else 'order_qty_from_generic')
                               if qty > 0 else 'order_urgent',
                'action_params': {'qty': f"{qty:.0f}", 'supplier': supplier or ''},
                'signal': signal,
            })

        elif signal == 'PEDIR_PRONTO' and days is not None:
            recs.append({
                'priority': 2, 'sku': sku, 'name': name,
                'rec_type': 'REORDER_SOON',
                'text': (
                    f"{name} has {format_coverage_en(days, period)} of coverage against a lead "
                    f"time of {round(lead)} days. Issue the order this week to keep the safety buffer."
                ),
                'text_params': {
                    'name': name, 'days': round(days), 'lead_days': round(lead),
                },
                'action': f"Order {qty:.0f} units before Friday" if qty > 0 else "Plan the order",
                'action_code': 'order_qty_by_friday' if qty > 0 else 'plan_order',
                'action_params': {'qty': f"{qty:.0f}"},
                'signal': signal,
            })

        if trend is not None:
            if trend >= 15:
                recs.append({
                    'priority': 3, 'sku': sku, 'name': name,
                    'rec_type': 'DEMAND_UP',
                    'text': (
                        f"Real demand for {name} is running {trend:.0f}% above the forecast. "
                        f"Consider raising the safety stock or bringing the next order forward."
                    ),
                    'text_params': {'name': name, 'pct': f"{trend:.0f}"},
                    'action': "Review the safety stock",
                    'action_code': 'review_safety_stock',
                    'action_params': {},
                    'signal': signal,
                })
            elif trend <= -15:
                recs.append({
                    'priority': 4, 'sku': sku, 'name': name,
                    'rec_type': 'DEMAND_DOWN',
                    'text': (
                        f"Demand for {name} is {abs(trend):.0f}% below the forecast. "
                        f"Check whether you lost a key customer or the trend really changed."
                    ),
                    'text_params': {'name': name, 'pct': f"{abs(trend):.0f}"},
                    'action': "Review with the sales team",
                    'action_code': 'review_with_sales',
                    'action_params': {},
                    'signal': signal,
                })

        if signal == 'SOBRESTOCK' and abc in ('A', 'B') and days is not None and value:
            # `days` is coverage in the active period's unit; the "óptimo" ceiling
            # is the SKU's configured overstock factor (3 by default) times the
            # lead time expressed in that SAME unit, so the excess is a
            # coherent period figure (mixing weeks against day-count lead was the
            # weekly-mode bug that produced "-12 días más de lo óptimo").
            lead_periods = lead / days_per_period
            overstock_factor = (
                (item.get("signal_thresholds") or {}).get("overstock_factor")
                or _sig_th.DEFAULT_OVERSTOCK_FACTOR
            )
            excess = days - lead_periods * overstock_factor
            # What pausing can free is the capital in the units ABOVE the
            # ceiling, not the whole shelf. This quoted `value` — every unit
            # on hand — so a SKU one day past its ceiling "would free" 100% of
            # its stock value; the excess is `excess / days` of it, the same
            # coverage arithmetic the sentence itself prints (math audit
            # 2026-10-01).
            freed = value * max(0.0, excess) / days if days > 0 else 0.0
            recs.append({
                'priority': 5, 'sku': sku, 'name': name,
                'rec_type': 'OVERSTOCK',
                'text': (
                    f"{name} has {format_coverage_en(days, period)} of coverage "
                    f"({format_coverage_en(excess, period)} more than optimal). Pausing the next order "
                    f"would free {money(freed, currency=currency)} of working capital."
                ),
                'text_params': {
                    'name': name, 'days': round(days), 'excess': round(excess),
                    'amount': money(freed, currency=currency),
                },
                'action': "Pause the next order",
                'action_code': 'pause_next_order',
                'action_params': {},
                'signal': signal,
            })

    # Deduplicate by sku+rec_type, keep highest priority
    seen: dict = {}
    for r in sorted(recs, key=lambda x: x['priority']):
        key = f"{r['sku']}_{r['rec_type']}"
        if key not in seen:
            seen[key] = r

    return list(seen.values())[:20]  # top 20 recommendations


# Ranking metric for choosing the model each SKU is bought from, best first.
#
# Repeated here as a literal rather than imported because the layering keeps
# forecasting_core out of this module — the authority is
# `forecasting_core/evaluation/metrics.py:CHAMPION_METRIC_ORDER`, and
# `backend/tests/test_champion_metric_parity.py` asserts the two are equal so
# the duplication cannot silently drift. Same arrangement as
# DEFAULT_LEAD_TIME_DAYS.
#
# The drift is not hypothetical: this list started at ("cost", "wape") while the
# engine had already moved to ("cost_horizon", ...), and on a real 13-SKU
# session the two layers then disagreed on 8 of them. The engine computed its
# recommendations from one model, the semáforo and the order quantity came from
# another, and the accuracy on screen described a third.
_CHAMPION_METRICS = ("cost_horizon", "cost", "wape", "mae")


def _champion_metric(rows: list[dict]) -> str:
    for metric in _CHAMPION_METRICS:
        if any(r.get(metric) is not None for r in rows):
            return metric
    return "wape"


def best_model_by_sku(rows: list[dict]) -> dict[str, str]:
    """{sku: the model its purchase is computed from}, by lowest asymmetric cost.

    This used to rank by WAPE. WAPE, like MAE, is symmetric: it scores a
    forecast that runs 10% under exactly as well as one that runs 10% over, so
    it crowned models that were wrong in the expensive direction as readily as
    in the cheap one. Ranking by `cost` picks the model whose mistakes are the
    affordable kind.

    `compute_session_accuracy` reports the WAPE of whichever model this
    function picked, so the accuracy on screen still describes the forecast the
    orders came from — the invariant the old shared-rule comment was protecting.

    Baselines are excluded: they exist to be beaten, and buying from a naive
    forecast because it happened to win would be a bug, not a fallback.
    """
    metric = _champion_metric(rows)
    best: dict[str, tuple[float, str]] = {}
    for r in rows:
        if r.get("type") == "baseline":
            continue
        score, model = r.get(metric), r.get("model")
        if score is None or not model:
            continue
        sku = str(r.get("sku"))
        if sku not in best or float(score) < best[sku][0]:
            best[sku] = (float(score), str(model))
    return {sku: model for sku, (_s, model) in best.items()}


# A WAPE at or above this is the engine's `sum|e| / (0 + 1e-8)` — a validation
# window with no demand at all — not an error rate: a real one would need errors
# a million times the units actually sold.
_WAPE_UNDEFINED = 1e6


def compute_session_accuracy(rows: list[dict], items: list[dict]) -> Optional[float]:
    """Session-level accuracy: 1 - WAPE of the model each SKU is bought from.

    Not the best WAPE available for that SKU — the WAPE of the model that
    actually produced the numbers on screen. Those were the same thing while the
    champion was chosen by WAPE; now that it is chosen by asymmetric cost they
    can differ, and reporting the better one would be advertising a forecast
    nobody is using.

    Baseline rows (naive & friends) are scored for reference only and must not
    drag the headline number down. The aggregate is weighted by each SKU's
    daily demand so low-volume SKUs don't dominate; falls back to a plain mean
    when no demand weights are available. Clamped at 0 (WAPE can exceed 1).

    A SKU scoring wape == 0 AND mae == 0 is left out entirely. WAPE divides by
    the total real demand in the validation window, so a window with no demand
    gives 0/0 — an error of zero over a scale of zero. That is not a perfect
    forecast, it is the absence of anything to be accurate about, and the SKU
    screen has always refused to print it. Measured on a real session: every one
    of seven models (including the naive baselines) scored exactly 0/0, the
    demand weight was unknown so the weighted branch fell through to the plain
    mean, and the purchasing panel told the buyer "Precisión promedio 100.0%"
    directly above a suggested order of 130 units.
    """
    champions = best_model_by_sku(rows)
    best_wape: dict[str, float] = {}
    for r in rows:
        if r.get('type') == 'baseline':
            continue
        wape = r.get('wape')
        if wape is None:
            continue
        if float(wape) == 0.0 and float(r.get('mae') or 0.0) == 0.0:
            continue
        # The other face of the same 0/0: no demand in the window but a
        # forecast that was not exactly zero. The engine divides by
        # `sum|y| + 1e-8`, so 30 days of 0.3 against 30 zeros scores a WAPE of
        # 900,000,000 — and one such dead SKU, at any weight, took the session's
        # "Precisión promedio" from 89% to 0% (math audit 2026-10-01). A WAPE
        # that large only exists as that epsilon; it measures nothing.
        if not math.isfinite(float(wape)) or float(wape) >= _WAPE_UNDEFINED:
            continue
        sku = str(r.get('sku'))
        if champions.get(sku) == r.get('model'):
            best_wape[sku] = float(wape)
        elif sku not in champions and (sku not in best_wape or wape < best_wape[sku]):
            # No champion (e.g. every row for this SKU lacks the ranking
            # metric): fall back to the old best-of rule rather than dropping
            # the SKU out of the headline entirely.
            best_wape[sku] = float(wape)
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
                tenant_id, item['sku'], avg, days=14, period=period,
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
            items=items, forecasts=briefing_forecasts, period=period,
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

    recs = generate_recommendations(items, period, _tenant_currency(tenant_id))

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
    # How many products the value above could be computed from at all. Without
    # it, a catalogue where nobody recorded a unit cost reports "₡0 en bodega" —
    # which reads as "your stock is worth nothing" when the truth is that we
    # were never told what it cost. The inventory screen already distinguishes
    # the two ("SKUs con costo registrado"); the purchasing panel could not,
    # because this number arrived with no denominator.
    valued_skus    = sum(1 for i in items if i.get('inventory_value'))

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
            # Every SKU in SOBRESTOCK, as /status, the dashboard summary and
            # scenarios count it. `overstocked` keeps only the ones with a cost
            # (it feeds the capital figure), so counting it read "0 in
            # overstock" on the demo tenant beside a SOBRESTOCK row with no
            # cost on file (math audit 2026-10-01).
            'overstock':           sum(1 for i in items if i['signal'] == 'SOBRESTOCK'),
            'sin_datos':            sum(1 for i in items if i['signal'] == 'SIN_DATOS'),
            'avg_accuracy':         avg_accuracy,
            'total_inventory_value': round(total_value, 2),
            'valued_skus':          valued_skus,
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
           WHERE s.status = 'COMPLETED' AND s.archived_at IS NULL AND NOT s.is_backtest
           GROUP BY s.tenant_id, t.name""",
    )


def get_latest_completed_session(tenant_id: str) -> Optional[dict]:
    return query_one(
        """SELECT id AS session_id FROM sessions
           WHERE tenant_id = %s AND status = 'COMPLETED'
             AND archived_at IS NULL AND NOT is_backtest
           ORDER BY updated_at DESC LIMIT 1""",
        (tenant_id,),
    )


# WHO gets an alert. This read 'admin', 'manager' in all three functions below
# and in freshness_service — and `manager` is not a role this product has ever
# had: VALID_ROLES is {admin, analyst, viewer} (users/roles.py) and the users
# column defaults to 'analyst' (db/migrations.py), which is also what the invite
# dialog proposes. So every alert went to admins only, while /mi-cuenta invited
# ANY role to link their WhatsApp "to receive inventory alerts", walked them
# through the OTP and showed them a green "Verificado". They then received
# nothing, on either channel, and were not even written to activity_logs — so
# the alert bell was empty too, and a person excluded from every digest looked
# exactly like a quiet week.
#
# `viewer` stays out on purpose: it is the read-only role, and a stockout digest
# is a call to action addressed to whoever can act on it.
def get_tenant_admin_emails(tenant_id: str) -> list[str]:
    rows = query(
        """SELECT email FROM users
           WHERE tenant_id = %s AND role IN ('admin', 'analyst')
           AND email IS NOT NULL""",
        (tenant_id,),
    )
    return [r["email"] for r in rows]


def get_tenant_admin_whatsapps(tenant_id: str) -> list[str]:
    """E.164 numbers of admins/analysts who opted into WhatsApp alerts."""
    rows = query(
        """SELECT whatsapp_number FROM users
           WHERE tenant_id = %s AND role IN ('admin', 'analyst')
           AND whatsapp_number IS NOT NULL AND whatsapp_number <> ''""",
        (tenant_id,),
    )
    return [r["whatsapp_number"] for r in rows]


def get_tenant_alert_recipients(tenant_id: str) -> list[dict]:
    """
    Admins/analysts with the identity needed to attribute a delivery outcome.
    The email/WhatsApp lists above return bare contact strings, which cannot be
    written to activity_logs (user_id is NOT NULL) — this returns the user row.
    """
    return [
        dict(r) for r in query(
            """SELECT id, email, whatsapp_number FROM users
               WHERE tenant_id = %s AND role IN ('admin', 'analyst')""",
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
    from backend.sessions import planning_service
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
            incoming_qty = get_incoming_qty(tid)

            # The SAME period the screens use. `resolve_active_session` above
            # deliberately returns the session the app is showing — and this
            # loop then read it as if it were daily, because `period` defaults
            # to "daily" in the signature and nobody passed one.
            #
            # For a weekly tenant that inverts the verdict: a SKU with 4 weeks
            # of cover against a 2-week lead time is OK on screen, and the
            # same numbers read as days are 4 against 14 — PEDIR_YA. The buyer
            # got an 8:00 email calling a SKU critical, opened /inventario, and
            # saw green. Every HTTP entry point passes the period
            # (api/v1/inventory.py:693); the two schedulers were the gap.
            period = planning_service.get_planning(tid).get("period", "daily")

            items = _compute_inventory_status(
                tid, sid,
                forecasts=forecasts, stock_rows=stock_rows,
                learned_lead_times=learned_lead_times,
                incoming_qty=incoming_qty,
                period=period,
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
                        incoming_qty=incoming_qty,
                        period=period,          # same reason as above
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
                    period=period,      # so coverage reads "4 semanas", not "4 días"
                    # This message carries the TENANT's identity to the tenant's
                    # own buyer, so it leaves through the tenant's transport when
                    # it configured one. Without this argument the override is
                    # stored, shown as in effect, and never used — which is the
                    # failure this whole layer exists to prevent.
                    tenant_id=tid,
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
                        **({} if delivered else {"reason": email_mod.failure_reason(tid)}),
                    },
                )

            # WhatsApp channel — highest open-rate in LatAm; opt-in per user
            # via users.whatsapp_number. No-op when Twilio isn't configured.
            from backend.notifications import whatsapp as wa_mod
            from backend.notifications.whatsapp import build_inventory_alert_text, send_whatsapp
            text = build_inventory_alert_text(
                critical, warning, inventory_url,
                transfer_count=transfer_count,
                period=period,
            )
            for r in recipients:
                number = (r.get("whatsapp_number") or "").strip()
                if not number:
                    continue
                delivered = send_whatsapp(number, text, tenant_id=tid)
                if not delivered:
                    log.warning("alert whatsapp not delivered to=%s", number)
                record_notification_delivery(
                    tid, r["id"], "inventory_alert_whatsapp", delivered,
                    context={
                        "channel": "whatsapp",
                        "recipient": number,
                        "critical": len(critical),
                        "warning": len(warning),
                        **({} if delivered else {"reason": wa_mod.failure_reason(tid)}),
                    },
                )

        except Exception as e:
            log.error("inventory_alert: tenant=%s error=%s", tid, e)
            # A crash BEFORE any send (a corrupt forecasts blob, an unreadable
            # stock table) used to leave this tenant with no email and no row
            # anywhere — and silence is what a normal day looks like, so "the
            # digest broke" was indistinguishable from "nothing is urgent".
            # record_notification_delivery's own docstring makes the argument:
            # without a failed row, absence is ambiguous. Write one per
            # recipient so it shows up in /me/activity, where they can see it.
            try:
                for r in get_tenant_alert_recipients(tid) or []:
                    record_notification_delivery(
                        tid, r["id"], "inventory_alert_email", False,
                        context={"channel": "email", "recipient": r.get("email"),
                                 "reason": f"digest failed: {e}"},
                    )
            except Exception as inner:  # the recipient lookup itself may be what broke
                log.error("inventory_alert: tenant=%s could not record failure: %s",
                          tid, inner)


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

    from backend.sessions import planning_service
    from backend.sessions.planning_service import resolve_active_session

    for tenant in tenants:
        tid = tenant["tenant_id"]
        try:
            sid = resolve_active_session(tid)
            if not sid:
                continue

            # Read at the tenant's own planning grain, like every screen does.
            # Without this a weekly session was classified as daily, so the
            # SOBRESTOCK population this snapshot measures was not the one the
            # app calls overstocked — and the monthly difference between two
            # such snapshots is what /impacto headlines as "capital liberado".
            period = planning_service.get_planning(tid).get("period", "daily")
            items = get_inventory_status(tid, sid, period=period)
            overstock_value = _sum_overstock_value(items)

            execute(
                """INSERT INTO inventory_overstock_snapshots
                       (tenant_id, session_id, overstock_value)
                   VALUES (%s, %s, %s)""",
                (tid, sid, overstock_value),
            )
        except Exception as e:
            log.error("overstock_snapshot: tenant=%s error=%s", tid, e)
