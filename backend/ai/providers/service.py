"""What the API and the capabilities endpoint ask of a company's AI provider.

Validation of the form lives here, with structured error codes the frontend
renders (`errors.ai_provider_*`). Nothing returned from this module ever holds
the key: config reads go through `store.row()`, which does not select it.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.ai.providers import catalog, client, store, transport
from backend.ai.providers.errors import ProviderError
from backend.errors import AppError

_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@+\-]{1,200}$")
_ORG_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


def validate(body: dict, existing: Optional[dict]) -> dict:
    """The cleaned values to store, or an AppError naming the field."""
    provider = str(body.get("provider") or "").strip()
    spec = catalog.get(provider)
    if spec is None:
        raise AppError("ai_provider_unknown", "Unknown AI provider.", status_code=422,
                       params={"provider": provider[:40]})

    model = str(body.get("model") or "").strip() or spec.default_model
    if not _MODEL_RE.match(model):
        raise AppError("ai_provider_model_invalid", "The model name is not valid.",
                       status_code=422)

    base_url = ""
    if spec.custom_base_url:
        base_url = str(body.get("base_url") or "").strip().rstrip("/")
        try:
            transport.check_base_url(base_url, allow_private_hosts=transport.allow_private())
        except ProviderError as exc:
            raise AppError("ai_provider_base_url_refused",
                           "This base URL cannot be used.", status_code=422,
                           params={"reason": exc.params.get("reason"),
                                   "setting": transport.ALLOW_PRIVATE_SETTING})

    raw_key = body.get("api_key")
    api_key: Optional[str] = None
    clear_key = False
    if isinstance(raw_key, str) and raw_key.strip():
        api_key = raw_key.strip()
        if len(api_key) > 500 or any(c.isspace() or ord(c) < 32 for c in api_key):
            raise AppError("ai_provider_key_invalid_format",
                           "The API key has spaces or characters a key never has.",
                           status_code=422)
    else:
        same_provider = bool(existing) and existing.get("provider") == provider
        if same_provider and existing.get("has_key"):
            api_key = None  # keep the stored one
        elif spec.key_required:
            raise AppError("ai_provider_key_required",
                           "Enter the API key for this provider.", status_code=422,
                           params={"provider": provider})
        else:
            # A custom URL without a key: never carry another provider's key
            # over to it.
            clear_key = not same_provider

    organization = project = ""
    if spec.supports_org:
        organization = str(body.get("organization") or "").strip()
        project = str(body.get("project") or "").strip()
        for v in (organization, project):
            if v and not _ORG_RE.match(v):
                raise AppError("ai_provider_org_invalid",
                               "The organization or project id is not valid.", status_code=422)

    temperature = body.get("temperature")
    if temperature in ("", None):
        temperature = None
    else:
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            temperature = -1
        if not 0.0 <= temperature <= 2.0:
            raise AppError("ai_provider_temperature_invalid",
                           "Temperature must be between 0 and 2.", status_code=422)

    cap = client._settings()["max_tokens"]
    max_tokens = body.get("max_tokens")
    if max_tokens in ("", None):
        max_tokens = None
    else:
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            max_tokens = 0
        if not 16 <= max_tokens <= cap:
            raise AppError("ai_provider_max_tokens_invalid",
                           "The answer length is outside the allowed range.", status_code=422,
                           params={"min": 16, "max": cap})

    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        raise AppError("ai_provider_enabled_invalid", "'enabled' must be true or false.",
                       status_code=422)

    return {"provider": provider, "model": model, "base_url": base_url, "api_key": api_key,
            "clear_key": clear_key, "organization": organization, "project": project,
            "temperature": temperature, "max_tokens": max_tokens, "enabled": enabled}


def save(tenant_id: str, body: dict, actor: str) -> dict:
    existing = store.row(tenant_id)
    clean = validate(body, existing)
    try:
        if clean["clear_key"]:
            from backend.db.connection import execute
            execute("UPDATE tenant_ai_providers SET api_key_enc = NULL, api_key_hint = NULL "
                    "WHERE tenant_id = %s", (tenant_id,))
        store.save(tenant_id, provider=clean["provider"], model=clean["model"],
                   base_url=clean["base_url"], api_key=clean["api_key"],
                   organization=clean["organization"], project=clean["project"],
                   temperature=clean["temperature"], max_tokens=clean["max_tokens"],
                   enabled=clean["enabled"], actor=actor)
    except store.SecretStorageUnavailable:
        raise AppError("secret_storage_unavailable",
                       "The key cannot be stored encrypted on this installation.",
                       status_code=409, params={"env": "INTEGRATIONS_SECRET_KEY"})
    return {"before": _summary(existing), "after": _summary(store.row(tenant_id)),
            "key_changed": bool(clean["api_key"]) or clean["clear_key"]}


def _summary(r: Optional[dict]) -> Optional[dict]:
    """For the audit row: what changed, never the key."""
    if not r:
        return None
    return {"provider": r.get("provider"), "model": r.get("model"),
            "base_url": r.get("base_url"), "enabled": r.get("enabled")}


def draft_config(tenant_id: str, body: dict) -> store.ProviderConfig:
    """A config to TEST without saving it. A blank key reuses the stored one
    only when the provider is the same (never another provider's key)."""
    existing_row = store.row(tenant_id)
    clean = validate(body, existing_row)
    key = clean["api_key"] or ""
    if not key and not clean["clear_key"]:
        stored = store.load_any(tenant_id)
        if stored and stored.provider == clean["provider"]:
            key = stored.api_key
    return store.ProviderConfig(
        tenant_id=tenant_id, provider=clean["provider"], model=clean["model"],
        base_url=clean["base_url"], api_key=key, organization=clean["organization"],
        project=clean["project"], temperature=clean["temperature"],
        max_tokens=clean["max_tokens"], enabled=clean["enabled"])


def config_view(tenant_id: str) -> dict:
    r = store.row(tenant_id)
    if not r:
        return {"configured": False}
    return {
        "configured": True,
        "provider": r["provider"], "model": r["model"], "base_url": r.get("base_url") or "",
        "has_key": bool(r.get("has_key")), "api_key_mask": store.masked(r),
        "organization": r.get("organization") or "", "project": r.get("project") or "",
        "temperature": r.get("temperature"), "max_tokens": r.get("max_tokens"),
        "enabled": bool(r.get("enabled")),
        "updated_at": _iso(r.get("updated_at")), "updated_by": r.get("updated_by"),
    }


def _instance_ready() -> bool:
    from backend.service_config.status import _service_is_ready
    return bool(_service_is_ready("llm", None))


def status_view(tenant_id: str) -> dict:
    r = store.row(tenant_id)
    s = client._settings()
    usage = store.usage_since(tenant_id, 7)
    if not r or not r.get("enabled"):
        return {"source": "stockai", "configured": bool(r), "enabled": bool(r and r["enabled"]),
                "instance_available": _instance_ready(), "usage_7d": usage,
                "provider": r["provider"] if r else None}
    state = store.breaker_state(r, s["cooldown"])
    return {
        "source": "company", "configured": True, "enabled": True,
        "provider": r["provider"], "model": r["model"],
        "breaker": state,
        "breaker_threshold": s["threshold"], "breaker_cooldown_s": s["cooldown"],
        "consecutive_failures": int(r.get("consecutive_failures") or 0),
        "last_success_at": _iso(r.get("last_success_at")),
        "last_failure": ({"code": r.get("last_failure_code"),
                          "message": r.get("last_failure_message"),
                          "at": _iso(r.get("last_failure_at"))}
                         if r.get("last_failure_code") else None),
        "last_test": ({"at": _iso(r.get("last_test_at")), "ok": r.get("last_test_ok"),
                       "code": r.get("last_test_code"),
                       "latency_ms": r.get("last_test_latency_ms")}
                      if r.get("last_test_at") else None),
        "usage_7d": usage,
    }


def capability(tenant_id: Optional[str], instance_ready: bool) -> dict[str, Any]:
    """For `/service-config/capabilities`: whether THIS tenant's assistant can
    answer and, for its own provider, why not. Codes only — the company's own
    provider failing is the company's information, not the deployment's."""
    r = store.row(tenant_id) if tenant_id else None
    if not r or not r.get("enabled"):
        return {"source": "stockai", "provider": None,
                "state": "ready" if instance_ready else "not_configured", "reason": None}
    s = client._settings()
    breaker = store.breaker_state(r, s["cooldown"])
    if breaker == "open":
        state = "paused"
    elif int(r.get("consecutive_failures") or 0) > 0 and r.get("last_failure_code"):
        state = "failing"
    else:
        state = "ready"
    return {"source": "company", "provider": r["provider"], "state": state,
            "reason": r.get("last_failure_code") if state != "ready" else None}


def health_summary() -> dict:
    """Instance-wide counts for /health: no tenant ids, no codes."""
    from backend.db.connection import query_one
    s = client._settings()
    from datetime import timedelta
    threshold_time = store._now() - timedelta(seconds=s["cooldown"])
    r = query_one(
        """SELECT COUNT(*) FILTER (WHERE enabled) AS configured,
                  COUNT(*) FILTER (WHERE enabled AND breaker_opened_at > %s) AS paused,
                  COUNT(*) FILTER (WHERE enabled AND consecutive_failures > 0) AS failing
             FROM tenant_ai_providers""", (threshold_time,)) or {}
    return {"tenants_with_own_provider": int(r.get("configured") or 0),
            "paused": int(r.get("paused") or 0), "failing": int(r.get("failing") or 0)}
