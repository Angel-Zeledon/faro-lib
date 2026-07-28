"""
Data freshness: the two clocks, the degraded semáforo, and the reminder that
fires when the user has stopped opening the app (plan #6 of
docs/friccion-onboarding-2026-07-27.md).

What is pinned here:

1. Stock and sales age on SEPARATE clocks. The app used to have one 14-day
   warning over the sales session and nothing at all over `inventory_stock`, so
   the semáforo was fully confident on top of month-old stock.
2. Past the blind thresholds the verdict becomes `degraded` — the traffic light
   stops claiming a colour instead of continuing to show green.
3. The reminder actually leaves the building: every test asserts WHAT was sent
   (the ages inside subject/body) and TO WHOM (the activity row on that
   recipient), never merely that a function was called.

conftest patches `email._send` and `whatsapp._send` session-wide, so nothing
here can reach the real Twilio/SMTP credentials in the local .env.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.db.connection import _json, execute, query, query_one
from backend.notifications import freshness_service as fs

NOW = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _db_pool(client):
    """The connection pool is opened by the app's lifespan. Most tests here talk
    to the DB directly without going through an endpoint, so the app fixture has
    to come up first or `execute()` raises "DB pool not initialized"."""
    return client


# ── Arrangement helpers ──────────────────────────────────────────────────────

def _stock_row(tenant_id: str, sku: str, age_days: int,
               now: datetime | None = None) -> None:
    """A stock row last confirmed `age_days` ago.

    `now` defaults to the frozen NOW the unit tests pass into the service.
    Tests that go through the HTTP endpoint must pass the REAL clock: the
    endpoint calls datetime.now() itself, so a NOW captured at import time
    reports an age one day off as soon as the run crosses UTC midnight — which
    a suite that can take over an hour does routinely.
    """
    execute(
        """INSERT INTO inventory_stock (tenant_id, sku, current_stock, updated_at)
           VALUES (%s, %s, %s, %s)""",
        (tenant_id, sku, 10, (now or NOW) - timedelta(days=age_days)),
    )


def _completed_session(
    tenant_id: str, *, trained_days_ago: int, data_through_days_ago: int | None = None,
    now: datetime | None = None,
) -> str:
    """A COMPLETED session whose profiler cached `date_max`, when given.

    The two ages are independent on purpose: a file uploaded today can still
    end three months ago, and that is the age the buyer cares about.

    `now` defaults to the frozen NOW the unit tests pass into the service. Tests
    that go through the HTTP endpoint must pass the REAL clock instead: the
    endpoint calls datetime.now() itself, so seeding against a NOW captured at
    import time reports an age one day off whenever the run crosses UTC
    midnight — which a 28-minute suite does often enough to matter.
    """
    now = now or NOW
    sid = f"sess-{uuid4().hex[:10]}"
    owner = query_one("SELECT id FROM users WHERE tenant_id = %s LIMIT 1", (tenant_id,))
    execute(
        """INSERT INTO sessions (id, tenant_id, name, status, created_by, updated_at)
           VALUES (%s, %s, %s, 'COMPLETED', %s, %s)""",
        (sid, tenant_id, "Ventas", owner["id"] if owner else "pytest",
         now - timedelta(days=trained_days_ago)),
    )
    inspection = None
    if data_through_days_ago is not None:
        last_date = (now - timedelta(days=data_through_days_ago)).date().isoformat()
        inspection = {"profile": {"stats": {"n_rows": 100, "date_max": last_date}}}
    execute(
        "INSERT INTO session_configs (session_id, tenant_id, inspection) VALUES (%s, %s, %s)",
        (sid, tenant_id, _json(inspection) if inspection else None),
    )
    return sid


def _only_this_tenant(monkeypatch, tenant_id: str) -> None:
    """The loop sweeps every tenant in the database; pin it to this one so the
    assertions are about this tenant's data, not the suite's leftovers."""
    monkeypatch.setattr(fs, "_tenants_with_completed_sessions", lambda: [tenant_id])


def _activity(tenant_id: str, action: str) -> list[dict]:
    return [dict(r) for r in query(
        """SELECT user_id, status, context FROM activity_logs
           WHERE tenant_id = %s AND action = %s""",
        (tenant_id, action),
    )]


# ── 1. Stock ages in days, on its own clock ──────────────────────────────────

class TestStockFreshness:
    def test_no_stock_at_all_is_unknown_not_fresh(self, test_tenant):
        out = fs.get_stock_freshness(test_tenant["id"], NOW)
        assert out["tracked_skus"] == 0
        assert out["age_days"] is None
        assert out["state"] == "unknown"

    @pytest.mark.parametrize("age,expected", [
        (0,  "fresh"),
        (6,  "fresh"),
        (7,  "stale"),   # STOCK_STALE_DAYS boundary
        (20, "stale"),
        (21, "blind"),   # STOCK_BLIND_DAYS boundary
        (60, "blind"),
    ])
    def test_state_by_age(self, test_tenant, age, expected):
        _stock_row(test_tenant["id"], f"SKU-{age}", age)
        out = fs.get_stock_freshness(test_tenant["id"], NOW)
        assert out["age_days"] == age
        assert out["state"] == expected

    def test_age_comes_from_the_newest_row(self, test_tenant):
        """One SKU touched yesterday does not make a 40-day-old table fresh —
        but it IS the honest answer to "when did anyone last confirm a
        quantity", so the newest row wins."""
        tid = test_tenant["id"]
        _stock_row(tid, "OLD-1", 40)
        _stock_row(tid, "NEW-1", 1)
        out = fs.get_stock_freshness(tid, NOW)
        assert out["age_days"] == 1
        assert out["tracked_skus"] == 2

    def test_stock_is_not_judged_by_the_sales_threshold(self, test_tenant):
        """The bug this feature fixes: 10-day-old stock was invisible because
        the only clock in the product warned at 14 days."""
        tid = test_tenant["id"]
        _stock_row(tid, "SKU-10", 10)
        _completed_session(tid, trained_days_ago=10, data_through_days_ago=10)

        out = fs.get_tenant_freshness(tid, NOW)
        assert out["stock"]["state"] == "stale", "stale stock must be reported"
        assert out["sales"]["state"] == "fresh", "10-day-old sales are perfectly normal"
        assert out["warn"] is True

    def test_another_tenants_stock_is_not_counted(self, test_tenant, make_tenant_user_headers):
        """Freshness is per tenant — a neighbour's fresh upload cannot make
        this tenant's semáforo look current."""
        _stock_row(test_tenant["id"], "MINE", 30)
        _, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        _stock_row(other_tid, "THEIRS", 0)

        assert fs.get_stock_freshness(test_tenant["id"], NOW)["age_days"] == 30
        assert fs.get_stock_freshness(other_tid, NOW)["age_days"] == 0


