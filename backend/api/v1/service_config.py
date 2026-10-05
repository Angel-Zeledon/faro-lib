"""The configuration panel's API: what is on, what is off, and how to fix it.

Three audiences, three guards, and they are not interchangeable:

  - `/capabilities` — any authenticated caller. Booleans only: can the
    assistant answer, can this tenant be emailed. No variable names, no
    sources, no hints. It exists so a screen can say "the assistant is off"
    instead of spinning against a service that will never answer.
  - `/services` and its writes — the INSTANCE OPERATOR (see
    `service_config/access.py`). These carry variable names, which layer won,
    and masked hints of stored secrets: deployment-owner information, not
    tenant-admin information.
  - `/services/tenant/...` — a tenant's own admin, over the services the
    registry marks `tenant_scoped`, and always against the tenant_id in the
    token. A tenant configuring the WhatsApp number ITS customers see is a real
    need; a tenant reaching the instance's DeepSeek key is not.

Nothing here ever returns a stored secret in cleartext. A write accepts one,
a read gives back four trailing characters at most, and there is no endpoint
that reverses that — an operator who lost a key re-issues it at the provider,
which is what they would have to do anyway.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.auth.guards import CurrentUser, get_current_user
from backend.errors import AppError
from backend.schemas.common import ok
from backend.service_config import status as status_mod
from backend.service_config import store
from backend.service_config.access import (
    OPERATOR_ENV,
    bootstrap_scope,
    instance_editing_enabled,
    is_instance_operator,
    operator_emails,
    require_instance_operator,
    require_tenant_channel_admin,
)
from backend.service_config.registry import BY_KEY, SERVICES

router = APIRouter(prefix="/service-config", tags=["service-config"])
log = logging.getLogger(__name__)


class ValuesBody(BaseModel):
    """Field values to write. An empty string CLEARS the override.

    Values are `str` on the wire even for ints and booleans: the registry knows
    each field's shape and `store.coerce` applies it, so there is exactly one
    place that decides what "true" means. A JSON `true` and the string "true"
    arriving at different endpoints and being parsed by different code is how a
    boolean ends up stored as the text "True" and read back as truthy garbage.
    """

    values: dict[str, str] = Field(default_factory=dict)


def _service_or_404(service_key: str):
    service = BY_KEY.get(service_key)
    if service is None:
        raise AppError(
            "service_not_found",
            f"Unknown service '{service_key}'.",
            status_code=404,
            params={"service": service_key},
        )
    return service


def _write(service_key: str, body: ValuesBody, *, tenant_id: str | None, actor: str):
    """Shared write path for both scopes — one place that turns the store's
    refusals into stated errors instead of a 500."""
    try:
        result = store.set_values(
            service_key, body.values, tenant_id=tenant_id, updated_by=actor
        )
    except store.SecretStorageUnavailable as exc:
        # Not a bug and not the operator's mistake: the deployment has no
        # Fernet key, so a secret cannot be stored encrypted and will not be
        # stored any other way.
        raise AppError(
            "secret_storage_unavailable", str(exc), status_code=409,
            params={"env": "INTEGRATIONS_SECRET_KEY"},
        )
    except ValueError as exc:
        raise AppError("invalid_config_value", str(exc), status_code=422)
    except store.ConfigStoreUnavailable as exc:
        # The write did not happen. Saying so is the whole point: a panel that
        # reports "saved" over a failed write is how somebody spends a week
        # convinced a key is configured.
        log.error("Configuration write failed (%s): %s", service_key, exc)
        raise AppError(
            "config_store_unavailable",
            "The configuration could not be saved. Nothing was changed.",
            status_code=503,
        )

    # The remembered probe was about the credentials that were in place before
    # this write. Keeping it would have the panel report `degraded` about a key
    # it has never tried.
    status_mod.forget_probe(service_key, tenant_id)

    log.info(
        "Service config written: service=%s scope=%s written=%s cleared=%s by=%s",
        service_key, tenant_id or "instance", result.written, result.cleared, actor,
    )
    return result


# ── Operations snapshot — the instance operator only ────────────────────────

@router.get("/ops")
def get_ops(_: CurrentUser = Depends(require_instance_operator)):
    """Queue, worker, pool, disk, backup and latency readings in one place.

    Read-only. Cross-tenant numbers (every tenant's jobs), so it is gated like
    the rest of instance configuration, never by the tenant `admin` role. The
    thresholds are the `operations` service in the registry.
    """
    from backend.service_config import ops

    return ok(ops.snapshot())


# ── Capabilities — every authenticated caller ───────────────────────────────

@router.get("/capabilities")
def get_capabilities(user: CurrentUser = Depends(get_current_user)):
    """Which user-visible features can answer right now, for this tenant.

    Reason-free on purpose. A viewer learning the assistant is unavailable is
    being helped; a viewer learning WHICH credential is missing is being told
    about the deployment's secrets.
    """
    return ok(status_mod.capabilities(user.tenant_id))


# ── Instance scope — the operator ───────────────────────────────────────────

@router.get("/services")
def list_services(user: CurrentUser = Depends(require_instance_operator)):
    """Every service, its state, its fields and where each value came from."""
    report = status_mod.full_report()
    # `bootstrap` is true when the caller operates this installation only
    # because it is the only company on it. The panel has to say that out loud:
    # the access is real but temporary, and it ends the day a second tenant
    # signs up — which is a bad day to discover you never named an operator.
    report["operator"] = {
        "env": OPERATOR_ENV,
        "editing_enabled": instance_editing_enabled(),
        "explicit": bool(operator_emails()),
        "bootstrap": bootstrap_scope() is not None,
    }
    return ok(report)


@router.get("/services/{service_key}")
def get_service(
    service_key: str, user: CurrentUser = Depends(require_instance_operator)
):
    service = _service_or_404(service_key)
    return ok(status_mod.service_report(service))


@router.put("/services/{service_key}")
def put_service(
    service_key: str,
    body: ValuesBody,
    user: CurrentUser = Depends(require_instance_operator),
):
    """Write instance-level overrides for one service."""
    service = _service_or_404(service_key)
    if not service.editable:
        raise AppError(
            "service_not_editable",
            f"'{service_key}' is configured by environment only.",
            status_code=422,
            params={"service": service_key},
        )
    result = _write(service_key, body, tenant_id=None, actor=user.user_id)
    return ok({
        "written": list(result.written),
        "cleared": list(result.cleared),
        "service": status_mod.service_report(service),
    })


@router.delete("/services/{service_key}")
def delete_service_overrides(
    service_key: str, user: CurrentUser = Depends(require_instance_operator)
):
    """Drop every stored override for a service — back to the environment."""
    service = _service_or_404(service_key)
    try:
        cleared = store.clear_service(service_key)
    except ValueError as exc:
        raise AppError("service_not_editable", str(exc), status_code=422)
    except store.ConfigStoreUnavailable as exc:
        log.error("Configuration clear failed (%s): %s", service_key, exc)
        raise AppError(
            "config_store_unavailable",
            "The configuration could not be cleared. Nothing was changed.",
            status_code=503,
        )
    status_mod.forget_probe(service_key)
    log.info("Service config cleared: service=%s scope=instance by=%s",
             service_key, user.user_id)
    return ok({
        "cleared": list(cleared),
        "service": status_mod.service_report(service),
    })


@router.post("/services/{service_key}/probe")
def probe_service(
    service_key: str, user: CurrentUser = Depends(require_instance_operator)
):
    """Ask the provider whether these credentials actually work.

    Never sends anything to a customer and never raises: a probe reports a
    failure, it does not become one.
    """
    service = _service_or_404(service_key)
    if not service.probe:
        raise AppError(
            "service_has_no_probe",
            f"'{service_key}' has nothing to reach.",
            status_code=422,
            params={"service": service_key},
        )
    result = status_mod.run_probe(service_key)
    return ok({
        "ok": result.ok,
        "code": result.code,
        "detail": result.detail,
        "checked_at": result.checked_at,
        "extra": result.extra,
    })


# ── Tenant scope — a tenant's own sender identity ───────────────────────────

def _tenant_scoped_or_422(service_key: str):
    service = _service_or_404(service_key)
    if not service.tenant_scoped:
        raise AppError(
            "service_not_tenant_scoped",
            f"'{service_key}' is configured for the whole installation, not per "
            "tenant.",
            status_code=422,
            params={"service": service_key},
        )
    return service


@router.get("/tenant/services")
def list_tenant_services(user: CurrentUser = Depends(require_tenant_channel_admin)):
    """The services a tenant may configure for itself, in its own scope.

    An operator sees the same shape here as any tenant admin: this endpoint is
    about the caller's tenant, and mixing the instance view into it is how a
    screen ends up showing one scope's values under another scope's label.
    """
    services = [
        status_mod.service_report(s, user.tenant_id)
        for s in SERVICES
        if s.tenant_scoped
    ]
    return ok({
        "services": services,
        "scope": "tenant",
        "tenant_id": user.tenant_id,
        "is_instance_operator": is_instance_operator(user),
        "overrides": {
            "store_available": store.store_available(),
            "encryption_available": store.encryption_available(),
            "tenant_fields": sorted(store.tenant_overrides(user.tenant_id).keys()),
        },
    })


@router.put("/tenant/services/{service_key}")
def put_tenant_service(
    service_key: str,
    body: ValuesBody,
    user: CurrentUser = Depends(require_tenant_channel_admin),
):
    """Write this tenant's own override. Scope comes from the token, never the body."""
    service = _tenant_scoped_or_422(service_key)
    result = _write(
        service_key, body, tenant_id=user.tenant_id, actor=user.user_id
    )
    return ok({
        "written": list(result.written),
        "cleared": list(result.cleared),
        "service": status_mod.service_report(service, user.tenant_id),
    })


@router.delete("/tenant/services/{service_key}")
def delete_tenant_service_overrides(
    service_key: str, user: CurrentUser = Depends(require_tenant_channel_admin)
):
    service = _tenant_scoped_or_422(service_key)
    try:
        cleared = store.clear_service(service_key, tenant_id=user.tenant_id)
    except store.ConfigStoreUnavailable as exc:
        log.error("Tenant configuration clear failed (%s): %s", service_key, exc)
        raise AppError(
            "config_store_unavailable",
            "The configuration could not be cleared. Nothing was changed.",
            status_code=503,
        )
    status_mod.forget_probe(service_key, user.tenant_id)
    return ok({
        "cleared": list(cleared),
        "service": status_mod.service_report(service, user.tenant_id),
    })


@router.post("/tenant/services/{service_key}/probe")
def probe_tenant_service(
    service_key: str, user: CurrentUser = Depends(require_tenant_channel_admin)
):
    """Probe a tenant's own credentials — the ones it pasted, in its own scope."""
    service = _tenant_scoped_or_422(service_key)
    if not service.probe:
        raise AppError(
            "service_has_no_probe",
            f"'{service_key}' has nothing to reach.",
            status_code=422,
            params={"service": service_key},
        )
    result = status_mod.run_probe(service_key, user.tenant_id)
    return ok({
        "ok": result.ok,
        "code": result.code,
        "detail": result.detail,
        "checked_at": result.checked_at,
        "extra": result.extra,
    })
