"""Tests for forecasting_core.validation.auto_correct: auto_correct_config, auto_correct_data."""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.validation.auto_correct import (
    auto_correct_config,
    auto_correct_data,
    Correction,
    CorrectionLog,
)


# ---------------------------------------------------------------------------
# Correction / CorrectionLog dataclasses
# ---------------------------------------------------------------------------

class TestCorrectionLog:

    def test_add_increases_length(self):
        log = CorrectionLog()
        log.add(Correction("set_default", "low", "desc"))
        assert len(log.corrections) == 1

    def test_to_list_has_required_keys(self):
        log = CorrectionLog()
        log.add(Correction("set_default", "low", "desc", before=None, after=0.8))
        entry = log.to_list()[0]
        for key in ("action", "risk", "description", "before", "after", "skus_affected"):
            assert key in entry

    def test_has_high_risk_false_when_all_low(self):
        log = CorrectionLog()
        log.add(Correction("set_default", "low", "desc"))
        assert log.has_high_risk() is False

    def test_has_high_risk_true_when_one_high(self):
        log = CorrectionLog()
        log.add(Correction("drop_sku", "high", "high risk change"))
        assert log.has_high_risk() is True

    def test_empty_log_has_no_high_risk(self):
        assert CorrectionLog().has_high_risk() is False

    def test_to_list_empty_returns_empty(self):
        assert CorrectionLog().to_list() == []


# ---------------------------------------------------------------------------
# auto_correct_config
# ---------------------------------------------------------------------------

class TestAutoCorrectConfig:

    def _base_cfg(self):
        return {
            "data": "sales.csv",
            "dt": "date",
            "target": "sales",
            "group_id": "sku",
            "models": {"lightgbm": {}},
            "features": {"lags": [1, 7], "rolling": [7]},
        }

    def test_returns_correction_log(self):
        cfg = self._base_cfg()
        result = auto_correct_config(cfg)
        assert isinstance(result, CorrectionLog)

    def test_missing_train_ratio_set_to_default(self):
        cfg = self._base_cfg()
        auto_correct_config(cfg)
        assert cfg["train_ratio"] == 0.8

    def test_missing_prediction_horizon_set_to_default(self):
        cfg = self._base_cfg()
        auto_correct_config(cfg)
        assert cfg["prediction_horizon"] == 7

    def test_missing_min_history_set_to_default(self):
        cfg = self._base_cfg()
        auto_correct_config(cfg)
        assert cfg["min_history"] == 20

    def test_train_ratio_above_095_clamped(self):
        cfg = self._base_cfg()
        cfg["train_ratio"] = 1.5
        auto_correct_config(cfg)
        assert cfg["train_ratio"] <= 0.95

    def test_train_ratio_below_01_clamped(self):
        cfg = self._base_cfg()
        cfg["train_ratio"] = 0.01
        auto_correct_config(cfg)
        assert cfg["train_ratio"] >= 0.1

    def test_valid_train_ratio_unchanged(self):
        cfg = self._base_cfg()
        cfg["train_ratio"] = 0.75
        auto_correct_config(cfg)
        assert cfg["train_ratio"] == pytest.approx(0.75)

    def test_missing_features_initialized(self):
        cfg = self._base_cfg()
        del cfg["features"]
        auto_correct_config(cfg)
        assert isinstance(cfg["features"], dict)

    def test_missing_lags_set_to_default(self):
        cfg = self._base_cfg()
        cfg["features"] = {}  # no lags key
        auto_correct_config(cfg)
        assert "lags" in cfg["features"]
        assert cfg["features"]["lags"] == [1, 7, 14]

    def test_missing_rolling_set_to_default(self):
        cfg = self._base_cfg()
        cfg["features"] = {"lags": [1, 7]}  # no rolling key
        auto_correct_config(cfg)
        assert "rolling" in cfg["features"]
        assert cfg["features"]["rolling"] == [7, 14]

    def test_existing_lags_not_overwritten(self):
        cfg = self._base_cfg()
        cfg["features"]["lags"] = [1, 3]
        auto_correct_config(cfg)
        assert cfg["features"]["lags"] == [1, 3]

    def test_no_corrections_when_config_complete(self):
        cfg = self._base_cfg()
        cfg.update({"train_ratio": 0.8, "prediction_horizon": 14, "min_history": 30})
        log = auto_correct_config(cfg)
        # Some defaults might still be set but no clamping should occur
        clamp_corrections = [c for c in log.corrections if c.action == "clamp"]
        assert len(clamp_corrections) == 0

    def test_all_corrections_are_low_risk(self):
        cfg = {}
        log = auto_correct_config(cfg)
        for c in log.corrections:
            assert c.risk in ("low", "medium"), f"Expected low/medium, got {c.risk}"


