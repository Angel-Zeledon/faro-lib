"""
DB-backed FIFO job queue.
Dequeue uses ORDER BY created_at to preserve arrival order.
Enqueue is implicit: a job with status='QUEUED' is already in the queue.
"""

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


def remove(job_id: str) -> None:
    """Used when cancelling a QUEUED job before it starts."""
    execute(
        "UPDATE jobs SET status = 'CANCELLED', completed_at = NOW() WHERE id = %s AND status = 'QUEUED'",
        (job_id,),
    )


def peek() -> list[dict]:
    """Returns all QUEUED jobs (used by /health endpoint)."""
    return query("SELECT id AS job_id, tenant_id FROM jobs WHERE status = 'QUEUED' ORDER BY created_at")
