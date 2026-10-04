from fastapi import APIRouter, Depends, Query

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
    archived: str = Query("active", pattern="^(active|archived|all)$"),
):
    sessions = session_svc.list_sessions(user.tenant_id, skip=skip, limit=limit, archived=archived)
    total = session_svc.count_sessions(user.tenant_id, archived=archived)
    return ok({"items": sessions, "total": total, "skip": skip, "limit": limit})


@router.post("", status_code=201)
def create_session(
    body: SessionCreate,
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
    return ok(session)


# NOTE: declared before GET /{session_id} — FastAPI matches routes in order,
# so "/summary" must not be swallowed by the path parameter.
@router.get("/summary")
def list_session_summaries(
    user: CurrentUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    q: str | None = Query(None, max_length=200, description="Search name, description, dataset"),
    status: list[str] | None = Query(None, description="Repeat to filter on several statuses"),
    dataset_id: str | None = Query(None, max_length=100),
    archived: str = Query("active", pattern="^(active|archived|all)$"),
    created_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    created_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    sort: str = Query("created_at", pattern="^(created_at|updated_at|name|status|horizon|accuracy)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """The sessions library: every session of the tenant, enriched (dataset name,
    horizon, SKU count, granularity, headline accuracy, models), searchable,
    filterable, sortable and paginated. `total` counts the rows matching the
    filters, so a pager can be drawn."""
    items, total = session_svc.list_session_summaries(
        user.tenant_id, skip=skip, limit=limit, q=q, status=status,
        dataset_id=dataset_id, archived=archived, created_from=created_from,
        created_to=created_to, sort=sort, order=order)
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

    return ok(session_svc.get_session(user.tenant_id, session_id))


@router.delete("/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Archive a session. Nothing is erased: the results, forecasts and artifacts
    stay in storage and `POST /sessions/{id}/restore` brings the session back.
    The verb stays DELETE for the clients that already call it."""
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise AppError("session_not_found", "Session not found", status_code=404)
    # Ask the JOB, not the session. `runner.py` only ever writes COMPLETED or
    # FAILED back onto the session, so a session whose worker is training right
    # now still reads QUEUED. Archiving mid-training would hide a run that is
    # about to write its results.
    if job_service.has_in_flight_job(user.tenant_id, session_id):
        raise AppError(
            "session_running_cannot_delete",
            "Cannot archive a session while its training is queued or running",
            status_code=409,
        )
    if s.get("archived_at") is None:
        session_svc.archive_session(user.tenant_id, session_id, user.user_id)
        log_action(
            user.tenant_id, user.user_id, "session.archive", resource=session_id,
            context={"name": s["name"], "status_at_archive": s["status"],
                     "dataset_id": s.get("dataset_id")},
        )


@router.post("/{session_id}/restore")
def restore_session(
    session_id: str,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Bring an archived session back into the working list. Counts against the
    plan's saved-forecast ceiling like a new one: at the ceiling it is refused
    with the same message, and the session stays archived and intact."""
    from backend.entitlements.service import enforce_limit, limit_guard
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise AppError("session_not_found", "Session not found", status_code=404)
    if s.get("archived_at") is None:
        return ok(s)
    with limit_guard(user.tenant_id) as conn:
        enforce_limit(user.tenant_id, "max_sessions",
                      session_svc.count_sessions(user.tenant_id, conn=conn), conn=conn)
        restored = session_svc.restore_session(user.tenant_id, session_id)
    log_action(
        user.tenant_id, user.user_id, "session.restore", resource=session_id,
        context={"name": s["name"]},
    )
    return ok(restored)
