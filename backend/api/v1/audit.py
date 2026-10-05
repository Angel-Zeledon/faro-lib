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

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from backend import audit
from backend.audit import service as audit_svc
from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user, require_admin
from backend.errors import AppError
from backend.lineage import run_metrics
from backend.lineage.manifest import latest_manifest
from backend.schemas.common import ok
from backend.sessions import service as session_svc

router = APIRouter(prefix="/audit", tags=["audit"])

# _COMPANY_WIDE_TRAIL: the trail covers the whole company - receptions,
# transfers and purchase orders of every warehouse, named in each row's details
# - and its rows carry no reliable warehouse to filter on. An admin limited to
# some warehouses is therefore refused it (`warehouse_scope_company_totals`),
# like every other company-wide read, instead of reading other warehouses'
# activity through it.
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
    wscope.require_company_wide(user)  # see _COMPANY_WIDE_TRAIL above
    return ok(audit_svc.list_audit(user.tenant_id, limit=limit, offset=offset, **filters))


@router.get("/filters")
def audit_filters(user: CurrentUser = Depends(require_admin)):
    """The filter vocabulary, served so the screen cannot offer a value that
    nothing can ever be recorded under."""
    wscope.require_company_wide(user)
    return ok({
        "target_types": audit_svc.TARGET_TYPES,
        "actions": audit_svc.audit_actions(),
        "actors": audit_svc.actors(user.tenant_id),
    })


@router.get("/export")
def export_audit(
    request: Request,
    filters: dict = Depends(_filters),
    user: CurrentUser = Depends(require_admin),
):
    wscope.require_company_wide(user)
    # Who took the audit trail out, and how much of it. The count is the rows the
    # file will carry (the export is capped), read before streaming starts.
    total = audit_svc.list_audit(user.tenant_id, limit=1, **filters)["total"]
    audit.note(request, after={
        "rows": min(int(total), audit_svc.EXPORT_MAX_ROWS), "format": "csv",
        "filters": {k: str(v) for k, v in filters.items() if v is not None},
    })
    stamp = date.today().isoformat()
    return StreamingResponse(
        audit_svc.export_csv(user.tenant_id, **filters),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.csv"'},
    )


@manifest_router.get("/training/run-durations")
def get_run_durations(
    limit: int = Query(run_metrics.DEFAULT_RUNS, ge=1, le=run_metrics.MAX_RUNS,
                       description="How many of the most recent runs to aggregate"),
    user: CurrentUser = Depends(get_current_user),
):
    """Median and p95 training duration over the tenant's last `limit` runs, by
    catalogue-size bucket and by granularity, read from the lineage manifests
    (no new storage). For choosing a retrain cadence from evidence."""
    return ok(run_metrics.run_duration_metrics(user.tenant_id, limit))


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
