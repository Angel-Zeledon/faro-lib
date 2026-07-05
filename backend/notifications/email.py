"""
Email notification service via SMTP (Gmail App Password).
All email templates live here. Credentials come from settings.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.config import settings

log = logging.getLogger(__name__)

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


def _send_resend(to: str, subject: str, html: str) -> None:
    """Send via the Resend HTTP API. Raises on failure."""
    import httpx

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json={
            "from": settings.email_from,
            "to": [to],
            "subject": f"[{_APP_NAME}] {subject}",
            "html": html,
        },
        timeout=15,
    )
    resp.raise_for_status()


def _send_smtp(to: str, subject: str, html: str) -> None:
    """Send via SMTP TLS (fallback transport). Raises on failure."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{_APP_NAME}] {subject}"
    msg["From"]    = f"{_APP_NAME} <{settings.smtp_user}>"
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_pass)
        smtp.sendmail(settings.smtp_user, to, msg.as_string())


def _transport_send(to: str, subject: str, html: str) -> None:
    """
    Dispatch an email: Resend when RESEND_API_KEY is set, SMTP as fallback,
    logged no-op with neither. Raises on transport failure so callers can
    report `email_sent=False`.
    """
    if settings.resend_api_key:
        _send_resend(to, subject, html)
        log.info("Email sent via Resend → %s | subject: %s", to, subject)
        return

    if not settings.smtp_user or not settings.smtp_pass:
        log.warning("No email transport configured (RESEND_API_KEY / SMTP) — email not sent to %s", to)
        return

    _send_smtp(to, subject, html)
    log.info("Email sent via SMTP → %s | subject: %s", to, subject)


def _send(to: str, subject: str, html: str) -> None:
    # Thin wrapper so tests (conftest) can patch the single `_send` entrypoint
    # while the dispatch logic in _transport_send stays independently testable.
    _transport_send(to, subject, html)


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


def send_password_reset_email(to: str, reset_url: str) -> None:
    """Send password reset link."""
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
    except Exception as exc:
        log.error("Failed to send password reset email to %s: %s", to, exc)


def send_change_password_code(to: str, code: str) -> None:
    """Send a 6-digit password-change verification code."""
    html = _base_html(
        "Código de verificación",
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">Cambio de contraseña</p>
        <p style="color:{_DIM};margin:0 0 24px;">
          Alguien solicitó cambiar la contraseña de tu cuenta en {_APP_NAME}.
          Usa el siguiente código para confirmar el cambio:
        </p>
        <div style="text-align:center;margin:24px 0;">
          <span style="display:inline-block;letter-spacing:10px;font-size:36px;
                       font-weight:800;color:{_PRIMARY};background:#13141e;
                       border:1px solid #1e2030;border-radius:10px;
                       padding:14px 24px;font-family:monospace;">{code}</span>
        </div>
        <p style="color:{_DIM};font-size:12px;margin:0;">
          Este código expira en <strong style="color:{_TEXT};">30 horas</strong>.
          Si no solicitaste este cambio, ignora este correo.
        </p>
        """,
    )
    try:
        _send(to, "Código de verificación para cambio de contraseña", html)
    except Exception as exc:
        log.error("Failed to send password-change code to %s: %s", to, exc)


def send_password_reset_otp(to: str, code: str) -> None:
    """Send a 6-digit OTP for forgot-password flow."""
    html = _base_html(
        "Recuperar contraseña",
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">Recupera tu contraseña</p>
        <p style="color:{_DIM};margin:0 0 24px;">
          Recibimos una solicitud para restablecer la contraseña de tu cuenta en {_APP_NAME}.
          Usa el siguiente código de verificación para continuar:
        </p>
        <div style="text-align:center;margin:24px 0;">
          <span style="display:inline-block;letter-spacing:10px;font-size:36px;
                       font-weight:800;color:{_PRIMARY};background:#13141e;
                       border:1px solid #1e2030;border-radius:10px;
                       padding:14px 24px;font-family:monospace;">{code}</span>
        </div>
        <p style="color:{_DIM};font-size:12px;margin:0;">
          Este código expira en <strong style="color:{_TEXT};">30 horas</strong>.
          Si no solicitaste este cambio, puedes ignorar este correo de forma segura.
        </p>
        """,
    )
    try:
        _send(to, "Código de verificación para recuperar contraseña", html)
    except Exception as exc:
        log.error("Failed to send password-reset OTP to %s: %s", to, exc)


