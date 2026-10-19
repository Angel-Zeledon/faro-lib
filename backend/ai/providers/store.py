"""The `tenant_ai_providers` row: the company's choice, its key and its health.

Rules this module keeps, because each one is a way a secret leaks:

* The key is stored ONLY as Fernet ciphertext (`service_config/crypto.py`, the
  same key every stored credential uses). With no encryption key available the
  save is refused; there is no plaintext path.
* Nothing that reads this table for display selects `api_key_enc`. `masked()`
  returns `••••abcd` built from the stored last four characters.
* The key is replace-only: an empty or omitted key on save keeps the stored
  one, as long as the provider did not change. Switching provider without a new
  key is refused, because the old provider's key would be sent to the new one.
* Errors stored here (`last_failure_message`) were scrubbed by the caller.

The clock is `_now()` so the breaker can be tested with a fake one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from backend.db.connection import execute, query, query_one

log = logging.getLogger(__name__)

MASK = "••••"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SecretStorageUnavailable(RuntimeError):
    pass


@dataclass
class ProviderConfig:
    tenant_id: str
    provider: str
    model: str
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    organization: str = ""
    project: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    enabled: bool = True

    def secrets(self) -> tuple[str, ...]:
        return (self.api_key,) if self.api_key else ()


_PUBLIC_COLS = """tenant_id, provider, model, base_url, api_key_hint,
    (api_key_enc IS NOT NULL) AS has_key, organization, project, temperature,
    max_tokens, price_input_per_mtok, price_output_per_mtok, enabled, created_at, created_by, updated_at, updated_by,
    last_test_at, last_test_ok, last_test_code, last_test_latency_ms,
    last_success_at, last_failure_at, last_failure_code, last_failure_message,
    consecutive_failures, breaker_opened_at"""


def _encrypt(value: str) -> str:
    from backend.service_config import crypto
    if not crypto.secret_storage_enabled():
        raise SecretStorageUnavailable("no encryption key: the API key cannot be stored")
    return crypto.encrypt_value(value)


def _decrypt(token: str) -> str:
    from backend.service_config import crypto
    return crypto.decrypt_value(token)


def row(tenant_id: str) -> Optional[dict]:
    """The row for display: never the ciphertext."""
    return query_one(f"SELECT {_PUBLIC_COLS} FROM tenant_ai_providers WHERE tenant_id = %s",
                     (tenant_id,))


def load_active(tenant_id: str) -> Optional[ProviderConfig]:
    """The tenant's provider with its key in clear, or None when the tenant
    has no row or switched it off — in which case StockAI's default applies,
    exactly as before this feature existed."""
    r = query_one(
        """SELECT tenant_id, provider, model, base_url, api_key_enc, organization, project,
                  temperature, max_tokens, enabled
             FROM tenant_ai_providers WHERE tenant_id = %s""", (tenant_id,))
    if not r or not r["enabled"]:
        return None
    return _to_config(r)


def load_any(tenant_id: str) -> Optional[ProviderConfig]:
    r = query_one(
        """SELECT tenant_id, provider, model, base_url, api_key_enc, organization, project,
                  temperature, max_tokens, enabled
             FROM tenant_ai_providers WHERE tenant_id = %s""", (tenant_id,))
    return _to_config(r) if r else None


def _to_config(r: dict) -> ProviderConfig:
    key = ""
    if r.get("api_key_enc"):
        try:
            key = _decrypt(r["api_key_enc"])
        except Exception:  # noqa: BLE001 - a rotated encryption key: say so, never crash
            log.error("[ai-provider] stored key for tenant %s cannot be decrypted "
                      "(encryption key changed?)", r["tenant_id"])
            key = ""
    return ProviderConfig(
        tenant_id=r["tenant_id"], provider=r["provider"], model=r["model"],
        base_url=r.get("base_url") or "", api_key=key,
        organization=r.get("organization") or "", project=r.get("project") or "",
        temperature=r.get("temperature"), max_tokens=r.get("max_tokens"),
        enabled=bool(r.get("enabled", True)),
    )


def masked(r: Optional[dict]) -> Optional[str]:
    if not r or not r.get("has_key"):
        return None
    return MASK + (r.get("api_key_hint") or "")


def save(tenant_id: str, *, provider: str, model: str, base_url: str, api_key: Optional[str],
         organization: str, project: str, temperature: Optional[float],
         max_tokens: Optional[int], enabled: bool, actor: str,
         price_input_per_mtok: Optional[float] = None,
         price_output_per_mtok: Optional[float] = None) -> dict:
    """Insert or replace the tenant's choice. `api_key=None` keeps the stored
    one. Health and breaker state are reset: they described the old settings."""
    enc = hint = None
    if api_key:
        enc = _encrypt(api_key)
        hint = api_key[-4:] if len(api_key) >= 8 else ""
    execute(
        """INSERT INTO tenant_ai_providers
               (tenant_id, provider, model, base_url, api_key_enc, api_key_hint,
                organization, project, temperature, max_tokens, enabled,
                created_by, updated_by, price_input_per_mtok, price_output_per_mtok)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id) DO UPDATE SET
               provider = EXCLUDED.provider, model = EXCLUDED.model,
               base_url = EXCLUDED.base_url,
               api_key_enc = COALESCE(EXCLUDED.api_key_enc, tenant_ai_providers.api_key_enc),
               api_key_hint = CASE WHEN EXCLUDED.api_key_enc IS NULL
                                   THEN tenant_ai_providers.api_key_hint
                                   ELSE EXCLUDED.api_key_hint END,
               organization = EXCLUDED.organization, project = EXCLUDED.project,
               temperature = EXCLUDED.temperature, max_tokens = EXCLUDED.max_tokens,
               enabled = EXCLUDED.enabled,
               price_input_per_mtok = EXCLUDED.price_input_per_mtok,
               price_output_per_mtok = EXCLUDED.price_output_per_mtok,
               updated_at = NOW(), updated_by = EXCLUDED.updated_by,
               last_failure_at = NULL, last_failure_code = NULL, last_failure_message = NULL,
               consecutive_failures = 0, breaker_opened_at = NULL,
               last_test_at = NULL, last_test_ok = NULL, last_test_code = NULL,
               last_test_latency_ms = NULL""",
        (tenant_id, provider, model, base_url or None, enc, hint,
         organization or None, project or None, temperature, max_tokens, enabled,
         actor, actor, price_input_per_mtok, price_output_per_mtok),
    )
    return row(tenant_id) or {}


def delete(tenant_id: str) -> bool:
    r = query("DELETE FROM tenant_ai_providers WHERE tenant_id = %s RETURNING tenant_id",
              (tenant_id,))
    return bool(r)


def record_test(tenant_id: str, *, ok: bool, code: Optional[str], latency_ms: Optional[int]) -> None:
    execute(
        """UPDATE tenant_ai_providers
              SET last_test_at = %s, last_test_ok = %s, last_test_code = %s,
                  last_test_latency_ms = %s
            WHERE tenant_id = %s""",
        (_now(), ok, code, latency_ms, tenant_id))


# ── Health, breaker and usage ────────────────────────────────────────────────

def record_success(cfg: ProviderConfig, input_tokens: int, output_tokens: int) -> None:
    execute(
        """UPDATE tenant_ai_providers
              SET last_success_at = %s, consecutive_failures = 0, breaker_opened_at = NULL
            WHERE tenant_id = %s""", (_now(), cfg.tenant_id))
    _usage(cfg, calls=1, failures=0, input_tokens=input_tokens, output_tokens=output_tokens)


def record_failure(cfg: ProviderConfig, code: str, message: str, *, counts: bool,
                   threshold: int) -> int:
    """Store the failure; open the breaker when the threshold is reached.
    Returns the consecutive count after this failure."""
    now = _now()
    r = query_one(
        """UPDATE tenant_ai_providers
              SET last_failure_at = %s, last_failure_code = %s, last_failure_message = %s,
                  consecutive_failures = consecutive_failures + %s,
                  breaker_opened_at = CASE
                      WHEN breaker_opened_at IS NOT NULL THEN %s
                      WHEN consecutive_failures + %s >= %s THEN %s
                      ELSE NULL END
            WHERE tenant_id = %s
        RETURNING consecutive_failures""",
        (now, code, message[:240], 1 if counts else 0, now,
         1 if counts else 0, max(1, threshold), now, cfg.tenant_id))
    _usage(cfg, calls=1, failures=1, input_tokens=0, output_tokens=0)
    return int(r["consecutive_failures"]) if r else 0


def _usage(cfg: ProviderConfig, *, calls: int, failures: int, input_tokens: int,
           output_tokens: int) -> None:
    try:
        execute(
            """INSERT INTO ai_provider_usage_daily
                   (tenant_id, day, provider, model, calls, failures, input_tokens, output_tokens)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, day, provider, model) DO UPDATE SET
                   calls = ai_provider_usage_daily.calls + EXCLUDED.calls,
                   failures = ai_provider_usage_daily.failures + EXCLUDED.failures,
                   input_tokens = ai_provider_usage_daily.input_tokens + EXCLUDED.input_tokens,
                   output_tokens = ai_provider_usage_daily.output_tokens + EXCLUDED.output_tokens""",
            (cfg.tenant_id, _now().date(), cfg.provider, cfg.model[:200], calls, failures,
             max(0, int(input_tokens or 0)), max(0, int(output_tokens or 0))))
    except Exception:  # noqa: BLE001 - a meter row is never worth failing an answer
        log.error("[ai-provider] usage row not written (tenant=%s)", cfg.tenant_id, exc_info=True)


def breaker_state(r: Optional[dict], cooldown_s: float) -> str:
    """closed | open | half_open, from the row and the clock."""
    if not r or not r.get("breaker_opened_at"):
        return "closed"
    opened = r["breaker_opened_at"]
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    return "open" if _now() - opened < timedelta(seconds=cooldown_s) else "half_open"


def claim_probe(tenant_id: str, cooldown_s: float) -> bool:
    """Atomically let ONE caller through a breaker whose cooldown is over: it
    re-arms the breaker to now, so concurrent callers see it open while the
    probe runs. A success closes it (`record_success`)."""
    now = _now()
    r = query_one(
        """UPDATE tenant_ai_providers SET breaker_opened_at = %s
            WHERE tenant_id = %s AND breaker_opened_at IS NOT NULL
              AND breaker_opened_at <= %s
        RETURNING tenant_id""",
        (now, tenant_id, now - timedelta(seconds=cooldown_s)))
    return r is not None


def close_breaker(tenant_id: str) -> None:
    execute("""UPDATE tenant_ai_providers
                  SET consecutive_failures = 0, breaker_opened_at = NULL
                WHERE tenant_id = %s""", (tenant_id,))


def claim_daily_alert(tenant_id: str) -> bool:
    """True once per tenant per UTC day: the caller then records the visible
    event. Atomic, so two workers failing at once do not both alert."""
    today = _now().date()
    r = query_one(
        """UPDATE tenant_ai_providers SET last_alerted_on = %s
            WHERE tenant_id = %s AND (last_alerted_on IS NULL OR last_alerted_on < %s)
        RETURNING tenant_id""", (today, tenant_id, today))
    return r is not None


def usage_since(tenant_id: str, days: int = 7) -> dict[str, Any]:
    """Calls and tokens over the last `days` UTC days, plus an ESTIMATED cost
    in USD from the prices the company typed (`price_*_per_mtok`).

    `estimated_cost_usd` is None, never 0, when no price is set: a missing
    price must not read as a free bill. The estimate applies today's prices to
    the whole window, so it is an estimate and the card says so."""
    since: date = _now().date() - timedelta(days=days - 1)
    r = query_one(
        """SELECT COALESCE(SUM(calls), 0) AS calls, COALESCE(SUM(failures), 0) AS failures,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens
             FROM ai_provider_usage_daily WHERE tenant_id = %s AND day >= %s""",
        (tenant_id, since)) or {}
    price = query_one(
        "SELECT price_input_per_mtok, price_output_per_mtok FROM tenant_ai_providers "
        "WHERE tenant_id = %s", (tenant_id,)) or {}
    pin, pout = price.get("price_input_per_mtok"), price.get("price_output_per_mtok")
    tin, tout = int(r.get("input_tokens") or 0), int(r.get("output_tokens") or 0)
    cost = None
    if pin is not None or pout is not None:
        cost = round((tin * float(pin or 0) + tout * float(pout or 0)) / 1_000_000, 4)
    return {"days": days, "calls": int(r.get("calls") or 0),
            "failures": int(r.get("failures") or 0),
            "input_tokens": tin, "output_tokens": tout, "estimated_cost_usd": cost}