# ── 2. Sales age from the DATA, not from the upload ──────────────────────────

class TestSalesFreshness:
    def test_age_uses_the_last_date_in_the_file(self, test_tenant):
        """Trained today over a file that ends 40 days ago: the sales ARE 40
        days old, and saying "updated today" would be the lie."""
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=0, data_through_days_ago=40)

        out = fs.get_sales_freshness(tid, NOW)
        assert out["age_days"] == 40
        assert out["basis"] == "data_date"
        assert out["state"] == "stale"
        assert out["data_through"] == (NOW - timedelta(days=40)).date().isoformat()

    def test_falls_back_to_the_training_date_when_the_profile_has_no_date(self, test_tenant):
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=20, data_through_days_ago=None)

        out = fs.get_sales_freshness(tid, NOW)
        assert out["age_days"] == 20
        assert out["basis"] == "upload_date"
        assert out["state"] == "stale"

    def test_draft_sessions_do_not_count_as_data(self, test_tenant):
        """A wizard someone abandoned is not a trained forecast."""
        tid = test_tenant["id"]
        owner = query_one("SELECT id FROM users WHERE tenant_id = %s LIMIT 1", (tid,))
        execute(
            """INSERT INTO sessions (id, tenant_id, name, status, created_by, updated_at)
               VALUES (%s, %s, 'Borrador', 'DRAFT', %s, %s)""",
            (f"sess-{uuid4().hex[:10]}", tid, owner["id"] if owner else "pytest", NOW),
        )
        out = fs.get_sales_freshness(tid, NOW)
        assert out["age_days"] is None
        assert out["state"] == "unknown"

    def test_newest_completed_session_wins(self, test_tenant):
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=90, data_through_days_ago=95)
        _completed_session(tid, trained_days_ago=3, data_through_days_ago=5)
        assert fs.get_sales_freshness(tid, NOW)["age_days"] == 5


