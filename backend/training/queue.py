"""
DB-backed FIFO job queue.
Dequeue uses ORDER BY created_at to preserve arrival order.
Enqueue is implicit: a job with status='QUEUED' is already in the queue.
"""

from typing import Optional

from backend.db.connection import query_one, execute


def enqueue(job_id: str, tenant_id: str) -> None:
    # No-op: job is already in the jobs table with status='QUEUED'
    pass


def dequeue() -> Optional[dict]:
    """Returns {job_id, tenant_id} of the oldest QUEUED job, or None."""
    row = query_one(
        """SELECT id AS job_id, tenant_id FROM jobs
           WHERE status = 'QUEUED'
           ORDER BY created_at
           LIMIT 1""",
    )
    return row


def remove(job_id: str) -> None:
    """Used when cancelling a QUEUED job before it starts."""
    execute(
        "UPDATE jobs SET status = 'CANCELLED', completed_at = NOW() WHERE id = %s AND status = 'QUEUED'",
        (job_id,),
    )


def peek() -> list[dict]:
    """Returns all QUEUED jobs (used by /health endpoint)."""
    from backend.db.connection import query
    return query("SELECT id AS job_id, tenant_id FROM jobs WHERE status = 'QUEUED' ORDER BY created_at")
