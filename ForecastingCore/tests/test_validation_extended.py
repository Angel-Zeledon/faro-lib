"""
Tests for the remaining validation modules:
  - validate_data       (data_validator.py)
  - detect_leakage      (leakage.py)
  - check_model_compatibility (compatibility.py)
  - PartialResultCollector, FallbackChain (pipeline_resilience.py)
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.validation.data_validator import validate_data
from forecasting_core.validation.leakage import detect_leakage
from forecasting_core.validation.compatibility import check_model_compatibility
from forecasting_core.validation.pipeline_resilience import (
    PartialResultCollector,
    FallbackChain,
    naive_forecast,
    seasonal_naive_forecast,
)
from forecasting_core.validation.modes import ValidationMode
from forecasting_core.validation.exceptions import PipelineError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n=60, skus=("A",), constant=False, all_nan=False, zero_ratio=0.0):
    rng = np.random.default_rng(0)
    rows = []
    for sku in skus:
        for i, d in enumerate(pd.date_range("2022-01-01", periods=n, freq="D")):
            if all_nan:
                val = np.nan
            elif constant:
                val = 5.0
            elif rng.random() < zero_ratio:
                val = 0.0
            else:
                val = float(rng.normal(50, 5))
            rows.append({"date": d, "sku": sku, "sales": val})
    return pd.DataFrame(rows)


def _valid_cfg(**kwargs):
    base = {
        "dt": "date",
        "target": "sales",
        "group_id": "sku",
        "min_history": 20,
        "prediction_horizon": 7,
        "train_ratio": 0.8,
        "models": {"lightgbm": {}},
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# validate_data
# ---------------------------------------------------------------------------

class TestValidateData:

    def test_valid_df_returns_valid(self):
        df = _make_df(n=60)
        result = validate_data(df, _valid_cfg())
        assert result.valid is True

    def test_missing_target_column_invalid(self):
        df = _make_df(n=60)
        result = validate_data(df, _valid_cfg(target="nonexistent"))
        assert not result.valid
        assert any(e.error_id == "MISSING_TARGET" for e in result.errors)

    def test_insufficient_history_strict_invalid(self):
        df = _make_df(n=5)  # 5 rows < 20 min_history
        result = validate_data(df, _valid_cfg(), mode=ValidationMode.STRICT)
        assert not result.valid
        assert any(e.error_id == "INSUFFICIENT_HISTORY" for e in result.errors)

    def test_insufficient_history_warning_mode_produces_warning(self):
        df = _make_df(n=5)
        result = validate_data(df, _valid_cfg(), mode=ValidationMode.WARNING)
        assert len(result.warnings) > 0

    def test_all_nan_target_invalid(self):
        df = _make_df(n=60, all_nan=True)
        result = validate_data(df, _valid_cfg())
        assert not result.valid
        assert any(e.error_id == "ALL_NAN_TARGET" for e in result.errors)

    def test_constant_target_strict_invalid(self):
        df = _make_df(n=60, constant=True)
        result = validate_data(df, _valid_cfg(), mode=ValidationMode.STRICT)
        assert not result.valid
        assert any(e.error_id == "CONSTANT_TARGET" for e in result.errors)

    def test_constant_target_warning_mode_ok(self):
        df = _make_df(n=60, constant=True)
        result = validate_data(df, _valid_cfg(), mode=ValidationMode.WARNING)
        assert result.valid  # warning, not error
        assert len(result.warnings) > 0

    def test_negative_values_produces_warning(self):
        df = _make_df(n=60)
        df.loc[df.index[:5], "sales"] = -10.0
        result = validate_data(df, _valid_cfg())
        # Negative values → informational warning, should not fail
        assert len(result.warnings) > 0

    def test_duplicate_timestamps_auto_fix_records_correction(self):
        df = _make_df(n=40)
        # Inject a duplicate date for SKU A
        dup_row = df[df["sku"] == "A"].iloc[0:1].copy()
        df = pd.concat([df, dup_row], ignore_index=True)
        result = validate_data(df, _valid_cfg(), mode=ValidationMode.AUTO_FIX)
        assert any(c["action"] == "drop_duplicate_timestamps" for c in result.corrections)

    def test_multi_sku_both_validated(self):
        df = _make_df(n=60, skus=("A", "B"))
        result = validate_data(df, _valid_cfg())
        assert result.valid

    def test_no_group_col_validates_whole_df(self):
        df = _make_df(n=60)
        cfg = _valid_cfg(group_id=None)
        result = validate_data(df, cfg)
        assert result.valid

    def test_custom_min_history_threshold(self):
        df = _make_df(n=30)
        result = validate_data(df, _valid_cfg(min_history=50), mode=ValidationMode.STRICT)
        assert not result.valid


# ---------------------------------------------------------------------------
# detect_leakage
# ---------------------------------------------------------------------------

class TestDetectLeakage:

    def test_no_leakage_clean_df(self):
        df = _make_df(n=60)
        df["lag_1"] = df["sales"].shift(1)
        result = detect_leakage(df, _valid_cfg(), feature_columns=["lag_1"])
        assert result.valid

    def test_target_as_feature_is_leakage(self):
        df = _make_df(n=60)
        result = detect_leakage(
            df, _valid_cfg(), feature_columns=["sales"]  # raw target as feature
        )
        assert not result.valid
        assert any(e.error_id == "TARGET_FEATURE_LEAKAGE" for e in result.errors)

    def test_unsorted_dates_produces_warning(self):
        df = _make_df(n=60)
        df = df.iloc[::-1].reset_index(drop=True)  # reverse date order
        result = detect_leakage(df, _valid_cfg())
        assert any(w.error_id == "UNSORTED_DATES" for w in result.warnings)

    def test_sorted_dates_no_warning(self):
        df = _make_df(n=60)  # already sorted ascending
        result = detect_leakage(df, _valid_cfg())
        assert not any(w.error_id == "UNSORTED_DATES" for w in result.warnings)

    def test_near_perfect_correlation_flagged(self):
        df = _make_df(n=100)
        # Create a feature that is almost identical to target (leak)
        df["leaky_feat"] = df["sales"] * 1.0001
        result = detect_leakage(
            df, _valid_cfg(), feature_columns=["leaky_feat"]
        )
        assert not result.valid
        assert any(e.error_id == "NEAR_PERFECT_CORRELATION" for e in result.errors)

    def test_valid_feature_no_correlation_error(self):
        df = _make_df(n=100)
        rng = np.random.default_rng(99)
        df["random_feat"] = rng.normal(0, 1, len(df))
        result = detect_leakage(
            df, _valid_cfg(), feature_columns=["random_feat"]
        )
        assert not any(e.error_id == "NEAR_PERFECT_CORRELATION" for e in result.errors)

    def test_no_feature_columns_no_leakage_errors(self):
        df = _make_df(n=60)
        result = detect_leakage(df, _valid_cfg(), feature_columns=None)
        assert result.valid

    def test_missing_dt_col_no_crash(self):
        df = _make_df(n=60)
        cfg = _valid_cfg(dt="nonexistent_col")
        result = detect_leakage(df, cfg)
        assert isinstance(result.valid, bool)


# ---------------------------------------------------------------------------
# check_model_compatibility
# ---------------------------------------------------------------------------

class TestCheckModelCompatibility:

    def test_valid_lgb_dense_series(self):
        df = _make_df(n=60)
        cfg = _valid_cfg(models={"lightgbm": {}}, min_history=20)
        result = check_model_compatibility(df, cfg, mode=ValidationMode.STRICT)
        assert result.valid

    def test_ets_negative_values_incompatible(self):
        df = _make_df(n=60)
        df.loc[df.index[:10], "sales"] = -5.0
        cfg = _valid_cfg(models={"ets": {}}, min_history=20)
        result = check_model_compatibility(df, cfg, mode=ValidationMode.STRICT)
        assert not result.valid

    def test_ets_positive_series_compatible(self):
        df = _make_df(n=60)
        df["sales"] = df["sales"].abs()
        cfg = _valid_cfg(models={"ets": {}}, min_history=20)
        result = check_model_compatibility(df, cfg, mode=ValidationMode.STRICT)
        assert result.valid

    def test_lstm_requires_min_rows(self):
        df = _make_df(n=10)  # only 10 rows, LSTM needs 14+7=21 default
        cfg = _valid_cfg(models={"lstm": {}}, min_history=5, prediction_horizon=7)
        result = check_model_compatibility(df, cfg, mode=ValidationMode.STRICT)
        assert not result.valid
        assert any(e.error_id == "MODEL_INCOMPATIBLE" for e in result.errors)

    def test_croston_on_dense_series_warns(self):
        df = _make_df(n=60)  # 0% zeros → dense
        cfg = _valid_cfg(models={"croston": {}}, min_history=20)
        result = check_model_compatibility(df, cfg, mode=ValidationMode.STRICT)
        # Croston on dense series (zero_ratio=0 < 0.2 default threshold*0.5)
        assert not result.valid

    def test_auto_fix_mode_produces_correction(self):
        df = _make_df(n=60)
        df.loc[df.index[:10], "sales"] = -5.0
        cfg = _valid_cfg(models={"ets": {}})
        result = check_model_compatibility(df, cfg, mode=ValidationMode.AUTO_FIX)
        assert result.valid  # no hard errors in auto-fix
        assert any(c["action"] == "skip_model_for_sku" for c in result.corrections)

    def test_missing_target_returns_valid(self):
        df = _make_df(n=60)
        cfg = _valid_cfg(target="nonexistent")
        result = check_model_compatibility(df, cfg)
        assert result.valid  # guard: returns valid if target missing

    def test_ml_model_needs_30_rows(self):
        df = _make_df(n=15)  # only 15 rows, ML needs 30
        cfg = _valid_cfg(models={"lightgbm": {}}, min_history=5)
        result = check_model_compatibility(df, cfg, mode=ValidationMode.STRICT)
        assert not result.valid


# ---------------------------------------------------------------------------
# PartialResultCollector
# ---------------------------------------------------------------------------

class TestPartialResultCollector:

    def test_record_success_increases_successes(self):
        col = PartialResultCollector()
        col.record_success("A", "lgb", {"mae": 2.0})
        assert len(col.successes) == 1

    def test_record_failure_increases_failures(self):
        col = PartialResultCollector()
        col.record_failure("B", "lgb", ValueError("oops"))
        assert len(col.failures) == 1

    def test_success_rate_all_success(self):
        col = PartialResultCollector()
        col.record_success("A", "lgb", {"mae": 1.0})
        col.record_success("B", "lgb", {"mae": 2.0})
        assert col.success_rate() == pytest.approx(1.0)

    def test_success_rate_empty_returns_zero(self):
        col = PartialResultCollector()
        assert col.success_rate() == 0.0

    def test_success_rate_mixed(self):
        col = PartialResultCollector()
        col.record_success("A", "lgb", {"mae": 1.0})
        col.record_failure("B", "lgb", ValueError("bad"))
        assert col.success_rate() == pytest.approx(0.5)

    def test_summary_keys(self):
        col = PartialResultCollector()
        col.record_success("A", "lgb", {"mae": 1.0})
        col.record_failure("B", "lgb", RuntimeError("err"))
        s = col.summary()
        assert "total" in s and "succeeded" in s and "failed" in s
        assert "success_rate" in s and "failed_skus" in s

    def test_summary_failed_skus_correct(self):
        col = PartialResultCollector()
        col.record_failure("BAD_SKU", "lgb", ValueError("err"))
        s = col.summary()
        assert "BAD_SKU" in s["failed_skus"]

    def test_to_results_dict_format(self):
        col = PartialResultCollector()
        col.record_success("A", "lgb", {"mae": 2.5, "rmse": 3.1})
        d = col.to_results_dict()
        assert "lgb_A" in d
        assert d["lgb_A"]["mae"] == 2.5

    def test_fail_fast_threshold_raises_pipeline_error(self):
        col = PartialResultCollector(fail_fast_threshold=0.5)
        col.record_success("A", "lgb", {"mae": 1.0})
        with pytest.raises(PipelineError):
            col.record_failure("B", "lgb", ValueError("err"))
            col.record_failure("C", "lgb", ValueError("err"))

    def test_threshold_1_never_raises(self):
        col = PartialResultCollector(fail_fast_threshold=1.0)
        for i in range(10):
            col.record_failure(f"SKU_{i}", "lgb", ValueError("err"))
        # Should not raise
        assert len(col.failures) == 10


# ---------------------------------------------------------------------------
# FallbackChain
# ---------------------------------------------------------------------------

class TestFallbackChain:

    def test_first_model_used_when_succeeds(self):
        chain = FallbackChain(["model_a", "model_b"])
        train_fns = {
            "model_a": lambda: {"mae": 1.0},
            "model_b": lambda: {"mae": 5.0},
        }
        model_used, metrics = chain.run("SKU_X", train_fns)
        assert model_used == "model_a"

    def test_fallback_to_second_when_first_fails(self):
        chain = FallbackChain(["model_a", "model_b"])

        def fail():
            raise RuntimeError("model_a broken")

        train_fns = {
            "model_a": fail,
            "model_b": lambda: {"mae": 3.0},
        }
        model_used, metrics = chain.run("SKU_X", train_fns)
        assert model_used == "model_b"
        assert metrics["mae"] == 3.0

    def test_all_models_fail_raises_runtime_error(self):
        chain = FallbackChain(["model_a", "model_b"])

        def fail():
            raise RuntimeError("broken")

        with pytest.raises(RuntimeError, match="All models in fallback chain failed"):
            chain.run("SKU_X", {"model_a": fail, "model_b": fail})

    def test_model_not_in_train_fns_skipped(self):
        chain = FallbackChain(["missing_model", "model_b"])
        train_fns = {"model_b": lambda: {"mae": 2.0}}
        model_used, _ = chain.run("SKU_X", train_fns)
        assert model_used == "model_b"

    def test_empty_train_fns_raises(self):
        chain = FallbackChain(["model_a"])
        with pytest.raises(RuntimeError):
            chain.run("SKU_X", {})

    def test_default_chain_used_when_none_provided(self):
        from forecasting_core.validation.pipeline_resilience import DEFAULT_FALLBACK_CHAIN
        chain = FallbackChain()
        assert chain.chain == DEFAULT_FALLBACK_CHAIN

    def test_empty_metrics_tries_next(self):
        # A callable returning {} is treated as "not successful" → try next
        chain = FallbackChain(["model_a", "model_b"])
        train_fns = {
            "model_a": lambda: {},     # empty dict → not truthy
            "model_b": lambda: {"mae": 4.0},
        }
        model_used, metrics = chain.run("SKU_X", train_fns)
        assert model_used == "model_b"


# ---------------------------------------------------------------------------
# naive_forecast / seasonal_naive_forecast
# ---------------------------------------------------------------------------

class TestFallbackForecasts:

    def test_naive_repeats_last_value(self):
        series = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        preds = naive_forecast(series, 3)
        assert (preds == 5.0).all()
        assert len(preds) == 3

    def test_naive_empty_series_returns_zeros(self):
        preds = naive_forecast(np.array([]), 3)
        assert (preds == 0.0).all()

    def test_seasonal_naive_repeats_season(self):
        series = np.arange(14, dtype=float)
        preds = seasonal_naive_forecast(series, 7, period=7)
        assert len(preds) == 7
        # step 0: series[14-7+0%7] = series[7] = 7.0
        assert preds[0] == pytest.approx(7.0)

    def test_seasonal_naive_short_series_no_crash(self):
        series = np.array([5.0, 6.0, 7.0])
        preds = seasonal_naive_forecast(series, 3, period=7)
        assert len(preds) == 3
        assert np.isfinite(preds).all()
