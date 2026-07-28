"""
How old the data behind the semáforo is, and the daily reminder that fires when
a tenant stops uploading.

Why this lives here and not in `inventory/`: the whole point of the feature is
the case where the user is NOT opening the app, so its primary consumer is the
notification loop, not the inventory dashboard. The read side (`GET
/data-freshness`) is the same computation rendered passively.

Two clocks, deliberately separate — this is the core of the fix:

* **Sales** age in weeks. A monthly uploader is normal, so nothing is wrong at
  day 20; the file is simply the one they meant to upload.
* **Stock** ages in DAYS. Every sale moves it. A stock figure three weeks old
  has had three weeks of unrecorded movement applied to it, and the semáforo
  computed on top of it is confident about a quantity nobody has confirmed.

Sharing one 14-day threshold across both (what the UI did before) means either
nagging the monthly uploader or staying silent over month-old stock. Hence the
five thresholds below.

Delivery follows the contract in `notifications/email.py`: the public senders
return bool and never raise, and every outcome — delivered or not — is written
to the recipient's activity log, so "no reminders" can never be mistaken for
"nothing to remind".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.config import settings
from backend.db.connection import query, query_one

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
# Sales: a distributor uploads once a month, so the passive warning starts at
# two weeks (unchanged from the old UI threshold) but nobody is emailed until a
# full monthly cycle plus five days of grace has passed without a new file.
SALES_STALE_DAYS    = 14   # passive warning in the header pill
SALES_REMINDER_DAYS = 35   # active email/WhatsApp: one cycle + grace
SALES_BLIND_DAYS    = 45   # the forecast is extrapolating >1.5 cycles: degrade

# Stock: one week is already a lot of unrecorded movement; at three weeks the
# quantity the semáforo divides by is a guess, so it stops claiming a colour.
STOCK_STALE_DAYS = 7
STOCK_BLIND_DAYS = 21

# At most one reminder a week per tenant. Only a delivered reminder starts the
# cooldown — a failed send must be retried tomorrow, not suppressed for a week.
REMINDER_COOLDOWN_DAYS = 7

REMINDER_EMAIL_ACTION    = "data_freshness_reminder_email"
REMINDER_WHATSAPP_ACTION = "data_freshness_reminder_whatsapp"


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _age_days(ts: Optional[datetime], now: datetime) -> Optional[int]:
    """Whole days between `ts` and `now`, or None when there is no timestamp.

    Naive timestamps coming back from the driver are read as UTC: the columns
    are TIMESTAMPTZ, and subtracting a naive from an aware datetime raises.
    """
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, (now - ts).days)


def _state(age_days: Optional[int], stale_at: int, blind_at: int) -> str:
    """`unknown` (never loaded) / `fresh` / `stale` (warn) / `blind` (degrade)."""
    if age_days is None:
        return "unknown"
    if age_days >= blind_at:
        return "blind"
    if age_days >= stale_at:
        return "stale"
    return "fresh"


# ── Stock ─────────────────────────────────────────────────────────────────────

def get_stock_freshness(tenant_id: str, now: Optional[datetime] = None) -> dict:
    """Age of the newest row in `inventory_stock`.

    Queried here rather than through `inventory.service` on purpose: this module
    owns the freshness question end to end, and the inventory service has no
    notion of it. `updated_at` is set to NOW() by every upsert (manual edit,
    bulk import, reception), so it is the honest "last time anyone confirmed a
    quantity" timestamp.
    """
    now = _now(now)
    row = query_one(
        """SELECT MAX(updated_at) AS last_update, COUNT(*) AS n
           FROM inventory_stock WHERE tenant_id = %s""",
        (tenant_id,),
    ) or {}
    last_update = row.get("last_update")
    tracked = int(row.get("n") or 0)
    age = _age_days(last_update, now)
    return {
        "tracked_skus": tracked,
        "updated_at":   last_update.isoformat() if last_update else None,
        "age_days":     age,
        "state":        _state(age, STOCK_STALE_DAYS, STOCK_BLIND_DAYS),
        "stale_days":   STOCK_STALE_DAYS,
        "blind_days":   STOCK_BLIND_DAYS,
    }


# ── Sales ─────────────────────────────────────────────────────────────────────

def get_sales_freshness(tenant_id: str, now: Optional[datetime] = None) -> dict:
    """Age of the sales history the active forecast was trained on.

    Two different ages exist and only one of them is the one the user cares
    about:

    * `data_through` — the last DATE inside the file, cached by the profiler in
      `session_configs.inspection -> profile -> stats -> date_max`. This is the
      real answer to "how old are my sales".
    * the session's `updated_at` — when it was last trained. Only used as a
      fallback, because a file uploaded today can still end three months ago.

    `basis` says which one was used, so the UI never states a precision it does
    not have.
    """
    now = _now(now)
    row = query_one(
        """SELECT s.id AS session_id, s.name AS session_name, s.updated_at,
                  sc.inspection #>> '{profile,stats,date_max}' AS data_through
           FROM sessions s
           LEFT JOIN session_configs sc ON sc.session_id = s.id
           WHERE s.tenant_id = %s AND s.status = 'COMPLETED'
           ORDER BY s.updated_at DESC LIMIT 1""",
        (tenant_id,),
    )
    if not row:
        return {
            "session_id": None, "session_name": None, "trained_at": None,
            "data_through": None, "basis": None, "age_days": None,
            "state": "unknown",
            "stale_days": SALES_STALE_DAYS, "reminder_days": SALES_REMINDER_DAYS,
            "blind_days": SALES_BLIND_DAYS,
        }

    data_through = row.get("data_through")
    age: Optional[int] = None
    basis: Optional[str] = None
    if data_through:
        try:
            last = datetime.fromisoformat(str(data_through)).replace(tzinfo=timezone.utc)
            age, basis = _age_days(last, now), "data_date"
        except ValueError:
            # A profiler that could not parse the date column leaves a value we
            # cannot trust; fall through to the upload date rather than guess.
            log.debug("unparseable date_max for tenant=%s: %r", tenant_id, data_through)
    if age is None:
        age, basis = _age_days(row.get("updated_at"), now), "upload_date"

    return {
        "session_id":   row.get("session_id"),
        "session_name": row.get("session_name"),
        "trained_at":   row["updated_at"].isoformat() if row.get("updated_at") else None,
        "data_through": str(data_through) if data_through else None,
        "basis":        basis,
        "age_days":     age,
        "state":        _state(age, SALES_STALE_DAYS, SALES_BLIND_DAYS),
        "stale_days":     SALES_STALE_DAYS,
        "reminder_days":  SALES_REMINDER_DAYS,
        "blind_days":     SALES_BLIND_DAYS,
    }


# ── Combined verdict ──────────────────────────────────────────────────────────

def get_tenant_freshness(tenant_id: str, now: Optional[datetime] = None) -> dict:
    """Both clocks plus the verdict the semáforo has to obey.

    `semaphore` is `degraded` as soon as EITHER input is blind. A green light
    is a claim about a quantity and a demand rate; if either is a guess, the
    honest state is "desactualizado", not a colour. Red/amber are left alone —
    they are already telling the user to act, and stale inputs make them more
    urgent, not less.
    """
    now = _now(now)
    sales = get_sales_freshness(tenant_id, now)
    stock = get_stock_freshness(tenant_id, now)

    reasons = []
    if stock["state"] == "blind":
        reasons.append("stock")
    if sales["state"] == "blind":
        reasons.append("sales")

    return {
        "sales": sales,
        "stock": stock,
        "semaphore": "degraded" if reasons else "current",
        "degraded_by": reasons,
        # True when the user should be nudged even though the semáforo still
        # holds — the passive warning tier.
        "warn": bool(reasons) or sales["state"] == "stale" or stock["state"] == "stale",
    }


# ── Daily reminder ────────────────────────────────────────────────────────────

def _tenants_with_completed_sessions() -> list[str]:
    """Only tenants that finished at least one training. A tenant that never
    uploaded is in onboarding, not in decay — telling them their sales are old
    would be nonsense."""
    return [
        r["tenant_id"] for r in query(
            "SELECT DISTINCT tenant_id FROM sessions WHERE status = 'COMPLETED'")
    ]


def _recipients(tenant_id: str) -> list[dict]:
    """Admins/managers, with the id needed to attribute the delivery outcome."""
    return [
        dict(r) for r in query(
            """SELECT id, email, whatsapp_number FROM users
               WHERE tenant_id = %s AND role IN ('admin', 'manager')""",
            (tenant_id,),
        )
    ]


def _last_reminder_at(tenant_id: str) -> Optional[datetime]:
    """When a reminder was last DELIVERED to this tenant.

    Failed sends are excluded on purpose: counting them would let a broken mail
    server silence the reminder for a week, which is the exact failure this
    feature exists to prevent.
    """
    row = query_one(
        """SELECT MAX(created_at) AS last_at FROM activity_logs
           WHERE tenant_id = %s AND status = 'success' AND action IN (%s, %s)""",
        (tenant_id, REMINDER_EMAIL_ACTION, REMINDER_WHATSAPP_ACTION),
    )
    return row.get("last_at") if row else None


def _record(tenant_id: str, user_id: str, action: str, delivered: bool, context: dict) -> None:
    """Write the outcome to the recipient's activity log. Never raises — an
    unwritable audit row must not abort the loop."""
    from backend.activity.service import log_action
    try:
        log_action(
            tenant_id, user_id, action, context=context,
            status="success" if delivered else "failed",
        )
    except Exception as e:  # pragma: no cover - audit write must never break alerting
        log.warning("freshness reminder log failed user=%s: %s", user_id, e)


def _is_due(freshness: dict) -> bool:
    """Reminder-worthy: the sales file has passed a full monthly cycle plus
    grace, or the stock the semáforo divides by has gone blind."""
    sales_age = freshness["sales"]["age_days"]
    if sales_age is not None and sales_age >= SALES_REMINDER_DAYS:
        return True
    return freshness["stock"]["state"] == "blind"


def run_daily_freshness_reminders(now: Optional[datetime] = None) -> int:
    """Mail (and WhatsApp) every tenant whose data has gone stale.

    Called once a day from the 08:00 UTC loop, right after the stockout digest —
    deliberately after, because a tenant with real stockouts gets the actionable
    email first and this one only adds the reason their numbers can't be trusted.

    Returns the number of tenants that received at least one delivered message.
    """
    from backend.notifications import email as email_mod
    from backend.notifications import whatsapp as wa_mod

    now = _now(now)
    app_url = getattr(settings, "frontend_url", "http://localhost:3000")
    upload_url = f"{app_url}/quick-start"

    notified = 0
    for tid in _tenants_with_completed_sessions():
        try:
            freshness = get_tenant_freshness(tid, now)
            if not _is_due(freshness):
                continue

            last = _last_reminder_at(tid)
            if last is not None:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if now - last < timedelta(days=REMINDER_COOLDOWN_DAYS):
                    continue

            recipients = _recipients(tid)
            if not recipients:
                continue

            sales_age = freshness["sales"]["age_days"]
            stock_age = freshness["stock"]["age_days"]
            # Only the clock that is actually late goes into the message. A
            # reminder triggered by month-old stock must not open with "your
            # sales are 4 days old" — naming a healthy figure as a problem is
            # how a reminder teaches the reader to ignore the next one.
            sales_late = sales_age is not None and sales_age >= SALES_REMINDER_DAYS
            stock_late = freshness["stock"]["state"] == "blind"
            any_delivered = False

            for r in recipients:
                if not r.get("email"):
                    continue
                delivered = email_mod.send_data_freshness_reminder_email(
                    to=r["email"],
                    sales_age_days=sales_age if sales_late else None,
                    stock_age_days=stock_age if stock_late else None,
                    upload_url=upload_url,
                )
                any_delivered = any_delivered or delivered
                _record(tid, r["id"], REMINDER_EMAIL_ACTION, delivered, {
                    "channel": "email",
                    "recipient": r["email"],
                    "sales_age_days": sales_age,
                    "stock_age_days": stock_age,
                    **({} if delivered else {"reason": email_mod.failure_reason()}),
                })

            # WhatsApp is a paid feature and opt-in per user (users.whatsapp_number).
            from backend.entitlements.plans import Feature
            from backend.entitlements.service import has_feature
            from backend.tenants.service import get_tenant
            if has_feature(get_tenant(tid) or {}, Feature.WHATSAPP_ALERTS):
                text = wa_mod.build_freshness_reminder_text(
                    sales_age_days=sales_age if sales_late else None,
                    stock_age_days=stock_age if stock_late else None,
                    upload_url=upload_url,
                )
                for r in recipients:
                    number = (r.get("whatsapp_number") or "").strip()
                    if not number:
                        continue
                    delivered = wa_mod.send_whatsapp(number, text)
                    any_delivered = any_delivered or delivered
                    _record(tid, r["id"], REMINDER_WHATSAPP_ACTION, delivered, {
                        "channel": "whatsapp",
                        "recipient": number,
                        "sales_age_days": sales_age,
                        "stock_age_days": stock_age,
                        **({} if delivered else {"reason": wa_mod.failure_reason()}),
                    })

            if any_delivered:
                notified += 1
        except Exception as e:
            log.error("freshness reminder failed for tenant=%s: %s", tid, e, exc_info=True)

    log.info("freshness reminder: notified %d tenants", notified)
    return notified
