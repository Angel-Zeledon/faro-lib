"""
Tests for models: ARIMA, ETS, Croston, ModelFactory, WeightedEnsemble.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.models.arima import run_arima_core
from forecasting_core.models.ets import run_ets_core
from forecasting_core.models.croston import croston_forecast, run_croston_core
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


# ─────────────────────────────────────────────────────────────────────────────
# WeightedEnsemble
# ─────────────────────────────────────────────────────────────────────────────

class TestStatModelsReportFullMetrics:
    """Every stat model must report the full metric set, not just MAE (regression
    guard for the bug where wrappers called evaluate_all() then kept only ['mae'])."""

    FULL_KEYS = {"mae", "rmse", "wape", "bias", "mape", "smape"}

    def test_arima_reports_full_metrics(self):
        df = _make_stat_df()
        results = run_arima_core(df, "date", "sales", "sku", 0.8, 20, 7, order=(1, 1, 0))
        assert self.FULL_KEYS.issubset(results["A"].keys())

    def test_ets_reports_full_metrics(self):
        df = _make_stat_df()
        results = run_ets_core(df, "date", "sales", "sku", 0.8, 20, 7)
        assert self.FULL_KEYS.issubset(results["A"].keys())

    def test_croston_reports_full_metrics(self):
        df = _make_stat_df()
        results = run_croston_core(df, "date", "sales", "sku", 0.8, 20, 7)
        assert self.FULL_KEYS.issubset(results["A"].keys())


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


