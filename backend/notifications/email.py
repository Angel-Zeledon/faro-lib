"""
Email notification service via SMTP (Gmail App Password).
All email templates live here. Credentials come from settings.

Delivery contract (two tiers, so no caller can report a send that never left):

* Transport tier — ``_transport_send`` / ``_send`` RAISE on every outcome that
  is not "handed to a live provider", including "no transport configured"
  (``EmailNotConfigured``). Returning quietly there is what made every caller
  below report success while nothing was sent.
* Public tier — every ``send_*`` returns ``bool``: True only when the message
  reached a transport. They never raise, so a failing mail server can never
  break a request or an alert loop. Callers that must tell the user (API
  responses, alert loops writing activity rows) branch on that bool.
"""

import base64
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.config import settings
from backend.notifications.locale import render_es

log = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """The message could not be handed to a transport."""


class EmailNotConfigured(EmailDeliveryError):
    """Neither Resend nor SMTP credentials are set — nothing can be sent."""


def is_configured() -> bool:
    """True when some transport can actually deliver. Mirrors whatsapp.is_configured()."""
    return bool(settings.resend_api_key or (settings.smtp_user and settings.smtp_pass))


def failure_reason() -> str:
    """
    Stable code for why a send failed, for API payloads and activity rows.
    The two are fixed by different people: `not_configured` is an operator
    setting up credentials, `transport_error` is the provider rejecting us.
    """
    return "transport_error" if is_configured() else "not_configured"

_APP_NAME = "ForecastPlatform"
_PRIMARY   = "#818cf8"
_BG        = "#08090d"
_SURFACE   = "#0f1015"
_TEXT      = "#e2e8f0"
_DIM       = "#94a3b8"


def _base_html(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>{title}</title></head>
<body style="margin:0;padding:0;background:{_BG};font-family:system-ui,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px;">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:{_SURFACE};border-radius:12px;border:1px solid #1e2030;overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="padding:24px 32px;border-bottom:1px solid #1e2030;">
            <span style="font-size:18px;font-weight:700;color:{_PRIMARY};">{_APP_NAME}</span>
          </td>
        </tr>
        <!-- Body -->
        <tr><td style="padding:32px;color:{_TEXT};font-size:14px;line-height:1.7;">
          {body_html}
        </td></tr>
        <!-- Footer -->
        <tr>
          <td style="padding:20px 32px;border-top:1px solid #1e2030;color:{_DIM};font-size:11px;">
            &copy; 2026 {_APP_NAME}. This email was sent to you because you created an account.
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _button(text: str, url: str) -> str:
    return (
        f'<div style="margin:24px 0;">'
        f'<a href="{url}" style="display:inline-block;padding:12px 28px;'
        f'background:{_PRIMARY};color:#fff;border-radius:8px;font-weight:600;'
        f'font-size:14px;text-decoration:none;">{text}</a></div>'
    )


def _strong(text: str) -> str:
    """Emphasise a fragment inside a sentence without moving markup into the catalog."""
    return f'<strong style="color:{_TEXT};">{text}</strong>'


# How long a verification code / setup link stays valid. The number lives in
# code; only the unit word comes from the locale catalog.
_CODE_TTL_HOURS = 30


def _code_ttl_label() -> str:
    return render_es("hours_duration", hours=_CODE_TTL_HOURS)


def _send_resend(to: str, subject: str, html: str, attachment: dict | None = None) -> None:
    """Send via the Resend HTTP API. Raises on failure."""
    import httpx

    payload = {
        "from": settings.email_from,
        "to": [to],
        "subject": f"[{_APP_NAME}] {subject}",
        "html": html,
    }
    if attachment:
        payload["attachments"] = [{
            "filename": attachment["filename"],
            "content": base64.b64encode(attachment["content_bytes"]).decode("ascii"),
        }]

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()


def _send_smtp(to: str, subject: str, html: str, attachment: dict | None = None) -> None:
    """Send via SMTP TLS (fallback transport). Raises on failure."""
    msg = MIMEMultipart("mixed" if attachment else "alternative")
    msg["Subject"] = f"[{_APP_NAME}] {subject}"
    msg["From"]    = f"{_APP_NAME} <{settings.smtp_user}>"
    msg["To"]      = to

    if attachment:
        body_part = MIMEMultipart("alternative")
        body_part.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(body_part)
        part = MIMEApplication(attachment["content_bytes"], Name=attachment["filename"])
        part["Content-Disposition"] = f'attachment; filename="{attachment["filename"]}"'
        msg.attach(part)
    else:
        msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_pass)
        smtp.sendmail(settings.smtp_user, to, msg.as_string())


