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
    # Team-messaging heads-up (WhatsApp first, SMS fallback): intentionally
    # omits the message body (SMS is unencrypted and billed per segment), so it
    # only names the sender.
    "dm_new_message_heads_up": "Faro: tienes un mensaje nuevo de {sender}. Léelo en {url}",
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
    # Data-freshness reminder: the one message that has to reach someone who
    # stopped opening the app, so it states the age and asks for one thing.
    "freshness_email_subject":       "Tus ventas son de hace {days} días — sube el archivo de este mes",
    "freshness_email_subject_stock": "Tu stock tiene {days} días sin actualizarse",
    "freshness_email_title":         "Tus datos se están quedando viejos",
    "freshness_email_sales":         "Tus ventas son de hace <strong>{days} días</strong>. Faro sigue pronosticando con ese archivo, así que lo que compres hoy se decide con lo que vendías hace más de un mes.",
    "freshness_email_stock":         "Tu stock lleva <strong>{days} días</strong> sin actualizarse. Mientras tanto el semáforo no puede confirmar cuánto te queda, así que dejó de mostrarse en verde.",
    "freshness_email_cta":           "Subir el archivo de este mes",
    "freshness_email_footer":        "Te escribimos porque hace tiempo que no subes datos. En cuanto subas el archivo, dejamos de avisarte.",
    "freshness_whatsapp_sales":      "📅 *Faro*: tus ventas son de hace {days} días — sube el archivo de este mes",
    "freshness_whatsapp_stock":      "📦 Tu stock lleva {days} días sin actualizarse; el semáforo quedó en “desactualizado”",
    "freshness_whatsapp_cta":        "Subir ahora: {url}",
    # ── Auth emails ───────────────────────────────────────────────────────────
    # Read in an inbox, never rendered by the frontend, so the Spanish lives
    # here. `{app}` is the product name the module passes in.
    "change_password_email_title":      "Código de verificación",
    "change_password_email_subject":    "Código de verificación para cambio de contraseña",
    "change_password_email_heading":    "Cambio de contraseña",
    "change_password_email_intro":      "Alguien solicitó cambiar la contraseña de tu cuenta en {app}. Usa el siguiente código para confirmar el cambio:",
    "change_password_email_expiry":     "Este código expira en {duration}. Si no solicitaste este cambio, ignora este correo.",
    "password_reset_otp_email_title":   "Recuperar contraseña",
    "password_reset_otp_email_subject": "Código de verificación para recuperar contraseña",
    "password_reset_otp_email_heading": "Recupera tu contraseña",
    "password_reset_otp_email_intro":   "Recibimos una solicitud para restablecer la contraseña de tu cuenta en {app}. Usa el siguiente código de verificación para continuar:",
    "password_reset_otp_email_expiry":  "Este código expira en {duration}. Si no solicitaste este cambio, puedes ignorar este correo de forma segura.",
    "account_setup_email_title":        "Activa tu cuenta",
    "account_setup_email_subject":      "Activa tu cuenta en {app}",
    "account_setup_email_heading":      "Bienvenido a {app}, {name}!",
    "account_setup_email_intro":        "Un administrador ha creado una cuenta para ti en {app}. Haz clic en el botón de abajo para verificar tu correo y activar tu cuenta.",
    "account_setup_email_cta":          "Activar mi cuenta",
    "account_setup_email_expiry":       "Este enlace expira en {duration}. Si no esperabas esta invitación, puedes ignorar este correo.",
    # The three messages above quote the same TTL; the number stays in code.
    "hours_duration":                   "{hours} horas",
    # ── Daily stockout digest (email) ─────────────────────────────────────────
    # `{s}` is the plural suffix the module decides, matching the convention of
    # `alert_email_more_row` above.
    "alert_email_title":            "Alerta de inventario",
    "alert_email_subject_critical": "🔴 {n} SKU{s} en riesgo de stockout",
    "alert_email_subject_warning":  "🟡 {n} SKU{s} por reabastecer",
    "alert_email_summary_critical": "{n} producto{s} en riesgo inmediato de stockout.",
    "alert_email_summary_warning":  "{n} producto{s} deben reabastecerse pronto.",
    "alert_email_badge_critical":   "🔴 PEDIR YA",
    "alert_email_badge_warning":    "🟡 PEDIR PRONTO",
    "alert_email_coverage_days":    "{days} días",
    "alert_email_col_sku":          "SKU",
    "alert_email_col_name":         "Nombre",
    "alert_email_col_signal":       "Señal",
    "alert_email_col_coverage":     "Cobertura",
    "alert_email_col_order":        "Pedir",
    "alert_email_col_supplier":     "Proveedor",
    "alert_email_cta":              "Ver tablero de inventario",
    "alert_email_footer":           "Esta alerta se genera automáticamente cuando hay productos en riesgo de stockout.",
    # ── Daily stockout digest (WhatsApp) ──────────────────────────────────────
    # Separate singular/plural entries wherever the verb agrees with the count —
    # a `{s}` suffix cannot express "se agota" → "se agotan".
    "alert_whatsapp_critical_one":  "🔴 *Faro*: {n} producto se agota antes de tu próximo pedido",
    "alert_whatsapp_critical_many": "🔴 *Faro*: {n} productos se agotan antes de tu próximo pedido",
    "alert_whatsapp_order_qty":     " · pedir {qty}",
    "alert_whatsapp_warning":       "🟡 {n} por reabastecer esta semana",
    # Network-aware suggestion: the stock exists, it is in the wrong warehouse.
    "alert_whatsapp_transfer_one":  "🔁 {n} producto se resuelve moviendo stock, sin comprar",
    "alert_whatsapp_transfer_many": "🔁 {n} productos se resuelven moviendo stock, sin comprar",
    "alert_whatsapp_cta":           "Ver y aprobar: {url}",
    # ── Supplier lead-time drift digest (email) ───────────────────────────────
    "lead_time_email_title":            "Desviación de lead time",
    "lead_time_email_subject":          "⏱️ {n} proveedor{s} tardando más de lo habitual",
    "lead_time_email_heading":          "Tus proveedores están tardando más",
    "lead_time_email_body_one":         "{n} proveedor se ha desviado de su lead time histórico. Si sigues pidiendo con el plazo anterior, el stock puede agotarse antes de que llegue la reposición.",
    "lead_time_email_body_many":        "{n} proveedores se han desviado de su lead time histórico. Si sigues pidiendo con el plazo anterior, el stock puede agotarse antes de que llegue la reposición.",
    "lead_time_email_col_supplier":     "Proveedor",
    "lead_time_email_col_current":      "Ahora",
    "lead_time_email_col_historical":   "Histórico",
    "lead_time_email_col_deviation":    "Desviación",
    "lead_time_email_col_receptions":   "Recepciones",
    "lead_time_email_days":             "{days} días",
    "lead_time_email_deviation_days":   "+{days} días",
    "lead_time_email_receptions_ratio": "{recent} de {baseline}",
    "lead_time_email_cta":              "Ver scorecard de proveedores",
    "lead_time_email_footer":           "Detectado comparando las últimas recepciones contra el historial propio de cada proveedor (regla de control estadístico de 3 sigma).",
    # ── Purchase order emailed to the supplier (PDF attached) ─────────────────
    "po_email_title":       "Nueva orden de compra",
    "po_email_subject":     "Orden de compra {reference}",
    "po_email_body":        "Hola {supplier}, adjuntamos una nueva orden de compra. El detalle completo está en el PDF adjunto.",
    "po_email_reference":   "Referencia: {reference}",
    "po_email_col_sku":     "SKU",
    "po_email_col_product": "Producto",
    "po_email_col_qty":     "Cantidad",
    # ── Monthly recap email ───────────────────────────────────────────────────
    # Every tile states where its number came from, so the copy never implies a
    # saving Faro cannot measure.
    "roi_email_title":                  "Tu resumen de {month}",
    "roi_email_subject_capital":        "Faro — liberaste {amount} en {month}",
    "roi_email_subject_default":        "Faro — tu resumen de {month}",
    "roi_email_headline_capital":       "En {month} liberaste {amount} de inventario detenido.",
    "roi_email_headline_default":       "Esto es lo que hiciste con Faro en {month}.",
    "roi_email_metric_adoption_label":  "de las recomendaciones que seguiste",
    "roi_email_metric_adoption_note":   "Seguiste {followed} de {shown} líneas que Faro te propuso.",
    "roi_email_metric_risks_label":     "riesgos de quiebre atendidos",
    "roi_email_metric_risks_note":      "Productos marcados “Pedir ya” que sí ordenaste en el mes. Es lo que hiciste, no una estimación de quiebres evitados.",
    "roi_email_metric_capital_label":   "capital liberado de sobrestock",
    "roi_email_metric_capital_note":    "Diferencia medida entre el valor de tu inventario en sobrestock al inicio y al final del mes.",
    "roi_email_metric_purchases_label": "en compras gestionadas",
    "roi_email_metric_purchases_note":  "Unidades ordenadas × costo unitario de tus propios datos.",
    "roi_email_cta":                    "Ver el resumen completo",
    "roi_email_footer":                 "Cada cifra sale de tus propios registros en Faro: las órdenes que generaste y las mediciones mensuales de tu inventario. No estimamos ahorros ni contamos quiebres evitados, porque eso no se puede medir con certeza — solo te mostramos lo que quedó registrado.",
    # Month names for the recap's "junio de 2026" label. Keyed in English so the
    # module indexes them with an English identifier, never a Spanish literal.
    "month_label":        "{month} de {year}",
    "month_name_january":   "enero",
    "month_name_february":  "febrero",
    "month_name_march":     "marzo",
    "month_name_april":     "abril",
    "month_name_may":       "mayo",
    "month_name_june":      "junio",
    "month_name_july":      "julio",
    "month_name_august":    "agosto",
    "month_name_september": "septiembre",
    "month_name_october":   "octubre",
    "month_name_november":  "noviembre",
    "month_name_december":  "diciembre",
}


def render_es(key: str, **params: object) -> str:
    """Render the Spanish template for `key`, interpolating `params`.

    Raises KeyError on an unknown key — a missing catalog entry is a
    programming error that must fail loudly in tests, not ship a blank.
    """
    return _ES[key].format(**params)
