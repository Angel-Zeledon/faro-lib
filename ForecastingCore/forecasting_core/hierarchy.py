"""Hierarchical forecasting reconciliation (bottom-up and top-down)."""
from __future__ import annotations

import logging
import pandas as pd
from typing import List

log = logging.getLogger(__name__)


class HierarchicalReconciler:
    """
    Reconcile forecasts across a hierarchy of levels.

    Args:
        levels: Ordered list of column names from coarsest to finest,
                e.g. ["category", "sub_category", "sku"].
    """

    def __init__(self, levels: List[str]):
        self.levels = levels

    def bottom_up(
        self,
        forecast_df: pd.DataFrame,
        date_col: str = "date",
        value_col: str = "forecast",
    ) -> pd.DataFrame:
        """
        Bottom-up reconciliation: aggregate leaf-level forecasts upward.

        Leaf level = last entry in self.levels.
        Replaces parent-level forecasts with the sum of their children.

        Args:
            forecast_df: DataFrame with columns including self.levels + date_col + value_col.
            date_col:    Name of the date column.
            value_col:   Name of the forecast value column.

        Returns:
            Reconciled DataFrame — parent rows replaced, leaf rows unchanged.
        """
        if not self.levels or len(self.levels) < 2:
            return forecast_df

        leaf = self.levels[-1]
        if leaf not in forecast_df.columns:
            log.warning(f"HierarchicalReconciler.bottom_up: leaf column '{leaf}' not found")
            return forecast_df

        result = forecast_df.copy()

        # Aggregate from leaf up to each parent level
        for i in range(len(self.levels) - 2, -1, -1):
            parent = self.levels[i]
            child_levels = self.levels[i + 1:]

            if parent not in result.columns:
                continue

            group_keys = [parent, date_col]
            agg = (
                result[result[leaf].notna()]
                .groupby(group_keys, as_index=False)[value_col]
                .sum()
                .rename(columns={value_col: f"_agg_{parent}"})
            )

            # Update rows that represent this parent level (no finer levels filled)
            parent_mask = result[child_levels[0]].isna() if child_levels else pd.Series(False, index=result.index)
            if parent_mask.any():
                result = result.merge(agg, on=group_keys, how="left")
                result.loc[parent_mask, value_col] = result.loc[parent_mask, f"_agg_{parent}"]
                result = result.drop(columns=[f"_agg_{parent}"])

        return result

    def top_down(
        self,
        forecast_df: pd.DataFrame,
        historical_df: pd.DataFrame,
        date_col: str = "date",
        value_col: str = "forecast",
        target_col: str = "value",
    ) -> pd.DataFrame:
        """
        Top-down reconciliation: disaggregate top-level forecast to leaves using
        historical proportions.

        Args:
            forecast_df:   DataFrame with top-level forecasts (self.levels[0] granularity).
            historical_df: Historical DataFrame used to compute proportions.
            date_col:      Date column name in forecast_df.
            value_col:     Forecast column name in forecast_df.
            target_col:    Target column name in historical_df.

        Returns:
            forecast_df with leaf-level rows added/updated according to proportions.
        """
        if not self.levels or len(self.levels) < 2:
            return forecast_df

        top_level = self.levels[0]
        leaf_level = self.levels[-1]

        required = {top_level, leaf_level, target_col}
        if not required.issubset(historical_df.columns):
            log.warning(
                f"HierarchicalReconciler.top_down: historical_df missing columns "
                f"{required - set(historical_df.columns)}"
            )
            return forecast_df

        # Compute historical proportions: leaf / top-level total
        hist = historical_df.copy()
        top_totals = hist.groupby(top_level)[target_col].sum().rename("_top_total")
        leaf_totals = hist.groupby([top_level, leaf_level])[target_col].sum().rename("_leaf_total")
        proportions = (
            leaf_totals.reset_index()
            .merge(top_totals.reset_index(), on=top_level)
        )
        proportions["_proportion"] = proportions["_leaf_total"] / proportions["_top_total"].clip(lower=1e-9)

        # Apply proportions to top-level forecasts
        top_fc = forecast_df[forecast_df[top_level].notna()].copy() if top_level in forecast_df.columns else forecast_df.copy()
        result = top_fc.merge(proportions[[top_level, leaf_level, "_proportion"]], on=top_level, how="left")
        result[value_col] = (result[value_col] * result["_proportion"]).clip(lower=0.0)
        result = result.drop(columns=["_proportion"])

        return result
