"""Per-period coverage/semáforo + horizon windowing (multi-period Phase C)."""


from backend.formatting import format_coverage, format_days
from backend.inventory.service import (
    _DAYS_PER_PERIOD,
    _days_per_period,
    _lead_time_in_periods,
    _steps_for_lead_time,
    generate_recommendations,
)


class TestRecommendationUnitLabels:
    """Polish: /hoy recommendation text must label per-period coverage in the
    active period's unit. A weekly session reports coverage_days IN WEEKS; the
    generator used to print "32 días" for 32 weeks and even mixed units when
    computing the overstock excess ("-12 días más de lo óptimo"). Lead time is a
    real calendar duration and stays in days."""

    def _items(self):
        return [
            {"sku": "YA", "display_name": "Aceite", "signal": "PEDIR_YA",
             "coverage_days": 0.3, "lead_time_days": 10, "recommended_qty": 264,
             "supplier": "Andina", "abc": "A", "inventory_value": 500},
            {"sku": "OVER", "display_name": "Azucar", "signal": "SOBRESTOCK",
             "coverage_days": 32.1, "lead_time_days": 7, "recommended_qty": 0,
             "supplier": "Valle", "abc": "A", "inventory_value": 21600},
        ]

    def _text(self, recs, sku, rec_type):
        return next(r["text"] for r in recs if r["sku"] == sku and r["rec_type"] == rec_type)

    def test_weekly_coverage_labeled_in_weeks_not_days(self):
        recs = generate_recommendations(self._items(), period="weekly")
        ya = self._text(recs, "YA", "STOCKOUT_RISK")
        over = self._text(recs, "OVER", "OVERSTOCK")
        # Coverage reads in weeks; the supplier lead time stays in days.
        assert "0 semanas de stock" in ya
        assert "tarda 10 días en entregar" in ya
        # 32.1 weeks coverage — NOT "32 días".
        assert "32 semanas de cobertura" in over
        assert "días de cobertura" not in over
        # Excess vs the 3×lead ceiling is computed in the SAME unit (weeks):
        # 32.1 − (7/7)*3 = 29.1 → "29 semanas", never a mixed-unit "11 días".
        assert "29 semanas más de lo óptimo" in over

    def test_daily_output_is_byte_identical_to_default(self):
        items = self._items()
        assert generate_recommendations(items, period="daily") == generate_recommendations(items)
        over = self._text(generate_recommendations(items), "OVER", "OVERSTOCK")
        # Daily keeps the "días" wording exactly as before.
        assert "32 días de cobertura" in over

    def test_format_coverage_daily_matches_format_days(self):
        for n in (0, 1, 2, 5.4, 32.1, 144):
            assert format_coverage(n, "daily") == format_days(n)
        assert format_coverage(1, "weekly") == "1 semana"
        assert format_coverage(3, "weekly") == "3 semanas"
        assert format_coverage(1, "monthly") == "1 mes"
        assert format_coverage(4, "monthly") == "4 meses"


class TestPeriodMathHelpers:
    def test_days_per_period_map(self):
        assert _DAYS_PER_PERIOD == {"daily": 1, "weekly": 7, "monthly": 30}
        assert _days_per_period("daily") == 1
        assert _days_per_period("weekly") == 7
        assert _days_per_period("monthly") == 30
        # Unknown/legacy period degrades to daily (1) — never raises.
        assert _days_per_period("fortnightly") == 1
        assert _days_per_period(None) == 1

    def test_lead_time_in_periods(self):
        assert _lead_time_in_periods(14, "weekly") == 2.0
        assert _lead_time_in_periods(15, "weekly") == 15 / 7
        assert _lead_time_in_periods(30, "monthly") == 1.0
        assert _lead_time_in_periods(15, "daily") == 15.0

    def test_steps_for_lead_time_rounds_up_min_one(self):
        assert _steps_for_lead_time(15, "weekly") == 3     # ceil(15/7)
        assert _steps_for_lead_time(14, "weekly") == 2     # ceil(14/7)
        assert _steps_for_lead_time(5, "weekly") == 1      # ceil(5/7) -> 1 floor
        assert _steps_for_lead_time(45, "monthly") == 2    # ceil(45/30)
        assert _steps_for_lead_time(15, "daily") == 15


from backend.db import session_store
from backend.inventory import service as svc
from backend.sessions.service import create_session


