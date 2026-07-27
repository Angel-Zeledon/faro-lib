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
                       "available_periods": ["daily"], "max_horizon": 90,
                       "period_source": "auto", "period_reason": "only_option",
                       "requested_period": None}

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


class TestWhyThisGrain:
    """The app has always picked the grain; it never said so.

    A tenant-wide setting that also drives the daily alert loops was offered as
    a bare dropdown in the top bar, which reads like a per-user view toggle.
    These pin the reason travelling with the answer so the UI can state it.
    """

    def test_multi_grain_family_reports_the_automatic_choice(
        self, client, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam_auto")
        got = plan.get_planning(tid)
        assert got["period"] == "daily"
        assert got["period_source"] == "auto"
        assert got["period_reason"] == "natural_frequency"

    def test_an_explicit_choice_is_reported_as_manual(
        self, client, test_tenant, registered_user,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam_manual")
        plan.set_planning(tid, "weekly", 4)
        got = plan.get_planning(tid)
        assert got["period"] == "weekly"
        assert got["period_source"] == "manual"
        assert got["period_reason"] == "manual_choice"

    def test_a_period_stored_before_source_existed_stays_honored(
        self, client, test_tenant, registered_user,
    ):
        """Legacy settings carry no `source`. Reading them as automatic would
        silently revert the grain of every tenant who had already chosen one."""
        from backend.tenants import service as tenant_svc

        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam_legacy")
        tenant_svc.update_settings(tid, {"planning": {"period": "weekly", "horizon": 4}})
        got = plan.get_planning(tid)
        assert got["period"] == "weekly"
        assert got["period_source"] == "manual"

    def test_a_chosen_grain_the_new_data_cannot_support_says_so(
        self, client, test_tenant, registered_user,
    ):
        """Their pick disappearing used to happen in silence, so the numbers
        changed unit under them with nothing to point at."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam_before")
        plan.set_planning(tid, "weekly", 4)
        _make_family(tid, uid, ["daily"], family_id="fam_after")
        # The seconds stagger inside _make_family cannot order two families,
        # and here the OLDER one has more members, so it would win on offset.
        execute("UPDATE sessions SET created_at = NOW() + interval '1 hour' "
                "WHERE tenant_id=%s AND family_id='fam_after'", (tid,))

        got = plan.get_planning(tid)
        assert got["period"] == "daily"
        assert got["period_source"] == "auto"
        assert got["period_reason"] == "chosen_grain_unavailable"
        assert got["requested_period"] == "weekly"

    def test_the_resolver_lands_on_the_same_grain_it_reports(
        self, client, test_tenant, registered_user,
    ):
        """One function decides both, so the announced grain and the session
        behind the numbers cannot drift apart."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam_agree")
        plan.set_planning(tid, "weekly", 4)

        reported = plan.get_planning(tid)["period"]
        sid = plan.resolve_active_session(tid)
        row = query_one("SELECT granularity FROM sessions WHERE id = %s", (sid,))
        assert row["granularity"] == reported == "weekly"


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


class TestResolveActiveSessionFollowsNewestTraining:
    """The Quick Start redirect lands on /hoy and lets the resolver decide which
    session the screen reads, so 'the run I just trained is the active one' is a
    property of resolve_active_session, not of the redirect."""

    def _newest(self, tid, family_id, hours=1):
        """Make one family unambiguously the newest by created_at (the seconds
        stagger inside _make_family is too small to order two families)."""
        execute(
            "UPDATE sessions SET created_at = NOW() + (%s || ' hours')::interval "
            "WHERE tenant_id=%s AND family_id=%s", (str(hours), tid, family_id))

    def test_freshly_trained_family_becomes_active(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily"], family_id="fam_old")
        _make_family(tid, uid, ["daily"], family_id="fam_new")
        self._newest(tid, "fam_new")

        sid = plan.resolve_active_session(tid)
        row = query_one("SELECT family_id FROM sessions WHERE id=%s AND tenant_id=%s",
                        (sid, tid))
        assert row["family_id"] == "fam_new", "the run just trained must be the active session"

    def test_stored_period_still_wins_when_the_new_family_offers_it(self, client, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam_old")
        plan.set_planning(tid, "weekly", 4)
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam_new")
        self._newest(tid, "fam_new")

        sid = plan.resolve_active_session(tid)
        row = query_one("SELECT family_id, granularity FROM sessions WHERE id=%s AND tenant_id=%s",
                        (sid, tid))
        assert (row["family_id"], row["granularity"]) == ("fam_new", "weekly")

    def test_period_absent_from_new_family_resolves_that_family_not_a_stale_grain(
            self, client, test_tenant, registered_user):
        """The tenant was planning monthly; the new upload only supports
        daily+weekly. get_planning coerces the period to 'daily', so the
        resolver must too — dropping through to latest-completed handed the
        screens the weekly sibling while the top bar said 'daily'."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly", "monthly"], family_id="fam_old")
        plan.set_planning(tid, "monthly", 6)
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam_new")
        self._newest(tid, "fam_new")
        # The coarser sibling finished last — that is what latest-completed
        # would have returned.
        execute("UPDATE sessions SET updated_at = NOW() + interval '2 hours' "
                "WHERE tenant_id=%s AND family_id='fam_new' AND granularity='weekly'", (tid,))

        planning = plan.get_planning(tid)
        assert planning["period"] == "daily"
        assert "monthly" not in planning["available_periods"]
        # The stored value is untouched — only the resolved view is coerced.
        assert tenant_svc.get_settings(tid)["planning"]["period"] == "monthly"

        sid = plan.resolve_active_session(tid)
        row = query_one("SELECT family_id, granularity FROM sessions WHERE id=%s AND tenant_id=%s",
                        (sid, tid))
        assert row["family_id"] == "fam_new"
        assert row["granularity"] == planning["period"]

    def test_family_less_tenant_unaffected(self, client, test_tenant, registered_user):
        """Tenants who never trained a family (pre-feature data) keep the legacy
        latest-completed behavior."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        old = session_svc.create_session(tid, uid, "legacy old")
        new = session_svc.create_session(tid, uid, "legacy new")
        execute("UPDATE sessions SET status='COMPLETED', updated_at=NOW() - interval '1 day' "
                "WHERE id=%s", (old["id"],))
        execute("UPDATE sessions SET status='COMPLETED', updated_at=NOW() WHERE id=%s",
                (new["id"],))
        assert plan.resolve_active_session(tid) == new["id"]


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


class TestActiveSessionBadgeContract:
    """The top-bar active-session badge resolves `planning.active_session_id`
    against the session list it already polls, and labels the session with its
    own granularity. Both fields must therefore stay on the wire together."""

    def test_sessions_list_exposes_granularity_of_the_resolved_session(
            self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        assert client.put("/api/v1/planning", headers=auth_headers,
                          json={"period": "weekly", "horizon": 6}).status_code == 200

        active_id = client.get(
            "/api/v1/planning", headers=auth_headers).json()["data"]["active_session_id"]
        row = query_one("SELECT granularity FROM sessions WHERE id=%s AND tenant_id=%s",
                        (active_id, tid))
        assert row["granularity"] == "weekly"

        items = client.get("/api/v1/sessions", headers=auth_headers).json()["data"]["items"]
        listed = next(s for s in items if s["session_id"] == active_id)
        assert listed["granularity"] == "weekly"
        assert listed["name"]

    def test_granularity_is_null_not_missing_for_family_less_session(
            self, client, auth_headers, test_tenant, registered_user):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        s = session_svc.create_session(tid, uid, "legacy")
        execute("UPDATE sessions SET status='COMPLETED' WHERE id=%s AND tenant_id=%s",
                (s["id"], tid))
        active_id = client.get(
            "/api/v1/planning", headers=auth_headers).json()["data"]["active_session_id"]
        assert active_id == s["id"]

        items = client.get("/api/v1/sessions", headers=auth_headers).json()["data"]["items"]
        listed = next(x for x in items if x["session_id"] == active_id)
        assert "granularity" in listed and listed["granularity"] is None


class TestAlertLoopUsesResolver:
    def test_alert_loop_resolves_active_period_session(self, client, test_tenant, registered_user, monkeypatch):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _make_family(tid, uid, ["daily", "weekly"], family_id="fam1")
        plan.set_planning(tid, "weekly", 4)

        seen = {}
        import backend.inventory.service as inv
        from backend.db import session_store as ss

        def spy(tenant_id, session_id):
            if tenant_id == tid:
                seen["sid"] = session_id
            return {}
        monkeypatch.setattr(ss, "get_forecasts", spy)

        inv.run_daily_inventory_alerts()

        weekly = query_one(
            "SELECT id FROM sessions WHERE tenant_id=%s AND family_id='fam1' "
            "AND granularity='weekly'", (tid,))
        assert seen.get("sid") == weekly["id"]
