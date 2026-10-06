"""
The buyer's side of the supplier confirmation link: read the answers, accept a
proposed change, reopen a locked page, revoke a link. See
`inventory/po_confirmation_service.py` for the rules.

Accepting is the only act here that changes what drives purchasing (the accepted
promised date becomes the order's expected arrival), so it is analyst-or-above,
recorded under the person's name, and never happens on its own.
"""

from fastapi import APIRouter, Depends

from backend.activity.events import record_event
from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.errors import AppError
from backend.inventory import po_confirmation_service as svc
from backend.inventory import reception_service as rec_svc
from backend.inventory.roi_service import format_po_number
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory", tags=["inventory-po-confirmations"])


def _reference(tenant_id: str, po_log_id: str) -> str:
    po = rec_svc.get_po(tenant_id, po_log_id) or {}
    return format_po_number(po.get("po_number"), po_log_id)


@router.get("/po-confirmations")
def confirmations_summary(user: CurrentUser = Depends(get_current_user)):
    """One row per order that has a confirmation link: its overall status and how
    many proposed changes still wait for a decision. Feeds the history chips."""
    return ok(wscope.filter_po_rows(user, svc.summary(user.tenant_id), key="po_log_id"))


@router.get("/po/{po_log_id}/confirmations")
def po_confirmations(
    po_log_id: str,
    user: CurrentUser = Depends(get_current_user),
    _scope: None = Depends(wscope.po_guard),
):
    """Each supplier's link for this order with every line's latest answer."""
    if not rec_svc.get_po(user.tenant_id, po_log_id):
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    return ok(svc.list_for_po(user.tenant_id, po_log_id))


@router.post("/po/{po_log_id}/confirmations/{confirmation_id}/accept")
def accept_change(
    po_log_id: str,
    confirmation_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
    _scope: None = Depends(wscope.po_guard),
):
    """Accept the supplier's proposed date/quantity for one line. The promised
    date then replaces the model's expected arrival for that line; nothing else
    about the order changes. Idempotent."""
    result = svc.accept(user.tenant_id, po_log_id, confirmation_id, user.user_id)
    if result["changed"]:
        record_event(
            user.tenant_id, user.user_id, "purchase.supplier_change_accepted",
            resource=po_log_id,
            details={"reference": _reference(user.tenant_id, po_log_id),
                     "supplier": result["supplier"], "sku": result["sku"],
                     "promised_date": result["promised_date"]},
        )
    return ok(result)


@router.post("/po/{po_log_id}/confirmation-links/{request_id}/reopen")
def reopen_link(
    po_log_id: str,
    request_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
    _scope: None = Depends(wscope.po_guard),
):
    """Let the supplier answer again. The earlier answers stay as history."""
    result = svc.reopen(user.tenant_id, po_log_id, request_id, user.user_id)
    if result["changed"]:
        record_event(
            user.tenant_id, user.user_id, "purchase.supplier_link_reopened",
            resource=po_log_id,
            details={"reference": _reference(user.tenant_id, po_log_id),
                     "supplier": result["supplier"]},
        )
    return ok(result)


@router.post("/po/{po_log_id}/confirmation-links/{request_id}/revoke")
def revoke_link(
    po_log_id: str,
    request_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
    _scope: None = Depends(wscope.po_guard),
):
    """Kill the supplier's link now. Answers already given are kept."""
    result = svc.revoke(user.tenant_id, po_log_id, request_id, user.user_id)
    if result["changed"]:
        record_event(
            user.tenant_id, user.user_id, "purchase.supplier_link_revoked",
            resource=po_log_id,
            details={"reference": _reference(user.tenant_id, po_log_id),
                     "supplier": result["supplier"]},
        )
    return ok(result)