def _forecast(per_bucket_demand: float, spread: float, n: int = 30) -> dict:
    """One model, n buckets of constant demand. In a period-trained session each
    bucket is one PERIOD, so per_bucket_demand is units/period for that grain."""
    pts = [
        {
            "date": f"2026-01-{i + 1:02d}",
            "value": per_bucket_demand,
            "lower": max(0.0, per_bucket_demand - spread),
            "upper": per_bucket_demand + spread,
        }
        for i in range(n)
    ]
    return {"lightgbm": {"forecast": pts}}


def _put_stock(client, headers, sku, **fields):
    r = client.put(f"/api/v1/inventory/stock/{sku}", json=fields, headers=headers)
    assert r.status_code == 200, r.text


class TestWeeklyCoverage:
    def test_weekly_coverage_reads_in_weeks_and_signal_matches_hand_calc(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "weekly-cov")["id"]
        sku = "WK_COV"
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        items = svc.get_inventory_status(tid, sid, period="weekly")
        it = next(i for i in items if i["sku"] == sku)
        assert it["coverage_days"] == 4.0          # value is in WEEKS now
        assert it["daily_demand"] == 10.0          # per-period (weekly) demand
        assert it["signal"] == "OK"

    def test_switching_period_flips_the_same_sku_coverage(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "flip")["id"]
        sku = "FLIP"
        _put_stock(client, auth_headers, sku, current_stock=20, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        daily = next(i for i in svc.get_inventory_status(tid, sid, period="daily")
                     if i["sku"] == sku)
        weekly = next(i for i in svc.get_inventory_status(tid, sid, period="weekly")
                      if i["sku"] == sku)
        assert daily["signal"] == "PEDIR_YA"
        assert weekly["signal"] == "PEDIR_PRONTO"
        assert daily["coverage_days"] == 2.0 and weekly["coverage_days"] == 2.0


class TestDailyRegression:
    def test_period_daily_is_byte_identical_to_default(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "daily-regression")["id"]
        _put_stock(client, auth_headers, "R_SHORT", current_stock=1, lead_time_days=10, moq=1)
        _put_stock(client, auth_headers, "R_OK", current_stock=130, lead_time_days=10, moq=1)
        _put_stock(client, auth_headers, "R_PILE", current_stock=9999, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {
            "R_SHORT": _forecast(100.0, 10.0, n=14),
            "R_OK":    _forecast(10.0, 6.0, n=14),
            "R_PILE":  _forecast(1.0, 0.1, n=14),
        })

        default_items = svc.get_inventory_status(tid, sid)
        daily_items = svc.get_inventory_status(tid, sid, period="daily")
        assert default_items == daily_items

    def test_hand_computed_daily_values_unchanged(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "daily-hand")["id"]
        _put_stock(client, auth_headers, "HAND", current_stock=40, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {"HAND": _forecast(10.0, 0.0, n=14)})
        it = next(i for i in svc.get_inventory_status(tid, sid) if i["sku"] == "HAND")
        assert it["coverage_days"] == 4.0
        assert it["daily_demand"] == 10.0
        assert it["signal"] == "PEDIR_YA"


class TestByWarehousePeriod:
    def test_weekly_by_warehouse_coverage_in_weeks(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "wh-weekly")["id"]
        sku = "WHWK"
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=14,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0)})

        rows = svc.get_inventory_status_by_warehouse(tid, sid, period="weekly")
        row = next(r for r in rows if r["sku"] == sku)
        assert row["coverage_days"] == 4.0     # weeks
        assert row["signal"] == "OK"

    def test_by_warehouse_daily_default_unchanged(
        self, client, auth_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "wh-daily")["id"]
        sku = "WHDL"
        _put_stock(client, auth_headers, sku, current_stock=40, lead_time_days=10,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {sku: _forecast(10.0, 0.0, n=14)})
        default = next(r for r in svc.get_inventory_status_by_warehouse(tid, sid)
                       if r["sku"] == sku)
        explicit = next(r for r in svc.get_inventory_status_by_warehouse(tid, sid, period="daily")
                        if r["sku"] == sku)
        assert default == explicit
        assert default["signal"] == "PEDIR_YA"


