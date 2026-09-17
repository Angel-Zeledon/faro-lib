"""A scheduled retrain, done so it cannot take the product down with it.

`workers/worker.py` used to call `create_job(tenant_id, session_id)` bare for
every due schedule. That one line had three consequences (stability 11.6):

1. **It retrained the session the whole app was reading.** `runner.py` marks a
   failed run's session FAILED, and `resolve_active_session` only ever returns a
   COMPLETED session — so an engine error at 3 a.m. left /hoy, the semáforo and
   the daily digest with nothing to read. The digest's `if not sid: continue`
   then wrote no row at all, so the only trace was `scheduled_jobs.last_error`,
   on a screen nobody opens because nothing announced a problem.
2. **It validated nothing.** The user-facing path (`api/v1/training.py`) checks
   the session's state, that the configs it needs exist, and the active-job cap.
   The scheduler checked none of them.
3. **It had no dedupe.** An hourly preset over a training that takes more than
   an hour queued B while A was still running, and both wrote results for one
   `session_id`.

What it does now, which is the owner's call of 2026-09-16 ("the most complete"):
each run **creates a new session** from the schedule's template and trains
that. The template — the session the user pointed the schedule at — is never
touched. A failed run therefore fails a session nobody is reading, and the app
keeps serving yesterday's numbers, which are numbers. A successful run becomes
the newest family and `resolve_active_session` switches to it on its own.

The slot economics are the reason this was a decision and not a fix: a saved
forecast is a plan ceiling (3 on free), and a daily schedule that keeps every
run fills it in three days. So the schedule REUSES its own slots — before each
run it removes the sessions it created itself except the one currently serving,
which bounds a schedule at two: what the buyer is reading, and what is training
to replace it. The template and every session a person created are out of
scope for that prune by construction: they carry no `scheduled_job_id`.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.sessions import service as session_svc

log = logging.getLogger(__name__)

# Copied from the template into each run. `dataset_ref` and `inspection` travel
# too: the engine reads them, and a run that re-derived them could silently
# train on a different shape than the session the user approved.
_COPIED_CONFIG_FIELDS = (
    "dataset_ref", "inspection", "columns_cfg", "features_cfg", "models_cfg",
    "validation_cfg", "business_cfg", "forecast_cfg", "granularity_cfg",
)

# A run of this schedule is already in flight while its session is in one of
# these.
_IN_FLIGHT = ("QUEUED", "RUNNING")

# The configs a training run cannot start without. Same two the user-facing
# endpoint refuses on, for the same reason.
_REQUIRED_CONFIG_FIELDS = ("columns_cfg", "models_cfg")


def in_flight_session(tenant_id: str, schedule_id: str) -> Optional[str]:
    """The session of this schedule's currently-running attempt, if any."""
    row = query_one(
        "SELECT id FROM sessions WHERE tenant_id = %s AND scheduled_job_id = %s "
        "AND status IN %s ORDER BY created_at DESC LIMIT 1",
        (tenant_id, schedule_id, _IN_FLIGHT),
    )
    return row["id"] if row else None


def _serving_session(tenant_id: str, schedule_id: str) -> Optional[str]:
    """The newest COMPLETED session this schedule produced — what the app is
    reading right now, and the one row the prune must not touch."""
    row = query_one(
        "SELECT id FROM sessions WHERE tenant_id = %s AND scheduled_job_id = %s "
        "AND status = 'COMPLETED' ORDER BY updated_at DESC LIMIT 1",
        (tenant_id, schedule_id),
    )
    return row["id"] if row else None


def prune_previous_runs(tenant_id: str, schedule_id: str) -> int:
    """Delete the sessions this schedule created, except the one now serving.

    This is the schedule reusing its own slot rather than consuming a new one
    every night. It can only ever reach rows it created itself — a session a
    person made carries no `scheduled_job_id` — and it deliberately runs BEFORE
    the new run rather than after: deleting the serving session first would
    leave the product blank for as long as the training takes.
    """
    serving = _serving_session(tenant_id, schedule_id)
    rows = query(
        "SELECT id FROM sessions WHERE tenant_id = %s AND scheduled_job_id = %s "
        "AND status NOT IN %s",
        (tenant_id, schedule_id, _IN_FLIGHT),
    ) or []
    doomed = [r["id"] for r in rows if r["id"] != serving]
    for session_id in doomed:
        try:
            session_svc.delete_session(tenant_id, session_id)
        except Exception as exc:  # noqa: BLE001
            # Not fatal: a slot that could not be freed costs a ceiling, and
            # failing the retrain over it would cost the forecast.
            log.warning("retrain prune: could not delete %s: %s", session_id, exc)
    return len(doomed)