# ── 3. The semáforo degrades instead of showing a confident colour ───────────

class TestSemaphoreDegradation:
    def test_fresh_data_keeps_the_semaphore_current(self, test_tenant):
        tid = test_tenant["id"]
        _stock_row(tid, "SKU-1", 1)
        _completed_session(tid, trained_days_ago=1, data_through_days_ago=2)

        out = fs.get_tenant_freshness(tid, NOW)
        assert out["semaphore"] == "current"
        assert out["degraded_by"] == []
        assert out["warn"] is False

    def test_month_old_stock_degrades_the_semaphore(self, test_tenant):
        """The headline case: sales uploaded yesterday, stock from a month ago.
        Every signal is computed against a quantity nobody has confirmed."""
        tid = test_tenant["id"]
        _stock_row(tid, "SKU-1", 30)
        _completed_session(tid, trained_days_ago=1, data_through_days_ago=2)

        out = fs.get_tenant_freshness(tid, NOW)
        assert out["semaphore"] == "degraded"
        assert out["degraded_by"] == ["stock"]
        assert out["stock"]["age_days"] == 30

    def test_two_month_old_sales_degrade_the_semaphore(self, test_tenant):
        tid = test_tenant["id"]
        _stock_row(tid, "SKU-1", 1)
        _completed_session(tid, trained_days_ago=60, data_through_days_ago=60)

        out = fs.get_tenant_freshness(tid, NOW)
        assert out["semaphore"] == "degraded"
        assert out["degraded_by"] == ["sales"]

    def test_both_clocks_are_reported_when_both_are_blind(self, test_tenant):
        tid = test_tenant["id"]
        _stock_row(tid, "SKU-1", 50)
        _completed_session(tid, trained_days_ago=50, data_through_days_ago=50)

        out = fs.get_tenant_freshness(tid, NOW)
        assert out["degraded_by"] == ["stock", "sales"]

    def test_a_tenant_with_no_stock_is_not_degraded(self, test_tenant):
        """Nothing to degrade: /hoy shows its "no inventory loaded" state, and
        claiming the semáforo is outdated on top of that would be noise."""
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=1, data_through_days_ago=1)
        out = fs.get_tenant_freshness(tid, NOW)
        assert out["semaphore"] == "current"


# ── 4. The read endpoint ─────────────────────────────────────────────────────

