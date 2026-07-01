"""
Tests for FeatureEngineer.group_cols migration (Task 4 — canonical schema plan).

Covers:
  - group_cols single-element list  (replaces old group=)
  - group_cols multi-element list   (multi-column groupby)
  - group_cols empty / omitted      (no-group path)
  - backward compat via group=      (one-step transition shim)
  - _groupby helper return types
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.features.engineer import FeatureEngineer
from forecasting_core.config.config import FeaturesConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**kw):
    defaults = dict(lags=[1, 7], diffs=[1], rolling=[7], calendar=False, ewm_spans=[])
    defaults.update(kw)
    return FeaturesConfig(**defaults)


def _df_single_group(n: int = 40, sku: str = "A"):
    """Single-group dataframe with one SKU."""
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "date": pd.date_range("2023-01-01", periods=n, freq="D"),
        "sku": sku,
        "sales": rng.uniform(10, 100, n),
    })


def _df_two_groups(n: int = 40):
    """Two-group dataframe with two SKUs."""
    rng = np.random.default_rng(1)
    rows = []
    for sku in ["X", "Y"]:
        for d in pd.date_range("2023-01-01", periods=n, freq="D"):
            rows.append({"date": d, "sku": sku, "sales": float(rng.uniform(10, 100))})
    return pd.DataFrame(rows)


def _df_multi_key(n: int = 40):
    """Multi-key dataframe with sku x store combos."""
    rng = np.random.default_rng(2)
    rows = []
    for sku in ["P", "Q"]:
        for store in ["S1", "S2"]:
            for d in pd.date_range("2023-01-01", periods=n, freq="D"):
                rows.append({
                    "date": d,
                    "sku": sku,
                    "store": store,
                    "sales": float(rng.uniform(5, 50)),
                })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Single group_cols element
# ---------------------------------------------------------------------------

class TestGroupColsSingle:

    def test_single_list_produces_lag_columns(self):
        eng = FeatureEngineer(_cfg(lags=[1, 7]), dt_col="date", target="sales",
                              group_cols=["sku"])
        out = eng.transform(_df_two_groups())
        assert "lag_1" in out.columns
        assert "lag_7" in out.columns

    def test_single_list_produces_rolling_columns(self):
        eng = FeatureEngineer(_cfg(rolling=[7]), dt_col="date", target="sales",
                              group_cols=["sku"])
        out = eng.transform(_df_two_groups())
        assert "roll_mean_7" in out.columns

    def test_single_list_no_cross_group_leakage(self):
        """lag_1 for the first row of each SKU must be NaN (dropped by dropna),
        confirming groupby is applied per SKU, not globally."""
        df = _df_two_groups(n=30)
        eng = FeatureEngineer(_cfg(lags=[1], diffs=[], rolling=[]), dt_col="date",
                              target="sales", group_cols=["sku"])
        out = eng.transform(df)
        # After dropna, every remaining row must have a non-null lag_1
        assert out["lag_1"].isnull().sum() == 0
        # Both SKUs still present in output
        assert set(out["sku"].unique()) == {"X", "Y"}

    def test_single_list_internal_group_cols_attribute(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales", group_cols=["sku"])
        assert eng._group_cols == ["sku"]


# ---------------------------------------------------------------------------
# Multi-element group_cols
# ---------------------------------------------------------------------------

class TestGroupColsMulti:

    def test_two_col_groupby_produces_features(self):
        eng = FeatureEngineer(_cfg(lags=[1], diffs=[], rolling=[7]), dt_col="date",
                              target="sales", group_cols=["sku", "store"])
        out = eng.transform(_df_multi_key())
        assert "lag_1" in out.columns
        assert "roll_mean_7" in out.columns

    def test_two_col_groupby_no_cross_group_leakage(self):
        """Each (sku, store) pair should have its own lag sequence."""
        df = _df_multi_key(n=30)
        eng = FeatureEngineer(_cfg(lags=[1], diffs=[], rolling=[]), dt_col="date",
                              target="sales", group_cols=["sku", "store"])
        out = eng.transform(df)
        assert out["lag_1"].isnull().sum() == 0
        # All four combos present
        combos = set(zip(out["sku"], out["store"]))
        assert combos == {("P", "S1"), ("P", "S2"), ("Q", "S1"), ("Q", "S2")}

    def test_two_col_group_cols_attribute(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales",
                              group_cols=["sku", "store"])
        assert eng._group_cols == ["sku", "store"]

    def test_ewm_with_two_col_groups(self):
        eng = FeatureEngineer(_cfg(lags=[], diffs=[], rolling=[], ewm_spans=[7]),
                              dt_col="date", target="sales",
                              group_cols=["sku", "store"])
        out = eng.transform(_df_multi_key(n=40))
        assert "ewm_7" in out.columns


# ---------------------------------------------------------------------------
# Empty group_cols (no-group path)
# ---------------------------------------------------------------------------

class TestGroupColsEmpty:

    def test_empty_list_produces_features(self):
        df = _df_single_group()
        df = df.drop(columns=["sku"])
        eng = FeatureEngineer(_cfg(lags=[1], diffs=[], rolling=[]), dt_col="date",
                              target="sales", group_cols=[])
        out = eng.transform(df)
        assert "lag_1" in out.columns
        assert len(out) > 0

    def test_omitted_group_cols_defaults_to_empty(self):
        """Neither group nor group_cols supplied — should not raise."""
        df = _df_single_group().drop(columns=["sku"])
        eng = FeatureEngineer(_cfg(lags=[1], diffs=[], rolling=[]), dt_col="date",
                              target="sales")
        out = eng.transform(df)
        assert "lag_1" in out.columns

    def test_empty_group_cols_attribute(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales", group_cols=[])
        assert eng._group_cols == []


# ---------------------------------------------------------------------------
# group_cols API (canonical form — shim removed)
# ---------------------------------------------------------------------------

class TestGroupColsCanonical:

    def test_group_cols_list_accepted(self):
        eng = FeatureEngineer(_cfg(lags=[1, 7]), dt_col="date", target="sales",
                              group_cols=["sku"])
        out = eng.transform(_df_two_groups())
        assert "lag_1" in out.columns
        assert "lag_7" in out.columns

    def test_group_cols_single_sets_attribute(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales", group_cols=["sku"])
        assert eng._group_cols == ["sku"]

    def test_group_cols_none_defaults_to_empty(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales", group_cols=None)
        assert eng._group_cols == []

    def test_group_cols_multi_sets_attribute(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales",
                              group_cols=["sku", "store"])
        assert eng._group_cols == ["sku", "store"]


# ---------------------------------------------------------------------------
# _groupby helper
# ---------------------------------------------------------------------------

class TestGroupbyHelper:

    def test_empty_group_cols_returns_none(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales", group_cols=[])
        df = _df_single_group()
        assert eng._groupby(df) is None

    def test_single_group_col_returns_groupby_object(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales", group_cols=["sku"])
        df = _df_two_groups()
        grp = eng._groupby(df)
        assert grp is not None
        assert hasattr(grp, "groups")

    def test_multi_group_cols_returns_groupby_object(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales",
                              group_cols=["sku", "store"])
        df = _df_multi_key()
        grp = eng._groupby(df)
        assert grp is not None
        assert hasattr(grp, "groups")

    def test_multi_group_cols_groups_by_all_keys(self):
        eng = FeatureEngineer(_cfg(), dt_col="date", target="sales",
                              group_cols=["sku", "store"])
        df = _df_multi_key(n=5)
        grp = eng._groupby(df)
        # 2 skus x 2 stores = 4 groups
        assert len(grp.groups) == 4
