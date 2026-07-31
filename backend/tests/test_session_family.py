"""Session family: schema, planning, fan-out (multi-period Phase A)."""

from backend.db.connection import query


def _columns(table: str) -> set[str]:
    rows = query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,))
    return {r["column_name"] for r in rows}


class TestFamilySchema:
    def test_sessions_have_family_columns(self, client):
        cols = _columns("sessions")
        assert "family_id" in cols
        assert "granularity" in cols


from backend.sessions import family_service as fam


def _daily_dates(n):
    import datetime
    d0 = datetime.date(2025, 1, 1)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


class TestPlanFamily:
    def test_long_daily_data_yields_three_grains(self):
        specs = fam.plan_family(_daily_dates(900))  # ~30 months
        grains = [s["granularity"] for s in specs]
        assert grains == ["daily", "weekly", "monthly"]
        base = specs[0]
        assert base["is_base"] is True and base["target_freq"] is None
        assert base["horizon"] == 90
        monthly = specs[-1]
        assert monthly["target_freq"] == "MS" and monthly["horizon"] == 12
        assert monthly["is_base"] is False

    def test_short_daily_data_yields_only_base(self):
        specs = fam.plan_family(_daily_dates(10))
        assert [s["granularity"] for s in specs] == ["daily"]
        assert specs[0]["is_base"] is True



from backend.db.connection import execute
from backend.db import session_store
from backend.sessions import service as session_svc
from backend.utils.ids import generate_id


def _make_ready_session(tid, uid, dates):
    """A session with a small CSV dataset (date col 'fecha') and the demo
    configs, forced to MODELS_CONFIGURED — the state callers reach before
    launching training."""
    import tempfile, os, csv
    from backend.sessions.defaults import default_quickstart_configs

    fd, path = tempfile.mkstemp(suffix=".csv"); os.close(fd)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["sku", "fecha", "cantidad"])
        for i, d in enumerate(dates):
            # Varying, non-monotone quantities: the pre-training gate now runs
            # inside launch_training_family, and a flat or ever-rising column is
            # a finding of its own that has nothing to do with what these tests
            # are about (the granularity fan-out).
            w.writerow(["A", d, 5 + (i % 4)])
    ds_id = generate_id("ds")
    execute(
        """INSERT INTO datasets (id, tenant_id, name, original_filename,
             file_type, file_path, size_bytes, uploaded_by, uploaded_at)
           VALUES (%s,%s,'t','t.csv','csv',%s,%s,%s,NOW())""",
        (ds_id, tid, path, os.path.getsize(path), uid))
    s = session_svc.create_session(tid, uid, "Base")
    sid = s["id"]
    session_svc.attach_dataset(tid, sid, ds_id)
    for field, cfg in default_quickstart_configs().items():
        session_store.set_field(tid, sid, field, cfg)
    session_svc.force_status(tid, sid, "MODELS_CONFIGURED")
    return sid


