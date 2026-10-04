from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from backend import audit

from backend.activity.service import log_action
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.errors import AppError
from backend.schemas.common import ok
from backend.schemas.session import SessionCreate, SessionUpdate
from backend.sessions import service as session_svc
from backend.training import job_service

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
def list_sessions(
    user: CurrentUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    sessions = session_svc.list_sessions(user.tenant_id, skip=skip, limit=limit)
    total = session_svc.count_sessions(user.tenant_id)
    return ok({"items": sessions, "total": total, "skip": skip, "limit": limit})


@router.post("", status_code=201)
def create_session(
    body: SessionCreate,
    request: Request,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    from backend.entitlements.service import enforce_limit, limit_guard
    # Counted and created under one per-tenant lock: two tabs starting a
    # forecast at the same moment must not both pass the same stale count.
    with limit_guard(user.tenant_id) as conn:
        enforce_limit(user.tenant_id, "max_sessions",
                      session_svc.count_sessions(user.tenant_id), conn=conn)
        session = session_svc.create_session(
            user.tenant_id, user.user_id, body.name, body.description, body.tags
        )
    audit.note(request, target_id=session["id"], label=body.name,
               after={"name": body.name})
    return ok(session)


# NOTE: declared before GET /{session_id} — FastAPI matches routes in order,
# so "/summary" must not be swallowed by the path parameter.
@router.get("/summary")
def list_session_summaries(
    user: CurrentUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    q: Optional[str] = Query(None, max_length=200, description="Matches the run's or the dataset's name"),
    status: Optional[str] = Query(None, max_length=30),
    sort: str = Query("created_desc", pattern="^(" + "|".join(session_svc.SUMMARY_SORTS) + ")$"),
):
    """Session history: enriched list (dataset name, horizon, SKU count,
    granularity) in a single batched query — no per-session lookups. Filtered,
    sorted and paged on the server; `total` counts the filtered set."""
    items = session_svc.list_session_summaries(
        user.tenant_id, skip=skip, limit=limit, q=q, status=status, sort=sort)
    total = session_svc.count_session_summaries(user.tenant_id, q=q, status=status)
    return ok({"items": items, "total": total, "skip": skip, "limit": limit})


@router.get("/{session_id}")
def get_session(session_id: str, user: CurrentUser = Depends(get_current_user)):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise AppError("session_not_found", "Session not found", status_code=404)
    return ok(s)


@router.patch("/{session_id}")
def update_session(
    session_id: str,
    body: SessionUpdate,
    request: Request,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    from backend.db.connection import execute
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise AppError("session_not_found", "Session not found", status_code=404)

    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.description is not None:
        updates["description"] = body.description
    if body.tags is not None:
        from backend.db.connection import _json
        updates["tags"] = body.tags

    _ALLOWED = frozenset({"name", "description", "tags"})
    if updates:
        safe = {k: v for k, v in updates.items() if k in _ALLOWED}
        if safe:
            set_clause = ", ".join(f"{k} = %s" for k in safe)
            values = [_json(v) if k == "tags" else v for k, v in safe.items()]
            execute(
                f"UPDATE sessions SET {set_clause}, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
                (*values, session_id, user.tenant_id),
            )

    after = session_svc.get_session(user.tenant_id, session_id)
    audit.note(
        request, label=(after or s).get("name"),
        before={"name": s.get("name"), "description": s.get("description"), "tags": s.get("tags")},
        after={"name": (after or {}).get("name"), "description": (after or {}).get("description"),
               "tags": (after or {}).get("tags")},
    )
    return ok(after)


@router.delete("/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise AppError("session_not_found", "Session not found", status_code=404)
    # Ask the JOB, not the session. `runner.py` only ever writes COMPLETED or
    # FAILED back onto the session, so a session whose worker is training right
    # now still reads QUEUED — this guard was checking a status the real flow
    # never produces, and the two tests covering it write RUNNING by hand.
    # Deleting mid-training cascades the job row away under a worker that then
    # writes results for a session that no longer exists.
    if job_service.has_in_flight_job(user.tenant_id, session_id):
        raise AppError(
            "session_running_cannot_delete",
            "Cannot delete a session while its training is queued or running",
            status_code=409,
        )
    session_svc.delete_session(user.tenant_id, session_id)
    log_action(
        user.tenant_id, user.user_id, "session.delete", resource=session_id,
        context={"name": s["name"], "status_at_deletion": s["status"]},
    )
