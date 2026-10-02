"""
Cancelling a purchase order, and reopening it (owner's decision, 2026-10-01).

Before this an order the buyer abandoned had no way out: it counted as "on the
way" (`service.get_incoming_detail`) until somebody received it, so it held
every recommendation for its SKUs down by exactly its units; it sat on the
overdue-reception list and on the payables calendar forever.

`cancelled_at` / `cancelled_by` / `cancel_reason` on `inventory_po_log` are the
statement. Every reader filters on `cancelled_at IS NULL` at query time —
"on the way", overdue receptions, open orders per supplier, payables — so the
next read of any screen already follows, and reopening restores all of it with
nothing to rebuild.

Two refusals, both because the order then describes something that happened:

- **Goods were received** (any line with `received_qty > 0`, or a reception
  status of partial/received). Those units are on the shelf; cancelling would
  pretend they are not coming while they are already here. The buyer records
  what arrived and closes the rest by receiving it as such.
- **It is marked paid.** Money left for it; unmark the payment first.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db.connection import query_one
from backend.errors import AppError

log = logging.getLogger(__name__)

# Long enough for a sentence, short enough not to become a document store.
MAX_REASON_LENGTH = 500


def _iso(value) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _get_po(tenant_id: str, po_log_id: str) -> dict:
    po = query_one(
        """SELECT l.id, l.po_number, l.reception_status, l.paid_at,
                  l.cancelled_at, l.cancelled_by, l.cancel_reason,
                  COALESCE((SELECT SUM(COALESCE(i.received_qty, 0))
                              FROM inventory_po_items i
                             WHERE i.po_log_id = l.id), 0) AS received_units
             FROM inventory_po_log l
            WHERE l.id = %s AND l.tenant_id = %s""",
        (po_log_id, tenant_id),
    )
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    return po


def cancel(tenant_id: str, po_log_id: str, user_id: str,
           reason: Optional[str] = None) -> dict:
    """Cancel an order nothing was received against. Idempotent: an order
    already cancelled keeps its original date, author and reason and is
    reported with `changed: False`."""
    po = _get_po(tenant_id, po_log_id)
    if po.get("cancelled_at") is not None:
        return {
            "po_log_id": po_log_id, "po_number": po.get("po_number"),
            "cancelled_at": _iso(po["cancelled_at"]),
            "cancel_reason": po.get("cancel_reason"), "changed": False,
        }
    received = float(po.get("received_units") or 0)
    if received > 0 or po.get("reception_status") in ("partial", "received"):
        raise AppError(
            "po_cancel_after_reception",
            "Goods were already received against this order; record what "
            "arrived instead of cancelling it",
            status_code=409,
            params={"reception_status": po.get("reception_status"),
                    "received_units": received},
        )
    if po.get("paid_at") is not None:
        raise AppError(
            "po_cancel_after_payment",
            "This order is marked as paid; unmark the payment before cancelling it",
            status_code=409,
        )

    clean_reason = (reason or "").strip()[:MAX_REASON_LENGTH] or None
    updated = query_one(
        """UPDATE inventory_po_log
              SET cancelled_at = NOW(), cancelled_by = %s, cancel_reason = %s
            WHERE id = %s AND tenant_id = %s AND cancelled_at IS NULL
              AND paid_at IS NULL
        RETURNING cancelled_at""",
        (user_id, clean_reason, po_log_id, tenant_id),
    )
    if updated is None:
        # Lost a race with another cancel (or a payment): report what is there.
        current = _get_po(tenant_id, po_log_id)
        if current.get("cancelled_at") is None:
            raise AppError(
                "po_cancel_after_payment",
                "This order is marked as paid; unmark the payment before cancelling it",
                status_code=409,
            )
        return {
            "po_log_id": po_log_id, "po_number": current.get("po_number"),
            "cancelled_at": _iso(current["cancelled_at"]),
            "cancel_reason": current.get("cancel_reason"), "changed": False,
        }
    log.info("[po-cancel] CANCEL tenant=%s po=%s by=%s", tenant_id, po_log_id, user_id)
    return {
        "po_log_id": po_log_id, "po_number": po.get("po_number"),
        "cancelled_at": _iso(updated["cancelled_at"]),
        "cancel_reason": clean_reason, "changed": True,
    }


def uncancel(tenant_id: str, po_log_id: str, user_id: str) -> dict:
    """Reopen a cancelled order: it counts as on the way, overdue and owed
    again, exactly as before. Idempotent (`changed: False` when it was not
    cancelled)."""
    po = _get_po(tenant_id, po_log_id)
    updated = query_one(
        """UPDATE inventory_po_log
              SET cancelled_at = NULL, cancelled_by = NULL, cancel_reason = NULL
            WHERE id = %s AND tenant_id = %s AND cancelled_at IS NOT NULL
        RETURNING id""",
        (po_log_id, tenant_id),
    )
    changed = updated is not None
    if changed:
        log.info("[po-cancel] REOPEN tenant=%s po=%s by=%s", tenant_id, po_log_id, user_id)
    return {"po_log_id": po_log_id, "po_number": po.get("po_number"),
            "cancelled_at": None, "cancel_reason": None, "changed": changed}