def _transport_send(to: str, subject: str, html: str, attachment: dict | None = None) -> None:
    """
    Dispatch an email: Resend when RESEND_API_KEY is set, SMTP as fallback.
    Raises on transport failure so callers can report `email_sent=False`.

    "No transport configured" raises too: it is a non-delivery like any other,
    and returning quietly here made every `try: _send() ... return True`
    caller claim it had mailed an invite / a verification link / a purchase
    order that no one ever received.
    """
    if settings.resend_api_key:
        _send_resend(to, subject, html, attachment)
        log.info("Email sent via Resend → %s | subject: %s", to, subject)
        return

    if not settings.smtp_user or not settings.smtp_pass:
        raise EmailNotConfigured(
            f"No email transport configured (RESEND_API_KEY / SMTP) — nothing sent to {to}"
        )

    _send_smtp(to, subject, html, attachment)
    log.info("Email sent via SMTP → %s | subject: %s", to, subject)


def _send(to: str, subject: str, html: str, attachment: dict | None = None) -> None:
    # Thin wrapper so tests (conftest) can patch the single `_send` entrypoint
    # while the dispatch logic in _transport_send stays independently testable.
    _transport_send(to, subject, html, attachment)


# Rows an inventory digest lists before collapsing the rest into a "+N more"
# line. A mail client is not a dashboard — the table stays scannable while the
# headline count stays the real one.
_ALERT_MAX_CRITICAL_ROWS = 10
_ALERT_MAX_WARNING_ROWS  = 5


# ── Public interface ──────────────────────────────────────────────────────────

def send_verification_email(to: str, full_name: str, verify_url: str) -> bool:
    """Send account verification link. Returns True if sent successfully."""
    name = full_name or to.split("@")[0]
    html = _base_html(
        "Verify your email",
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">Welcome, {name}!</p>
        <p style="color:{_DIM};margin:0 0 20px;">
          Thanks for signing up for {_APP_NAME}. Please verify your email address
          to activate your account.
        </p>
        {_button("Verify my email", verify_url)}
        <p style="color:{_DIM};font-size:12px;">
          This link expires in 24 hours. If you didn't create an account, you can safely ignore this email.
        </p>
        """,
    )
    try:
        _send(to, "Verify your email address", html)
        return True
    except Exception as exc:
        log.error("Failed to send verification email to %s: %s", to, exc)
        return False


def send_password_reset_email(to: str, reset_url: str) -> bool:
    """Send password reset link. Returns True if sent successfully."""
    html = _base_html(
        "Reset your password",
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">Reset your password</p>
        <p style="color:{_DIM};margin:0 0 20px;">
          We received a request to reset the password for your {_APP_NAME} account.
          Click the button below to choose a new password.
        </p>
        {_button("Reset my password", reset_url)}
        <p style="color:{_DIM};font-size:12px;">
          This link expires in 15 minutes. If you didn't request a password reset,
          you can safely ignore this email — your password won't change.
        </p>
        """,
    )
    try:
        _send(to, "Password reset request", html)
        return True
    except Exception as exc:
        log.error("Failed to send password reset email to %s: %s", to, exc)
        return False


