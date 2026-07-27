"""
Background-scheduler regressions in backend/workers/worker.py.

Two defects are pinned here:

1. Month-end crash of the daily loops. The next-run boundary used to be
   computed with ``next_run.replace(day=next_run.day + 1)``, which raises
   "day is out of range for month" on the 31st, on Feb 28/29 and on the last
   day of every 30-day month. The bare ``except Exception: time.sleep(3600)``
   below it swallowed the crash without logging, so on those days no stockout
   alert and no supplier lead-time alert went out, the 6:00 UTC integration
   sync never ran, and nothing said why. These tests inject a frozen "now" —
   nothing sleeps for real.

2. A scheduled retrain whose trigger keeps failing was invisible: the error
   went to the log only, and ``next_run`` stayed in the past so the scheduler
   re-attempted every 60 s forever. It is now recorded on the row
   (``last_error`` / ``last_error_at``), mirroring
   ``integration_connections.last_error``, and ``next_run`` moves forward.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.db.connection import execute, query_one
from backend.workers import worker


class _StopLoop(BaseException):
    """Escapes the infinite loops under test. A BaseException on purpose: the
    loops catch `Exception`, so a plain error would be swallowed and retried."""


def _frozen_datetime(instant: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant
    return _Frozen


def _first_sleep(monkeypatch, loop_fn, instant: datetime) -> float:
    """Run one iteration of a scheduler loop with `now` frozen at `instant`
    and return the number of seconds it decided to sleep."""
    sleeps: list[float] = []

    def _fake_sleep(secs):
        sleeps.append(secs)
        raise _StopLoop

    monkeypatch.setattr(worker, "datetime", _frozen_datetime(instant))
    monkeypatch.setattr(worker.time, "sleep", _fake_sleep)

    with pytest.raises(_StopLoop):
        loop_fn()
    return sleeps[0]


# ── Bug 1: next-run arithmetic ───────────────────────────────────────────────

class TestNextDailyRun:
    """`_next_daily_run` must land on a real calendar instant on every date."""

    @pytest.mark.parametrize("now,expected", [
        # 31st of a 31-day month — the original `replace(day=32)` crash.
        (datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc),
         datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)),
        # Feb 28 of a non-leap year — `replace(day=29)` crash.
        (datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc),
         datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)),
        # Feb 28 of a LEAP year — Feb 29 exists and must be used.
        (datetime(2028, 2, 28, 9, 0, tzinfo=timezone.utc),
         datetime(2028, 2, 29, 8, 0, tzinfo=timezone.utc)),
        # Feb 29 itself.
        (datetime(2028, 2, 29, 9, 0, tzinfo=timezone.utc),
         datetime(2028, 3, 1, 8, 0, tzinfo=timezone.utc)),
        # Last day of a 30-day month — `replace(day=31)` crash.
        (datetime(2026, 4, 30, 23, 59, tzinfo=timezone.utc),
         datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)),
        # Year boundary.
        (datetime(2026, 12, 31, 8, 0, 1, tzinfo=timezone.utc),
         datetime(2027, 1, 1, 8, 0, tzinfo=timezone.utc)),
        # Before the boundary — same day, no roll forward.
        (datetime(2026, 1, 31, 7, 59, tzinfo=timezone.utc),
         datetime(2026, 1, 31, 8, 0, tzinfo=timezone.utc)),
        # Exactly on the boundary — must move to tomorrow, never sleep 0.
        (datetime(2026, 1, 31, 8, 0, tzinfo=timezone.utc),
         datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)),
    ])
    def test_boundary_is_a_valid_instant(self, now, expected):
        assert worker._next_daily_run(now, 8) == expected

    def test_every_day_of_a_full_year_is_computable(self):
        """No date in a leap year may raise, and the result is always in the
        future — the property the original arithmetic violated 5 times a year."""
        day = datetime(2028, 1, 1, 9, 30, tzinfo=timezone.utc)
        end = datetime(2029, 1, 1, 9, 30, tzinfo=timezone.utc)
        while day < end:
            nxt = worker._next_daily_run(day, 8)
            assert nxt > day
            assert (nxt - day) <= timedelta(days=1)
            day += timedelta(days=1)


class TestDailyLoopsOnMonthEnd:
    """The loops themselves — not just the helper — must survive month end."""

    def test_inventory_alert_loop_sleeps_until_next_8am_on_the_31st(self, monkeypatch):
        instant = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
        slept = _first_sleep(monkeypatch, worker._inventory_alert_loop, instant)
        # 09:00 on Jan 31 → 08:00 on Feb 1 = 23 h. The buggy version crashed
        # and fell into the silent 1-hour retry (3600).
        assert slept == 23 * 3600

    def test_inventory_alert_loop_survives_feb_28(self, monkeypatch):
        instant = datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc)
        slept = _first_sleep(monkeypatch, worker._inventory_alert_loop, instant)
        assert slept == 20 * 3600  # 12:00 Feb 28 → 08:00 Mar 1

    def test_integration_sync_loop_sleeps_until_next_6am_on_the_31st(self, monkeypatch):
        instant = datetime(2026, 3, 31, 7, 0, tzinfo=timezone.utc)
        slept = _first_sleep(monkeypatch, worker._integration_sync_loop, instant)
        assert slept == 23 * 3600  # 07:00 Mar 31 → 06:00 Apr 1

    def test_loop_failure_is_logged_not_swallowed(self, monkeypatch, caplog):
        """The retry branch must say what it caught. It used to be a bare
        `except Exception: time.sleep(3600)` — invisible even in the log."""
        def _boom(now, hour):
            raise RuntimeError("scheduler arithmetic exploded")

        monkeypatch.setattr(worker, "_next_daily_run", _boom)
        caplog.set_level("ERROR", logger="backend.workers.worker")

        instant = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
        slept = _first_sleep(monkeypatch, worker._inventory_alert_loop, instant)

        assert slept == worker._DAILY_LOOP_RETRY_SECONDS
        assert any(
            "scheduler arithmetic exploded" in r.getMessage()
            for r in caplog.records if r.levelname == "ERROR"
        ), "the swallowed exception was never logged"


# ── Bug 2: a failing schedule must leave a durable trace ─────────────────────

@pytest.fixture
def scheduled_job(test_tenant, test_session):
    """An enabled schedule whose next_run is already due. scheduled_jobs has no
    FK to tenants, so it is cleaned up explicitly."""
    sched_id = f"sched-{uuid4().hex[:10]}"
    execute(
        """INSERT INTO scheduled_jobs (id, tenant_id, session_id, cron_expr, next_run, enabled)
           VALUES (%s, %s, %s, %s, %s, TRUE)""",
        (sched_id, test_tenant["id"], test_session["id"],
         "0 6 * * 1", datetime.now(timezone.utc) - timedelta(minutes=5)),
    )
    yield {"id": sched_id, "tenant_id": test_tenant["id"], "session_id": test_session["id"]}
    execute("DELETE FROM scheduled_jobs WHERE id = %s", (sched_id,))


def _row(sched_id: str) -> dict:
    return query_one(
        "SELECT next_run, last_run, last_error, last_error_at FROM scheduled_jobs WHERE id = %s",
        (sched_id,),
    )


class TestScheduledJobFailureIsRecorded:
    def test_failed_trigger_stores_error_and_moves_next_run_forward(
        self, monkeypatch, scheduled_job,
    ):
        def _explode(tenant_id, session_id, created_by):
            raise RuntimeError("plan quota exceeded")

        monkeypatch.setattr(worker, "create_job", _explode)
        now = datetime.now(timezone.utc)

        triggered = worker._run_due_scheduled_jobs(now)
        assert triggered == 0

        row = _row(scheduled_job["id"])
        assert "plan quota exceeded" in row["last_error"]
        assert row["last_error_at"] is not None
        # next_run advanced past `now`: the schedule no longer re-fires (and
        # re-fails) on every 60 s poll.
        assert row["next_run"] > now
        # And nothing was queued.
        queued = query_one(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE session_id = %s",
            (scheduled_job["session_id"],),
        )
        assert queued["cnt"] == 0

    def test_successful_trigger_queues_a_job_and_clears_the_error(self, scheduled_job):
        # Pre-load a stale error, as a previously failing schedule would have.
        execute(
            "UPDATE scheduled_jobs SET last_error = %s, last_error_at = NOW() WHERE id = %s",
            ("plan quota exceeded", scheduled_job["id"]),
        )
        now = datetime.now(timezone.utc)

        triggered = worker._run_due_scheduled_jobs(now)
        assert triggered == 1

        row = _row(scheduled_job["id"])
        assert row["last_error"] is None
        assert row["last_error_at"] is None
        assert row["last_run"] is not None
        assert row["next_run"] > now

        job = query_one(
            "SELECT status, created_by FROM jobs WHERE session_id = %s",
            (scheduled_job["session_id"],),
        )
        assert job is not None, "scheduler did not enqueue a training job"
        assert job["status"] == "QUEUED"
        assert job["created_by"] == "scheduler"

    def test_disabled_schedule_is_never_triggered(self, monkeypatch, scheduled_job):
        execute("UPDATE scheduled_jobs SET enabled = FALSE WHERE id = %s", (scheduled_job["id"],))

        def _explode(tenant_id, session_id, created_by):
            raise AssertionError("disabled schedule must not be triggered")

        monkeypatch.setattr(worker, "create_job", _explode)
        assert worker._run_due_scheduled_jobs(datetime.now(timezone.utc)) == 0

    def test_unparseable_cron_still_moves_next_run_forward(self, monkeypatch, scheduled_job):
        """A broken cron used to leave next_run in the past forever."""
        execute(
            "UPDATE scheduled_jobs SET cron_expr = %s WHERE id = %s",
            ("not a cron", scheduled_job["id"]),
        )
        now = datetime.now(timezone.utc)
        worker._run_due_scheduled_jobs(now)

        row = _row(scheduled_job["id"])
        assert row["next_run"] > now


class TestScheduleApiSurfacesLastError:
    def test_get_schedule_returns_the_recorded_failure(
        self, client, auth_headers, scheduled_job,
    ):
        execute(
            "UPDATE scheduled_jobs SET last_error = %s, last_error_at = NOW() WHERE id = %s",
            ("plan quota exceeded", scheduled_job["id"]),
        )
        resp = client.get(
            f"/api/v1/sessions/{scheduled_job['session_id']}/schedule", headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["last_error"] == "plan quota exceeded"
        assert data["last_error_at"] is not None

    def test_healthy_schedule_reports_no_error(self, client, auth_headers, scheduled_job):
        resp = client.get(
            f"/api/v1/sessions/{scheduled_job['session_id']}/schedule", headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["last_error"] is None

    def test_other_tenant_cannot_read_the_schedule(
        self, client, scheduled_job, make_tenant_user_headers,
    ):
        other = make_tenant_user_headers(role="analyst")
        resp = client.get(
            f"/api/v1/sessions/{scheduled_job['session_id']}/schedule", headers=other,
        )
        assert resp.status_code == 404