class TestStatusEndpointPeriod:
    def test_status_envelope_carries_active_period(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "ep-weekly")["id"]
        _put_stock(client, auth_headers, "EP", current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {"EP": _forecast(10.0, 0.0)})

        monkeypatch.setattr(
            inv_api.planning_service, "get_planning",
            lambda t: {"period": "weekly", "horizon": 4,
                       "available_periods": ["daily", "weekly"], "max_horizon": 26})

        r = client.get(f"/api/v1/inventory/status?session_id={sid}", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["period"] == "weekly"
        assert data["coverage_unit"] == "week"
        it = next(i for i in data["items"] if i["sku"] == "EP")
        assert it["coverage_days"] == 4.0   # 4 weeks
        assert it["signal"] == "OK"

    def test_status_defaults_session_to_active_resolver(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "ep-default")["id"]
        _put_stock(client, auth_headers, "EPD", current_stock=1, lead_time_days=10, moq=1)
        session_store.set_forecasts(tid, sid, {"EPD": _forecast(100.0, 0.0, n=14)})
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session",
                            lambda t: sid)
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "daily", "horizon": 14,
                                       "available_periods": ["daily"], "max_horizon": 90})
        r = client.get("/api/v1/inventory/status", headers=auth_headers)
        assert r.status_code == 200, r.text
        skus = {i["sku"] for i in r.json()["data"]["items"]}
        assert "EPD" in skus

    def test_status_no_session_and_no_active_returns_400(
        self, client, auth_headers, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session",
                            lambda t: None)
        r = client.get("/api/v1/inventory/status", headers=auth_headers)
        assert r.status_code == 400


class TestOptimizerHorizonConversion:
    def test_endpoint_converts_active_horizon_to_days(
        self, client, auth_headers, test_tenant, test_session, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        captured = {}

        def _fake_build(tenant_id, session_id, horizon_days, stock_rows=None, period="daily"):
            captured["horizon_days"] = horizon_days
            captured["period"] = period
            return None

        monkeypatch.setattr(inv_api.opt_svc, "build_optimization_input", _fake_build)
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "monthly", "horizon": 4,
                                       "available_periods": ["daily", "monthly"],
                                       "max_horizon": 12})
        r = client.get(f"/api/v1/inventory/optimize?session_id={test_session['id']}",
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        assert captured["horizon_days"] == 120
        assert captured["period"] == "monthly"

    def test_endpoint_accepts_horizon_beyond_old_cap(
        self, client, auth_headers, test_session, monkeypatch
    ):
        import backend.api.v1.inventory as inv_api
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "monthly", "horizon": 12,
                                       "available_periods": ["monthly"], "max_horizon": 12})
        r = client.get(f"/api/v1/inventory/optimize?session_id={test_session['id']}",
                       headers=auth_headers)
        assert r.status_code == 200, r.text


class TestOptimizerLeadTimeBuckets:
    def test_lead_time_periodized(self, client, auth_headers, test_tenant):
        from backend.inventory import optimizer_service as opt
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "opt-lead")["id"]
        _put_stock(client, auth_headers, "OPTL", current_stock=5, lead_time_days=30,
                   moq=1, warehouse="principal")
        session_store.set_forecasts(tid, sid, {"OPTL": _forecast(10.0, 0.0)})
        inp = opt.build_optimization_input(tid, sid, horizon_days=4, period="monthly")
        assert inp is not None
        assert inp.lead_time_buckets["OPTL"] == 1
        inp_d = opt.build_optimization_input(tid, sid, horizon_days=30)
        assert inp_d.lead_time_buckets["OPTL"] == 30


