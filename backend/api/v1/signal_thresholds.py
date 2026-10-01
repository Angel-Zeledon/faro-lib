"""
The semáforo's lead-time multipliers — read, configure, preview, reset.

GET    /inventory/signal-thresholds          any signed-in user (viewer included)
PUT    /inventory/signal-thresholds          analyst or admin
DELETE /inventory/signal-thresholds          analyst or admin (back to defaults)
POST   /inventory/signal-thresholds/preview  any signed-in user — read-only:
       runs the real semáforo with the candidate multipliers and reports how
       many products would change signal. Nothing is written.

The logic lives in `backend/inventory/signal_thresholds.py`; this file only
orchestrates. Scope: 'global' is the tenant-wide rule; 'supplier' and
'category' are overrides that beat it for the products they name.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.inventory import signal_thresholds as svc
from backend.schemas.common import ok

router = APIRouter(prefix="/inventory/signal-thresholds", tags=["inventory-signal-thresholds"])

ScopeType = Literal["global", "supplier", "category"]


class ThresholdsBody(BaseModel):
    scope_type: ScopeType = "global"
    scope_value: Optional[str] = None
    # Typed loosely on purpose: the service validates and answers with a
    # stable AppError code the frontend can translate, instead of FastAPI's
    # generic 422 shape.
    order_now_factor: Optional[float] = None
    overstock_factor: Optional[float] = None

    def factors(self) -> dict:
        return {
            "order_now_factor": self.order_now_factor,
            "overstock_factor": self.overstock_factor,
        }


class PreviewBody(ThresholdsBody):
    session_id: Optional[str] = None
    # True previews a RESET of this scope (the factors are ignored).
    reset: bool = False


@router.get("")
def get_thresholds(user: CurrentUser = Depends(get_current_user)):
    return ok(svc.get_signal_thresholds(user.tenant_id))


@router.put("")
def put_thresholds(
    body: ThresholdsBody,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    saved = svc.set_signal_thresholds(
        user.tenant_id, body.scope_type, body.scope_value, body.factors())
    return ok({"saved": saved, **svc.get_signal_thresholds(user.tenant_id)})


@router.delete("")
def reset_thresholds(
    scope_type: ScopeType = Query(default="global"),
    scope_value: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    cleared = svc.clear_signal_thresholds(user.tenant_id, scope_type, scope_value)
    return ok({"cleared": cleared, **svc.get_signal_thresholds(user.tenant_id)})


@router.post("/preview")
def preview_thresholds(
    body: PreviewBody,
    user: CurrentUser = Depends(get_current_user),
):
    from backend.sessions import planning_service

    session_id = body.session_id or planning_service.resolve_active_session(user.tenant_id)
    if not session_id:
        # Degrade out loud: no trained session means there is no semáforo to
        # preview against — say so instead of answering "0 products change".
        return ok({"available": False, "reason": "no_session"})
    period = planning_service.get_planning(user.tenant_id).get("period", "daily")
    result = svc.preview_signal_changes(
        user.tenant_id, session_id, period,
        body.scope_type, body.scope_value,
        None if body.reset else body.factors(),
    )
    return ok(result)