# ---------------------------------------------------------------------------
# auto_correct_data
# ---------------------------------------------------------------------------

def _make_df(n=60, skus=("A",), nan_ratio=0.0, add_outlier=False, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for sku in skus:
        for i, d in enumerate(pd.date_range("2022-01-01", periods=n, freq="D")):
            val = float(rng.normal(50, 5))
            if rng.random() < nan_ratio:
                val = np.nan
            rows.append({"date": d, "sku": sku, "sales": val})
    df = pd.DataFrame(rows)
    if add_outlier:
        df.loc[df.index[0], "sales"] = 99999.0
    return df


def _cfg():
    return {"dt": "date", "target": "sales", "group_id": "sku"}


class TestAutoCorrectData:

    def test_returns_tuple_df_and_log(self):
        df = _make_df()
        result = auto_correct_data(df, _cfg())
        assert isinstance(result, tuple)
        assert len(result) == 2
        df_out, log = result
        assert isinstance(df_out, pd.DataFrame)
        assert isinstance(log, CorrectionLog)

    def test_original_df_not_modified(self):
        df = _make_df(nan_ratio=0.1)
        original_nans = df["sales"].isna().sum()
        auto_correct_data(df, _cfg())
        assert df["sales"].isna().sum() == original_nans  # original unchanged

    def test_nan_values_filled(self):
        df = _make_df(n=60, nan_ratio=0.2)
        df_out, log = auto_correct_data(df, _cfg())
        assert df_out["sales"].isna().sum() == 0

    def test_nan_fill_logged(self):
        df = _make_df(n=60, nan_ratio=0.2)
        _, log = auto_correct_data(df, _cfg())
        actions = [c.action for c in log.corrections]
        assert "fill_nan_target" in actions

    def test_no_nan_no_fill_correction(self):
        df = _make_df(n=60, nan_ratio=0.0)
        _, log = auto_correct_data(df, _cfg())
        actions = [c.action for c in log.corrections]
        assert "fill_nan_target" not in actions

    def test_outlier_clipped(self):
        df = _make_df(n=60, add_outlier=True)
        df_out, log = auto_correct_data(df, _cfg(), clip_outliers=True)
        # The extreme value (99999) should be clipped
        assert df_out["sales"].max() < 99999.0

    def test_clip_outliers_logged(self):
        df = _make_df(n=60, add_outlier=True)
        _, log = auto_correct_data(df, _cfg(), clip_outliers=True)
        actions = [c.action for c in log.corrections]
        assert "clip_outliers" in actions

    def test_no_outliers_no_clip_correction(self):
        df = _make_df(n=60, nan_ratio=0.0, add_outlier=False)
        _, log = auto_correct_data(df, _cfg(), clip_outliers=True)
        actions = [c.action for c in log.corrections]
        assert "clip_outliers" not in actions

    def test_clip_disabled_does_not_clip(self):
        df = _make_df(n=60, add_outlier=True)
        df_out, _ = auto_correct_data(df, _cfg(), clip_outliers=False)
        assert df_out["sales"].max() == pytest.approx(99999.0)

    def test_missing_target_column_returns_unchanged_df(self):
        df = _make_df(n=60)
        df_out, log = auto_correct_data(df, {"dt": "date", "target": "nonexistent", "group_id": "sku"})
        assert len(df_out) == len(df)
        assert len(log.corrections) == 0

    def test_multi_sku_nan_filled(self):
        df = _make_df(n=60, skus=("A", "B"), nan_ratio=0.15)
        df_out, log = auto_correct_data(df, _cfg())
        assert df_out["sales"].isna().sum() == 0

    def test_clip_correction_is_medium_risk(self):
        df = _make_df(n=60, add_outlier=True)
        _, log = auto_correct_data(df, _cfg(), clip_outliers=True)
        clip_corrections = [c for c in log.corrections if c.action == "clip_outliers"]
        for c in clip_corrections:
            assert c.risk == "medium"

    def test_output_shape_preserved(self):
        df = _make_df(n=60, nan_ratio=0.1, add_outlier=True)
        df_out, _ = auto_correct_data(df, _cfg())
        assert df_out.shape == df.shape
