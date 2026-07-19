"""
WhatsApp notifications via the Twilio REST API.

Configured with TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM
in .env. Without them every send is a logged no-op, so the daily alert loop
can call this unconditionally.

Recipient numbers come from users.whatsapp_number (E.164, e.g. +573001234567).
"""

import logging

from backend.config import settings

log = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_whatsapp_from
    )


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
        log.info("WhatsApp sent → %s", to_number)
        return True
    except Exception as exc:
        log.error("WhatsApp send failed to %s: %s", to_number, exc)
        return False


def build_inventory_alert_text(
    critical_items: list[dict],
    warning_items: list[dict],
    inventory_url: str,
) -> str:
    """Compact daily-alert message: WhatsApp favours short, scannable text."""
    lines: list[str] = []
    n_crit = len(critical_items)
    if n_crit:
        lines.append(f"🔴 *Faro*: {n_crit} producto{'s' if n_crit != 1 else ''} se agota{'n' if n_crit != 1 else ''} antes de tu próximo pedido")
        for i in critical_items[:5]:
            days = i.get("coverage_days")
            days_str = f"{days:.0f}d" if days is not None else "—"
            qty = i.get("recommended_qty")
            qty_str = f" · pedir {qty:,.0f}" if qty else ""
            lines.append(f"  • {i.get('display_name') or i.get('sku')} ({days_str}{qty_str})")
        if n_crit > 5:
            lines.append(f"  … y {n_crit - 5} más")
    n_warn = len(warning_items)
    if n_warn:
        lines.append(f"🟡 {n_warn} por reabastecer esta semana")
    lines.append(f"Ver y aprobar: {inventory_url}")
    return "\n".join(lines)


def build_po_supplier_text(supplier_name: str, po_log_id: str, items: list[dict]) -> str:
    """Short WhatsApp message accompanying a PO PDF sent to a supplier."""
    n = len(items)
    lines = [
        f"📦 *Nueva orden de compra* para {supplier_name}",
        f"{n} producto{'s' if n != 1 else ''}:",
    ]
    for i in items[:10]:
        qty = i.get("final_qty") or 0
        lines.append(f"  • {i.get('display_name') or i.get('sku')} — {qty:,.0f}")
    if n > 10:
        lines.append(f"  … y {n - 10} más")
    lines.append(f"\nDetalle completo en el PDF adjunto. Referencia: {po_log_id}")
    return "\n".join(lines)
