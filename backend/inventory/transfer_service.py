"""
Inter-warehouse transfers (feature 5.4).

Send -> receive lifecycle mirroring PO reception: creating a transfer
decrements the origin warehouse inside one transaction (goods become
in-transit — owned by no warehouse), and the destination confirms arrival,
possibly partially. Stock therefore never shows units in two places, and
never shows in-transit units as available anywhere.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.db.connection import execute, query, query_one, transaction

log = logging.getLogger(__name__)

RECEIVABLE = ("in_transit", "partial")


def get_transfer(tenant_id: str, transfer_id: str, conn: Optional[Any] = None) -> Optional[dict]:
    header = query_one(
        "SELECT * FROM inventory_transfer_log WHERE id = %s AND tenant_id = %s",
        (transfer_id, tenant_id), conn=conn)
    if not header:
        return None
    header = dict(header)
    header["items"] = query(
        """SELECT id, sku, qty_sent, qty_received FROM inventory_transfer_items
           WHERE transfer_id = %s AND tenant_id = %s ORDER BY sku""",
        (transfer_id, tenant_id), conn=conn)
    return header


def list_transfers(tenant_id: str, status: Optional[str] = None) -> list[dict]:
    if status:
        headers = query(
            """SELECT * FROM inventory_transfer_log
               WHERE tenant_id = %s AND status = %s ORDER BY created_at DESC""",
            (tenant_id, status))
    else:
        headers = query(
            "SELECT * FROM inventory_transfer_log WHERE tenant_id = %s ORDER BY created_at DESC",
            (tenant_id,))
    if not headers:
        return []
    ids = tuple(h["id"] for h in headers)
    items = query(
        """SELECT id, transfer_id, sku, qty_sent, qty_received
           FROM inventory_transfer_items
           WHERE tenant_id = %s AND transfer_id IN %s ORDER BY sku""",
        (tenant_id, ids))
    by_transfer: dict[str, list[dict]] = {}
    for it in items:
        by_transfer.setdefault(it["transfer_id"], []).append(it)
    out = []
    for h in headers:
        d = dict(h)
        d["items"] = by_transfer.get(h["id"], [])
        out.append(d)
    return out


def create_transfer(
    tenant_id: str,
    user_id: str,
    from_warehouse: str,
    to_warehouse: str,
    items: list[dict],
    notes: Optional[str] = None,
) -> dict:
    """
    Create AND send a transfer: validates everything up front, then decrements
    the origin stock and writes header+items in one transaction.
    items: [{sku, qty}].
    """
    from backend.inventory import service as inv_svc
    from backend.inventory import warehouse_service as wh_svc

    from_warehouse = (from_warehouse or "").strip()
    to_warehouse = (to_warehouse or "").strip()
    if not from_warehouse or not to_warehouse:
        raise ValueError("Origin and destination warehouses are required")
    if from_warehouse == to_warehouse:
        raise ValueError("Origin and destination warehouses must differ")
    if not wh_svc.get_warehouse_by_name(tenant_id, from_warehouse):
        raise ValueError(f"Warehouse '{from_warehouse}' not found")
    if not wh_svc.get_warehouse_by_name(tenant_id, to_warehouse):
        raise ValueError(f"Warehouse '{to_warehouse}' not found")
    if not items:
        raise ValueError("A transfer needs at least one item")

    # Merge duplicate SKUs so the availability check sees the real total.
    qty_by_sku: dict[str, float] = {}
    for ln in items:
        sku = str(ln.get("sku") or "").strip()
        qty = float(ln.get("qty") or 0)
        if not sku:
            raise ValueError("Every transfer line needs a SKU")
        if qty <= 0:
            raise ValueError(f"Quantity for '{sku}' must be positive")
        qty_by_sku[sku] = qty_by_sku.get(sku, 0.0) + qty

    # Availability check BEFORE any write.
    for sku, qty in qty_by_sku.items():
        row = inv_svc.get_stock(tenant_id, sku, warehouse=from_warehouse)
        available = float(row["current_stock"]) if row else 0.0
        if qty > available:
            raise ValueError(
                f"Insufficient stock of '{sku}' in '{from_warehouse}' "
                f"({available:g} available, {qty:g} requested)")

    with transaction() as conn:
        header = query_one(
            """INSERT INTO inventory_transfer_log
                   (tenant_id, from_warehouse, to_warehouse, status, notes, created_by)
               VALUES (%s, %s, %s, 'in_transit', %s, %s)
               RETURNING *""",
            (tenant_id, from_warehouse, to_warehouse, notes, user_id),
            conn=conn)
        for sku, qty in sorted(qty_by_sku.items()):
            execute(
                """INSERT INTO inventory_transfer_items
                       (tenant_id, transfer_id, sku, qty_sent)
                   VALUES (%s, %s, %s, %s)""",
                (tenant_id, header["id"], sku, qty), conn=conn)
            execute(
                """UPDATE inventory_stock
                   SET current_stock = current_stock - %s, updated_at = NOW()
                   WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                (qty, tenant_id, sku, from_warehouse), conn=conn)
            new_row = inv_svc.get_stock(tenant_id, sku, warehouse=from_warehouse, conn=conn)
            execute(
                "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock) VALUES (%s, %s, %s)",
                (tenant_id, sku, new_row["current_stock"]), conn=conn)
        result = get_transfer(tenant_id, header["id"], conn=conn)

    log.info("[transfer] sent tenant=%s id=%s %s->%s skus=%d",
             tenant_id, result["id"], from_warehouse, to_warehouse, len(qty_by_sku))
    return result


