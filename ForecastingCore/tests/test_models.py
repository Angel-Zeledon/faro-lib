"""
Tests for models: ARIMA, ETS, Croston, ModelFactory, WeightedEnsemble.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.models.arima import run_arima_core
from forecasting_core.models.ets import run_ets_core
from forecasting_core.models.croston import croston_forecast, run_croston_core
from forecasting_core.models.lstm import run_lstm_core
from forecasting_core.models.factory import ModelFactory
from forecasting_core.ensemble.ensemble import WeightedEnsemble


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_stat_df(n=80, skus=("A",), seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for sku in skus:
        sales = np.maximum(1, rng.normal(50, 8, n)).round(2)
        for i, d in enumerate(pd.date_range("2021-01-01", periods=n, freq="D")):
            rows.append({"date": d, "sku": sku, "sales": float(sales[i])})
    return pd.DataFrame(rows)


def _make_shocked_tail_df(n_train=120, horizon=10, tail_extra=30, level=50.0, seed=3):
    """
    A train window plus a held-out tail whose FIRST `horizon` steps continue
    the train level and whose LATER steps are deliberately wrong by two
    orders of magnitude.

    A model fit on the flat train level forecasts something near `level`
    throughout the whole test window — it has no way to know the tail jumps.
    So the windowed cost (first `horizon` steps) stays small regardless; the
    whole-tail cost balloons once the shocked steps enter the average. This
    is what makes the fixture able to fail on the pre-fix code: if the shocked
    tail leaks into `cost_horizon`, the number moves by orders of magnitude.

    `train_ratio` is returned alongside the frame so `cut` lands exactly on
    `n_train` (`int(len(series) * train_ratio) == n_train`).
    """
    rng = np.random.default_rng(seed)
    steady = np.maximum(1.0, rng.normal(level, level * 0.06, n_train + horizon))
    shocked = np.full(tail_extra, level * 100.0)
    values = np.concatenate([steady, shocked])
    dates = pd.date_range("2021-01-01", periods=len(values), freq="D")
    df = pd.DataFrame({"date": dates, "sku": "A", "sales": values})
    train_ratio = n_train / len(values)
    return df, train_ratio


# ─────────────────────────────────────────────────────────────────────────────
# Croston
# ─────────────────────────────────────────────────────────────────────────────

class TestCroston:

    def test_all_zeros_returns_zeros(self):
        result = croston_forecast(np.zeros(20), alpha=0.1, n_ahead=5)
        assert (result == 0.0).all()
        assert len(result) == 5

    def test_positive_series_returns_positive(self):
        series = np.array([0, 5, 0, 0, 10, 0, 8, 0, 6])
        result = croston_forecast(series, alpha=0.1, n_ahead=3)
        assert (result > 0).all()

    def test_output_length_matches_n_ahead(self):
        series = np.array([1, 0, 2, 0, 3])
        result = croston_forecast(series, n_ahead=7)
        assert len(result) == 7

    def test_rate_is_size_over_interval_debiased_by_sba(self):
        """10 units every 5 buckets is a raw rate of 2 per bucket. At
        alpha=0.1 the SBA factor is (1 - 0.1/2) = 0.95, so the de-biased
        rate is 1.9, not 2.0.

        The intervals used to be built with `prepend=idx[0]`, which injected a
        zero-length first gap and seeded the interval smoothing with it. This
        same series came out at ~7.4 per bucket — 3.7x the truth — and Croston
        is routed precisely at the intermittent SKUs where nothing else would
        catch it. This test now also protects the SBA correction: it fails
        equally if the correction is dropped (result would be 2.0) as if the
        interval bug regresses.
        """
        series = np.zeros(20)
        series[[0, 5, 10, 15]] = 10.0
        assert croston_forecast(series, alpha=0.1, n_ahead=1)[0] == pytest.approx(1.9)

    def test_one_demand_in_the_whole_series_rates_it_over_that_series_debiased(self):
        """One sale of 10 in 50 buckets has a raw rate of 0.2 per bucket, not
        10. At alpha=0.1 the SBA factor is 0.95, so the de-biased rate is
        0.19, not 0.2 and nowhere near 10.
        """
        series = np.zeros(50)
        series[30] = 10.0
        assert croston_forecast(series, n_ahead=1)[0] == pytest.approx(0.19)

    def test_sba_correction_factor_is_one_minus_alpha_over_two(self):
        """The SBA de-bias multiplies the raw rate by (1 - alpha/2), not by
        (1 - alpha) and not by 1 (i.e. not skipped).

        This series is constant in both demand size (10) and interval (5), so
        the exponential smoothing of z and p converges to those exact values
        regardless of alpha — the raw rate is 2.0 for any alpha. That isolates
        the correction factor: at alpha=0.4, (1 - alpha/2) = 0.8 gives 1.6.
        The wrong-but-plausible (1 - alpha) = 0.6 would give 1.2, and skipping
        the correction entirely would give 2.0 — both fail this assertion.
        """
        series = np.zeros(20)
        series[[0, 5, 10, 15]] = 10.0
        assert croston_forecast(series, alpha=0.4, n_ahead=1)[0] == pytest.approx(1.6)

    def test_run_croston_core_returns_dict(self):
        df = _make_stat_df()
        results = run_croston_core(df, "date", "sales", "sku", 0.8, 20, 7)
        assert "A" in results
        assert "mae" in results["A"]

    def test_run_croston_core_with_horizon(self):
        df = _make_stat_df()
        results = run_croston_core(df, "date", "sales", "sku", 0.8, 20, 7, horizon=7)
        assert "forecast" in results["A"]
        assert len(results["A"]["forecast"]) == 7
        assert "residuals" in results["A"]

    def test_run_croston_core_short_series_skipped(self):
        df = _make_stat_df(n=5)
        results = run_croston_core(df, "date", "sales", "sku", 0.8, 20, 7)
        assert len(results) == 0  # too short → skipped

    def test_run_croston_core_no_group(self):
        rng = np.random.default_rng(0)
        df = pd.DataFrame({
            "date": pd.date_range("2021-01-01", periods=60),
            "sales": np.maximum(0, rng.normal(10, 5, 60)),
        })
        results = run_croston_core(df, "date", "sales", None, 0.8, 20, 7)
        assert "__all__" in results

    def test_cost_horizon_ignores_the_tail_past_the_horizon(self):
        """The champion race compares `cost_horizon` across every model
        family; it must not be the whole-tail `cost` in disguise (see
        docs/stability.md #17(d))."""
        df, train_ratio = _make_shocked_tail_df()
        results = run_croston_core(df, "date", "sales", "sku", train_ratio, 20, 7, horizon=10)
        res = results["A"]
        assert res["horizon_steps"] == 10
        assert res["cost_horizon"] < res["cost"] / 10
        assert res["cost_horizon"] < 50.0

    def test_short_tail_reports_over_available_steps_without_padding(self):
        df = _make_stat_df(n=80)
        results = run_croston_core(df, "date", "sales", "sku", 0.8, 20, 7, horizon=30)
        res = results["A"]
        held_out = 80 - int(80 * 0.8)
        assert held_out < 30, "fixture assumption: tail shorter than horizon"
        assert res["horizon_steps"] == held_out
        assert np.isfinite(res["cost_horizon"])


# ─────────────────────────────────────────────────────────────────────────────
# ARIMA
# ─────────────────────────────────────────────────────────────────────────────

class TestArima:

    def test_returns_mae_in_dict(self):
        df = _make_stat_df()
        results = run_arima_core(df, "date", "sales", "sku", 0.8, 20, 7, order=(1, 1, 0))
        assert "A" in results
        assert "mae" in results["A"]
        assert results["A"]["mae"] >= 0

    def test_with_horizon_returns_forecast(self):
        df = _make_stat_df()
        results = run_arima_core(df, "date", "sales", "sku", 0.8, 20, 7,
                                 order=(1, 1, 0), horizon=5)
        assert "forecast" in results["A"]
        assert len(results["A"]["forecast"]) == 5

    def test_short_series_skipped(self):
        df = _make_stat_df(n=3)
        results = run_arima_core(df, "date", "sales", "sku", 0.8, 20, 7)
        assert len(results) == 0

    def test_multi_sku(self):
        df = _make_stat_df(skus=["A", "B", "C"])
        results = run_arima_core(df, "date", "sales", "sku", 0.8, 20, 7, order=(1, 0, 0))
        assert len(results) == 3

    def test_arima_no_group(self):
        rng = np.random.default_rng(5)
        df = pd.DataFrame({
            "date": pd.date_range("2021-01-01", periods=60),
            "sales": rng.normal(50, 5, 60),
        })
        results = run_arima_core(df, "date", "sales", None, 0.8, 20, 7)
        assert "__all__" in results

    def test_cost_horizon_ignores_the_tail_past_the_horizon(self):
        df, train_ratio = _make_shocked_tail_df()
        results = run_arima_core(
            df, "date", "sales", "sku", train_ratio, 20, 7,
            order=(1, 1, 0), horizon=10,
        )
        res = results["A"]
        assert res["horizon_steps"] == 10
        # Old code copied the whole-tail `cost` straight into `cost_horizon`;
        # on this fixture that number is dominated by the 30 shocked steps
        # and would be orders of magnitude larger than the windowed one.
        assert res["cost_horizon"] < res["cost"] / 10
        assert res["cost_horizon"] < 50.0

    def test_short_tail_reports_over_available_steps_without_padding(self):
        df = _make_stat_df(n=80)
        results = run_arima_core(df, "date", "sales", "sku", 0.8, 20, 7,
                                 order=(1, 1, 0), horizon=30)
        res = results["A"]
        held_out = 80 - int(80 * 0.8)
        assert held_out < 30, "fixture assumption: tail shorter than horizon"
        assert res["horizon_steps"] == held_out
        assert np.isfinite(res["cost_horizon"])

    def test_no_horizon_means_no_cost_horizon(self):
        """A caller that did not ask for an h-step evaluation must not
        receive a fabricated one."""
        df = _make_stat_df()
        results = run_arima_core(df, "date", "sales", "sku", 0.8, 20, 7, order=(1, 1, 0))
        res = results["A"]
        assert res["cost_horizon"] is None
        assert res["horizon_steps"] is None


# ─────────────────────────────────────────────────────────────────────────────
# ETS
# ─────────────────────────────────────────────────────────────────────────────

class TestETS:

    def test_returns_mae(self):
        df = _make_stat_df()
        results = run_ets_core(df, "date", "sales", "sku", 0.8, 20, 7)
        assert "A" in results
        assert results["A"]["mae"] >= 0

    def test_with_horizon_returns_forecast(self):
        df = _make_stat_df()
        results = run_ets_core(df, "date", "sales", "sku", 0.8, 20, 7, horizon=14)
        assert "forecast" in results["A"]
        assert len(results["A"]["forecast"]) == 14

    def test_short_series_skipped(self):
        df = _make_stat_df(n=5)
        results = run_ets_core(df, "date", "sales", "sku", 0.8, 20, 7)
        assert len(results) == 0

    def test_all_zeros_in_train_uses_no_seasonal(self):
        # ETS with all-zeros in train should fall back to additive no-seasonal
        zeros = pd.DataFrame({
            "date": pd.date_range("2021-01-01", periods=60),
            "sku": "Z",
            "sales": [0.0] * 60,
        })
        # Should not raise; may have high MAE
        results = run_ets_core(zeros, "date", "sales", "sku", 0.8, 20, 7)
        # Either succeeds or skips (both are acceptable behavior)
        assert isinstance(results, dict)

    def test_cost_horizon_ignores_the_tail_past_the_horizon(self):
        df, train_ratio = _make_shocked_tail_df()
        results = run_ets_core(df, "date", "sales", "sku", train_ratio, 20, 7, horizon=10)
        res = results["A"]
        assert res["horizon_steps"] == 10
        assert res["cost_horizon"] < res["cost"] / 10
        assert res["cost_horizon"] < 50.0

    def test_short_tail_reports_over_available_steps_without_padding(self):
        df = _make_stat_df(n=80)
        results = run_ets_core(df, "date", "sales", "sku", 0.8, 20, 7, horizon=30)
        res = results["A"]
        held_out = 80 - int(80 * 0.8)
        assert held_out < 30, "fixture assumption: tail shorter than horizon"
        assert res["horizon_steps"] == held_out
        assert np.isfinite(res["cost_horizon"])


# ─────────────────────────────────────────────────────────────────────────────
# ModelFactory
# ─────────────────────────────────────────────────────────────────────────────

class TestModelFactory:

    def test_build_ml_returns_lightgbm(self):
        factory = ModelFactory({"lightgbm": {"n_estimators": 50}})
        models = factory.build_ml()
        assert "lightgbm" in models
        assert hasattr(models["lightgbm"], "fit")

    def test_build_ml_returns_xgboost(self):
        factory = ModelFactory({"xgboost": {"n_estimators": 50}})
        models = factory.build_ml()
        assert "xgboost" in models

    def test_ml_names(self):
        factory = ModelFactory({"lightgbm": {}, "arima": {}, "xgboost": {}})
        assert set(factory.ml_names()) == {"lightgbm", "xgboost"}

    def test_stat_names(self):
        factory = ModelFactory({"lightgbm": {}, "arima": {}, "ets": {}})
        assert set(factory.stat_names()) == {"arima", "ets"}

    def test_dl_names(self):
        factory = ModelFactory({"lstm": {}, "lightgbm": {}})
        assert factory.dl_names() == ["lstm"]

    def test_create_lightgbm(self):
        model = ModelFactory.create("lightgbm", {"n_estimators": 20})
        assert hasattr(model, "fit")

    def test_create_xgboost(self):
        model = ModelFactory.create("xgboost", {"n_estimators": 20})
        assert hasattr(model, "fit")

    def test_create_unsupported_raises(self):
        with pytest.raises(ValueError, match="unsupported"):
            ModelFactory.create("arima", {})

    def test_available_models_includes_known(self):
        models = ModelFactory.available_models()
        for m in ["lightgbm", "xgboost", "arima", "prophet"]:
            assert m in models

    def test_empty_config_returns_no_ml(self):
        factory = ModelFactory({})
        models = factory.build_ml()
        assert len(models) == 0

    def test_unknown_model_name_ignored_in_build_ml(self):
        factory = ModelFactory({"unknown_model": {}, "lightgbm": {}})
        models = factory.build_ml()
        assert "unknown_model" not in models
        assert "lightgbm" in models

    def test_lightgbm_verbosity_suppressed(self):
        # verbosity=-1 is forced regardless of user params
        model = ModelFactory.create("lightgbm", {"verbosity": 0})
        assert model.get_params()["verbosity"] == -1

    def test_build_ml_defaults_lightgbm_n_jobs_to_one(self):
        # Trainer parallelizes across SKUs in worker threads; each model must
        # stay single-threaded internally or concurrent SKUs would
        # oversubscribe the CPU against each other.
        factory = ModelFactory({"lightgbm": {"n_estimators": 20}})
        model = factory.build_ml()["lightgbm"]
        assert model.get_params()["n_jobs"] == 1

    def test_build_ml_defaults_xgboost_n_jobs_to_one(self):
        factory = ModelFactory({"xgboost": {"n_estimators": 20}})
        model = factory.build_ml()["xgboost"]
        assert model.get_params()["n_jobs"] == 1

    def test_build_ml_respects_explicit_n_jobs(self):
        # An explicit user setting must still win over the default.
        factory = ModelFactory({"lightgbm": {"n_jobs": 4}})
        model = factory.build_ml()["lightgbm"]
        assert model.get_params()["n_jobs"] == 4

    def test_there_is_no_quantile_model_builder(self):
        """The p10/p50/p90 pass is gone on purpose — see pipeline.py step 7b.

        Asserted rather than merely deleted: bringing the builder back means
        bringing back 58% of the training cost for a band the purchase quantity
        does not read, so it should be a deliberate act with this test in the
        diff, not a quiet re-addition.
        """
        assert not hasattr(ModelFactory({"lightgbm": {}}), "build_quantile_ml")

    def test_create_defaults_n_jobs_to_one(self):
        assert ModelFactory.create("lightgbm", {}).get_params()["n_jobs"] == 1
        assert ModelFactory.create("xgboost", {}).get_params()["n_jobs"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# WeightedEnsemble
# ─────────────────────────────────────────────────────────────────────────────

class TestStatModelsReportFullMetrics:
    """Every stat model must report the full metric set, not just MAE (regression
    guard for the bug where wrappers called evaluate_all() then kept only ['mae']).

    Checking key presence alone is a false-positive risk: a wrapper could regress
    to e.g. result["rmse"] = None and `FULL_KEYS.issubset(keys())` would still pass.
    So every assertion here also checks the values are real, finite numbers."""

    FULL_KEYS = {"mae", "rmse", "wape", "bias", "mape", "smape"}

    @staticmethod
    def _assert_full_and_finite(metrics: dict):
        assert TestStatModelsReportFullMetrics.FULL_KEYS.issubset(metrics.keys())
        for key in TestStatModelsReportFullMetrics.FULL_KEYS:
            value = metrics[key]
            assert value is not None, f"{key} is None"
            assert isinstance(value, (int, float)), f"{key} is not numeric: {value!r}"
            assert np.isfinite(value), f"{key} is not finite: {value!r}"

    def test_arima_reports_full_metrics(self):
        df = _make_stat_df()
        results = run_arima_core(df, "date", "sales", "sku", 0.8, 20, 7, order=(1, 1, 0))
        self._assert_full_and_finite(results["A"])

    def test_ets_reports_full_metrics(self):
        df = _make_stat_df()
        results = run_ets_core(df, "date", "sales", "sku", 0.8, 20, 7)
        self._assert_full_and_finite(results["A"])

    def test_croston_reports_full_metrics(self, df_intermittent):
        results = run_croston_core(df_intermittent, "date", "sales", "sku", 0.8, 20, 7)
        self._assert_full_and_finite(results["X"])

    def test_lstm_reports_full_metrics(self):
        df = _make_stat_df()
        results = run_lstm_core(df, "date", "sales", "sku", 0.8, 20, 7)
        if results:  # LSTM returns empty dict if TensorFlow is not installed
            self._assert_full_and_finite(results["A"])


class TestLSTMCostHorizonWindow:
    """LSTM's `cost_horizon` must be windowed the same way as the other
    statistical families — see models/ets.py for the full rationale.

    LSTM's test predictions are built from sliding windows over the whole
    series (window=14 by default), so the target of test sequence `i` sits
    at raw index `i + window`, not at `cut + i`. The fixture places the shock
    so it starts exactly one step after the first `horizon` test targets end,
    accounting for that offset — see the inline arithmetic below.
    """

    WINDOW = 14
    HORIZON = 10
    CUT = 100  # cut in sequence-space, i.e. int(len(X) * train_ratio)

    def _make_df(self):
        rng = np.random.default_rng(4)
        # First test target is at raw index CUT + WINDOW; the shock must start
        # no earlier than HORIZON steps after that, i.e. at
        # CUT + WINDOW + HORIZON.
        shock_start = self.CUT + self.WINDOW + self.HORIZON
        steady = np.maximum(1.0, rng.normal(50.0, 3.0, shock_start))
        shocked = np.full(90, 5000.0)
        values = np.concatenate([steady, shocked])
        dates = pd.date_range("2021-01-01", periods=len(values), freq="D")
        # len(X) = len(values) - WINDOW; train_ratio picked so
        # int(len(X) * train_ratio) == CUT exactly.
        train_ratio = self.CUT / (len(values) - self.WINDOW)
        return pd.DataFrame({"date": dates, "sku": "A", "sales": values}), train_ratio

    def test_cost_horizon_ignores_the_tail_past_the_horizon(self):
        df, train_ratio = self._make_df()
        results = run_lstm_core(
            df, "date", "sales", "sku", train_ratio=train_ratio, min_rows=20,
            seasonal_period=7, horizon=self.HORIZON, window=self.WINDOW,
            epochs=20, patience=5,
        )
        if not results:
            pytest.skip("TensorFlow not installed")
        res = results["A"]
        assert res["horizon_steps"] == self.HORIZON
        # Old code copied the whole-tail `cost` (dominated by ~90 shocked
        # steps) straight into `cost_horizon`. The windowed number must stay
        # close to the steady-level error instead.
        assert res["cost_horizon"] < res["cost"] / 3


class TestWeightedEnsemble:

    def test_fit_and_predict(self):
        ens = WeightedEnsemble()
        ens.fit({"SKU1": {"lgb": 5.0, "arima": 10.0}})
        preds = ens.predict("SKU1", {"lgb": np.array([10.0, 20.0]), "arima": np.array([12.0, 18.0])})
        assert len(preds) == 2
        # lgb gets higher weight (lower mae)
        assert preds[0] < 11.0  # closer to lgb's 10

    def test_unfit_sku_returns_mean(self):
        ens = WeightedEnsemble()
        ens.fit({})
        preds = ens.predict("UNKNOWN", {"a": np.array([10.0]), "b": np.array([20.0])})
        assert preds[0] == pytest.approx(15.0)

    def test_predict_no_predictions_raises(self):
        ens = WeightedEnsemble()
        ens.fit({"SKU1": {"lgb": 1.0}})
        with pytest.raises(ValueError, match="No predictions"):
            ens.predict("SKU1", {})

    def test_weights_sum_to_one(self):
        ens = WeightedEnsemble()
        ens.fit({"S": {"a": 2.0, "b": 4.0, "c": 8.0}})
        weights = ens._weights["S"]
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_zero_mae_model_gets_dominant_weight(self):
        ens = WeightedEnsemble()
        # mae=0 → weight = 1/(0+1e-8) ≈ 1e8
        ens.fit({"S": {"perfect": 0.0, "bad": 100.0}})
        assert ens._weights["S"]["perfect"] > 0.9999

    def test_weights_df_returns_dataframe(self):
        ens = WeightedEnsemble()
        ens.fit({"S1": {"a": 1.0}, "S2": {"b": 2.0}})
        df = ens.weights_df()
        assert isinstance(df, pd.DataFrame)
        assert "weight" in df.columns


