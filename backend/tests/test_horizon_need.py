"""The forecast horizon follows the buying need (lead time + review period).

Pure tests first (no DB): the derivation and the planner. Then the launch,
which needs the database fixtures.
"""
import datetime

from backend.sessions import family_service as fam
from backend.sessions import horizon_need as hn


def _daily_dates(n):
    d0 = datetime.date(2025, 1, 1)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def _row(supplier, lead, source="user", review=0):
    return {"supplier": supplier, "lead_time_days": lead,
            "lead_time_source": source, "review_period_days": review}


class TestDeriveNeed:
    def test_nothing_declared_is_none(self):
        assert hn.derive_need({}) is None
        # Only the schema's untouched default lead time: an assumption, ignored.
        assert hn.derive_need({"A": _row("S", 15, source="default")}) is None

    def test_binding_supplier_is_the_largest_lead_plus_review(self):
        need = hn.derive_need({
            "A": _row("Fast", 10),
            "B": _row("Slow", 90, review=30),
            "C": _row("Mid", 60, review=10),
        })
        assert need["supplier"] == "Slow" and need["sku"] == "B"
        assert need["required_days"] == 120
        assert need["lead_time_days"] == 90 and need["review_period_days"] == 30
        assert need["need_days"] == 138          # ceil(120 * 1.15)
        assert need["capped"] is False

    def test_assumed_lead_time_still_counts_a_declared_review_period(self):
        need = hn.derive_need({"A": _row("S", 15, source="default", review=45)})
        assert need["required_days"] == 45 and need["lead_time_days"] == 0

    def test_learned_and_rule_sources_count(self):
        for source in ("learned", "supplier_rule", "file", "user"):
            assert hn.derive_need({"A": _row("S", 40, source=source)})["required_days"] == 40

    def test_need_beyond_the_ceiling_is_capped_and_says_so(self):
        need = hn.derive_need({"A": _row("S", 400)})
        assert need["need_days"] == hn.HORIZON_CEILING_DAYS == 365
        assert need["capped"] is True

    def test_ties_resolve_deterministically(self):
        rows = {"B": _row("Zeta", 50), "A": _row("Alpha", 50)}
        assert hn.derive_need(rows)["supplier"] == "Alpha"
        assert hn.derive_need(dict(reversed(list(rows.items()))))["supplier"] == "Alpha"

    def test_garbage_row_is_skipped_not_fatal(self):
        need = hn.derive_need({"A": {"lead_time_days": "x", "lead_time_source": "user"},
                               "B": _row("S", 20)})
        assert need["supplier"] == "S"


NEED_138 = {"need_days": 138, "required_days": 120, "capped": False,
            "lead_time_days": 90, "review_period_days": 30,
            "supplier": "Slow", "sku": "B"}


class TestPlanFamilyWithNeed:
    def test_no_need_is_exactly_the_old_behaviour(self):
        specs = fam.plan_family(_daily_dates(900), need=None)
        assert {s["granularity"]: s["horizon"] for s in specs} == {
            "daily": 90, "weekly": 26, "monthly": 12}
        assert all("horizon_extension" not in s for s in specs)

    def test_only_the_grains_that_fall_short_are_raised(self):
        specs = fam.plan_family(_daily_dates(900), need=NEED_138)
        by = {s["granularity"]: s for s in specs}
        # daily reach 90 < 138 days; weekly ceil(138/7)=20 <= 26; monthly 5 <= 12
        assert by["daily"]["horizon"] == 138
        assert by["daily"]["horizon_extension"]["configured_steps"] == 90
        assert by["weekly"]["horizon"] == 26 and "horizon_extension" not in by["weekly"]
        assert by["monthly"]["horizon"] == 12 and "horizon_extension" not in by["monthly"]

    def test_short_user_choice_is_raised_in_every_grain(self):
        specs = fam.plan_family(_daily_dates(900), user_horizon_days=28, need=NEED_138)
        by = {s["granularity"]: s["horizon"] for s in specs}
        assert by == {"daily": 138, "weekly": 20, "monthly": 5}

    def test_a_larger_choice_is_never_lowered(self):
        small_need = {**NEED_138, "need_days": 30, "required_days": 26}
        specs = fam.plan_family(_daily_dates(900), need=small_need)
        assert {s["granularity"]: s["horizon"] for s in specs} == {
            "daily": 90, "weekly": 26, "monthly": 12}
        specs = fam.plan_family(_daily_dates(900), user_granularity="weekly",
                                user_horizon_days=182, need=NEED_138)
        assert specs[0]["horizon"] == 26 and "horizon_extension" not in specs[0]

    def test_ceiling_need_in_months(self):
        capped = {**NEED_138, "need_days": 365, "capped": True}
        specs = fam.plan_family(_daily_dates(900), user_granularity="monthly", need=capped)
        assert specs[0]["horizon"] == 13          # ceil(365 / 30)
        assert specs[0]["horizon_extension"]["capped"] is True

    def test_the_sentence_names_the_supplier_and_the_arithmetic(self):
        ext = fam.plan_family(_daily_dates(900), need=NEED_138)[0]["horizon_extension"]
        text = fam.describe_extension(ext)
        assert "extended to 138 daily steps" in text
        assert "supplier Slow" in text
        assert "lead time + review = 120 days (90 + 30)" in text
        no_supplier = fam.describe_extension({**ext, "supplier": None, "sku": "B"})
        assert "SKU B" in no_supplier

    def test_backtests_opt_out(self):
        import inspect
        from backend.forecast_check import backtest
        assert "extend_for_buying_need=False" in inspect.getsource(backtest.launch_backtest)


