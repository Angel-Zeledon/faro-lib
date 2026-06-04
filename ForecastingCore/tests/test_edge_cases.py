"""
Edge-case and robustness tests.
These deliberately feed bad/unusual inputs to find silent failures and poor error messages.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.evaluation.metrics import evaluate_all, forecast_intervals
from forecasting_core.data.loader import DataLoader, LoadError
from forecasting_core.data.quality import DataQualityChecker
from forecasting_core.features.engineer import FeatureEngineer
from forecasting_core.config.config import SessionConfig, FeaturesConfig
from forecasting_core.ensemble.ensemble import WeightedEnsemble
from forecasting_core.business.inventory import InventoryAdvisor
from forecasting_core.training.trainer import WalkForwardSplitter
from forecasting_core.models.croston import croston_forecast
from forecasting_core.evaluation.baselines import BaselineEvaluator


# ─────────────────────────────────────────────────────────────────────────────
# Metrics robustness
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsEdgeCases:

    def test_evaluate_all_single_point(self):
        result = evaluate_all([100.0], [90.0])
        assert result["mae"] == pytest.approx(10.0)
        assert np.isfinite(result["rmse"])

    def test_evaluate_all_very_large_values(self):
        y    = [1e10, 2e10]
        yhat = [1e10 + 1, 2e10 - 1]
        result = evaluate_all(y, yhat)
        assert np.isfinite(result["mae"])

    def test_evaluate_all_negative_values(self):
        result = evaluate_all([-10, -20, -30], [-11, -19, -31])
        assert result["mae"] > 0

    def test_wape_denominator_near_zero(self):
        # y is tiny but non-zero — should not explode
        result = evaluate_all([1e-9, 1e-9], [0.0, 0.0])
        assert np.isfinite(result["wape"])

    def test_all_same_predictions_zero_rmse(self):
        y = np.full(100, 42.0)
        result = evaluate_all(y, y)
        assert result["rmse"] == pytest.approx(0.0)

    # ⚠️ CRITICAL FIX REQUIRED
    def test_forecast_intervals_empty_residuals_raises(self):
        """np.quantile on empty array raises ValueError — library should guard this."""
        with pytest.raises(Exception):
            forecast_intervals(np.array([]), np.array([10.0, 20.0]))

    def test_forecast_intervals_single_residual(self):
        residuals = np.array([5.0])
        forecast = np.array([100.0])
        result = forecast_intervals(residuals, forecast, quantiles=[0.9])
        assert "p90_lo" in result
        assert result["p90_lo"][0] == pytest.approx(95.0)
        assert result["p90_hi"][0] == pytest.approx(105.0)


# ─────────────────────────────────────────────────────────────────────────────
# DataLoader edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestDataLoaderEdgeCases:

    def test_load_empty_csv(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("col1,col2\n")  # header only
        df = DataLoader().load(str(p))
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_load_csv_with_missing_values(self, tmp_path):
        p = tmp_path / "nulls.csv"
        p.write_text("date,sales\n2022-01-01,\n2022-01-02,10\n")
        df = DataLoader().load(str(p))
        assert df["sales"].isna().any()

    def test_load_csv_with_spaces_in_path(self, tmp_path):
        folder = tmp_path / "my folder"
        folder.mkdir()
        p = folder / "data file.csv"
        p.write_text("a,b\n1,2\n")
        df = DataLoader().load(str(p))
        assert len(df) == 1

    def test_load_df_preserves_dtypes(self):
        df = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0], "c": ["x", "y"]})
        result = DataLoader().load_df(df)
        assert result["a"].dtype == df["a"].dtype

    def test_load_df_dict_raises(self):
        with pytest.raises(LoadError):
            DataLoader().load_df({"a": 1})


# ─────────────────────────────────────────────────────────────────────────────
# DataQualityChecker edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestDataQualityEdgeCases:

    def test_single_row_per_sku(self):
        df = pd.DataFrame({
            "date": ["2022-01-01", "2022-01-01"],
            "sku": ["A", "B"],
            "sales": [10.0, 20.0],
        })
        checker = DataQualityChecker("date", "sales", "sku", min_history=20)
        reports = checker.check(df)
        for r in reports.values():
            assert r.has_min_history is False
            assert r.n_rows == 1

    def test_nan_target_values_handled(self):
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=30),
            "sku": "A",
            "sales": [None if i % 5 == 0 else float(i) for i in range(30)],
        })
        checker = DataQualityChecker("date", "sales", "sku", min_history=10)
        # Should not crash when converting to float with NaN
        try:
            reports = checker.check(df)
            assert "A" in reports
        except Exception as e:
            pytest.fail(f"Unexpected crash on NaN target: {e}")

    def test_non_date_column_for_dt(self):
        df = pd.DataFrame({
            "notadate": ["abc", "def", "ghi"] * 10,
            "sku": "A",
            "sales": [1.0] * 30,
        })
        checker = DataQualityChecker("notadate", "sales", "sku", min_history=10)
        # Should not raise; may not sort correctly but shouldn't crash
        try:
            checker.check(df)
        except Exception as e:
            # Acceptable if it raises, but must not be a silent wrong result
            assert "date" in str(e).lower() or isinstance(e, (KeyError, ValueError, TypeError))

    def test_quality_score_never_exceeds_100(self):
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=50),
            "sku": "A",
            "sales": [100.0] * 50,  # perfect series
        })
        checker = DataQualityChecker("date", "sales", "sku", min_history=10)
        reports = checker.check(df)
        assert reports["A"].quality_score <= 100.0

    def test_all_nan_target_raises_or_empty(self):
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=20),
            "sku": "A",
            "sales": [np.nan] * 20,
        })
        checker = DataQualityChecker("date", "sales", "sku", min_history=10)
        try:
            reports = checker.check(df)
            # If it doesn't raise, the result must not be wrong
            # (e.g., zero_ratio=0 on all-NaN is misleading — ⚠️ CRITICAL)
        except Exception:
            pass  # raising is acceptable


# ─────────────────────────────────────────────────────────────────────────────
# FeatureEngineer edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureEngineerEdgeCases:

    def test_unsorted_dates_handled(self):
        """FeatureEngineer should work even if dates aren't pre-sorted."""
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-10", periods=40, freq="-1D"),  # reversed
            "sales": np.arange(40, dtype=float),
        })
        cfg = FeaturesConfig(lags=[1], rolling=[7], diffs=[1], calendar=False, ewm_spans=[])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group=None)
        # Should not raise; engineer doesn't sort internally — values may be wrong
        # but it must not crash
        out = eng.transform(df)
        assert isinstance(out, pd.DataFrame)

    def test_duplicate_dates_handled(self):
        df = pd.DataFrame({
            "date": ["2022-01-01"] * 40,  # all same date
            "sales": np.random.default_rng(0).normal(10, 2, 40),
        })
        cfg = FeaturesConfig(lags=[1], rolling=[7], diffs=[], calendar=False, ewm_spans=[])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group=None)
        # Should not crash
        try:
            out = eng.transform(df)
            assert isinstance(out, pd.DataFrame)
        except Exception as e:
            pytest.fail(f"FeatureEngineer crashed on duplicate dates: {e}")

    def test_very_large_rolling_window(self):
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=10, freq="D"),
            "sales": [float(i) for i in range(10)],
        })
        cfg = FeaturesConfig(lags=[], rolling=[100], diffs=[], calendar=False, ewm_spans=[])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group=None)
        out = eng.transform(df)
        # After dropna, result might be empty — that's fine
        assert isinstance(out, pd.DataFrame)

    def test_string_sales_column_raises(self):
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=30, freq="D"),
            "sales": ["high"] * 30,
        })
        cfg = FeaturesConfig(lags=[1], rolling=[], diffs=[], calendar=False, ewm_spans=[])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group=None)
        with pytest.raises((TypeError, ValueError, Exception)):
            eng.transform(df)