def send_change_password_code(to: str, code: str) -> bool:
    """Send a 6-digit password-change verification code. Returns True if sent."""
    html = _base_html(
        render_es("change_password_email_title"),
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">
          {render_es("change_password_email_heading")}
        </p>
        <p style="color:{_DIM};margin:0 0 24px;">
          {render_es("change_password_email_intro", app=_APP_NAME)}
        </p>
        <div style="text-align:center;margin:24px 0;">
          <span style="display:inline-block;letter-spacing:10px;font-size:36px;
                       font-weight:800;color:{_PRIMARY};background:#13141e;
                       border:1px solid #1e2030;border-radius:10px;
                       padding:14px 24px;font-family:monospace;">{code}</span>
        </div>
        <p style="color:{_DIM};font-size:12px;margin:0;">
          {render_es("change_password_email_expiry", duration=_strong(_code_ttl_label()))}
        </p>
        """,
    )
    try:
        _send(to, render_es("change_password_email_subject"), html)
        return True
    except Exception as exc:
        log.error("Failed to send password-change code to %s: %s", to, exc)
        return False


def send_password_reset_otp(to: str, code: str) -> bool:
    """Send a 6-digit OTP for forgot-password flow. Returns True if sent."""
    html = _base_html(
        render_es("password_reset_otp_email_title"),
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">
          {render_es("password_reset_otp_email_heading")}
        </p>
        <p style="color:{_DIM};margin:0 0 24px;">
          {render_es("password_reset_otp_email_intro", app=_APP_NAME)}
        </p>
        <div style="text-align:center;margin:24px 0;">
          <span style="display:inline-block;letter-spacing:10px;font-size:36px;
                       font-weight:800;color:{_PRIMARY};background:#13141e;
                       border:1px solid #1e2030;border-radius:10px;
                       padding:14px 24px;font-family:monospace;">{code}</span>
        </div>
        <p style="color:{_DIM};font-size:12px;margin:0;">
          {render_es("password_reset_otp_email_expiry", duration=_strong(_code_ttl_label()))}
        </p>
        """,
    )
    try:
        _send(to, render_es("password_reset_otp_email_subject"), html)
        return True
    except Exception as exc:
        log.error("Failed to send password-reset OTP to %s: %s", to, exc)
        return False


