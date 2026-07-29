"""
WhatsApp notifications via the Twilio REST API.

Configured with TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM
in .env. Without them every send is a logged no-op, so the daily alert loop
can call this unconditionally.

Recipient numbers come from users.whatsapp_number (E.164, e.g. +573001234567).
"""

import logging

from backend.config import settings
from backend.notifications.locale import render_es

log = logging.getLogger(__name__)

# Critical SKUs listed before the rest collapse into a "y N más" line.
_ALERT_MAX_CRITICAL_LINES = 5


def is_configured() -> bool:
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_whatsapp_from
    )


def failure_reason() -> str:
    """Stable code for why a send failed — mirrors email.failure_reason()."""
    return "transport_error" if is_configured() else "not_configured"


def _transport_send(to_number: str, body: str, media_url: str | None) -> str | None:
    """Raw Twilio HTTP call. Raises on failure. Independently testable.
    Returns the Twilio message SID so callers can confirm delivery later."""
    import httpx

    sid = settings.twilio_account_sid
    data = {
        "From": settings.twilio_whatsapp_from,
        "To": f"whatsapp:{to_number}",
        "Body": body,
    }
    if media_url:
        data["MediaUrl"] = media_url

    resp = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, settings.twilio_auth_token),
        data=data,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("sid")


def send_whatsapp(to_number: str, body: str, media_url: str | None = None) -> bool:
    """
    Send a WhatsApp text (optionally with a media attachment, e.g. a PDF URL
    Twilio will fetch and deliver) to +E164 number. Returns True on success.
    Never raises — alerting must not break the caller's loop.
    """
    if not is_configured():
        log.warning("Twilio not configured — WhatsApp not sent to %s", to_number)
        return False
    if not to_number:
        return False

    try:
        _send(to_number, body, media_url)
        log.info("WhatsApp sent → %s", to_number)
        return True
    except Exception as exc:
        log.error("WhatsApp send failed to %s: %s", to_number, exc)
        return False


def _send(to_number: str, body: str, media_url: str | None) -> str | None:
    # Thin wrapper so tests (conftest) can patch the single `_send` entrypoint
    # while the dispatch logic in _transport_send stays independently testable —
    # same convention as notifications/email.py.
    return _transport_send(to_number, body, media_url)


def fetch_message_status(message_sid: str) -> str | None:
    """Current Twilio delivery status of a sent message ('delivered', 'failed',
    'undelivered', …), or None when the lookup itself fails."""
    import httpx

    sid = settings.twilio_account_sid
    try:
        resp = httpx.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages/{message_sid}.json",
            auth=(sid, settings.twilio_auth_token),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("status")
    except Exception as exc:
        log.warning("WhatsApp status lookup failed for %s: %s", message_sid, exc)
        return None


def send_whatsapp_and_confirm(to_number: str, body: str, wait_seconds: float = 8.0) -> bool:
    """
    Send a WhatsApp text and confirm Twilio actually delivered it.

    Twilio ACCEPTS the API call and fails asynchronously (e.g. error 63015 when
    the recipient's sandbox opt-in expired), so a plain send_whatsapp() returns
    True for messages that never arrive. This variant polls the message status
    once after `wait_seconds` and returns False on failed/undelivered, letting
    the caller fall back to another channel. Blocking — call it from a
    background task, never inline in a request. Never raises.
    """
    if not is_configured() or not to_number:
        return False
    try:
        message_sid = _send(to_number, body, None)
    except Exception as exc:
        log.error("WhatsApp send failed to %s: %s", to_number, exc)
        return False

    # A patched/mocked transport (tests) yields no real SID — treat as accepted.
    if not isinstance(message_sid, str) or not message_sid:
        return True

    import time
    time.sleep(wait_seconds)
    status = fetch_message_status(message_sid)
    if status in ("failed", "undelivered"):
        log.warning("WhatsApp %s to %s not delivered (status=%s)", message_sid, to_number, status)
        return False
    log.info("WhatsApp sent → %s (status=%s)", to_number, status or "unknown")
    return True


