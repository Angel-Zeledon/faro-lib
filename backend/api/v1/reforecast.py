"""Re-forecast a session with newer sales, without retraining it.

`GET  /sessions/{id}/reforecast/status` — is the action on offer, and what it would do.
`POST /sessions/{id}/reforecast`        — create a NEW session derived from this one.

The tag is `training` on purpose: a re-forecast is a (much lighter) kind of run
and is classified for the public API exactly as training is.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.model_registry import reforecast_service
from backend.schemas.common import ok

router = APIRouter(tags=["training"])


class ReforecastRequest(BaseModel):
    # A newer snapshot of the same source, when the new sales live in a different
    # dataset than the one the session was trained on. Omitted: the session's own
    # dataset (whose file may have been replaced with a newer one).
    dataset_id: Optional[str] = None


@router.get("/sessions/{session_id}/reforecast/status")
def reforecast_status(session_id: str, user: CurrentUser = Depends(get_current_user)):
    """Whether a re-forecast with newer sales is on offer for this session: stored
    models present, newer data than the forecast used, and the refit age of the
    models. `reason` is a stable code when it is not."""
    return ok(reforecast_service.status(user.tenant_id, session_id))


@router.post("/sessions/{session_id}/reforecast", status_code=202)
def start_reforecast(
    session_id: str,
    body: Optional[ReforecastRequest] = None,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Create a NEW session that forecasts from this session's stored models over
    newer sales, without retraining. The source session is never modified. Counts
    against the plan's saved-forecast ceiling; runs on the job queue."""
    launched = reforecast_service.launch_reforecast(
        user.tenant_id, user.user_id, session_id,
        dataset_id=body.dataset_id if body else None,
    )
    return ok({**launched, "status": "QUEUED"})
