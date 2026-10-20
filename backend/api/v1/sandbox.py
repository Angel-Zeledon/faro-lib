"""A sandbox to build an integration against. A person asks for it (an administrator
of the company); an API key never mints one. See `backend/sandbox/service.py`."""

from fastapi import APIRouter, Depends

from backend.auth.guards import CurrentUser, get_current_user, require_admin
from backend.entitlements.service import ensure_feature
from backend.sandbox import service
from backend.schemas.common import ok

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.get("")
def list_sandboxes(user: CurrentUser = Depends(get_current_user)):
    return ok(service.list_sandboxes(user.tenant_id))


@router.post("", status_code=201)
def create_sandbox(user: CurrentUser = Depends(require_admin)):
    # A sandbox exists to build against the API, so it asks the same question
    # creating a key does: does the plan include it?
    ensure_feature(user.tenant_id, "api")
    return ok(service.create_sandbox(user.tenant_id))


@router.delete("/{sandbox_id}")
def delete_sandbox(sandbox_id: str, user: CurrentUser = Depends(require_admin)):
    service.delete_sandbox(user.tenant_id, sandbox_id)
    return ok({"deleted": True})
