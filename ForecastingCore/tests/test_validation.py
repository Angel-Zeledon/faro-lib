"""
Tests for forecasting_core.validation: schema, semantic, modes, exceptions.
"""

import pytest
import pandas as pd
import numpy as np

from forecasting_core.validation.schema import validate_schema
from forecasting_core.validation.semantic import validate_semantic
from forecasting_core.validation.modes import ValidationMode, ValidationResult
from forecasting_core.validation.exceptions import (
    ForecastLibraryError, SchemaValidationError, SemanticValidationError,
    DataValidationError, InsufficientDataError, DataLeakageError,
    CompatibilityError, ModelTrainingError, SKUTrainingError,
    ConfigurationError, PipelineError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Exception hierarchy
# ─────────────────────────────────────────────────────────────────────────────

class TestExceptionHierarchy:

    def test_all_errors_inherit_base(self):
        for cls in [SchemaValidationError, SemanticValidationError,
                    DataValidationError, InsufficientDataError,
                    CompatibilityError, ModelTrainingError,
                    ConfigurationError, PipelineError]:
            assert issubclass(cls, ForecastLibraryError)

    def test_sku_training_error_stores_context(self):
        cause = ValueError("bad fit")
        err = SKUTrainingError(sku="SKU_001", model="lgb", cause=cause)
        assert err.sku == "SKU_001"
        assert err.model == "lgb"
        assert err.cause is cause
        assert "SKU_001" in str(err)
        assert "lgb" in str(err)

    def test_to_dict_has_required_keys(self):
        err = SchemaValidationError("bad config", error_id="TEST", suggestions=["fix it"])
        d = err.to_dict()
        assert "error_id" in d and "message" in d and "suggestions" in d

    def test_default_error_id_is_class_name(self):
        err = ConfigurationError("oops")
        assert err.error_id == "ConfigurationError"

    def test_insufficient_data_inherits_data_validation(self):
        err = InsufficientDataError("too few rows")
        assert isinstance(err, DataValidationError)

    def test_data_leakage_inherits_data_validation(self):
        err = DataLeakageError("leakage")
        assert isinstance(err, DataValidationError)

    def test_severity_defaults_to_error(self):
        err = ForecastLibraryError("msg")
        assert err.severity == "error"


# ─────────────────────────────────────────────────────────────────────────────
# validate_schema
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateSchema:

    def _valid_config(self):
        return {
            "data": "sales.csv",
            "dt": "date",
            "target": "sales",
            "group_id": "sku",
            "models": {"lightgbm": {}},
            "features": {"lags": [1, 7]},
            "train_ratio": 0.8,
            "prediction_horizon": 14,
        }

    def test_valid_config_returns_valid(self):
        result = validate_schema(self._valid_config())
        assert result.valid is True
        assert len(result.errors) == 0

    def test_missing_required_keys(self):
        for key in ["data", "dt", "target", "group_id", "models", "features"]:
            cfg = self._valid_config()
            del cfg[key]
            result = validate_schema(cfg, ValidationMode.STRICT)
            assert not result.valid, f"Missing '{key}' should fail"
            assert any(e.error_id == "MISSING_KEY" for e in result.errors)

    def test_train_ratio_out_of_range(self):
        cfg = self._valid_config()
        cfg["train_ratio"] = 1.5
        result = validate_schema(cfg, ValidationMode.STRICT)
        assert not result.valid
        assert any(e.error_id == "OUT_OF_RANGE" for e in result.errors)

    def test_train_ratio_auto_fix_clamps(self):
        cfg = self._valid_config()
        cfg["train_ratio"] = 1.5
        result = validate_schema(cfg, ValidationMode.AUTO_FIX)
        assert result.valid
        assert cfg["train_ratio"] <= 0.95

    def test_wrong_type_train_ratio_strict(self):
        cfg = self._valid_config()
        cfg["train_ratio"] = "high"
        result = validate_schema(cfg, ValidationMode.STRICT)
        assert not result.valid
        assert any(e.error_id == "WRONG_TYPE" for e in result.errors)

    def test_empty_models_dict_invalid(self):
        cfg = self._valid_config()
        cfg["models"] = {}
        result = validate_schema(cfg, ValidationMode.STRICT)
        assert not result.valid

    def test_warning_mode_collects_not_raises(self):
        cfg = self._valid_config()
        cfg["train_ratio"] = 1.5
        result = validate_schema(cfg, ValidationMode.WARNING)
        # In WARNING mode, out-of-range becomes a warning, not error
        assert len(result.warnings) > 0

    def test_result_to_dict_serializable(self):
        result = validate_schema(self._valid_config())
        d = result.to_dict()
        assert "valid" in d and "errors" in d and "warnings" in d


# ─────────────────────────────────────────────────────────────────────────────
# validate_semantic
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateSemantic:

    def _make_df(self, n=60):
        return pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=n, freq="D"),
            "sku":  ["A"] * n,
            "sales": np.random.default_rng(0).normal(50, 5, n),
        })

    def _valid_cfg(self):
        return {"dt": "date", "target": "sales", "group_id": "sku",
                "features": {"lags": [1, 7]}, "train_ratio": 0.8, "prediction_horizon": 14}

    def test_valid_config_returns_valid(self):
        result = validate_semantic(self._valid_cfg(), self._make_df())
        assert result.valid is True

    def test_missing_date_column(self):
        cfg = self._valid_cfg()
        cfg["dt"] = "nonexistent_date"
        result = validate_semantic(cfg, self._make_df(), ValidationMode.STRICT)
        assert not result.valid
        assert any(e.error_id == "COLUMN_NOT_FOUND" for e in result.errors)

    def test_missing_target_column(self):
        cfg = self._valid_cfg()
        cfg["target"] = "not_here"
        result = validate_semantic(cfg, self._make_df(), ValidationMode.STRICT)
        assert not result.valid

    def test_non_numeric_target_strict(self):
        df = self._make_df()
        df["sales"] = df["sales"].astype(str)  # make it string
        result = validate_semantic(self._valid_cfg(), df, ValidationMode.STRICT)
        assert not result.valid
        assert any(e.error_id == "NON_NUMERIC_TARGET" for e in result.errors)

    def test_non_numeric_target_auto_fix(self):
        df = self._make_df()
        df["sales"] = df["sales"].astype(str)
        result = validate_semantic(self._valid_cfg(), df, ValidationMode.AUTO_FIX)
        assert result.valid
        assert any(c["action"] == "cast_target_to_numeric" for c in result.corrections)

    def test_unparseable_dates_detected(self):
        # Build a fresh string-typed date column so pandas doesn't reject the assignment
        df = self._make_df()
        df = df.assign(date="not-a-date")  # string column, not datetime
        result = validate_semantic(self._valid_cfg(), df, ValidationMode.STRICT)
        assert not result.valid

    def test_large_lags_warning(self):
        cfg = self._valid_cfg()
        cfg["features"] = {"lags": [50]}  # 50 > 80% of 60 rows
        result = validate_semantic(cfg, self._make_df(n=60), ValidationMode.WARNING)
        # Should produce a warning about lags being too large
        assert len(result.warnings) > 0 or not result.valid


# ─────────────────────────────────────────────────────────────────────────────
# ValidationResult
# ─────────────────────────────────────────────────────────────────────────────

class TestValidationResult:

    def test_raise_if_invalid_raises_first_error(self):
        err = SchemaValidationError("bad")
        result = ValidationResult(valid=False, errors=[err])
        with pytest.raises(SchemaValidationError):
            result.raise_if_invalid()

    def test_raise_if_invalid_does_nothing_when_valid(self):
        result = ValidationResult(valid=True)
        result.raise_if_invalid()  # should not raise

    def test_merge_combines_errors(self):
        r1 = ValidationResult(valid=False, errors=[SchemaValidationError("e1")])
        r2 = ValidationResult(valid=True, warnings=[SemanticValidationError("w1")])
        merged = r1.merge(r2)
        assert len(merged.errors) == 1
        assert len(merged.warnings) == 1
        assert merged.valid is False  # False AND True = False

    def test_merge_both_valid(self):
        r1 = ValidationResult(valid=True)
        r2 = ValidationResult(valid=True)
        merged = r1.merge(r2)
        assert merged.valid is True
