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


import datetime

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
        for d in dates:
            w.writerow(["A", d, 5])
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
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(10))
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
