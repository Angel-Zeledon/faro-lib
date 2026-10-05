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
from backend.lineage.hashing import dataset_content_hash
from backend.sessions import service as session_svc
from backend.sessions.schedule_runs import (
    FAILED, LAUNCHED, REASON_LAUNCH_FAILED, REASON_NO_NEW_DATA, REASON_STILL_RUNNING,
    REASON_SOURCE_REFRESH_FAILED, SKIPPED, last_successful_hash, record_run,
)

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
        "AND status = 'COMPLETED' AND archived_at IS NULL "
        "ORDER BY updated_at DESC LIMIT 1",
        (tenant_id, schedule_id),
    )
    return row["id"] if row else None


def prune_previous_runs(tenant_id: str, schedule_id: str) -> int:
    """Archive the sessions this schedule created, except the one now serving.

    This is the schedule reusing its own slot rather than consuming a new one
    every night. It ARCHIVES, never deletes (sessions are permanent): an
    archived run leaves the working list and stops counting toward the
    saved-forecast ceiling, but its results stay recoverable from the library.
    It can only ever reach rows it created itself — a session a person made
    carries no `scheduled_job_id` — and it deliberately runs BEFORE the new run
    rather than after: archiving the serving session first would leave the
    product blank for as long as the training takes.
    """
    serving = _serving_session(tenant_id, schedule_id)
    rows = query(
        "SELECT id FROM sessions WHERE tenant_id = %s AND scheduled_job_id = %s "
        "AND status NOT IN %s AND archived_at IS NULL",
        (tenant_id, schedule_id, _IN_FLIGHT),
    ) or []
    doomed = [r["id"] for r in rows if r["id"] != serving]
    for session_id in doomed:
        try:
            session_svc.archive_session(tenant_id, session_id, "scheduler")
        except Exception as exc:  # noqa: BLE001
            # Not fatal: a slot that could not be freed costs a ceiling, and
            # failing the retrain over it would cost the forecast.
            log.warning("retrain prune: could not archive %s: %s", session_id, exc)
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


def _sql_parent(tenant_id: str, dataset_id: str) -> Optional[dict]:
    """The SQL source a snapshot dataset was materialized from, if any."""
    row = query_one(
        "SELECT p.id, p.name FROM datasets d JOIN datasets p "
        "ON p.id = d.parent_id AND p.tenant_id = d.tenant_id "
        "WHERE d.id = %s AND d.tenant_id = %s AND p.source_type = 'sql'",
        (dataset_id, tenant_id),
    )
    return dict(row) if row else None


# Failures that mean the CONNECTION is broken (not the query): the source's
# badge turns to "error" so the data screen says so too, not only the bell.
_CONNECTION_FAILURES = frozenset({
    "data_source_dns_failed", "data_source_host_unreachable", "data_source_connect_timeout",
    "data_source_tls_failed", "data_source_auth_failed", "data_source_host_rejected",
    "data_source_database_not_found", "data_source_credentials_unreadable",
    "data_source_host_not_allowed", "data_source_host_forbidden", "data_source_driver_missing",
    "data_source_connect_failed",
    "data_source_not_connected",
})


def _report_refresh_failure(tenant_id: str, user_id: str, parent: dict, exc: Exception) -> None:
    """Put a failed SQL refresh where the tenant looks: the bell (a critical
    activity event naming the source and the failure's code) and, when the
    connection itself is broken, the source's status. Never raises — the
    schedule's own failure must still be recorded by the caller."""
    code = getattr(exc, "code", None) or "data_source_connect_failed"
    try:
        from backend.activity.events import record_event
        record_event(
            tenant_id, user_id, "data.sql_refresh_failed", resource=parent.get("id"),
            details={"source_name": parent.get("name")},
            reason="sql_source_refresh_failed",
            reason_params={"source_name": parent.get("name"), "error_code": code},
            status="error",
        )
        if code in _CONNECTION_FAILURES:
            execute(
                "UPDATE datasets SET connection_status='error', updated_at=NOW() "
                "WHERE id=%s AND tenant_id=%s",
                (parent.get("id"), tenant_id),
            )
    except Exception:  # noqa: BLE001
        log.exception("retrain: could not report the refresh failure of %s", parent.get("id"))


