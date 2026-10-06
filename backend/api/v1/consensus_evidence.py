"""Refreshing the evidence the S&OP consensus accuracy report is graded on.

The consensus routes themselves (settings, submissions, versions, sign-off and the
accuracy report) are served by the Rust service, `backend-rs/src/routes/consensus.rs`.
This one route stays in Python because it reads the dataset files, which Rust does
not (`forecast_check/consensus_evidence.py`). Same tag as the Rust routes, INTERNAL
in `api/public_surface.py`.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.api.v1.forecasts import _require_completed
from backend.auth.guards import CurrentUser, require_analyst_or_above
from backend.forecast_check import consensus_evidence
from backend.schemas.common import ok

router = APIRouter(tags=["consensus"])


@router.post("/sessions/{session_id}/consensus/evidence/refresh")
def refresh_evidence(
    session_id: str,
    dataset_id: Optional[str] = Query(default=None, max_length=64),
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Copy the statistical forecast and the real sales of every graded SKU-period."""
    session = _require_completed(user.tenant_id, session_id)
    return ok(consensus_evidence.refresh(user.tenant_id, session, dataset_id))