def send_account_setup_email(to: str, full_name: str, setup_url: str) -> bool:
    """Sent to a user created by an admin — prompts them to verify via link. Returns True on success."""
    name = full_name or to.split("@")[0]
    html = _base_html(
        render_es("account_setup_email_title"),
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">
          {render_es("account_setup_email_heading", app=_APP_NAME, name=name)}
        </p>
        <p style="color:{_DIM};margin:0 0 20px;">
          {render_es("account_setup_email_intro", app=_APP_NAME)}
        </p>
        {_button(render_es("account_setup_email_cta"), setup_url)}
        <p style="color:{_DIM};font-size:12px;">
          {render_es("account_setup_email_expiry", duration=_code_ttl_label())}
        </p>
        """,
    )
    try:
        _send(to, render_es("account_setup_email_subject", app=_APP_NAME), html)
        return True
    except Exception as exc:
        log.error("Failed to send account setup email to %s: %s", to, exc)
        return False


def send_inventory_alert_email(
    to: str,
    critical_items: list[dict],
    warning_items: list[dict],
    inventory_url: str,
) -> bool:
    """
    Daily digest: SKUs at risk of stockout. Returns True if sent.

    Callers pass the FULL lists. Trimming for readability happens here, after
    the counts are taken, so the subject and body report how many SKUs are
    really at risk — callers used to slice to [:10]/[:5] and the digest then
    announced "10 SKUs" when 47 were about to run out.
    """
    _RED  = "#ef4444"
    _AMB  = "#f59e0b"
    _GRN  = "#22c55e"

    def _row(item: dict, color: str, badge: str) -> str:
        sku   = item.get("sku", "")
        name  = item.get("display_name") or ""
        days  = item.get("coverage_days")
        recom = item.get("recommended_qty")
        prov  = item.get("supplier") or "—"
        days_str  = (
            render_es("alert_email_coverage_days", days=f"{days:.0f}")
            if days is not None else "—"
        )
        recom_str = f"{recom:,.0f}" if recom else "—"
        return (
            f'<tr style="border-bottom:1px solid #1e2030;">'
            f'<td style="padding:10px 12px;font-family:monospace;font-size:12px;">{sku}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{_DIM};">{name}</td>'
            f'<td style="padding:10px 12px;">'
            f'  <span style="background:{color}20;color:{color};padding:2px 8px;'
            f'  border-radius:20px;font-size:11px;font-weight:700;">{badge}</span>'
            f'</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{color};font-weight:600;">{days_str}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{_GRN};font-weight:700;">{recom_str}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{_DIM};">{prov}</td>'
            f'</tr>'
        )

    def _more_row(hidden: int) -> str:
        return (
            f'<tr style="border-bottom:1px solid #1e2030;">'
            f'<td colspan="6" style="padding:10px 12px;font-size:12px;color:{_DIM};">'
            f'{render_es("alert_email_more_row", n=hidden, s="s" if hidden != 1 else "")}'
            f'</td></tr>'
        )

    n_critical = len(critical_items)
    n_warning  = len(warning_items)

    badge_critical = render_es("alert_email_badge_critical")
    badge_warning  = render_es("alert_email_badge_warning")
    all_items = [_row(i, _RED, badge_critical) for i in critical_items[:_ALERT_MAX_CRITICAL_ROWS]]
    if n_critical > _ALERT_MAX_CRITICAL_ROWS:
        all_items.append(_more_row(n_critical - _ALERT_MAX_CRITICAL_ROWS))
    all_items += [_row(i, _AMB, badge_warning) for i in warning_items[:_ALERT_MAX_WARNING_ROWS]]
    if n_warning > _ALERT_MAX_WARNING_ROWS:
        all_items.append(_more_row(n_warning - _ALERT_MAX_WARNING_ROWS))

    subject_prefix = (
        render_es("alert_email_subject_critical",
                  n=n_critical, s="s" if n_critical > 1 else "")
        if n_critical else
        render_es("alert_email_subject_warning",
                  n=n_warning, s="s" if n_warning > 1 else "")
    )

    def _th(label: str) -> str:
        return (
            f'<th style="padding:8px 12px;text-align:left;color:{_DIM};'
            f'font-size:10px;text-transform:uppercase;">{label}</th>'
        )

    table_html = (
        '<table width="100%" style="border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#13141e;">'
        + "".join(_th(render_es(k)) for k in (
            "alert_email_col_sku", "alert_email_col_name", "alert_email_col_signal",
            "alert_email_col_coverage", "alert_email_col_order", "alert_email_col_supplier",
        ))
        + '</tr></thead><tbody>'
        + "".join(all_items) +
        '</tbody></table>'
    )

    summary_critical = (
        f'<span style="color:#ef4444;font-weight:600;">'
        f'{render_es("alert_email_summary_critical", n=n_critical, s="s" if n_critical > 1 else "")}'
        f'</span> '
    ) if n_critical else ''
    summary_warning = (
        f'<span style="color:#f59e0b;">'
        f'{render_es("alert_email_summary_warning", n=n_warning, s="s" if n_warning > 1 else "")}'
        f'</span>'
    ) if n_warning else ''

    html = _base_html(
        render_es("alert_email_title"),
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 4px;">{render_es("alert_email_title")}</p>
        <p style="color:{_DIM};margin:0 0 24px;font-size:13px;">
          {summary_critical}
          {summary_warning}
        </p>
        {table_html}
        {_button(render_es("alert_email_cta"), inventory_url)}
        <p style="color:{_DIM};font-size:11px;margin:0;">
          {render_es("alert_email_footer")}
        </p>
        """,
    )
    try:
        _send(to, subject_prefix, html)
        return True
    except Exception as exc:
        log.error("Failed to send inventory alert to %s: %s", to, exc)
        return False


