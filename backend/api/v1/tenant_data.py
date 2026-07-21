"""
Admin-only tenant data export (ZIP) and cascade delete — Costa Rica Ley 8968
/ GDPR-style right to export & erasure. Router stays thin; all logic lives in
backend/tenants/data_export.py.
"""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.auth.guards import CurrentUser, require_admin
from backend.schemas.common import ok
from backend.tenants import data_export
from backend.tenants.service import get_tenant

router = APIRouter(prefix="/tenant", tags=["tenant"])
log = logging.getLogger(__name__)


class DeleteTenantRequest(BaseModel):
    # The caller must type either the tenant's own slug or the literal word
    # "DELETE" so the endpoint can never fire by accident (e.g. a stray
    # request with an empty body).
    confirm: str


@router.get("/export")
def export_tenant_data(user: CurrentUser = Depends(require_admin)):
    """Streams a ZIP with every table this tenant owns, scoped to tenant_id."""
    try:
        zip_bytes = data_export.build_export_zip(user.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    filename = f"faro_export_{user.tenant_id}_{date.today().isoformat()}.zip"
    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("")
def delete_tenant_data(body: DeleteTenantRequest, user: CurrentUser = Depends(require_admin)):
    """Cascade-deletes the caller's tenant and ALL of its data. Irreversible."""
    tenant = get_tenant(user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    confirm = body.confirm.strip()
    if confirm != tenant["slug"] and confirm.upper() != "DELETE":
        raise HTTPException(
            status_code=400,
            detail="Confirmation required: pass the tenant's slug or the literal 'DELETE'.",
        )

    result = data_export.delete_tenant(user.tenant_id)
    log.warning("[tenant] tenant=%s ERASED by user=%s", user.tenant_id, user.user_id)
    return ok(result)
