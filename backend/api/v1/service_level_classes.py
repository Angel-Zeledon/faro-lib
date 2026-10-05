"""
Suggested service level per ABC class — read it, then accept it class by class.

GET  /inventory/service-level-classes         any signed-in user (viewer included)
POST /inventory/service-level-classes/apply   analyst or admin; audited

Nothing changes by itself: the GET only describes, the POST is the person
accepting one class's suggestion. The logic lives in
`backend/inventory/service_level_classes.py`; the classification math in
`backend/inventory/abc_xyz.py`.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from backend import audit
from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.inventory import service_level_classes as svc
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory/service-level-classes", tags=["inventory-service-level-classes"])


class ApplyBody(BaseModel):
    abc: str = Field(pattern="^[AaBbCc]$")
    session_id: Optional[str] = None


@router.get("")
def get_service_level_classes(
    session_id: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    wscope.require_company_wide(user)  # company-wide classification
    return ok(svc.get_suggestions(user.tenant_id, session_id))


@router.post("/apply")
def apply_service_level_class(
    body: ApplyBody,
    request: Request,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    wscope.require_company_wide(user)
    result = svc.apply_suggestion(user.tenant_id, body.abc, body.session_id)
    audit.note(
        request, target_id=result["abc"], label=f"service level class {result['abc']}",
        after={"abc": result["abc"], "service_level": result["service_level"],
               "updated": result["updated"], "kept_own_level": result["kept_own_level"]},
    )
    return ok(result)
