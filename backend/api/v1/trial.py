"""POST /trial — a throwaway account for somebody looking from the landing.

Public on purpose: the visitor has no account yet. Everything that keeps it
from being abused lives in `backend/trial/service.py`.
"""

from fastapi import APIRouter, Request, status

from backend.api.v1.auth import _check_rate
from backend.errors import AppError
from backend.schemas.common import ok
from backend.trial import service as trial_svc

router = APIRouter(prefix="/trial", tags=["trial"])


def _client_address(request: Request) -> str:
    """Best effort. Behind Caddy and the Next proxy the socket peer is the
    proxy, so the first forwarded address is the closest thing to the visitor
    there is — and a client can forge it, which is why the installation-wide
    ceilings in the service are the real limit and this one is a speed bump."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"


@router.post("", status_code=status.HTTP_201_CREATED)
def create_trial(request: Request):
    try:
        _check_rate(
            f"trial:{_client_address(request)}",
            max_attempts=trial_svc.MAX_TRIALS_PER_ADDRESS,
            window_secs=24 * 3600,
        )
    except AppError:
        # Its own code: the generic one tells the visitor to wait a few
        # minutes, and this window is a day.
        raise AppError(
            "trial_limit_per_address",
            "Too many trial accounts from this connection today.",
            status_code=429,
            params={"max": trial_svc.MAX_TRIALS_PER_ADDRESS},
        )
    return ok(trial_svc.create_trial_account())
