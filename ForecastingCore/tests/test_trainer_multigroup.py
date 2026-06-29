"""
Tests for Trainer.train() group_cols migration (Task 5 — canonical schema plan).

Covers:
  - group_cols two-element list (sku + store) → 4 result keys for 2×2 matrix
  - Result keys contain U+2502 separator (│)
  - Each result dict exposes "sku" and "store" fields
  - Single-element group_cols still works (backward path)
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from forecasting_core.training.trainer import Trainer


def _two_sku_two_store_df():
    rows = []
    for sku in ["A", "B"]:
        for store in ["Norte", "Sur"]:
            for i in range(30):
                rows.append({
                    "sku": sku, "store": store,
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                    "sales": float(10 + i + (5 if sku == "B" else 0)),
                    "feat1": float(i),
                })
    return pd.DataFrame(rows)


def test_multigroup_produces_four_result_keys():
    df = _two_sku_two_store_df()
    models = {"linreg": LinearRegression()}
    trainer = Trainer(train_ratio=0.7, walk_forward=False)
    results = trainer.train(df, models, group_cols=["sku", "store"],
                            target="sales", dt="date")
    # 4 series × 1 model = 4 keys
    assert len(results) == 4


def test_multigroup_result_keys_contain_pipe_separator():
    df = _two_sku_two_store_df()
    models = {"linreg": LinearRegression()}
    trainer = Trainer(train_ratio=0.7, walk_forward=False)
    results = trainer.train(df, models, group_cols=["sku", "store"],
                            target="sales", dt="date")
    for key in results:
        assert "│" in key, f"Expected │ in key, got: {key}"


def test_multigroup_result_has_sku_and_store_fields():
    df = _two_sku_two_store_df()
    models = {"linreg": LinearRegression()}
    trainer = Trainer(train_ratio=0.7, walk_forward=False)
    results = trainer.train(df, models, group_cols=["sku", "store"],
                            target="sales", dt="date")
    for r in results.values():
        assert "sku" in r
        assert "store" in r


def test_single_group_col_still_works():
    df = _two_sku_two_store_df()
    models = {"linreg": LinearRegression()}
    trainer = Trainer(train_ratio=0.7, walk_forward=False)
    # Pass as list with one element
    results = trainer.train(df, models, group_cols=["sku"],
                            target="sales", dt="date")
    assert len(results) == 2   # 2 SKUs
