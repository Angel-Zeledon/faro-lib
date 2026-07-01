"""
Shared fixtures for the forecasting_core test suite.
"""

import os
import sys
import tempfile
import pytest
import numpy as np
import pandas as pd

# Ensure the ForecastingCore package is importable from any working directory
_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# DataFrame fixtures
# ---------------------------------------------------------------------------

def _make_df(n=120, skus=("A", "B"), seed=42):
    """Well-formed multi-SKU time-series DataFrame."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-01", periods=n, freq="D")
    rows = []
    for sku in skus:
        sales = np.maximum(0, rng.normal(100, 20, n)).round(2)
        for i, d in enumerate(dates):
            rows.append({"date": d, "sku": sku, "sales": sales[i]})
    return pd.DataFrame(rows)


@pytest.fixture
def df_standard():
    """120 daily rows, 2 SKUs, clean data."""
    return _make_df(n=120, skus=["A", "B"])


@pytest.fixture
def df_single():
    """Single series (no SKU column), 100 rows."""
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=100, freq="D"),
        "sales": np.maximum(0, rng.normal(50, 10, 100)).round(2),
    })


@pytest.fixture
def df_intermittent():
    """Series with ~60% zeros (intermittent demand)."""
    rng = np.random.default_rng(1)
    n = 80
    sales = rng.choice([0] * 50 + list(rng.integers(1, 20, 30)), n, replace=True).astype(float)
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="D"),
        "sku": "X",
        "sales": sales,
    })


@pytest.fixture
def df_tiny():
    """Only 5 rows — too small for most models."""
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=5, freq="D"),
        "sku": "Z",
        "sales": [10, 12, 9, 11, 13],
    })


@pytest.fixture
def csv_path(df_standard, tmp_path):
    """Writes df_standard to a temp CSV and returns the path."""
    p = tmp_path / "sales.csv"
    df_standard.to_csv(str(p), index=False)
    return str(p)


@pytest.fixture
def parquet_path(df_standard, tmp_path):
    pytest.importorskip("pyarrow", reason="pyarrow required for parquet tests")
    p = tmp_path / "sales.parquet"
    df_standard.to_parquet(str(p), index=False)
    return str(p)


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_config():
    """Minimal valid SessionConfig dict."""
    return {
        "columns": {"target": "sales", "date": "date", "group_keys": ["sku"]},
        "features": {"lags": [1, 7], "rolling": [7], "diffs": [1], "calendar": True, "ewm_spans": []},
        "models": {"lightgbm": {}},
        "training": {"train_ratio": 0.8, "walk_forward": False, "wfv_splits": 3,
                     "min_history": 20, "seasonal_period": 7},
        "forecast": {"horizon": 7},
        "business": {"service_level": 0.95, "lead_time_days": 7,
                     "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
    }


# ---------------------------------------------------------------------------
# Numpy arrays for metric tests
# ---------------------------------------------------------------------------

@pytest.fixture
def perfect_preds():
    y = np.array([10.0, 20.0, 30.0, 40.0])
    return y, y.copy()


@pytest.fixture
def imperfect_preds():
    y    = np.array([10.0, 20.0, 30.0, 40.0])
    yhat = np.array([12.0, 18.0, 33.0, 38.0])
    return y, yhat
