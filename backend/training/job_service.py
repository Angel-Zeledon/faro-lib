from typing import Optional

from backend.db.connection import query_one, query, execute, _json
from backend.utils.ids import generate_id


def create_job(tenant_id: str, session_id: str, created_by: str, conn=None) -> dict:
    """Insert a QUEUED job. `conn` is the connection of a `limit_guard` block
    when the insert must be atomic with a ceiling check (the daily training
    cap): the row is then visible to the next waiter's count at commit."""
    job_id = generate_id("job")
    initial_progress = {"percent": 0, "step": "queued", "message": "Waiting for worker..."}
    execute(
        """INSERT INTO jobs
           (id, tenant_id, session_id, created_by, status, created_at, progress)
           VALUES (%s, %s, %s, %s, 'QUEUED', NOW(), %s)""",
        (job_id, tenant_id, session_id, created_by, _json(initial_progress)),
        conn=conn,
    )
    return get_job(tenant_id, job_id) if conn is None else {"id": job_id}


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
    progress = {"percent": 0, "step": "starting", "message": "Worker picked up job"}
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
        """UPDATE jobs SET status = 'COMPLETED', completed_at = NOW(), progress = %s, error = NULL
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


def has_in_flight_job(tenant_id: str, session_id: str) -> bool:
    """True while a worker is going to write to this session.

    The session row does NOT answer this. `runner.py` only ever calls
    `force_status` with COMPLETED or FAILED, so a session that is training
    right now still reads QUEUED — the state machine's RUNNING state is
    reachable only by a test that writes it by hand. Anything that must refuse
    while training is in progress has to ask the job.
    """
    row = query_one(
        "SELECT 1 AS hit FROM jobs WHERE tenant_id = %s AND session_id = %s "
        "AND status IN ('QUEUED', 'RUNNING') LIMIT 1",
        (tenant_id, session_id),
    )
    return row is not None


# A job that has said nothing for this long is an orphan of a dead worker, not
# a training in progress; the indicator must not show it forever.
_ACTIVE_JOB_MAX_AGE_HOURS = 12


def list_active_training(tenant_id: str) -> list[dict]:
    """The tenant's training runs that are queued or running right now.

    One entry per *family* (a launch fans out into daily/weekly/... sessions,
    each with its own job). ``percent`` averages every member, a finished member
    counting as 100, so it matches what the wizard shows. The base member's
    stage and message are the ones reported because that is the session the
    user lands on.
    """
    active = query(
        """SELECT j.id AS job_id, j.session_id, s.family_id
           FROM jobs j JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id
           WHERE j.tenant_id = %s AND j.status IN ('QUEUED', 'RUNNING')
             AND j.created_at > NOW() - (%s * INTERVAL '1 hour')""",
        (tenant_id, _ACTIVE_JOB_MAX_AGE_HOURS),
    )
    if not active:
        return []
    family_ids = sorted({r["family_id"] or r["session_id"] for r in active})
    members = query(
        """SELECT DISTINCT ON (s.id)
                  s.id AS session_id, s.name, s.granularity,
                  COALESCE(s.family_id, s.id) AS family_id,
                  j.id AS job_id, j.status, j.progress, j.created_at
           FROM sessions s JOIN jobs j ON j.session_id = s.id AND j.tenant_id = s.tenant_id
           WHERE s.tenant_id = %s AND COALESCE(s.family_id, s.id) = ANY(%s)
           ORDER BY s.id, j.created_at DESC""",
        (tenant_id, family_ids),
    )
    families: dict[str, list[dict]] = {}
    for m in members:
        families.setdefault(m["family_id"], []).append(m)

    out = []
    for fam_id in family_ids:
        rows = families.get(fam_id) or []
        if not any(r["status"] in ("QUEUED", "RUNNING") for r in rows):
            continue
        # The base session is the one whose id is the family id.
        base = next((r for r in rows if r["session_id"] == fam_id), rows[0])
        pcts = []
        for r in rows:
            if r["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                pcts.append(100)
            else:
                p = (r.get("progress") or {}).get("percent")
                pcts.append(p if isinstance(p, (int, float)) else 0)
        progress = base.get("progress") or {}
        out.append({
            "family_id": fam_id,
            "base_session_id": base["session_id"],
            "base_job_id": base["job_id"],
            "name": base["name"],
            "status": "RUNNING" if any(r["status"] == "RUNNING" for r in rows) else "QUEUED",
            "percent": round(sum(pcts) / len(pcts)),
            "step": progress.get("step"),
            "message": progress.get("message"),
            "members": [
                {"job_id": r["job_id"], "session_id": r["session_id"],
                 "granularity": r["granularity"], "status": r["status"]}
                for r in rows
            ],
        })
    return out
