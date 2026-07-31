"""
Tests for forecasting_core.features.engineer.FeatureEngineer.
"""

import pytest
import numpy as np
import pandas as pd

from forecasting_core.features.engineer import FeatureEngineer
from forecasting_core.config.config import FeaturesConfig


def _make_cfg(**kwargs):
    defaults = dict(lags=[1, 7], diffs=[1], rolling=[7], calendar=True, ewm_spans=[])
    defaults.update(kwargs)
    return FeaturesConfig(**defaults)


def _make_df(n=60, with_sku=True):
    rng = np.random.default_rng(42)
    rows = []
    for sku in (["A", "B"] if with_sku else ["single"]):
        for i, d in enumerate(pd.date_range("2022-01-01", periods=n, freq="D")):
            rows.append({"date": d, "sku": sku if with_sku else None, "sales": float(rng.normal(50, 10))})
    df = pd.DataFrame(rows)
    if not with_sku:
        df = df.drop(columns=["sku"])
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Happy-path: transform produces expected columns
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureEngineerTransform:

    def test_lag_columns_created(self):
        df = _make_df()
        cfg = _make_cfg(lags=[1, 7])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        assert "lag_1" in out.columns
        assert "lag_7" in out.columns

    def test_rolling_columns_created(self):
        df = _make_df()
        cfg = _make_cfg(rolling=[7, 14])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        assert "roll_mean_7" in out.columns
        assert "roll_std_14" in out.columns

    def test_calendar_columns_created(self):
        df = _make_df()
        cfg = _make_cfg(calendar=True)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        for col in ["year", "month", "dow", "is_weekend", "sin_month", "cos_month"]:
            assert col in out.columns, f"Missing calendar column: {col}"

    def test_calendar_false_skips_calendar_cols(self):
        df = _make_df()
        cfg = _make_cfg(calendar=False)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        assert "month" not in out.columns

    def test_diff_columns_created(self):
        df = _make_df()
        cfg = _make_cfg(diffs=[1, 7])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        assert "diff_1" in out.columns
        assert "diff_7" in out.columns

    def test_pct_change_columns_created(self):
        df = _make_df()
        cfg = _make_cfg(diffs=[1])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        assert "pct_change_1" in out.columns

    def test_ewm_columns_created(self):
        df = _make_df()
        cfg = _make_cfg(ewm_spans=[7, 14])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        assert "ewm_7" in out.columns
        assert "ewm_14" in out.columns

    def test_transform_drops_nan_rows(self):
        df = _make_df(n=30)
        cfg = _make_cfg(lags=[1, 7])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        assert out.isnull().sum().sum() == 0

    def test_transform_no_group_col(self):
        df = _make_df(with_sku=False)
        cfg = _make_cfg(lags=[1], rolling=[7], calendar=True)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        out = eng.transform(df)
        assert "lag_1" in out.columns
        assert len(out) > 0

    def test_returns_copy_not_mutates(self):
        df = _make_df()
        original_cols = set(df.columns)
        cfg = _make_cfg()
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        eng.transform(df)
        assert set(df.columns) == original_cols  # original unchanged

    def test_output_has_no_infinities(self):
        df = _make_df()
        cfg = _make_cfg()
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        numeric = out.select_dtypes(include=np.number)
        assert not np.isinf(numeric.values).any()


