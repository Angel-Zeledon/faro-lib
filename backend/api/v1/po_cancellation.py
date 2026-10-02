"""
Cancel a purchase order, and reopen it. Its own small router sharing the
`/inventory/po` prefix, like `po_payments.py`. See
`inventory/po_cancel_service.py` for the rules.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.auth.guards import CurrentUser, require_analyst_or_above
from backend.inventory import po_cancel_service as cancel_svc
from backend.inventory.roi_service import format_po_number
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory/po", tags=["inventory-cancellation"])


class CancelRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=cancel_svc.MAX_REASON_LENGTH)


@router.post("/{po_log_id}/cancel")
def cancel_po(
    po_log_id: str,
    body: Optional[CancelRequest] = None,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Cancel an order nothing was received against. Its units stop counting
    as on the way, so the semáforo may ask for them again; it leaves the
    overdue list and the payables calendar. Idempotent."""
    result = cancel_svc.cancel(user.tenant_id, po_log_id, user.user_id,
                               reason=body.reason if body else None)
    if result["changed"]:
        record_event(
            user.tenant_id, user.user_id, "purchase.order_cancelled",
            resource=po_log_id,
            details={"reference": format_po_number(result.get("po_number"), po_log_id),
                     "cancel_reason": result.get("cancel_reason")},
            reason="cancelled_by_user",
        )
    return ok(result)


@router.post("/{po_log_id}/uncancel")
def uncancel_po(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """The undo of cancel: the order counts as on the way again."""
    result = cancel_svc.uncancel(user.tenant_id, po_log_id, user.user_id)
    if result["changed"]:
        record_event(
            user.tenant_id, user.user_id, "purchase.order_uncancelled",
            resource=po_log_id,
            details={"reference": format_po_number(result.get("po_number"), po_log_id)},
            reason="reversed_by_user",
        )
    return ok(result)
