from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.activity import service as act_svc
from backend.auth.guards import CurrentUser, get_current_user
from backend.schemas.common import ok

router = APIRouter(prefix="/me/activity", tags=["activity"])


@router.get("")
def get_activity(
    user:   CurrentUser    = Depends(get_current_user),
    limit:  int            = Query(50,   ge=1, le=200),
    offset: int            = Query(0,    ge=0),
    action: Optional[str]  = Query(None),
):
    return ok(act_svc.get_logs(user.tenant_id, user.user_id, limit, offset, action))


@router.get("/action-types")
def get_action_types(user: CurrentUser = Depends(get_current_user)):
    return ok(act_svc.get_action_types(user.tenant_id, user.user_id))
