"""Warehouse-scoped digests: the daily alert and the freshness reminder, for the
users a company-wide digest is withheld from.

A user limited to some warehouses used to get NOTHING from these digests (only
an activity line saying the company summary was not sent). They now get the same
digest, computed over their warehouses only.

Nothing here computes a second number. The rows come from the same per-warehouse
status the scoped screens read (`get_inventory_status_by_warehouse`), restricted
by the same helper those screens call (`warehouse_scope.scoped_status_rows`,
which also strips transfer pointers at warehouses outside the scope); the
freshness rows are filtered with `warehouse_scope.filter_rows`, as
`GET /data-freshness` does. The company-wide snapshot is never touched.

A recipient whose scope has nothing to report gets no e-mail, and an activity
line (`scoped_digest_empty`) says so: a missing e-mail must not read as a broken
one. The monthly ROI recap stays company-only: `roi_service.get_month_report`
has no warehouse filter, so scoped users are told it was withheld
(`company_digest_withheld`).
"""

from __future__ import annotations

import logging
from typing import Iterable

from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser

log = logging.getLogger(__name__)

# Activity-log action for "your digest had nothing in it" (rendered by the
# frontend as enum.activity_<action>).
SCOPED_DIGEST_EMPTY_ACTION = "scoped_digest_empty"


# ── Pure logic ───────────────────────────────────────────────────────────────

def scope_label_names(names: Iterable[str] | None) -> list[str]:
    """The recipient's warehouse names in a stable, readable order."""
    return sorted({n for n in (names or []) if n}, key=lambda n: n.casefold())


def recipient_user(tenant_id: str, recipient: dict) -> CurrentUser:
    """The caller identity the scope helpers resolve a recipient's scope from.
    The role is irrelevant to scope resolution."""
    return CurrentUser(recipient["id"], tenant_id, "analyst")


def split_signals(items: list[dict]) -> tuple[list[dict], list[dict]]:
    critical = [i for i in items if i.get("signal") == "PEDIR_YA"]
    warning = [i for i in items if i.get("signal") == "PEDIR_PRONTO"]
    return critical, warning


def scoped_alert_content(user: CurrentUser, warehouse_rows: list[dict]) -> dict:
    """What a scoped recipient's stockout digest holds.

    `warehouse_rows`: the tenant's per-(sku, warehouse) status rows, unfiltered.
    Returns the warehouses named, the critical and warning rows (theirs only)
    and the number of transfer suggestions that remain after the scope strips
    the ones pointing outside it.
    """
    names = scope_label_names(wscope.scope_names(user))
    rows = wscope.scoped_status_rows(user, warehouse_rows)
    critical, warning = split_signals(rows)
    return {
        "warehouses": names,
        "critical": critical,
        "warning": warning,
        "transfer_count": sum(1 for r in rows if r.get("recommended_action") == "transfer"),
    }


def scoped_freshness_content(user: CurrentUser, freshness: dict) -> dict:
    """The silent warehouses a scoped recipient's reminder may name: their own."""
    names = scope_label_names(wscope.scope_names(user))
    items = wscope.filter_rows(user, (freshness.get("warehouses") or {}).get("items") or [],
                               key="name")
    return {
        "warehouses": names,
        "silent": [{"name": w["name"], "days": w["silent_days"]}
                   for w in items if w.get("lagging")],
    }


# ── Delivery ─────────────────────────────────────────────────────────────────

def record_scoped_digest_empty(tenant_id: str, user_id: str, digest: str,
                               warehouses: list[str]) -> None:
    """Say, in the recipient's own activity log, why no digest reached them."""
    from backend.inventory.service import record_notification_delivery
    record_notification_delivery(
        tenant_id, user_id, SCOPED_DIGEST_EMPTY_ACTION, True,
        context={"digest": digest, "warehouses": warehouses},
    )


def _record_failure(tenant_id: str, recipient: dict, action: str, channel: str, reason: str) -> None:
    from backend.inventory.service import record_notification_delivery
    record_notification_delivery(
        tenant_id, recipient["id"], action, False,
        context={"channel": channel, "recipient": recipient.get(channel_key(channel)),
                 "reason": reason},
    )


def channel_key(channel: str) -> str:
    return "email" if channel == "email" else "whatsapp_number"


