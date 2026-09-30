"""
Safety stock: measured cumulative quantile first, classical formula as fallback.

The quantity a reorder point is exposed to is the SUM of demand over the lead
time. `z * sigma * sqrt(L)` approximates that sum's quantile under assumptions
retail demand does not satisfy — Gaussian, symmetric, independent across
buckets. When the engine has measured the cumulative error directly, no
approximation is needed.

The second half of this file pins the arithmetic that decides how much capital
a tenant ties up. A silent factor of 1.28 there is invisible in every screen the
product has.
"""

import math
import uuid
from datetime import datetime, timedelta

import pytest

from backend.db.connection import execute
from backend.inventory.service import (
    MIN_LEAD_TIME_OBSERVATIONS,
    _Q90_Z, _calc_recommended, _measured_safety_stock, _point_sigma,
    _resolve_lead_time_std, _safety_stock, best_model_by_sku,
    compute_session_accuracy, get_learned_lead_time_stds,
)


def _risk(offsets: dict, model: str = "global_lgbm") -> dict:
    return {"model": model, "quantiles": [0.5, 0.9, 0.95],
            "cumulative_offsets": offsets}


class TestMeasuredSafetyStock:

    def test_uses_the_band_for_the_matching_lead_time(self):
        risk = _risk({"7": {"0.95": 40.0}, "14": {"0.95": 90.0}})
        assert _measured_safety_stock(risk, lead_time=14, service_level=0.95) == 90.0

    def test_picks_the_quantile_nearest_the_service_level(self):
        risk = _risk({"7": {"0.5": 0.0, "0.9": 30.0, "0.95": 40.0}})
        assert _measured_safety_stock(risk, 7, 0.9) == 30.0
        assert _measured_safety_stock(risk, 7, 0.5) == 0.0

    def test_a_fractional_lead_time_rounds_up_to_a_whole_bucket(self):
        """2.14 weeks of lead time must be covered by 3 buckets, not 2."""
        risk = _risk({"2": {"0.95": 20.0}, "3": {"0.95": 33.0}})
        assert _measured_safety_stock(risk, 2.14, 0.95) == 33.0

    def test_lead_time_beyond_the_backtest_falls_back_to_the_longest_measured(self):
        """Extrapolating a band nobody verified would be a confident invention."""
        risk = _risk({"7": {"0.95": 40.0}, "14": {"0.95": 90.0}})
        assert _measured_safety_stock(risk, 60, 0.95) == 90.0

    def test_absent_evidence_returns_none_rather_than_zero(self):
        """None means 'fall back'; 0.0 would mean 'no cushion needed'."""
        assert _measured_safety_stock(None, 7, 0.95) is None
        assert _measured_safety_stock({}, 7, 0.95) is None
        assert _measured_safety_stock(_risk({}), 7, 0.95) is None

    def test_never_negative(self):
        risk = _risk({"7": {"0.95": -12.0}})
        assert _measured_safety_stock(risk, 7, 0.95) == 0.0


class TestFallback:

    def test_without_a_measurement_the_classical_formula_is_used(self):
        # z(0.95) = 1.645: a service level is a ONE-sided probability, so the
        # 1.96 of a two-sided 95% interval would be the wrong constant here.
        got = _safety_stock(avg_std=10.0, lead_time=9, service_level=0.95, risk=None)
        assert got == pytest.approx(1.645 * 10.0 * 3.0, rel=1e-6)

    def test_the_measurement_wins_when_present(self):
        risk = _risk({"9": {"0.95": 7.0}})
        assert _safety_stock(10.0, 9, 0.95, risk) == 7.0

    def test_measured_band_splits_across_warehouses(self):
        """A whole-SKU band allocated to a warehouse holding a third of demand."""
        risk = _risk({"7": {"0.95": 90.0}})
        assert _safety_stock(10.0, 7, 0.95, risk, risk_scale=1 / 3) == pytest.approx(30.0)

    def test_recommendation_uses_the_same_number(self):
        """The order quantity and the reorder point must not disagree."""
        risk = _risk({"7": {"0.95": 50.0}})
        qty = _calc_recommended(current_stock=0.0, avg_daily=10.0, avg_std=999.0,
                                lead_time=7, moq=0, service_level=0.95, risk=risk)
        assert qty == pytest.approx(10.0 * 7 + 50.0)

    def test_the_moq_floor_does_not_move_a_need_above_it(self):
        """
        `moq` is a MINIMUM, not a case size. This used to assert 72.0 — 70 units
        of real need rounded up to the next multiple of 24 — which is the
        arithmetic for "the supplier ships in cases", a thing this product has
        no field for and never asked the buyer about.
        """
        qty = _calc_recommended(0.0, 10.0, 0.0, 7, moq=24, service_level=0.95,
                                risk=_risk({"7": {"0.95": 0.0}}))
        assert qty == 70.0        # already above the 24-unit minimum


