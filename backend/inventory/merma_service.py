"""
Mermas (shrinkage / non-sale stock-outs).

Lets a user quickly record inventory that left stock for a reason OTHER than
a sale — breakage, expiry, self-consumption, or a gift/sample. Two effects,
mirroring how reception_service.py handles the opposite direction (stock
arriving):
  1. Decrements inventory_stock.stock_actual through the SAME UPDATE path
     every other stock-affecting event uses (receptions, manual edits), plus
     a point-in-time snapshot — so /inventory/status (and the semáforo) picks
     this up immediately. No parallel, disconnected stock field.
  2. Accumulates the cost (quantity × unit cost, captured at record time) in
     inventory_mermas, ready to feed a future "how much you lose to
     shrinkage" monthly summary (that summary itself is a separate feature —
     this module only records the event and its cost).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

# Canonical reason codes. Spanish labels live in Frontend/src/i18n/translations.ts
# (inventory.merma_reason_*) — the same pattern as the signal/product_type enums.
REASONS = ("breakage", "expiry", "self_consumption", "gift")


def record_merma(
    tenant_id: str,
    sku: str,
    quantity: float,
    reason: str,
    user_id: Optional[str] = None,
    bodega: Optional[str] = None,
    notes: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> dict:
    """
    Records a merma event and decrements stock accordingly.
    Raises ValueError with a user-facing message on invalid state/input.
    """
    if quantity is None or quantity <= 0:
        raise ValueError("La cantidad debe ser mayor que 0")
    if reason not in REASONS:
        raise ValueError(f"Razón inválida. Opciones: {', '.join(REASONS)}")

    from backend.inventory import service as inv_svc

    bodega = bodega or "principal"
    existing = inv_svc.get_stock(tenant_id, sku, bodega=bodega)
    if not existing:
        raise ValueError(f"SKU '{sku}' no encontrado en inventario (bodega '{bodega}')")

    current = float(existing["stock_actual"] or 0)
    if quantity > current:
        raise ValueError(
            f"La cantidad ({quantity}) excede el stock actual ({current}) de '{sku}'"
        )

    unit_cost = existing.get("costo_unitario")
    cost_total = round(quantity * float(unit_cost), 2) if unit_cost is not None else None

    occurred_at = occurred_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    # 1. Stock: decrement through the same UPDATE path reception_service uses
    # to increment it (never a parallel field) so the semáforo stays accurate.
    # The WHERE clause re-checks stock_actual >= quantity atomically — the
    # early guard above is just a friendly error for the common case; without
    # this conditional UPDATE, two concurrent mermas could both pass the
    # Python-side check against the same stale read and drive stock negative.
    updated = query_one(
        """UPDATE inventory_stock
           SET stock_actual = stock_actual - %s, updated_at = NOW()
           WHERE tenant_id = %s AND sku = %s AND bodega = %s AND stock_actual >= %s
           RETURNING stock_actual""",
        (quantity, tenant_id, sku, bodega, quantity),
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
            "INSERT INTO inventory_snapshots (tenant_id, sku, stock_actual) VALUES (%s, %s, %s)",
            (tenant_id, sku, updated["stock_actual"]),
        )
    except Exception as e:
        log.warning("merma snapshot failed sku=%s: %s", sku, e)

    # 3. Record the merma event with its accumulated cost.
    row = query_one(
        """INSERT INTO inventory_mermas
               (tenant_id, sku, bodega, quantity, reason, costo_unitario, costo_total,
                notes, created_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (tenant_id, sku, bodega, quantity, reason, unit_cost, cost_total,
         notes, user_id, occurred_at),
    )

    log.info(
        "[merma] tenant=%s sku=%s bodega=%s qty=%s reason=%s costo_total=%s",
        tenant_id, sku, bodega, quantity, reason, cost_total,
    )
    return row


def list_mermas(tenant_id: str, sku: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Recent merma events, most recent first. Basis for the future monthly summary."""
    if sku:
        return query(
            """SELECT * FROM inventory_mermas
               WHERE tenant_id = %s AND sku = %s
               ORDER BY created_at DESC LIMIT %s""",
            (tenant_id, sku, limit),
        )
    return query(
        """SELECT * FROM inventory_mermas
           WHERE tenant_id = %s
           ORDER BY created_at DESC LIMIT %s""",
        (tenant_id, limit),
    )
