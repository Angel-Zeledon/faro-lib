"""Live credential checks — "is this key actually good?"

A service can be fully configured and completely broken: an expired key, a
suspended account, a Twilio sender that was never approved, a Pinecone index
deleted last month. Presence of a variable proves nothing, and the place that
discovers the truth today is the 8:00 alert loop, in a log nobody reads.

So every external service that can be reached gets a probe, and the panel has a
button. The rules every probe follows:

  - **It never raises.** A probe reports a failure; it does not become one.
  - **It never sends anything to a customer.** Checking email asks the provider
    about the account, it does not mail someone to see if mail works.
  - **It is cheap.** The LLM probe asks for one token. The point is to
    authenticate, not to evaluate the model.
  - **It says which layer failed**, as a stable code the frontend renders in
    Spanish: `auth_failed` is a wrong key, `unreachable` is a network or DNS
    problem, `timeout` is a provider that accepted the connection and went
    quiet. Those three lead to three different actions and used to look
    identical.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any

from backend.service_config.resolver import effective

log = logging.getLogger(__name__)

# How long any single probe may take. Above the frontend proxy's patience would
# turn a diagnostic into its own outage, so it is deliberately short: a provider
# that cannot answer in 12 seconds is a finding, not a false alarm.
PROBE_TIMEOUT_S = 12.0


@dataclass
class ProbeResult:
    ok: bool
    code: str
    """Stable, English, rendered in Spanish by the frontend. One of:
    `ok`, `not_configured`, `auth_failed`, `unreachable`, `timeout`,
    `rejected`, `missing_dependency`, `unexpected`."""
    detail: str = ""
    """Free text for the log and the panel's expandable line — provider wording,
    never a credential."""
    checked_at: str = dc_field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    extra: dict[str, Any] = dc_field(default_factory=dict)


def _classify_http(exc: Exception) -> ProbeResult:
    """Turn an httpx failure into one of the four codes that lead somewhere."""
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return ProbeResult(False, "timeout", "The provider did not answer in time.")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = (exc.response.text or "")[:300]
        if status in (401, 403):
            return ProbeResult(False, "auth_failed", f"HTTP {status}. {body}")
        if status == 404:
            return ProbeResult(False, "rejected", f"HTTP 404 — resource not found. {body}")
        if status == 429:
            return ProbeResult(False, "rejected", f"HTTP 429 — rate limited or out of quota. {body}")
        return ProbeResult(False, "rejected", f"HTTP {status}. {body}")
    if isinstance(exc, httpx.HTTPError):
        return ProbeResult(False, "unreachable", str(exc)[:300])
    return ProbeResult(False, "unexpected", f"{type(exc).__name__}: {exc}"[:300])


def probe_llm(tenant_id: str | None = None) -> ProbeResult:
    """One-token completion against the configured DeepSeek endpoint."""
    import httpx

    cfg = effective(tenant_id)
    if not cfg.deepseek_api_key:
        return ProbeResult(False, "not_configured", "DEEPSEEK_API_KEY is empty.")

    try:
        resp = httpx.post(
            f"{str(cfg.deepseek_base_url).rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.deepseek_api_key}"},
            json={
                "model": cfg.deepseek_model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=PROBE_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        model = data.get("model") or cfg.deepseek_model
        return ProbeResult(True, "ok", f"Answered as {model}.", extra={"model": model})
    except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
        result = _classify_http(exc)
        log.info("LLM probe failed: %s — %s", result.code, result.detail)
        return result


def probe_email(tenant_id: str | None = None) -> ProbeResult:
    """Authenticate against whichever transport would actually be used.

    Mirrors `_transport_send`'s dispatch exactly — Resend when its key is set,
    SMTP otherwise — because a probe that tests the transport the send would
    NOT use is worse than no probe.
    """
    import httpx

    cfg = effective(tenant_id)

    if cfg.resend_api_key:
        try:
            resp = httpx.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {cfg.resend_api_key}"},
                timeout=PROBE_TIMEOUT_S,
            )
            resp.raise_for_status()
            return ProbeResult(
                True, "ok", "Resend accepted the key.", extra={"transport": "resend"}
            )
        except Exception as exc:  # noqa: BLE001
            result = _classify_http(exc)
            result.extra["transport"] = "resend"
            return result

    if not cfg.smtp_user or not cfg.smtp_pass:
        return ProbeResult(
            False, "not_configured",
            "No RESEND_API_KEY and no SMTP_USER/SMTP_PASS — nothing would be sent.",
        )

    try:
        with smtplib.SMTP(cfg.smtp_server, int(cfg.smtp_port), timeout=PROBE_TIMEOUT_S) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(cfg.smtp_user, cfg.smtp_pass)
        return ProbeResult(
            True, "ok", f"SMTP login accepted at {cfg.smtp_server}.",
            extra={"transport": "smtp"},
        )
    except smtplib.SMTPAuthenticationError as exc:
        return ProbeResult(False, "auth_failed", str(exc)[:300], extra={"transport": "smtp"})
    except (smtplib.SMTPException, OSError) as exc:
        return ProbeResult(False, "unreachable", str(exc)[:300], extra={"transport": "smtp"})
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(False, "unexpected", f"{type(exc).__name__}: {exc}"[:300])


def probe_twilio(tenant_id: str | None = None) -> ProbeResult:
    """Fetch the Twilio account. Authenticates without messaging anyone."""
    import httpx

    cfg = effective(tenant_id)
    sid, token = cfg.twilio_account_sid, cfg.twilio_auth_token
    if not sid or not token:
        return ProbeResult(
            False, "not_configured", "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are empty."
        )

    try:
        resp = httpx.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
            auth=(sid, token),
            timeout=PROBE_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        if status != "active":
            # A suspended account authenticates perfectly and delivers nothing.
            return ProbeResult(
                False, "rejected", f"The Twilio account is '{status}', not active.",
                extra={"account_status": status},
            )
        return ProbeResult(
            True, "ok", f"Twilio account active ({data.get('friendly_name', sid)}).",
            extra={"account_status": status},
        )
    except Exception as exc:  # noqa: BLE001
        return _classify_http(exc)


def probe_rag(tenant_id: str | None = None) -> ProbeResult:
    """Check both halves — the embedder and the index — and say which failed."""
    cfg = effective(tenant_id)
    missing = [
        env for env, val in (
            ("VOYAGEAI_API_KEY", cfg.voyageai_api_key),
            ("PINECONE_API_KEY", cfg.pinecone_api_key),
            ("PINECONE_INDEX", cfg.pinecone_index),
        ) if not val
    ]
    if missing:
        return ProbeResult(False, "not_configured", ", ".join(missing) + " empty.")

    try:
        import voyageai
    except ImportError:
        return ProbeResult(
            False, "missing_dependency",
            "The 'voyageai' package is not installed (pip install voyageai).",
        )
    try:
        from pinecone import Pinecone
    except ImportError:
        return ProbeResult(
            False, "missing_dependency",
            "The 'pinecone' package is not installed (pip install pinecone).",
        )

    try:
        # The same model the real indexer uses, imported rather than repeated:
        # a probe that authenticates against a different model can pass while
        # every actual embed fails.
        from backend.ai.rag_service import EMBED_MODEL

        voyageai.Client(api_key=cfg.voyageai_api_key).embed(
            ["ping"], model=EMBED_MODEL, input_type="query"
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            False, "auth_failed", f"Voyage AI rejected the key: {exc}"[:300],
            extra={"half": "embedder"},
        )

    try:
        index = Pinecone(api_key=cfg.pinecone_api_key).Index(cfg.pinecone_index)
        stats = index.describe_index_stats()
        vectors = getattr(stats, "total_vector_count", None)
        if vectors is None and isinstance(stats, dict):
            vectors = stats.get("total_vector_count")
        return ProbeResult(
            True, "ok",
            f"Index '{cfg.pinecone_index}' reachable ({vectors} vectors).",
            extra={"vectors": vectors},
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            False, "unreachable", f"Pinecone index error: {exc}"[:300],
            extra={"half": "index"},
        )


def probe_secret_storage(tenant_id: str | None = None) -> ProbeResult:
    """Verify the Fernet key round-trips. Nothing external to reach.

    What CAN be wrong here, and is silent until the first save, is the key
    itself: a malformed one only fails when a secret is finally stored, which is
    the moment somebody is typing a credential into `/instalacion` and being
    told it did not save.
    """
    from backend.service_config import store

    if not store.encryption_available():
        return ProbeResult(
            False, "not_configured",
            "INTEGRATIONS_SECRET_KEY is empty — no credential can be stored.",
        )
    try:
        from backend.service_config.crypto import decrypt_value, encrypt_value

        probe_text = "stockai-config-probe"
        if decrypt_value(encrypt_value(probe_text)) != probe_text:
            return ProbeResult(False, "unexpected", "The key did not round-trip.")
        return ProbeResult(True, "ok", "Encryption key is valid.")
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            False, "rejected",
            f"INTEGRATIONS_SECRET_KEY is not a valid Fernet key: {exc}"[:300],
        )


def probe_social_login(tenant_id: str | None = None) -> ProbeResult:
    """Each configured provider is asked whether it recognises our client.

    Google and Apple get a deliberately bogus authorization code: a known
    client is answered `invalid_grant`, an unknown one `invalid_client`.
    Facebook issues an app access token for a correct id/secret pair. Nobody
    is signed in and nothing is sent to anyone. `extra.providers` carries the
    per-provider verdict; the service is `ok` only if every configured one is.
    """
    from backend.auth.social import providers as social

    verdicts = {p: social.probe_provider(p) for p in social.PROVIDERS}
    configured = {p: v for p, v in verdicts.items() if v != "not_configured"}
    if not configured:
        return ProbeResult(False, "not_configured", "No provider is fully configured.",
                           extra={"providers": verdicts})
    failing = {p: v for p, v in configured.items() if v != "ok"}
    if not failing:
        return ProbeResult(True, "ok", "Every configured provider accepted the client.",
                           extra={"providers": verdicts})
    # The worst code wins, so the panel names the action that is needed.
    order = ("auth_failed", "rejected", "unreachable", "timeout")
    code = next((c for c in order if c in failing.values()), "unexpected")
    detail = ", ".join(f"{p}: {v}" for p, v in failing.items())
    return ProbeResult(False, code, detail, extra={"providers": verdicts})


PROBES = {
    "probe_social_login": probe_social_login,
    "probe_llm": probe_llm,
    "probe_email": probe_email,
    "probe_twilio": probe_twilio,
    "probe_rag": probe_rag,
    "probe_secret_storage": probe_secret_storage,
}


def run(probe_name: str, tenant_id: str | None = None) -> ProbeResult:
    """Run a probe by the name the registry gave it."""
    fn = PROBES.get(probe_name)
    if fn is None:
        return ProbeResult(False, "unexpected", f"No probe named {probe_name}.")
    try:
        return fn(tenant_id)
    except Exception as exc:  # noqa: BLE001 - the last net; probes handle their own
        log.exception("Probe %s raised", probe_name)
        return ProbeResult(False, "unexpected", f"{type(exc).__name__}: {exc}"[:300])