def send_supplier_lead_time_alert_email(
    to: str,
    deviations: list[dict],
    scorecard_url: str,
) -> bool:
    """
    Daily digest: suppliers whose recent lead time drifted significantly off
    their own history (feature 3.3). Rows come from
    supplier_health_service.get_lead_time_deviations(). Returns True if sent.
    """
    _RED = "#ef4444"
    _AMB = "#f59e0b"

    def _row(d: dict) -> str:
        # `severidad` / `lead_time_reciente` / `n_reciente` are payload keys owned
        # by supplier_health_service, not user-facing copy — read, never rendered.
        color = _RED if d.get("severidad") == "alta" else _AMB
        return (
            f'<tr style="border-bottom:1px solid #1e2030;">'
            f'<td style="padding:10px 12px;font-size:13px;font-weight:600;">{d.get("supplier", "")}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{color};font-weight:700;">'
            f'  {render_es("lead_time_email_days", days=d.get("lead_time_reciente"))}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{_DIM};">'
            f'  {render_es("lead_time_email_days", days=d.get("lead_time_historico"))}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{color};font-weight:600;">'
            f'  {render_es("lead_time_email_deviation_days", days=d.get("deviation_days"))}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{_DIM};">'
            f'  {render_es("lead_time_email_receptions_ratio", recent=d.get("n_reciente"), baseline=d.get("n_baseline"))}</td>'
            f'</tr>'
        )

    n = len(deviations)
    subject = render_es("lead_time_email_subject", n=n, s="es" if n > 1 else "")

    def _th(label: str) -> str:
        return (
            f'<th style="padding:8px 12px;text-align:left;color:{_DIM};'
            f'font-size:10px;text-transform:uppercase;">{label}</th>'
        )

    table_html = (
        '<table width="100%" style="border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#13141e;">'
        + "".join(_th(render_es(k)) for k in (
            "lead_time_email_col_supplier", "lead_time_email_col_current",
            "lead_time_email_col_historical", "lead_time_email_col_deviation",
            "lead_time_email_col_receptions",
        ))
        + '</tr></thead><tbody>'
        + "".join(_row(d) for d in deviations)
        + '</tbody></table>'
    )

    html = _base_html(
        render_es("lead_time_email_title"),
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 4px;">
          {render_es("lead_time_email_heading")}
        </p>
        <p style="color:{_DIM};margin:0 0 24px;font-size:13px;">
          {render_es("lead_time_email_body_many" if n > 1 else "lead_time_email_body_one", n=n)}
        </p>
        {table_html}
        {_button(render_es("lead_time_email_cta"), scorecard_url)}
        <p style="color:{_DIM};font-size:11px;margin:0;">
          {render_es("lead_time_email_footer")}
        </p>
        """,
    )
    try:
        _send(to, subject, html)
        return True
    except Exception as exc:
        log.error("Failed to send supplier lead-time alert to %s: %s", to, exc)
        return False


def send_data_freshness_reminder_email(
    to: str,
    sales_age_days: int | None,
    stock_age_days: int | None,
    upload_url: str,
) -> bool:
    """
    The reminder that reaches a buyer who stopped opening the app.

    Sent by `notifications.freshness_service.run_daily_freshness_reminders`.
    `stock_age_days` is passed only when the stock clock is what triggered the
    reminder, so the email never lists an age that is not part of the problem.
    Returns True if the message reached a transport.
    """
    blocks: list[str] = []
    if sales_age_days is not None:
        blocks.append(
            f'<p style="color:{_DIM};margin:0 0 14px;">'
            f'{render_es("freshness_email_sales", days=sales_age_days)}</p>'
        )
    if stock_age_days is not None:
        blocks.append(
            f'<p style="color:{_DIM};margin:0 0 14px;">'
            f'{render_es("freshness_email_stock", days=stock_age_days)}</p>'
        )

    html = _base_html(
        render_es("freshness_email_title"),
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 12px;">
          {render_es("freshness_email_title")}
        </p>
        {"".join(blocks)}
        {_button(render_es("freshness_email_cta"), upload_url)}
        <p style="color:{_DIM};font-size:11px;margin:0;">
          {render_es("freshness_email_footer")}
        </p>
        """,
    )
    subject = (
        render_es("freshness_email_subject", days=sales_age_days)
        if sales_age_days is not None
        else render_es("freshness_email_subject_stock", days=stock_age_days)
    )
    try:
        _send(to, subject, html)
        return True
    except Exception as exc:
        log.error("Failed to send data-freshness reminder to %s: %s", to, exc)
        return False


