"""API endpoints for accounting integrations: connect/list/sync/delete.

Thin orchestration only — all the actual work (credential encryption, provider
HTTP calls, the fetch->import->auto-train pipeline) lives in
backend.integrations.{store,registry,sync_service}. Gated to tenants with the
INTEGRATIONS feature (Enterprise plan) via a router-level dependency, so every
route below requires it with no risk of a new route forgetting the check.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.guards import (
    CurrentUser, get_current_user, require_admin,
    require_verified_admin, require_verified_analyst_or_above,
)
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.integrations import registry, store, sync_service
from backend.integrations.base import IntegrationAuthError, IntegrationSyncError
from backend.integrations.crypto import integrations_enabled
from backend.integrations.registry import SUPPORTED_PROVIDERS
from backend.schemas.common import ok

router = APIRouter(
    prefix="/integrations", tags=["integrations"],
    dependencies=[Depends(require_feature(Feature.INTEGRATIONS))],
)
log = logging.getLogger(__name__)


@router.get("")
def list_integrations(user: CurrentUser = Depends(get_current_user)):
    return ok({
        "connections": store.list_connections(user.tenant_id),
        "providers": SUPPORTED_PROVIDERS,
    })


@router.post("/{provider}/connect")
def connect_provider(
    provider: str,
    credentials: dict,
    # Verified email required: connecting hands third-party credentials to an
    # account whose address nobody has proven belongs to the signer-up.
    user: CurrentUser = Depends(require_verified_admin),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown accounting provider: {provider!r}")
    if not integrations_enabled():
        raise HTTPException(status_code=400, detail="Integrations are not configured on this server")

    provider_impl = registry.get_provider(provider, credentials)
    try:
        provider_impl.test_connection()
    except IntegrationAuthError:
        raise HTTPException(status_code=400, detail="Invalid credentials")
    except IntegrationSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    connection = store.create_connection(user.tenant_id, provider, credentials)
    log.info("[integrations] connected provider=%s tenant=%s", provider, user.tenant_id)
    return ok(connection)


@router.post("/{id}/sync")
def sync_now(id: str, user: CurrentUser = Depends(require_verified_analyst_or_above)):
    connection = store.get_connection(id)
    if connection is None or connection["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=404, detail="Integration connection not found")

    try:
        result = sync_service.sync_connection(id)
    except (IntegrationAuthError, IntegrationSyncError) as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return ok(result)


@router.delete("/{id}")
def delete_integration(id: str, user: CurrentUser = Depends(require_admin)):
    deleted = store.delete_connection(user.tenant_id, id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Integration connection not found")
    return ok({"deleted": id})
