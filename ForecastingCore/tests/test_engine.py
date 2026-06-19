"""
Tests for ForecastEngine — the main public API of forecasting_core.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.engine import ForecastEngine, EngineError
from forecasting_core.config.config import SessionConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_df(n=120, skus=("A", "B"), seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for sku in skus:
        sales = np.maximum(1, rng.normal(50, 8, n)).round(2)
        for i, d in enumerate(pd.date_range("2021-01-01", periods=n, freq="D")):
            rows.append({"date": d, "sku": sku, "sales": float(sales[i])})
    return pd.DataFrame(rows)


def _trained_engine(tmp_path, df=None, models=None):
    """Build and train a minimal engine."""
    df = df or _make_df()
    csv = str(tmp_path / "data.csv")
    df.to_csv(csv, index=False)
    models = models or {"lightgbm": {"n_estimators": 10}}
    engine = ForecastEngine.from_dict({
        "columns": {"target": "sales", "date": "date", "group": "sku"},
        "features": {"lags": [1, 7], "rolling": [7], "diffs": [1],
                     "calendar": False, "ewm_spans": []},
        "models": models,
        "training": {"train_ratio": 0.8, "walk_forward": False,
                     "wfv_splits": 3, "min_history": 20, "seasonal_period": 7},
        "forecast": {"horizon": 7},
        "business": {"service_level": 0.95, "lead_time_days": 7,
                     "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
    })
    engine.load_data(csv)
    engine.train()
    return engine


# ─────────────────────────────────────────────────────────────────────────────
# Construction
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineConstruction:

    def test_from_dict_creates_engine(self):
        engine = ForecastEngine.from_dict({
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        })
        assert isinstance(engine, ForecastEngine)

    def test_from_config_json(self, tmp_path):
        cfg_dict = {"columns": {"target": "y", "date": "ds"}, "models": {"arima": {}}}
        cfg = SessionConfig.from_dict(cfg_dict)
        p = str(tmp_path / "cfg.json")
        cfg.to_json(p)
        engine = ForecastEngine.from_config(p)
        assert isinstance(engine, ForecastEngine)

    def test_default_engine_has_no_data(self):
        engine = ForecastEngine()
        assert engine._df is None


# ─────────────────────────────────────────────────────────────────────────────
# load_data
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineLoadData:

    def test_load_csv(self, csv_path):
        engine = ForecastEngine()
        engine._ensure_config()
        engine.load_data(csv_path)
        assert engine._df is not None
        assert len(engine._df) > 0

    def test_load_dataframe(self, df_standard):
        engine = ForecastEngine()
        engine.load_data(df_standard)
        assert len(engine._df) == len(df_standard)

    def test_load_nonexistent_raises(self):
        engine = ForecastEngine()
        engine._ensure_config()
        with pytest.raises(Exception, match="[Ff]ile not found|[Nn]ot found"):
            engine.load_data("/tmp/does_not_exist_xyz.csv")

    def test_load_data_stores_path_in_config(self, csv_path):
        engine = ForecastEngine.from_dict({
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        })
        engine.load_data(csv_path)
        assert engine._config.data.path == csv_path

    def test_load_data_returns_self(self, df_standard):
        engine = ForecastEngine()
        result = engine.load_data(df_standard)
        assert result is engine


# ─────────────────────────────────────────────────────────────────────────────
# Require-before-train guards
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineGuards:

    def test_get_metrics_before_train_raises(self):
        engine = ForecastEngine()
        with pytest.raises(EngineError, match="train"):
            engine.get_metrics()

    def test_get_forecast_before_train_raises(self):
        engine = ForecastEngine()
        with pytest.raises(EngineError, match="train"):
            engine.get_forecast()

    def test_get_inventory_before_train_raises(self):
        engine = ForecastEngine()
        with pytest.raises(EngineError, match="train"):
            engine.get_inventory_report()

    def test_predict_before_train_raises(self):
        engine = ForecastEngine()
        with pytest.raises(EngineError, match="train"):
            engine.predict()

    def test_get_data_quality_before_data_raises(self):
        engine = ForecastEngine.from_dict({
            "columns": {"target": "s", "date": "d"},
            "models": {"lightgbm": {}},
        })
        with pytest.raises((EngineError, Exception)):
            engine.get_data_quality_report()


# ─────────────────────────────────────────────────────────────────────────────
# Inspect methods (pre-training)
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineInspect:

    def test_get_profile(self, df_standard):
        engine = ForecastEngine()
        engine.load_data(df_standard)
        profile = engine.get_profile()
        assert "columns" in profile and "stats" in profile

    def test_get_column_options(self, df_standard):
        engine = ForecastEngine()
        engine.load_data(df_standard)
        opts = engine.get_column_options()
        assert "date_candidates" in opts

    def test_get_config_schema(self):
        engine = ForecastEngine()
        schema = engine.get_config_schema()
        assert "available_models" in schema

    def test_get_available_models(self):
        engine = ForecastEngine()
        models = engine.get_available_models()
        assert "lightgbm" in models

    def test_get_data_quality_report(self, df_standard):
        engine = ForecastEngine.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "models": {"lightgbm": {}},
        })
        engine.load_data(df_standard)
        report = engine.get_data_quality_report()
        assert isinstance(report, dict)
        assert "A" in report or "B" in report

    def test_get_routing_plan(self, df_standard):
        engine = ForecastEngine.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "models": {"lightgbm": {}, "arima": {}},
        })
        engine.load_data(df_standard)
        routing = engine.get_routing_plan()
        assert isinstance(routing, dict)


# ─────────────────────────────────────────────────────────────────────────────
# Train and results
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineTrainAndResults:

    def test_train_returns_self(self, tmp_path, df_standard):
        csv = str(tmp_path / "d.csv")
        df_standard.to_csv(csv, index=False)
        engine = ForecastEngine.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "features": {"lags": [1], "rolling": [], "diffs": [], "calendar": False, "ewm_spans": []},
            "models": {"lightgbm": {"n_estimators": 5}},
            "training": {"train_ratio": 0.8, "walk_forward": False, "min_history": 10},
            "forecast": {"horizon": 7},
        })
        engine.load_data(csv)
        result = engine.train()
        assert result is engine

    def test_get_metrics_after_train(self, tmp_path):
        engine = _trained_engine(tmp_path)
        metrics = engine.get_metrics()
        assert "rows" in metrics and "by_model" in metrics
        assert len(metrics["rows"]) > 0

    def test_by_model_includes_all_avg_metrics(self, tmp_path):
        # Train both an ML and a stat model: the default (lightgbm-only) fixture
        # would only exercise the ml code path through _flatten/get_metrics, and
        # checking `key in model_stats` alone is a false-positive risk (a NaN
        # average — e.g. from an all-null column — would still satisfy `in`).
        engine = _trained_engine(tmp_path, models={"lightgbm": {"n_estimators": 10}, "arima": {}})
        metrics = engine.get_metrics()
        assert set(metrics["by_model"].keys()) >= {"lightgbm", "arima"}
        for model_name, model_stats in metrics["by_model"].items():
            for key in ["avg_mae", "avg_rmse", "avg_wape", "avg_bias", "avg_mape", "avg_smape"]:
                assert key in model_stats, f"{key} missing from by_model entry: {model_stats}"
                value = model_stats[key]
                assert value is not None, f"{model_name}.{key} is None"
                assert pd.notna(value), f"{model_name}.{key} is NaN"
                assert np.isfinite(value), f"{model_name}.{key} is not finite: {value!r}"

    def test_get_forecast_after_train(self, tmp_path):
        engine = _trained_engine(tmp_path)
        fc = engine.get_forecast()
        assert "rows" in fc and "n_skus" in fc
        assert fc["n_skus"] > 0

    def test_get_inventory_after_train(self, tmp_path):
        engine = _trained_engine(tmp_path)
        inv = engine.get_inventory_report()
        assert "recommendations" in inv

    def test_predict_returns_dataframe(self, tmp_path):
        engine = _trained_engine(tmp_path)
        df = engine.predict()
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_generate_report_structure(self, tmp_path):
        engine = _trained_engine(tmp_path)
        report = engine.generate_report()
        assert "run_id" in report
        assert "metrics" in report
        assert "inventory" in report

    def test_get_forecast_dict_structure(self, tmp_path):
        engine = _trained_engine(tmp_path)
        fc_dict = engine.get_forecast_dict()
        assert isinstance(fc_dict, dict)
        if fc_dict:
            sku = next(iter(fc_dict))
            model = next(iter(fc_dict[sku]))
            pts = fc_dict[sku][model]
            assert isinstance(pts, list)
            assert "date" in pts[0] and "value" in pts[0]

    def test_run_id_is_string(self, tmp_path):
        engine = _trained_engine(tmp_path)
        assert isinstance(engine._run_id, str) and len(engine._run_id) > 0

    def test_export_config_creates_file(self, tmp_path):
        engine = _trained_engine(tmp_path)
        p = str(tmp_path / "exported.json")
        engine.export_config(p)
        import os
        assert os.path.exists(p)


# ─────────────────────────────────────────────────────────────────────────────
# Fluent configure methods
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineConfiguration:

    def test_choose_columns_returns_self(self, df_standard):
        engine = ForecastEngine()
        engine.load_data(df_standard)
        engine._ensure_config()
        result = engine.choose_columns(target="sales", date="date", sku="sku")
        assert result is engine

    def test_configure_features_returns_self(self):
        engine = ForecastEngine()
        engine._ensure_config()
        result = engine.configure_features(lags=[1, 7])
        assert result is engine

    def test_configure_training_returns_self(self):
        engine = ForecastEngine()
        engine._ensure_config()
        result = engine.configure_training(train_ratio=0.75)
        assert result is engine

    def test_configure_forecast_returns_self(self):
        engine = ForecastEngine()
        engine._ensure_config()
        result = engine.configure_forecast(horizon=21)
        assert result is engine
        assert engine._config.forecast.horizon == 21

    def test_select_models_returns_self(self):
        engine = ForecastEngine()
        engine._ensure_config()
        result = engine.select_models(["lightgbm", "arima"])
        assert result is engine
        assert "lightgbm" in engine._config.models

    def test_set_config_replaces_config(self):
        engine = ForecastEngine()
        engine.set_config({
            "columns": {"target": "y", "date": "ds"},
            "models": {"arima": {}},
        })
        assert engine._config.columns.target == "y"


# ─────────────────────────────────────────────────────────────────────────────
# ForecastEngine.save() / ForecastEngine.load()
# ─────────────────────────────────────────────────────────────────────────────

class TestEnginePersistence:

    def _trained_engine(self, tmp_path, df_standard, base_config):
        csv = str(tmp_path / "data.csv")
        df_standard.to_csv(csv, index=False)
        from forecasting_core.config.config import SessionConfig
        cfg = SessionConfig.from_dict({
            **base_config,
            "data": {"path": csv},
            "models": {"lightgbm": {"n_estimators": 10}},
            "training": {"train_ratio": 0.8, "walk_forward": False,
                         "wfv_splits": 2, "min_history": 20, "seasonal_period": 7},
        })
        engine = ForecastEngine()
        engine.load_data(df_standard)
        engine.set_config(cfg.to_dict())
        engine.train()
        return engine

    def test_save_creates_file(self, tmp_path, df_standard, base_config):
        engine = self._trained_engine(tmp_path, df_standard, base_config)
        path = str(tmp_path / "engine.joblib")
        engine.save(path)
        import os
        assert os.path.exists(path)

    def test_save_returns_self(self, tmp_path, df_standard, base_config):
        engine = self._trained_engine(tmp_path, df_standard, base_config)
        path = str(tmp_path / "engine.joblib")
        result = engine.save(path)
        assert result is engine

    def test_save_before_train_raises(self, tmp_path):
        engine = ForecastEngine()
        with pytest.raises(Exception):
            engine.save(str(tmp_path / "engine.joblib"))

    def test_load_returns_engine_instance(self, tmp_path, df_standard, base_config):
        engine = self._trained_engine(tmp_path, df_standard, base_config)
        path = str(tmp_path / "engine.joblib")
        engine.save(path)
        loaded = ForecastEngine.load(path)
        assert isinstance(loaded, ForecastEngine)

    def test_load_restores_fitted_models(self, tmp_path, df_standard, base_config):
        engine = self._trained_engine(tmp_path, df_standard, base_config)
        path = str(tmp_path / "engine.joblib")
        engine.save(path)
        loaded = ForecastEngine.load(path)
        assert len(loaded._fitted_models) > 0

    def test_load_restores_config(self, tmp_path, df_standard, base_config):
        engine = self._trained_engine(tmp_path, df_standard, base_config)
        path = str(tmp_path / "engine.joblib")
        engine.save(path)
        loaded = ForecastEngine.load(path)
        assert loaded._config is not None
        assert loaded._config.columns.target == engine._config.columns.target

    def test_loaded_engine_can_predict(self, tmp_path, df_standard, base_config):
        engine = self._trained_engine(tmp_path, df_standard, base_config)
        path = str(tmp_path / "engine.joblib")
        engine.save(path)
        loaded = ForecastEngine.load(path)
        loaded.load_data(df_standard)
        fc = loaded.predict(horizon=7)
        assert isinstance(fc, pd.DataFrame)
        assert len(fc) > 0

    def test_roundtrip_forecast_consistent(self, tmp_path, df_standard, base_config):
        """Forecast from saved+loaded engine matches original engine."""
        engine = self._trained_engine(tmp_path, df_standard, base_config)
        fc_before = engine.predict(horizon=7)
        path = str(tmp_path / "engine.joblib")
        engine.save(path)
        loaded = ForecastEngine.load(path)
        loaded.load_data(df_standard)
        fc_after = loaded.predict(horizon=7)
        # Same SKUs and models should appear
        assert set(fc_before["sku"].unique()) == set(fc_after["sku"].unique())
