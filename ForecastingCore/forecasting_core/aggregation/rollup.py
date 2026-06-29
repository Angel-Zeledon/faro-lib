from __future__ import annotations
import pandas as pd


def aggregate_by_sku(forecast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Sum base (SKU × Store) forecasts by SKU across all stores.

    Input:  DataFrame with columns [sku, store, date, step, forecast]
    Output: DataFrame with columns [sku, date, step, forecast]
    """
    return (
        forecast_df
        .groupby(["sku", "date", "step"], as_index=False)["forecast"]
        .sum()
    )


def aggregate_by_store(forecast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Sum base (SKU × Store) forecasts by Store across all SKUs.

    Input:  DataFrame with columns [sku, store, date, step, forecast]
    Output: DataFrame with columns [store, date, step, forecast]
    """
    return (
        forecast_df
        .groupby(["store", "date", "step"], as_index=False)["forecast"]
        .sum()
    )
