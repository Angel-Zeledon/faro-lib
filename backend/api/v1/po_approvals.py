"""
Purchase-order approval endpoints (opt-in; see `inventory/po_approval_service.py`).

* Admin configures the rules and who may approve.
* An analyst asks for approval on an order that needs it.
* An approver (a user an admin flagged) approves, or rejects with a reason.

Nothing here changes how orders behave for a tenant with no active rule.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.auth.guards import (
    CurrentUser, get_current_user, require_admin, require_analyst_or_above,
)
from backend.inventory import po_approval_service as svc
from backend.inventory.roi_service import format_po_number
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory", tags=["inventory-approvals"])

_MAX_MONEY = 1e12


class RuleBody(BaseModel):
    threshold: float = Field(gt=0, le=_MAX_MONEY)
    warehouse: Optional[str] = Field(default=None, max_length=120)
    supplier_id: Optional[str] = Field(default=None, max_length=64)
    self_approve_below: Optional[float] = Field(default=None, gt=0, le=_MAX_MONEY)


class RulePatch(BaseModel):
    threshold: Optional[float] = Field(default=None, gt=0, le=_MAX_MONEY)
    warehouse: Optional[str] = Field(default=None, max_length=120)
    supplier_id: Optional[str] = Field(default=None, max_length=64)
    self_approve_below: Optional[float] = Field(default=None, gt=0, le=_MAX_MONEY)
    active: Optional[bool] = None


class ApproverBody(BaseModel):
    can_approve: bool


class RequestBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=svc.MAX_COMMENT_LENGTH)


class DecisionBody(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=svc.MAX_COMMENT_LENGTH)


# ── Configuration (admin) ────────────────────────────────────────────────────

@router.get("/po-approval/settings")
def get_settings(user: CurrentUser = Depends(get_current_user)):
    """Rules and approvers, for the configuration card. Any signed-in person may
    read it (the PO screen needs to know whether the workflow is on); only an
    admin can change it."""
    rules = svc.list_rules(user.tenant_id)
    return ok({
        "rules": rules,
        "enabled": any(r["active"] for r in rules),
        "approvers": svc.list_approvers(user.tenant_id),
        "is_approver": svc.is_approver(user.tenant_id, user.user_id),
    })


@router.post("/po-approval/rules", status_code=201)
def create_rule(body: RuleBody, user: CurrentUser = Depends(require_admin)):
    return ok(svc.create_rule(user.tenant_id, user.user_id, body.model_dump()))


@router.patch("/po-approval/rules/{rule_id}")
def update_rule(rule_id: str, body: RulePatch, user: CurrentUser = Depends(require_admin)):
    # exclude_unset: an omitted field keeps its value; an explicit null on
    # warehouse / supplier / self-approval clears it.
    return ok(svc.update_rule(user.tenant_id, rule_id, body.model_dump(exclude_unset=True)))


@router.delete("/po-approval/rules/{rule_id}")
def delete_rule(rule_id: str, user: CurrentUser = Depends(require_admin)):
    svc.delete_rule(user.tenant_id, rule_id)
    return ok({"deleted": True})


@router.put("/po-approval/approvers/{user_id}")
def set_approver(user_id: str, body: ApproverBody,
                 user: CurrentUser = Depends(require_admin)):
    return ok(svc.set_approver(user.tenant_id, user_id, body.can_approve))


# ── The approver's inbox ─────────────────────────────────────────────────────

@router.get("/po-approval/pending")
def pending(user: CurrentUser = Depends(get_current_user)):
    return ok(svc.list_pending(user.tenant_id, user.user_id))


# ── Per order ────────────────────────────────────────────────────────────────

@router.get("/po/{po_log_id}/approval")
def get_approval(po_log_id: str, user: CurrentUser = Depends(get_current_user)):
    return ok(svc.describe(user.tenant_id, po_log_id, user.user_id))


@router.post("/po/{po_log_id}/approval/request")
def request_approval(po_log_id: str, body: Optional[RequestBody] = None,
                     user: CurrentUser = Depends(require_analyst_or_above)):
    result = svc.request_approval(user.tenant_id, po_log_id, user.user_id,
                                  note=body.note if body else None)
    if result["changed"]:
        po_row = result.get("history", [{}])[0]
        record_event(
            user.tenant_id, user.user_id, "purchase.approval_requested",
            resource=po_log_id,
            details={"reference": _reference(user.tenant_id, po_log_id),
                     "value": po_row.get("amount")},
        )
    return ok(result)


@router.post("/po/{po_log_id}/approval/approve")
def approve(po_log_id: str, body: Optional[DecisionBody] = None,
            user: CurrentUser = Depends(require_analyst_or_above)):
    return ok(_decide(user, po_log_id, "approved", body.comment if body else None))


@router.post("/po/{po_log_id}/approval/reject")
def reject(po_log_id: str, body: DecisionBody,
           user: CurrentUser = Depends(require_analyst_or_above)):
    return ok(_decide(user, po_log_id, "rejected", body.comment))


def _reference(tenant_id: str, po_log_id: str) -> str:
    from backend.db.connection import query_one
    row = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
                    (po_log_id, tenant_id)) or {}
    return format_po_number(row.get("po_number"), po_log_id)


def _decide(user: CurrentUser, po_log_id: str, decision: str, comment: Optional[str]) -> dict:
    result = svc.decide(user.tenant_id, po_log_id, user.user_id, decision, comment)
    if result["changed"]:
        record_event(
            user.tenant_id, user.user_id,
            "purchase.approval_approved" if decision == "approved"
            else "purchase.approval_rejected",
            resource=po_log_id,
            details={"reference": format_po_number(result.get("po_number"), po_log_id),
                     "value": result.get("amount"),
                     "decision_comment": result.get("comment")},
        )
    return result
