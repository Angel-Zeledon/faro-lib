"""
Spanish copy catalog for backend-only channels (WhatsApp, email, PDF) that the
frontend never renders, so frontend i18n cannot localize them.

Code references an English snake_case key and interpolates params via
``render_es`` — the Spanish string itself lives only here, never inline in
backend logic (see CLAUDE.md, Language section).
"""

from __future__ import annotations

_ES: dict[str, str] = {
    "whatsapp_verification_code": "Tu código de verificación de Faro es: {code}",
    # Daily stockout digest: the rows a channel could not fit are announced, so
    # the listed rows are never mistaken for the full count.
    "alert_email_more_row":  "… y {n} producto{s} más en la misma condición — velos en el tablero",
    "alert_whatsapp_more":   "  … y {n} más",
    # Supplier-facing PO message (sent by Faro straight to the supplier).
    "po_supplier_header":    "📦 *Nueva orden de compra* para {supplier}",
    "po_supplier_count":     "{n} producto{s}:",
    "po_supplier_line":      "  • {name} — {qty}",
    "po_supplier_more":      "  … y {n} más",
    "po_supplier_footer":    "\nDetalle completo en el PDF adjunto. Referencia: {reference}",
    # Buyer-facing PO message: the buyer receives this on their own WhatsApp
    # and forwards it to the supplier, so no Faro↔supplier integration is
    # needed (PENDIENTES #1).
    "po_forward_header":     "📦 *Orden de compra {reference}*",
    "po_forward_supplier":   "\n*{supplier}*",
    "po_forward_line":       "  • {name} — {qty}",
    "po_forward_more":       "  … y {n} más",
    "po_forward_footer":     "\nReenvía este mensaje a tu proveedor para confirmar el pedido.",
}


def render_es(key: str, **params: object) -> str:
    """Render the Spanish template for `key`, interpolating `params`.

    Raises KeyError on an unknown key — a missing catalog entry is a
    programming error that must fail loudly in tests, not ship a blank.
    """
    return _ES[key].format(**params)
