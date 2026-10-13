"""A company's own AI provider: choose it, test it, see how it is doing.

  GET  /ai-provider/providers  — the catalogue the form offers (analyst+)
  GET  /ai-provider/config     — the stored choice, key MASKED (analyst+)
  PUT  /ai-provider/config     — save (admin). Does not call the provider unless
                                 `?test=true`; then the saved settings are tested.
  POST /ai-provider/test       — a tiny real completion + the model list (admin).
                                 With a body, tests that DRAFT without saving it.
  POST /ai-provider/reset      — back to StockAI's default (admin)
  GET  /ai-provider/status     — breaker, last success / failure, 7-day usage (analyst+)

Never exposed to API keys (`ai-provider` is in INTERNAL_TAGS): a key holder
could otherwise read which provider a company pays for, or swap it. The key is
accepted on write and never returned by any route; a viewer is refused every
route here, and reads the "assistant unavailable because ..." state from
`/service-config/capabilities` instead.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request

from backend.ai.providers import catalog, client, service, store
from backend.audit import note as audit_note
from backend.auth.guards import CurrentUser, require_admin, require_role
from backend.schemas.common import ok

router = APIRouter(prefix="/ai-provider", tags=["ai-provider"])
log = logging.getLogger(__name__)

_read_guard = require_role("admin", "analyst")


@router.get("/providers")
def list_providers(_: CurrentUser = Depends(_read_guard)):
    return ok({"providers": catalog.public_catalogue(),
               "allow_private_hosts": client._settings()["allow_private"],
               "max_output_tokens": client._settings()["max_tokens"]})


@router.get("/config")
def get_config(user: CurrentUser = Depends(_read_guard)):
    return ok(service.config_view(user.tenant_id))


@router.get("/status")
def get_status(user: CurrentUser = Depends(_read_guard)):
    return ok(service.status_view(user.tenant_id))


def _test_and_record(cfg: store.ProviderConfig, *, saved: bool) -> dict:
    result = client.test_connection(cfg)
    if saved:
        err = result.get("error") or {}
        store.record_test(cfg.tenant_id, ok=bool(result["ok"]), code=err.get("code"),
                          latency_ms=result.get("latency_ms"))
        if result["ok"]:
            # A passing test is the best probe there is: resume a paused provider.
            store.close_breaker(cfg.tenant_id)
    return result


@router.put("/config")
def put_config(
    request: Request,
    body: dict = Body(...),
    test: bool = Query(False),
    user: CurrentUser = Depends(require_admin),
):
    outcome = service.save(user.tenant_id, body or {}, actor=user.user_id)
    audit_note(request, target_id=user.tenant_id,
               label=(outcome["after"] or {}).get("provider"),
               before=outcome["before"],
               after={**(outcome["after"] or {}), "key_changed": outcome["key_changed"]})
    data = {"config": service.config_view(user.tenant_id), "test": None}
    if test:
        cfg = store.load_any(user.tenant_id)
        if cfg is not None:
            data["test"] = _test_and_record(cfg, saved=True)
    data["status"] = service.status_view(user.tenant_id)
    return ok(data)


@router.post("/test")
def post_test(
    request: Request,
    body: Optional[dict] = Body(None),
    user: CurrentUser = Depends(require_admin),
):
    if body:
        cfg = service.draft_config(user.tenant_id, body)
        saved = False
        stored = store.row(user.tenant_id)
        # A draft identical to what is stored (same provider/model/URL, key
        # left blank) IS the stored config: record the result on it.
        if stored and stored["provider"] == cfg.provider and stored["model"] == cfg.model \
                and (stored.get("base_url") or "") == cfg.base_url and not (body.get("api_key") or "").strip():
            saved = True
    else:
        cfg = store.load_any(user.tenant_id)
        saved = True
        if cfg is None:
            from backend.errors import AppError
            raise AppError("ai_provider_not_configured",
                           "This company has not chosen its own AI provider.", status_code=404)
    result = _test_and_record(cfg, saved=saved)
    audit_note(request, target_id=user.tenant_id, label=cfg.provider,
               after={"provider": cfg.provider, "model": cfg.model, "ok": result["ok"],
                      "error_code": (result.get("error") or {}).get("code")})
    return ok(result)


@router.post("/reset")
def post_reset(request: Request, user: CurrentUser = Depends(require_admin)):
    before = store.row(user.tenant_id)
    removed = store.delete(user.tenant_id)
    audit_note(request, target_id=user.tenant_id,
               before=({"provider": before["provider"], "model": before["model"]}
                       if before else None),
               after={"provider": "stockai_default"})
    return ok({"reset": removed, "status": service.status_view(user.tenant_id)})
