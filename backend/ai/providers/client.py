"""The tenant's AI client: one interface, retries, a breaker, and honest failure.

`TenantLLMClient(cfg).messages.create(...)` has the exact surface of the
instance client in `backend/ai/local_llm.py`, so every consumer works
unchanged. What it adds, in order, on every call:

1. Breaker check. Open -> `circuit_open` at once (no network, no wait). Past
   its cooldown -> exactly one caller is let through as a probe.
2. Per-tenant concurrency limit (`ai_provider_max_concurrent_per_tenant`).
3. The request, through `transport.request` (SSRF rules, pinned DNS, no
   redirects, size and time ceilings).
4. Retries — only for failures where the request was refused or never
   arrived (`ProviderError.retryable`): 429 and 5xx/529 answers, connection
   failures and connect timeouts. Jittered exponential backoff, honouring
   Retry-After (capped), all inside one overall deadline. A read timeout or a
   connection dropped mid-answer is NEVER retried: the provider may have
   processed and billed it, and a blind repeat would bill it twice.
5. Recording: success (tokens, last_success_at, breaker closed) or failure
   (code, scrubbed message, consecutive count, breaker), plus the daily usage
   row; and once per UTC day a visible activity event naming the problem.

It never falls back to another provider. A failure raises `ProviderError`;
every consumer already catches exceptions and degrades to its rule-based text,
and the ones that show a reason read `.code` off it.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

from backend.ai.providers import adapters, catalog, store, transport
from backend.ai.providers.errors import COUNTS_FOR_BREAKER, ProviderError, classify_http, scrub

log = logging.getLogger(__name__)

# Seams for tests: a sleep that does not sleep and a deterministic jitter. The
# breaker's clock is `store._now`.
_sleep: Callable[[float], None] = time.sleep
_random: Callable[[], float] = random.random

BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0
MIN_ATTEMPT_S = 1.0

_semaphores: dict[str, threading.BoundedSemaphore] = {}
_sem_lock = threading.Lock()


def _settings():
    from backend.service_config.resolver import effective
    cfg = effective()
    return {
        "connect": float(cfg.ai_provider_connect_timeout_s),
        "read": float(cfg.ai_provider_read_timeout_s),
        "deadline": float(cfg.ai_provider_total_deadline_s),
        "retries": max(0, int(cfg.ai_provider_max_retries)),
        "threshold": max(1, int(cfg.ai_provider_breaker_threshold)),
        "cooldown": max(1.0, float(cfg.ai_provider_breaker_cooldown_s)),
        "concurrency": max(1, int(cfg.ai_provider_max_concurrent_per_tenant)),
        "max_tokens": max(16, int(cfg.ai_provider_max_output_tokens)),
        "allow_private": bool(cfg.ai_provider_allow_private_hosts),
    }


def _semaphore(tenant_id: str, size: int) -> threading.BoundedSemaphore:
    with _sem_lock:
        sem = _semaphores.get(tenant_id)
        if sem is None or getattr(sem, "_stockai_size", None) != size:
            sem = threading.BoundedSemaphore(size)
            sem._stockai_size = size  # type: ignore[attr-defined]
            _semaphores[tenant_id] = sem
        return sem


def base_url_of(cfg: store.ProviderConfig) -> str:
    spec = catalog.get(cfg.provider)
    if spec is None:
        raise ProviderError("bad_request", f"unknown provider {cfg.provider!r}",
                            params={"reason": "unknown_provider"})
    return cfg.base_url if spec.custom_base_url else spec.base_url


def backoff_delay(attempt: int, retry_after: Optional[float]) -> float:
    if retry_after is not None:
        return min(retry_after, BACKOFF_CAP_S * 2)
    raw = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** attempt))
    return raw * (0.5 + 0.5 * _random())  # "equal jitter"


def _one_exchange(cfg: store.ProviderConfig, *, method: str, path: str, body: Optional[dict],
                  deadline: float, s: dict) -> transport.HttpResult:
    spec = catalog.get(cfg.provider)
    base = base_url_of(cfg)
    if spec.wire == "anthropic":
        headers = adapters.anthropic_headers(api_key=cfg.api_key)
    else:
        headers = adapters.openai_headers(spec, api_key=cfg.api_key, base_url=base,
                                          organization=cfg.organization, project=cfg.project)
    if method == "GET":
        headers.pop("Content-Type", None)
    return transport.request(method, adapters.join_url(base, path), headers=headers,
                             json_body=body, connect_timeout=s["connect"],
                             read_timeout=s["read"], deadline=deadline,
                             allow_private_hosts=s["allow_private"])


def call_with_retries(cfg: store.ProviderConfig, *, method: str, path: str,
                      body: Optional[dict], budget_s: float, s: dict):
    """Run one logical request with the retry policy in the module header.
    Returns (HttpResult with a 2xx status, attempts)."""
    deadline_mono = time.monotonic() + max(1.0, min(budget_s, s["deadline"]))
    attempt = 0
    while True:
        try:
            res = _one_exchange(cfg, method=method, path=path, body=body,
                                deadline=deadline_mono, s=s)
            if 200 <= res.status < 300:
                return res, attempt + 1
            raise classify_http(res.status, res.body, res.headers, model=cfg.model,
                                secrets=cfg.secrets())
        except ProviderError as exc:
            exc.message = scrub(exc.message, cfg.secrets())
            if attempt >= s["retries"] or not exc.retryable:
                raise
            delay = backoff_delay(attempt, exc.retry_after)
            if time.monotonic() + delay + MIN_ATTEMPT_S > deadline_mono:
                raise
            log.info("[ai-provider] tenant=%s provider=%s %s, retry %d in %.1fs",
                     cfg.tenant_id, cfg.provider, exc.code, attempt + 1, delay)
            _sleep(delay)
            attempt += 1


def _alert_once_a_day(cfg: store.ProviderConfig, exc: ProviderError) -> None:
    """The visible trace of a failing provider, at most once per UTC day."""
    if exc.code not in COUNTS_FOR_BREAKER and exc.code != "circuit_open":
        return
    try:
        if not store.claim_daily_alert(cfg.tenant_id):
            return
        from backend.activity.events import record_event
        record_event(cfg.tenant_id, "system", "ai_provider.failing",
                     resource=cfg.provider,
                     details={"provider": cfg.provider, "model": cfg.model},
                     reason="ai_provider_call_failed",
                     reason_params={"error_code": exc.code, "provider": cfg.provider},
                     status="failed")
    except Exception:  # noqa: BLE001 - the alert must never break the answer path
        log.error("[ai-provider] could not record the daily failure event", exc_info=True)


class _TenantMessages:
    def __init__(self, cfg: store.ProviderConfig, timeout: float):
        self._cfg = cfg
        self._timeout = timeout

    def create(self, model: Optional[str] = None, max_tokens: int = 1024,
               system: Optional[str] = None, messages: Optional[list] = None,
               tools: Optional[list] = None, tool_choice: Optional[str] = None,
               **_ignored):
        """`model` is accepted and ignored, like the instance client: the
        company's configured model is what runs."""
        cfg = self._cfg
        s = _settings()
        spec = catalog.get(cfg.provider)
        if spec is None:
            raise ProviderError("bad_request", "unknown provider", params={"reason": "unknown_provider"})

        if spec.key_required and not cfg.api_key:
            # The stored key cannot be read (the encryption key changed) or is
            # missing. Sending the request without one would come back as a
            # provider 401 and send the admin to check a key that is fine at
            # the provider; say it here, without a network call.
            exc = ProviderError("invalid_key", "no usable API key is stored; enter it again",
                                params={"reason": "missing"})
            store.record_failure(cfg, exc.code, exc.message, counts=True, threshold=s["threshold"])
            _alert_once_a_day(cfg, exc)
            raise exc

        r = store.row(cfg.tenant_id)
        state = store.breaker_state(r, s["cooldown"])
        if state == "open" or (state == "half_open"
                               and not store.claim_probe(cfg.tenant_id, s["cooldown"])):
            exc = ProviderError("circuit_open", "paused after repeated failures",
                                params={"last_code": (r or {}).get("last_failure_code")})
            _alert_once_a_day(cfg, exc)
            raise exc

        cap = min(s["max_tokens"], cfg.max_tokens or s["max_tokens"])
        budget = max(1.0, min(float(self._timeout or s["deadline"]), s["deadline"]))
        if spec.wire == "anthropic":
            body = adapters.anthropic_body(model=cfg.model, system=system, messages=messages or [],
                                           max_tokens=min(max_tokens, cap), tools=tools,
                                           tool_choice=tool_choice)
        else:
            body = adapters.openai_body(spec, model=cfg.model, system=system,
                                        messages=messages or [], max_tokens=min(max_tokens, cap),
                                        tools=tools, tool_choice=tool_choice,
                                        temperature=cfg.temperature)

        sem = _semaphore(cfg.tenant_id, s["concurrency"])
        if not sem.acquire(timeout=min(10.0, budget)):
            raise ProviderError("rate_limited", "too many AI calls in flight for this company",
                                params={"scope": "stockai"})
        try:
            try:
                res, _attempts = call_with_retries(cfg, method="POST", path=adapters.chat_path(spec),
                                                   body=body, budget_s=budget, s=s)
                try:
                    data = res.json()
                except ValueError:
                    raise ProviderError("malformed_response", "answer is not JSON",
                                        params={"reason": "not_json"}, phase="read")
                parse = adapters.parse_anthropic if spec.wire == "anthropic" else adapters.parse_openai
                resp = parse(data, model=cfg.model, secrets=cfg.secrets())
            except ProviderError as exc:
                exc.message = scrub(exc.message, cfg.secrets())
                store.record_failure(cfg, exc.code, exc.message,
                                     counts=exc.code in COUNTS_FOR_BREAKER,
                                     threshold=s["threshold"])
                log.warning("[ai-provider] tenant=%s provider=%s model=%s failed: %s",
                            cfg.tenant_id, cfg.provider, cfg.model, exc.code)
                _alert_once_a_day(cfg, exc)
                raise
            store.record_success(cfg, resp.usage.input_tokens, resp.usage.output_tokens)
            return resp
        finally:
            sem.release()