# ─────────────────────────────────────────────────────────────────────────────
# Leakage: diff/pct_change must never contain the current target value
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTargetLeakageInDiffs:

    def test_diff_uses_only_past_values(self):
        # Geometric series makes shifted vs unshifted unambiguous:
        # at the row where sales == 80, diff_1 must be 40 - 20 = 20
        # (yesterday minus the day before), NOT 80 - 40 = 40.
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=6, freq="D"),
            "sales": [10.0, 20.0, 40.0, 80.0, 160.0, 320.0],
        })
        cfg = _make_cfg(lags=[1], diffs=[1], rolling=[], calendar=False)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        out = eng.transform(df)
        row = out[out["sales"] == 80.0].iloc[0]
        assert row["diff_1"] == pytest.approx(20.0)

    def test_pct_change_uses_only_past_values(self):
        # at the row where sales == 240, pct_change_1 must be (60-20)/20 = 2.0,
        # NOT (240-60)/60 = 3.0.
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=4, freq="D"),
            "sales": [10.0, 20.0, 60.0, 240.0],
        })
        cfg = _make_cfg(lags=[1], diffs=[1], rolling=[], calendar=False)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        out = eng.transform(df)
        row = out[out["sales"] == 240.0].iloc[0]
        assert row["pct_change_1"] == pytest.approx(2.0)

    def test_target_not_reconstructible_from_lag_plus_diff(self):
        # With unshifted diffs, y[t] == lag_1[t] + diff_1[t] exactly — the model
        # is handed the answer and learns to extrapolate the last slope.
        df = _make_df(n=80, with_sku=False)
        cfg = _make_cfg(lags=[1], diffs=[1], rolling=[], calendar=False)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        out = eng.transform(df)
        reconstructed = out["lag_1"] + out["diff_1"]
        assert not np.allclose(reconstructed.values, out["sales"].values)

    def test_diff_respects_group_boundaries(self):
        rows = []
        for sku, base in (("A", 1.0), ("B", 100.0)):
            for i, d in enumerate(pd.date_range("2022-01-01", periods=4, freq="D")):
                rows.append({"date": d, "sku": sku, "sales": base * (i + 1)})
        df = pd.DataFrame(rows)
        cfg = _make_cfg(lags=[1], diffs=[1], rolling=[], calendar=False)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        # B's third row (sales=300): diff_1 = 200 - 100 within the group,
        # never contaminated by A's values.
        row = out[(out["sku"] == "B") & (out["sales"] == 300.0)].iloc[0]
        assert row["diff_1"] == pytest.approx(100.0)


# ─────────────────────────────────────────────────────────────────────────────
# Calendar feature correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestCalendarFeatures:

    def test_is_weekend_correct(self):
        # 2022-01-01 is Saturday
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=7, freq="D"),
            "sales": [1.0] * 7,
        })
        cfg = _make_cfg(calendar=True, lags=[], rolling=[], diffs=[])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        out = eng.transform(df)
        assert out.loc[out["date"] == pd.Timestamp("2022-01-01"), "is_weekend"].values[0] == 1
        assert out.loc[out["date"] == pd.Timestamp("2022-01-03"), "is_weekend"].values[0] == 0

    def test_sin_cos_month_range(self):
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=365, freq="D"),
            "sales": 1.0,
        })
        cfg = _make_cfg(calendar=True, lags=[], rolling=[], diffs=[])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        out = eng.transform(df)
        assert (out["sin_month"].abs() <= 1.0).all()
        assert (out["cos_month"].abs() <= 1.0).all()

    def test_easter_date_algorithm(self):
        # Known Easter dates. The algorithm moved to features/calendar.py so
        # that inference computes it the same way training does.
        from forecasting_core.features.calendar import easter_date
        assert easter_date(2022) == pd.Timestamp("2022-04-17")
        assert easter_date(2023) == pd.Timestamp("2023-04-09")


# ─────────────────────────────────────────────────────────────────────────────
# Error / edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureEngineerEdgeCases:

    def test_empty_lags_list(self):
        df = _make_df(n=30)
        cfg = _make_cfg(lags=[], diffs=[], rolling=[7])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        assert "lag_1" not in out.columns

    def test_large_lag_reduces_rows(self):
        df = _make_df(n=30)
        cfg = _make_cfg(lags=[20], diffs=[], rolling=[])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        out = eng.transform(df)
        # After dropna, must have fewer rows than 60 (2 skus × 30)
        assert len(out) < 60

    def test_missing_target_column_raises(self):
        df = _make_df().drop(columns=["sales"])
        cfg = _make_cfg()
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        with pytest.raises((KeyError, ValueError)):
            eng.transform(df)

    def test_missing_date_column_raises(self):
        df = _make_df().drop(columns=["date"])
        cfg = _make_cfg()
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=["sku"])
        with pytest.raises((KeyError, Exception)):
            eng.transform(df)

    def test_single_row_df(self):
        df = pd.DataFrame({"date": [pd.Timestamp("2022-01-01")], "sales": [10.0]})
        cfg = _make_cfg(lags=[1], rolling=[7], diffs=[1], calendar=False)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        # Should not crash; result may be empty after dropna
        out = eng.transform(df)
        assert isinstance(out, pd.DataFrame)
