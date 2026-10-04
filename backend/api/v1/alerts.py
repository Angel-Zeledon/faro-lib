"""
What StockAI did, readable inside the app.

Two reads over one store:

* `GET /alerts` — the BELL. Scheduled sends plus the system events that need a
  decision (critical and warning). An interruption, so it is deliberately a
  subset: a bell that lists every successful import is a bell people stop
  reading.
* `GET /alerts/activity` — the full history, `info` rows included, filterable
  by kind and severity. The answer to "what happened while I was not looking"
  that is not a log file.

The payload carries no prose: `kind`, `action`, `severity`, `status`, `reason`
and `failure_reason` are stable English machine values and `details` /
`reason_params` hold the numbers, so the Spanish sentence is built once by the
frontend's i18n layer instead of being hardcoded here. Same contract as the
AppError envelope.
"""

from fastapi import APIRouter, Depends, Query

from backend.auth.guards import CurrentUser, get_current_user
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


@router.get("/activity")
def list_activity(
    user:     CurrentUser = Depends(get_current_user),
    limit:    int         = Query(50, ge=1, le=200),
    offset:   int         = Query(0, ge=0),
    kind:     str | None  = Query(None, description="One of GET /alerts/kinds"),
    severity: str | None  = Query(None, pattern="^(critical|warning|info)$"),
):
    """Everything the system did for this tenant, newest first.

    A read, and every role gets it: the point of the screen is that nobody has
    to ask what happened. Deliveries are NOT fan-out-grouped here — on the bell
    six rows for one digest is noise, on an audit screen the per-recipient
    outcome is exactly what is being audited.
    """
    return ok(alert_history.list_activity(
        user.tenant_id, limit=limit, offset=offset, kind=kind, severity=severity,
    ))


@router.get("/kinds")
def list_kinds(user: CurrentUser = Depends(get_current_user)):
    """The filter vocabulary, from the same registry the writers use, so the
    screen's filter cannot drift from what can actually be recorded."""
    return ok({"kinds": alert_history.activity_kinds()})


@router.post("/read")
def mark_alerts_read(user: CurrentUser = Depends(get_current_user)):
    """Mark every alert up to now as read for the calling user.

    Any signed-in role. It writes one row, and that row is the caller's own UI
    state — the same category as `POST /messages/read` ("marking your own
    unread count"), not company state.

    It used to require analyst-or-above, on the reasoning that no alert is ever
    addressed to a viewer. That stopped being true the moment the bell started
    carrying tenant-wide SYSTEM events — a failed training is not addressed to
    anybody in particular — so a viewer would have collected a badge with no
    way on earth to clear it.
    """
    return ok(alert_history.mark_read(user.tenant_id, user.user_id))
