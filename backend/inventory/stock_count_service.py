"""
Physical stock count: walk a warehouse with a phone, compare what is on the
shelf with what the system believes, and apply the differences with a trail.

A count is a session (`stock_counts`) with one line per SKU
(`stock_count_lines`). Each line keeps the counted quantity NEXT TO the system
quantity at the moment of the first scan, because the stock moves while people
count (a reception lands, a shrinkage is recorded). Applying therefore writes
the DIFFERENCE, not the counted number:

    adjustment = counted - system_at_count
    new stock  = stock now + adjustment        (never below zero)

so a reception booked after the scan is not erased by a count taken before it.

Lifecycle: open -> closed -> applied, or cancelled from open/closed. Lines can
only change while the count is open; apply needs a closed count and can run
exactly once (the count row is locked and its status flips in the same
transaction as the stock writes, so two taps cannot both win).

Everything the write touches goes through `inventory.service.upsert_stock` (the
chokepoint that records the snapshot, creates the warehouse row and enforces the
SKU / location ceilings) on ONE connection held under the tenant's limit_guard:
either every selected adjustment lands, or none does.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.db.connection import query, query_one
from backend.errors import AppError

log = logging.getLogger(__name__)

MAX_QTY = 1_000_000_000
STATUSES = ("open", "closed", "applied", "cancelled")
LINE_MODES = ("add", "set")
LINE_SOURCES = ("scan", "manual")
ADJUSTMENT_REASON = "physical_count"
_EPS = 1e-9


# ── Barcode / SKU lookup ──────────────────────────────────────────────────────

def lookup(tenant_id: str, code: str, warehouse: Optional[str] = None) -> dict:
    """Resolve a scanned code to one SKU: barcode first, then SKU, exact before
    case-insensitive. Tenant-scoped. Raises 404 when nothing matches and 409
    when the best match is ambiguous (two SKUs share the barcode) — picking one
    silently would count units against the wrong product."""
    from backend.inventory import warehouse_service as wh_svc

    cleaned = (code or "").strip()
    if not cleaned:
        raise AppError("lookup_code_required", "A code is required", status_code=422)

    rows = query(
        """SELECT sku, warehouse, display_name, barcode, unit_of_measure, unit_cost,
                  current_stock, category
           FROM inventory_stock
           WHERE tenant_id = %s
             AND (barcode = %s OR sku = %s OR LOWER(barcode) = LOWER(%s) OR LOWER(sku) = LOWER(%s))""",
        (tenant_id, cleaned, cleaned, cleaned, cleaned),
    )

    def rank(r: dict) -> tuple[int, str]:
        if r["barcode"] == cleaned:
            return 0, "barcode"
        if r["sku"] == cleaned:
            return 1, "sku"
        if (r["barcode"] or "").lower() == cleaned.lower():
            return 2, "barcode"
        return 3, "sku"

    if not rows:
        raise AppError("lookup_code_not_found", f"No SKU or barcode matches '{cleaned}'",
                       status_code=404, params={"code": cleaned})

    best = min(rank(r)[0] for r in rows)
    winners = [r for r in rows if rank(r)[0] == best]
    skus = sorted({r["sku"] for r in winners})
    if len(skus) > 1:
        raise AppError(
            "lookup_code_ambiguous",
            f"'{cleaned}' matches more than one SKU: {', '.join(skus)}",
            status_code=409, params={"code": cleaned, "skus": ", ".join(skus)},
        )
    sku = skus[0]
    matched_by = rank(winners[0])[1]
    sku_rows = query(
        """SELECT sku, warehouse, display_name, barcode, unit_of_measure, unit_cost,
                  current_stock, category
           FROM inventory_stock WHERE tenant_id = %s AND sku = %s ORDER BY warehouse""",
        (tenant_id, sku),
    )
    wh_name = wh_svc.resolve_canonical_name(tenant_id, warehouse) if warehouse else None
    here = next((r for r in sku_rows if r["warehouse"] == wh_name), None) if wh_name else None
    base = here or sku_rows[0]
    if wh_name:
        system_qty = float(here["current_stock"] or 0) if here else 0.0
    else:
        system_qty = sum(float(r["current_stock"] or 0) for r in sku_rows)
    unit_cost = base["unit_cost"]
    if unit_cost is None:
        unit_cost = next((r["unit_cost"] for r in sku_rows if r["unit_cost"] is not None), None)
    return {
        "sku": sku,
        "display_name": base["display_name"],
        "barcode": base["barcode"],
        "unit_of_measure": base["unit_of_measure"],
        "category": base["category"],
        "unit_cost": unit_cost,
        "warehouse": wh_name,
        "system_qty": system_qty,
        "in_warehouse": (here is not None) if wh_name else None,
        "matched_by": matched_by,
    }


# ── Counts ────────────────────────────────────────────────────────────────────

def _get_count_row(tenant_id: str, count_id: str, conn: Any = None, lock: bool = False) -> dict:
    row = query_one(
        "SELECT * FROM stock_counts WHERE tenant_id = %s AND id = %s"
        + (" FOR UPDATE" if lock else ""),
        (tenant_id, count_id), conn=conn,
    )
    if not row:
        raise AppError("count_not_found", "Stock count not found", status_code=404,
                       params={"count_id": count_id})
    return row


def create_count(
    tenant_id: str, user_id: str, warehouse: Optional[str],
    scope_category: Optional[str] = None, scope_supplier: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    from backend.inventory import warehouse_service as wh_svc

    name = wh_svc.resolve_canonical_name(tenant_id, warehouse)
    # A count walks a place that exists. The default location is accepted even
    # before its row is created (a tenant that has only ever imported stock).
    if not wh_svc.get_warehouse_by_name(tenant_id, name) and name != wh_svc.DEFAULT_WAREHOUSE:
        raise AppError("warehouse_not_found", f"Warehouse '{name}' not found",
                       status_code=404, params={"warehouse": name})
    row = query_one(
        """INSERT INTO stock_counts
               (tenant_id, warehouse, scope_category, scope_supplier, notes, created_by)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
        (tenant_id, name, (scope_category or None), (scope_supplier or None),
         (notes or None), user_id),
    )
    return row