class TestPointSigmaIsASigma:
    """`upper` is the top of a band, not a standard deviation."""

    def test_q90_branch_recovers_sigma(self):
        sigma = _point_sigma({"value": 100.0, "q90": 100.0 + _Q90_Z * 8.0})
        assert sigma == pytest.approx(8.0, rel=1e-6)

    def test_upper_branch_recovers_the_same_sigma(self):
        """
        The legacy path used to return the raw spread, which is ~1.28 sigma, and
        the caller multiplied by z again. A configured 95% service level was
        really served at about 98% — more capital tied up than anyone asked for,
        with nothing on screen saying so.
        """
        sigma = _point_sigma({"value": 100.0, "upper": 100.0 + _Q90_Z * 8.0})
        assert sigma == pytest.approx(8.0, rel=1e-6)

    def test_both_branches_agree(self):
        band = 100.0 + _Q90_Z * 5.0
        assert _point_sigma({"value": 100.0, "q90": band}) == pytest.approx(
            _point_sigma({"value": 100.0, "upper": band})
        )

    def test_effective_service_level_is_the_configured_one(self):
        """End to end: a 95% setting produces a 1.645-sigma cushion.

        Before the fix this path returned the raw band spread as sigma, so the
        cushion came out at 1.2816 x 1.645 = 2.11 sigma — the ~98% coverage
        nobody configured.
        """
        sigma = 8.0
        point = {"value": 100.0, "upper": 100.0 + _Q90_Z * sigma}
        cushion = _safety_stock(_point_sigma(point), lead_time=1, service_level=0.95)
        assert cushion == pytest.approx(1.645 * sigma, rel=1e-6)
        assert cushion < 2.0 * sigma, "the double-z inflation is back"

    def test_missing_band_yields_no_sigma(self):
        assert _point_sigma({"value": 100.0}) == 0.0


class TestChampionByCost:

    def test_ranks_by_asymmetric_cost_when_available(self):
        rows = [
            {"sku": "A", "model": "timid", "type": "ml", "wape": 0.10, "cost": 9.0},
            {"sku": "A", "model": "generous", "type": "ml", "wape": 0.14, "cost": 4.0},
        ]
        assert best_model_by_sku(rows)["A"] == "generous"

    def test_falls_back_to_wape_for_older_results(self):
        rows = [
            {"sku": "A", "model": "m1", "type": "ml", "wape": 0.20},
            {"sku": "A", "model": "m2", "type": "ml", "wape": 0.05},
        ]
        assert best_model_by_sku(rows)["A"] == "m2"

    def test_baselines_never_win(self):
        rows = [
            {"sku": "A", "model": "naive", "type": "baseline", "cost": 0.01},
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 5.0},
        ]
        assert best_model_by_sku(rows)["A"] == "lightgbm"

    def test_accuracy_reports_the_selected_model_not_the_best_one(self):
        """
        Selection is by cost, so the chosen model need not have the best WAPE.
        Reporting the best WAPE would advertise a forecast nobody is buying from.
        """
        rows = [
            {"sku": "A", "model": "timid", "type": "ml", "wape": 0.10, "cost": 9.0},
            {"sku": "A", "model": "generous", "type": "ml", "wape": 0.20, "cost": 4.0},
        ]
        items = [{"sku": "A", "daily_demand": 10.0}]
        accuracy = compute_session_accuracy(rows, items)
        assert accuracy == pytest.approx(0.80, rel=1e-6), (
            "accuracy must describe 'generous' (the model selected), not 'timid'"
        )

    def test_the_global_model_competes_on_the_same_table(self):
        rows = [
            {"sku": "A", "model": "lightgbm", "type": "ml", "cost": 8.0},
            {"sku": "A", "model": "global_lgbm", "type": "global", "cost": 3.0},
        ]
        assert best_model_by_sku(rows)["A"] == "global_lgbm"


