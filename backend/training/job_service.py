from datetime import datetime
from typing import Optional

from backend.db.connection import query_one, query, execute, _json
from backend.utils.ids import generate_id


def create_job(tenant_id: str, session_id: str, created_by: str) -> dict:
    job_id = generate_id("job")
    initial_progress = {"percent": 0, "step": "queued", "message": "Waiting for worker..."}
    execute(
        """INSERT INTO jobs
           (id, tenant_id, session_id, created_by, status, created_at, progress)
           VALUES (%s, %s, %s, %s, 'QUEUED', NOW(), %s)""",
        (job_id, tenant_id, session_id, created_by, _json(initial_progress)),
    )
    return get_job(tenant_id, job_id)


def get_job(tenant_id: str, job_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM jobs WHERE id = %s AND tenant_id = %s",
        (job_id, tenant_id),
    )


def list_jobs_for_session(tenant_id: str, session_id: str) -> list[dict]:
    return query(
        "SELECT * FROM jobs WHERE tenant_id = %s AND session_id = %s ORDER BY created_at DESC",
        (tenant_id, session_id),
    )


def mark_running(tenant_id: str, job_id: str, worker_id: str) -> dict:
    progress = {"percent": 5, "step": "starting", "message": "Worker picked up job"}
    execute(
        """UPDATE jobs SET status = 'RUNNING', started_at = NOW(),
           worker_id = %s, progress = %s
           WHERE id = %s AND tenant_id = %s""",
        (worker_id, _json(progress), job_id, tenant_id),
    )
    return get_job(tenant_id, job_id)


def mark_completed(tenant_id: str, job_id: str) -> dict:
    progress = {"percent": 100, "step": "done", "message": "Training complete"}
    execute(
        """UPDATE jobs SET status = 'COMPLETED', completed_at = NOW(), progress = %s
           WHERE id = %s AND tenant_id = %s""",
        (_json(progress), job_id, tenant_id),
    )
    return get_job(tenant_id, job_id)


def mark_failed(tenant_id: str, job_id: str, error: str) -> dict:
    progress = {"percent": 0, "step": "failed", "message": error[:200]}
    execute(
        """UPDATE jobs SET status = 'FAILED', completed_at = NOW(),
           error = %s, progress = %s
           WHERE id = %s AND tenant_id = %s""",
        (error, _json(progress), job_id, tenant_id),
    )
    return get_job(tenant_id, job_id)


def cancel_job(tenant_id: str, job_id: str) -> dict:
    j = get_job(tenant_id, job_id)
    if not j:
        raise ValueError("Job not found")
    if j["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
        raise ValueError(f"Cannot cancel job in status '{j['status']}'")
    execute(
        """UPDATE jobs SET status = 'CANCELLED', completed_at = NOW()
           WHERE id = %s AND tenant_id = %s""",
        (job_id, tenant_id),
    )
    return get_job(tenant_id, job_id)


def update_progress(tenant_id: str, job_id: str, progress: dict) -> None:
    execute(
        "UPDATE jobs SET progress = %s WHERE id = %s AND tenant_id = %s",
        (_json(progress), job_id, tenant_id),
    )


def count_active_jobs_for_tenant(tenant_id: str) -> int:
    from backend.db.connection import query_one as _qone
    row = _qone(
        "SELECT COUNT(*) AS cnt FROM jobs WHERE tenant_id = %s AND status IN ('QUEUED', 'RUNNING')",
        (tenant_id,),
    )
    return row["cnt"] if row else 0
