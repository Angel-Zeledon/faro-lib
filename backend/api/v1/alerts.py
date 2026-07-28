"""
Alert history — what the daily loop sent, readable inside the app.

The payload carries no prose: `kind`, `status` and `failure_reason` are stable
English machine values and `details` holds the numbers, so the Spanish sentence
is built once by the frontend's i18n layer (`alerts.*`) instead of being
hardcoded here. Same contract as the AppError envelope.
"""

from fastapi import APIRouter, Depends, Query

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.notifications import alert_history
from backend.schemas.common import ok

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
def list_alerts(
    user:  CurrentUser = Depends(get_current_user),
    limit: int         = Query(20, ge=1, le=100),
):
    """The tenant's most recent alerts, newest first, plus this user's unread
    count. A read — every role may see the history of what was sent to them."""
    return ok(alert_history.list_alerts(user.tenant_id, user.user_id, limit))


@router.post("/read")
def mark_alerts_read(user: CurrentUser = Depends(require_analyst_or_above)):
    """Mark every alert up to now as read for the calling user.

    It writes, so it takes the same guard as every other mutating endpoint. No
    alert is ever addressed to a viewer (recipients are admin/manager), so the
    role that cannot clear the badge is also the role that never has one.
    """
    return ok(alert_history.mark_read(user.tenant_id, user.user_id))
