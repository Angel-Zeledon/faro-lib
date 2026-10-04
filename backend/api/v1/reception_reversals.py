"""
The inverses of `receive_po` and `mark_po_sent`.

Both actions used to have no way back (docs/assistant-actions.md, section 0):
`receive_po` adds units to real stock and writes `supplier_lead_time_obs`,
which moves a supplier's learned lead time and scorecard; `mark_po_sent`
stamps `sent_at`, which anchors the cash calendar. That is exactly why the
WhatsApp assistant's `approve_po` and `register_reception` tools are
suspended in `backend/whatsapp/tools.py` — an action a WhatsApp "sí" could
trigger with no undo anywhere. This module is what closes that gap, so those
two inverses now exist (`reception_service.unreceive_po` /
`reception_service.unsend_po`) — it does NOT re-enable the write tools,
which is a separate decision left to the owner.

Kept as its own router rather than added to `backend/api/v1/inventory.py`
(mirrors `inventory_recommendation_log.py`'s own small router sharing the
`/inventory` prefix) so the PO endpoints stay in one place that this change
does not have to touch.
"""

from fastapi import APIRouter, Depends

from backend.activity.events import record_event
from backend.auth.guards import CurrentUser, require_analyst_or_above
from backend.inventory import reception_service as rec_svc
from backend.inventory.roi_service import format_po_number
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory/po", tags=["inventory-reversals"])


@router.post("/{po_log_id}/unreceive")
def unreceive_po(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Undo a reception: take the received units back out of stock, reset the
    PO line's `received_qty` and the order's `reception_status`, and remove
    the lead-time observation(s) that reception taught the supplier
    scorecard. Refuses (409, with the offending SKU/warehouse named) rather
    than go negative if some of the received units are no longer in stock.
    See `reception_service.unreceive_po` for the full reasoning.
    """
    po_before = rec_svc.get_po(user.tenant_id, po_log_id) or {}
    result = rec_svc.unreceive_po(user.tenant_id, po_log_id, user.user_id)

    record_event(
        user.tenant_id, user.user_id, "purchase.reception_undone",
        resource=po_log_id,
        details={
            "reference":  format_po_number(po_before.get("po_number"), po_log_id),
            "sku_count":  result["sku_count"],
            "units":      result["units_removed"],
            "warehouse":  po_before.get("destination_warehouse"),
        },
        reason="reversed_by_user",
    )
    return ok(result)


@router.post("/{po_log_id}/unsend")
def unsend_po(
    po_log_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Undo `mark_po_sent`: clear `sent_at` so the order stops counting as
    incoming stock and drops off the cash-payables calendar. Does NOT recall
    the email/WhatsApp message a prior `/send` may have delivered — that left
    the system and nothing can pull it back. Refuses once a reception exists
    against this PO (undo that first). See `reception_service.unsend_po`.
    """
    po_before = rec_svc.get_po(user.tenant_id, po_log_id) or {}
    result = rec_svc.unsend_po(user.tenant_id, po_log_id, user.user_id)

    record_event(
        user.tenant_id, user.user_id, "purchase.order_unsent",
        resource=po_log_id,
        details={
            "reference": format_po_number(po_before.get("po_number"), po_log_id),
        },
        reason="reversed_by_user",
    )
    return ok(result)