def _template(tenant_id: str, session_id: str) -> dict:
    session = session_svc.get_session(tenant_id, session_id)
    if not session:
        raise AppError(
            "scheduled_retrain_template_missing",
            "The session this schedule retrains no longer exists.",
            status_code=404, params={"session_id": session_id},
        )
    if not session.get("dataset_id"):
        raise AppError(
            "scheduled_retrain_template_has_no_dataset",
            "The session this schedule retrains has no dataset attached.",
            status_code=422, params={"session_id": session_id},
        )
    missing = [f.replace("_cfg", "") for f in _REQUIRED_CONFIG_FIELDS
               if not session_store.get_field(tenant_id, session_id, f)]
    if missing:
        raise AppError(
            "scheduled_retrain_template_incomplete",
            f"The session this schedule retrains is missing configuration: {missing}.",
            status_code=422, params={"missing": ", ".join(missing)},
        )
    return session


def launch_scheduled_retrain(
    tenant_id: str, schedule_id: str, template_session_id: str,
    user_id: str = "scheduler",
) -> Optional[dict]:
    """Run this schedule once. Returns the new family, or None when skipped.

    Skipping is a normal outcome, not a failure: an hourly schedule over a
    two-hour training has nothing to do on the second hour, and queueing a
    second run would have both write results for one session.
    """
    already = in_flight_session(tenant_id, schedule_id)
    if already:
        log.info("Scheduled retrain skipped: %s still training for schedule %s",
                 already, schedule_id)
        return None

    template = _template(tenant_id, template_session_id)

    # Free the slot this schedule used last time before asking for another one,
    # so a plan ceiling counts what the schedule actually keeps.
    prune_previous_runs(tenant_id, schedule_id)

    from backend.entitlements.service import enforce_limit, limit_guard

    with limit_guard(tenant_id) as conn:
        enforce_limit(tenant_id, "max_sessions",
                      session_svc.count_sessions(tenant_id, conn=conn), conn=conn)
        run = session_svc.create_session(
            tenant_id, user_id, template["name"],
            description=template.get("description"),
            tags=template.get("tags") or [],
        )

    run_id = run["id"]
    for field in _COPIED_CONFIG_FIELDS:
        value = session_store.get_field(tenant_id, template_session_id, field)
        if value is not None:
            session_store.set_field(tenant_id, run_id, field, value)

    # The dataset, the schedule marker, and the state a launch is allowed from.
    # Written in one statement so a run can never exist half-linked: a session
    # with no dataset_id and a scheduled_job_id would be pruned as a previous
    # run before anybody noticed it had never trained.
    execute(
        """UPDATE sessions
           SET dataset_id = %s, scheduled_job_id = %s, status = 'MODELS_CONFIGURED',
               pipeline_step = 'train', updated_at = NOW()
           WHERE id = %s AND tenant_id = %s""",
        (template["dataset_id"], schedule_id, run_id, tenant_id),
    )

    from backend.sessions import family_service as fam
    try:
        family = fam.launch_training_family(tenant_id, run_id, user_id)
    except Exception:
        # The run never started — a blocked data gate, a ceiling, a broken
        # dataset. The session it would have trained is of no use to anybody and
        # holding it costs a saved-forecast slot until the next trigger prunes
        # it, so it goes now. The error still propagates: the scheduler records
        # it on `scheduled_jobs.last_error` and the feed carries the reason.
        try:
            session_svc.delete_session(tenant_id, run_id)
        except Exception:  # noqa: BLE001
            log.warning("retrain: could not clean up the failed run %s", run_id)
        raise

    log.info("Scheduled retrain launched: schedule=%s template=%s run=%s",
             schedule_id, template_session_id, run_id)
    return family