def _current_dataset(
    tenant_id: str, schedule_id: str, template: dict, user_id: str,
) -> tuple[str, Optional[str]]:
    """The dataset this run trains on, and the id of a snapshot made just now.

    A template whose dataset came from a SQL source re-runs that source's saved
    query and trains on the fresh snapshot. A file dataset is used as it is:
    `POST /data-sources/{id}/file` replaces the file under the same id, so the
    schedule already sees new uploads. If the source cannot be refreshed the run
    FAILS with that reason — retraining on the stale snapshot would report a
    refresh that never happened.
    """
    dataset_id = template["dataset_id"]
    parent = _sql_parent(tenant_id, dataset_id)
    if not parent:
        return dataset_id, None
    old = query_one(
        "SELECT name FROM datasets WHERE id = %s AND tenant_id = %s",
        (dataset_id, tenant_id),
    )
    from backend.datasources.service import materialize_sql_source
    try:
        fresh = materialize_sql_source(
            tenant_id, user_id, parent["id"],
            name=(old or {}).get("name"),
        )
    except Exception as exc:
        exc._schedule_run_reason = REASON_SOURCE_REFRESH_FAILED  # type: ignore[attr-defined]
        _report_refresh_failure(tenant_id, user_id, parent, exc)
        raise
    fresh_id = fresh["id"]
    execute(
        "UPDATE datasets SET created_by_schedule_id = %s WHERE id = %s AND tenant_id = %s",
        (schedule_id, fresh_id, tenant_id),
    )
    return fresh_id, fresh_id


