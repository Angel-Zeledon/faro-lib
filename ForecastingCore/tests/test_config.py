"""
Tests for forecasting_core.config.config — SessionConfig, sub-configs, validation.
"""

import json
import pytest

from forecasting_core.config.config import (
    SessionConfig, ConfigError,
    DataConfig, ColumnsConfig, FeaturesConfig,
    TrainingConfig, ForecastConfig, BusinessConfig, ModelRoutingConfig,
)


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigDefaults:

    def test_default_name(self):
        cfg = SessionConfig()
        assert cfg.name == "session_1"

    def test_default_models_has_lightgbm(self):
        cfg = SessionConfig()
        assert "lightgbm" in cfg.models

    def test_default_training_walk_forward(self):
        cfg = SessionConfig()
        assert cfg.training.walk_forward is True

    def test_default_forecast_horizon(self):
        cfg = SessionConfig()
        assert cfg.forecast.horizon == 14

    def test_default_service_level(self):
        cfg = SessionConfig()
        assert cfg.business.service_level == 0.95


# ─────────────────────────────────────────────────────────────────────────────
# from_dict — happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigFromDict:

    def _valid_dict(self):
        return {
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "models": {"lightgbm": {"n_estimators": 100}},
            "training": {"train_ratio": 0.8},
            "forecast": {"horizon": 14},
        }

    def test_basic_from_dict(self):
        cfg = SessionConfig.from_dict(self._valid_dict())
        assert cfg.columns.target == "sales"
        assert cfg.columns.date == "date"

    def test_model_hyperparams_preserved(self):
        d = self._valid_dict()
        cfg = SessionConfig.from_dict(d)
        assert cfg.models["lightgbm"]["n_estimators"] == 100

    def test_unknown_keys_in_sub_config_ignored(self):
        d = self._valid_dict()
        d["training"]["unknown_param"] = 999
        # Should not raise
        cfg = SessionConfig.from_dict(d)
        assert not hasattr(cfg.training, "unknown_param")

    def test_multi_model_config(self):
        d = self._valid_dict()
        d["models"] = {"lightgbm": {}, "xgboost": {}, "arima": {}}
        cfg = SessionConfig.from_dict(d)
        assert len(cfg.models) == 3

    def test_name_from_dict(self):
        d = self._valid_dict()
        d["name"] = "my_session"
        cfg = SessionConfig.from_dict(d)
        assert cfg.name == "my_session"


# ─────────────────────────────────────────────────────────────────────────────
# Validation — ConfigError cases
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigValidation:

    def _minimal(self):
        return {
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        }

    def test_missing_target_raises(self):
        d = self._minimal()
        d["columns"]["target"] = ""
        with pytest.raises(ConfigError, match="target"):
            SessionConfig.from_dict(d)

    def test_missing_date_raises(self):
        d = self._minimal()
        d["columns"]["date"] = ""
        with pytest.raises(ConfigError, match="date"):
            SessionConfig.from_dict(d)

    def test_empty_models_raises(self):
        d = self._minimal()
        d["models"] = {}
        with pytest.raises(ConfigError, match="model"):
            SessionConfig.from_dict(d)

    def test_invalid_train_ratio_above_1(self):
        d = self._minimal()
        d["training"] = {"train_ratio": 1.5}
        with pytest.raises(ConfigError, match="train_ratio"):
            SessionConfig.from_dict(d)

    def test_invalid_train_ratio_zero(self):
        d = self._minimal()
        d["training"] = {"train_ratio": 0.0}
        with pytest.raises(ConfigError, match="train_ratio"):
            SessionConfig.from_dict(d)

    def test_invalid_horizon_zero(self):
        d = self._minimal()
        d["forecast"] = {"horizon": 0}
        with pytest.raises(ConfigError, match="horizon"):
            SessionConfig.from_dict(d)

    def test_invalid_horizon_negative(self):
        d = self._minimal()
        d["forecast"] = {"horizon": -5}
        with pytest.raises(ConfigError, match="horizon"):
            SessionConfig.from_dict(d)

    def test_validate_returns_self(self):
        cfg = SessionConfig()
        cfg.columns.target = "sales"
        cfg.columns.date = "date"
        result = cfg.validate()
        assert result is cfg


# ─────────────────────────────────────────────────────────────────────────────
# Serialization: to_dict / to_json / from_json
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigSerialization:

    def test_to_dict_roundtrip(self):
        d = {
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "models": {"lightgbm": {}},
        }
        cfg = SessionConfig.from_dict(d)
        d2 = cfg.to_dict()
        assert d2["columns"]["target"] == "sales"
        assert "lightgbm" in d2["models"]

    def test_to_json_creates_file(self, tmp_path):
        d = {
            "columns": {"target": "y", "date": "ds"},
            "models": {"arima": {}},
        }
        cfg = SessionConfig.from_dict(d)
        p = str(tmp_path / "cfg.json")
        cfg.to_json(p)
        loaded = json.load(open(p))
        assert loaded["columns"]["target"] == "y"

    def test_from_json_roundtrip(self, tmp_path):
        d = {
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        }
        cfg = SessionConfig.from_dict(d)
        p = str(tmp_path / "cfg.json")
        cfg.to_json(p)
        cfg2 = SessionConfig.from_json(p)
        assert cfg2.columns.target == "sales"

    def test_hash_is_deterministic(self):
        d = {
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        }
        h1 = SessionConfig.from_dict(d).hash
        h2 = SessionConfig.from_dict(d).hash
        assert h1 == h2

    def test_hash_changes_with_different_config(self):
        d1 = {"columns": {"target": "sales", "date": "date"}, "models": {"lightgbm": {}}}
        d2 = {"columns": {"target": "units", "date": "date"}, "models": {"lightgbm": {}}}
        h1 = SessionConfig.from_dict(d1).hash
        h2 = SessionConfig.from_dict(d2).hash
        assert h1 != h2

    def test_hash_length_is_8(self):
        cfg = SessionConfig()
        cfg.columns.target = "s"
        cfg.columns.date = "d"
        assert len(cfg.hash) == 8


# ─────────────────────────────────────────────────────────────────────────────
# schema()
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigSchema:

    def test_schema_returns_dict(self):
        s = SessionConfig.schema()
        assert isinstance(s, dict)

    def test_schema_has_training_section(self):
        s = SessionConfig.schema()
        assert "training" in s
        assert "train_ratio" in s["training"]

    def test_schema_has_available_models(self):
        s = SessionConfig.schema()
        assert "available_models" in s
        assert "lightgbm" in s["available_models"]

    def test_schema_tuning_keys_present(self):
        s = SessionConfig.schema()
        assert "tuning" in s["training"]
        assert "tuning_trials" in s["training"]