class TestFreshnessEndpoint:
    def test_returns_both_clocks_and_the_verdict(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        # Real clock on BOTH clocks, not the frozen NOW — the endpoint uses its
        # own, and seeding either side against NOW drifts a day at midnight.
        real_now = datetime.now(timezone.utc)
        _stock_row(tid, "SKU-1", 30, now=real_now)
        _completed_session(tid, trained_days_ago=2, data_through_days_ago=3,
                           now=real_now)

        resp = client.get("/api/v1/data-freshness", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["semaphore"] == "degraded"
        assert data["degraded_by"] == ["stock"]
        assert data["stock"]["age_days"] == 30
        assert data["sales"]["age_days"] == 3
        assert data["stock"]["blind_days"] == fs.STOCK_BLIND_DAYS

    def test_viewer_can_read_it(self, client, viewer_headers, test_tenant):
        """Read-only and, above all, the honest answer must not be a privilege:
        a viewer looking at a green board deserves to know it is two months old."""
        _stock_row(test_tenant["id"], "SKU-1", 40, now=datetime.now(timezone.utc))
        resp = client.get("/api/v1/data-freshness", headers=viewer_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["stock"]["age_days"] == 40

    def test_requires_authentication(self, client):
        assert client.get("/api/v1/data-freshness").status_code in (401, 403)

    def test_does_not_leak_another_tenants_freshness(
        self, client, test_tenant, make_tenant_user_headers,
    ):
        _stock_row(test_tenant["id"], "MINE", 40)
        other = make_tenant_user_headers(role="admin")
        resp = client.get("/api/v1/data-freshness", headers=other)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["stock"]["tracked_skus"] == 0


# ── 5. The reminder that reaches a user who stopped opening the app ──────────

def _capture_emails(monkeypatch) -> list[dict]:
    """Capture at the transport boundary: this proves the message was actually
    rendered and handed over, not that a helper was invoked."""
    sent: list[dict] = []
    from backend.notifications import email as email_mod
    monkeypatch.setattr(
        email_mod, "_send",
        lambda to, subject, html, attachment=None: sent.append(
            {"to": to, "subject": subject, "html": html}),
    )
    return sent


class TestFreshnessReminder:
    def test_stale_sales_email_states_the_age_and_reaches_the_admin(
        self, monkeypatch, registered_user, test_tenant,
    ):
        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40)
        _stock_row(tid, "SKU-1", 1)
        _only_this_tenant(monkeypatch, tid)
        sent = _capture_emails(monkeypatch)

        assert fs.run_daily_freshness_reminders(NOW) == 1

        assert len(sent) == 1, "the reminder never left the building"
        assert sent[0]["to"] == registered_user["email"]
        assert "40 días" in sent[0]["subject"]
        assert "40 días" in sent[0]["html"]
        assert "/quick-start" in sent[0]["html"], "no way back into the product"

        rows = _activity(tid, fs.REMINDER_EMAIL_ACTION)
        assert len(rows) == 1
        assert rows[0]["user_id"] == uid
        assert rows[0]["status"] == "success"
        assert rows[0]["context"]["recipient"] == registered_user["email"]
        assert rows[0]["context"]["sales_age_days"] == 40

    @pytest.mark.parametrize("age,expected_emails", [
        (20, 0),   # monthly uploader mid-cycle
        (34, 0),   # a punctual monthly uploader is at ~34 days right before uploading
        (35, 1),   # SALES_REMINDER_DAYS: one cycle plus grace has passed
    ])
    def test_monthly_uploader_is_not_nagged_before_the_cycle_closes(
        self, monkeypatch, registered_user, test_tenant, age, expected_emails,
    ):
        """The threshold that decides between a useful reminder and spam that
        gets filtered. A file that is one cycle old is not a problem yet."""
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=age, data_through_days_ago=age)
        _stock_row(tid, "SKU-1", 2)
        _only_this_tenant(monkeypatch, tid)
        sent = _capture_emails(monkeypatch)

        fs.run_daily_freshness_reminders(NOW)

        assert len(sent) == expected_emails
        assert len(_activity(tid, fs.REMINDER_EMAIL_ACTION)) == expected_emails

    def test_blind_stock_alone_triggers_its_own_reminder(
        self, monkeypatch, registered_user, test_tenant,
    ):
        """Sales uploaded this week, stock untouched for a month: the semáforo
        is degraded, so the user has to hear about it even though the sales
        clock is fine."""
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=3, data_through_days_ago=4)
        _stock_row(tid, "SKU-1", 25)
        _only_this_tenant(monkeypatch, tid)
        sent = _capture_emails(monkeypatch)

        assert fs.run_daily_freshness_reminders(NOW) == 1
        assert len(sent) == 1
        assert "25 días" in sent[0]["subject"]
        assert "25 días" in sent[0]["html"]
        # The sales clock is healthy, so its age must not appear as a problem.
        assert "4 días" not in sent[0]["html"]

    def test_fresh_stock_age_is_not_mentioned_in_a_sales_reminder(
        self, monkeypatch, registered_user, test_tenant,
    ):
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40)
        _stock_row(tid, "SKU-1", 3)
        _only_this_tenant(monkeypatch, tid)
        sent = _capture_emails(monkeypatch)

        fs.run_daily_freshness_reminders(NOW)
        assert "40 días" in sent[0]["html"]
        assert "3 días" not in sent[0]["html"]

    def test_a_delivered_reminder_silences_the_next_six_days(
        self, monkeypatch, registered_user, test_tenant,
    ):
        """Driven off the REAL clock, unlike its neighbours, and it has to be.

        The cooldown reads `MAX(activity_logs.created_at)`, and that column is
        stamped by the DATABASE's NOW() — so the cadence is measured between a
        real timestamp and whatever clock the loop is handed. A frozen NOW only
        agrees with the DB while today happens to be that date; the day it is
        not, `NOW + 8 days` is less than seven real days after the row was
        written and the reminder silently does not come back.
        """
        tid = test_tenant["id"]
        base = datetime.now(timezone.utc)
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40, now=base)
        _only_this_tenant(monkeypatch, tid)
        sent = _capture_emails(monkeypatch)

        assert fs.run_daily_freshness_reminders(base) == 1
        assert fs.run_daily_freshness_reminders(base + timedelta(days=1)) == 0
        assert fs.run_daily_freshness_reminders(base + timedelta(days=6)) == 0
        assert len(sent) == 1, "the daily loop mailed the same tenant twice"

        # And it comes back once the cooldown has elapsed — the reminder is a
        # cadence, not a one-shot.
        assert fs.run_daily_freshness_reminders(base + timedelta(days=8)) == 1
        assert len(sent) == 2

    def test_a_failed_reminder_does_not_start_the_cooldown(
        self, monkeypatch, registered_user, test_tenant,
    ):
        """The failure this whole feature exists to prevent: a dead transport
        silently buying itself a week of silence."""
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40)
        _only_this_tenant(monkeypatch, tid)
        monkeypatch.setattr("backend.notifications.email.is_configured", lambda: True)

        # A transport that is down today and back up tomorrow.
        up = {"value": False}
        sent: list[dict] = []

        def _sender(*, to, sales_age_days, stock_age_days, upload_url):
            if not up["value"]:
                return False
            sent.append({"to": to, "sales_age_days": sales_age_days})
            return True

        monkeypatch.setattr(
            "backend.notifications.email.send_data_freshness_reminder_email", _sender)

        assert fs.run_daily_freshness_reminders(NOW) == 0

        rows = _activity(tid, fs.REMINDER_EMAIL_ACTION)
        assert len(rows) == 1, "an alert that never arrived left no trace"
        assert rows[0]["status"] == "failed"
        assert rows[0]["context"]["reason"] == "transport_error"
        # No cooldown was earned: no reminder has ever been DELIVERED.
        assert fs._last_reminder_at(tid) is None

        # So tomorrow it tries again instead of waiting out a week of silence.
        up["value"] = True
        assert fs.run_daily_freshness_reminders(NOW + timedelta(days=1)) == 1
        assert len(sent) == 1
        assert sent[0]["to"] == registered_user["email"]

    def test_unconfigured_transport_is_recorded_with_its_own_reason(
        self, monkeypatch, registered_user, test_tenant,
    ):
        from backend.notifications import email as email_mod

        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40)
        _only_this_tenant(monkeypatch, tid)
        monkeypatch.setattr(email_mod.settings, "resend_api_key", "")
        monkeypatch.setattr(email_mod.settings, "smtp_user", "")
        monkeypatch.setattr(email_mod.settings, "smtp_pass", "")
        monkeypatch.setattr(email_mod, "_send", email_mod._transport_send)

        assert fs.run_daily_freshness_reminders(NOW) == 0
        rows = _activity(tid, fs.REMINDER_EMAIL_ACTION)
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["context"]["reason"] == "not_configured"

    def test_tenant_that_never_trained_is_never_reminded(
        self, monkeypatch, registered_user, test_tenant,
    ):
        """They are in onboarding, not in decay. "Your sales are old" would be
        the wrong sentence entirely."""
        tid = test_tenant["id"]
        # Even with ancient stock rows, which on their own would be due.
        _stock_row(tid, "SKU-1", 60)
        assert fs.get_tenant_freshness(tid, NOW)["stock"]["state"] == "blind"

        # The sweep is what holds them out: the loop only ever visits tenants
        # returned here, and this one is not among them.
        assert tid not in fs._tenants_with_completed_sessions()
        assert fs.get_sales_freshness(tid, NOW)["state"] == "unknown"

    def test_a_completed_session_puts_the_tenant_in_the_sweep(self, test_tenant):
        """Guards the query the loop actually runs — the other tests pin it."""
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40)
        assert tid in fs._tenants_with_completed_sessions()

    def test_whatsapp_reminder_states_the_age_and_the_recipient(
        self, monkeypatch, registered_user, test_tenant,
    ):
        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        execute("UPDATE users SET whatsapp_number = %s WHERE id = %s", ("+573001112222", uid))
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40)
        _only_this_tenant(monkeypatch, tid)
        _capture_emails(monkeypatch)
        monkeypatch.setattr("backend.entitlements.service.has_feature", lambda *a, **kw: True)

        wa_sent: list[tuple] = []
        monkeypatch.setattr(
            "backend.notifications.whatsapp.send_whatsapp",
            lambda number, text, *a, **kw: wa_sent.append((number, text)) or True)

        fs.run_daily_freshness_reminders(NOW)

        assert len(wa_sent) == 1
        number, text = wa_sent[0]
        assert number == "+573001112222"
        assert "40 días" in text
        assert "/quick-start" in text

        rows = _activity(tid, fs.REMINDER_WHATSAPP_ACTION)
        assert len(rows) == 1
        assert rows[0]["user_id"] == uid
        assert rows[0]["status"] == "success"
        assert rows[0]["context"]["recipient"] == "+573001112222"

    def test_whatsapp_is_not_sent_without_the_entitlement(
        self, monkeypatch, registered_user, test_tenant,
    ):
        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        execute("UPDATE users SET whatsapp_number = %s WHERE id = %s", ("+573001112222", uid))
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40)
        _only_this_tenant(monkeypatch, tid)
        _capture_emails(monkeypatch)
        monkeypatch.setattr("backend.entitlements.service.has_feature", lambda *a, **kw: False)
        monkeypatch.setattr(
            "backend.notifications.whatsapp.send_whatsapp",
            lambda *a, **kw: pytest.fail("WhatsApp sent without the plan feature"))

        fs.run_daily_freshness_reminders(NOW)
        assert _activity(tid, fs.REMINDER_WHATSAPP_ACTION) == []

    def test_one_tenants_failure_does_not_stop_the_others(
        self, monkeypatch, registered_user, test_tenant,
    ):
        """The loop runs for every tenant in the product; one broken row must
        not cancel everyone else's reminder."""
        tid = test_tenant["id"]
        _completed_session(tid, trained_days_ago=40, data_through_days_ago=40)
        monkeypatch.setattr(
            fs, "_tenants_with_completed_sessions", lambda: ["tenant-does-not-exist", tid])
        sent = _capture_emails(monkeypatch)

        assert fs.run_daily_freshness_reminders(NOW) == 1
        assert len(sent) == 1
        assert sent[0]["to"] == registered_user["email"]