class TestBriefingPeriodAware:
    def test_briefing_uses_active_period_not_daily(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        """QA Bug 2: /hoy morning-briefing must read the session in the active
        period. A weekly session's per-period demand read as daily flags
        everything PEDIR_YA; period-aware, it agrees with /inventory."""
        import backend.api.v1.inventory as inv_api
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "brief-weekly")["id"]
        # 10 units/week, 40 stock, lead 14d -> 4 weeks coverage vs 2-week lead
        # -> OK weekly. Read as DAILY it would be 4 "days" vs 14 -> PEDIR_YA.
        _put_stock(client, auth_headers, "BRF", current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {"BRF": _forecast(10.0, 0.0)})
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session",
                            lambda t: sid)
        monkeypatch.setattr(inv_api.planning_service, "get_planning",
                            lambda t: {"period": "weekly", "horizon": 4,
                                       "available_periods": ["daily", "weekly"],
                                       "max_horizon": 26})
        r = client.get("/api/v1/inventory/morning-briefing", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        # Weekly-aware: BRF is OK, not a risk -> risks empty of BRF.
        risk_skus = {i["sku"] for i in data["risks"]}
        assert "BRF" not in risk_skus


class TestNarrativeKpiConsistency:
    """Polish #4: the /hoy executive summary (AI narrative) and the KPI tile must
    agree in EVERY planning period. Both read get_morning_briefing; the narrative
    endpoint previously omitted the active period and defaulted to daily, so a
    weekly session's per-period demand was misread as daily and the narrative
    claimed immediate-risk products the KPI tile ('Riesgo hoy 0') denied — a
    self-contradiction on the same screen."""

    def _immediate_risk_from_narrative(self, narrative: dict) -> int:
        """The narrative's own count of immediate-risk products, read from the
        structured key_points the endpoint returns. Both the fallback builder and
        the LLM path derive these from the same briefing kpis['order_now']."""
        import re
        for kp in narrative.get("key_points", []):
            m = re.match(r"\s*(\d+)\s+producto", kp)
            if m and "riesgo inmediato" in kp:
                return int(m.group(1))
        return 0

    def _force_rule_based_fallback(self, monkeypatch):
        """Pin the deterministic, network-free rule-based narrative so the test
        asserts on stable output (populated key_points + prose) and never waits on
        the forced-Ollama client's timeout. The period-resolution under test is
        upstream of the LLM, so the fallback exercises it identically."""
        import backend.ai.narrative_service as ns
        monkeypatch.setattr(ns, "_get_client", lambda: None)

    def test_weekly_narrative_agrees_with_kpi_tile(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        import backend.sessions.planning_service as ps
        self._force_rule_based_fallback(monkeypatch)
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "narr-weekly")["id"]
        # 10 units/week, 40 stock, lead 14d -> 4 weeks coverage vs 2-week lead
        # -> OK weekly (order_now=0). Read as DAILY it is 4 "days" vs 14 ->
        # PEDIR_YA (order_now>=1) — the exact mismatch the bug produced.
        _put_stock(client, auth_headers, "BRF", current_stock=40, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {"BRF": _forecast(10.0, 0.0)})
        monkeypatch.setattr(ps, "get_planning",
                            lambda t: {"period": "weekly", "horizon": 4,
                                       "available_periods": ["daily", "weekly"],
                                       "max_horizon": 26})

        brief = client.get(f"/api/v1/inventory/morning-briefing?session_id={sid}",
                           headers=auth_headers)
        assert brief.status_code == 200, brief.text
        kpi_order_now = brief.json()["data"]["kpis"]["order_now"]
        assert kpi_order_now == 0   # weekly-aware: BRF is OK

        narr = client.post("/api/v1/ai/narrative/morning",
                           json={"session_id": sid, "profile": "distributor"},
                           headers=auth_headers)
        assert narr.status_code == 200, narr.text
        n = narr.json()["data"]
        # The summary must reflect the SAME immediate-risk count as the KPI tile.
        assert self._immediate_risk_from_narrative(n) == kpi_order_now
        assert n["urgency"] == "ok"   # not 'critical'
        # The prose must not claim immediate-risk products the KPI denies.
        assert "riesgo inmediato" not in n["narrative"]

    def test_daily_narrative_agrees_with_kpi_tile(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        """Same consistency must hold in daily mode (where it already worked):
        when the KPI counts a risk, the narrative counts the same one."""
        import backend.sessions.planning_service as ps
        self._force_rule_based_fallback(monkeypatch)
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "narr-daily")["id"]
        _put_stock(client, auth_headers, "DRF", current_stock=4, lead_time_days=14, moq=1)
        session_store.set_forecasts(tid, sid, {"DRF": _forecast(10.0, 0.0, n=14)})
        monkeypatch.setattr(ps, "get_planning",
                            lambda t: {"period": "daily", "horizon": 14,
                                       "available_periods": ["daily"], "max_horizon": 90})

        brief = client.get(f"/api/v1/inventory/morning-briefing?session_id={sid}",
                           headers=auth_headers)
        assert brief.status_code == 200, brief.text
        kpi_order_now = brief.json()["data"]["kpis"]["order_now"]
        assert kpi_order_now == 1   # daily: DRF is PEDIR_YA

        narr = client.post("/api/v1/ai/narrative/morning",
                           json={"session_id": sid, "profile": "distributor"},
                           headers=auth_headers)
        assert narr.status_code == 200, narr.text
        n = narr.json()["data"]
        assert self._immediate_risk_from_narrative(n) == kpi_order_now
        assert n["urgency"] == "critical"
        assert "riesgo inmediato" in n["narrative"]
