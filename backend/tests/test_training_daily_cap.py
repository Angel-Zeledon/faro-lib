"""The daily training ceiling (`max_trainings_per_day`).

What has to be true:

1. The count is trainings (family heads) launched today in the TENANT's day:
   siblings, back-tests, re-forecasts and jobs that never started do not count.
2. A launch past the ceiling is refused with the structured 403 and creates
   nothing; the same launch on a bigger plan goes through.
3. Two simultaneous launches cannot both take the last training of the day.
4. A scheduled retrain past the ceiling is SKIPPED with its reason, recorded in
   `schedule_runs`, and reaches the activity feed once per day.
5. GET /entitlements reports the day's usage.

`testing_mode` is on in the local .env and bypasses every ceiling, so each test
turns it off itself.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.db.connection import _json, execute, query, query_one
from backend.sessions import service as session_svc
from backend.training import daily_cap


def _set_tier(tenant_id: str, tier: str) -> None:
    execute("UPDATE tenants SET tier = %s WHERE id = %s", (tier, tenant_id))


def _set_quota(tenant_id: str, quota: dict) -> None:
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json(quota), tenant_id))


def _session(tenant_id: str, *, family_of: str | None = None, backtest=False,
             reforecast=False) -> str:
    """A session row. `family_of` makes it a sibling of that base session."""
    sid = session_svc.create_session(tenant_id, "u", f"s-{uuid4().hex[:6]}")["id"]
    execute(
        "UPDATE sessions SET family_id = %s, is_backtest = %s, is_reforecast = %s "
        "WHERE id = %s AND tenant_id = %s",
        (family_of or sid, backtest, reforecast, sid, tenant_id),
    )
    return sid


def _job(tenant_id: str, session_id: str, *, status="QUEUED", started=False,
         created_at: datetime | None = None) -> str:
    jid = f"job_{uuid4().hex[:12]}"
    execute(
        """INSERT INTO jobs (id, tenant_id, session_id, created_by, status,
                             created_at, started_at, progress)
           VALUES (%s, %s, %s, 'u', %s, %s, %s, '{}')""",
        (jid, tenant_id, session_id, status,
         created_at or datetime.now(timezone.utc),
         datetime.now(timezone.utc) if started else None),
    )
    return jid


def test_only_family_heads_of_real_trainings_count(test_tenant):
    t = test_tenant["id"]
    base = _session(t)
    _job(t, base)                                         # counts
    sibling = _session(t, family_of=base)
    _job(t, sibling)                                      # a sibling: same launch
    _job(t, _session(t, backtest=True))                   # verification run
    _job(t, _session(t, reforecast=True))                 # loads stored models
    _job(t, _session(t), status="FAILED", started=False)  # never started
    _job(t, _session(t), status="CANCELLED", started=False)
    _job(t, _session(t), status="FAILED", started=True)   # started, then failed: counts
    assert daily_cap.count_trainings_today(t) == 2


def test_yesterdays_jobs_do_not_count(test_tenant):
    t = test_tenant["id"]
    _job(t, _session(t), created_at=datetime.now(timezone.utc) - timedelta(days=2))
    assert daily_cap.count_trainings_today(t) == 0


def test_the_day_is_the_tenants_not_utc(test_tenant, monkeypatch):
    from zoneinfo import ZoneInfo
    t = test_tenant["id"]
    tz = ZoneInfo("America/Costa_Rica")            # UTC-6, no DST
    monkeypatch.setattr("backend.api.v1.timezone.zoneinfo_of", lambda _t: tz)
    now = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)   # 21:00 on Oct 5 local
    start, end = daily_cap.day_bounds_utc(t, now)
    assert start == datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc)


def test_a_free_tenant_is_refused_its_second_training_and_nothing_is_created(
    monkeypatch, test_tenant,
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    t = test_tenant["id"]
    _set_tier(t, "free")
    _job(t, _session(t))
    jobs_before = query_one("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id = %s", (t,))["c"]

    with pytest.raises(HTTPException) as exc:
        daily_cap.ensure_can_train(t)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "PLAN_LIMIT_REACHED"
    assert exc.value.detail["limit"] == "max_trainings_per_day"
    assert exc.value.detail["current"] == 1 and exc.value.detail["max"] == 1
    assert query_one("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id = %s", (t,))["c"] == jobs_before

    _set_tier(t, "paid")                                   # 10 a day
    daily_cap.ensure_can_train(t)
    _set_tier(t, "corporate")                              # unlimited
    daily_cap.ensure_can_train(t)


def test_two_simultaneous_launches_cannot_both_take_the_last_training(
    monkeypatch, test_tenant,
):
    from backend.sessions.family_service import _enqueue
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    t = test_tenant["id"]
    _set_tier(t, "free")
    _set_quota(t, {"max_trainings_per_day": 2})
    sessions = [_session(t) for _ in range(8)]
    ready = threading.Barrier(len(sessions))

    def launch(sid):
        ready.wait(timeout=30)
        try:
            _enqueue(t, sid, "u", count_as_training=True)
            return "ok"
        except HTTPException as exc:
            return exc.detail["code"]

    with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
        results = list(pool.map(launch, sessions))
    assert results.count("ok") == 2, results
    assert results.count("PLAN_LIMIT_REACHED") == 6, results
    assert query_one("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id = %s", (t,))["c"] == 2


def test_a_backtest_launch_is_exempt(monkeypatch, test_tenant):
    """The exemption lives in `launch_training_family` (`counts_as_training`);
    here the observable half: back-test jobs never add to the count."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    t = test_tenant["id"]
    _set_tier(t, "free")
    for _ in range(3):
        _job(t, _session(t, backtest=True))
    daily_cap.ensure_can_train(t)
    assert daily_cap.count_trainings_today(t) == 0


def test_a_scheduled_retrain_past_the_ceiling_is_skipped_loudly_once_a_day(
    monkeypatch, test_tenant,
):
    from backend.sessions import retrain_service as rs
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    t = test_tenant["id"]
    _set_tier(t, "free")
    _job(t, _session(t))
    template = session_svc.get_session(t, _session(t))
    sched = f"sched_{uuid4().hex[:8]}"

    cap = daily_cap.over_cap(t)
    assert cap == {"max": 1, "used": 1}
    rs._skip_for_training_cap(t, sched, template, "ds_x", None, None, cap)
    rs._skip_for_training_cap(t, sched, template, "ds_x", None, None, cap)

    runs = query("SELECT outcome, reason, reason_params FROM schedule_runs "
                 "WHERE tenant_id = %s AND schedule_id = %s", (t, sched))
    assert [(r["outcome"], r["reason"]) for r in runs] == [("skipped", "training_cap_reached")] * 2
    assert runs[0]["reason_params"] == {"max": 1, "used": 1}
    events = query("SELECT context FROM activity_logs WHERE tenant_id = %s "
                   "AND action = 'training.blocked'", (t,))
    assert len(events) == 1, "once per day per schedule, not once per due run"
    assert events[0]["context"]["reason"] == "training_cap_reached"
    # Nothing was trained or created for the skipped run.
    assert query_one("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id = %s", (t,))["c"] == 1


def test_entitlements_reports_the_days_usage(make_tenant_user_headers, client):
    headers, t = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(t, "free")
    _job(t, _session(t))
    body = client.get("/api/v1/entitlements", headers=headers).json()
    data = body.get("data", body)
    assert data["limits"]["max_trainings_per_day"] == 1
    assert data["usage"]["trainings_today"] == 1
    assert data["usage"]["max_trainings_per_day"] == 1