def send_scoped_inventory_alerts(
    tenant_id: str,
    recipients: list[dict],
    warehouse_rows: list[dict],
    *,
    inventory_url: str,
    period: str,
) -> int:
    """Send each warehouse-limited recipient the stockout digest of THEIR
    warehouses. Returns how many recipients received at least one message.
    One recipient failing never stops the others, and never goes unrecorded."""
    from backend.inventory.service import record_notification_delivery
    from backend.notifications import email as email_mod
    from backend.notifications import whatsapp as wa_mod

    reached = 0
    for r in recipients:
        try:
            user = recipient_user(tenant_id, r)
            content = scoped_alert_content(user, warehouse_rows)
            names = content["warehouses"]
            critical, warning = content["critical"], content["warning"]
            if not critical and not warning:
                record_scoped_digest_empty(tenant_id, r["id"], "inventory_alert", names)
                continue

            got = False
            if r.get("email"):
                delivered = email_mod.send_inventory_alert_email(
                    to=r["email"], critical_items=critical, warning_items=warning,
                    inventory_url=inventory_url, period=period, tenant_id=tenant_id,
                    scope_warehouses=names,
                )
                got = got or delivered
                record_notification_delivery(
                    tenant_id, r["id"], "inventory_alert_email", delivered,
                    context={
                        "channel": "email", "recipient": r["email"],
                        "critical": len(critical), "warning": len(warning),
                        "warehouses": names,
                        **({} if delivered else {"reason": email_mod.failure_reason(tenant_id)}),
                    },
                )
            number = (r.get("whatsapp_number") or "").strip()
            if number:
                text = wa_mod.build_inventory_alert_text(
                    critical, warning, inventory_url,
                    transfer_count=content["transfer_count"], period=period,
                    scope_warehouses=names,
                )
                # Same paid-only gate as the company digest.
                delivered = wa_mod.send_whatsapp(number, text, tenant_id=tenant_id,
                                                 plan_gated=True)
                got = got or delivered
                record_notification_delivery(
                    tenant_id, r["id"], "inventory_alert_whatsapp", delivered,
                    context={
                        "channel": "whatsapp", "recipient": number,
                        "critical": len(critical), "warning": len(warning),
                        "warehouses": names,
                        **({} if delivered else {"reason": wa_mod.failure_reason(tenant_id)}),
                    },
                )
            reached += 1 if got else 0
        except Exception as e:
            log.error("scoped alert failed tenant=%s user=%s: %s", tenant_id, r.get("id"), e)
            try:
                _record_failure(tenant_id, r, "inventory_alert_email", "email",
                                f"scoped digest failed: {e}")
            except Exception as inner:  # pragma: no cover - audit write must never break alerting
                log.error("scoped alert: could not record failure: %s", inner)
    return reached


def send_scoped_freshness_reminders(
    tenant_id: str,
    recipients: list[dict],
    freshness: dict,
    *,
    sales_age: int | None,
    stock_age: int | None,
    sales_late: bool,
    stock_late: bool,
    upload_url: str,
    email_action: str,
    whatsapp_action: str,
) -> int:
    """The freshness reminder for each warehouse-limited recipient: the same
    company clocks `GET /data-freshness` shows them, and only THEIR silent
    warehouses. Nothing to say -> no message and an activity line."""
    from backend.inventory.service import record_notification_delivery
    from backend.notifications import email as email_mod
    from backend.notifications import whatsapp as wa_mod

    reached = 0
    for r in recipients:
        try:
            user = recipient_user(tenant_id, r)
            content = scoped_freshness_content(user, freshness)
            names, silent = content["warehouses"], content["silent"]
            if not sales_late and not stock_late and not silent:
                record_scoped_digest_empty(tenant_id, r["id"], "freshness_reminder", names)
                continue

            got = False
            if r.get("email"):
                delivered = email_mod.send_data_freshness_reminder_email(
                    tenant_id=tenant_id, to=r["email"],
                    sales_age_days=sales_age if sales_late else None,
                    stock_age_days=stock_age if stock_late else None,
                    silent_warehouses=silent, upload_url=upload_url,
                    scope_warehouses=names,
                )
                got = got or delivered
                record_notification_delivery(
                    tenant_id, r["id"], email_action, delivered,
                    context={
                        "channel": "email", "recipient": r["email"],
                        "sales_age_days": sales_age, "stock_age_days": stock_age,
                        "warehouses": names,
                        **({"silent_warehouses": len(silent)} if silent else {}),
                        **({} if delivered else {"reason": email_mod.failure_reason(tenant_id)}),
                    },
                )
            number = (r.get("whatsapp_number") or "").strip()
            if number:
                text = wa_mod.build_freshness_reminder_text(
                    sales_age_days=sales_age if sales_late else None,
                    stock_age_days=stock_age if stock_late else None,
                    silent_warehouses=silent, upload_url=upload_url,
                    scope_warehouses=names,
                )
                delivered = wa_mod.send_whatsapp(number, text, tenant_id=tenant_id,
                                                 plan_gated=True)
                got = got or delivered
                record_notification_delivery(
                    tenant_id, r["id"], whatsapp_action, delivered,
                    context={
                        "channel": "whatsapp", "recipient": number,
                        "sales_age_days": sales_age, "stock_age_days": stock_age,
                        "warehouses": names,
                        **({"silent_warehouses": len(silent)} if silent else {}),
                        **({} if delivered else {"reason": wa_mod.failure_reason(tenant_id)}),
                    },
                )
            reached += 1 if got else 0
        except Exception as e:
            log.error("scoped freshness reminder failed tenant=%s user=%s: %s",
                      tenant_id, r.get("id"), e)
            try:
                _record_failure(tenant_id, r, email_action, "email",
                                f"scoped digest failed: {e}")
            except Exception as inner:  # pragma: no cover
                log.error("scoped freshness: could not record failure: %s", inner)
    return reached
