"""Multi-period Phase B: tenant settings, planning service, resolver, API."""

from backend.tenants import service as tenant_svc


class TestTenantSettings:
    def test_settings_default_empty(self, client, test_tenant):
        assert tenant_svc.get_settings(test_tenant["id"]) == {}

    def test_update_settings_merges_and_persists(self, client, test_tenant):
        tid = test_tenant["id"]
        tenant_svc.update_settings(tid, {"planning": {"period": "weekly", "horizon": 6}})
        tenant_svc.update_settings(tid, {"other": 1})
        got = tenant_svc.get_settings(tid)
        assert got["planning"] == {"period": "weekly", "horizon": 6}  # not clobbered
        assert got["other"] == 1

    def test_get_settings_unknown_tenant_is_empty(self, client):
        assert tenant_svc.get_settings("ten_does_not_exist") == {}


from backend.db.connection import execute, query_one
from backend.sessions import service as session_svc
from backend.sessions import planning_service as plan


def _make_family(tid, uid, members, family_id="fam_test", completed=True):
    """Insert sibling sessions sharing family_id, each carrying a granularity.
    `members` is a list of granularity strings; created_at is staggered so the
    LAST-inserted family reads as the newest. Returns the family_id."""
    for i, grain in enumerate(members):
        s = session_svc.create_session(tid, uid, f"{grain} member")
        status = "COMPLETED" if completed else "MODELS_CONFIGURED"
        execute(
            "UPDATE sessions SET family_id=%s, granularity=%s, status=%s, "
            "created_at = NOW() + (%s || ' seconds')::interval, updated_at = NOW() "
            "WHERE id=%s AND tenant_id=%s",
            (family_id, grain, status, str(i), s["id"], tid))
    return family_id


class TestGetPlanning:
    def test_default_is_daily_14_for_fresh_tenant(self, client, test_tenant):
        got = plan.get_planning(test_tenant["id"])
        assert got == {"period": "daily", "horizon": 14,
                       "available_periods": ["daily"], "max_horizon": 90}

    def test_available_periods_from_newest_family(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly", "monthly"], family_id="fam1")
        got = plan.get_planning(tid)
        assert got["available_periods"] == ["daily", "weekly", "monthly"]
        assert got["period"] == "daily" and got["max_horizon"] == 90

    def test_newest_family_wins(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily"], family_id="old")
        _make_family(tid, uid, ["daily", "weekly"], family_id="new")
        assert plan.get_planning(tid)["available_periods"] == ["daily", "weekly"]


class TestSetPlanning:
    def test_set_valid_period_and_horizon(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        out = plan.set_planning(tid, "weekly", 6)
        assert out["period"] == "weekly" and out["horizon"] == 6
        assert plan.get_planning(tid)["period"] == "weekly"

    def test_invalid_period_rejected(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily"], family_id="fam1")
        try:
            plan.set_planning(tid, "weekly", 4)
            assert False, "expected ValueError"
        except ValueError:
            pass
        assert plan.get_planning(tid)["period"] == "daily"

    def test_over_reach_horizon_rejected(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        try:
            plan.set_planning(tid, "weekly", 27)
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestResolveActiveSession:
    def test_resolves_period_matched_session(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        plan.set_planning(tid, "weekly", 4)
        sid = plan.resolve_active_session(tid)
        row = query_one("SELECT granularity FROM sessions WHERE id=%s", (sid,))
        assert row["granularity"] == "weekly"

    def test_falls_back_for_family_less_tenant(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, "legacy")
        execute("UPDATE sessions SET status='COMPLETED' WHERE id=%s", (s["id"],))
        assert plan.resolve_active_session(tid) == s["id"]

    def test_none_when_no_completed_session(self, client, test_tenant):
        assert plan.resolve_active_session(test_tenant["id"]) is None


class TestPlanningApi:
    def test_get_planning_default(self, client, auth_headers):
        r = client.get("/api/v1/planning", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["period"] == "daily" and data["horizon"] == 14
        assert data["available_periods"] == ["daily"]
        assert "active_session_id" in data

    def test_put_planning_admin_succeeds(self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=auth_headers,
                       json={"period": "weekly", "horizon": 6})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["period"] == "weekly"
        assert plan.get_planning(tid)["period"] == "weekly"

    def test_put_planning_analyst_forbidden(self, client, analyst_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=analyst_headers,
                       json={"period": "weekly", "horizon": 6})
        assert r.status_code == 403, r.text
        assert plan.get_planning(tid)["period"] == "daily"

    def test_put_planning_viewer_forbidden(self, client, viewer_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=viewer_headers,
                       json={"period": "weekly", "horizon": 6})
        assert r.status_code == 403, r.text
        assert plan.get_planning(tid)["period"] == "daily"

    def test_put_planning_invalid_period_422(self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=auth_headers,
                       json={"period": "weekly", "horizon": 4})
        assert r.status_code == 422, r.text

    def test_put_planning_over_reach_422(self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        r = client.put("/api/v1/planning", headers=auth_headers,
                       json={"period": "weekly", "horizon": 99})
        assert r.status_code == 422, r.text