def _free_unreferenced_snapshots(tenant_id: str, schedule_id: str, keep: str) -> None:
    """Delete snapshots this schedule materialized earlier that no session
    uses any more, so a nightly SQL schedule does not pile up one dataset a day.
    Only rows it created itself (`created_by_schedule_id`) can be reached."""
    rows = query(
        "SELECT id FROM datasets WHERE tenant_id = %s AND created_by_schedule_id = %s "
        "AND id <> %s AND id NOT IN "
        "(SELECT dataset_id FROM sessions WHERE tenant_id = %s AND dataset_id IS NOT NULL)",
        (tenant_id, schedule_id, keep, tenant_id),
    ) or []
    from backend.datasources.service import delete_source
    for row in rows:
        try:
            delete_source(tenant_id, row["id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("retrain: could not free snapshot %s: %s", row["id"], exc)


RETRAIN_MODE_REFIT = "refit"
RETRAIN_MODE_REFORECAST = "reforecast"
RETRAIN_MODES = (RETRAIN_MODE_REFIT, RETRAIN_MODE_REFORECAST)


def retrain_mode(tenant_id: str, schedule_id: str) -> str:
    row = query_one(
        "SELECT retrain_mode FROM scheduled_jobs WHERE id = %s AND tenant_id = %s",
        (schedule_id, tenant_id),
    )
    mode = (row or {}).get("retrain_mode")
    return mode if mode in RETRAIN_MODES else RETRAIN_MODE_REFIT


def _launch_reforecasts(
    tenant_id: str, schedule_id: str, template_session_id: str,
    dataset_id: str, content_hash: Optional[str],
) -> Optional[dict]:
    """Re-forecast each grain of the serving family from its stored models, or
    return None when a full refit is the right answer (and say nothing: the
    caller then refits, which is the safe path).

    A full refit is chosen when there is nothing to derive from, when the models
    are `reforecast_full_refit_days` old or older, or when the schedule's last
    re-forecast FAILED — a refusal (the data drifted past what the stored models
    can answer for) would otherwise repeat every day until the age limit."""
    from backend.model_registry import reforecast_service as rf

    parents = rf.reforecast_parents_for_schedule(
        tenant_id, schedule_id, template_session_id)
    if not parents or any(rf.refit_due(p) for p in parents):
        return None
    last = query_one(
        "SELECT status FROM sessions WHERE tenant_id = %s AND scheduled_job_id = %s "
        "AND is_reforecast ORDER BY created_at DESC LIMIT 1",
        (tenant_id, schedule_id),
    )
    if last and last["status"] == "FAILED":
        log.info("Scheduled retrain %s: the last re-forecast failed - refitting", schedule_id)
        return None

    prune_previous_runs(tenant_id, schedule_id)
    launched = []
    for parent in parents:
        launched.append(rf.launch_reforecast(
            tenant_id, "scheduler", parent["id"], schedule_id=schedule_id,
            dataset_id=dataset_id, require_new_data=False, enforce_job_cap=False,
        ))
    record_run(
        tenant_id, schedule_id, LAUNCHED, session_id=launched[0]["session_id"],
        dataset_id=dataset_id, content_hash=content_hash,
        reason_params={"mode": "reforecast", "sessions": len(launched)},
    )
    log.info("Scheduled re-forecast launched: schedule=%s sessions=%s",
             schedule_id, [x["session_id"] for x in launched])
    return {"family_id": parents[0].get("family_id"), "mode": RETRAIN_MODE_REFORECAST,
            "sessions": launched, "base_job_id": launched[0]["job_id"]}


def launch_scheduled_retrain(
    tenant_id: str, schedule_id: str, template_session_id: str,
    user_id: str = "scheduler",
) -> Optional[dict]:
    """Run this schedule once and write what came of it to `schedule_runs`:
    launched, skipped (and why) or failed (and why). The error still
    propagates so the scheduler records `last_error` as before."""
    try:
        return _launch(tenant_id, schedule_id, template_session_id, user_id)
    except Exception as exc:
        record_run(
            tenant_id, schedule_id, FAILED,
            reason=getattr(exc, "_schedule_run_reason", REASON_LAUNCH_FAILED),
            reason_params={
                "detail": str(getattr(exc, "message", None) or exc)[:300],
            },
        )
        raise


def _launch(
    tenant_id: str, schedule_id: str, template_session_id: str, user_id: str,
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
        record_run(tenant_id, schedule_id, SKIPPED, reason=REASON_STILL_RUNNING,
                   session_id=already)
        return None

    template = _template(tenant_id, template_session_id)

    # Bring the data up to date BEFORE deciding anything. A SQL-backed schedule
    # used to retrain on the snapshot the wizard once materialized, forever:
    # `materialize_sql_source` makes a new dataset each run and nothing pointed
    # the schedule at it. Now the run re-materializes the source itself.
    dataset_id, fresh_dataset_id = _current_dataset(
        tenant_id, schedule_id, template, user_id)

    # Nothing new since the last successful run: say so, visibly, and stop. The
    # nightly preset over a file nobody touched used to retrain identical data
    # and replace the serving session with an equivalent one.
    content_hash = dataset_content_hash(tenant_id, dataset_id)
    if content_hash is not None and content_hash == last_successful_hash(
            tenant_id, schedule_id):
        record_run(
            tenant_id, schedule_id, SKIPPED, reason=REASON_NO_NEW_DATA,
            dataset_id=dataset_id, content_hash=content_hash,
        )
        if fresh_dataset_id:
            # The snapshot we just took is byte-identical to the one already
            # trained on; keeping it would only fill the datasets list.
            from backend.datasources.service import delete_source
            delete_source(tenant_id, fresh_dataset_id)
        log.info("Scheduled retrain skipped: no new data for schedule %s", schedule_id)
        return None

    # 'Re-forecast daily, refit periodically': while the models are young and the
    # last re-forecast did not fail, new data only advances the forecast from the
    # stored models. Anything that stops that (models too old, nothing stored, a
    # failed re-forecast) falls through to the full refit below.
    if retrain_mode(tenant_id, schedule_id) == RETRAIN_MODE_REFORECAST:
        reforecasted = _launch_reforecasts(
            tenant_id, schedule_id, template_session_id, dataset_id, content_hash)
        if reforecasted is not None:
            if fresh_dataset_id:
                _free_unreferenced_snapshots(tenant_id, schedule_id, keep=fresh_dataset_id)
            return reforecasted

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
        (dataset_id, schedule_id, run_id, tenant_id),
    )

    from backend.sessions import family_service as fam
    try:
        family = fam.launch_training_family(tenant_id, run_id, user_id)
    except Exception:
        # The run never started — a blocked data gate, a ceiling, a broken
        # dataset. The session it would have trained is of no use to anybody and
        # holding it costs a saved-forecast slot until the next trigger prunes
        # it, so it is archived now (kept, out of the working list and off the
        # ceiling). The error still propagates: the scheduler records it on
        # `scheduled_jobs.last_error` and the feed carries the reason.
        try:
            session_svc.archive_session(tenant_id, run_id, "scheduler")
        except Exception:  # noqa: BLE001
            log.warning("retrain: could not archive the failed run %s", run_id)
        raise

    record_run(
        tenant_id, schedule_id, LAUNCHED, session_id=run_id,
        dataset_id=dataset_id, content_hash=content_hash,
    )
    if fresh_dataset_id:
        _free_unreferenced_snapshots(tenant_id, schedule_id, keep=fresh_dataset_id)

    log.info("Scheduled retrain launched: schedule=%s template=%s run=%s",
             schedule_id, template_session_id, run_id)
    return family
