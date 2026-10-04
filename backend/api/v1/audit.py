"""The audit trail and the run lineage manifest.

* `GET /audit` — who did what to which object, filterable and paged. Admin
  only: it names people, and the point of an audit trail is that the people it
  describes do not control who reads it.
* `GET /audit/export` — the same filter as CSV.
* `GET /sessions/{id}/manifest` — how a forecast was produced. A read for every
  role (and a key): it describes a forecast the reader can already see.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.audit import service as audit_svc
from backend.auth.guards import CurrentUser, get_current_user, require_admin
from backend.errors import AppError
from backend.lineage.manifest import latest_manifest
from backend.schemas.common import ok
from backend.sessions import service as session_svc

router = APIRouter(prefix="/audit", tags=["audit"])
manifest_router = APIRouter(tags=["sessions"])


def _filters(
    actor: Optional[str] = Query(None, description="Actor id (user id, api_key:<id>, scheduler)"),
    target_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None, description="One of GET /audit/filters actions"),
    target_id: Optional[str] = Query(None, max_length=200),
    status: Optional[str] = Query(None, pattern="^(success|error)$"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
) -> dict:
    return {
        "actor": actor, "target_type": target_type, "action": action,
        "target_id": target_id, "status": status,
        "date_from": date_from, "date_to": date_to,
    }


@router.get("")
def list_audit(
    filters: dict = Depends(_filters),
    limit: int = Query(50, ge=1, le=audit_svc.MAX_PAGE),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_admin),
):
    return ok(audit_svc.list_audit(user.tenant_id, limit=limit, offset=offset, **filters))


@router.get("/filters")
def audit_filters(user: CurrentUser = Depends(require_admin)):
    """The filter vocabulary, served so the screen cannot offer a value that
    nothing can ever be recorded under."""
    return ok({
        "target_types": audit_svc.TARGET_TYPES,
        "actions": audit_svc.audit_actions(),
        "actors": audit_svc.actors(user.tenant_id),
    })


@router.get("/export")
def export_audit(
    filters: dict = Depends(_filters),
    user: CurrentUser = Depends(require_admin),
):
    stamp = date.today().isoformat()
    return StreamingResponse(
        audit_svc.export_csv(user.tenant_id, **filters),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.csv"'},
    )


@manifest_router.get("/sessions/{session_id}/manifest")
def get_manifest(session_id: str, user: CurrentUser = Depends(get_current_user)):
    """How this forecast was produced: who or what started the run, the dataset's
    content hash and size, the full configuration, engine and library versions,
    per-model outcomes, stage timings and a hash of the forecast. Written once
    when the run ends and never changed. 404 `manifest_not_available` for a
    session that has not finished a run since manifests were introduced."""
    if not session_svc.get_session(user.tenant_id, session_id):
        raise AppError("session_not_found", "Session not found", status_code=404)
    found = latest_manifest(user.tenant_id, session_id)
    if not found:
        raise AppError(
            "manifest_not_available",
            "This session has no lineage manifest: it has not finished a "
            "training run since manifests were introduced.",
            status_code=404, params={"session_id": session_id},
        )
    return ok(found)