def list_counts(tenant_id: str, status: Optional[str] = None, limit: int = 50) -> list[dict]:
    where, params = "c.tenant_id = %s", [tenant_id]
    if status:
        where += " AND c.status = %s"
        params.append(status)
    params.append(limit)
    return query(
        f"""SELECT c.*,
                   (SELECT COUNT(*) FROM stock_count_lines l WHERE l.count_id = c.id) AS lines_count
            FROM stock_counts c WHERE {where}
            ORDER BY c.created_at DESC LIMIT %s""",
        tuple(params),
    )


def get_count(tenant_id: str, count_id: str) -> dict:
    count = _get_count_row(tenant_id, count_id)
    lines = query(
        """SELECT * FROM stock_count_lines WHERE tenant_id = %s AND count_id = %s
           ORDER BY scanned_at DESC""",
        (tenant_id, count_id),
    )
    return {**count, "lines": lines}


# ── Lines ─────────────────────────────────────────────────────────────────────

def upsert_line(
    tenant_id: str, count_id: str, user_id: str, sku: str, qty: float,
    mode: str = "add", source: str = "manual", client_ref: Optional[str] = None,
) -> dict:
    """Record `qty` units of `sku`. `add` (the default, what scanning twice
    means) accumulates; `set` replaces the counted total. A `client_ref` makes
    the call idempotent: the phone retries after a lost response or replays its
    offline queue, and the same scan must never count twice."""
    from backend.db.connection import transaction

    if mode not in LINE_MODES:
        raise AppError("count_invalid_mode", "mode must be 'add' or 'set'", status_code=422)
    if source not in LINE_SOURCES:
        raise AppError("count_invalid_source", "source must be 'scan' or 'manual'",
                       status_code=422)
    if qty is None or qty < 0 or qty > MAX_QTY:
        raise AppError("count_invalid_quantity", "Quantity out of range", status_code=422)

    with transaction() as conn:
        count = _get_count_row(tenant_id, count_id, conn=conn, lock=True)
        if count["status"] != "open":
            raise AppError("count_not_open", "This count is no longer open for changes",
                           status_code=409, params={"status": count["status"]})

        if client_ref:
            fresh = query_one(
                """INSERT INTO stock_count_ops (count_id, client_ref, tenant_id)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING client_ref""",
                (count_id, client_ref, tenant_id), conn=conn,
            )
            if fresh is None:
                existing = query_one(
                    "SELECT * FROM stock_count_lines WHERE count_id = %s AND sku = %s",
                    (count_id, sku), conn=conn,
                )
                return {"duplicate": True, "line": existing}

        stock_rows = query(
            "SELECT warehouse, current_stock FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s", (tenant_id, sku), conn=conn,
        )
        if not stock_rows:
            raise AppError("count_sku_not_found", f"SKU '{sku}' not found in inventory",
                           status_code=404, params={"sku": sku})
        here = next((r for r in stock_rows if r["warehouse"] == count["warehouse"]), None)
        system_qty = float(here["current_stock"] or 0) if here else 0.0

        existing = query_one(
            "SELECT * FROM stock_count_lines WHERE count_id = %s AND sku = %s",
            (count_id, sku), conn=conn,
        )
        if existing:
            new_qty = float(existing["counted_qty"]) + qty if mode == "add" else qty
            if new_qty > MAX_QTY:
                raise AppError("count_invalid_quantity", "Quantity out of range",
                               status_code=422)
            line = query_one(
                """UPDATE stock_count_lines
                   SET counted_qty = %s, source = %s, scanned_at = NOW(), scanned_by = %s
                   WHERE id = %s RETURNING *""",
                (new_qty, source, user_id, existing["id"]), conn=conn,
            )
        else:
            line = query_one(
                """INSERT INTO stock_count_lines
                       (tenant_id, count_id, sku, counted_qty, system_qty_at_count,
                        source, scanned_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (tenant_id, count_id, sku, qty, system_qty, source, user_id), conn=conn,
            )
    return {"duplicate": False, "line": line}


def delete_line(tenant_id: str, count_id: str, sku: str) -> None:
    from backend.db.connection import transaction

    with transaction() as conn:
        count = _get_count_row(tenant_id, count_id, conn=conn, lock=True)
        if count["status"] != "open":
            raise AppError("count_not_open", "This count is no longer open for changes",
                           status_code=409, params={"status": count["status"]})
        gone = query_one(
            "DELETE FROM stock_count_lines WHERE count_id = %s AND sku = %s RETURNING id",
            (count_id, sku), conn=conn,
        )
        if not gone:
            raise AppError("count_line_not_found", f"SKU '{sku}' is not in this count",
                           status_code=404, params={"sku": sku})


def close_count(tenant_id: str, count_id: str, user_id: str) -> dict:
    from backend.db.connection import transaction

    with transaction() as conn:
        count = _get_count_row(tenant_id, count_id, conn=conn, lock=True)
        if count["status"] != "open":
            raise AppError("count_not_open", "This count is no longer open for changes",
                           status_code=409, params={"status": count["status"]})
        n = query_one("SELECT COUNT(*) AS c FROM stock_count_lines WHERE count_id = %s",
                      (count_id,), conn=conn)["c"]
        if n == 0:
            raise AppError("count_empty", "Count at least one item before closing",
                           status_code=409)
        return query_one(
            """UPDATE stock_counts SET status = 'closed', closed_at = NOW(), closed_by = %s
               WHERE id = %s RETURNING *""", (user_id, count_id), conn=conn,
        )


def cancel_count(tenant_id: str, count_id: str, user_id: str) -> dict:
    from backend.db.connection import transaction

    with transaction() as conn:
        count = _get_count_row(tenant_id, count_id, conn=conn, lock=True)
        if count["status"] not in ("open", "closed"):
            raise AppError("count_cannot_cancel", "Only an open or closed count can be cancelled",
                           status_code=409, params={"status": count["status"]})
        return query_one(
            """UPDATE stock_counts SET status = 'cancelled', cancelled_at = NOW(),
                      cancelled_by = %s WHERE id = %s RETURNING *""",
            (user_id, count_id), conn=conn,
        )


# ── Review ────────────────────────────────────────────────────────────────────

_LINE_VIEW_SQL = """
    SELECT l.*, s.display_name,
           s.current_stock AS current_qty,
           COALESCE(s.unit_cost, (SELECT x.unit_cost FROM inventory_stock x
                                  WHERE x.tenant_id = l.tenant_id AND x.sku = l.sku
                                    AND x.unit_cost IS NOT NULL LIMIT 1)) AS unit_cost
    FROM stock_count_lines l
    JOIN stock_counts c ON c.id = l.count_id
    LEFT JOIN inventory_stock s
           ON s.tenant_id = l.tenant_id AND s.sku = l.sku AND s.warehouse = c.warehouse
    WHERE l.tenant_id = %s AND l.count_id = %s
"""


def _shape_line(r: dict) -> dict:
    counted = float(r["counted_qty"])
    system = float(r["system_qty_at_count"])
    diff = counted - system
    cost = r["unit_cost"]
    current = float(r["current_qty"]) if r["current_qty"] is not None else 0.0
    return {
        "sku": r["sku"],
        "display_name": r["display_name"],
        "counted_qty": counted,
        "system_qty_at_count": system,
        "current_qty": current,
        "difference": diff,
        # Never divided or defaulted: no cost means no value, not a zero value.
        "unit_cost": cost,
        "value_impact": round(diff * float(cost), 2) if cost is not None else None,
        "moved_since_count": abs(current - system) > _EPS,
        "source": r["source"],
        "scanned_at": r["scanned_at"],
        "applied_at": r["applied_at"],
        "applied_from": r["applied_from"],
        "applied_to": r["applied_to"],
    }


def preview(tenant_id: str, count_id: str) -> dict:
    count = _get_count_row(tenant_id, count_id)
    lines = [_shape_line(r) for r in query(_LINE_VIEW_SQL, (tenant_id, count_id))]

    def order(l: dict):
        # Priced lines by the size of the money at stake, then the unpriced ones
        # by units: a line with no cost must not look harmless.
        if l["value_impact"] is not None:
            return (0, -abs(l["value_impact"]))
        return (1, -abs(l["difference"]))

    lines.sort(key=order)
    diffs = [l for l in lines if abs(l["difference"]) > _EPS]
    over = [l for l in diffs if l["difference"] > 0]
    short = [l for l in diffs if l["difference"] < 0]
    priced = [l for l in diffs if l["value_impact"] is not None]

    where, params = "tenant_id = %s AND warehouse = %s", [tenant_id, count["warehouse"]]
    if count["scope_category"]:
        where += " AND category = %s"
        params.append(count["scope_category"])
    if count["scope_supplier"]:
        where += " AND supplier = %s"
        params.append(count["scope_supplier"])
    params.append(count_id)
    uncounted = query_one(
        f"""SELECT COUNT(*) AS c FROM inventory_stock
            WHERE {where} AND sku NOT IN
                  (SELECT sku FROM stock_count_lines WHERE count_id = %s)""",
        tuple(params),
    )["c"]

    return {
        "count": count,
        "lines": lines,
        "totals": {
            "lines": len(lines),
            "lines_with_difference": len(diffs),
            "units_over": sum(l["difference"] for l in over),
            "units_short": -sum(l["difference"] for l in short),
            "value_over": round(sum(l["value_impact"] for l in priced if l["difference"] > 0), 2),
            "value_short": round(-sum(l["value_impact"] for l in priced if l["difference"] < 0), 2),
            "net_value": round(sum(l["value_impact"] for l in priced), 2),
            "unpriced_lines": len(diffs) - len(priced),
            # SKUs of the walked area nobody scanned. They are NOT treated as
            # zero: an unscanned shelf is unknown, not empty.
            "uncounted_skus": uncounted,
        },
    }


# ── Apply ─────────────────────────────────────────────────────────────────────

def apply_count(
    tenant_id: str, count_id: str, user_id: str, skus: Optional[list[str]] = None,
) -> dict:
    """Write the selected differences into stock, exactly once.

    `skus` None = every line of the count; a list = only those lines (a partial
    apply). Lines left out are NOT applied and the count still becomes final:
    a count is one event in time, and re-applying it later would write numbers
    nobody has looked at since.
    """
    from backend.entitlements.service import limit_guard
    from backend.inventory import service as inv_svc

    if skus is not None and len(skus) == 0:
        raise AppError("count_no_lines_selected", "Select at least one line to apply",
                       status_code=422)

    with limit_guard(tenant_id) as conn:
        count = _get_count_row(tenant_id, count_id, conn=conn, lock=True)
        if count["status"] == "applied":
            raise AppError("count_already_applied", "This count was already applied",
                           status_code=409)
        if count["status"] != "closed":
            raise AppError("count_not_closed", "Close the count before applying it",
                           status_code=409, params={"status": count["status"]})
        warehouse = count["warehouse"]

        lines = query(
            """SELECT l.*, s.current_stock AS current_qty,
                      COALESCE(s.unit_cost, (SELECT x.unit_cost FROM inventory_stock x
                                             WHERE x.tenant_id = l.tenant_id AND x.sku = l.sku
                                               AND x.unit_cost IS NOT NULL LIMIT 1)) AS unit_cost
               FROM stock_count_lines l
               LEFT JOIN inventory_stock s
                      ON s.tenant_id = l.tenant_id AND s.sku = l.sku AND s.warehouse = %s
               WHERE l.tenant_id = %s AND l.count_id = %s AND l.applied_at IS NULL
               ORDER BY l.sku
               FOR UPDATE OF l""",
            (warehouse, tenant_id, count_id), conn=conn,
        )
        if skus is not None:
            wanted = set(skus)
            unknown = wanted - {l["sku"] for l in lines}
            if unknown:
                raise AppError(
                    "count_line_not_found",
                    f"SKU '{sorted(unknown)[0]}' is not in this count",
                    status_code=404, params={"sku": sorted(unknown)[0]},
                )
            lines = [l for l in lines if l["sku"] in wanted]

        units_added = units_removed = 0.0
        value_net = 0.0
        unpriced = adjusted = unchanged = 0
        for l in lines:
            current = float(l["current_qty"]) if l["current_qty"] is not None else 0.0
            diff = float(l["counted_qty"]) - float(l["system_qty_at_count"])
            new_qty = max(0.0, current + diff)
            if abs(new_qty - current) <= _EPS:
                unchanged += 1
                query_one(
                    """UPDATE stock_count_lines SET applied_at = NOW(), applied_by = %s,
                              applied_from = %s, applied_to = %s WHERE id = %s RETURNING id""",
                    (user_id, current, current, l["id"]), conn=conn,
                )
                continue
            inv_svc.upsert_stock(
                tenant_id, l["sku"], {"current_stock": new_qty, "warehouse": warehouse},
                conn=conn,
            )
            delta = new_qty - current
            cost = l["unit_cost"]
            query_one(
                """INSERT INTO stock_adjustments
                       (tenant_id, sku, warehouse, qty_before, qty_after, delta, unit_cost,
                        reason, ref_id, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (tenant_id, l["sku"], warehouse, current, new_qty, delta, cost,
                 ADJUSTMENT_REASON, count_id, user_id), conn=conn,
            )
            query_one(
                """UPDATE stock_count_lines SET applied_at = NOW(), applied_by = %s,
                          applied_from = %s, applied_to = %s WHERE id = %s RETURNING id""",
                (user_id, current, new_qty, l["id"]), conn=conn,
            )
            adjusted += 1
            if delta > 0:
                units_added += delta
            else:
                units_removed += -delta
            if cost is not None:
                value_net += delta * float(cost)
            else:
                unpriced += 1

        query_one(
            """UPDATE stock_counts SET status = 'applied', applied_at = NOW(), applied_by = %s
               WHERE id = %s RETURNING id""", (user_id, count_id), conn=conn,
        )

    return {
        "count_id": count_id,
        "warehouse": warehouse,
        "lines_applied": len(lines),
        "lines_adjusted": adjusted,
        "lines_unchanged": unchanged,
        "units_added": units_added,
        "units_removed": units_removed,
        "net_value": round(value_net, 2),
        "unpriced_lines": unpriced,
    }
