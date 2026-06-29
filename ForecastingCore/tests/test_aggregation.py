import pandas as pd
import pytest
from forecasting_core.aggregation.rollup import aggregate_by_sku, aggregate_by_store


def _base_forecast_df():
    rows = []
    for sku in ["A", "B"]:
        for store in ["Norte", "Sur"]:
            for step in [1, 2, 3]:
                rows.append({
                    "sku": sku, "store": store,
                    "date": pd.Timestamp("2024-02-01") + pd.Timedelta(days=step - 1),
                    "step": step,
                    "forecast": float(10 * (1 if sku == "A" else 2) * (1 if store == "Norte" else 3)),
                })
    return pd.DataFrame(rows)


def test_by_sku_sums_across_stores():
    df = _base_forecast_df()
    result = aggregate_by_sku(df)
    sku_a = result[result["sku"] == "A"]
    # A-Norte = 10, A-Sur = 30 → total = 40 per step
    assert (sku_a["forecast"] == 40.0).all()


def test_by_store_sums_across_skus():
    df = _base_forecast_df()
    result = aggregate_by_store(df)
    norte = result[result["store"] == "Norte"]
    # A-Norte=10, B-Norte=20 → total = 30 per step
    assert (norte["forecast"] == 30.0).all()


def test_by_sku_has_no_store_column():
    df = _base_forecast_df()
    result = aggregate_by_sku(df)
    assert "store" not in result.columns


def test_by_store_has_no_sku_column():
    df = _base_forecast_df()
    result = aggregate_by_store(df)
    assert "sku" not in result.columns


def test_no_double_counting_by_sku():
    df = _base_forecast_df()
    grand_total_base  = df["forecast"].sum()
    by_sku_total      = aggregate_by_sku(df)["forecast"].sum()
    by_store_total    = aggregate_by_store(df)["forecast"].sum()
    # Each step appears for 2 stores in by_sku / 2 skus in by_store
    # grand_total per step = 10+30+20+60 = 120 × 3 steps = 360
    assert by_sku_total == grand_total_base
    assert by_store_total == grand_total_base
