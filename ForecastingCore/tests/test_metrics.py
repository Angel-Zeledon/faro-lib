"""
Tests for forecasting_core.evaluation.metrics and evaluation.baselines.
"""

import numpy as np
import pytest
from forecasting_core.evaluation.metrics import (
    mae, rmse, wape, bias, mape,
    evaluate_all, evaluate_by_horizon,
    forecast_intervals, global_wape, business_loss,
)
from forecasting_core.evaluation.baselines import BaselineEvaluator


# ─────────────────────────────────────────────────────────────────────────────
# Scalar metrics — happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestScalarMetrics:

    def test_mae_perfect(self):
        assert mae([1, 2, 3], [1, 2, 3]) == 0.0

    def test_mae_known_value(self):
        assert abs(mae([10, 20], [12, 18]) - 2.0) < 1e-9

    def test_rmse_perfect(self):
        assert rmse([5, 5], [5, 5]) == 0.0

    def test_rmse_known_value(self):
        # errors: [2, -2] → sqrt(mean([4,4])) = 2
        assert abs(rmse([10, 20], [12, 18]) - 2.0) < 1e-9

    def test_wape_zero_target(self):
        # denominator is protected by 1e-8
        result = wape([0, 0, 0], [1, 1, 1])
        assert result > 0  # large but not inf/nan

    def test_wape_perfect(self):
        assert wape([10, 20], [10, 20]) == 0.0

    def test_bias_positive(self):
        # yhat > y → positive bias
        b = bias([10], [15])
        assert b == pytest.approx(5.0)

    def test_bias_negative(self):
        b = bias([15], [10])
        assert b == pytest.approx(-5.0)

    def test_mape_perfect(self):
        assert mape([10, 20, 30], [10, 20, 30]) == 0.0

    def test_mape_zero_protection(self):
        # y=0 → denominator is eps=1e-8, should not raise
        result = mape([0, 10], [1, 10])
        assert np.isfinite(result)

    def test_all_metrics_return_floats(self):
        for fn in [mae, rmse, wape, bias, mape]:
            result = fn([10, 20], [11, 19])
            assert isinstance(result, float), f"{fn.__name__} must return float"


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_all
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateAll:

    def test_returns_all_keys(self):
        result = evaluate_all([1, 2, 3], [1, 2, 3])
        assert set(result.keys()) == {"mae", "rmse", "wape", "bias", "mape"}

    def test_perfect_preds_all_zero(self):
        result = evaluate_all([5, 10, 15], [5, 10, 15])
        assert result["mae"] == 0.0
        assert result["rmse"] == 0.0
        assert result["bias"] == 0.0

    def test_list_and_array_inputs(self):
        r1 = evaluate_all([1, 2], [2, 1])
        r2 = evaluate_all(np.array([1, 2]), np.array([2, 1]))
        assert r1["mae"] == pytest.approx(r2["mae"])

    def test_single_element(self):
        result = evaluate_all([100], [90])
        assert result["mae"] == pytest.approx(10.0)


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_by_horizon
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateByHorizon:

    def test_returns_one_key_per_step(self):
        result = evaluate_by_horizon([10, 20, 30], [10, 21, 29])
        assert list(result.keys()) == ["mae@1", "mae@2", "mae@3"]

    def test_values_are_absolute_errors(self):
        result = evaluate_by_horizon([10, 20], [11, 18])
        assert result["mae@1"] == pytest.approx(1.0)
        assert result["mae@2"] == pytest.approx(2.0)


# ─────────────────────────────────────────────────────────────────────────────
# forecast_intervals
# ─────────────────────────────────────────────────────────────────────────────

