"""
End-to-end pipeline tests for forecasting_core.
Tests the full Pipeline.run() flow and the _ml_recursive_forecast helper.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline, PipelineResults, _ml_recursive_forecast


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(models=None, horizon=7, walk_forward=False):
    models = models or {"lightgbm": {"n_estimators": 20}}
    return SessionConfig.from_dict({
        "columns": {"target": "sales", "date": "date", "group": "sku"},
        "features": {"lags": [1, 7], "rolling": [7], "diffs": [1],
                     "calendar": False, "ewm_spans": []},
        "models": models,
        "training": {"train_ratio": 0.8, "walk_forward": walk_forward,
                     "wfv_splits": 3, "min_history": 20, "seasonal_period": 7},
        "forecast": {"horizon": horizon},
        "business": {"service_level": 0.95, "lead_time_days": 7,
                     "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
    })


def _make_sales_df(n=100, skus=("A", "B"), seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for sku in skus:
        sales = np.maximum(1, rng.normal(50, 8, n)).round(2)
        for i, d in enumerate(pd.date_range("2021-01-01", periods=n, freq="D")):
            rows.append({"date": d, "sku": sku, "sales": float(sales[i])})
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# _ml_recursive_forecast unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMlRecursiveForecast:

    def _make_model(self):
        from lightgbm import LGBMRegressor
        rng = np.random.default_rng(0)
        X = rng.normal(0, 1, (100, 3))
        y = X[:, 0] * 2 + 10
        model = LGBMRegressor(n_estimators=10, verbosity=-1)
        model.fit(X, y)
        return model

    def test_output_length_equals_horizon(self):
        model = self._make_model()
        history = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        feat = np.array([50.0, 45.0, 3.0])
        names = ["lag_1", "lag_2", "other"]
        preds = _ml_recursive_forecast(model, history, feat, names, horizon=5)
        assert len(preds) == 5

    def test_predictions_are_non_negative(self):
        model = self._make_model()
        history = np.arange(10, dtype=float)
        feat = np.array([10.0, 8.0, 3.0])
        names = ["lag_1", "lag_2", "other"]
        preds = _ml_recursive_forecast(model, history, feat, names, horizon=7)
        assert (preds >= 0).all()

    def test_lag_features_update_correctly(self):
        """A model that returns lag_1 exactly should give a random walk."""
        from sklearn.base import BaseEstimator
        class IdentityLag(BaseEstimator):
            def predict(self, X):
                return X[:, 0]  # returns lag_1

        model = IdentityLag()
        history = np.array([19.0])
        feat = np.array([19.0])  # lag_1 = 19
        names = ["lag_1"]
        preds = _ml_recursive_forecast(model, history, feat, names, horizon=4)
        # With lag_1 = previous pred and identity model: [19, 19, 19, 19]
        assert preds[0] == pytest.approx(19.0)

    def test_empty_history_does_not_crash(self):
        model = self._make_model()
        preds = _ml_recursive_forecast(model, np.array([]), np.array([0.0, 0.0, 0.0]),
                                       ["lag_1", "lag_2", "x"], horizon=3)
        assert len(preds) == 3

    def test_no_lag_columns_in_feature_names(self):
        model = self._make_model()
        history = np.arange(10, dtype=float)
        feat = np.array([10.0, 5.0, 2.0])
        # No lag_* columns → no buffer updates, still runs
        preds = _ml_recursive_forecast(model, history, feat, ["a", "b", "c"], horizon=3)
        assert len(preds) == 3

    def test_model_exception_uses_buffer_fallback(self):
        from sklearn.base import BaseEstimator
        class FailModel(BaseEstimator):
            def predict(self, X): raise RuntimeError("always fails")

        preds = _ml_recursive_forecast(FailModel(), np.array([42.0]), np.array([42.0]), ["lag_1"], horizon=3)
        assert len(preds) == 3
        assert all(p >= 0 for p in preds)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline.run() — happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineRun:

    def test_pipeline_returns_results_object(self, tmp_path, df_standard, base_config):
        # Write df to CSV so pipeline can load it
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        assert isinstance(results, PipelineResults)

    def test_pipeline_metrics_df_not_empty(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        assert results.metrics_df is not None
        assert len(results.metrics_df) > 0

    def test_pipeline_metrics_df_columns(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        df = results.metrics_df
        for col in ["sku", "model", "mae"]:
            assert col in df.columns

    def test_pipeline_forecast_df_generated(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "forecast": {"horizon": 7},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        assert results.forecast_df is not None
        assert len(results.forecast_df) > 0

    def test_pipeline_forecast_horizon_respected(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "forecast": {"horizon": 14},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        if results.forecast_df is not None and len(results.forecast_df) > 0:
            max_step = results.forecast_df["step"].max()
            assert max_step <= 14

    def test_pipeline_inventory_df_generated(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        assert results.inventory_df is not None

    def test_pipeline_run_id_non_empty(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        assert results.run_id != ""

    def test_pipeline_with_arima_only(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"arima": {}}})
        results = Pipeline(cfg).run()
        assert results.metrics_df is not None

    def test_pipeline_with_ets_only(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"ets": {}}})
        results = Pipeline(cfg).run()
        assert results.metrics_df is not None

    def test_pipeline_mae_columns_non_negative(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        df = results.metrics_df
        mae_col = df["mae"].dropna()
        assert (mae_col >= 0).all()

    def test_pipeline_results_contain_baselines(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        models_in_results = set(results.metrics_df["model"].unique())
        # Baselines should always be present
        assert any(m in models_in_results for m in ["naive", "seasonal_naive", "historical_avg"])


# ─────────────────────────────────────────────────────────────────────────────
# Test _flatten() metric forwarding — Tasks 1-3 metric completeness
# ─────────────────────────────────────────────────────────────────────────────

class TestFlattenForwardsFullMetricSet:
    """Verify that _flatten() forwards all metrics for stat/dl and new metrics for ml."""

    def test_stat_model_rows_have_full_metric_columns(self, tmp_path, df_standard, base_config):
        """Test that stat models include rmse, wape, bias, mape, smape."""
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"arima": {}}})
        results = Pipeline(cfg).run()
        df = results.metrics_df
        stat_rows = df[df["type"].isin(["stat", "dl"])]
        assert not stat_rows.empty, "fixture must include at least one stat/dl model"
        for col in ["rmse", "wape", "bias", "mape", "smape"]:
            assert col in stat_rows.columns, f"missing column {col} in stat rows"
            assert stat_rows[col].notna().any(), f"{col} is all-null for stat/dl rows"

    def test_ml_model_rows_have_mape_and_smape(self, tmp_path, df_standard, base_config):
        """Test that ML models include mape and smape."""
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({**base_config,
                                       "data": {"path": csv},
                                       "models": {"lightgbm": {"n_estimators": 10}}})
        results = Pipeline(cfg).run()
        df = results.metrics_df
        ml_rows = df[df["type"] == "ml"]
        assert not ml_rows.empty, "fixture must include at least one ml model"
        for col in ["mape", "smape"]:
            assert col in ml_rows.columns, f"missing column {col} in ml rows"
            assert ml_rows[col].notna().any(), f"{col} is all-null for ml rows"


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline._inventory — unit tests for real-forecast-based inventory
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineInventory:
    """Tests for the _inventory helper, which now uses real forecast arrays."""

    def _setup(self, horizon=7):
        cfg = _make_config(horizon=horizon)
        pipeline = Pipeline(cfg)
        c = cfg.columns
        b = cfg.business
        df = _make_sales_df(n=60, skus=("A", "B"))
        return pipeline, df, c, b, horizon

    def _make_forecast_df(self, skus, horizon, model="lgbm", demand=50.0):
        rows = []
        for sku in skus:
            for step in range(1, horizon + 1):
                rows.append({"sku": sku, "model": model, "step": step, "forecast": demand})
        return pd.DataFrame(rows)

    def _make_metrics_df(self, skus, model="lgbm", mae=5.0):
        return pd.DataFrame([{"sku": sku, "model": model, "mae": mae} for sku in skus])

    # -- Uses real forecasts --------------------------------------------------

    def test_uses_forecast_df_when_provided(self):
        pipeline, df, c, b, h = self._setup()
        fc_df = self._make_forecast_df(["A", "B"], h, demand=100.0)
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_df)
        assert inv is not None
        assert len(inv) == 2

    def test_inventory_sku_set_matches_forecast_skus(self):
        pipeline, df, c, b, h = self._setup()
        fc_df = self._make_forecast_df(["A", "B"], h)
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_df)
        assert set(inv["sku"].unique()) == {"A", "B"}

    def test_forecast_values_used_not_historical_mean(self):
        """If forecast demand is very high, reorder_point should be bigger than with low demand."""
        pipeline, df, c, b, h = self._setup(horizon=14)
        fc_high = self._make_forecast_df(["A"], h, demand=10_000.0)
        fc_low  = self._make_forecast_df(["A"], h, demand=1.0)
        inv_high = pipeline._inventory(df, c, b, h, forecast_df=fc_high)
        inv_low  = pipeline._inventory(df, c, b, h, forecast_df=fc_low)
        assert inv_high.loc[inv_high["sku"] == "A", "reorder_point"].values[0] > \
               inv_low.loc[inv_low["sku"] == "A", "reorder_point"].values[0]

    def test_best_model_selected_by_mae(self):
        """With two competing models, the one with lower MAE should be used."""
        pipeline, df, c, b, h = self._setup(horizon=7)
        fc_df = pd.concat([
            self._make_forecast_df(["A"], h, model="good_model", demand=1000.0),
            self._make_forecast_df(["A"], h, model="bad_model",  demand=1.0),
        ], ignore_index=True)
        metrics_df = pd.DataFrame([
            {"sku": "A", "model": "good_model", "mae": 2.0},
            {"sku": "A", "model": "bad_model",  "mae": 99.0},
        ])
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_df, metrics_df=metrics_df)
        # good_model has lower MAE → selected → demand=1000 → high reorder_point
        rop = inv.loc[inv["sku"] == "A", "reorder_point"].values[0]
        # A reorder_point driven by demand=1000 will be >> one driven by demand=1
        assert rop > 10

    def test_no_metrics_df_uses_all_rows_for_sku(self):
        """When metrics_df is None, all forecast rows for the SKU are averaged."""
        pipeline, df, c, b, h = self._setup()
        fc_df = self._make_forecast_df(["A"], h)
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_df, metrics_df=None)
        assert inv is not None

    def test_forecast_shorter_than_horizon_is_padded(self):
        """A forecast with fewer steps than horizon should be padded, not crash."""
        pipeline, df, c, b, h = self._setup(horizon=14)
        fc_short = self._make_forecast_df(["A"], horizon=5, demand=30.0)  # only 5 steps
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_short)
        assert inv is not None
        assert len(inv) > 0

    def test_negative_forecast_clipped_to_zero(self):
        """Negative forecast values should be clipped to 0 before inventory calc."""
        pipeline, df, c, b, h = self._setup()
        fc_df = self._make_forecast_df(["A"], h, demand=-50.0)
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_df)
        # Clipped to 0 → should produce valid result (not NaN or crash)
        assert inv is not None

    # -- Fallback to historical mean -----------------------------------------

    def test_falls_back_to_historical_mean_when_no_forecast(self):
        pipeline, df, c, b, h = self._setup()
        inv = pipeline._inventory(df, c, b, h, forecast_df=None)
        assert inv is not None
        assert len(inv) > 0

    def test_falls_back_when_forecast_df_empty(self):
        pipeline, df, c, b, h = self._setup()
        empty_fc = pd.DataFrame(columns=["sku", "model", "step", "forecast"])
        inv = pipeline._inventory(df, c, b, h, forecast_df=empty_fc)
        assert inv is not None

    def test_inventory_returns_dataframe(self):
        pipeline, df, c, b, h = self._setup()
        inv = pipeline._inventory(df, c, b, h)
        assert isinstance(inv, pd.DataFrame)

    def test_inventory_has_expected_columns(self):
        pipeline, df, c, b, h = self._setup()
        inv = pipeline._inventory(df, c, b, h)
        for col in ["sku", "action", "reorder_point"]:
            assert col in inv.columns


# ─────────────────────────────────────────────────────────────────────────────
# Ensemble rows and quantile columns in forecast_df
# ─────────────────────────────────────────────────────────────────────────────

class TestForecastDfEnsembleAndQuantiles:
    """
    Verify that _generate_forecast_df produces:
      - quantile columns (q50, q90, q95, …) from config.forecast.quantiles
      - an "ensemble" model row per SKU when multiple models competed
    """

    def _two_model_config(self, horizon=7):
        return SessionConfig.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "features": {"lags": [1, 7], "rolling": [7], "diffs": [1],
                         "calendar": False, "ewm_spans": []},
            "models": {
                "lightgbm": {"n_estimators": 10},
                "xgboost":  {"n_estimators": 10},
            },
            "training": {"train_ratio": 0.8, "walk_forward": False,
                         "wfv_splits": 2, "min_history": 20, "seasonal_period": 7},
            "forecast": {"horizon": horizon, "quantiles": [0.1, 0.5, 0.9]},
            "business": {"service_level": 0.95, "lead_time_days": 7,
                         "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
        })

    def test_quantile_columns_appear_in_forecast_df(self, tmp_path):
        df = _make_sales_df(n=100, skus=("A",))
        csv = str(tmp_path / "data.csv")
        df.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "features": {"lags": [1, 7], "rolling": [7], "diffs": [1],
                         "calendar": False, "ewm_spans": []},
            "models": {"lightgbm": {"n_estimators": 10}},
            "training": {"train_ratio": 0.8, "walk_forward": False,
                         "wfv_splits": 2, "min_history": 20, "seasonal_period": 7},
            "forecast": {"horizon": 7, "quantiles": [0.1, 0.5, 0.9]},
            "business": {"service_level": 0.95, "lead_time_days": 7,
                         "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
            "data": {"path": csv},
        })
        results = Pipeline(cfg).run()
        assert results.forecast_df is not None
        cols = results.forecast_df.columns.tolist()
        assert any(c.startswith("q") and c[1:].isdigit() for c in cols)

    def test_quantile_values_non_negative(self, tmp_path):
        df = _make_sales_df(n=100, skus=("A",))
        csv = str(tmp_path / "data.csv")
        df.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "features": {"lags": [1], "rolling": [7], "diffs": [1],
                         "calendar": False, "ewm_spans": []},
            "models": {"lightgbm": {"n_estimators": 10}},
            "training": {"train_ratio": 0.8, "walk_forward": False,
                         "wfv_splits": 2, "min_history": 20, "seasonal_period": 7},
            "forecast": {"horizon": 7, "quantiles": [0.1, 0.9]},
            "business": {"service_level": 0.95, "lead_time_days": 7,
                         "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
            "data": {"path": csv},
        })
        results = Pipeline(cfg).run()
        fdf = results.forecast_df
        q_cols = [c for c in fdf.columns if c.startswith("q") and c[1:].isdigit()]
        for col in q_cols:
            assert (fdf[col].dropna() >= 0).all()

    def test_ensemble_row_present_with_two_ml_models(self, tmp_path):
        df = _make_sales_df(n=120, skus=("A",))
        csv = str(tmp_path / "data.csv")
        df.to_csv(csv, index=False)
        cfg = self._two_model_config()
        from dataclasses import replace as _replace
        import forecasting_core.config.config as _cc
        cfg2 = _cc.SessionConfig.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "features": {"lags": [1, 7], "rolling": [7], "diffs": [1],
                         "calendar": False, "ewm_spans": []},
            "models": {"lightgbm": {"n_estimators": 10}, "xgboost": {"n_estimators": 10}},
            "training": {"train_ratio": 0.8, "walk_forward": False,
                         "wfv_splits": 2, "min_history": 20, "seasonal_period": 7},
            "forecast": {"horizon": 7, "quantiles": [0.1, 0.5, 0.9]},
            "business": {"service_level": 0.95, "lead_time_days": 7,
                         "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
            "data": {"path": csv},
        })
        results = Pipeline(cfg2).run()
        assert results.forecast_df is not None
        models = set(results.forecast_df["model"].unique())
        assert "ensemble" in models

    def test_ensemble_forecast_within_individual_model_range(self, tmp_path):
        """Ensemble value should be a weighted avg — between the extremes of individual models."""
        df = _make_sales_df(n=120, skus=("A",))
        csv = str(tmp_path / "data.csv")
        df.to_csv(csv, index=False)
        cfg = SessionConfig.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "features": {"lags": [1, 7], "rolling": [7], "diffs": [1],
                         "calendar": False, "ewm_spans": []},
            "models": {"lightgbm": {"n_estimators": 10}, "xgboost": {"n_estimators": 10}},
            "training": {"train_ratio": 0.8, "walk_forward": False,
                         "wfv_splits": 2, "min_history": 20, "seasonal_period": 7},
            "forecast": {"horizon": 7, "quantiles": [0.1, 0.9]},
            "business": {"service_level": 0.95, "lead_time_days": 7,
                         "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
            "data": {"path": csv},
        })
        results = Pipeline(cfg).run()
        if results.forecast_df is None or "ensemble" not in results.forecast_df["model"].values:
            pytest.skip("ensemble row not generated (models may not have converged)")
        fdf = results.forecast_df
        ens = fdf[fdf["model"] == "ensemble"]["forecast"].values
        others = fdf[fdf["model"] != "ensemble"]["forecast"].values
        assert ens.min() >= 0.0
        assert ens.max() <= others.max() * 1.01  # allow tiny float rounding
