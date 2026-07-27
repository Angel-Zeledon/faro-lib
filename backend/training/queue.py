"""
DB-backed FIFO job queue.
Dequeue uses ORDER BY created_at to preserve arrival order.
Enqueue is implicit: a job with status='QUEUED' is already in the queue.
"""

import json
from typing import Callable, Optional

from backend.db.connection import query, query_one, execute


def enqueue(job_id: str, tenant_id: str) -> None:
    # No-op: job is already in the jobs table with status='QUEUED'
    pass


def dequeue(
    is_tenant_blocked: Optional[Callable[[str], bool]] = None,
) -> Optional[dict]:
    """Returns {job_id, tenant_id} of the oldest QUEUED job, or None.

    When `is_tenant_blocked` is given, jobs of tenants the predicate rejects
    (e.g. already at their plan's concurrent-job limit) are skipped: they stay
    QUEUED and are reconsidered on the next poll, while other tenants' jobs
    keep flowing in arrival order.
    """
    if is_tenant_blocked is None:
        return query_one(
            """SELECT id AS job_id, tenant_id FROM jobs
               WHERE status = 'QUEUED'
               ORDER BY created_at
               LIMIT 1""",
        )
    rows = query(
        """SELECT id AS job_id, tenant_id FROM jobs
           WHERE status = 'QUEUED'
           ORDER BY created_at""",
    )
    blocked: dict[str, bool] = {}  # one predicate call per tenant per poll
    for row in rows:
        tenant_id = row["tenant_id"]
        if tenant_id not in blocked:
            blocked[tenant_id] = bool(is_tenant_blocked(tenant_id))
        if not blocked[tenant_id]:
            return row
    return None


def claim(worker_id: str, is_tenant_blocked: Optional[Callable[[str], bool]] = None) -> Optional[dict]:
    """Atomically take the oldest eligible QUEUED job and mark it RUNNING.

    Replaces dequeue()+get_job()+mark_running(), which was a read-then-write
    race: two workers could both SELECT the same QUEUED row, both find it still
    QUEUED, and both dispatch it — training the same session twice, each
    overwriting the other's results. A single dev worker hid it; a second
    replica would not.

    `FOR UPDATE SKIP LOCKED` is what makes concurrent workers take *different*
    jobs instead of queueing behind the same row.

    Blocked tenants (at their plan's concurrent-job limit) are excluded rather
    than skipped after the fact: the predicate is Python, so it runs first and
    its verdict is passed into the statement as an exclusion list. Those jobs
    stay QUEUED and are reconsidered next poll, exactly as before.
    """
    excluded: list[str] = []
    if is_tenant_blocked is not None:
        seen: dict[str, bool] = {}
        for row in query("SELECT DISTINCT tenant_id FROM jobs WHERE status = 'QUEUED'"):
            tid = row["tenant_id"]
            if tid not in seen:
                seen[tid] = bool(is_tenant_blocked(tid))
            if seen[tid]:
                excluded.append(tid)

    progress = json.dumps(
        {"percent": 5, "step": "starting", "message": "Worker picked up job"})
    return query_one(
        """UPDATE jobs SET status = 'RUNNING', started_at = NOW(),
                          worker_id = %s, progress = %s
           WHERE id = (
               SELECT id FROM jobs
               WHERE status = 'QUEUED' AND NOT (tenant_id = ANY(%s))
               ORDER BY created_at
               FOR UPDATE SKIP LOCKED
               LIMIT 1
           )
           RETURNING id AS job_id, tenant_id, session_id""",
        (worker_id, progress, excluded),
    )


def remove(job_id: str) -> None:
    """Used when cancelling a QUEUED job before it starts."""
    execute(
        "UPDATE jobs SET status = 'CANCELLED', completed_at = NOW() WHERE id = %s AND status = 'QUEUED'",
        (job_id,),
    )


def peek() -> list[dict]:
    """Returns all QUEUED jobs (used by /health endpoint)."""
    return query("SELECT id AS job_id, tenant_id FROM jobs WHERE status = 'QUEUED' ORDER BY created_at")