class TestForecastIntervals:

    def test_returns_lo_hi_per_quantile(self):
        residuals = np.random.default_rng(0).normal(0, 5, 100)
        forecast = np.array([50.0, 55.0, 60.0])
        result = forecast_intervals(residuals, forecast, quantiles=[0.5, 0.9])
        assert "p50_lo" in result and "p50_hi" in result
        assert "p90_lo" in result and "p90_hi" in result

    def test_lo_below_hi(self):
        residuals = np.random.default_rng(1).normal(0, 10, 200)
        forecast = np.full(5, 100.0)
        result = forecast_intervals(residuals, forecast)
        assert (result["p90_lo"] < result["p90_hi"]).all()

    def test_empty_residuals_raises(self):
        # ⚠️ CRITICAL FIX REQUIRED: empty residuals produce ValueError from np.quantile
        # The library should guard against this with an explicit check.
        with pytest.raises(Exception):
            forecast_intervals(np.array([]), np.array([10.0]))

    def test_shape_matches_forecast(self):
        residuals = np.ones(50)
        forecast = np.array([1.0, 2.0, 3.0])
        result = forecast_intervals(residuals, forecast, quantiles=[0.9])
        assert len(result["p90_lo"]) == 3


# ─────────────────────────────────────────────────────────────────────────────
# global_wape and business_loss
# ─────────────────────────────────────────────────────────────────────────────

class TestGlobalWapeBusinessLoss:

    def test_global_wape_perfect(self):
        assert global_wape([10, 20], [10, 20]) == 0.0

    def test_global_wape_all_zero_y(self):
        result = global_wape([0, 0], [1, 1])
        assert np.isfinite(result)

    def test_business_loss_zero_errors(self):
        assert business_loss([10, 20], [10, 20]) == 0.0

    def test_business_loss_asymmetric(self):
        # over-forecast: over=5, under=0 → 1.0 * 5 = 5
        over = business_loss([10], [15], overforecast_cost=1.0, underforecast_cost=3.0)
        # under-forecast: over=0, under=5 → 3.0 * 5 = 15
        under = business_loss([15], [10], overforecast_cost=1.0, underforecast_cost=3.0)
        assert under == pytest.approx(15.0)
        assert over == pytest.approx(5.0)
        assert under > over

    def test_business_loss_returns_float(self):
        result = business_loss([100, 200], [110, 190])
        assert isinstance(result, float)


# ─────────────────────────────────────────────────────────────────────────────
# BaselineEvaluator
# ─────────────────────────────────────────────────────────────────────────────

class TestBaselineEvaluator:

    def test_naive_repeats_last_value(self):
        train = np.array([1, 2, 3, 4, 5])
        preds = BaselineEvaluator.naive(train, 3)
        assert (preds == 5.0).all()

    def test_historical_avg_correct(self):
        train = np.array([10.0, 20.0, 30.0])
        preds = BaselineEvaluator.historical_avg(train, 2)
        assert all(p == pytest.approx(20.0) for p in preds)

    def test_seasonal_naive_period_7(self):
        train = np.arange(14, dtype=float)  # 0..13
        preds = BaselineEvaluator.seasonal_naive(train, 7, period=7)
        # step 0: train[14 - 7 + 0] = train[7] = 7.0
        assert preds[0] == pytest.approx(7.0)

    def test_seasonal_naive_train_shorter_than_period(self):
        # train has 3 rows but period=7 → should not raise, uses fallback
        train = np.array([5.0, 6.0, 7.0])
        preds = BaselineEvaluator.seasonal_naive(train, 3, period=7)
        assert len(preds) == 3
        # All should be finite floats
        assert np.isfinite(preds).all()

    def test_evaluate_baselines_returns_three_models(self):
        train = np.arange(30, dtype=float)
        test  = np.arange(30, 37, dtype=float)
        result = BaselineEvaluator.evaluate_baselines(train, test, period=7)
        assert set(result.keys()) == {"naive", "seasonal_naive", "historical_avg"}
        for v in result.values():
            assert "mae" in v

    def test_evaluate_baselines_single_element_test(self):
        train = np.ones(20)
        test  = np.array([2.0])
        result = BaselineEvaluator.evaluate_baselines(train, test)
        assert result["naive"]["mae"] == pytest.approx(1.0)
