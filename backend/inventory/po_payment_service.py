"""
Whether the supplier's invoice for a purchase order has been paid.

The cash calendar (`cash_service.get_payables`) dates an invoice from the PO's
`sent_at` plus the supplier's credit days. Until 2026-10-01 nothing ever took a
PO OFF that calendar: there was no paid state, so an order sent in March was
still "overdue" in October, `overdue_total` only grew, and the affordability
check ended up answering "does not fit" to every cart (math audit O3). The buyer
had no way to say "this one is settled".

`paid_at` / `paid_by` on `inventory_po_log` are that statement. They are a
record of what the buyer told us, not of a bank movement — StockAI sees no
payments — which is why the action is explicit, attributed and reversible.

Granularity is the whole order. A PO can span several suppliers and the
calendar groups it per (PO, supplier), but every order screen in the product
is per PO, and a per-supplier paid flag would be a new concept nobody asked
for. Marking an order paid settles every supplier on it.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db.connection import query_one
from backend.errors import AppError

log = logging.getLogger(__name__)


def _iso(value) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _get_po(tenant_id: str, po_log_id: str) -> dict:
    po = query_one(
        "SELECT id, po_number, sent_at, paid_at, paid_by, cancelled_at "
        "FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
        (po_log_id, tenant_id),
    )
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    return po


def mark_paid(tenant_id: str, po_log_id: str, user_id: str) -> dict:
    """Record that the order's invoice was paid.

    **Only a sent order.** An unsent PO is a draft: no supplier has invoiced
    it, the cash calendar does not count it, and "paid" would describe money
    that was never owed. Refused with 409 rather than stored, so the flag can
    never sit on an order the calendar does not even show.

    **Idempotent, and the first payment date wins.** `paid_at IS NULL` in the
    WHERE clause: a double click, a retry after a timeout or two buyers acting
    at once all leave the ORIGINAL date and author in place, and the caller is
    told nothing changed (`changed: False`) instead of a second date silently
    replacing the first.
    """
    po = _get_po(tenant_id, po_log_id)
    if po.get("cancelled_at") is not None:
        raise AppError(
            "po_cancelled",
            "This order was cancelled; reopen it before marking it as paid",
            status_code=409,
        )
    if po.get("sent_at") is None:
        raise AppError(
            "po_paid_requires_sent",
            "Only an order that was sent to the supplier can be marked as paid",
            status_code=409,
        )

    updated = query_one(
        """UPDATE inventory_po_log
              SET paid_at = NOW(), paid_by = %s
            WHERE id = %s AND tenant_id = %s AND paid_at IS NULL
        RETURNING paid_at, paid_by""",
        (user_id, po_log_id, tenant_id),
    )
    if updated is None:
        current = _get_po(tenant_id, po_log_id)
        return {
            "po_log_id": po_log_id, "po_number": current.get("po_number"),
            "paid_at": _iso(current.get("paid_at")),
            "paid_by": current.get("paid_by"), "changed": False,
        }
    log.info("[po-payment] PAID tenant=%s po=%s by=%s", tenant_id, po_log_id, user_id)
    return {
        "po_log_id": po_log_id, "po_number": po.get("po_number"),
        "paid_at": _iso(updated["paid_at"]), "paid_by": updated["paid_by"],
        "changed": True,
    }


def mark_unpaid(tenant_id: str, po_log_id: str, user_id: str) -> dict:
    """The undo of `mark_paid`: the order is owed again and returns to the
    cash calendar on its original due date (which is derived from `sent_at`,
    never from the payment, so nothing else has to be restored).

    Idempotent the same way: an order that is not marked as paid is left
    alone and reported with `changed: False`.
    """
    po = _get_po(tenant_id, po_log_id)
    updated = query_one(
        """UPDATE inventory_po_log
              SET paid_at = NULL, paid_by = NULL
            WHERE id = %s AND tenant_id = %s AND paid_at IS NOT NULL
        RETURNING id""",
        (po_log_id, tenant_id),
    )
    changed = updated is not None
    if changed:
        log.info("[po-payment] UNPAID tenant=%s po=%s by=%s", tenant_id, po_log_id, user_id)
    return {
        "po_log_id": po_log_id, "po_number": po.get("po_number"),
        "paid_at": None, "paid_by": None, "changed": changed,
    }
