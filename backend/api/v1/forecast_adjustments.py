"""
Manual forecast adjustments and their measured value
(see `inventory/forecast_adjustment_service.py`).

Its own router and tag: a person's judgement is recorded under a person's name,
so an API key does not make adjustments (the tag is INTERNAL in
`api/public_surface.py`).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.api.v1.forecasts import _require_completed
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.forecast_check import adjustment_value
from backend.inventory import forecast_adjustment_service as svc
from backend.schemas.common import ok

router = APIRouter(tags=["forecast-adjustments"])

_MAX_VALUE = 1e9


class AdjustmentBody(BaseModel):
    sku: str = Field(min_length=1, max_length=200)
    start_date: str
    end_date: str
    mode: str = Field(pattern="^(percent|absolute)$")
    # percent: +15 means 15% more demand. absolute: units added (negative removes)
    # across the whole period.
    value: float = Field(ge=-_MAX_VALUE, le=_MAX_VALUE)
    reason_code: str
    reason_note: Optional[str] = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


@router.get("/sessions/{session_id}/adjustments")
def list_adjustments(
    session_id: str,
    sku: Optional[str] = Query(default=None, max_length=200),
    include_superseded: bool = Query(default=False),
    user: CurrentUser = Depends(get_current_user),
):
    _require_completed(user.tenant_id, session_id)
    return ok({
        "reasons": list(svc.REASONS),
        "items": svc.list_for_session(user.tenant_id, session_id, sku, include_superseded),
    })


@router.post("/sessions/{session_id}/adjustments", status_code=201)
def create_adjustment(
    session_id: str,
    body: AdjustmentBody,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    _require_completed(user.tenant_id, session_id)
    row = svc.create(
        user.tenant_id, session_id, user.user_id,
        sku=body.sku, start_date=body.start_date, end_date=body.end_date,
        mode=body.mode, value=body.value, reason_code=body.reason_code,
        reason_note=body.reason_note,
    )
    record_event(
        user.tenant_id, user.user_id, "forecast.adjusted",
        resource=row["id"],
        details={"sku": row["sku"], "adjustment": f"{row['pct']:+.1f}%",
                 "adjustment_reason": row["reason_code"]},
    )
    return ok(row)


@router.get("/sessions/{session_id}/adjustments/value-added")
def adjustments_value_added(
    session_id: str,
    dataset_id: Optional[str] = Query(default=None, max_length=64),
    user: CurrentUser = Depends(get_current_user),
):
    """Did the adjustments beat the model's own forecast, now that sales arrived?"""
    session = _require_completed(user.tenant_id, session_id)
    return ok(adjustment_value.value_added(user.tenant_id, session, dataset_id))