class TenantLLMClient:
    """The company's own provider, behind the instance client's surface."""

    is_tenant_provider = True

    def __init__(self, cfg: store.ProviderConfig, timeout: float = 60.0):
        self.provider = cfg.provider
        self.model = cfg.model
        self.messages = _TenantMessages(cfg, timeout)


# ── Connection test and model list (the settings card) ───────────────────────

def list_models(cfg: store.ProviderConfig, *, budget_s: float = 10.0) -> list[dict]:
    spec = catalog.get(cfg.provider)
    s = _settings()
    path = adapters.models_path() + ("?limit=100" if spec.wire == "anthropic" else "")
    res, _ = call_with_retries(cfg, method="GET", path=path, body=None, budget_s=budget_s,
                               s={**s, "retries": 0})
    try:
        data = res.json()
    except ValueError:
        raise ProviderError("malformed_response", "model list is not JSON",
                            params={"reason": "not_json"}, phase="read")
    return adapters.parse_models(data, spec)


def test_connection(cfg: store.ProviderConfig, *, budget_s: float = 20.0) -> dict:
    """A tiny real completion plus the model list. Never raises; never
    returns the key. Not counted against the breaker (a person is testing),
    but a success closes a breaker: it is the best probe there is."""
    spec = catalog.get(cfg.provider)
    s = _settings()
    t0 = time.monotonic()
    result: dict = {"ok": False, "provider": cfg.provider, "model": cfg.model,
                    "latency_ms": None, "models": [], "models_error": None, "error": None,
                    "reply_preview": None}
    try:
        if spec is None:
            raise ProviderError("bad_request", "unknown provider", params={"reason": "unknown_provider"})
        if spec.key_required and not cfg.api_key:
            raise ProviderError("invalid_key", "no API key stored", params={"reason": "missing"})
        prompt = [{"role": "user", "content": "Reply with the single word: OK"}]
        if spec.wire == "anthropic":
            body = adapters.anthropic_body(model=cfg.model, system=None, messages=prompt,
                                           max_tokens=16, tools=None, tool_choice=None)
        else:
            body = adapters.openai_body(spec, model=cfg.model, system=None, messages=prompt,
                                        max_tokens=16, tools=None, tool_choice=None,
                                        temperature=cfg.temperature)
        res, _ = call_with_retries(cfg, method="POST", path=adapters.chat_path(spec), body=body,
                                   budget_s=budget_s, s={**s, "retries": 0})
        try:
            data = res.json()
        except ValueError:
            raise ProviderError("malformed_response", "answer is not JSON",
                                params={"reason": "not_json"}, phase="read")
        parse = adapters.parse_anthropic if spec.wire == "anthropic" else adapters.parse_openai
        try:
            resp = parse(data, model=cfg.model, secrets=cfg.secrets())
            preview = (resp.content[0].text if resp.content else "")[:40]
        except ProviderError as exc:
            # A 16-token budget can be eaten by a reasoning model before any
            # visible text: the key, the URL and the model all worked.
            if exc.code == "malformed_response" and exc.params.get("reason") == "truncated":
                preview = ""
            else:
                raise
        result.update(ok=True, latency_ms=int((time.monotonic() - t0) * 1000),
                      reply_preview=scrub(preview, cfg.secrets()))
    except ProviderError as exc:
        result["error"] = {"code": exc.code, "params": exc.params,
                           "message": scrub(exc.message, cfg.secrets()), "status": exc.status}
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    except Exception as exc:  # noqa: BLE001 - a test must answer, whatever happened
        log.error("[ai-provider] connection test crashed: %s", type(exc).__name__)
        result["error"] = {"code": "malformed_response", "params": {"reason": "unexpected"},
                           "message": type(exc).__name__, "status": None}
        return result
    if spec.lists_models:
        try:
            result["models"] = list_models(cfg, budget_s=10.0)
        except ProviderError as exc:
            result["models_error"] = {"code": exc.code, "params": exc.params}
        except Exception:  # noqa: BLE001
            result["models_error"] = {"code": "malformed_response", "params": {}}
    return result
