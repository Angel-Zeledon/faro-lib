from fastapi import APIRouter, Depends, HTTPException

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.config import settings
from backend.db import session_store
from backend.schemas.common import ok
from backend.sessions import service as session_svc
from backend.sessions.state_machine import PRE_TRAIN_STATES
from backend.training import job_service

router = APIRouter(tags=["training"])


@router.post("/sessions/{session_id}/train", status_code=202)
def start_training(
    session_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    if s["status"] not in PRE_TRAIN_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"Session must be in {sorted(PRE_TRAIN_STATES)} to start training. "
                   f"Current status: {s['status']}",
        )

    # Validate required configs are present in DB
    missing = []
    for field in ("columns_cfg", "models_cfg"):
        if not session_store.get_field(user.tenant_id, session_id, field):
            missing.append(field.replace("_cfg", ""))
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required configuration: {missing}. "
                   "Complete the wizard steps before training.",
        )

    # Rate limit: max 3 concurrent jobs per tenant (bypassed in testing mode)
    if not settings.testing_mode:
        active = job_service.count_active_jobs_for_tenant(user.tenant_id)
        if active >= 3:
            raise HTTPException(
                status_code=429,
                detail=f"Too many active training jobs ({active}). Wait for a job to finish before queuing another.",
            )

    job = job_service.create_job(user.tenant_id, session_id, user.user_id)
    session_svc.set_last_job(user.tenant_id, session_id, job["id"])

    try:
        session_svc.transition(user.tenant_id, session_id, "QUEUED", "training")
    except ValueError:
        pass

    return ok({"job_id": job["id"], "status": "QUEUED"})


@router.get("/sessions/{session_id}/jobs")
def list_jobs(session_id: str, user: CurrentUser = Depends(get_current_user)):
    session_svc.get_session(user.tenant_id, session_id)  # 404 check
    jobs = job_service.list_jobs_for_session(user.tenant_id, session_id)
    return ok(jobs)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: CurrentUser = Depends(get_current_user)):
    job = job_service.get_job(user.tenant_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return ok(job)


@router.get("/jobs/{job_id}/logs")
def get_job_logs(
    job_id: str,
    user: CurrentUser = Depends(get_current_user),
    tail: int = 200,
):
    job = job_service.get_job(user.tenant_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    lines = session_store.get_logs(user.tenant_id, job["session_id"], job_id, tail=tail)
    return ok({"job_id": job_id, "lines": lines, "total": len(lines)})


@router.get("/sessions/{session_id}/train/status")
def get_train_status(session_id: str, user: CurrentUser = Depends(get_current_user)):
    """
    Returns the latest job status for this session.
    Alias used by the Frontend — maps to GET /jobs/{job_id} for the most recent job.
    """
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    last_job_id = s.get("last_job_id")
    if not last_job_id:
        return ok({"session_id": session_id, "status": s["status"], "job": None})

    job = job_service.get_job(user.tenant_id, last_job_id)
    return ok({
        "session_id": session_id,
        "status": s["status"],
        "job_id": last_job_id,
        "job": job,
    })


@router.delete("/jobs/{job_id}", status_code=200)
def cancel_job(job_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    try:
        job = job_service.cancel_job(user.tenant_id, job_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    try:
        session_svc.force_status(user.tenant_id, job["session_id"], "CANCELLED")
    except Exception:
        pass

    return ok(job)
