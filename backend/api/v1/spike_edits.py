"""
Spike exclusions: mark a past period of a product as a one-off
(see `inventory/spike_edit_service.py`).

Its own router and tag: a person's judgement about their history is recorded
under a person's name, so an API key does not make it (the tag is INTERNAL in
`api/public_surface.py`). Marking changes nothing now; the next training applies
it, and nothing here retrains.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.inventory import spike_edit_service as svc
from backend.schemas.common import ok

router = APIRouter(tags=["spike-edits"])


class SpikeEditBody(BaseModel):
    sku: str = Field(min_length=1, max_length=200)
    start_date: str
    end_date: str
    reason_code: str
    reason_note: Optional[str] = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


def _details(row: dict) -> dict:
    # ISO dates joined by "..": language-neutral, so the activity feed needs no copy.
    return {"sku": row["sku"], "period": f"{row['start_date']}..{row['end_date']}",
            "spike_reason": row["reason_code"]}


@router.get("/sessions/{session_id}/spike-edits")
def list_spike_edits(
    session_id: str,
    sku: Optional[str] = Query(default=None, max_length=200),
    include_reverted: bool = Query(default=False),
    user: CurrentUser = Depends(get_current_user),
):
    return ok({
        "reasons": list(svc.REASONS),
        "items": svc.list_for_session(user.tenant_id, session_id, sku, include_reverted),
    })


@router.post("/sessions/{session_id}/spike-edits", status_code=201)
def create_spike_edit(
    session_id: str,
    body: SpikeEditBody,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    row = svc.create(
        user.tenant_id, session_id, user.user_id,
        sku=body.sku, start_date=body.start_date, end_date=body.end_date,
        reason_code=body.reason_code, reason_note=body.reason_note,
    )
    record_event(user.tenant_id, user.user_id, "forecast.spike_excluded",
                 resource=row["id"], details=_details(row))
    return ok(row)


@router.post("/spike-edits/{spike_edit_id}/revert")
def revert_spike_edit(
    spike_edit_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    row = svc.revert(user.tenant_id, spike_edit_id, user.user_id)
    record_event(user.tenant_id, user.user_id, "forecast.spike_restored",
                 resource=row["id"], details=_details(row))
    return ok(row)
