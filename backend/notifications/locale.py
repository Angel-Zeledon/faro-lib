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
    # ── PO document (PDF/TXT) that Faro sends to the supplier ────────────────
    # A line with no cost on file used to print "₡0", and the total with it, so
    # the document leaving the tenant's name quoted a price of zero to their
    # supplier. An unknown price is stated as unknown; the total then covers
    # only the priced lines and says so, instead of adding zeros in silence.
    "po_pdf_cost_unknown":  "a convenir",
    "po_pdf_total":         "Total: {amount}",
    "po_pdf_total_partial": "Total ({priced} de {total} líneas con precio): {amount}",
    "po_pdf_total_none":    "Total: pendiente de cotizar",
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
    # ── Inventory summary PDF (downloaded, then forwarded to other people) ────
    # The buyer downloads this and sends it on, so it never passes through the
    # frontend and its Spanish belongs here. Note `inventory_pdf_generated_on`
    # takes an already-formatted date: `strftime("%B")` returns whatever the
    # SERVER's locale is, which on a machine running in English printed
    # "03 de August de 2026" on a Spanish document.
    "inventory_pdf_title":           "RESUMEN DE INVENTARIO",
    "inventory_pdf_generated_on":    "Generado el {date}",
    "inventory_pdf_kpi_total":       "Total SKUs",
    "inventory_pdf_kpi_urgent":      "Pedir YA",
    "inventory_pdf_kpi_warning":     "Pedir pronto",
    "inventory_pdf_kpi_ok":          "OK",
    "inventory_pdf_kpi_overstock":   "Sobrestock",
    "inventory_pdf_kpi_value":       "Valor bodega",
    "inventory_pdf_section_action":  "Productos que requieren acción",
    "inventory_pdf_section_rest":    "Resto del inventario",
    "inventory_pdf_col_sku":         "SKU",
    "inventory_pdf_col_name":        "Nombre",
    "inventory_pdf_col_signal":      "Señal",
    "inventory_pdf_col_stock":       "Stock actual",
    "inventory_pdf_col_coverage":    "Días cobertura",
    "inventory_pdf_col_order":       "Pedir",
    "inventory_pdf_col_supplier":    "Proveedor",
    "inventory_pdf_col_abc_xyz":     "ABC-XYZ",
    # The signal VALUES stay as stored (PEDIR_YA…); these are their labels.
    "inventory_pdf_signal_order_now":  "🔴 PEDIR YA",
    "inventory_pdf_signal_order_soon": "🟡 Pedir pronto",
    "inventory_pdf_signal_ok":         "🟢 OK",
    "inventory_pdf_signal_overstock":  "🔵 Sobrestock",
    "inventory_pdf_signal_no_data":    "Sin datos",
    "inventory_pdf_footer":          "Generado automáticamente · Sesión {session} · Nivel de servicio {level}%",
    # ── Purchase-order PDF header block ───────────────────────────────────────
    # Two forms on purpose: the PDF's heading is sentence case, the plain-text
    # fallback's banner is upper case. `.title()` would have written "Orden De
    # Compra" — Spanish does not capitalize the preposition.
    "po_pdf_heading":          "Orden de Compra",
    "po_pdf_title":            "ORDEN DE COMPRA",
    "po_pdf_date":             "Fecha",
    "po_pdf_reference":        "Referencia",
    "po_pdf_issued_on":        "Fecha de emisión",
    "po_pdf_supplier":         "Proveedor",
    "po_pdf_section_lines":    "Líneas del pedido",
    "po_pdf_col_sku":          "SKU",
    "po_pdf_col_product":      "Producto",
    "po_pdf_col_qty":          "Cantidad",
    "po_pdf_col_unit_cost":    "Costo unitario",
    "po_pdf_col_subtotal":     "Subtotal",
    # ── Inventory status export (CSV the user downloads) ──────────────────────
    "inventory_csv_col_sku":            "SKU",
    "inventory_csv_col_name":           "Nombre",
    "inventory_csv_col_supplier":       "Proveedor",
    "inventory_csv_col_signal":         "Señal",
    "inventory_csv_col_stock":          "Stock actual",
    "inventory_csv_col_coverage":       "Días cobertura",
    "inventory_csv_col_lead_demand":    "Demanda (lead time)",
    "inventory_csv_col_lead_time":      "Lead time (días)",
    "inventory_csv_col_lead_source":    "Origen lead time",
    "inventory_csv_col_recommended":    "Cantidad recomendada",
    "inventory_csv_col_moq":            "MOQ",
    "inventory_csv_col_unit_cost":      "Costo unitario",
    "inventory_csv_col_order_value":    "Valor orden",
    "inventory_csv_lead_source_learned":   "Aprendido",
    "inventory_csv_lead_source_declared":  "Configurado",
    # ── WhatsApp assistant ────────────────────────────────────────────────────
    # Everything the bot says back on WhatsApp. The channel never reaches the
    # frontend, so this is the catalogue that owns its wording.
    "wa_unknown_number":     "Hola 👋 No reconozco este número. Vincula tu WhatsApp desde tu perfil en Faro para poder ayudarte por aquí.",
    "wa_rate_limited":       "Vas muy rápido 🙏 Espera un momento y vuelve a escribirme.",
    "wa_help":               "Puedo ayudarte con tu inventario: pregúntame por el semáforo (qué pedir), tus órdenes pendientes o el pronóstico de un SKU. También puedo aprobar una orden o registrar una recepción.",
    "wa_generic_mode":       "Recibí tu mensaje. Por ahora estoy en modo básico: puedo confirmar una acción pendiente si respondes “sí”. Muy pronto podré responder tus consultas de inventario por aquí.",
    "wa_apology":            "Perdón, tuve un problema procesando tu mensaje. ¿Puedes intentarlo de nuevo?",
    "wa_read_only":          "Tu perfil es de solo lectura, así que no puedo ejecutar acciones. Puedo darte información de inventario si quieres.",
    "wa_read_only_tool":     "Tu perfil es de solo lectura; no puedes ejecutar esta acción.",
    "wa_no_analysis_yet":    "Aún no hay un análisis de inventario listo. Sube tus ventas y entrena un modelo primero.",
    "wa_status_line":        "🔴 {risks} para pedir ya · 🟡 {warnings} por reabastecer · 🟢 {overstock} con sobrestock",
    "wa_no_pending_pos":     "No tienes órdenes de compra pendientes de recibir.",
    "wa_no_forecasts_yet":   "Aún no hay pronósticos listos para esta cuenta.",
    "wa_forecast_not_found": "No encontré pronóstico para el SKU {sku}.",
    "wa_forecast_too_short": "El pronóstico del SKU {sku} aún no tiene suficientes puntos.",
    "wa_trend_up":           "sube",
    "wa_trend_down":         "baja",
    "wa_trend_flat":         "estable",
    "wa_ask_sku_for_forecast": "¿De qué SKU quieres el pronóstico? Indícame el código.",
    "wa_forecast_summary":     "Pronóstico SKU {sku}: {periods} periodos, promedio {avg} uds/periodo, tendencia {trend} (de {first} a {last}).",
    "wa_pending_pos_header":   "Órdenes pendientes:",
    "wa_pending_po_line":      "  • {reference} — {skus} SKU{total} ({status})",
    "wa_status_item_line":     "  • {name} ({coverage}{qty})",
    "wa_status_order_qty":     " · pedir {qty}",
    # Every write is confirmed in two steps; the confirmation word is Spanish
    # copy and the matcher for it lives in the agent, keyed off this same
    # catalog rather than a second literal.
    "wa_confirm_suffix":       " ¿Confirmas? (responde SÍ)",
    "wa_confirm_send_po":      "Aprobar y enviar la orden {reference} — {suppliers} proveedor(es), total {amount}.",
    "wa_confirm_reception":    "Registrar recepción de {qty} uds de {sku} en {warehouse} (orden {reference}).",
    "wa_ask_po_to_approve":    "Indícame el número de la orden de compra a aprobar.",
    "wa_po_not_found":         "No encontré esa orden de compra.",
    "wa_po_already_sent":      "Esa orden ya fue enviada.",
    "wa_ask_quantity":         "¿Cuántas unidades llegaron? Indícame la cantidad.",
    "wa_ask_reception_sku":    "¿De qué SKU es la recepción?",
    "wa_quantity_positive":    "La cantidad recibida debe ser mayor a cero.",
    "wa_no_pending_po_sku":    "No encontré una orden pendiente con el SKU {sku}.",
    "wa_no_pending_po_sku_wh": "No encontré una orden pendiente con el SKU {sku} en {warehouse}.",
    "wa_po_sent_ok":           "Listo ✅ Orden {reference} aprobada y marcada como enviada.",
    "wa_reception_ok":         "Listo ✅ Registré {qty} uds de {sku} en {warehouse}.",
    "wa_unknown_action":       "Acción no reconocida.",
    # ── Duration nouns for the Spanish channels ───────────────────────────────
    # `backend/formatting.py` composes "1 día" / "N semanas" for the PDF, the
    # WhatsApp digest and the recap email. Those nouns are Spanish copy, so they
    # belong here rather than as literals in a formatting helper — and they must
    # agree in number, or the PDF shown when a product is about to run out reads
    # "1 días de stock".
    "unit_day_one":     "1 día",
    "unit_day_many":    "{n} días",
    "unit_week_one":    "1 semana",
    "unit_week_many":   "{n} semanas",
    "unit_month_one":   "1 mes",
    "unit_month_many":  "{n} meses",
    # Month names for the recap's "junio de 2026" label. Keyed in English so the
    # module indexes them with an English identifier, never a Spanish literal.
    "month_label":        "{month} de {year}",
    "day_month_year":     "{day} de {month} de {year}",
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


_MONTH_KEYS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def render_es(key: str, **params: object) -> str:
    """Render the Spanish template for `key`, interpolating `params`.

    Raises KeyError on an unknown key — a missing catalog entry is a
    programming error that must fail loudly in tests, not ship a blank.
    """
    return _ES[key].format(**params)


def render_month(year: int, month: int) -> str:
    """(2026, 6) -> 'junio de 2026'."""
    return render_es("month_label",
                     month=render_es(f"month_name_{_MONTH_KEYS[month - 1]}"),
                     year=year)


def render_date(d) -> str:
    """A date -> '3 de agosto de 2026'.

    Exists because `strftime("%d de %B de %Y")` reads the month name from the
    SERVER's locale: on a machine running in English it printed "03 de August
    de 2026" onto a Spanish PDF.
    """
    return render_es("day_month_year", day=d.day,
                     month=render_es(f"month_name_{_MONTH_KEYS[d.month - 1]}"),
                     year=d.year)
