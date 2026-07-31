"""Hierarchical forecasting reconciliation (bottom-up and top-down)."""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional

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

    # ------------------------------------------------------------------
    # MinT
    # ------------------------------------------------------------------

    def mint(
        self,
        forecast_df: pd.DataFrame,
        residuals: Optional[Dict[str, "np.ndarray"]] = None,
        date_col: str = "date",
        value_col: str = "forecast",
        shrinkage: Optional[float] = None,
    ) -> pd.DataFrame:
        """
        Minimum-trace reconciliation (MinT) with a shrunk covariance estimate.

        Bottom-up and top-down are both projections that throw information away.
        Bottom-up leaves every leaf forecast exactly as it was and simply
        relabels the aggregates; top-down does the reverse, discarding whatever
        each leaf's own model learned. Neither can make a SKU-level forecast
        better, which is the level the purchase order is written at.

        MinT projects the whole vector of base forecasts onto the coherent
        subspace while minimising the trace of the reconciled error covariance:

            y~ = S (S' W^-1 S)^-1 S' W^-1  y^

        where S sums the leaves into every level. Because the projection mixes
        the levels, an aggregate forecast built on ten times the data can and
        does correct the leaves under it — a bottom-up reconciliation cannot do
        that by construction.

        Args:
            residuals: {leaf key: in-sample or backtest residuals}. Used to
                       estimate W. Absent leaves fall back to the pooled
                       variance; with no residuals at all W becomes the
                       identity, which is the OLS special case of MinT.
            shrinkage: Shrinkage intensity toward the diagonal target in [0, 1].
                       None estimates it from the data (Schafer-Strimmer).

        Returns:
            forecast_df with `value_col` replaced by the reconciled values.
            Rows whose level cannot be identified are returned untouched.
        """
        import numpy as np

        if not self.levels or len(self.levels) < 2:
            return forecast_df
        leaf = self.levels[-1]
        if leaf not in forecast_df.columns or value_col not in forecast_df.columns:
            log.warning("HierarchicalReconciler.mint: leaf or value column missing")
            return forecast_df

        result = forecast_df.copy()
        out_parts = []

        for date, block in result.groupby(date_col, sort=True):
            leaves = sorted(
                str(v) for v in block[block[leaf].notna()][leaf].dropna().unique()
            )
            if len(leaves) < 2:
                out_parts.append(block)
                continue

            base, index_map, rows = self._stack_levels(block, leaves, leaf, value_col)
            if base is None:
                out_parts.append(block)
                continue

            S = self._summing_matrix(block, leaves, leaf, rows)
            W = self._error_covariance(rows, leaves, residuals, shrinkage)

            try:
                W_inv = np.linalg.pinv(W)
                middle = np.linalg.pinv(S.T @ W_inv @ S)
                P = S @ middle @ S.T @ W_inv
                reconciled = P @ base
            except np.linalg.LinAlgError as exc:
                log.warning(f"MinT: projection failed on {date} ({exc}) — block left as is")
                out_parts.append(block)
                continue

            block = block.copy()
            for position, idx in index_map.items():
                block.loc[idx, value_col] = float(max(0.0, reconciled[position]))
            out_parts.append(block)

        return pd.concat(out_parts).sort_index() if out_parts else result

    # -- MinT internals -------------------------------------------------

    @staticmethod
    def _row_level(row, levels, leaf) -> Optional[str]:
        """The finest level this row actually identifies, or None."""
        for level in reversed(levels):
            value = row.get(level)
            if value is not None and not pd.isna(value):
                return level
        return None

    def _stack_levels(self, block, leaves, leaf, value_col):
        """Base forecast vector for one date, plus where each entry came from."""
        import numpy as np

        rows: list[tuple] = []
        index_map: dict[int, object] = {}
        values: list[float] = []

        for idx, row in block.iterrows():
            level = self._row_level(row, self.levels, leaf)
            if level is None:
                continue
            key = str(row[level])
            rows.append((level, key))
            index_map[len(values)] = idx
            values.append(float(row[value_col] or 0.0))

        if not values:
            return None, {}, []
        return np.array(values, dtype=float), index_map, rows

    def _summing_matrix(self, block, leaves, leaf, rows):
        """
        S: one row per forecast entry, one column per leaf, 1 where the leaf
        contributes to that entry.
        """
        import numpy as np

        leaf_pos = {key: i for i, key in enumerate(leaves)}
        # Which leaves sit under each (level, key) pair, read off the frame
        # itself rather than assumed — the hierarchy is whatever the data says.
        membership: dict[tuple, set] = {}
        for _idx, row in block.iterrows():
            leaf_value = row.get(leaf)
            if leaf_value is None or pd.isna(leaf_value):
                continue
            for level in self.levels:
                value = row.get(level)
                if value is None or pd.isna(value):
                    continue
                membership.setdefault((level, str(value)), set()).add(str(leaf_value))

        S = np.zeros((len(rows), len(leaves)))
        for r, (level, key) in enumerate(rows):
            if level == leaf:
                pos = leaf_pos.get(key)
                if pos is not None:
                    S[r, pos] = 1.0
                continue
            for child in membership.get((level, key), set()):
                pos = leaf_pos.get(child)
                if pos is not None:
                    S[r, pos] = 1.0
        return S

    @staticmethod
    def _error_covariance(rows, leaves, residuals, shrinkage):
        """
        W, shrunk toward its diagonal.

        The full sample covariance of forecast errors is singular long before a
        real catalogue has enough backtest points to estimate it — more series
        than observations is the normal case, not the exception. Shrinking
        toward the diagonal keeps it invertible and is what makes MinT usable
        outside a paper.
        """
        import numpy as np

        n = len(rows)
        variances = np.ones(n)
        if residuals:
            pooled = [float(np.var(v)) for v in residuals.values()
                      if v is not None and len(v) > 1]
            fallback = float(np.mean(pooled)) if pooled else 1.0
            for i, (_level, key) in enumerate(rows):
                sample = residuals.get(key)
                if sample is not None and len(sample) > 1:
                    variances[i] = max(float(np.var(sample)), 1e-9)
                else:
                    variances[i] = max(fallback, 1e-9)

        W = np.diag(variances)
        if shrinkage is not None:
            lam = float(min(max(shrinkage, 0.0), 1.0))
            W = lam * np.diag(np.diag(W)) + (1.0 - lam) * W
        return W