# ─────────────────────────────────────────────────────────────────────────────
# stability.md 17(a) — the ignored `lead_time_std`.
#
# `_safety_stock` used to be `z * avg_std * sqrt(L)`: demand uncertainty over a
# lead time treated as a FIXED number, even though the product asks the buyer
# for that lead time's own variability and stores it on every supplier. The
# textbook quantity is the standard deviation of demand over a VARIABLE lead
# time:
#
#     sigma_LT = sqrt(L * sigma_d^2 + d^2 * sigma_L^2)
#     safety_stock = z * sigma_LT
#
# which only reduces to the old formula when sigma_L (`lead_time_std`) is 0.
# Every test below would fail against the pre-fix `_safety_stock`, which took
# no `avg_daily`/`lead_time_std` keywords at all (a `TypeError`, not a wrong
# number) — the strongest possible "must fail on the old behaviour".
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyStockWithLeadTimeVariance:

    def test_zero_lead_time_std_reproduces_the_classical_number(self):
        """sigma_L = 0 (no supplier, or one with no spread at all) must give
        EXACTLY today's number — the collapse the task requires."""
        old = 1.645 * 12.0 * math.sqrt(10)
        got = _safety_stock(
            avg_std=12.0, lead_time=10, service_level=0.95,
            avg_daily=80.0, lead_time_std=0.0,
        )
        assert got == pytest.approx(old, rel=1e-9)

    def test_nonzero_lead_time_std_widens_the_cushion_to_the_exact_formula(self):
        avg_std, lead_time, sl, avg_daily, lt_std = 12.0, 10, 0.95, 80.0, 4.0
        z = 1.645
        demand_term = z * avg_std * math.sqrt(lead_time)
        lead_time_term = z * avg_daily * lt_std
        expected = math.sqrt(demand_term ** 2 + lead_time_term ** 2)

        got = _safety_stock(avg_std, lead_time, sl, avg_daily=avg_daily, lead_time_std=lt_std)
        assert got == pytest.approx(expected, rel=1e-9)

        baseline = _safety_stock(avg_std, lead_time, sl)
        assert got > baseline, "a variable lead time must strictly widen the cushion"

    def test_matches_the_stability_doc_worked_example_in_proportion(self):
        """stability.md 17(a): an importer at 21 +/- 5 days, ~250 units/day,
        forecast-error sigma ~32, has a lead-time VARIANCE term ~70x the demand
        VARIANCE term — so the lead-time STANDARD DEVIATION term (its sqrt)
        should be roughly sqrt(70) ~ 8.4x the classical one."""
        avg_std, lead_time, avg_daily, lt_std = 32.0, 21, 250.0, 5.0
        z = 1.645
        demand_term = z * avg_std * math.sqrt(lead_time)
        lead_time_term = z * avg_daily * lt_std
        assert lead_time_term / demand_term == pytest.approx(8.4, abs=0.5)

        combined = _safety_stock(avg_std, lead_time, 0.95, avg_daily=avg_daily, lead_time_std=lt_std)
        classical = _safety_stock(avg_std, lead_time, 0.95)
        assert combined / classical > 8.0, (
            "the ignored term dominates: the fix must move the cushion by "
            "roughly an order of magnitude, not a rounding amount"
        )

    def test_measured_branch_combines_with_lead_time_term_in_quadrature(self):
        """`_measured_safety_stock` covers DEMAND uncertainty only. The
        lead-time term is a SEPARATE source of variance and must be added back
        in quadrature, not discarded because a measured band exists."""
        risk = _risk({"7": {"0.95": 50.0}})
        avg_daily, lt_std, sl = 20.0, 3.0, 0.95
        z = 1.645
        lead_time_term = z * avg_daily * lt_std
        expected = math.sqrt(50.0 ** 2 + lead_time_term ** 2)

        got = _safety_stock(
            avg_std=999.0, lead_time=7, service_level=sl, risk=risk,
            avg_daily=avg_daily, lead_time_std=lt_std,
        )
        assert got == pytest.approx(expected, rel=1e-9)
        assert got > 50.0, "the lead-time term must not be swallowed by the measured band"
        # It must ADD in quadrature, not simply replace the measured band with
        # the lead-time term nor discard the lead-time term outright.
        assert got != pytest.approx(50.0, rel=1e-6)
        assert got != pytest.approx(lead_time_term, rel=1e-6)

    def test_measured_branch_unaffected_when_lead_time_std_is_zero(self):
        """No supplier spread at all: the measured branch must behave exactly
        as it always did (this is what `test_the_measurement_wins_when_present`
        in TestFallback above already pins; repeated here with the new kwargs
        present but zeroed, to prove they are truly inert)."""
        risk = _risk({"9": {"0.95": 7.0}})
        got = _safety_stock(10.0, 9, 0.95, risk, avg_daily=123.0, lead_time_std=0.0)
        assert got == 7.0


