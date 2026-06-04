"""
Time-series feature generation: lags, rolling windows, EWM, differences.

All functions are shift(1) anti-leakage by default — no future data
is used to compute features for the current timestep.

Accessed via:
    dataset.target().lags([1, 7, 14])
    dataset.target().rolling.mean([7, 30])
    dataset.target().rolling.std([7, 30])
    dataset.target().ewm([7, 14])
    dataset.target().diffs([1, 7])
"""

from __future__ import annotations

from typing import List, TYPE_CHECKING

import pandas as pd

from forecastlib.preprocessing.transformers import TransformStep

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


# ---------------------------------------------------------------------------
# Lag features
# ---------------------------------------------------------------------------

def _apply_lags(
    dataset: "Dataset",
    columns: List[str],
    periods: List[int],
) -> "Dataset":
    """
    Create lag_N columns for each (column, period) pair.

    Computed per-group if a group column is configured.
    shift(N) ensures no leakage — lag_1 at time t = value at time t-1.
    """
    schema = dataset._schema
    df     = dataset._df.copy()
    group  = schema.group_col if schema.group_col and schema.group_col in df.columns else None

    new_cols = []
    for col in columns:
        if col not in df.columns:
            continue
        for p in sorted(periods):
            name = f"{col}_lag{p}"
            if group:
                df[name] = df.groupby(group)[col].shift(p)
            else:
                df[name] = df[col].shift(p)
            new_cols.append(name)

    step = TransformStep(
        name=f"lags{periods} on {columns}  →  {new_cols}",
        kind="ts.lags",
        columns=columns,
        params={"periods": periods, "group_col": group, "new_cols": new_cols},
    )
    return dataset._mutate(df, step)


# ---------------------------------------------------------------------------
# Differencing
# ---------------------------------------------------------------------------

def _apply_diffs(
    dataset: "Dataset",
    columns: List[str],
    periods: List[int],
) -> "Dataset":
    """
    Create diff_N columns: value(t) - value(t-N).
    """
    schema = dataset._schema
    df     = dataset._df.copy()
    group  = schema.group_col if schema.group_col and schema.group_col in df.columns else None

    new_cols = []
    for col in columns:
        if col not in df.columns:
            continue
        for p in sorted(periods):
            name = f"{col}_diff{p}"
            if group:
                df[name] = df.groupby(group)[col].diff(p)
            else:
                df[name] = df[col].diff(p)
            new_cols.append(name)

    step = TransformStep(
        name=f"diffs{periods} on {columns}",
        kind="ts.diffs",
        columns=columns,
        params={"periods": periods, "group_col": group},
    )
    return dataset._mutate(df, step)


# ---------------------------------------------------------------------------
# EWM
# ---------------------------------------------------------------------------

def _apply_ewm(
    dataset: "Dataset",
    columns: List[str],
    spans: List[int],
) -> "Dataset":
    """
    Create ewm_N columns.  shift(1) before computing EWM ensures no leakage.
    """
    schema = dataset._schema
    df     = dataset._df.copy()
    group  = schema.group_col if schema.group_col and schema.group_col in df.columns else None

    new_cols = []
    for col in columns:
        if col not in df.columns:
            continue
        for span in sorted(spans):
            name = f"{col}_ewm{span}"
            if group:
                df[name] = df.groupby(group)[col].transform(
                    lambda s: s.shift(1).ewm(span=span, min_periods=1).mean()
                )
            else:
                df[name] = df[col].shift(1).ewm(span=span, min_periods=1).mean()
            new_cols.append(name)

    step = TransformStep(
        name=f"ewm{spans} on {columns}",
        kind="ts.ewm",
        columns=columns,
        params={"spans": spans, "group_col": group},
    )
    return dataset._mutate(df, step)


# ---------------------------------------------------------------------------
# RollingAccessor — accessed via ColumnView.rolling
# ---------------------------------------------------------------------------

class RollingAccessor:
    """
    Rolling-window feature accessor.

    Accessed via ``ColumnView.rolling``:

        dataset.target().rolling.mean([7, 30])
        dataset.target().rolling.std([7, 30])
        dataset.target().rolling.min([7, 30])
        dataset.target().rolling.max([7, 30])
        dataset.target().rolling.sum([7, 30])
        dataset.target().rolling.median([7, 30])

    All methods apply shift(1) to prevent data leakage.
    """

    def __init__(self, dataset: "Dataset", columns: List[str]):
        self._dataset = dataset
        self._columns = columns

    def _apply(self, windows: List[int], fn: str) -> "Dataset":
        schema = self._dataset._schema
        df     = self._dataset._df.copy()
        group  = schema.group_col if schema.group_col and schema.group_col in df.columns else None

        new_cols = []
        for col in self._columns:
            if col not in df.columns:
                continue
            for w in sorted(windows):
                name = f"{col}_roll{fn[:3]}{w}"
                if group:
                    df[name] = df.groupby(group)[col].transform(
                        lambda s, w=w, fn=fn: getattr(
                            s.shift(1).rolling(w, min_periods=1), fn
                        )()
                    )
                else:
                    rolled = df[col].shift(1).rolling(w, min_periods=1)
                    df[name] = getattr(rolled, fn)()
                new_cols.append(name)

        step = TransformStep(
            name=f"rolling.{fn}({windows}) on {self._columns}",
            kind=f"ts.rolling.{fn}",
            columns=self._columns,
            params={"windows": windows, "fn": fn, "group_col": group},
        )
        return self._dataset._mutate(df, step)

    def mean(self, windows: List[int]) -> "Dataset":
        """Rolling mean. shift(1) anti-leakage. >>> ds.target().rolling.mean([7, 30])"""
        return self._apply(windows, "mean")

    def std(self, windows: List[int]) -> "Dataset":
        """Rolling standard deviation."""
        return self._apply(windows, "std")

    def min(self, windows: List[int]) -> "Dataset":
        """Rolling minimum."""
        return self._apply(windows, "min")

    def max(self, windows: List[int]) -> "Dataset":
        """Rolling maximum."""
        return self._apply(windows, "max")

    def sum(self, windows: List[int]) -> "Dataset":
        """Rolling sum."""
        return self._apply(windows, "sum")

    def median(self, windows: List[int]) -> "Dataset":
        """Rolling median."""
        return self._apply(windows, "median")

    def cv(self, windows: List[int]) -> "Dataset":
        """
        Rolling coefficient of variation (std / mean).
        Useful as a volatility indicator.
        """
        schema = self._dataset._schema
        df     = self._dataset._df.copy()
        group  = schema.group_col if schema.group_col and schema.group_col in df.columns else None

        for col in self._columns:
            if col not in df.columns:
                continue
            for w in sorted(windows):
                name = f"{col}_rollcv{w}"
                if group:
                    mean = df.groupby(group)[col].transform(
                        lambda s, w=w: s.shift(1).rolling(w, min_periods=2).mean()
                    )
                    std  = df.groupby(group)[col].transform(
                        lambda s, w=w: s.shift(1).rolling(w, min_periods=2).std()
                    )
                else:
                    shifted = df[col].shift(1)
                    mean = shifted.rolling(w, min_periods=2).mean()
                    std  = shifted.rolling(w, min_periods=2).std()
                df[name] = std / (mean.abs() + 1e-9)

        step = TransformStep(
            name=f"rolling.cv({windows}) on {self._columns}",
            kind="ts.rolling.cv",
            columns=self._columns,
            params={"windows": windows},
        )
        return self._dataset._mutate(df, step)