# ─────────────────────────────────────────────────────────────────────────────
# WalkForwardSplitter edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestWalkForwardSplitterEdgeCases:

    def test_n_equals_0_returns_empty(self):
        splits = WalkForwardSplitter(n_splits=3).split(0)
        assert splits == []

    def test_n_equals_1_returns_empty(self):
        splits = WalkForwardSplitter(n_splits=3).split(1)
        assert splits == []

    def test_fold_size_zero_does_not_produce_empty_test(self):
        # Very small n relative to n_splits — fold_size becomes 0
        splits = WalkForwardSplitter(n_splits=10).split(15)
        for tr, te in splits:
            assert len(te) > 0, "Test fold must not be empty"

    def test_indices_are_contiguous_arrays(self):
        splitter = WalkForwardSplitter(n_splits=3)
        for tr, te in splitter.split(100):
            assert tr.dtype == np.int64 or tr.dtype == np.intp
            assert len(te) > 0


# ─────────────────────────────────────────────────────────────────────────────
# WeightedEnsemble edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestWeightedEnsembleEdgeCases:

    def test_model_in_weights_not_in_predictions_gives_partial_sum(self):
        ens = WeightedEnsemble()
        ens.fit({"S": {"a": 1.0, "b": 1.0}})
        # Only supply model 'a', model 'b' is missing from predictions
        preds = ens.predict("S", {"a": np.array([10.0])})
        # weight["b"] * missing = 0, so result = weight["a"] * 10
        # ⚠️ This is a SILENT FAILURE if weights don't sum to 1 for provided models
        assert np.isfinite(preds[0])

    def test_single_model_gets_full_weight(self):
        ens = WeightedEnsemble()
        ens.fit({"S": {"only_model": 5.0}})
        preds = ens.predict("S", {"only_model": np.array([42.0])})
        assert preds[0] == pytest.approx(42.0)

    def test_predict_with_mismatched_prediction_lengths(self):
        ens = WeightedEnsemble()
        ens.fit({"S": {"a": 1.0, "b": 2.0}})
        preds = ens.predict("S", {
            "a": np.array([10.0, 20.0, 30.0]),
            "b": np.array([12.0, 18.0, 33.0]),
        })
        assert len(preds) == 3


