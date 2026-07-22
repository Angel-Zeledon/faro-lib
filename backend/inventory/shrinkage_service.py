"""
Shrinkage (non-sale stock-outs).

Lets a user quickly record inventory that left stock for a reason OTHER than
a sale — breakage, expiry, self-consumption, or a gift/sample. Two effects,
mirroring how reception_service.py handles the opposite direction (stock
arriving):
  1. Decrements inventory_stock.current_stock through the SAME UPDATE path
     every other stock-affecting event uses (receptions, manual edits), plus
     a point-in-time snapshot — so /inventory/status (and the signal) picks
     this up immediately. No parallel, disconnected stock field.
  2. Accumulates the cost (quantity × unit cost, captured at record time) in
     inventory_shrinkage, ready to feed a future "how much you lose to
     shrinkage" monthly summary (that summary itself is a separate feature —
     this module only records the event and its cost).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Canonical reason codes. Spanish labels live in Frontend/src/i18n/translations.ts
# (inventory.shrinkage_reason_*) — the same pattern as the signal/product_type enums.
REASONS = ("breakage", "expiry", "self_consumption", "gift")

# Reasons that only the SYSTEM writes, never the manual shrinkage form:
# transfer_loss rows come from transfer_service.close_transfer (a partial
# transfer closed with missing units). They live in the same ledger and the
# UI labels them, but /shrinkage/reasons (the form's option list) exposes
# only the manual REASONS above.
SYSTEM_REASONS = ("transfer_loss",)


def record_shrinkage(
    tenant_id: str,
    sku: str,
    quantity: float,
    reason: str,
    user_id: Optional[str] = None,
    warehouse: Optional[str] = None,
    notes: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> dict:
    """
    Records a shrinkage event and decrements stock accordingly.
    Raises ValueError with a user-facing message on invalid state/input.
    """
    if quantity is None or quantity <= 0:
        raise ValueError("La cantidad debe ser mayor que 0")
    if reason not in REASONS:
        raise ValueError(f"Razón inválida. Opciones: {', '.join(REASONS)}")

    from backend.inventory import service as inv_svc

    warehouse = warehouse or "principal"
    existing = inv_svc.get_stock(tenant_id, sku, warehouse=warehouse)
    if not existing:
        raise ValueError(f"SKU '{sku}' no encontrado en inventario (bodega '{warehouse}')")

    current = float(existing["current_stock"] or 0)
    if quantity > current:
        raise ValueError(
            f"La cantidad ({quantity}) excede el stock actual ({current}) de '{sku}'"
        )

    unit_cost = existing.get("unit_cost")

    occurred_at = occurred_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    # 1. Stock: decrement through the same UPDATE path reception_service uses
    # to increment it (never a parallel field) so the semáforo stays accurate.
    # The WHERE clause re-checks current_stock >= quantity atomically — the
    # early guard above is just a friendly error for the common case; without
    # this conditional UPDATE, two concurrent shrinkage could both pass the
    # Python-side check against the same stale read and drive stock negative.
    updated = query_one(
        """UPDATE inventory_stock
           SET current_stock = current_stock - %s, updated_at = NOW()
           WHERE tenant_id = %s AND sku = %s AND warehouse = %s AND current_stock >= %s
           RETURNING current_stock""",
        (quantity, tenant_id, sku, warehouse, quantity),
    )
    if updated is None:
        raise ValueError(
            f"La cantidad ({quantity}) excede el stock actual de '{sku}' "
            "(pudo haber cambiado por otra operación concurrente)"
        )

    # 2. Point-in-time snapshot, same as every other stock-affecting event —
    # keeps /stock/{sku}/history consistent with receptions and manual edits.
    try:
        execute(
            "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock) VALUES (%s, %s, %s)",
            (tenant_id, sku, updated["current_stock"]),
        )
    except Exception as e:
        log.warning("shrinkage snapshot failed sku=%s: %s", sku, e)

    # 3. Record the shrinkage event with its accumulated cost.
    return insert_ledger_row(
        tenant_id, sku=sku, warehouse=warehouse, quantity=quantity,
        reason=reason, unit_cost=unit_cost, notes=notes, user_id=user_id,
        occurred_at=occurred_at,
    )


def insert_ledger_row(
    tenant_id: str,
    *,
    sku: str,
    warehouse: str,
    quantity: float,
    reason: str,
    unit_cost: Optional[float] = None,
    notes: Optional[str] = None,
    user_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    conn: Optional[Any] = None,
) -> dict:
    """
    The single writer of the shrinkage ledger: cost math + INSERT, nothing
    else. record_shrinkage (manual form: validates reason, decrements stock)
    and transfer_service.close_transfer (system transfer_loss: stock already
    gone) both end here, so the ledger row shape lives in exactly one place.
    """
    cost_total = round(quantity * float(unit_cost), 2) if unit_cost is not None else None
    occurred_at = occurred_at or datetime.now(timezone.utc)
    row = query_one(
        """INSERT INTO inventory_shrinkage
               (tenant_id, sku, warehouse, quantity, reason, unit_cost, total_cost,
                notes, created_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (tenant_id, sku, warehouse, quantity, reason, unit_cost, cost_total,
         notes, user_id, occurred_at),
        conn=conn,
    )
    log.info(
        "[shrinkage] tenant=%s sku=%s warehouse=%s qty=%s reason=%s total_cost=%s",
        tenant_id, sku, warehouse, quantity, reason, cost_total,
    )
    return row


def list_shrinkage(tenant_id: str, sku: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Recent shrinkage events, most recent first. Basis for the future monthly summary."""
    if sku:
        return query(
            """SELECT * FROM inventory_shrinkage
               WHERE tenant_id = %s AND sku = %s
               ORDER BY created_at DESC LIMIT %s""",
            (tenant_id, sku, limit),
        )
    return query(
        """SELECT * FROM inventory_shrinkage
           WHERE tenant_id = %s
           ORDER BY created_at DESC LIMIT %s""",
        (tenant_id, limit),
    )
