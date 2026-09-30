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
# pct_change on a zero-inflated target must not delete the row (stability.md
# 17b: found while measuring intermittent-demand coverage)
# ─────────────────────────────────────────────────────────────────────────────
#
# `pct_change_{d}` divides by the PREVIOUS value. On an intermittent series
# that previous value is zero most of the time, which computes to inf, gets
# replaced with NaN, and used to force the row itself out of `dropna`. That is
# not a warm-up gap — the lag/rolling windows are already full — it is a ratio
# that is genuinely undefined, and dropping the row over it throws away a real
# demand observation for exactly the SKUs with the fewest to spare. Measured
# on a 12-SKU, ~75%-zero synthetic catalogue before this fix: `diffs=[1]`
# alone took 400 rows per SKU down to ~30.

class TestPctChangeZeroDenominatorKeepsRows:

    def test_zero_denominator_does_not_drop_the_row(self):
        """A single zero in the middle of an otherwise dense series must not
        remove the row whose `pct_change_1` divides by it — `diff_1` and
        every lag stay well-defined there regardless.

        `pct_change_1` is computed on the SHIFT(1) series (see `_lags`'s own
        no-leakage rationale), so the undefined ratio lands one row later
        than the zero itself — found by construction below rather than
        hardcoded, so this test does not silently start checking the wrong
        row if the shift convention ever changes.
        """
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=6, freq="D"),
            "sales": [10.0, 20.0, 0.0, 5.0, 8.0, 3.0],
        })
        cfg = _make_cfg(lags=[1], diffs=[1], rolling=[], calendar=False)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])

        # What pct_change_1 looks like BEFORE any row is dropped, to find the
        # NaN row this fix is about (independent of the dropna behaviour
        # under test — see `transform`'s own `drop_warmup=False` path).
        # Filtered to where `diff_1` IS defined, so this isolates the
        # zero-denominator case from lag_1/diff_1's own genuine warm-up rows
        # (which this fix does not, and should not, rescue).
        pre_drop = eng.transform(df, drop_warmup=False)
        undefined = pre_drop[pre_drop["pct_change_1"].isna() & pre_drop["diff_1"].notna()]
        undefined_dates = undefined["date"]
        assert len(undefined_dates) >= 1, "fixture must produce an undefined pct_change_1"

        out = eng.transform(df)
        assert set(undefined_dates) <= set(out["date"]), (
            "a row whose pct_change_1 is undefined (zero denominator) was "
            "dropped — diff_1 and every lag were still well-defined there"
        )
        # Only genuine warm-up is lost: lag_1's first row, and diff_1 needs
        # one more point of history than lag_1 alone.
        assert len(out) == 4

    def test_pct_change_column_survives_with_its_own_nan(self):
        """The column itself is kept (LightGBM/XGBoost route NaN natively —
        see `FeatureEngineer.transform`'s own docstring) — only the ROW-DROP
        is what this fix removes."""
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=6, freq="D"),
            "sales": [10.0, 20.0, 0.0, 5.0, 8.0, 3.0],
        })
        cfg = _make_cfg(lags=[1], diffs=[1], rolling=[], calendar=False)
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        out = eng.transform(df)
        assert "pct_change_1" in out.columns
        # The row this fix keeps is precisely the one with a NaN here — a
        # fabricated finite value would defeat the point of the test.
        assert out["pct_change_1"].isna().sum() == 1

    def test_intermittent_catalogue_retains_most_of_its_history(self):
        """The end-to-end regression this fix exists for: a 75%-zero, 400-day
        SKU used to lose the vast majority of its rows to this one column.
        Pins a floor loose enough to survive an unrelated tweak to the
        synthetic fixture, tight enough to fail against the pre-fix code
        (which kept well under half)."""
        rng = np.random.default_rng(11)
        n = 400
        hit = rng.random(n) < 0.25
        size = rng.poisson(4.0, n) + 1.0
        demand = np.where(hit, size, 0.0)
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=n, freq="D"),
            "sales": demand,
        })
        cfg = _make_cfg(lags=[1, 7, 14, 28], diffs=[1], rolling=[7, 14, 28],
                        calendar=True, ewm_spans=[7, 14])
        eng = FeatureEngineer(cfg, dt_col="date", target="sales", group_cols=[])
        out = eng.transform(df)
        assert len(out) >= 0.8 * n, (
            f"kept only {len(out)}/{n} rows on a 75%-zero series — "
            "pct_change_1 is still forcing rows out over an undefined ratio"
        )


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
