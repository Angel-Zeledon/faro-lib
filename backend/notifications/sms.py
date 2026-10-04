"""
SMS notifications via the Twilio REST API.

Reuses TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN and adds TWILIO_SMS_FROM (a plain
E.164 sender — the whatsapp:-prefixed sender cannot send SMS). Without them
every send is a logged no-op, so callers may fire unconditionally.

Recipient numbers come from users.whatsapp_number (E.164, e.g. +573001234567).
"""

import logging

from backend.service_config.resolver import effective

log = logging.getLogger(__name__)


def is_configured(tenant_id: str | None = None) -> bool:
    """True when this scope can actually send. `tenant_id` asks it for a tenant
    that configured its own Twilio sender — see whatsapp.is_configured()."""
    cfg = effective(tenant_id)
    return bool(
        cfg.twilio_account_sid
        and cfg.twilio_auth_token
        and cfg.twilio_sms_from
    )


def failure_reason(tenant_id: str | None = None) -> str:
    """Stable code for why a send failed — mirrors email.failure_reason()."""
    return "transport_error" if is_configured(tenant_id) else "not_configured"


def _transport_send(to_number: str, body: str, tenant_id: str | None = None) -> None:
    """Raw Twilio HTTP call. Raises on failure. Independently testable."""
    import httpx

    cfg = effective(tenant_id)
    sid = cfg.twilio_account_sid
    resp = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, cfg.twilio_auth_token),
        data={
            "From": cfg.twilio_sms_from,
            "To": to_number,
            "Body": body,
        },
        timeout=15,
    )
    resp.raise_for_status()


def send_sms(to_number: str, body: str, tenant_id: str | None = None) -> bool:
    """
    Send an SMS to a +E164 number. Returns True on success. Never raises —
    a failed notification must not break the message send it decorates.
    """
    if not is_configured(tenant_id):
        log.warning("Twilio SMS not configured — SMS not sent to %s", to_number)
        return False
    if not to_number:
        return False

    try:
        _send(to_number, body, tenant_id)
        log.info("SMS sent → %s", to_number)
        return True
    except Exception as exc:
        log.error("SMS send failed to %s: %s", to_number, exc)
        return False


def _send(to_number: str, body: str, tenant_id: str | None = None) -> None:
    # Thin wrapper so tests (conftest) can patch the single `_send` entrypoint
    # while _transport_send stays independently testable — same convention as
    # notifications/email.py and whatsapp.py.
    _transport_send(to_number, body, tenant_id)
