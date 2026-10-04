import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request

from backend import audit
from pydantic import BaseModel, field_validator

from backend.auth.guards import (
    CurrentUser, get_current_user, require_analyst_or_above,
)
from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.schemas.common import ok
from backend.sessions import service as session_svc

router = APIRouter(
    tags=["schedule"],
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
        # Five fields is not the same as five VALID fields: '0 99 * * 1' passed the
        # count check, croniter then raised inside _next_run, and the broad except
        # there turned it into "24 hours from now" — the schedule was accepted and
        # silently ran at an hour nobody asked for. Reject it here instead.
        try:
            from croniter import croniter
            croniter(v.strip())
        except ImportError:                                   # pragma: no cover
            pass                                              # see _next_run's fallback
        except Exception as exc:
            raise ValueError(f"cron_expr is not a valid cron expression: {exc}") from exc
        return v.strip()


def _next_run(cron_expr: str, tenant_id: str) -> datetime:
    """The next firing instant, with the cron read in the TENANT's timezone.

    Stored as UTC either way — the worker's due check compares instants and must
    not change. What changes is the reading: "0 6 * * 1" used to be evaluated in
    UTC while the picker calls it "cada lunes a las 6am", so a Costa Rican admin
    chose 6am and the screen answered "12:00 a.m.". Now 6am means 6am where the
    company is.
    """
    from datetime import timezone
    from backend.api.v1.timezone import zoneinfo_of

    tz = zoneinfo_of(tenant_id)
    try:
        from croniter import croniter
        local_next = croniter(cron_expr, datetime.now(tz)).get_next(datetime)
        # croniter returns a naive datetime in the frame of the base it was given.
        if local_next.tzinfo is None:
            local_next = local_next.replace(tzinfo=tz)
        return local_next.astimezone(timezone.utc)
    except Exception as exc:
        # Last resort only: the expression is validated by SaveScheduleRequest and
        # croniter is a hard dependency, so reaching this means something the
        # request could not foresee. It used to be silent, which is how a bad cron
        # became "tomorrow" without a word — say so, since the hour the user
        # picked is NOT the hour this will run at.
        from datetime import timedelta
        log.error("[schedule] could not read cron %r for tenant %s — falling back to "
                  "+24h, which is NOT the hour that was chosen: %s", cron_expr, tenant_id, exc)
        return datetime.now(timezone.utc) + timedelta(hours=24)


@router.get("/schedules")
def list_schedules(user: CurrentUser = Depends(get_current_user)):
    """Every schedule this tenant has, with the session's name.

    The screen was per-session only, and it opens on whichever session comes
    first: an admin whose retrain was armed on a DIFFERENT session saw the empty
    "create a schedule" form and had no way to know one existed — they could arm
    a second one without ever seeing the first. `last_run` / `last_error` ride
    along so a schedule that has been failing for weeks is visible from the list
    rather than only after selecting the right session.
    """
    rows = query(
        """SELECT j.id, j.session_id, s.name AS session_name, j.cron_expr,
                  j.next_run, j.enabled, j.last_run, j.last_error, j.last_error_at
             FROM scheduled_jobs j
             JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id
            WHERE j.tenant_id = %s
            ORDER BY j.next_run NULLS LAST""",
        (user.tenant_id,),
    )
    return ok([dict(r) for r in rows])


@router.get("/schedules/history")
def schedule_history(
    limit: int = 20,
    user: CurrentUser = Depends(get_current_user),
):
    """What the scheduler has actually done, newest first.

    `scheduled_jobs` keeps only `last_run` and `last_error` — the LAST one. After
    a month of nightly recalculation there was no way to see a schedule that had
    been failing intermittently, or that quietly stopped producing results while
    still reporting a healthy next run.

    No new table: a scheduled trigger already writes a row to `jobs` with
    `created_by = 'scheduler'`, which is the run itself, with its status, timing
    and error. This just reads it back.
    """
    limit = max(1, min(int(limit), 100))
    rows = query(
        """SELECT j.id, j.session_id, s.name AS session_name, j.status,
                  j.created_at, j.started_at, j.completed_at, j.error
             FROM jobs j
             JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id
            WHERE j.tenant_id = %s AND j.created_by = 'scheduler'
            ORDER BY j.created_at DESC
            LIMIT %s""",
        (user.tenant_id, limit),
    )
    entries = []
    for r in rows:
        e = dict(r)
        e["reason"] = None
        e["reason_params"] = {}
        entries.append(e)

    # Due but not trained (nothing new, still running) or unable to start (the
    # SQL source refused): there is no `jobs` row for these, and they are
    # exactly the runs a tenant wonders about. `launched` rows are the jobs above.
    extra = query(
        """SELECT r.id, r.session_id, COALESCE(s.name, t.name, '') AS session_name,
                  UPPER(r.outcome) AS status, r.ran_at AS created_at,
                  r.reason, r.reason_params
             FROM schedule_runs r
             LEFT JOIN scheduled_jobs j ON j.id = r.schedule_id AND j.tenant_id = r.tenant_id
             LEFT JOIN sessions t ON t.id = j.session_id AND t.tenant_id = r.tenant_id
             LEFT JOIN sessions s ON s.id = r.session_id AND s.tenant_id = r.tenant_id
            WHERE r.tenant_id = %s AND r.outcome <> 'launched'
            ORDER BY r.ran_at DESC LIMIT %s""",
        (user.tenant_id, limit),
    )
    for r in extra:
        e = dict(r)
        e.update(started_at=None, completed_at=None, error=None)
        entries.append(e)
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    return ok(entries[:limit])


@router.get("/sessions/{session_id}/schedule")
def get_schedule(session_id: str, user: CurrentUser = Depends(get_current_user)):
    if not session_svc.get_session(user.tenant_id, session_id):
        raise AppError("session_not_found", "Session not found", status_code=404)
    # last_run / last_error / last_error_at come along so the UI can tell a
    # healthy schedule from one whose trigger has been failing for weeks.
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
    request: Request,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    if not session_svc.get_session(user.tenant_id, session_id):
        raise AppError("session_not_found", "Session not found", status_code=404)
    next_run = _next_run(body.cron_expr, user.tenant_id)
    existing = query_one(
        "SELECT id, cron_expr, enabled FROM scheduled_jobs WHERE session_id = %s AND tenant_id = %s",
        (session_id, user.tenant_id),
    )
    audit.note(
        request,
        before=({"cron_expr": existing["cron_expr"], "enabled": existing["enabled"]}
                if existing else None),
        after={"cron_expr": body.cron_expr, "enabled": body.enabled},
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
def delete_schedule(session_id: str, request: Request,
                    user: CurrentUser = Depends(require_analyst_or_above)):
    if not session_svc.get_session(user.tenant_id, session_id):
        raise AppError("session_not_found", "Session not found", status_code=404)
    previous = query_one(
        "SELECT cron_expr, enabled FROM scheduled_jobs WHERE session_id = %s AND tenant_id = %s",
        (session_id, user.tenant_id),
    )
    if previous:
        audit.note(request, before={"cron_expr": previous["cron_expr"],
                                    "enabled": previous["enabled"]})
    execute(
        "DELETE FROM scheduled_jobs WHERE session_id = %s AND tenant_id = %s",
        (session_id, user.tenant_id),
    )
    return ok({"deleted": session_id})
