from fastapi import APIRouter, Depends, HTTPException, Query

from backend.activity.service import log_action
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.schemas.common import ok
from backend.schemas.session import SessionCreate, SessionUpdate
from backend.sessions import service as session_svc

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
    user: CurrentUser = Depends(require_analyst_or_above),
):
    # if not check_session_quota(user.tenant_id):
    #     raise HTTPException(status_code=429, detail="Session quota exceeded for your plan")
    session = session_svc.create_session(
        user.tenant_id, user.user_id, body.name, body.description, body.tags
    )
    return ok(session)


@router.get("/{session_id}")
def get_session(session_id: str, user: CurrentUser = Depends(get_current_user)):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
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
        raise HTTPException(status_code=404, detail="Session not found")

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
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s["status"] == "RUNNING":
        raise HTTPException(status_code=409, detail="Cannot delete a running session")
    session_svc.delete_session(user.tenant_id, session_id)
    log_action(
        user.tenant_id, user.user_id, "session.delete", resource=session_id,
        context={"name": s["name"], "status_at_deletion": s["status"]},
    )
