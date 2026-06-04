"""Tests for forecasting_core.monitoring.drift: psi, psi_level, ks_test, DriftDetector."""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.monitoring.drift import (
    psi, psi_level, ks_test, DriftDetector, PSI_THRESHOLDS,
)


# ---------------------------------------------------------------------------
# psi()
# ---------------------------------------------------------------------------

class TestPSI:

    def test_identical_distributions_near_zero(self):
        rng = np.random.default_rng(0)
        data = rng.normal(0, 1, 200)
        result = psi(data, data)
        assert result < 0.01

    def test_very_different_distributions_large(self):
        rng = np.random.default_rng(1)
        ref = rng.normal(0, 1, 200)
        cur = rng.normal(10, 1, 200)  # completely shifted distribution
        result = psi(ref, cur)
        assert result > PSI_THRESHOLDS["significant"]

    def test_returns_float(self):
        result = psi([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
        assert isinstance(result, float)

    def test_non_negative(self):
        rng = np.random.default_rng(2)
        ref = rng.normal(0, 1, 100)
        cur = rng.normal(0.5, 1.2, 100)
        assert psi(ref, cur) >= 0.0

    def test_single_element_does_not_raise(self):
        # Edge case: very small arrays
        result = psi([1.0], [2.0])
        assert np.isfinite(result)

    def test_custom_n_bins(self):
        rng = np.random.default_rng(3)
        ref = rng.normal(0, 1, 200)
        cur = rng.normal(0, 1, 200)
        p5  = psi(ref, cur, n_bins=5)
        p20 = psi(ref, cur, n_bins=20)
        # Both should be small for same distribution; just check they return float
        assert isinstance(p5, float) and isinstance(p20, float)


# ---------------------------------------------------------------------------
# psi_level()
# ---------------------------------------------------------------------------

class TestPSILevel:

    def test_none_level_below_threshold(self):
        assert psi_level(0.05) == "none"

    def test_low_level(self):
        assert psi_level(0.15) == "low"

    def test_moderate_level(self):
        assert psi_level(0.22) == "moderate"

    def test_significant_level(self):
        assert psi_level(0.30) == "significant"

    def test_boundary_moderate(self):
        # Exactly at moderate threshold → should be moderate or above
        v = PSI_THRESHOLDS["moderate"]
        assert psi_level(v) in ("moderate", "significant")

    def test_boundary_significant(self):
        v = PSI_THRESHOLDS["significant"]
        assert psi_level(v) == "significant"


# ---------------------------------------------------------------------------
# ks_test()
# ---------------------------------------------------------------------------

class TestKSTest:

    def test_same_data_high_p_value(self):
        rng = np.random.default_rng(0)
        data = rng.normal(0, 1, 300)
        result = ks_test(data, data)
        assert result["p_value"] > 0.05
        assert result["drift"] is False

    def test_different_data_low_p_value(self):
        rng = np.random.default_rng(1)
        ref = rng.normal(0, 1, 300)
        cur = rng.normal(5, 1, 300)
        result = ks_test(ref, cur)
        assert result["p_value"] < 0.05
        assert result["drift"] is True

    def test_result_has_required_keys(self):
        result = ks_test([1, 2, 3, 4], [1, 2, 3, 4])
        assert "statistic" in result
        assert "p_value" in result
        assert "drift" in result

    def test_statistic_between_0_and_1(self):
        result = ks_test([1, 2, 3], [4, 5, 6])
        assert 0.0 <= result["statistic"] <= 1.0


# ---------------------------------------------------------------------------
# DriftDetector
# ---------------------------------------------------------------------------

def _make_numeric_df(n=200, seed=0, shift=0.0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "date":    pd.date_range("2023-01-01", periods=n, freq="D"),
        "sku":     ["A"] * n,
        "sales":   np.abs(rng.normal(50 + shift, 10, n)),
        "feat_1":  rng.normal(0 + shift, 1, n),
        "feat_2":  rng.normal(5 + shift, 2, n),
    })


class TestDriftDetectorFeatureDrift:

    def test_no_drift_same_data(self):
        df = _make_numeric_df(n=200)
        det = DriftDetector(df, target_col="sales", group_col="sku")
        report = det.detect_feature_drift(df)
        # With identical data PSI should be tiny → no drift
        for col_data in report.values():
            assert col_data["drift"] is False

    def test_drift_on_shifted_data(self):
        ref = _make_numeric_df(n=200, seed=0, shift=0.0)
        cur = _make_numeric_df(n=200, seed=1, shift=20.0)  # large shift
        det = DriftDetector(ref, target_col="sales", group_col="sku")
        report = det.detect_feature_drift(cur)
        # At least one feature should show drift with a 20-unit shift
        assert any(v["drift"] for v in report.values())

    def test_target_column_excluded(self):
        df = _make_numeric_df(n=200)
        det = DriftDetector(df, target_col="sales", group_col="sku")
        report = det.detect_feature_drift(df)
        assert "sales" not in report

    def test_group_column_excluded(self):
        df = _make_numeric_df(n=200)
        df["sku_num"] = 1  # add a numeric group-like col to ensure it's skipped
        det = DriftDetector(df, target_col="sales", group_col="sku")
        # "sku" is non-numeric — won't appear. Just ensure no KeyError.
        det.detect_feature_drift(df)

    def test_too_few_rows_column_skipped(self):
        ref = _make_numeric_df(n=200)
        cur = _make_numeric_df(n=5)  # only 5 rows in current — below threshold of 10
        det = DriftDetector(ref, target_col="sales", group_col="sku")
        report = det.detect_feature_drift(cur)
        # All current cols < 10 rows → should be empty report
        assert report == {}

    def test_report_has_required_keys(self):
        df = _make_numeric_df(n=200)
        cur = _make_numeric_df(n=200, shift=5.0)
        det = DriftDetector(df, target_col="sales", group_col="sku")
        report = det.detect_feature_drift(cur)
        for col, data in report.items():
            assert "psi" in data
            assert "psi_level" in data
            assert "ks_p_value" in data
            assert "drift" in data


class TestDriftDetectorPredictionDrift:

    def test_same_preds_no_drift(self):
        df = _make_numeric_df(n=200)
        det = DriftDetector(df, target_col="sales", group_col="sku")
        preds = np.random.default_rng(0).normal(50, 5, 200)
        result = det.detect_prediction_drift(preds, preds)
        assert result["drift_detected"] is False

    def test_shifted_preds_drift_detected(self):
        df = _make_numeric_df(n=200)
        det = DriftDetector(df, target_col="sales", group_col="sku")
        ref_p = np.random.default_rng(0).normal(50, 5, 200)
        cur_p = np.random.default_rng(1).normal(150, 5, 200)
        result = det.detect_prediction_drift(ref_p, cur_p)
        assert result["drift_detected"] is True

    def test_result_has_required_keys(self):
        df = _make_numeric_df()
        det = DriftDetector(df, target_col="sales", group_col="sku")
        preds = np.ones(100)
        result = det.detect_prediction_drift(preds, preds)
        assert "psi" in result and "psi_level" in result
        assert "ks_p_value" in result and "drift_detected" in result


class TestDriftDetectorAlerts:

    def test_alerts_non_empty_when_drift_detected(self):
        ref = _make_numeric_df(n=200, shift=0.0)
        cur = _make_numeric_df(n=200, shift=20.0)
        det = DriftDetector(ref, target_col="sales", group_col="sku")
        report = det.detect_feature_drift(cur)
        alerts = det.alerts(report)
        # If any drift is detected the alert list should mention it
        drifted = [col for col, v in report.items() if v["drift"]]
        if drifted:
            assert len(alerts) > 0
            assert any(drifted[0] in a for a in alerts)

    def test_alerts_empty_when_no_drift(self):
        df = _make_numeric_df(n=200)
        det = DriftDetector(df, target_col="sales", group_col="sku")
        report = det.detect_feature_drift(df)
        alerts = det.alerts(report)
        assert alerts == []


class TestPerformanceDecay:

    def test_no_decay_when_below_threshold(self):
        result = DriftDetector.performance_decay([5.0, 6.0, 5.5], 5.8, threshold_pct=0.20)
        assert result["decay"] is False

    def test_decay_detected_when_above_threshold(self):
        result = DriftDetector.performance_decay([5.0, 5.0], 10.0, threshold_pct=0.20)
        assert result["decay"] is True
        assert result["retrain_recommended"] is True

    def test_empty_historical_returns_no_decay(self):
        result = DriftDetector.performance_decay([], 10.0)
        assert result["decay"] is False
        assert "No historical" in result["message"]

    def test_degradation_pct_correct(self):
        result = DriftDetector.performance_decay([10.0], 12.0)
        # degradation = (12-10)/10 = 0.20 → 20%
        assert abs(result["degradation_pct"] - 20.0) < 1.0

    def test_result_has_required_keys(self):
        result = DriftDetector.performance_decay([5.0], 6.0)
        assert "baseline_mae" in result
        assert "current_mae" in result
        assert "degradation_pct" in result
        assert "decay" in result
        assert "retrain_recommended" in result
