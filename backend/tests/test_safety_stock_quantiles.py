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

import pytest

from backend.inventory.service import (
    _Q90_Z, _calc_recommended, _measured_safety_stock, _point_sigma,
    _safety_stock, best_model_by_sku, compute_session_accuracy,
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

    def test_moq_rounds_up(self):
        qty = _calc_recommended(0.0, 10.0, 0.0, 7, moq=24, service_level=0.95,
                                risk=_risk({"7": {"0.95": 0.0}}))
        assert qty == 72.0        # 70 rounded up to the next case of 24


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
