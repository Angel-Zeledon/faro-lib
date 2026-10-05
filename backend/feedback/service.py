"""Storing a feedback report and telling the instance contact about it.

The row and the screenshot file are the record. The e-mail is a notification:
it is attempted AFTER the record is committed and its outcome is reported to the
person, never assumed -- a report that exists only inside a failed SMTP call is
a report we lost while telling the sender it was received.

Nothing here logs the message, the screenshot or the e-mail address: ids and
sizes only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.db.connection import get_conn, query_one
from backend.errors import AppError
from backend.feedback.validation import (
    MAX_APP_VERSION_CHARS, MAX_ERROR_CODE_CHARS, MAX_USER_AGENT_CHARS,
    RATE_MAX_PER_HOUR, RATE_WINDOW_SECONDS, Screenshot,
    clean_message, clean_page_path, clip, is_rate_limited,
)
from backend.storage import paths
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)


def screenshot_abspath(tenant_id: str, relative: str) -> Path:
    """Resolve a stored screenshot path, refusing anything outside the tenant's
    own feedback folder (the column is ours, but a path is checked where it is
    used, not where it was written)."""
    base = paths.feedback_dir(tenant_id).resolve()
    target = (base / relative).resolve()
    if base != target.parent:
        raise ValueError("screenshot path escapes the feedback folder")
    return target


def create_report(
    *,
    tenant_id: str,
    user_id: str,
    message: str,
    error_code: str | None,
    page_path: str | None,
    user_agent: str | None,
    app_version: str | None,
    screenshot: Screenshot | None,
    consent_reply: bool,
    consent_news: bool,
) -> dict:
    """Validate the text, enforce the hourly limit, write the file and the row.

    The limit check and the insert share one transaction behind a per-user
    advisory lock, so ten simultaneous clicks cannot all read "9 so far" and
    each add one. Raises AppError (`feedback_rate_limited`, 429) when over.
    """
    text = clean_message(message)
    report_id = generate_id("fbk")
    code = clip(error_code, MAX_ERROR_CODE_CHARS)
    page = clean_page_path(page_path)
    agent = clip(user_agent, MAX_USER_AGENT_CHARS)
    version = clip(app_version, MAX_APP_VERSION_CHARS)

    written: Path | None = None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"feedback:{user_id}",))
                cur.execute(
                    "SELECT COUNT(*) AS n FROM feedback_reports "
                    "WHERE user_id = %s AND created_at > NOW() - make_interval(secs => %s)",
                    (user_id, RATE_WINDOW_SECONDS),
                )
                recent = int(cur.fetchone()["n"])
                if is_rate_limited(recent):
                    raise AppError(
                        "feedback_rate_limited",
                        "You have sent several reports in the last hour. Please try again later.",
                        status_code=429,
                        params={"max": RATE_MAX_PER_HOUR},
                    )
                cur.execute("SELECT email FROM users WHERE id = %s AND tenant_id = %s",
                            (user_id, tenant_id))
                who = cur.fetchone() or {}

                relative = None
                if screenshot is not None:
                    target = paths.feedback_screenshot_file(tenant_id, report_id, screenshot.ext)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(screenshot.content)
                    written = target
                    relative = target.name

                cur.execute(
                    """INSERT INTO feedback_reports
                           (id, tenant_id, user_id, message, error_code, page_path,
                            user_agent, app_version, account_email, screenshot_path,
                            consent_reply, consent_reply_at, consent_news, consent_news_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, CASE WHEN %s THEN NOW() END,
                               %s, CASE WHEN %s THEN NOW() END)""",
                    (report_id, tenant_id, user_id, text, code, page, agent, version,
                     who.get("email"), relative,
                     bool(consent_reply), bool(consent_reply),
                     bool(consent_news), bool(consent_news)),
                )
    except Exception:
        # The transaction rolled back (or never committed): a file with no row
        # is storage nobody will ever find or erase.
        if written is not None:
            try:
                written.unlink(missing_ok=True)
            except OSError:
                log.warning("feedback: could not remove orphan screenshot for %s", report_id)
        raise

    return {
        "id": report_id,
        "message": text,
        "error_code": code,
        "page_path": page,
        "user_agent": agent,
        "app_version": version,
        "account_email": who.get("email"),
        "consent_reply": bool(consent_reply),
        "consent_news": bool(consent_news),
    }


def notify_instance_contact(
    report: dict, *, tenant_id: str, screenshot: Screenshot | None,
) -> bool:
    """E-mail the report to CONTACT_EMAIL. Returns whether it was sent.

    False covers both "no contact address configured" and "the transport
    failed"; the report is stored either way and the caller says so.
    """
    from backend.service_config.resolver import effective
    to = effective().contact_email
    if not to:
        log.warning(
            "[feedback] report %s stored but CONTACT_EMAIL is not set: nobody was told",
            report["id"],
        )
        return False
    from backend.notifications.email import send_feedback_email
    tenant = query_one("SELECT name FROM tenants WHERE id = %s", (tenant_id,)) or {}
    sent = send_feedback_email(
        to=to,
        tenant_name=tenant.get("name") or tenant_id,
        tenant_id=tenant_id,
        report=report,
        screenshot=screenshot,
    )
    if sent:
        from backend.db.connection import execute
        execute("UPDATE feedback_reports SET notified = TRUE WHERE id = %s AND tenant_id = %s",
                (report["id"], tenant_id))
    return sent

