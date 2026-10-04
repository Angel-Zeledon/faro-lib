"""
End-to-end pipeline tests for forecasting_core.
Tests the full Pipeline.run() flow.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline, PipelineResults


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

    # -- Safety stock driven by the q90 band, not forecast-path dispersion ----

    def test_flat_forecast_with_q90_band_gets_nonzero_safety_stock(self):
        """A flat, constant-value forecast is what a GOOD model produces on a
        stable SKU, and np.std(a constant array) == 0. Before wiring q90 in,
        InventoryAdvisor.recommend() defaulted demand_std to np.std(forecast)
        whenever _inventory called it without std_by_sku — so every
        stable SKU got safety_stock == 0 regardless of the model's actual
        uncertainty. A q90 band above the flat forecast must now produce a
        positive cushion."""
        pipeline, df, c, b, h = self._setup(horizon=7)
        rows = [
            {"sku": "A", "model": "lgbm", "step": s, "forecast": 50.0, "q90": 70.0}
            for s in range(1, h + 1)
        ]
        fc_df = pd.DataFrame(rows)
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_df)
        safety_stock = inv.loc[inv["sku"] == "A", "safety_stock"].values[0]
        assert safety_stock > 0

    def test_safety_stock_matches_q90_derived_sigma_exactly(self):
        """sigma fed to the advisor must be exactly (q90 - forecast) / 1.2816
        — the same quantity backend/inventory/service.py::_point_sigma
        computes — not np.std(forecast) or some other derivation. A flat
        forecast with a known band lets the expected safety_stock be computed
        independently (z * sigma * sqrt(lead_time_days)) and compared."""
        from scipy import stats as scipy_stats
        pipeline, df, c, b, h = self._setup(horizon=7)
        sigma = 10.0
        q90_z = 1.2816  # norm.ppf(0.9)
        band = sigma * q90_z
        rows = [
            {"sku": "A", "model": "lgbm", "step": s, "forecast": 50.0, "q90": 50.0 + band}
            for s in range(1, h + 1)
        ]
        fc_df = pd.DataFrame(rows)
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_df)
        safety_stock = inv.loc[inv["sku"] == "A", "safety_stock"].values[0]
        z = float(scipy_stats.norm.ppf(b.service_level))
        expected = z * sigma * np.sqrt(b.lead_time_days)
        assert safety_stock == pytest.approx(expected, rel=0.02)

    # -- The engine has no stock source: unknown, never an assumed zero ------

    def test_engine_generated_inventory_reports_stock_as_unknown(self):
        """Pipeline._inventory never has a stocks_by_sku source — there is no
        stock column anywhere in the engine's inputs. Before the None-state
        fix, batch_recommend() defaulted every SKU's current_stock to 0.0,
        which produced stockout_risk~1.0, days_coverage=0.0 and
        action="REORDER" for every single SKU regardless of the real stock
        position (see backend/ai/rag_service.py ~line 723 for where that
        reached a tenant as "100% of your products are critical"). It must
        now come back as an explicit unknown, not a number."""
        pipeline, df, c, b, h = self._setup(horizon=7)
        fc_df = self._make_forecast_df(["A", "B"], h, demand=100.0)
        inv = pipeline._inventory(df, c, b, h, forecast_df=fc_df)
        assert inv["stockout_risk"].isna().all()
        assert inv["days_coverage"].isna().all()
        assert (inv["action"] == "UNKNOWN").all()

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


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline._demand_risk (stability.md 17b) — the per-SKU cumulative band
# ─────────────────────────────────────────────────────────────────────────────
#
# Unit-level tests against fabricated `results_ml` entries rather than a full
# Pipeline.run(): the property under test is how `_demand_risk` reads and
# filters `Trainer`'s pooled bank, not whether training itself succeeds — that
# is what test_training.py's TestCumulativeResidualBank* classes cover.

class _FakeProfile:
    def __init__(self, scale):
        self.scale = scale


class _FakeGlobalForecaster:
    """Stands in for `GlobalDirectForecaster` — `_demand_risk` only reads
    `.cumulative_residuals_by_horizon` and `.profile.scale` off it."""

    def __init__(self, cumulative, scale):
        self.cumulative_residuals_by_horizon = cumulative
        self.profile = _FakeProfile(scale)


def _ascending_residuals(n, scale=1.0):
    """n ascending values scaled by `scale`. `np.quantile(., 0.95)` on this
    array is deterministic (index 0.95*(n-1), linear interpolation) — for
    n=40 that is exactly `37.05 * scale` — which is what the monotonicity
    test below depends on to predict the exact clamped value."""
    return (np.arange(n, dtype=float) * scale).tolist()


class TestDemandRiskBank:

    def _pipeline(self):
        return Pipeline(_make_config())

    # -- thin evidence: a horizon below the floor publishes nothing --------

    def test_horizon_below_the_floor_is_dropped(self):
        from forecasting_core.evaluation.conformal import MIN_RESIDUALS_PER_HORIZON
        entry = {
            "sku": "S1", "model": "lgbm",
            "cumulative_residuals_by_horizon": {
                1: _ascending_residuals(MIN_RESIDUALS_PER_HORIZON - 1),
            },
            "series_scale": 10.0,
        }
        pipeline = self._pipeline()
        pipeline._champion_by_sku = {"S1": "lgbm"}
        risk = pipeline._demand_risk({"lgbm_S1": entry}, [0.95])
        assert "S1" not in risk, (
            "a horizon under MIN_RESIDUALS_PER_HORIZON must not publish a "
            "quantile fabricated from a handful of points"
        )

    def test_horizon_at_the_floor_publishes(self):
        """The boundary itself — not just comfortably above or below it."""
        from forecasting_core.evaluation.conformal import MIN_RESIDUALS_PER_HORIZON
        entry = {
            "sku": "S1", "model": "lgbm",
            "cumulative_residuals_by_horizon": {
                1: _ascending_residuals(MIN_RESIDUALS_PER_HORIZON),
            },
            "series_scale": 10.0,
        }
        pipeline = self._pipeline()
        pipeline._champion_by_sku = {"S1": "lgbm"}
        risk = pipeline._demand_risk({"lgbm_S1": entry}, [0.95])
        assert "S1" in risk

    def test_no_folds_at_all_emits_no_band(self):
        entry = {
            "sku": "S1", "model": "lgbm",
            "cumulative_residuals_by_horizon": {},
            "series_scale": 10.0,
        }
        pipeline = self._pipeline()
        pipeline._champion_by_sku = {"S1": "lgbm"}
        risk = pipeline._demand_risk({"lgbm_S1": entry}, [0.95])
        assert risk == {}

    # -- one band per SKU, and it is the champion's -------------------------

    def test_champion_band_survives_over_a_later_global_entry(self):
        """
        THE regression this task exists to prevent.

        `results_ml` holds one entry per (model, SKU), and the global model's
        results are `.update()`-d in AFTER the per-SKU ones (pipeline.py
        step 7c). Before the fix, iterating `results_ml.values()` let the
        later global entry overwrite the earlier per-SKU one at the same SKU
        key regardless of which one was actually the champion — every
        per-SKU champion silently lost its band to `global_lgbm`.
        """
        n = 40
        per_sku_entry = {
            "sku": "S1", "model": "xgboost",
            "cumulative_residuals_by_horizon": {1: _ascending_residuals(n, scale=1.0)},
            "series_scale": 10.0,
        }
        global_entry = {
            "sku": "S1", "model": "global_lgbm",
            "direct_forecaster": _FakeGlobalForecaster(
                cumulative={1: _ascending_residuals(n, scale=5.0)}, scale=5.0,
            ),
        }
        # Insertion order mirrors the real pipeline: per-SKU results first,
        # global results merged in afterwards.
        results_ml = {"xgboost_S1": per_sku_entry, "global_lgbm_S1": global_entry}

        pipeline = self._pipeline()
        pipeline._champion_by_sku = {"S1": "xgboost"}
        risk = pipeline._demand_risk(results_ml, [0.95])

        assert risk["S1"]["model"] == "xgboost", (
            "the later global entry overwrote the per-SKU champion's band"
        )

    def test_global_champion_still_wins_when_it_actually_is_the_champion(self):
        """The fix must not simply always prefer the per-SKU model — when the
        global model genuinely is the champion its existing (pre-17b) path
        through `direct_forecaster` must still be the one that is read."""
        n = 40
        per_sku_entry = {
            "sku": "S1", "model": "xgboost",
            "cumulative_residuals_by_horizon": {1: _ascending_residuals(n, scale=1.0)},
            "series_scale": 10.0,
        }
        global_entry = {
            "sku": "S1", "model": "global_lgbm",
            "direct_forecaster": _FakeGlobalForecaster(
                cumulative={1: _ascending_residuals(n, scale=5.0)}, scale=5.0,
            ),
        }
        results_ml = {"xgboost_S1": per_sku_entry, "global_lgbm_S1": global_entry}

        pipeline = self._pipeline()
        pipeline._champion_by_sku = {"S1": "global_lgbm"}
        risk = pipeline._demand_risk(results_ml, [0.95])

        assert risk["S1"]["model"] == "global_lgbm"
        # Read from the forecaster's own band and scale, not the per-SKU
        # entry's series_scale (10.0, which would give a different number).
        expected_offset = round(
            float(np.quantile(_ascending_residuals(n, scale=5.0), 0.95)) * 5.0, 4,
        )
        assert risk["S1"]["cumulative_offsets"]["1"]["0.95"] == pytest.approx(expected_offset)

    # -- cumulative bands are monotonic in the horizon -----------------------

    def test_bands_are_non_decreasing_across_the_horizon(self):
        """
        A dip at h=2 below h=1 is sampling noise, not a real property — the
        uncertainty of a sum cannot shrink as terms are added to it.
        `enforce_horizon_monotonic` exists precisely for this.
        """
        n = 40
        entry = {
            "sku": "S1", "model": "lgbm",
            "cumulative_residuals_by_horizon": {
                1: _ascending_residuals(n, scale=1.0),   # raw q95 ~= 37.05
                2: _ascending_residuals(n, scale=0.5),   # raw q95 ~= 18.525 (the dip)
                3: _ascending_residuals(n, scale=2.0),   # raw q95 ~= 74.1
            },
            "series_scale": 1.0,
        }
        pipeline = self._pipeline()
        pipeline._champion_by_sku = {"S1": "lgbm"}
        risk = pipeline._demand_risk({"lgbm_S1": entry}, [0.95])
        offsets = risk["S1"]["cumulative_offsets"]
        v1 = offsets["1"]["0.95"]
        v2 = offsets["2"]["0.95"]
        v3 = offsets["3"]["0.95"]

        assert v1 <= v2 <= v3, f"bands are not monotonic in the horizon: {v1}, {v2}, {v3}"
        # Not merely non-decreasing by coincidence: h=2's own raw quantile
        # (~18.525) is smaller than h=1's (~37.05). A monotone result at h=2
        # equal to h=1 proves the dip was actually clamped upward, not that
        # this fixture happened not to trigger it.
        assert v2 == pytest.approx(v1), (
            f"h=2 reported its own (smaller) empirical quantile {v2} instead "
            f"of being clamped to h=1's {v1}"
        )
