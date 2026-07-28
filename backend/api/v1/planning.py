"""
Active planning view API (multi-period planning, Phase B).

GET /planning  — the active {period, horizon}, the family's available periods,
                 the reach cap for the current period, and the resolved active
                 session id (for the frontend's default). Any authenticated role.
PUT /planning  — set {period, horizon}. Admin-only.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth.guards import CurrentUser, get_current_user, require_admin
from backend.errors import AppError
from backend.schemas.common import ok
from backend.sessions import planning_service as plan

router = APIRouter(prefix="/planning", tags=["planning"])


class PlanningUpdate(BaseModel):
    period:  str = Field(..., description="daily | weekly | monthly")
    horizon: int = Field(..., ge=1, le=90)


@router.get("")
def get_planning(user: CurrentUser = Depends(get_current_user)):
    data = plan.get_planning(user.tenant_id)
    data["active_session_id"] = plan.resolve_active_session(user.tenant_id)
    return ok(data)


@router.put("")
def put_planning(
    body: PlanningUpdate,
    user: CurrentUser = Depends(require_admin),
):
    try:
        data = plan.set_planning(user.tenant_id, body.period, body.horizon)
    except AppError:
        # Already carries its status, machine code and params — re-raise it
        # untouched. Wrapping it in an HTTPException here would flatten it to
        # English prose and the admin would keep reading English.
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ok(data)
