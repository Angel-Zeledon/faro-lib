"""The daily operator digest: what failed on this installation in the last 24h.

Stability §14.g. `/health` carries service state and loop freshness, and every
tenant has `/actividad` — but when a training fails at 3 a.m. on a customer's
own server, nobody is looking at either. This is the cheap answer the owner
approved on 2026-09-30: once a day, one email over the mail channel that
already exists, to the people who operate the installation, listing what broke
across ALL tenants. It is not error aggregation (no stack traces, no grouping,
no alerting within the hour) — it is "somebody finds out by lunch".

Four sources, all of them records the app already keeps:

  * training jobs that ended FAILED (`jobs`), with tenant and session names;
  * scheduled retrain triggers that could not fire (`scheduled_jobs.last_error`);
  * background loops whose last pass was skipped or failed (`system_loop_runs`);
  * tenant events that are `critical`, or a delivery recorded as `failed`
    (`activity_logs`) — minus `training.failed`, which the jobs list already
    carries with more detail.

Who gets it: `INSTANCE_ADMIN_EMAILS`, or — on a deployment that named nobody
and has exactly one tenant — that tenant's active admins, the same implicit
operator `service_config.access` grants the configuration panel to.

Rules, each one a silent failure this module refuses to have:

  * A quiet day sends nothing, but the pass is still recorded (`completed`),
    so "no email" is distinguishable from "the digest never ran".
  * No operator, or no mail transport, is a `skipped` pass with the reason
    written to `system_loop_runs.last_error` (visible in `/health`) and a
    warning in the log — never a crash and never a pass that looks healthy.
  * One pass per day boundary. The window is `(boundary - 24h, boundary]`, so
    consecutive passes tile the timeline with no gap and no overlap, and a
    restart that reaches the same boundary finds it recorded and sends nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from backend.db.connection import query
from backend.workers import loop_state

log = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)

# Loop statuses worth an operator's attention. `first_boot` is a skip by
# design (a fresh install does not fire its digest on boot), not a failure.
_BAD_LOOP_STATUSES = (loop_state.STATUS_SKIPPED, loop_state.STATUS_FAILED)
_BENIGN_LOOP_ERRORS = ("first_boot",)

_ERROR_EXCERPT_CHARS = 300

# Reasons a pass is recorded as skipped. English identifiers: they land in
# `/health` and the log, both read by the operator, never by a tenant.
SKIP_NO_OPERATORS = "no_operators"
SKIP_EMAIL_NOT_CONFIGURED = "email_not_configured"


def operator_recipients() -> list[str]:
    """The addresses the digest goes to, or [] when the deployment has none.

    Mirrors `service_config.access.is_instance_operator`: the explicit list
    wins; without one, a single-tenant deployment's active admins are its
    operators; with two or more tenants and no list, nobody is.
    """
    from backend.service_config import access

    explicit = access.operator_emails()
    if explicit:
        return explicit
    tenant_id = access.sole_tenant_id()
    if tenant_id is None:
        return []
    rows = query(
        "SELECT email FROM users WHERE tenant_id = %s AND role = 'admin' "
        "AND status = 'active' ORDER BY created_at",
        (tenant_id,),
    ) or []
    return [r["email"].strip().lower() for r in rows if (r.get("email") or "").strip()]


def _excerpt(text: Any) -> str:
    value = " ".join(str(text or "").split())
    if len(value) > _ERROR_EXCERPT_CHARS:
        value = value[: _ERROR_EXCERPT_CHARS - 1] + "…"
    return value


def collect_failures(window_start: datetime, window_end: datetime) -> dict[str, list[dict]]:
    """Every failure the app recorded in `(window_start, window_end]`, all tenants."""
    jobs = query(
        """SELECT j.id AS job_id, j.tenant_id, t.name AS tenant_name,
                  j.session_id, s.name AS session_name, j.error,
                  COALESCE(j.completed_at, j.started_at, j.created_at) AS failed_at
             FROM jobs j
             LEFT JOIN tenants  t ON t.id = j.tenant_id
             LEFT JOIN sessions s ON s.id = j.session_id
            WHERE j.status = 'FAILED'
              AND COALESCE(j.completed_at, j.started_at, j.created_at) > %s
              AND COALESCE(j.completed_at, j.started_at, j.created_at) <= %s
            ORDER BY failed_at""",
        (window_start, window_end),
    ) or []

    schedules = query(
        """SELECT sj.id AS schedule_id, sj.tenant_id, t.name AS tenant_name,
                  sj.session_id, s.name AS session_name,
                  sj.last_error AS error, sj.last_error_at AS failed_at
             FROM scheduled_jobs sj
             LEFT JOIN tenants  t ON t.id = sj.tenant_id
             LEFT JOIN sessions s ON s.id = sj.session_id
            WHERE sj.last_error IS NOT NULL
              AND sj.last_error_at > %s AND sj.last_error_at <= %s
            ORDER BY sj.last_error_at""",
        (window_start, window_end),
    ) or []

    loops = query(
        """SELECT loop, last_boundary, last_run_at, last_status, last_error
             FROM system_loop_runs
            WHERE loop <> %s
              AND last_status = ANY(%s)
              AND COALESCE(last_error, '') <> ALL(%s)
              AND last_run_at > %s AND last_run_at <= %s
            ORDER BY loop""",
        (loop_state.OPERATOR_DIGEST, list(_BAD_LOOP_STATUSES),
         list(_BENIGN_LOOP_ERRORS), window_start, window_end),
    ) or []

    events = query(
        """SELECT a.tenant_id, t.name AS tenant_name, a.action, a.resource,
                  a.status, a.context, a.created_at AS failed_at
             FROM activity_logs a
             LEFT JOIN tenants t ON t.id = a.tenant_id
            WHERE (a.context->>'severity' = 'critical' OR a.status = 'failed')
              AND a.action <> 'training.failed'
              AND a.created_at > %s AND a.created_at <= %s
            ORDER BY a.created_at""",
        (window_start, window_end),
    ) or []

    return {
        "jobs": [{**r, "error": _excerpt(r.get("error"))} for r in jobs],
        "schedules": [{**r, "error": _excerpt(r.get("error"))} for r in schedules],
        "loops": [{**r, "last_error": _excerpt(r.get("last_error"))} for r in loops],
        "events": [{
            **r,
            "detail": _excerpt(
                ((r.get("context") or {}).get("reason_params") or {}).get("detail")
                or (r.get("context") or {}).get("reason")
                or r.get("resource")
            ),
        } for r in events],
    }


def failure_count(failures: dict[str, list[dict]]) -> int:
    return sum(len(v) for v in failures.values())


def run_operator_digest(boundary: datetime) -> dict:
    """Build and send the digest for the 24h ending at `boundary`, once.

    Records the pass in `system_loop_runs` under `loop_state.OPERATOR_DIGEST`
    whatever the outcome, and returns a summary for the caller's log:
    `{"status", "items", "sent", "reason"}`.
    """
    from backend.notifications import email as email_mod

    done = loop_state.last_boundary(loop_state.OPERATOR_DIGEST)
    if done is not None and done >= boundary:
        log.info("Operator digest: boundary %s already handled (last=%s) — not resending",
                 boundary.isoformat(), done.isoformat())
        return {"status": "already_done", "items": 0, "sent": 0, "reason": None}

    recipients = operator_recipients()
    reason = None
    if not recipients:
        reason = SKIP_NO_OPERATORS
        log.warning(
            "Operator digest skipped: no instance operator to send it to. Set "
            "INSTANCE_ADMIN_EMAILS (a multi-tenant deployment has no implicit "
            "operator) — failures on this installation reach nobody until then.")
    elif not email_mod.is_configured():
        reason = SKIP_EMAIL_NOT_CONFIGURED
        log.warning(
            "Operator digest skipped: no email transport is configured "
            "(RESEND_API_KEY or SMTP_USER/SMTP_PASS) — failures on this "
            "installation reach nobody until one is.")
    if reason:
        loop_state.mark_run(loop_state.OPERATOR_DIGEST, boundary,
                            status=loop_state.STATUS_SKIPPED, error=reason)
        return {"status": loop_state.STATUS_SKIPPED, "items": 0, "sent": 0, "reason": reason}

    window_start = boundary - WINDOW
    failures = collect_failures(window_start, boundary)
    items = failure_count(failures)
    if items == 0:
        log.info("Operator digest: no failures between %s and %s — nothing sent",
                 window_start.isoformat(), boundary.isoformat())
        loop_state.mark_run(loop_state.OPERATOR_DIGEST, boundary)
        return {"status": loop_state.STATUS_COMPLETED, "items": 0, "sent": 0, "reason": None}

    sent = 0
    for to in recipients:
        if email_mod.send_operator_digest_email(to, failures, window_start, boundary):
            sent += 1
    if sent == len(recipients):
        log.info("Operator digest: %d failure(s) mailed to %d operator(s)", items, sent)
        loop_state.mark_run(loop_state.OPERATOR_DIGEST, boundary)
        return {"status": loop_state.STATUS_COMPLETED, "items": items, "sent": sent,
                "reason": None}

    # Recorded, not retried: the addresses that did receive it must not get a
    # second copy on the next restart. The failure is visible in /health.
    reason = f"send_failed:{len(recipients) - sent}/{len(recipients)}"
    log.error("Operator digest: %d failure(s), delivered to only %d of %d operator(s)",
              items, sent, len(recipients))
    loop_state.mark_run(loop_state.OPERATOR_DIGEST, boundary,
                        status=loop_state.STATUS_FAILED, error=reason)
    return {"status": loop_state.STATUS_FAILED, "items": items, "sent": sent, "reason": reason}