# ── 6. It has to be wired into the loop that actually runs daily ─────────────

class _StopLoop(BaseException):
    """Escapes the infinite scheduler loop. A BaseException on purpose: the
    loop catches `Exception`, so a plain error would be swallowed and retried.
    Same convention as tests/test_worker_schedulers.py."""


class TestReminderRunsInTheDailyLoop:
    def test_the_08_utc_loop_fires_the_freshness_reminder(self, monkeypatch):
        """A reminder nothing calls is a reminder nobody receives. Time is
        faked — this never sleeps."""
        from backend.workers import worker

        calls: list[str] = []
        monkeypatch.setattr(
            "backend.inventory.service.run_daily_inventory_alerts",
            lambda: calls.append("stockout"))
        monkeypatch.setattr(
            "backend.inventory.supplier_health_service.run_daily_supplier_lead_time_alerts",
            lambda: calls.append("supplier_lead_time"))
        monkeypatch.setattr(
            fs, "run_daily_freshness_reminders", lambda: calls.append("freshness"))

        # The first sleep returns so the loop reaches the jobs; the second one
        # (waiting for tomorrow) ends the test.
        slept = {"n": 0}

        def _fake_sleep(secs):
            slept["n"] += 1
            if slept["n"] > 1:
                raise _StopLoop

        monkeypatch.setattr(worker.time, "sleep", _fake_sleep)
        with pytest.raises(_StopLoop):
            worker._inventory_alert_loop()

        assert calls == ["stockout", "supplier_lead_time", "freshness"]

    def test_a_broken_stockout_digest_does_not_cancel_the_reminder(self, monkeypatch):
        """The three daily jobs are independent: the freshness reminder is the
        only one that still has something to say when the tenant's data is so
        old that the stockout digest cannot be computed at all."""
        from backend.workers import worker

        calls: list[str] = []

        def _boom():
            raise RuntimeError("stockout digest exploded")

        monkeypatch.setattr("backend.inventory.service.run_daily_inventory_alerts", _boom)
        monkeypatch.setattr(
            "backend.inventory.supplier_health_service.run_daily_supplier_lead_time_alerts",
            _boom)
        monkeypatch.setattr(
            fs, "run_daily_freshness_reminders", lambda: calls.append("freshness"))

        slept = {"n": 0}

        def _fake_sleep(secs):
            slept["n"] += 1
            if slept["n"] > 1:
                raise _StopLoop

        monkeypatch.setattr(worker.time, "sleep", _fake_sleep)
        with pytest.raises(_StopLoop):
            worker._inventory_alert_loop()

        assert calls == ["freshness"]


# ── 7. The message layer itself ──────────────────────────────────────────────

class TestReminderMessages:
    def test_email_reports_false_when_no_transport_is_configured(self, monkeypatch):
        """Same contract as every other sender: a non-delivery is never a send."""
        from backend.notifications import email as email_mod

        monkeypatch.setattr(email_mod.settings, "resend_api_key", "")
        monkeypatch.setattr(email_mod.settings, "smtp_user", "")
        monkeypatch.setattr(email_mod.settings, "smtp_pass", "")
        monkeypatch.setattr(email_mod, "_send", email_mod._transport_send)

        assert email_mod.send_data_freshness_reminder_email(
            to="a@b.co", sales_age_days=40, stock_age_days=None,
            upload_url="http://x/quick-start",
        ) is False

    def test_whatsapp_text_mentions_only_the_clock_that_triggered_it(self):
        from backend.notifications.whatsapp import build_freshness_reminder_text

        sales_only = build_freshness_reminder_text(34, None, "http://x/quick-start")
        assert "34 días" in sales_only
        assert "stock" not in sales_only.lower()

        stock_only = build_freshness_reminder_text(None, 25, "http://x/quick-start")
        assert "25 días" in stock_only
        assert "ventas" not in stock_only.lower()