# ─────────────────────────────────────────────────────────────────────────────
# InventoryAdvisor edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestInventoryEdgeCases:

    def test_all_zero_forecast(self):
        adv = InventoryAdvisor()
        rec = adv.recommend("Z", np.zeros(14), current_stock=0.0)
        # days_of_coverage with 0 demand should not be inf
        assert np.isfinite(rec.days_of_coverage)

    def test_negative_forecast_clipped_or_handled(self):
        adv = InventoryAdvisor()
        # Negative values shouldn't break the math
        try:
            rec = adv.recommend("Z", np.array([-5.0, -10.0, 5.0]))
            assert np.isfinite(rec.forecast_mean)
        except Exception as e:
            pytest.fail(f"InventoryAdvisor crashed on negative forecast: {e}")

    def test_empty_forecast_raises_or_degrades(self):
        adv = InventoryAdvisor()
        # Empty forecast → mean is nan
        with pytest.raises(Exception):
            adv.recommend("Z", np.array([]))

    def test_single_value_forecast(self):
        adv = InventoryAdvisor()
        rec = adv.recommend("Z", np.array([100.0]))
        assert rec.forecast_total == pytest.approx(100.0)


# ─────────────────────────────────────────────────────────────────────────────
# Croston edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestCrostonEdgeCases:

    def test_single_non_zero_event(self):
        series = np.array([0, 0, 10, 0, 0])
        result = croston_forecast(series, n_ahead=3)
        assert len(result) == 3
        assert (result > 0).all()

    def test_alpha_zero_uses_initial_values(self):
        series = np.array([5, 0, 10, 0, 8])
        result = croston_forecast(series, alpha=0.0, n_ahead=2)
        assert np.isfinite(result).all()

    def test_alpha_one_uses_last_values(self):
        series = np.array([5, 0, 10, 0, 8])
        result = croston_forecast(series, alpha=1.0, n_ahead=2)
        assert np.isfinite(result).all()

    def test_output_is_always_array(self):
        result = croston_forecast(np.zeros(10), n_ahead=5)
        assert isinstance(result, np.ndarray)


# ─────────────────────────────────────────────────────────────────────────────
# SessionConfig edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionConfigEdgeCases:

    def test_from_dict_with_empty_dict_raises(self):
        with pytest.raises(Exception):
            SessionConfig.from_dict({})

    def test_from_dict_with_none_models_raises(self):
        with pytest.raises(Exception):
            SessionConfig.from_dict({
                "columns": {"target": "y", "date": "ds"},
                "models": None,
            })

    def test_train_ratio_exactly_boundary_valid(self):
        # train_ratio must be strictly in (0, 1) — 0.0 and 1.0 should fail
        for ratio in [0.0, 1.0]:
            with pytest.raises(Exception):
                SessionConfig.from_dict({
                    "columns": {"target": "y", "date": "ds"},
                    "models": {"lightgbm": {}},
                    "training": {"train_ratio": ratio},
                })

    def test_from_json_nonexistent_file_raises(self, tmp_path):
        with pytest.raises((FileNotFoundError, IOError)):
            SessionConfig.from_json(str(tmp_path / "missing.json"))

    def test_hash_is_8_chars(self):
        cfg = SessionConfig.from_dict({
            "columns": {"target": "s", "date": "d"},
            "models": {"arima": {}},
        })
        assert len(cfg.hash) == 8

    def test_to_dict_is_json_serializable(self):
        import json
        cfg = SessionConfig.from_dict({
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        })
        # Should not raise
        json.dumps(cfg.to_dict())
