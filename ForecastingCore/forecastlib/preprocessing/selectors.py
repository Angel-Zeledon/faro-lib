"""
ColumnView — the column-scoped transformation proxy.

ColumnView is constructed by Dataset selection methods and acts as the
"left-hand side" of every transformation expression:

    dataset.numeric()            →  ColumnView(numeric cols)
    dataset.cols(["price"])      →  ColumnView(["price"])
    dataset.target()             →  ColumnView([target_col])

The right-hand side is always an Accessor property or a direct method:

    .scale.standard()      →  ScaleAccessor.standard() → Dataset
    .encode.one_hot()      →  EncodeAccessor.one_hot() → Dataset
    .fill.median()         →  FillAccessor.median()    → Dataset
    .rolling.mean([7,30])  →  RollingAccessor.mean()   → Dataset
    .lags([1,7,14])        →  direct method             → Dataset
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset
    from forecastlib.preprocessing.cleaners import CleanAccessor
    from forecastlib.preprocessing.encoders import EncodeAccessor
    from forecastlib.preprocessing.scalers import ScaleAccessor
    from forecastlib.preprocessing.missing import FillAccessor
    from forecastlib.time_series.features import RollingAccessor
    from forecastlib.time_series.calendar import FeaturesAccessor

from forecastlib.exceptions import SelectionError


class ColumnView:
    """
    A scoped view over a Dataset's columns.

    This is the hub for all transformation accessors.  It holds a reference
    to the parent Dataset plus the list of selected column names.

    Parameters
    ----------
    dataset : Dataset
        The parent Dataset.
    columns : list of str
        Selected column names.
    label : str, optional
        Human-readable description used in repr and logs.
    """

    def __init__(
        self,
        dataset: "Dataset",
        columns: List[str],
        label: str = "",
    ):
        self._dataset = dataset
        self._columns = list(columns)
        self._label   = label

    # ------------------------------------------------------------------
    # View modification
    # ------------------------------------------------------------------

    def exclude(self, cols: List[str]) -> "ColumnView":
        """
        Remove columns from this view.

        >>> dataset.numeric().exclude(["id", "year"]).scale.standard()
        """
        remaining = [c for c in self._columns if c not in cols]
        if not remaining:
            raise SelectionError(
                f"After excluding {cols}, no columns remain in the selection.",
                suggestions=["Check the column names you are excluding."],
            )
        return ColumnView(self._dataset, remaining, label=f"{self._label}.exclude({cols})")

    # ------------------------------------------------------------------
    # Transformation accessor properties
    # ------------------------------------------------------------------

    @property
    def scale(self) -> "ScaleAccessor":
        """Scaling transformations: standard, minmax, robust, log, power."""
        from forecastlib.preprocessing.scalers import ScaleAccessor
        return ScaleAccessor(self._dataset, self._columns)

    @property
    def encode(self) -> "EncodeAccessor":
        """Encoding transformations: one_hot, label, ordinal, auto."""
        from forecastlib.preprocessing.encoders import EncodeAccessor
        return EncodeAccessor(self._dataset, self._columns)

    @property
    def fill(self) -> "FillAccessor":
        """Missing-value filling: mean, median, mode, forward, smart."""
        from forecastlib.preprocessing.missing import FillAccessor
        return FillAccessor(self._dataset, self._columns)

    @property
    def clean(self) -> "CleanAccessor":
        """Cleaning operations: clip, strip, fix_datetime, drop_nulls."""
        from forecastlib.preprocessing.cleaners import CleanAccessor
        return CleanAccessor(self._dataset, self._columns)

    @property
    def rolling(self) -> "RollingAccessor":
        """Rolling-window features: mean, std, min, max, sum."""
        from forecastlib.time_series.features import RollingAccessor
        return RollingAccessor(self._dataset, self._columns)

    @property
    def features(self) -> "FeaturesAccessor":
        """Datetime feature generation: calendar, holidays."""
        from forecastlib.time_series.calendar import FeaturesAccessor
        return FeaturesAccessor(self._dataset, self._columns)

    # ------------------------------------------------------------------
    # Direct time-series methods (terminal → return Dataset)
    # ------------------------------------------------------------------

    def lags(self, periods: List[int]) -> "Dataset":
        """
        Add lag features for the selected column(s).

        Creates columns named lag_1, lag_7, etc.  If a group column is
        configured, lags are computed per-group (correct for panel data).

        Parameters
        ----------
        periods : list of int
            Lag periods, e.g. [1, 7, 14].

        Returns
        -------
        Dataset

        Examples
        --------
        >>> ds.target().lags([1, 7, 14])
        >>> ds.cols(["sales", "promo"]).lags([1, 7])
        """
        from forecastlib.time_series.features import _apply_lags
        return _apply_lags(self._dataset, self._columns, periods)

    def ewm(self, spans: List[int]) -> "Dataset":
        """
        Add Exponentially Weighted Mean features.

        Creates columns named ewm_7, ewm_14, etc. (shift(1) anti-leakage).

        Parameters
        ----------
        spans : list of int
            EWM span values.

        >>> ds.target().ewm([7, 14, 28])
        """
        from forecastlib.time_series.features import _apply_ewm
        return _apply_ewm(self._dataset, self._columns, spans)

    def diffs(self, periods: List[int]) -> "Dataset":
        """
        Add differencing features.

        Creates columns named diff_1, diff_7, etc.

        Parameters
        ----------
        periods : list of int

        >>> ds.target().diffs([1, 7])
        """
        from forecastlib.time_series.features import _apply_diffs
        return _apply_diffs(self._dataset, self._columns, periods)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def column_names(self) -> List[str]:
        return list(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def __repr__(self) -> str:
        label = f" ({self._label})" if self._label else ""
        return f"ColumnView{label}[{', '.join(self._columns[:6])}{'...' if len(self._columns) > 6 else ''}]"
