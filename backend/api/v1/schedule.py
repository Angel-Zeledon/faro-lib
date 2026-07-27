import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from backend.auth.guards import CurrentUser, get_current_user
from backend.db.connection import execute, query_one
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.errors import AppError
from backend.schemas.common import ok
from backend.sessions import service as session_svc

router = APIRouter(
    tags=["schedule"],
    dependencies=[Depends(require_feature(Feature.SCHEDULED_REPORTS))],
)
log = logging.getLogger(__name__)

CRON_PRESETS = {
    "0 6 * * 1":  "Every Monday at 6am",
    "0 0 * * *":  "Every day at midnight",
    "0 * * * *":  "Every hour",
    "0 8 * * 0":  "Every Sunday at 8am",
    "0 6 * * 1-5": "Weekdays at 6am",
    "0 0 1 * *":  "First day of month",
}


class SaveScheduleRequest(BaseModel):
    cron_expr: str
    enabled:   bool = True

    @field_validator("cron_expr")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError("cron_expr must have exactly 5 fields (e.g. '0 6 * * 1')")
        return v.strip()


def _next_run(cron_expr: str) -> datetime:
    from datetime import timezone
    try:
        from croniter import croniter
        return croniter(cron_expr, datetime.now(timezone.utc)).get_next(datetime)
    except Exception:
        # croniter may not be installed yet — fall back to 24h from now
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(hours=24)


@router.get("/sessions/{session_id}/schedule")
def get_schedule(session_id: str, user: CurrentUser = Depends(get_current_user)):
    if not session_svc.get_session(user.tenant_id, session_id):
        raise AppError("session_not_found", "Session not found", status_code=404)
    # last_run / last_error / last_error_at come along so the UI can tell a
    # healthy schedule from one whose trigger has been failing for weeks —
    # same contract as integration_connections.last_error.
    row = query_one(
        "SELECT id, session_id, cron_expr, next_run, enabled, last_run, last_error, last_error_at "
        "FROM scheduled_jobs WHERE session_id = %s AND tenant_id = %s",
        (session_id, user.tenant_id),
    )
    if not row:
        raise AppError(
            "schedule_not_configured",
            "No schedule configured for this session",
            status_code=404,
        )
    return ok(dict(row))


@router.post("/sessions/{session_id}/schedule")
def save_schedule(
    session_id: str,
    body: SaveScheduleRequest,
    user: CurrentUser = Depends(get_current_user),
):
    if not session_svc.get_session(user.tenant_id, session_id):
        raise AppError("session_not_found", "Session not found", status_code=404)
    next_run = _next_run(body.cron_expr)
    existing = query_one(
        "SELECT id FROM scheduled_jobs WHERE session_id = %s AND tenant_id = %s",
        (session_id, user.tenant_id),
    )
    if existing:
        execute(
            "UPDATE scheduled_jobs SET cron_expr=%s, next_run=%s, enabled=%s WHERE id=%s",
            (body.cron_expr, next_run, body.enabled, existing["id"]),
        )
        schedule_id = existing["id"]
    else:
        row = query_one(
            """INSERT INTO scheduled_jobs (id, tenant_id, session_id, cron_expr, next_run, enabled)
               VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s) RETURNING id""",
            (user.tenant_id, session_id, body.cron_expr, next_run, body.enabled),
        )
        schedule_id = row["id"] if row else None

    log.info("[schedule] saved session=%s cron=%s", session_id, body.cron_expr)
    return ok({
        "id":         schedule_id,
        "session_id": session_id,
        "cron_expr":  body.cron_expr,
        "next_run":   next_run.isoformat(),
        "enabled":    body.enabled,
    })


@router.delete("/sessions/{session_id}/schedule")
def delete_schedule(session_id: str, user: CurrentUser = Depends(get_current_user)):
    if not session_svc.get_session(user.tenant_id, session_id):
        raise AppError("session_not_found", "Session not found", status_code=404)
    execute(
        "DELETE FROM scheduled_jobs WHERE session_id = %s AND tenant_id = %s",
        (session_id, user.tenant_id),
    )
    return ok({"deleted": session_id})
