"""What a schedule did each time it was due.

`jobs` records runs that STARTED. A schedule that was due and chose not to
train (nothing new had arrived) or could not even begin (the SQL source
refused to materialize) left no row anywhere, so a tenant looking at the
history of a nightly schedule could not tell "healthy, nothing to do" from
"dead". This table is that missing half.

Outcomes: `launched`, `skipped`, `failed`. `reason` is an English code the
frontend renders through `schedule.run_reason.<code>` with `reason_params`.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.db.connection import _json, execute, query, query_one
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

LAUNCHED = "launched"
SKIPPED = "skipped"
FAILED = "failed"

REASON_NO_NEW_DATA = "no_new_data"
REASON_STILL_RUNNING = "still_running"
REASON_SOURCE_REFRESH_FAILED = "source_refresh_failed"
REASON_LAUNCH_FAILED = "launch_failed"


def record_run(
    tenant_id: str, schedule_id: str, outcome: str, *,
    reason: Optional[str] = None, reason_params: Optional[dict] = None,
    session_id: Optional[str] = None, dataset_id: Optional[str] = None,
    content_hash: Optional[str] = None,
) -> None:
    """Append one row. Never raises: the history is a record of the run, and
    losing it must not turn a launched retrain into a failed one."""
    try:
        execute(
            """INSERT INTO schedule_runs
               (id, tenant_id, schedule_id, outcome, reason, reason_params,
                session_id, dataset_id, content_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (generate_id("srun"), tenant_id, schedule_id, outcome, reason,
             _json(reason_params or {}), session_id, dataset_id, content_hash),
        )
    except Exception:  # noqa: BLE001
        log.exception("could not record schedule run for schedule=%s", schedule_id)


def last_successful_hash(tenant_id: str, schedule_id: str) -> Optional[str]:
    """Content hash the schedule last trained on to a COMPLETED session.

    A run whose session failed, or was deleted by a person, does not count:
    nothing is serving that data, so the next due run must train.
    """
    row = query_one(
        """SELECT r.content_hash
             FROM schedule_runs r
             JOIN sessions s ON s.id = r.session_id AND s.tenant_id = r.tenant_id
            WHERE r.tenant_id = %s AND r.schedule_id = %s
              AND r.outcome = 'launched' AND r.content_hash IS NOT NULL
              AND s.status = 'COMPLETED'
            ORDER BY r.ran_at DESC LIMIT 1""",
        (tenant_id, schedule_id),
    )
    return row["content_hash"] if row else None


def list_runs(tenant_id: str, limit: int = 20, offset: int = 0) -> list[dict]:
    rows = query(
        """SELECT id, schedule_id, ran_at, outcome, reason, reason_params,
                  session_id, dataset_id, content_hash
             FROM schedule_runs WHERE tenant_id = %s
            ORDER BY ran_at DESC LIMIT %s OFFSET %s""",
        (tenant_id, limit, offset),
    )
    return [dict(r) for r in rows]
