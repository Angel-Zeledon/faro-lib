"""The daily operator digest (stability §14.g): a training that fails at 3 a.m.
must reach a person by lunch, and nothing else may.

Every test pins its window in 2001 on purpose. The digest reads ALL tenants,
and this database is shared with the rest of the suite (and other sessions),
so a window around "now" would pick up somebody else's failed job and make the
quiet-day assertions meaningless. Nothing else in the suite writes rows dated
2001, so inside these windows the only failures are the ones a test seeds.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.config import settings
from backend.db.connection import execute, query_one
from backend.notifications import email as email_mod
from backend.notifications import operator_digest as digest
from backend.sessions import service as session_svc
from backend.training.job_service import create_job
from backend.workers import loop_state

OPERATORS = ["ops-a@example.com", "ops-b@example.com"]


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _loop_row() -> dict | None:
    return query_one(
        "SELECT last_boundary, last_status, last_error FROM system_loop_runs "
        "WHERE loop = %s", (loop_state.OPERATOR_DIGEST,))


@pytest.fixture
def digest_state():
    """Isolate the process-wide `operator_digest` row: remove it for the test,
    put back whatever was there. Without this a real boundary from 2026 would
    make every 2001 pass look already done."""
    saved = query_one(
        "SELECT last_boundary, last_run_at, last_status, last_error "
        "FROM system_loop_runs WHERE loop = %s", (loop_state.OPERATOR_DIGEST,))
    execute("DELETE FROM system_loop_runs WHERE loop = %s", (loop_state.OPERATOR_DIGEST,))
    yield
    execute("DELETE FROM system_loop_runs WHERE loop = %s", (loop_state.OPERATOR_DIGEST,))
    if saved:
        execute(
            """INSERT INTO system_loop_runs
                   (loop, last_boundary, last_run_at, last_status, last_error)
               VALUES (%s, %s, %s, %s, %s)""",
            (loop_state.OPERATOR_DIGEST, saved["last_boundary"], saved["last_run_at"],
             saved["last_status"], saved["last_error"]),
        )


@pytest.fixture
def sent(monkeypatch, digest_state):
    """Operators configured, a transport available, and every send recorded."""
    monkeypatch.setattr(settings, "instance_admin_emails", list(OPERATORS))
    monkeypatch.setattr(email_mod, "is_configured", lambda tenant_id=None: True)
    outbox: list[dict] = []

    def _record(to, subject, html, attachment=None, tenant_id=None):
        outbox.append({"to": to, "subject": subject, "html": html, "tenant_id": tenant_id})

    monkeypatch.setattr(email_mod, "_send", _record)
    return outbox


def _failed_job(tenant_id: str, user_id: str, at: datetime, error: str) -> dict:
    name = f"digest-session-{uuid4().hex[:8]}"
    session = session_svc.create_session(tenant_id, user_id, name)
    job_id = create_job(tenant_id, session["id"], user_id)["id"]
    execute(
        "UPDATE jobs SET status = 'FAILED', error = %s, started_at = %s, "
        "completed_at = %s WHERE id = %s",
        (error, at - timedelta(minutes=5), at, job_id),
    )
    return {"job_id": job_id, "session_id": session["id"], "session_name": name}


class TestTheDigestReachesTheOperators:

    def test_failed_job_yields_one_email_per_operator_naming_tenant_and_session(
        self, sent, registered_user,
    ):
        tenant = registered_user["tenant"]
        boundary = _utc(2001, 3, 10, 12, 0)
        job = _failed_job(tenant["id"], registered_user["user"]["id"],
                          _utc(2001, 3, 10, 3, 0), "LightGBM exploded at 3am")
        # A purchase order that reached nobody: a critical tenant event.
        event_id = f"act_{uuid4().hex[:12]}"
        execute(
            """INSERT INTO activity_logs (id, tenant_id, user_id, action, resource,
                                          context, status, created_at)
               VALUES (%s, %s, 'system', 'purchase.order_not_sent', 'PO-DIGEST-1',
                       '{"severity": "critical", "reason": "no_transport_configured"}',
                       'error', %s)""",
            (event_id, tenant["id"], _utc(2001, 3, 10, 9, 0)),
        )
        try:
            result = digest.run_operator_digest(boundary)
        finally:
            execute("DELETE FROM activity_logs WHERE id = %s", (event_id,))

        assert result["status"] == loop_state.STATUS_COMPLETED
        assert result["items"] == 2
        assert sorted(m["to"] for m in sent) == sorted(OPERATORS), (
            "each operator must get exactly one copy")
        for mail in sent:
            assert tenant["name"] in mail["html"]
            assert job["session_name"] in mail["html"]
            assert "LightGBM exploded at 3am" in mail["html"]
            assert "purchase.order_not_sent" in mail["html"]
            # Platform mail: never a tenant's own sender.
            assert mail["tenant_id"] is None
        row = _loop_row()
        assert row["last_status"] == loop_state.STATUS_COMPLETED
        assert row["last_boundary"] == boundary
        assert row["last_error"] is None

    def test_implicit_operator_of_a_single_tenant_install_gets_it(
        self, sent, registered_user, monkeypatch,
    ):
        """No INSTANCE_ADMIN_EMAILS and one tenant: its admins operate the
        installation (service_config.access), so they get the digest."""
        from backend.service_config import access
        tenant = registered_user["tenant"]
        monkeypatch.setattr(settings, "instance_admin_emails", [])
        monkeypatch.setattr(access, "sole_tenant_id", lambda: tenant["id"])
        _failed_job(tenant["id"], registered_user["user"]["id"],
                    _utc(2001, 4, 10, 1, 0), "boom")

        digest.run_operator_digest(_utc(2001, 4, 10, 12, 0))

        assert [m["to"] for m in sent] == [registered_user["email"].lower()]


class TestNothingIsSentWhenNothingFailed:

    def test_quiet_day_sends_nothing_but_records_the_pass(self, sent):
        boundary = _utc(2001, 2, 1, 12, 0)
        result = digest.run_operator_digest(boundary)

        assert sent == []
        assert result["items"] == 0
        row = _loop_row()
        assert row["last_status"] == loop_state.STATUS_COMPLETED
        assert row["last_boundary"] == boundary
        assert row["last_error"] is None

    def test_failure_from_25h_ago_is_excluded_one_from_23h_ago_is_not(
        self, sent, registered_user,
    ):
        tenant_id = registered_user["tenant"]["id"]
        uid = registered_user["user"]["id"]
        boundary = _utc(2001, 5, 10, 12, 0)
        old = _failed_job(tenant_id, uid, boundary - timedelta(hours=25), "stale-failure-marker")
        fresh = _failed_job(tenant_id, uid, boundary - timedelta(hours=23), "fresh-failure-marker")

        digest.run_operator_digest(boundary)

        assert len(sent) == len(OPERATORS)
        body = sent[0]["html"]
        assert fresh["session_name"] in body
        assert old["session_name"] not in body
        assert "fresh-failure-marker" in body
        assert "stale-failure-marker" not in body

    def test_only_a_25h_old_failure_means_a_quiet_day(self, sent, registered_user):
        boundary = _utc(2001, 6, 10, 12, 0)
        _failed_job(registered_user["tenant"]["id"], registered_user["user"]["id"],
                    boundary - timedelta(hours=25), "too old")

        digest.run_operator_digest(boundary)

        assert sent == []
        assert _loop_row()["last_status"] == loop_state.STATUS_COMPLETED


class TestSkippedOutLoud:

    def test_no_operators_is_skipped_with_the_reason_recorded(
        self, sent, registered_user, monkeypatch, caplog,
    ):
        from backend.service_config import access
        monkeypatch.setattr(settings, "instance_admin_emails", [])
        monkeypatch.setattr(access, "sole_tenant_id", lambda: None)
        boundary = _utc(2001, 7, 10, 12, 0)
        _failed_job(registered_user["tenant"]["id"], registered_user["user"]["id"],
                    _utc(2001, 7, 10, 2, 0), "nobody will hear this")

        with caplog.at_level("WARNING", logger="backend.notifications.operator_digest"):
            result = digest.run_operator_digest(boundary)

        assert sent == []
        assert result["status"] == loop_state.STATUS_SKIPPED
        row = _loop_row()
        assert row["last_status"] == loop_state.STATUS_SKIPPED
        assert row["last_error"] == digest.SKIP_NO_OPERATORS
        assert row["last_boundary"] == boundary
        assert "INSTANCE_ADMIN_EMAILS" in caplog.text

    def test_no_mail_transport_is_skipped_with_the_reason_recorded(
        self, sent, monkeypatch,
    ):
        monkeypatch.setattr(email_mod, "is_configured", lambda tenant_id=None: False)
        digest.run_operator_digest(_utc(2001, 8, 10, 12, 0))

        assert sent == []
        row = _loop_row()
        assert row["last_status"] == loop_state.STATUS_SKIPPED
        assert row["last_error"] == digest.SKIP_EMAIL_NOT_CONFIGURED

    def test_a_send_that_fails_is_recorded_as_failed(self, sent, registered_user,
                                                      monkeypatch):
        def _boom(*a, **k):
            raise email_mod.EmailDeliveryError("provider said no")
        monkeypatch.setattr(email_mod, "_send", _boom)
        _failed_job(registered_user["tenant"]["id"], registered_user["user"]["id"],
                    _utc(2001, 9, 10, 2, 0), "x")

        digest.run_operator_digest(_utc(2001, 9, 10, 12, 0))

        row = _loop_row()
        assert row["last_status"] == loop_state.STATUS_FAILED
        assert row["last_error"] == f"send_failed:{len(OPERATORS)}/{len(OPERATORS)}"


class TestOncePerDay:

    def test_second_run_for_the_same_boundary_does_not_resend(self, sent, registered_user):
        boundary = _utc(2001, 10, 10, 12, 0)
        _failed_job(registered_user["tenant"]["id"], registered_user["user"]["id"],
                    _utc(2001, 10, 10, 4, 0), "once is enough")

        first = digest.run_operator_digest(boundary)
        assert len(sent) == len(OPERATORS)
        second = digest.run_operator_digest(boundary)

        assert second["status"] == "already_done"
        assert len(sent) == len(OPERATORS), "a restart re-sent the same day's digest"
        assert first["status"] == loop_state.STATUS_COMPLETED
        assert _loop_row()["last_boundary"] == boundary


class _StopLoop(BaseException):
    """Escapes the infinite loop; BaseException so the loop's `except Exception`
    does not swallow it (same convention as test_worker_schedulers.py)."""


class TestTheLoop:

    def test_catch_up_pass_runs_the_digest_and_records_the_boundary(
        self, sent, registered_user, monkeypatch,
    ):
        """A restart at 12:30 with yesterday's pass recorded: the loop must run
        today's 12:00 digest before it ever sleeps."""
        from backend.workers import worker

        loop_state.mark_run(loop_state.OPERATOR_DIGEST, _utc(2001, 11, 9, 12, 0))
        _failed_job(registered_user["tenant"]["id"], registered_user["user"]["id"],
                    _utc(2001, 11, 10, 3, 0), "caught up")
        now = _utc(2001, 11, 10, 12, 30)

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(worker, "datetime", _Frozen)

        def _sleep(_secs):
            raise _StopLoop

        monkeypatch.setattr(worker.time, "sleep", _sleep)
        with pytest.raises(_StopLoop):
            worker._operator_digest_loop()

        assert len(sent) == len(OPERATORS)
        row = _loop_row()
        assert row["last_boundary"] == _utc(2001, 11, 10, 12, 0)
        assert row["last_status"] == loop_state.STATUS_COMPLETED

    def test_health_lists_the_operator_digest_loop(self, client, digest_state):
        loops = {entry["loop"] for entry in client.get("/health").json()["loops"]}
        assert loop_state.OPERATOR_DIGEST in loops
