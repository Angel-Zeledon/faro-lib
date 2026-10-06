"""Scheduled management reports: the Python half (worker pass, builder, mail).

The routes are Rust (`backend-rs/src/routes/scheduled_reports.rs`, tested by
`tests/contract/scheduled_reports_contract.py`); everything here reads its
claims back from the database. The only network seams are the mail transport
(`email._send`, replaced) and the clock (`now` is an argument of the pass).
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.activity.events import EVENTS, REASONS
from backend.db.connection import execute, query, query_one
from backend.notifications import email as email_mod
from backend.notifications import outbox
from backend.scheduled_reports import builder, catalog, internal_api, render, service, tokens
from backend.scheduled_reports.schedule_math import (
    advance, cron_for, local_key, next_run_after, window_bounds_utc, window_for)
from backend.users import service as user_svc

UTC = timezone.utc


def u(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# ── Pure: when a report is due ───────────────────────────────────────────────
# The same vectors are pinned in the Rust unit tests (routes/schedule next_run
# for report schedules); a drift on either side turns one of them red.

NEXT_RUN_VECTORS = [
    # (cron, zone, after, expected next firing, UTC)
    ("0 6 * * 1", "America/Costa_Rica", "2026-10-06T00:00:00", "2026-10-12T12:00:00"),
    ("0 8 15 * *", "America/Bogota", "2026-10-15T13:00:00", "2026-11-15T13:00:00"),
    # Spring forward: 02:00 on Sunday 2026-03-29 does not exist in Madrid.
    ("0 2 * * 0", "Europe/Madrid", "2026-03-23T00:00:00", "2026-03-29T01:00:00"),
    # Fall back: 02:00 on 2026-10-25 happens twice; the first is 00:00 UTC.
    ("0 2 * * 0", "Europe/Madrid", "2026-10-20T00:00:00", "2026-10-25T00:00:00"),
    ("0 2 * * 0", "America/New_York", "2026-03-02T00:00:00", "2026-03-08T07:00:00"),
    ("0 1 * * 0", "America/New_York", "2026-10-26T00:00:00", "2026-11-01T05:00:00"),
    ("0 0 28 * *", "America/Santiago", "2026-08-01T00:00:00", "2026-08-28T04:00:00"),
]


class TestScheduleMath:
    @pytest.mark.parametrize("cron,zone,after,expected", NEXT_RUN_VECTORS)
    def test_next_run_vectors(self, cron, zone, after, expected):
        assert next_run_after(cron, zone, u(after)) == u(expected)

    def test_cron_for_weekly_uses_iso_weekday(self):
        assert cron_for("weekly", 1, None, 6) == "0 6 * * 1"
        assert cron_for("weekly", 7, None, 6) == "0 6 * * 0"   # Sunday is 0 in cron
        assert cron_for("monthly", None, 15, 8) == "0 8 15 * *"

    @pytest.mark.parametrize("args", [("weekly", None, None, 6), ("weekly", 8, None, 6),
                                      ("monthly", None, 29, 6), ("daily", 1, None, 6)])
    def test_cron_for_refuses_what_it_cannot_express(self, args):
        with pytest.raises(ValueError):
            cron_for(*args)

    def test_repeated_hour_is_one_period(self):
        # croniter fires at BOTH instants of 02:00 on 2026-10-25 in Madrid; the
        # local key is identical, which is what the UNIQUE run key catches.
        first, second = u("2026-10-25T00:00:00"), u("2026-10-25T01:00:00")
        assert local_key(first, "Europe/Madrid") == local_key(second, "Europe/Madrid") == "2026-10-25T02:00"
        # ...and advance() steps over the repeat to the next real week.
        assert advance("0 2 * * 0", "Europe/Madrid", first, first) == u("2026-11-01T01:00:00")

    def test_new_york_repeated_hour_is_one_period(self):
        first, second = u("2026-11-01T05:00:00"), u("2026-11-01T06:00:00")
        assert local_key(first, "America/New_York") == local_key(second, "America/New_York")
        assert advance("0 1 * * 0", "America/New_York", first, first) == u("2026-11-08T06:00:00")

    def test_advance_never_schedules_the_past(self):
        due = u("2026-10-05T12:00:00")
        now = due + timedelta(days=20)
        nxt = advance("0 6 * * 1", "America/Costa_Rica", due, now)
        assert nxt > now

    def test_windows(self):
        assert window_for("weekly", date(2026, 10, 5)) == (date(2026, 9, 28), date(2026, 10, 4))
        assert window_for("monthly", date(2026, 3, 15)) == (date(2026, 2, 15), date(2026, 3, 14))
        assert window_for("monthly", date(2026, 1, 1)) == (date(2025, 12, 1), date(2025, 12, 31))

    def test_window_bounds_follow_the_tenant_zone_across_dst(self):
        # Madrid week that contains the spring-forward day is 167 hours long.
        lo, hi = window_bounds_utc(date(2026, 3, 23), date(2026, 3, 29), "Europe/Madrid")
        assert (hi - lo) == timedelta(hours=167)


class TestUnsubscribeToken:
    SECRET = "test-secret"
    RID = "11111111-2222-3333-4444-555555555555"
    # Pinned: the Rust verifier tests the same string.
    PINNED = RID + ".P6npnbIEQgFwI0JuS9STwjLzGFfrxpufdgYihtYiXfg"

    def test_pinned_vector(self):
        assert tokens.mint(self.SECRET, self.RID) == self.PINNED

    def test_roundtrip_and_tamper(self):
        assert tokens.verify(self.SECRET, self.PINNED) == self.RID
        assert tokens.verify("another-secret", self.PINNED) is None
        assert tokens.verify(self.SECRET, self.PINNED[:-1] + "A") is None
        assert tokens.verify(self.SECRET, "22222222." + self.PINNED.split(".")[1]) is None
        assert tokens.verify(self.SECRET, "") is None
        assert tokens.verify(self.SECRET, "nodot") is None


class TestInternalRenderAuth:
    def test_signature_window_and_tamper(self):
        body = b'{"a":1}'
        now = 1_800_000_000.0
        sig = internal_api.sign("k", str(now), body)
        assert internal_api._verified("k", str(now), sig, body, now=now + 59)
        assert not internal_api._verified("k", str(now), sig, body, now=now + 61)
        assert not internal_api._verified("k", str(now), sig, body + b" ", now=now)
        assert not internal_api._verified("other", str(now), sig, body, now=now)
        assert not internal_api._verified("k", None, sig, body, now=now)
        assert not internal_api._verified("k", "abc", sig, body, now=now)

    def test_the_signature_is_pinned_for_the_rust_client(self):
        # The same string is asserted by backend-rs/.../pyclient.rs.
        assert internal_api.sign("k", "1800000000.0", b'{"a":1}') == "263db5cf08e56b77b22456e01e8198f84221272de31275eb590d58838b5c34b4"

    def test_endpoint_is_closed_without_the_signature(self, client):
        r = client.post("/internal/scheduled-reports/render", json={"tenant_id": "x"})
        assert r.status_code == 401

    def test_endpoint_renders_for_a_signed_request(self, client, test_tenant):
        import time
        from backend.config import settings
        body = json.dumps({"tenant_id": test_tenant["id"], "sections": ["purchasing_summary"],
                           "frequency": "weekly", "schedule_name": "Weekly"}).encode()
        ts = str(time.time())
        r = client.post("/internal/scheduled-reports/render", content=body, headers={
            "X-Internal-Timestamp": ts,
            "X-Internal-Signature": internal_api.sign(settings.secret_key, ts, body),
            "Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["report"]["sections"][0]["code"] == "purchasing_summary"
        assert "Resumen de compras" in data["html"]

    def test_endpoint_refuses_an_unknown_section(self, client, test_tenant):
        import time
        from backend.config import settings
        body = json.dumps({"tenant_id": test_tenant["id"], "sections": ["salaries"],
                           "frequency": "weekly"}).encode()
        ts = str(time.time())
        r = client.post("/internal/scheduled-reports/render", content=body, headers={
            "X-Internal-Timestamp": ts,
            "X-Internal-Signature": internal_api.sign(settings.secret_key, ts, body)})
        assert r.status_code == 422


class TestVocabulary:
    def test_events_and_reasons_are_declared(self):
        for action in ("queued", "failed", "auto_paused", "skipped", "unsubscribed"):
            assert f"scheduled_report.{action}" in EVENTS
        for reason in ("report_build_failed", "report_failed_repeatedly", "report_missed_window",
                       "report_no_recipients"):
            assert reason in REASONS

    def test_outbox_knows_the_kind(self):
        assert outbox.check_params("email", "scheduled_report",
                                   {"run_id": "r", "recipient_id": "x"}) is None
        assert outbox.check_params("email", "scheduled_report", {"run_id": "r"}) == "missing_param:recipient_id"

    def test_health_lists_the_loop(self, client):
        loops = {e["loop"] for e in client.get("/health").json()["loops"]}
        assert "scheduled_reports" in loops

    def test_the_worker_runs_the_loop(self, monkeypatch):
        from backend.config import settings
        from backend.workers import worker
        monkeypatch.setattr(settings, "worker_enabled", False)
        monkeypatch.setattr(settings, "scheduler_enabled", True)
        assert "report-scheduler" in worker.enabled_components()


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def box(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(email_mod, "_send",
                        lambda to, subject, html, attachment=None, tenant_id=None: sent.append((to, subject, html)))
    monkeypatch.setattr(email_mod, "is_configured", lambda tenant_id=None: True)
    return sent


def _user(tenant, role="analyst", **fields):
    email = f"{role}-{uuid4().hex[:8]}@example.com"
    user = user_svc.create_user(tenant_id=tenant["id"], email=email, password="TestPass123!",
                                role=role, full_name="Person")
    user_svc.mark_verified(tenant["id"], user["id"])
    for column, value in fields.items():
        execute(f"UPDATE users SET {column} = %s WHERE id = %s", (value, user["id"]))
    return {**user, "email": email}


def _schedule(tenant, *, frequency="weekly", tz="America/Costa_Rica", next_run=None, sections=None,
              recipients=(), externals=(), name="Weekly management", tz_of_tenant=None):
    """A schedule row due at `next_run`, with its recipients."""
    if tz_of_tenant:
        from backend.tenants.service import update_settings
        update_settings(tenant["id"], {"timezone": tz_of_tenant})
    cron = cron_for(frequency, 1 if frequency == "weekly" else None,
                    15 if frequency == "monthly" else None, 6)
    sid = str(uuid4())
    execute(
        """INSERT INTO report_schedules (id, tenant_id, name, sections, frequency, weekday, day_of_month,
                  hour, cron_expr, anchored_tz, next_run_at, created_by)
           VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, 6, %s, %s, %s, 'test')""",
        (sid, tenant["id"], name, json.dumps(sections or ["purchasing_summary"]), frequency,
         1 if frequency == "weekly" else None, 15 if frequency == "monthly" else None,
         cron, tz, next_run or u("2026-10-12T12:00:00")))
    rids = {}
    for user in recipients:
        rids[user["id"]] = query_one(
            "INSERT INTO report_schedule_recipients (schedule_id, tenant_id, kind, user_id) "
            "VALUES (%s, %s, 'user', %s) RETURNING id", (sid, tenant["id"], user["id"]))["id"]
    for address in externals:
        rids[address] = query_one(
            "INSERT INTO report_schedule_recipients (schedule_id, tenant_id, kind, email) "
            "VALUES (%s, %s, 'external', %s) RETURNING id", (sid, tenant["id"], address))["id"]
    return sid, rids


def _allow(tenant, address):
    execute("INSERT INTO report_external_allowlist (tenant_id, email, added_by) VALUES (%s, %s, 'test')",
            (tenant["id"], address))


def _schedule_row(sid):
    return query_one("SELECT * FROM report_schedules WHERE id = %s", (sid,))


def _runs(sid):
    return query("SELECT * FROM report_schedule_runs WHERE schedule_id = %s ORDER BY started_at", (sid,))


def _mail_rows(tenant):
    return query("SELECT * FROM outbound_messages WHERE tenant_id = %s AND kind = 'scheduled_report' "
                 "ORDER BY created_at", (tenant["id"],))


DUE = u("2026-10-12T12:00:00")


# ── The worker pass ──────────────────────────────────────────────────────────

class TestWorkerPass:
    def test_a_due_schedule_runs_once_and_queues_one_mail_per_recipient(self, box, test_tenant):
        a, b = _user(test_tenant), _user(test_tenant, "viewer")
        sid, rids = _schedule(test_tenant, recipients=[a, b])
        assert service.process_due(DUE + timedelta(seconds=30)) == 1
        runs = _runs(sid)
        assert len(runs) == 1 and runs[0]["status"] == "queued" and runs[0]["recipients_queued"] == 2
        mails = _mail_rows(test_tenant)
        assert sorted(m["recipient"] for m in mails) == sorted([a["email"], b["email"]])
        assert {m["dedupe_key"] for m in mails} == {f"report:{runs[0]['id']}:{r}" for r in rids.values()}
        row = _schedule_row(sid)
        assert row["last_status"] == "queued" and row["consecutive_failures"] == 0
        assert row["next_run_at"] == u("2026-10-19T12:00:00")
        assert runs[0]["snapshot"]["sections"][0]["code"] == "purchasing_summary"

    def test_nothing_is_due_nothing_happens(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        assert service.process_due(DUE - timedelta(minutes=1)) == 0
        assert _runs(sid) == [] and _mail_rows(test_tenant) == []

    def test_a_paused_schedule_is_never_claimed(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        execute("UPDATE report_schedules SET enabled = FALSE, paused_reason = 'user' WHERE id = %s", (sid,))
        assert service.process_due(DUE + timedelta(seconds=30)) == 0
        assert _runs(sid) == []

    def test_double_send_guard_two_workers_one_period(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant), _user(test_tenant)])
        now = DUE + timedelta(seconds=30)
        results: list[int] = []
        threads = [threading.Thread(target=lambda: results.append(service.process_due(now))) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(results) == 1
        assert len(_runs(sid)) == 1
        assert len(_mail_rows(test_tenant)) == 2

    def test_double_send_guard_restart_after_the_advance_was_lost(self, box, test_tenant):
        # Worst case of a restart: the period is owned (run row exists) but
        # `next_run_at` points at it again. The run key refuses it.
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        service.process_due(DUE + timedelta(seconds=30))
        execute("UPDATE report_schedules SET next_run_at = %s WHERE id = %s", (DUE, sid))
        assert service.process_due(DUE + timedelta(minutes=2)) == 0
        assert len(_runs(sid)) == 1 and len(_mail_rows(test_tenant)) == 1
        assert _schedule_row(sid)["next_run_at"] == u("2026-10-19T12:00:00")  # and it moved on

    def test_double_send_guard_repeated_hour_when_clocks_go_back(self, box, test_tenant):
        user = _user(test_tenant)
        first = u("2026-10-25T00:00:00")          # 02:00 CEST
        sid, _ = _schedule(test_tenant, tz="Europe/Madrid", tz_of_tenant="Europe/Madrid", next_run=first,
                           recipients=[user])
        execute("UPDATE report_schedules SET cron_expr = '0 2 * * 0' WHERE id = %s", (sid,))
        service.process_due(first + timedelta(seconds=30))
        assert len(_runs(sid)) == 1
        # What croniter would have produced without the guard: the SAME local
        # 02:00 an hour later (02:00 CET). Force it and run the pass again.
        execute("UPDATE report_schedules SET next_run_at = %s WHERE id = %s", (u("2026-10-25T01:00:00"), sid))
        assert service.process_due(u("2026-10-25T01:00:30")) == 0
        assert len(_runs(sid)) == 1 and len(_mail_rows(test_tenant)) == 1

    def test_spring_forward_sends_once_at_the_shifted_hour(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, tz="Europe/Madrid", tz_of_tenant="Europe/Madrid",
                           next_run=u("2026-03-29T01:00:00"), recipients=[_user(test_tenant)])
        execute("UPDATE report_schedules SET cron_expr = '0 2 * * 0' WHERE id = %s", (sid,))
        assert service.process_due(u("2026-03-29T01:00:30")) == 1
        assert len(_runs(sid)) == 1
        assert _schedule_row(sid)["next_run_at"] == u("2026-04-05T00:00:00")   # 02:00 CEST again

    def test_a_worker_that_was_down_too_long_skips_instead_of_sending_stale(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        assert service.process_due(DUE + timedelta(days=2)) == 0
        runs = _runs(sid)
        assert [r["status"] for r in runs] == ["skipped"] and runs[0]["error"] == "missed_beyond_catchup_window"
        assert _mail_rows(test_tenant) == []
        assert _schedule_row(sid)["next_run_at"] > DUE + timedelta(days=2)
        ev = query_one("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = 'scheduled_report.skipped'",
                       (test_tenant["id"],))
        assert ev and ev["context"]["reason"] == "report_missed_window"

    def test_a_tenant_that_changed_zone_is_re_anchored_not_fired_at_the_old_hour(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, tz="America/New_York", recipients=[_user(test_tenant)])
        # The tenant's zone is the default (Costa Rica), not the anchored one.
        assert service.process_due(DUE + timedelta(seconds=30)) == 0
        row = _schedule_row(sid)
        assert row["anchored_tz"] == "America/Costa_Rica" and _runs(sid) == []
        assert row["next_run_at"] == u("2026-10-19T12:00:00")

    def test_a_stale_building_run_is_re_driven_without_a_second_mail(self, box, test_tenant):
        user = _user(test_tenant)
        sid, rids = _schedule(test_tenant, recipients=[user])
        service.process_due(DUE + timedelta(seconds=30))
        run = _runs(sid)[0]
        # The worker "died" after queueing but before saying so.
        execute("UPDATE report_schedule_runs SET status = 'building', started_at = %s WHERE id = %s",
                (DUE - timedelta(hours=1), run["id"]))
        service.process_due(DUE + timedelta(minutes=5))
        after = _runs(sid)[0]
        assert after["status"] == "queued" and after["attempts"] == 2
        assert len(_mail_rows(test_tenant)) == 1

    def test_a_run_that_keeps_dying_fails_visibly(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        service.process_due(DUE + timedelta(seconds=30))
        run = _runs(sid)[0]
        execute("UPDATE report_schedule_runs SET status = 'building', attempts = %s, started_at = %s WHERE id = %s",
                (catalog.MAX_RUN_ATTEMPTS, DUE - timedelta(hours=1), run["id"]))
        service.process_due(DUE + timedelta(minutes=5))
        after = _runs(sid)[0]
        assert after["status"] == "failed" and after["error"] == "interrupted"
        assert _schedule_row(sid)["consecutive_failures"] == 1


# ── Who gets it ──────────────────────────────────────────────────────────────

class TestRecipients:
    def test_a_deactivated_recipient_gets_nothing_and_the_reason_is_recorded(self, box, test_tenant):
        gone = _user(test_tenant, status="suspended")
        kept = _user(test_tenant)
        sid, _ = _schedule(test_tenant, recipients=[gone, kept])
        service.process_due(DUE + timedelta(seconds=30))
        assert [m["recipient"] for m in _mail_rows(test_tenant)] == [kept["email"]]
        run = _runs(sid)[0]
        assert run["recipients_skipped"] == [{"recipient": gone["id"], "kind": "user", "reason": "user_inactive"}]

    def test_a_removed_user_is_skipped(self, box, test_tenant):
        gone = _user(test_tenant)
        sid, _ = _schedule(test_tenant, recipients=[gone, _user(test_tenant)])
        execute("DELETE FROM users WHERE id = %s", (gone["id"],))
        service.process_due(DUE + timedelta(seconds=30))
        assert len(_mail_rows(test_tenant)) == 1
        assert _runs(sid)[0]["recipients_skipped"][0]["reason"] == "user_removed"

    def test_a_warehouse_limited_user_does_not_get_a_company_wide_report(self, box, test_tenant):
        scoped = _user(test_tenant)
        execute("UPDATE users SET warehouse_scope = '[]'::jsonb WHERE id = %s", (scoped["id"],))
        sid, _ = _schedule(test_tenant, recipients=[scoped, _user(test_tenant)])
        service.process_due(DUE + timedelta(seconds=30))
        assert len(_mail_rows(test_tenant)) == 1
        assert _runs(sid)[0]["recipients_skipped"][0]["reason"] == "user_warehouse_scoped"

    def test_an_external_address_needs_the_admin_allow_list_at_send_time(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)],
                           externals=["board@outside.example", "former@outside.example"])
        _allow(test_tenant, "board@outside.example")          # the other was removed from the list
        service.process_due(DUE + timedelta(seconds=30))
        recipients = [m["recipient"] for m in _mail_rows(test_tenant)]
        assert "board@outside.example" in recipients and "former@outside.example" not in recipients
        assert len(recipients) == 2
        skipped = _runs(sid)[0]["recipients_skipped"]
        assert skipped == [{"recipient": "former@outside.example", "kind": "external",
                            "reason": "external_not_allowed"}]

    def test_an_unsubscribed_recipient_is_skipped(self, box, test_tenant):
        a, b = _user(test_tenant), _user(test_tenant)
        sid, rids = _schedule(test_tenant, recipients=[a, b])
        execute("UPDATE report_schedule_recipients SET unsubscribed_at = NOW() WHERE id = %s", (rids[a["id"]],))
        service.process_due(DUE + timedelta(seconds=30))
        assert [m["recipient"] for m in _mail_rows(test_tenant)] == [b["email"]]

    def test_nobody_left_pauses_the_schedule_and_says_so(self, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant, status="suspended")])
        service.process_due(DUE + timedelta(seconds=30))
        run = _runs(sid)[0]
        assert run["status"] == "skipped" and run["error"] == "no_recipients"
        row = _schedule_row(sid)
        assert row["enabled"] is False and row["paused_reason"] == "no_recipients"
        ev = query_one("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = 'scheduled_report.auto_paused'",
                       (test_tenant["id"],))
        assert ev["context"]["reason"] == "report_no_recipients" and ev["context"]["severity"] == "warning"
        assert _mail_rows(test_tenant) == []

    def test_cross_tenant_isolation(self, box, test_tenant):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            outsider = _user(other)
            mine = _user(test_tenant)
            # A recipient row that names ANOTHER tenant's user and ANOTHER
            # tenant's allow-listed address must resolve to nothing.
            sid, _ = _schedule(test_tenant, recipients=[mine, outsider], externals=["leak@outside.example"])
            _allow(other, "leak@outside.example")
            service.process_due(DUE + timedelta(seconds=30))
            assert [m["recipient"] for m in _mail_rows(test_tenant)] == [mine["email"]]
            reasons = {s["reason"] for s in _runs(sid)[0]["recipients_skipped"]}
            assert reasons == {"user_removed", "external_not_allowed"}
            assert _mail_rows(other) == []
            # A message cannot be rendered through another tenant's id either.
            run_id = _runs(sid)[0]["id"]
            rid = query_one("SELECT id FROM report_schedule_recipients WHERE schedule_id = %s AND user_id = %s",
                            (sid, mine["id"]))["id"]
            with pytest.raises(service.DeliveryRefused) as exc:
                service.render_for_delivery(other["id"], run_id, rid)
            assert exc.value.reason == "run_missing"
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


# ── Failure is visible and bounded ───────────────────────────────────────────

class TestFailures:
    def test_no_transport_is_a_failed_run_not_a_queued_one(self, monkeypatch, test_tenant):
        monkeypatch.setattr(email_mod, "is_configured", lambda tenant_id=None: False)
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        service.process_due(DUE + timedelta(seconds=30))
        run = _runs(sid)[0]
        assert run["status"] == "failed" and run["error"] == "not_configured"
        assert _mail_rows(test_tenant) == []
        assert _schedule_row(sid)["last_status"] == "failed"
        ev = query_one("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = 'scheduled_report.failed'",
                       (test_tenant["id"],))
        assert ev["context"]["reason"] == "no_transport_configured"

    def test_three_failures_in_a_row_pause_the_schedule(self, monkeypatch, test_tenant):
        monkeypatch.setattr(email_mod, "is_configured", lambda tenant_id=None: False)
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        for week in range(3):
            execute("UPDATE report_schedules SET next_run_at = %s WHERE id = %s",
                    (DUE + timedelta(weeks=week), sid))
            service.process_due(DUE + timedelta(weeks=week, seconds=30))
        row = _schedule_row(sid)
        assert len(_runs(sid)) == 3 and row["consecutive_failures"] == 3
        assert row["enabled"] is False and row["paused_reason"] == "failures"
        ev = query_one("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = 'scheduled_report.auto_paused'",
                       (test_tenant["id"],))
        assert ev["context"]["failures"] == 3 and ev["context"]["reason"] == "report_failed_repeatedly"

    def test_a_success_resets_the_failure_count(self, monkeypatch, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        execute("UPDATE report_schedules SET consecutive_failures = 2 WHERE id = %s", (sid,))
        service.process_due(DUE + timedelta(seconds=30))
        assert _schedule_row(sid)["consecutive_failures"] == 0

    def test_a_crashing_builder_is_a_failed_run_with_the_class_not_a_traceback(self, monkeypatch, box, test_tenant):
        sid, _ = _schedule(test_tenant, recipients=[_user(test_tenant)])
        monkeypatch.setattr(builder, "build_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        service.process_due(DUE + timedelta(seconds=30))
        run = _runs(sid)[0]
        assert run["status"] == "failed" and run["error"] == "RuntimeError"


# ── The content: not available instead of invented ───────────────────────────

class TestBuilder:
    def _report(self, tenant, sections=None, frequency="weekly", now=None):
        return builder.build_report(tenant["id"], sections or list(catalog.SECTIONS), frequency,
                                    now or u("2026-10-12T12:00:00"))

    def test_a_tenant_with_no_data_says_not_available_where_it_cannot_know(self, test_tenant):
        sections = {s["code"]: s for s in self._report(test_tenant)["sections"]}
        assert sections["budget_vs_spend"] == {"code": "budget_vs_spend", "available": False,
                                               "reason": "no_running_budget"}
        assert sections["supplier_scorecard"]["available"] is False
        assert sections["supplier_scorecard"]["reason"] == "no_receptions_recorded"
        # Counting nothing is a real zero, and the value of nothing is not a number.
        p = sections["purchasing_summary"]
        assert (p["generated"], p["sent"], p["received"]) == (0, 0, 0) and p["generated_value"] is None
        c = sections["committed_demand"]
        assert c["total"] == 0 and c["verdict"] == {"available": True, "at_risk": 0, "covered": 0, "no_verdict": 0}

    def test_purchasing_counts_come_from_the_order_log_inside_the_window(self, test_tenant):
        t = test_tenant["id"]
        def po(generated, **cols):
            keys = ["tenant_id", "session_id", "generated_at"] + list(cols)
            vals = [t, "s", generated] + list(cols.values())
            execute(f"INSERT INTO inventory_po_log ({', '.join(keys)}) VALUES ({', '.join(['%s'] * len(keys))})", vals)
        # Window (weekly, produced 2026-10-12 local): 2026-10-05 .. 2026-10-11.
        po(u("2026-10-06T15:00:00"), total_value=100.0, sent_at=u("2026-10-07T15:00:00"))
        po(u("2026-10-07T15:00:00"), total_value=None, sent_at=u("2026-10-08T15:00:00"),
           reception_status="received", received_at=u("2026-10-10T15:00:00"))
        po(u("2026-10-08T15:00:00"), total_value=50.0, cancelled_at=u("2026-10-09T15:00:00"))
        po(u("2026-09-01T15:00:00"), total_value=999.0)       # outside the window
        p = self._report(test_tenant, ["purchasing_summary"])["sections"][0]
        assert p["generated"] == 2 and p["generated_then_cancelled"] == 1
        assert p["sent"] == 2 and p["received"] == 1
        # One of the two live orders has no value: the total is a floor and says so.
        assert p["generated_value"] == 100.0 and p["orders_without_value"] == 1

    def test_budget_figures_and_the_unknown_cost_flag(self, test_tenant):
        t = test_tenant["id"]
        execute(
            """INSERT INTO purchase_budgets (id, tenant_id, root_id, revision, period_type, period_start,
                      period_end, amount, currency, scope_type, created_by)
               VALUES ('b1', %s, 'r1', 1, 'month', '2026-10-01', '2026-10-31', 1000, 'USD', 'company', 'x')""", (t,))
        b = self._report(test_tenant, ["budget_vs_spend"])["sections"][0]
        assert b["available"] and len(b["budgets"]) == 1
        row = b["budgets"][0]
        assert (row["amount"], row["spent"], row["committed"], row["remaining"]) == (1000.0, 0.0, 0.0, 1000.0)
        assert row["currency"] == "USD" and row["unknown_cost_lines"] == 0

    def test_commitment_counts_and_a_verdict_that_could_not_be_computed(self, monkeypatch, test_tenant):
        t = test_tenant["id"]
        for status in ("open", "open", "fulfilled", "cancelled"):
            execute("""INSERT INTO committed_demand (tenant_id, sku, delivery_date, quantity, status, created_by)
                       VALUES (%s, 'A', '2026-12-01', 5, %s, 'x')""", (t, status))
        from backend.inventory import committed_demand_service as svc
        monkeypatch.setattr(svc, "annotate_risk", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hub down")))
        c = self._report(test_tenant, ["committed_demand"])["sections"][0]
        assert c["by_status"] == {"open": 2, "fulfilled": 1, "cancelled": 1}
        assert c["verdict"] == {"available": False, "reason": "verdict_unavailable"}

    def test_verdict_counts_split_at_risk_covered_and_no_verdict(self, monkeypatch, test_tenant):
        t = test_tenant["id"]
        for sku in ("A", "B", "C"):
            execute("""INSERT INTO committed_demand (tenant_id, sku, delivery_date, quantity, created_by)
                       VALUES (%s, %s, '2026-12-01', 5, 'x')""", (t, sku))
        from backend.inventory import committed_demand_service as svc
        verdict = {"A": True, "B": False, "C": None}

        def fake(tenant_id, items, **_):
            for i in items:
                i["at_risk"] = verdict[i["sku"]]
            return items
        monkeypatch.setattr(svc, "annotate_risk", fake)
        v = self._report(test_tenant, ["committed_demand"])["sections"][0]["verdict"]
        assert v == {"available": True, "at_risk": 1, "covered": 1, "no_verdict": 1}

    def test_supplier_without_a_declared_lead_time_is_not_graded(self, test_tenant):
        t = test_tenant["id"]
        sid = str(uuid4())
        execute("INSERT INTO inventory_po_log (id, tenant_id, session_id) VALUES (%s, %s, 's')", (sid, t))
        execute("INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days) "
                "VALUES (%s, 'Acme', %s, 9)", (t, sid))
        s = self._report(test_tenant, ["supplier_scorecard"])["sections"][0]
        assert s["available"] and s["suppliers"][0]["supplier"] == "Acme"
        assert s["suppliers"][0]["on_time_rate"] is None            # nobody declared a lead time
        assert s["suppliers_without_declared_lead_time"] == 1

    def test_a_section_that_crashes_is_reported_unavailable_not_dropped(self, monkeypatch, test_tenant):
        monkeypatch.setattr(builder, "supplier_scorecard", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        out = self._report(test_tenant, ["supplier_scorecard", "purchasing_summary"])["sections"]
        assert [s["code"] for s in out] == ["supplier_scorecard", "purchasing_summary"]
        assert out[0] == {"code": "supplier_scorecard", "available": False, "reason": "build_failed"}
        assert out[1]["available"] is True


# ── The mail ─────────────────────────────────────────────────────────────────

class TestDelivery:
    def _queued(self, test_tenant, **kw):
        user = _user(test_tenant)
        sid, rids = _schedule(test_tenant, recipients=[user], **kw)
        service.process_due(DUE + timedelta(seconds=30))
        return user, sid, rids[user["id"]], _runs(sid)[0]

    def test_the_drain_sends_the_report_with_a_personal_unsubscribe_link(self, box, test_tenant):
        user, sid, rid, run = self._queued(test_tenant)
        row = _mail_rows(test_tenant)[0]
        outbox.process_due()
        assert query_one("SELECT status FROM outbound_messages WHERE id = %s", (row["id"],))["status"] == "sent"
        to, subject, html = box[-1]
        assert to == user["email"] and "Weekly management" in subject
        from backend.config import settings
        assert f"/reportes-programados/baja?token={tokens.mint(settings.secret_key, rid)}" in html
        assert "Resumen de compras" in html

    def test_a_recipient_deactivated_after_queueing_is_refused_at_delivery(self, box, test_tenant):
        user, sid, rid, run = self._queued(test_tenant)
        execute("UPDATE users SET status = 'suspended' WHERE id = %s", (user["id"],))
        row = _mail_rows(test_tenant)[0]
        outbox.process_due()
        done = query_one("SELECT status, last_error FROM outbound_messages WHERE id = %s", (row["id"],))
        assert (done["status"], done["last_error"]) == ("failed", "recipient_not_eligible")
        assert box == []

    def test_a_schedule_paused_after_queueing_sends_nothing(self, box, test_tenant):
        user, sid, rid, run = self._queued(test_tenant)
        execute("UPDATE report_schedules SET enabled = FALSE, paused_reason = 'user' WHERE id = %s", (sid,))
        outbox.process_due()
        assert query_one("SELECT last_error FROM outbound_messages WHERE tenant_id = %s", (test_tenant["id"],))[
            "last_error"] == "schedule_paused"
        assert box == []

    def test_an_unsubscribe_after_queueing_sends_nothing(self, box, test_tenant):
        user, sid, rid, run = self._queued(test_tenant)
        execute("UPDATE report_schedule_recipients SET unsubscribed_at = NOW() WHERE id = %s", (rid,))
        outbox.process_due()
        assert box == []

    def test_the_html_prints_not_available_never_a_zero_for_unknown(self, test_tenant):
        report = builder.build_report(test_tenant["id"], list(catalog.SECTIONS), "weekly", DUE)
        html = render.render_sections_html(report)
        assert "No disponible: no hay un presupuesto vigente hoy." in html
        assert "No disponible: todavía no hay recepciones registradas." in html
