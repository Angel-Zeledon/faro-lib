"""File-drop sources: add one, test it, see what arrived, settle what was held.

Managing a source (it holds credentials for the company's server) is an admin's
job. Settling a held file replaces data, which an analyst already does by uploading
one, so an analyst may do it. Reads are open to any signed-in user.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.auth.guards import (
    CurrentUser, get_current_user, require_admin, require_analyst_or_above,
)
from backend.file_drop import service
from backend.schemas.common import ok

router = APIRouter(prefix="/file-drop", tags=["file-drop"])


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=service.MAX_NAME_LENGTH)
    kind: str
    config: dict = Field(default_factory=dict)
    # Write-only: never echoed back.
    secrets: dict = Field(default_factory=dict)
    target_dataset_id: Optional[str] = None
    file_pattern: Optional[str] = None
    interval_minutes: Optional[int] = Field(default=None, ge=5, le=24 * 60)
    min_age_seconds: Optional[int] = Field(default=None, ge=0, le=24 * 3600)


class SourcePatch(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    secrets: Optional[dict] = None
    target_dataset_id: Optional[str] = None
    file_pattern: Optional[str] = None
    interval_minutes: Optional[int] = Field(default=None, ge=5, le=24 * 60)
    enabled: Optional[bool] = None


class HostKeyBody(BaseModel):
    fingerprint: str = Field(min_length=10, max_length=200)


class DecisionBody(BaseModel):
    action: str = Field(pattern="^(apply|reject)$")


@router.get("/sources")
def list_sources(user: CurrentUser = Depends(get_current_user)):
    return ok(service.list_sources(user.tenant_id))


@router.post("/sources", status_code=201)
def create_source(body: SourceCreate, user: CurrentUser = Depends(require_admin)):
    return ok(service.create_source(user.tenant_id, user.user_id, body.model_dump()))


@router.patch("/sources/{source_id}")
def update_source(source_id: str, body: SourcePatch, user: CurrentUser = Depends(require_admin)):
    return ok(service.update_source(user.tenant_id, source_id,
                                    body.model_dump(exclude_unset=True)))


@router.delete("/sources/{source_id}")
def delete_source(source_id: str, user: CurrentUser = Depends(require_admin)):
    service.delete_source(user.tenant_id, source_id)
    return ok({"deleted": True})


@router.post("/sources/{source_id}/test")
def test_source(source_id: str, user: CurrentUser = Depends(require_admin)):
    return ok(service.test_connection(user.tenant_id, source_id))


@router.post("/sources/{source_id}/host-key")
def accept_host_key(source_id: str, body: HostKeyBody, user: CurrentUser = Depends(require_admin)):
    """Accept the SFTP server's new host key after a change was refused."""
    return ok(service.accept_host_key(user.tenant_id, source_id, body.fingerprint))


@router.post("/sources/{source_id}/check")
def check_now(source_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    return ok(service.check_now(user.tenant_id, source_id))


@router.get("/files")
def list_files(source_id: Optional[str] = Query(default=None),
               limit: int = Query(default=100, ge=1, le=500),
               user: CurrentUser = Depends(get_current_user)):
    return ok(service.list_files(user.tenant_id, source_id, limit))


@router.post("/files/{file_id}/decision")
def decide(file_id: str, body: DecisionBody, user: CurrentUser = Depends(require_analyst_or_above)):
    return ok(service.decide(user.tenant_id, user.user_id, file_id, body.action))