def send_training_complete_email(to: str, session_name: str, dashboard_url: str) -> bool:
    """Notify user when a training job finishes. Returns True if sent."""
    html = _base_html(
        "Training complete",
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">Training complete ✓</p>
        <p style="color:{_DIM};margin:0 0 20px;">
          Your forecast session <strong style="color:{_TEXT};">{session_name}</strong>
          has finished training. Head to the dashboard to view your results,
          model metrics, and inventory recommendations.
        </p>
        {_button("View results", dashboard_url)}
        """,
    )
    try:
        _send(to, f"Training complete — {session_name}", html)
        return True
    except Exception as exc:
        log.error("Failed to send training complete email to %s: %s", to, exc)
        return False


def send_po_to_supplier_email(
    *,
    to: str,
    supplier_name: str,
    po_log_id: str,
    items: list[dict],
    pdf_bytes: bytes,
    pdf_filename: str,
    po_ref: str | None = None,
) -> bool:
    """Send a purchase order's PDF to its supplier. Returns True if sent."""
    ref = po_ref or po_log_id
    def _row(item: dict) -> str:
        sku = item.get("sku", "")
        name = item.get("display_name") or sku
        qty = item.get("final_qty") or 0
        return (
            f'<tr style="border-bottom:1px solid #1e2030;">'
            f'<td style="padding:8px 10px;font-family:monospace;font-size:12px;">{sku}</td>'
            f'<td style="padding:8px 10px;font-size:12px;color:{_DIM};">{name}</td>'
            f'<td style="padding:8px 10px;font-size:12px;font-weight:600;">{qty:,.0f}</td>'
            f'</tr>'
        )

    def _th(label: str) -> str:
        return (
            f'<th style="padding:8px 10px;text-align:left;color:{_DIM};'
            f'font-size:10px;text-transform:uppercase;">{label}</th>'
        )

    table_html = (
        '<table width="100%" style="border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#13141e;">'
        + "".join(_th(render_es(k)) for k in (
            "po_email_col_sku", "po_email_col_product", "po_email_col_qty",
        ))
        + '</tr></thead><tbody>' + "".join(_row(i) for i in items) + '</tbody></table>'
    )

    html = _base_html(
        render_es("po_email_title"),
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">{render_es("po_email_title")}</p>
        <p style="color:{_DIM};margin:0 0 20px;">
          {render_es("po_email_body", supplier=supplier_name)}
        </p>
        {table_html}
        <p style="color:{_DIM};font-size:11px;margin:20px 0 0;">
          {render_es("po_email_reference", reference=ref)}
        </p>
        """,
    )
    try:
        _send(to, render_es("po_email_subject", reference=ref), html,
              attachment={"filename": pdf_filename, "content_bytes": pdf_bytes})
        return True
    except Exception as exc:
        log.error("Failed to send PO email to supplier %s <%s>: %s", supplier_name, to, exc)
        return False


# ── Monthly recap ─────────────────────────────────────────────────────────────
# Target market is Costa Rica, so amounts render as colones (CRC). CR uses the
# period as thousands separator.
_MONTH_KEYS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def _fmt_crc(value: float) -> str:
    return "₡" + f"{value:,.0f}".replace(",", ".")


def _month_label(month_key: str) -> str:
    """'2026-06' -> 'junio de 2026' (month name and joiner come from the catalog)."""
    year, month = (int(p) for p in month_key.split("-"))
    return render_es(
        "month_label",
        month=render_es(f"month_name_{_MONTH_KEYS[month - 1]}"),
        year=year,
    )


def _recap_metric_block(value: str, label: str, note: str, color: str) -> str:
    """One big-number tile. `note` states where the number comes from."""
    return (
        f'<td style="padding:14px 16px;vertical-align:top;">'
        f'<div style="font-size:30px;font-weight:800;color:{color};line-height:1;">{value}</div>'
        f'<div style="font-size:13px;font-weight:600;color:{_TEXT};margin-top:6px;">{label}</div>'
        f'<div style="font-size:11px;color:{_DIM};margin-top:4px;line-height:1.5;">{note}</div>'
        f'</td>'
    )


def send_monthly_roi_email(to: str, report: dict, roi_url: str) -> bool:
    """
    Monthly recap of what the buyer did with Faro.

    Every figure comes straight from `get_month_report`; metrics that could not
    be derived from the tenant's own records are omitted from the email rather
    than shown as zero. Returns True if the email was handed to the transport.
    """
    _GRN = "#22c55e"
    _RED = "#ef4444"
    month_label = _month_label(report["month"])

    tiles: list[str] = []

    adoption = report.get("adoption_rate")
    if adoption is not None:
        tiles.append(_recap_metric_block(
            f"{round(adoption * 100)}%",
            render_es("roi_email_metric_adoption_label"),
            render_es("roi_email_metric_adoption_note",
                      followed=report["recommendations_followed"],
                      shown=report["recommendations_shown"]),
            _PRIMARY,
        ))

    risks = report.get("stockout_risks_handled")
    if risks:
        tiles.append(_recap_metric_block(
            f"{risks}",
            render_es("roi_email_metric_risks_label"),
            render_es("roi_email_metric_risks_note"),
            _RED,
        ))

    capital = report.get("capital_freed")
    if capital is not None:
        tiles.append(_recap_metric_block(
            _fmt_crc(capital),
            render_es("roi_email_metric_capital_label"),
            render_es("roi_email_metric_capital_note"),
            _GRN,
        ))

    managed = report.get("managed_purchase_value")
    if managed is not None:
        tiles.append(_recap_metric_block(
            _fmt_crc(managed),
            render_es("roi_email_metric_purchases_label"),
            render_es("roi_email_metric_purchases_note"),
            _TEXT,
        ))

    # Two tiles per row keeps the layout readable in narrow mail clients.
    rows = "".join(
        f'<tr>{"".join(tiles[i:i + 2])}</tr>' for i in range(0, len(tiles), 2)
    )
    tiles_html = (
        f'<table width="100%" style="border-collapse:collapse;background:#13141e;'
        f'border-radius:10px;margin:0 0 20px;">{rows}</table>'
    )

    headline = (
        render_es("roi_email_headline_capital",
                  month=month_label, amount=_fmt_crc(capital))
        if capital is not None
        else render_es("roi_email_headline_default", month=month_label)
    )
    title = render_es("roi_email_title", month=month_label)

    html = _base_html(
        title,
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">{title}</p>
        <p style="color:{_DIM};margin:0 0 20px;font-size:13px;">{headline}</p>
        {tiles_html}
        {_button(render_es("roi_email_cta"), roi_url)}
        <p style="color:{_DIM};font-size:11px;margin:0;line-height:1.6;">
          {render_es("roi_email_footer")}
        </p>
        """,
    )

    subject = (
        render_es("roi_email_subject_capital",
                  month=month_label, amount=_fmt_crc(capital))
        if capital is not None
        else render_es("roi_email_subject_default", month=month_label)
    )
    try:
        _send(to, subject, html)
        return True
    except Exception as exc:
        log.error("Failed to send monthly recap to %s: %s", to, exc)
        return False