def send_account_setup_email(to: str, full_name: str, setup_url: str) -> bool:
    """Sent to a user created by an admin — prompts them to verify via link. Returns True on success."""
    name = full_name or to.split("@")[0]
    html = _base_html(
        "Activa tu cuenta",
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 8px;">Bienvenido a {_APP_NAME}, {name}!</p>
        <p style="color:{_DIM};margin:0 0 20px;">
          Un administrador ha creado una cuenta para ti en {_APP_NAME}.
          Haz clic en el botón de abajo para verificar tu correo y activar tu cuenta.
        </p>
        {_button("Activar mi cuenta", setup_url)}
        <p style="color:{_DIM};font-size:12px;">
          Este enlace expira en 30 horas. Si no esperabas esta invitación, puedes ignorar este correo.
        </p>
        """,
    )
    try:
        _send(to, "Activa tu cuenta en " + _APP_NAME, html)
        return True
    except Exception as exc:
        log.error("Failed to send account setup email to %s: %s", to, exc)
        return False


def send_inventory_alert_email(
    to: str,
    critical_items: list[dict],
    warning_items: list[dict],
    inventory_url: str,
) -> None:
    """Daily digest: SKUs at risk of stockout."""
    _RED  = "#ef4444"
    _AMB  = "#f59e0b"
    _GRN  = "#22c55e"

    def _row(item: dict, color: str, badge: str) -> str:
        sku   = item.get("sku", "")
        name  = item.get("display_name") or ""
        dias  = item.get("dias_cobertura")
        recom = item.get("cantidad_recomendada")
        prov  = item.get("proveedor") or "—"
        dias_str  = f"{dias:.0f} días" if dias is not None else "—"
        recom_str = f"{recom:,.0f}" if recom else "—"
        return (
            f'<tr style="border-bottom:1px solid #1e2030;">'
            f'<td style="padding:10px 12px;font-family:monospace;font-size:12px;">{sku}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{_DIM};">{name}</td>'
            f'<td style="padding:10px 12px;">'
            f'  <span style="background:{color}20;color:{color};padding:2px 8px;'
            f'  border-radius:20px;font-size:11px;font-weight:700;">{badge}</span>'
            f'</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{color};font-weight:600;">{dias_str}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{_GRN};font-weight:700;">{recom_str}</td>'
            f'<td style="padding:10px 12px;font-size:12px;color:{_DIM};">{prov}</td>'
            f'</tr>'
        )

    all_items = (
        [_row(i, _RED, "🔴 PEDIR YA") for i in critical_items] +
        [_row(i, _AMB, "🟡 PEDIR PRONTO") for i in warning_items]
    )

    n_critical = len(critical_items)
    n_warning  = len(warning_items)
    subject_prefix = f"🔴 {n_critical} SKU{'s' if n_critical > 1 else ''} en riesgo de stockout" if n_critical else f"🟡 {n_warning} SKU{'s' if n_warning > 1 else ''} por reabastecer"

    table_html = (
        '<table width="100%" style="border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#13141e;">'
        f'<th style="padding:8px 12px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">SKU</th>'
        f'<th style="padding:8px 12px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">Nombre</th>'
        f'<th style="padding:8px 12px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">Señal</th>'
        f'<th style="padding:8px 12px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">Cobertura</th>'
        f'<th style="padding:8px 12px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">Pedir</th>'
        f'<th style="padding:8px 12px;text-align:left;color:{_DIM};font-size:10px;text-transform:uppercase;">Proveedor</th>'
        '</tr></thead><tbody>'
        + "".join(all_items) +
        '</tbody></table>'
    )

    html = _base_html(
        "Alerta de inventario",
        f"""
        <p style="font-size:20px;font-weight:700;margin:0 0 4px;">Alerta de inventario</p>
        <p style="color:{_DIM};margin:0 0 24px;font-size:13px;">
          {'<span style="color:#ef4444;font-weight:600;">' + str(n_critical) + ' producto' + ('s' if n_critical > 1 else '') + ' en riesgo inmediato de stockout.</span> ' if n_critical else ''}
          {'<span style="color:#f59e0b;">' + str(n_warning) + ' producto' + ('s' if n_warning > 1 else '') + ' deben reabastecerse pronto.</span>' if n_warning else ''}
        </p>
        {table_html}
        {_button("Ver tablero de inventario", inventory_url)}
        <p style="color:{_DIM};font-size:11px;margin:0;">
          Esta alerta se genera automáticamente cuando hay productos en riesgo de stockout.
        </p>
        """,
    )
    try:
        _send(to, subject_prefix, html)
    except Exception as exc:
        log.error("Failed to send inventory alert to %s: %s", to, exc)


def send_training_complete_email(to: str, session_name: str, dashboard_url: str) -> None:
    """Notify user when a training job finishes."""
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
    except Exception as exc:
        log.error("Failed to send training complete email to %s: %s", to, exc)
