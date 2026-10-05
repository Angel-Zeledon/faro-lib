"""
Demand plan versions with sign-off (see `inventory/demand_plan_service.py`).

Its own router and tag, INTERNAL in `api/public_surface.py`: approving a plan is
a person with the authority deciding, and "who approved this plan" must name one.

Every route requires company-wide access (`wscope.require_company_wide`): a
version sums every warehouse's forecast and every commitment, so a user limited
to some warehouses neither reads nor decides on it. A version is a record and a
measurement: nothing here changes a purchase recommendation.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.inventory import demand_plan_math as plan_math
from backend.inventory import demand_plan_service as svc
from backend.schemas.common import ok

router = APIRouter(tags=["demand-plans"])


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=svc.MAX_NAME_LENGTH)
    session_id: Optional[str] = Field(default=None, max_length=64)
    horizon_periods: Optional[int] = Field(default=None, ge=1, le=plan_math.MAX_HORIZON_PERIODS)
    note: Optional[str] = Field(default=None, max_length=svc.MAX_COMMENT_LENGTH)


class CommentBody(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=svc.MAX_COMMENT_LENGTH)


def _event(user: CurrentUser, action: str, version: dict, **extra) -> None:
    record_event(user.tenant_id, user.user_id, action, resource=version["id"],
                 details={"plan_name": version["name"], **extra})


@router.get("/demand-plans")
def list_plans(user: CurrentUser = Depends(get_current_user)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    people = svc.approvers(user.tenant_id)
    return ok({
        "items": svc.list_versions(user.tenant_id),
        "statuses": list(svc.STATUSES),
        "approver_count": len(people),
        "can_approve": any(a["id"] == user.user_id for a in people),
        "max_versions": svc.MAX_VERSIONS,
    })


@router.post("/demand-plans", status_code=201)
def create_plan(body: CreateBody, user: CurrentUser = Depends(require_analyst_or_above)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    version = svc.create_version(user.tenant_id, user.user_id, name=body.name,
                                 session_id=body.session_id,
                                 horizon_periods=body.horizon_periods, note=body.note)
    _event(user, "demand_plan.created", version, skus=version["sku_count"],
           periods=version["horizon_periods"])
    return ok(version)


@router.get("/demand-plans/diff")
def diff_plans(a: str = Query(..., max_length=64), b: str = Query(..., max_length=64),
               limit: int = Query(default=50, ge=1, le=plan_math.MAX_ROWS_RETURNED),
               user: CurrentUser = Depends(get_current_user)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    return ok(svc.diff(user.tenant_id, a, b, limit))


@router.get("/demand-plans/{version_id}")
def get_plan(version_id: str, user: CurrentUser = Depends(get_current_user)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    version = svc.get_version(user.tenant_id, version_id)
    people = svc.approvers(user.tenant_id)
    version["approver_count"] = len(people)
    version["can_approve"] = any(p["id"] == user.user_id for p in people)
    return ok(version)


@router.get("/demand-plans/{version_id}/lines")
def plan_lines(version_id: str, q: Optional[str] = Query(default=None, max_length=200),
               offset: int = Query(default=0, ge=0),
               limit: int = Query(default=50, ge=1, le=plan_math.MAX_ROWS_RETURNED),
               user: CurrentUser = Depends(get_current_user)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    return ok(svc.lines(user.tenant_id, version_id, q=q, offset=offset, limit=limit))


@router.get("/demand-plans/{version_id}/accuracy")
def plan_accuracy(version_id: str, dataset_id: Optional[str] = Query(default=None, max_length=64),
                  user: CurrentUser = Depends(get_current_user)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    return ok(svc.accuracy(user.tenant_id, version_id, dataset_id))


@router.post("/demand-plans/{version_id}/submit")
def submit_plan(version_id: str, body: CommentBody,
                user: CurrentUser = Depends(require_analyst_or_above)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    version = svc.transition(user.tenant_id, version_id, user.user_id, "submitted", body.comment)
    _event(user, "demand_plan.submitted", version)
    return ok(version)


@router.post("/demand-plans/{version_id}/approve")
def approve_plan(version_id: str, body: CommentBody,
                 user: CurrentUser = Depends(require_analyst_or_above)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    version = svc.transition(user.tenant_id, version_id, user.user_id, "approved", body.comment)
    _event(user, "demand_plan.approved", version, decision_comment=body.comment or "",
           superseded=len(version["superseded"]))
    return ok(version)


@router.post("/demand-plans/{version_id}/reject")
def reject_plan(version_id: str, body: CommentBody,
                user: CurrentUser = Depends(require_analyst_or_above)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    version = svc.transition(user.tenant_id, version_id, user.user_id, "rejected", body.comment)
    _event(user, "demand_plan.rejected", version, decision_comment=body.comment or "")
    return ok(version)


@router.post("/demand-plans/{version_id}/comments", status_code=201)
def comment_plan(version_id: str, body: CommentBody,
                 user: CurrentUser = Depends(require_analyst_or_above)):
    wscope.require_company_wide(user)  # company totals: not for a warehouse-scoped user
    version = svc.add_comment(user.tenant_id, version_id, user.user_id, body.comment or "")
    _event(user, "demand_plan.commented", version)
    return ok(version)