class TestResolveLeadTimeStd:
    """Priority order for sigma_L (stability.md 17a): learned spread from real
    receptions (once there is enough of it) beats the configured value, which
    beats zero for a SKU with no supplier at all."""

    def test_no_supplier_collapses_to_zero(self):
        assert _resolve_lead_time_std(None, {}, {}) == 0.0
        assert _resolve_lead_time_std("", {"x": 9.0}, {"x": 9.0}) == 0.0

    def test_learned_wins_over_configured(self):
        got = _resolve_lead_time_std("Acme", {"acme": 6.0}, {"acme": 2.0})
        assert got == 6.0

    def test_falls_back_to_configured_when_not_yet_learned(self):
        got = _resolve_lead_time_std("Acme", {}, {"acme": 2.0})
        assert got == 2.0

    def test_neither_map_having_the_supplier_is_zero(self):
        assert _resolve_lead_time_std("Acme", {}, {}) == 0.0

    def test_matched_case_insensitively_and_trimmed(self):
        got = _resolve_lead_time_std("  ACME  ", {}, {"acme": 3.5})
        assert got == 3.5


class TestLearnedLeadTimeStdThreshold:
    """`get_learned_lead_time_stds` must be gated on the SAME
    MIN_LEAD_TIME_OBSERVATIONS threshold as `get_learned_lead_times` (the
    MEAN) — trusting a spread computed from fewer receptions than we already
    refuse to trust for the mean would be an inconsistency of the exact kind
    `MIN_LEAD_TIME_OBSERVATIONS`'s docstring warns against."""

    def _seed_po(self, client, auth_headers, supplier: str) -> str:
        resp = client.post(
            "/api/v1/inventory/log-po",
            params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
            json={"items": [{
                "sku": f"STDSEED-{uuid.uuid4().hex[:8]}",
                "display_name": "seed", "supplier": supplier,
                "signal": "PEDIR_YA", "recommended_qty": 1,
                "final_qty": 1, "unit_cost": 1.0, "status": "approved",
            }]},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["data"]["id"]

    def test_needs_the_same_minimum_observations_as_the_mean(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        prov = f"Std-{uuid.uuid4().hex[:6]}"

        # Two observations (below MIN_LEAD_TIME_OBSERVATIONS = 3): 2 and 4 days.
        for days in (2.0, 4.0):
            po = self._seed_po(client, auth_headers, prov)
            execute(
                "INSERT INTO supplier_lead_time_obs "
                "(tenant_id, supplier, po_log_id, lead_time_days) VALUES (%s, %s, %s, %s)",
                (tid, prov, po, days),
            )
        assert prov.lower() not in get_learned_lead_time_stds(tid), (
            "below MIN_LEAD_TIME_OBSERVATIONS the learned spread must not exist yet"
        )

        # A third observation (6 days) clears the threshold:
        # stdev_samp([2, 4, 6]) = 2.0 exactly.
        po3 = self._seed_po(client, auth_headers, prov)
        execute(
            "INSERT INTO supplier_lead_time_obs "
            "(tenant_id, supplier, po_log_id, lead_time_days) VALUES (%s, %s, %s, %s)",
            (tid, prov, po3, 6.0),
        )
        stds = get_learned_lead_time_stds(tid)
        assert prov.lower() in stds, (
            f"at n={MIN_LEAD_TIME_OBSERVATIONS} the learned spread must exist"
        )
        assert stds[prov.lower()] == pytest.approx(2.0, rel=1e-6)


class TestConfiguredLeadTimeStdMap:
    def test_reads_back_what_the_supplier_form_saved(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import supplier_service as sup_svc

        tid = test_tenant["id"]
        name = f"Conf-{uuid.uuid4().hex[:6]}"
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": name, "lead_time_days": 15, "lead_time_std": 4,
        })
        assert r.status_code == 201, r.text

        m = sup_svc.get_lead_time_std_map(tid)
        assert m[name.lower()] == 4.0


# ── Full-stack (stability.md 17a): DB + get_inventory_status ─────────────────
# The unit tests above pin the formula and the priority rule; these pin that
# the real call sites (a) actually wire a resolved sigma_L in, and (b) convert
# it from DAYS to the active PERIOD's units the same way `lead_time` itself
# is converted — the exact mistake the task warns "silently scales the
# cushion by the period length".

def _flat_forecast(daily: float, spread: float, days: int = 30) -> dict:
    """One model, constant per-bucket demand — every derived number is
    hand-checkable. Mirrors test_cart_why_and_margin.py's helper."""
    return {"lightgbm": {"forecast": [
        {
            "date": (datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat(),
            "value": daily,
            "lower": max(0.0, daily - spread),
            "upper": daily + spread,
        }
        for i in range(days)
    ]}}


def _make_po(client, auth_headers, *, sku: str, supplier: str, qty: float = 1.0) -> str:
    resp = client.post(
        "/api/v1/inventory/log-po",
        params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
        json={"items": [{
            "sku": sku, "display_name": f"Prod {sku}", "supplier": supplier,
            "signal": "PEDIR_YA", "recommended_qty": qty,
            "final_qty": qty, "unit_cost": 1.0, "status": "approved",
        }]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


class TestLeadTimeStdEndToEnd:

    def test_sku_with_no_supplier_is_unchanged(self, client, auth_headers, test_tenant):
        """No supplier at all -> sigma_L = 0 -> byte-for-byte the old formula,
        end to end through get_inventory_status."""
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        sku = f"NOSUP-{uuid.uuid4().hex[:6]}"
        r = client.put(f"/api/v1/inventory/stock/{sku}", headers=auth_headers, json={
            "current_stock": 500, "lead_time_days": 10, "moq": 1,
            "unit_cost": 2.0, "sale_price": 3.0,
        })
        assert r.status_code == 200, r.text

        sid = create_session(tid, "usr_test", "no-supplier-lt-std")["id"]
        avg_daily, avg_std = 20.0, 10.0
        session_store.set_forecasts(tid, sid, {sku: _flat_forecast(avg_daily, avg_std * _Q90_Z)})

        item = next(
            i for i in inv_svc.get_inventory_status(tid, sid, period="daily")
            if i["sku"] == sku
        )
        assert item["supplier"] is None
        expected = 1.645 * avg_std * math.sqrt(10)
        assert item["calc_explanation"]["safety_stock"] == pytest.approx(expected, abs=0.1)

    def test_weekly_period_converts_lead_time_std_not_just_lead_time(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        prov = f"Weekly-{uuid.uuid4().hex[:6]}"
        lead_time_days = 14
        lt_std_days = 7
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": prov, "lead_time_days": lead_time_days, "lead_time_std": lt_std_days,
        })
        assert r.status_code == 201, r.text

        sku = f"WK-{uuid.uuid4().hex[:6]}"
        r2 = client.put(f"/api/v1/inventory/stock/{sku}", headers=auth_headers, json={
            "current_stock": 500, "lead_time_days": lead_time_days, "moq": 1,
            "supplier": prov, "unit_cost": 2.0, "sale_price": 3.0,
        })
        assert r2.status_code == 200, r2.text

        sid = create_session(tid, "usr_test", "weekly-lt-std")["id"]
        avg_daily, avg_std = 30.0, 10.0
        session_store.set_forecasts(tid, sid, {sku: _flat_forecast(avg_daily, avg_std * _Q90_Z)})

        def _expected(lt_days: float, lt_std: float) -> float:
            z = 1.645
            term1 = z * avg_std * math.sqrt(lt_days)
            term2 = z * avg_daily * lt_std
            return math.sqrt(term1 ** 2 + term2 ** 2)

        daily_item = next(
            i for i in inv_svc.get_inventory_status(tid, sid, period="daily")
            if i["sku"] == sku
        )
        weekly_item = next(
            i for i in inv_svc.get_inventory_status(tid, sid, period="weekly")
            if i["sku"] == sku
        )

        expected_daily = _expected(lead_time_days, lt_std_days)
        expected_weekly = _expected(lead_time_days / 7, lt_std_days / 7)

        assert daily_item["calc_explanation"]["safety_stock"] == pytest.approx(
            expected_daily, abs=0.1)
        assert weekly_item["calc_explanation"]["safety_stock"] == pytest.approx(
            expected_weekly, abs=0.1)
        # The bug this guards against: converting `lead_time` to periods but
        # leaving `lead_time_std` in raw days would leave the weekly number
        # close to the DAILY one instead of the correctly-shrunk weekly one.
        assert weekly_item["calc_explanation"]["safety_stock"] != pytest.approx(
            daily_item["calc_explanation"]["safety_stock"], abs=0.1)

    def test_learned_std_beats_configured_once_threshold_met(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        prov = f"LearnStd-{uuid.uuid4().hex[:6]}"
        lead_time_days = 10
        configured_std = 2
        r = client.post("/api/v1/inventory/suppliers", headers=auth_headers, json={
            "name": prov, "lead_time_days": lead_time_days, "lead_time_std": configured_std,
        })
        assert r.status_code == 201, r.text

        sku = f"LS-{uuid.uuid4().hex[:6]}"
        r2 = client.put(f"/api/v1/inventory/stock/{sku}", headers=auth_headers, json={
            "current_stock": 500, "lead_time_days": lead_time_days, "moq": 1,
            "supplier": prov, "unit_cost": 2.0, "sale_price": 3.0,
        })
        assert r2.status_code == 200, r2.text

        sid = create_session(tid, "usr_test", "learned-std-test")["id"]
        avg_daily, avg_std = 25.0, 10.0
        session_store.set_forecasts(tid, sid, {sku: _flat_forecast(avg_daily, avg_std * _Q90_Z)})

        z = 1.645
        def _expected(lt_std: float) -> float:
            term1 = z * avg_std * math.sqrt(lead_time_days)
            term2 = z * avg_daily * lt_std
            return math.sqrt(term1 ** 2 + term2 ** 2)

        def _get_item():
            return next(
                i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == sku
            )

        item_before = _get_item()
        assert item_before["calc_explanation"]["safety_stock"] == pytest.approx(
            _expected(configured_std), abs=0.1)

        # Two receptions of 4 and 10 days -- below MIN_LEAD_TIME_OBSERVATIONS.
        # Their mean (7) and spread must NOT move anything yet.
        for days in (4.0, 10.0):
            po = _make_po(client, auth_headers, sku=f"LSSEED-{uuid.uuid4().hex[:6]}", supplier=prov)
            execute(
                "INSERT INTO supplier_lead_time_obs "
                "(tenant_id, supplier, po_log_id, lead_time_days) VALUES (%s, %s, %s, %s)",
                (tid, prov, po, days),
            )
        item_thin = _get_item()
        assert item_thin["calc_explanation"]["safety_stock"] == pytest.approx(
            _expected(configured_std), abs=0.1), (
            "thin evidence (n < MIN_LEAD_TIME_OBSERVATIONS) must not replace "
            "the configured spread"
        )

        # A third reception of 16 days: mean(4, 10, 16) = 10 (matches the
        # already-configured lead_time_days, so the DEMAND term does not move
        # too and this isolates the lead-time-VARIANCE term specifically).
        # stdev_samp([4, 10, 16]) = 6.0 exactly.
        po3 = _make_po(client, auth_headers, sku=f"LSSEED-{uuid.uuid4().hex[:6]}", supplier=prov)
        execute(
            "INSERT INTO supplier_lead_time_obs "
            "(tenant_id, supplier, po_log_id, lead_time_days) VALUES (%s, %s, %s, %s)",
            (tid, prov, po3, 16.0),
        )
        learned_std = 6.0
        item_after = _get_item()
        assert item_after["lead_time_days"] == lead_time_days, (
            "the learned MEAN was chosen to equal the configured value on "
            "purpose, so only the spread term is under test here"
        )
        assert item_after["calc_explanation"]["safety_stock"] == pytest.approx(
            _expected(learned_std), abs=0.1), (
            f"at n={MIN_LEAD_TIME_OBSERVATIONS} the learned spread must replace "
            "the configured one"
        )
        assert item_after["calc_explanation"]["safety_stock"] != pytest.approx(
            item_before["calc_explanation"]["safety_stock"], abs=0.1)