def build_inventory_alert_text(
    critical_items: list[dict],
    warning_items: list[dict],
    inventory_url: str,
    transfer_count: int = 0,
) -> str:
    """
    Compact daily-alert message: WhatsApp favours short, scannable text.

    Callers pass the FULL lists: the counts are taken before the message is
    trimmed to _ALERT_MAX_CRITICAL_LINES, so "y N más" states how many SKUs are
    really hidden. Passing a pre-sliced list made it say "y 5 más" while 37
    products were about to run out.
    """
    lines: list[str] = []
    n_crit = len(critical_items)
    if n_crit:
        lines.append(render_es(
            "alert_whatsapp_critical_one" if n_crit == 1 else "alert_whatsapp_critical_many",
            n=n_crit,
        ))
        for i in critical_items[:_ALERT_MAX_CRITICAL_LINES]:
            days = i.get("coverage_days")
            # "d" is the language-neutral day symbol, not copy.
            days_str = f"{days:.0f}d" if days is not None else "—"
            qty = i.get("recommended_qty")
            qty_str = render_es("alert_whatsapp_order_qty", qty=f"{qty:,.0f}") if qty else ""
            lines.append(f"  • {i.get('display_name') or i.get('sku')} ({days_str}{qty_str})")
        if n_crit > _ALERT_MAX_CRITICAL_LINES:
            lines.append(render_es("alert_whatsapp_more", n=n_crit - _ALERT_MAX_CRITICAL_LINES))
    n_warn = len(warning_items)
    if n_warn:
        lines.append(render_es("alert_whatsapp_warning", n=n_warn))
    if transfer_count > 0:
        # Network-aware suggestion (feature 5.4): stock exists, it's just in
        # the wrong warehouse — no money needs to be spent.
        lines.append(render_es(
            "alert_whatsapp_transfer_one" if transfer_count == 1 else "alert_whatsapp_transfer_many",
            n=transfer_count,
        ))
    lines.append(render_es("alert_whatsapp_cta", url=inventory_url))
    return "\n".join(lines)


def build_freshness_reminder_text(
    sales_age_days: int | None,
    stock_age_days: int | None,
    upload_url: str,
) -> str:
    """
    Data-freshness reminder for the highest open-rate channel in LatAm.

    Only the clock that triggered the reminder is mentioned — `stock_age_days`
    is None unless the stock itself went blind — so the message never pads
    itself with an age that is not part of the problem.
    """
    lines: list[str] = []
    if sales_age_days is not None:
        lines.append(render_es("freshness_whatsapp_sales", days=sales_age_days))
    if stock_age_days is not None:
        lines.append(render_es("freshness_whatsapp_stock", days=stock_age_days))
    lines.append(render_es("freshness_whatsapp_cta", url=upload_url))
    return "\n".join(lines)


def _line_label(item: dict) -> str:
    return str(item.get("display_name") or item.get("sku") or "")


def build_po_supplier_text(supplier_name: str, po_log_id: str, items: list[dict]) -> str:
    """Short WhatsApp message accompanying a PO PDF sent to a supplier."""
    n = len(items)
    lines = [
        render_es("po_supplier_header", supplier=supplier_name),
        render_es("po_supplier_count", n=n, s="s" if n != 1 else ""),
    ]
    for i in items[:10]:
        lines.append(render_es(
            "po_supplier_line",
            name=_line_label(i), qty=f"{(i.get('final_qty') or 0):,.0f}",
        ))
    if n > 10:
        lines.append(render_es("po_supplier_more", n=n - 10))
    lines.append(render_es("po_supplier_footer", reference=po_log_id))
    return "\n".join(lines)


def build_po_forward_text(reference: str, groups: list[dict]) -> str:
    """
    The message the BUYER receives on their own WhatsApp and forwards to each
    supplier (PENDIENTES #1). `groups`: [{"supplier": name, "items": [...]}] —
    one section per supplier so a mixed order can be forwarded piece by piece.
    """
    lines = [render_es("po_forward_header", reference=reference)]
    for g in groups:
        items = g.get("items") or []
        lines.append(render_es("po_forward_supplier", supplier=g.get("supplier") or "—"))
        for i in items[:15]:
            lines.append(render_es(
                "po_forward_line",
                name=_line_label(i), qty=f"{(i.get('final_qty') or 0):,.0f}",
            ))
        if len(items) > 15:
            lines.append(render_es("po_forward_more", n=len(items) - 15))
    lines.append(render_es("po_forward_footer"))
    return "\n".join(lines)