class TestLaunchFamily:
    def test_long_data_launches_three_queued_siblings(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))

        result = fam.launch_training_family(tid, sid, uid)

        assert result["family_id"] == sid
        rows = query(
            "SELECT id, granularity, family_id, status FROM sessions "
            "WHERE tenant_id=%s AND family_id=%s ORDER BY granularity", (tid, sid))
        assert len(rows) == 3
        assert {r["granularity"] for r in rows} == {"daily", "weekly", "monthly"}
        assert all(r["status"] == "QUEUED" for r in rows)
        monthly = next(r for r in rows if r["granularity"] == "monthly")
        gcfg = session_store.get_field(tid, monthly["id"], "granularity_cfg")
        assert gcfg["strategy"] == "aggregate" and gcfg["target_freq"] == "MS"
        fcfg = session_store.get_field(tid, monthly["id"], "forecast_cfg")
        assert fcfg["horizon"] == 12
        base_fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert base_fcfg["horizon"] == 90

    def test_short_data_launches_only_base(self, client, test_tenant, registered_user):
        """25 days: enough to train at all (the gate's floor is 20 periods), far
        short of the 20 weekly buckets a coarser family member would need."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(25))
        result = fam.launch_training_family(tid, sid, uid)
        rows = query("SELECT granularity FROM sessions WHERE family_id=%s", (sid,))
        assert [r["granularity"] for r in rows] == ["daily"]
        assert result["base_job_id"]


class TestEntryPoints:
    def test_demo_quickstart_creates_a_family(self, client, auth_headers, test_tenant):
        r = client.post("/api/v1/demo/quickstart", headers=auth_headers)
        assert r.status_code == 202, r.text
        sid = r.json()["data"]["session_id"]
        rows = query(
            "SELECT granularity FROM sessions WHERE tenant_id=%s AND family_id=%s",
            (test_tenant["id"], sid))
        assert len(rows) >= 1
        assert all(row["granularity"] for row in rows)  # every family member tagged


# ── Quick Start plan settings (user horizon + granularity + name) ─────────────

class TestPlanFamilyUserSettings:
    def test_explicit_weekly_plans_only_weekly(self):
        specs = fam.plan_family(
            _daily_dates(900), user_granularity="weekly", user_horizon_days=56)
        assert [s["granularity"] for s in specs] == ["weekly"]
        s = specs[0]
        assert s["horizon"] == 8            # ceil(56 / 7)
        assert s["target_freq"] == "W-MON"  # daily-native data must aggregate
        assert s["is_base"] is False

    def test_auto_horizon_days_convert_and_floor_per_grain(self):
        specs = fam.plan_family(_daily_dates(900), user_horizon_days=56)
        by = {s["granularity"]: s["horizon"] for s in specs}
        # 56 days -> daily 56, weekly ceil(56/7)=8, monthly ceil(56/30)=2
        assert by == {"daily": 56, "weekly": 8, "monthly": 2}

    def test_auto_horizon_days_capped_by_generous_reach(self):
        specs = fam.plan_family(_daily_dates(900), user_horizon_days=180)
        by = {s["granularity"]: s["horizon"] for s in specs}
        # 180 days -> daily capped at 90, weekly at 26, monthly ceil(180/30)=6
        assert by == {"daily": 90, "weekly": 26, "monthly": 6}

    def test_explicit_monthly_horizon_floored_at_min_steps(self):
        specs = fam.plan_family(
            _daily_dates(900), user_granularity="monthly", user_horizon_days=28)
        assert [s["granularity"] for s in specs] == ["monthly"]
        # ceil(28/30) = 1 step is useless -> floored to 2
        assert specs[0]["horizon"] == 2

    def test_nonviable_pick_falls_back_to_auto(self):
        # 10 daily points: only the daily base is viable; monthly falls back
        specs = fam.plan_family(
            _daily_dates(10), user_granularity="monthly", user_horizon_days=28)
        assert [s["granularity"] for s in specs] == ["daily"]
        assert specs[0]["horizon"] == 28
        assert specs[0]["is_base"] is True

    def test_no_user_settings_keeps_generous_reach(self):
        specs = fam.plan_family(_daily_dates(900))
        by = {s["granularity"]: s["horizon"] for s in specs}
        assert by == {"daily": 90, "weekly": 26, "monthly": 12}


class TestLaunchFamilyUserSettings:
    def test_explicit_weekly_launches_single_session(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))

        result = fam.launch_training_family(
            tid, sid, uid, user_horizon_days=56, user_granularity="weekly")

        rows = query(
            "SELECT id, granularity, status FROM sessions "
            "WHERE tenant_id=%s AND family_id=%s", (tid, sid))
        assert len(rows) == 1 and rows[0]["id"] == sid
        assert rows[0]["granularity"] == "weekly"
        assert rows[0]["status"] == "QUEUED"
        fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert fcfg["horizon"] == 8
        assert fcfg["user_horizon_days"] == 56
        assert fcfg["user_granularity"] == "weekly"
        gcfg = session_store.get_field(tid, sid, "granularity_cfg")
        assert gcfg["strategy"] == "aggregate" and gcfg["target_freq"] == "W-MON"
        assert result["base_job_id"]

    def test_auto_with_horizon_days_sizes_every_sibling(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))

        fam.launch_training_family(tid, sid, uid, user_horizon_days=56)

        rows = query(
            "SELECT id, granularity FROM sessions WHERE tenant_id=%s AND family_id=%s",
            (tid, sid))
        horizons = {
            r["granularity"]: session_store.get_field(tid, r["id"], "forecast_cfg")["horizon"]
            for r in rows}
        assert horizons == {"daily": 56, "weekly": 8, "monthly": 2}
        base_fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert base_fcfg["user_horizon_days"] == 56
        assert "user_granularity" not in base_fcfg  # auto is not persisted
        gcfg = session_store.get_field(tid, sid, "granularity_cfg")
        assert gcfg["strategy"] == "native" and gcfg["target_freq"] is None


class TestTrainEndpointPlanSettings:
    def test_viewer_denied_and_nothing_launched(self, client, test_tenant,
                                                registered_user, viewer_headers):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))
        n_before = query("SELECT id FROM sessions WHERE tenant_id=%s", (tid,))

        r = client.post(
            f"/api/v1/sessions/{sid}/train",
            json={"user_horizon_days": 56, "user_granularity": "weekly"},
            headers=viewer_headers)

        assert r.status_code == 403
        n_after = query("SELECT id FROM sessions WHERE tenant_id=%s", (tid,))
        assert len(n_after) == len(n_before)  # no siblings created
        row = query("SELECT status, family_id FROM sessions WHERE id=%s", (sid,))[0]
        assert row["status"] == "MODELS_CONFIGURED"
        assert row["family_id"] is None
        jobs = query("SELECT id FROM jobs WHERE tenant_id=%s AND session_id=%s", (tid, sid))
        assert jobs == []

    def test_analyst_launch_persists_user_settings(self, client, test_tenant,
                                                   analyst_user, analyst_headers):
        tid, uid = test_tenant["id"], analyst_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))

        r = client.post(
            f"/api/v1/sessions/{sid}/train",
            json={"user_horizon_days": 56, "user_granularity": "weekly"},
            headers=analyst_headers)

        assert r.status_code == 202, r.text
        family = r.json()["data"]["family"]
        assert [m["granularity"] for m in family["sessions"]] == ["weekly"]
        fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert fcfg["horizon"] == 8
        assert fcfg["user_horizon_days"] == 56
        assert fcfg["user_granularity"] == "weekly"
        row = query("SELECT status, granularity FROM sessions WHERE id=%s", (sid,))[0]
        assert row["status"] == "QUEUED" and row["granularity"] == "weekly"

    def test_invalid_granularity_rejected_and_state_unchanged(self, client, test_tenant,
                                                              registered_user, auth_headers):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))

        r = client.post(
            f"/api/v1/sessions/{sid}/train",
            json={"user_granularity": "hourly"},
            headers=auth_headers)

        assert r.status_code == 422
        row = query("SELECT status, family_id FROM sessions WHERE id=%s", (sid,))[0]
        assert row["status"] == "MODELS_CONFIGURED" and row["family_id"] is None

    def test_bodyless_train_keeps_auto_behavior(self, client, test_tenant,
                                                registered_user, auth_headers):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))

        r = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)

        assert r.status_code == 202, r.text
        fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert fcfg["horizon"] == 90  # GENEROUS_REACH daily, unchanged behavior
        assert "user_horizon_days" not in fcfg and "user_granularity" not in fcfg


class TestDemoQuickstartPlanSettings:
    def test_viewer_denied_and_no_session_created(self, client, test_tenant, viewer_headers):
        tid = test_tenant["id"]
        r = client.post(
            "/api/v1/demo/quickstart",
            json={"name": "My Plan", "user_horizon_days": 56, "user_granularity": "weekly"},
            headers=viewer_headers)
        assert r.status_code == 403
        assert query("SELECT id FROM sessions WHERE tenant_id=%s", (tid,)) == []

    def test_analyst_demo_persists_name_and_settings(self, client, test_tenant,
                                                     analyst_headers):
        tid = test_tenant["id"]
        r = client.post(
            "/api/v1/demo/quickstart",
            json={"name": "My Plan", "user_horizon_days": 56, "user_granularity": "weekly"},
            headers=analyst_headers)
        assert r.status_code == 202, r.text
        sid = r.json()["data"]["session_id"]
        row = query("SELECT name FROM sessions WHERE id=%s AND tenant_id=%s", (sid, tid))[0]
        assert row["name"] == "My Plan"
        fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert fcfg["user_horizon_days"] == 56
        # Whether weekly was viable depends on the bundled CSV's span; the
        # audit key is only written when the explicit pick was accepted at all
        # (launch persists it regardless of viability):
        assert fcfg["user_granularity"] == "weekly"

    def test_demo_without_body_keeps_default_name(self, client, test_tenant, auth_headers):
        tid = test_tenant["id"]
        r = client.post("/api/v1/demo/quickstart", headers=auth_headers)
        assert r.status_code == 202, r.text
        sid = r.json()["data"]["session_id"]
        row = query("SELECT name FROM sessions WHERE id=%s AND tenant_id=%s", (sid, tid))[0]
        assert row["name"] == "Demo Faro"
