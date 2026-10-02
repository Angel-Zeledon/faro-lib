"""
Mark a purchase order's invoice as paid, and undo it (math audit 2026-10-01, O3).

Its own small router sharing the `/inventory/po` prefix, the same way
`reception_reversals.py` is, so the PO endpoints in `inventory.py` are not
touched by this change. See `inventory/po_payment_service.py` for the model.
"""

from fastapi import APIRouter, Depends

from backend.activity.events import record_event
from backend.auth.guards import CurrentUser, require_analyst_or_above
from backend.inventory import po_payment_service as pay_svc
from backend.inventory.roi_service import format_po_number
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory/po", tags=["inventory-payments"])


@router.post("/{po_log_id}/mark-paid")
def mark_po_paid(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Take a sent order off the cash calendar: its invoice is settled.

    Idempotent — a second call keeps the first payment date and author and
    answers `changed: false`, and only a real change is written to the
    activity log, so a double click does not leave two "paid" rows.
    """
    result = pay_svc.mark_paid(user.tenant_id, po_log_id, user.user_id)
    if result["changed"]:
        record_event(
            user.tenant_id, user.user_id, "purchase.order_paid",
            resource=po_log_id,
            details={"reference": format_po_number(result.get("po_number"), po_log_id)},
        )
    return ok(result)


@router.post("/{po_log_id}/mark-unpaid")
def mark_po_unpaid(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """The undo of mark-paid: the order is owed again, on its original due
    date. Recorded as a warning (it puts money back on the calendar), with the
    same reason every other reversal carries."""
    result = pay_svc.mark_unpaid(user.tenant_id, po_log_id, user.user_id)
    if result["changed"]:
        record_event(
            user.tenant_id, user.user_id, "purchase.order_unpaid",
            resource=po_log_id,
            details={"reference": format_po_number(result.get("po_number"), po_log_id)},
            reason="reversed_by_user",
        )
    return ok(result)