# ── launch (database) ─────────────────────────────────────────────────────────

from backend.db import session_store
from backend.db.connection import query
from backend.tests.test_session_family import _make_ready_session


class TestLaunchExtendsHorizon:
    def test_run_records_and_states_the_extension(
            self, client, test_tenant, registered_user, monkeypatch):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))
        monkeypatch.setattr(hn, "tenant_need", lambda tenant_id: dict(NEED_138))

        result = fam.launch_training_family(tid, sid, uid)

        fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert fcfg["horizon"] == 138
        assert fcfg["horizon_extension"]["supplier"] == "Slow"
        assert fcfg["horizon_extension"]["configured_steps"] == 90
        weekly = next(r for r in query(
            "SELECT id, granularity FROM sessions WHERE tenant_id=%s AND family_id=%s",
            (tid, sid)) if r["granularity"] == "weekly")
        wcfg = session_store.get_field(tid, weekly["id"], "forecast_cfg")
        assert wcfg["horizon"] == 26 and "horizon_extension" not in wcfg
        lines = session_store.get_logs(tid, sid, result["base_job_id"])
        assert any("Horizon extended to 138 daily steps" in ln and "supplier Slow" in ln
                   for ln in lines)
        assert result["sessions"][0]["horizon_extension"]["steps"] == 138

    def test_no_declared_need_changes_nothing(
            self, client, test_tenant, registered_user, monkeypatch):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))
        monkeypatch.setattr(hn, "tenant_need", lambda tenant_id: None)
        result = fam.launch_training_family(tid, sid, uid)
        fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert fcfg["horizon"] == 90 and "horizon_extension" not in fcfg
        assert session_store.get_logs(tid, sid, result["base_job_id"]) == []

    def test_a_failing_lookup_is_logged_in_the_run_and_does_not_block_it(
            self, client, test_tenant, registered_user, monkeypatch):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))

        def boom(tenant_id):
            raise RuntimeError("db hiccup")
        monkeypatch.setattr(hn, "tenant_need", boom)
        result = fam.launch_training_family(tid, sid, uid)
        assert session_store.get_field(tid, sid, "forecast_cfg")["horizon"] == 90
        lines = session_store.get_logs(tid, sid, result["base_job_id"])
        assert any("Could not derive the horizon" in ln for ln in lines)

    def test_a_relaunch_does_not_inherit_a_stale_extension(
            self, client, test_tenant, registered_user, monkeypatch):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        sid = _make_ready_session(tid, uid, _daily_dates(900))
        session_store.set_field(tid, sid, "forecast_cfg",
                                {"horizon": 138, "horizon_extension": {"steps": 138}})
        monkeypatch.setattr(hn, "tenant_need", lambda tenant_id: None)
        fam.launch_training_family(tid, sid, uid)
        fcfg = session_store.get_field(tid, sid, "forecast_cfg")
        assert fcfg["horizon"] == 90 and "horizon_extension" not in fcfg


class TestHorizonNeedEndpoint:
    def test_preview_reports_the_extension_per_grain(
            self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(hn, "tenant_need", lambda tenant_id: dict(NEED_138))
        r = client.get("/api/v1/planning/horizon-need?horizon_days=28", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["need"]["need_days"] == 138
        assert data["by_grain"]["daily"] == {
            "configured_steps": 28, "steps": 138, "extended": True}
        assert data["ceiling_days"] == 365

    def test_preview_without_a_need_is_null(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(hn, "tenant_need", lambda tenant_id: None)
        data = client.get("/api/v1/planning/horizon-need", headers=auth_headers).json()["data"]
        assert data["need"] is None
        assert not any(g["extended"] for g in data["by_grain"].values())

    def test_a_viewer_can_read_it(self, client, viewer_headers):
        assert client.get("/api/v1/planning/horizon-need",
                          headers=viewer_headers).status_code == 200

    def test_out_of_range_horizon_is_refused(self, client, auth_headers):
        assert client.get("/api/v1/planning/horizon-need?horizon_days=366",
                          headers=auth_headers).status_code == 422