def receive_transfer(
    tenant_id: str, transfer_id: str, lines: Optional[list[dict]] = None
) -> dict:
    """
    Record arrival at the destination. lines: [{sku, received_qty}]; None means
    "everything outstanding arrived". Partial receptions accumulate.
    """
    from backend.entitlements.service import enforce_limit
    from backend.inventory import service as inv_svc
    from backend.inventory import warehouse_service as wh_svc

    t = get_transfer(tenant_id, transfer_id)
    if not t:
        raise ValueError("Transfer not found")
    if t["status"] not in RECEIVABLE:
        raise ValueError(f"This transfer cannot be received (status: {t['status']})")

    outstanding = {
        i["sku"]: float(i["qty_sent"]) - float(i["qty_received"] or 0)
        for i in t["items"]
    }
    if lines is None:
        to_receive = {sku: qty for sku, qty in outstanding.items() if qty > 0}
    else:
        to_receive = {}
        for ln in lines:
            sku = str(ln.get("sku") or "")
            if sku not in outstanding:
                raise ValueError(f"SKU '{sku}' is not part of this transfer")
            qty = float(ln.get("received_qty") or 0)
            if qty < 0:
                raise ValueError(f"Negative received quantity for '{sku}'")
            if qty > outstanding[sku]:
                raise ValueError(
                    f"'{sku}': receiving {qty:g} but only {outstanding[sku]:g} outstanding")
            if qty > 0:
                to_receive[sku] = qty
    if not to_receive:
        raise ValueError("Nothing to receive")

    dest = t["to_warehouse"]
    # Pre-check limits BEFORE any write: destination rows that don't exist yet
    # are NEW stock rows created through upsert_stock — same chokepoint rules
    # (and all-or-nothing guarantee) as PO reception.
    existing_keys = inv_svc.list_stock_keys(tenant_id)
    new_pairs = {(sku, dest) for sku in to_receive} - existing_keys
    if new_pairs:
        enforce_limit(tenant_id, "max_skus", inv_svc.count_stock(tenant_id),
                      adding=len(new_pairs))
    if dest not in wh_svc.list_warehouse_names(tenant_id):
        enforce_limit(tenant_id, "max_locations", wh_svc.count_warehouses(tenant_id))

    with transaction() as conn:
        for sku, qty in sorted(to_receive.items()):
            execute(
                """UPDATE inventory_transfer_items
                   SET qty_received = COALESCE(qty_received, 0) + %s
                   WHERE transfer_id = %s AND tenant_id = %s AND sku = %s""",
                (qty, transfer_id, tenant_id, sku), conn=conn)
            existing = inv_svc.get_stock(tenant_id, sku, warehouse=dest, conn=conn)
            if existing:
                execute(
                    """UPDATE inventory_stock
                       SET current_stock = current_stock + %s, updated_at = NOW()
                       WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                    (qty, tenant_id, sku, dest), conn=conn)
            else:
                origin_row = inv_svc.get_stock(
                    tenant_id, sku, warehouse=t["from_warehouse"], conn=conn)
                inv_svc.upsert_stock(tenant_id, sku, {
                    "current_stock": qty,
                    "display_name": (origin_row or {}).get("display_name"),
                    "supplier": (origin_row or {}).get("supplier"),
                    "warehouse": dest,
                }, conn=conn)
            new_row = inv_svc.get_stock(tenant_id, sku, warehouse=dest, conn=conn)
            execute(
                "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock) VALUES (%s, %s, %s)",
                (tenant_id, sku, new_row["current_stock"]), conn=conn)

        fresh = get_transfer(tenant_id, transfer_id, conn=conn)
        fully = all(float(i["qty_received"] or 0) >= float(i["qty_sent"])
                    for i in fresh["items"])
        status = "received" if fully else "partial"
        execute(
            """UPDATE inventory_transfer_log
               SET status = %s, received_at = CASE WHEN %s THEN NOW() ELSE received_at END
               WHERE id = %s AND tenant_id = %s""",
            (status, fully, transfer_id, tenant_id), conn=conn)
        result = get_transfer(tenant_id, transfer_id, conn=conn)

    log.info("[transfer] received tenant=%s id=%s status=%s", tenant_id, transfer_id, status)
    return result


def cancel_transfer(tenant_id: str, transfer_id: str) -> dict:
    """Cancel an in-transit transfer with nothing received: goods go back home."""
    t = get_transfer(tenant_id, transfer_id)
    if not t:
        raise ValueError("Transfer not found")
    if t["status"] != "in_transit" or any(
            float(i["qty_received"] or 0) > 0 for i in t["items"]):
        raise ValueError("Only in-transit transfers with nothing received can be cancelled")

    from backend.inventory import service as inv_svc

    with transaction() as conn:
        for i in t["items"]:
            execute(
                """UPDATE inventory_stock
                   SET current_stock = current_stock + %s, updated_at = NOW()
                   WHERE tenant_id = %s AND sku = %s AND warehouse = %s""",
                (float(i["qty_sent"]), tenant_id, i["sku"], t["from_warehouse"]),
                conn=conn)
            new_row = inv_svc.get_stock(
                tenant_id, i["sku"], warehouse=t["from_warehouse"], conn=conn)
            execute(
                "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock) VALUES (%s, %s, %s)",
                (tenant_id, i["sku"], new_row["current_stock"]), conn=conn)
        execute(
            """UPDATE inventory_transfer_log SET status = 'cancelled'
               WHERE id = %s AND tenant_id = %s""",
            (transfer_id, tenant_id), conn=conn)
        result = get_transfer(tenant_id, transfer_id, conn=conn)

    log.info("[transfer] cancelled tenant=%s id=%s", tenant_id, transfer_id)
    return result
