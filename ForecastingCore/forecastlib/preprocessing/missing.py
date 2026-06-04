"""
FillAccessor — missing-value imputation strategies.

Accessed via:
    dataset.fill.smart()
    dataset.numeric().fill.median()
    dataset.categorical().fill.mode()
    dataset.cols(["sales"]).fill.forward()
    dataset.fill.time_series()

All methods return the parent Dataset for chaining.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import pandas as pd
import numpy as np

from forecastlib.preprocessing.transformers import TransformStep

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


class FillAccessor:
    """
    Missing-value imputation.

    Methods
    -------
    mean()                      Fill with column mean
    median()                    Fill with column median
    mode()                      Fill with column mode (most frequent)
    constant(value)             Fill with a fixed value
    forward(limit)              Forward fill (ffill)
    backward(limit)             Backward fill (bfill)
    interpolate(method)         Pandas interpolation
    smart()                     Auto-select best strategy per column
    time_series()               Group-aware forward fill for panel data
    knn(n_neighbors)            KNN imputation (sklearn)
    """

    def __init__(self, dataset: "Dataset", columns: List[str]):
        self._dataset = dataset
        self._columns = [c for c in columns if c in dataset._df.columns]

    def _cols_with_nulls(self) -> List[str]:
        df = self._dataset._df
        return [c for c in self._columns if df[c].isna().any()]

    # ------------------------------------------------------------------
    # Statistical fills
    # ------------------------------------------------------------------

    def mean(self) -> "Dataset":
        """Fill nulls with column mean (numeric columns only)."""
        df = self._dataset._df.copy()
        filled: Dict[str, float] = {}
        for col in self._cols_with_nulls():
            if pd.api.types.is_numeric_dtype(df[col]):
                val = float(df[col].mean())
                df[col] = df[col].fillna(val)
                filled[col] = val
        step = TransformStep(
            name=f"fill.mean on {list(filled.keys())}",
            kind="fill.mean",
            columns=list(filled.keys()),
            params={"fill_values": filled},
        )
        return self._dataset._mutate(df, step)

    def median(self) -> "Dataset":
        """Fill nulls with column median (numeric columns only)."""
        df = self._dataset._df.copy()
        filled: Dict[str, float] = {}
        for col in self._cols_with_nulls():
            if pd.api.types.is_numeric_dtype(df[col]):
                val = float(df[col].median())
                df[col] = df[col].fillna(val)
                filled[col] = val
        step = TransformStep(
            name=f"fill.median on {list(filled.keys())}",
            kind="fill.median",
            columns=list(filled.keys()),
            params={"fill_values": filled},
        )
        return self._dataset._mutate(df, step)

    def mode(self) -> "Dataset":
        """Fill nulls with the most frequent value (works for any dtype)."""
        df = self._dataset._df.copy()
        filled: Dict[str, Any] = {}
        for col in self._cols_with_nulls():
            mode_val = df[col].mode()
            if len(mode_val) > 0:
                df[col] = df[col].fillna(mode_val.iloc[0])
                filled[col] = str(mode_val.iloc[0])
        step = TransformStep(
            name=f"fill.mode on {list(filled.keys())}",
            kind="fill.mode",
            columns=list(filled.keys()),
            params={"fill_values": filled},
        )
        return self._dataset._mutate(df, step)

    def constant(self, value: Any) -> "Dataset":
        """Fill all nulls in selected columns with a fixed constant value."""
        df = self._dataset._df.copy()
        cols_filled = []
        for col in self._cols_with_nulls():
            df[col] = df[col].fillna(value)
            cols_filled.append(col)
        step = TransformStep(
            name=f"fill.constant({value}) on {cols_filled}",
            kind="fill.constant",
            columns=cols_filled,
            params={"value": value},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Temporal fills
    # ------------------------------------------------------------------

    def forward(self, limit: Optional[int] = None) -> "Dataset":
        """
        Forward-fill (carry last valid value forward).

        Parameters
        ----------
        limit : int, optional
            Maximum number of consecutive NaN values to fill.

        >>> ds.cols(["sales"]).fill.forward()
        >>> ds.cols(["price"]).fill.forward(limit=3)
        """
        df = self._dataset._df.copy()
        for col in self._cols_with_nulls():
            df[col] = df[col].ffill(limit=limit)
        step = TransformStep(
            name=f"fill.forward(limit={limit}) on {self._columns}",
            kind="fill.forward",
            columns=self._columns,
            params={"limit": limit},
        )
        return self._dataset._mutate(df, step)

    def backward(self, limit: Optional[int] = None) -> "Dataset":
        """Backward-fill (carry next valid value backward)."""
        df = self._dataset._df.copy()
        for col in self._cols_with_nulls():
            df[col] = df[col].bfill(limit=limit)
        step = TransformStep(
            name=f"fill.backward(limit={limit}) on {self._columns}",
            kind="fill.backward",
            columns=self._columns,
            params={"limit": limit},
        )
        return self._dataset._mutate(df, step)

    def interpolate(self, method: str = "linear") -> "Dataset":
        """
        Interpolate missing values.

        Parameters
        ----------
        method : str, default "linear"
            Interpolation method.  See pd.Series.interpolate for options.

        >>> ds.numeric().fill.interpolate()
        >>> ds.cols(["sales"]).fill.interpolate(method="time")
        """
        df = self._dataset._df.copy()
        for col in self._cols_with_nulls():
            if pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].interpolate(method=method)
        step = TransformStep(
            name=f"fill.interpolate(method={method!r})",
            kind="fill.interpolate",
            columns=self._columns,
            params={"method": method},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Smart auto-fill
    # ------------------------------------------------------------------

    def smart(self) -> "Dataset":
        """
        Automatically select the best imputation strategy per column.

        Decision logic:
          - Numeric with < 5% nulls → median
          - Numeric with >= 5% nulls → interpolation
          - Categorical / object → mode
          - Boolean → mode
          - Datetime → forward fill

        >>> ds.fill.smart()
        """
        df      = self._dataset._df.copy()
        actions: Dict[str, str] = {}

        for col in self._cols_with_nulls():
            s = df[col]
            null_rate = s.isna().mean()

            if pd.api.types.is_datetime64_any_dtype(s):
                df[col] = s.ffill()
                actions[col] = "ffill"

            elif pd.api.types.is_numeric_dtype(s):
                if null_rate < 0.05:
                    df[col] = s.fillna(float(s.median()))
                    actions[col] = f"median ({float(s.median()):.4g})"
                else:
                    df[col] = s.interpolate(method="linear")
                    actions[col] = "interpolate"

            elif s.dtype in ("object", "category") or str(s.dtype) == "string":
                mode_val = s.mode()
                if len(mode_val) > 0:
                    df[col] = s.fillna(mode_val.iloc[0])
                    actions[col] = f"mode ({mode_val.iloc[0]!r})"

            else:
                df[col] = s.ffill()
                actions[col] = "ffill"

        step = TransformStep(
            name=f"fill.smart (actions: {actions})",
            kind="fill.smart",
            columns=list(actions.keys()),
            params={"actions": actions},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Time-series-aware fill (group-aware ffill)
    # ------------------------------------------------------------------

    def time_series(self) -> "Dataset":
        """
        Time-series-aware forward fill.

        If a group column is configured, fills within each group separately
        (correct for panel datasets — prevents data from one SKU polluting another).

        After ffill, any remaining nulls at the start of the series are
        filled with backward fill or zero (depending on dtype).

        >>> ds.fill.time_series()
        """
        schema = self._dataset._schema
        df     = self._dataset._df.copy()

        if schema.datetime_col and schema.datetime_col in df.columns:
            sort_by = ([schema.group_col, schema.datetime_col]
                       if schema.group_col and schema.group_col in df.columns
                       else [schema.datetime_col])
            df = df.sort_values(sort_by).reset_index(drop=True)

        cols_with_nulls = self._cols_with_nulls()

        if schema.group_col and schema.group_col in df.columns:
            for col in cols_with_nulls:
                df[col] = (
                    df.groupby(schema.group_col)[col]
                    .transform(lambda s: s.ffill().bfill())
                )
        else:
            for col in cols_with_nulls:
                df[col] = df[col].ffill().bfill()

        # Fill any remaining nulls with 0 for numeric
        for col in cols_with_nulls:
            if pd.api.types.is_numeric_dtype(df[col]) and df[col].isna().any():
                df[col] = df[col].fillna(0)

        step = TransformStep(
            name=f"fill.time_series on {cols_with_nulls}",
            kind="fill.time_series",
            columns=cols_with_nulls,
            params={"group_col": schema.group_col},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # KNN imputation
    # ------------------------------------------------------------------

    def knn(self, n_neighbors: int = 5) -> "Dataset":
        """
        KNN imputation using sklearn's KNNImputer.

        Only applied to numeric columns.

        Parameters
        ----------
        n_neighbors : int, default 5

        >>> ds.numeric().fill.knn()
        """
        from sklearn.impute import KNNImputer

        df  = self._dataset._df.copy()
        num = [c for c in self._cols_with_nulls()
               if pd.api.types.is_numeric_dtype(df[c])]

        if num:
            imputer = KNNImputer(n_neighbors=n_neighbors)
            df[num] = imputer.fit_transform(df[num])

        step = TransformStep(
            name=f"fill.knn(n={n_neighbors}) on {num}",
            kind="fill.knn",
            columns=num,
            params={"n_neighbors": n_neighbors},
            fitted=_KNNWrapper(imputer if num else None, num),
        )
        return self._dataset._mutate(df, step)


class _KNNWrapper:
    def __init__(self, imputer, columns):
        self.imputer = imputer
        self.columns = columns

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.imputer is None:
            return df
        df = df.copy()
        valid = [c for c in self.columns if c in df.columns]
        if valid:
            df[valid] = self.imputer.transform(df[valid])
        return df
