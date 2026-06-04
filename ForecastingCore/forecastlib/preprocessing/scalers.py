"""
ScaleAccessor — numeric scaling transformations.

Accessed via:
    dataset.numeric().scale.standard()
    dataset.cols(["price"]).scale.minmax()
    dataset.cols(["revenue"]).scale.log()

All methods return the parent Dataset for chaining.
"""

from __future__ import annotations

from typing import List, Tuple, TYPE_CHECKING

import numpy as np
import pandas as pd

from forecastlib.data.validators import DatasetValidator
from forecastlib.exceptions import TransformError
from forecastlib.preprocessing.transformers import TransformStep

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


class ScaleAccessor:
    """
    Numeric scaling transformations.

    Accessed via ``ColumnView.scale`` or ``Dataset.numeric().scale``.

    Methods
    -------
    standard()           StandardScaler  (z-score normalisation)
    minmax(range)        MinMaxScaler
    robust()             RobustScaler    (IQR-based, outlier-resistant)
    log(base, offset)    log(x + offset) transform
    power()              Yeo-Johnson power transform
    """

    def __init__(self, dataset: "Dataset", columns: List[str]):
        self._dataset = dataset
        self._columns = columns

    def _validated_cols(self, operation: str) -> List[str]:
        """Filter to numeric columns, validate, warn about target scaling."""
        df = self._dataset._df
        DatasetValidator.require_nonempty_selection(self._columns, f"scale.{operation}")
        numeric_cols = [c for c in self._columns if pd.api.types.is_numeric_dtype(df[c])]
        non_numeric  = [c for c in self._columns if c not in numeric_cols]
        if non_numeric:
            raise TransformError(
                f"Cannot scale non-numeric column(s): {non_numeric}.",
                suggestions=[
                    f"dataset.categorical().encode.one_hot()           # encode first",
                    f"dataset.cols({non_numeric}).encode.label()        # or label-encode",
                    f"dataset.exclude({non_numeric}).scale.{operation}() # or exclude them",
                ],
            )
        DatasetValidator.warn_target_scaling(numeric_cols, self._dataset._schema)
        return numeric_cols

    # ------------------------------------------------------------------
    # Standard (z-score)
    # ------------------------------------------------------------------

    def standard(self) -> "Dataset":
        """
        Standardize to zero mean, unit variance.

        Uses sklearn's StandardScaler. Fitted on current data.
        The fitted scaler is stored in the TransformStep for pipeline reuse.

        >>> ds.numeric().scale.standard()
        >>> ds.cols(["price", "revenue"]).scale.standard()
        """
        from sklearn.preprocessing import StandardScaler
        cols = self._validated_cols("standard")
        df   = self._dataset._df.copy()

        scaler = StandardScaler()
        df[cols] = scaler.fit_transform(df[cols])

        step = TransformStep(
            name=f"StandardScaler on {cols}",
            kind="scale.standard",
            columns=cols,
            params={},
            fitted=_SklearnColScaler(scaler, cols),
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # MinMax
    # ------------------------------------------------------------------

    def minmax(self, feature_range: Tuple[float, float] = (0.0, 1.0)) -> "Dataset":
        """
        Scale to a fixed range [min, max].

        Parameters
        ----------
        feature_range : tuple, default (0, 1)

        >>> ds.numeric().scale.minmax()
        >>> ds.numeric().scale.minmax(feature_range=(-1, 1))
        """
        from sklearn.preprocessing import MinMaxScaler
        cols = self._validated_cols("minmax")
        df   = self._dataset._df.copy()

        scaler = MinMaxScaler(feature_range=feature_range)
        df[cols] = scaler.fit_transform(df[cols])

        step = TransformStep(
            name=f"MinMaxScaler{feature_range} on {cols}",
            kind="scale.minmax",
            columns=cols,
            params={"feature_range": list(feature_range)},
            fitted=_SklearnColScaler(scaler, cols),
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Robust (IQR-based)
    # ------------------------------------------------------------------

    def robust(self) -> "Dataset":
        """
        Scale using IQR — robust to outliers.

        >>> ds.numeric().exclude(["id"]).scale.robust()
        """
        from sklearn.preprocessing import RobustScaler
        cols = self._validated_cols("robust")
        df   = self._dataset._df.copy()

        scaler = RobustScaler()
        df[cols] = scaler.fit_transform(df[cols])

        step = TransformStep(
            name=f"RobustScaler on {cols}",
            kind="scale.robust",
            columns=cols,
            params={},
            fitted=_SklearnColScaler(scaler, cols),
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Log transform
    # ------------------------------------------------------------------

    def log(self, base: float = None, offset: float = 1.0) -> "Dataset":
        """
        Apply log(x + offset) transform.

        Parameters
        ----------
        base : float, optional
            Logarithm base.  None → natural log.
        offset : float, default 1.0
            Added before taking the log (ensures x > 0).

        >>> ds.cols(["revenue"]).scale.log()
        >>> ds.cols(["sales"]).scale.log(base=10, offset=0)
        """
        cols = self._validated_cols("log")
        DatasetValidator.require_positive(
            self._dataset._df, cols,
            operation="log transform",
        )
        df = self._dataset._df.copy()

        for col in cols:
            vals = df[col].astype(float) + offset
            df[col] = np.log(vals) if base is None else np.log(vals) / np.log(base)

        step = TransformStep(
            name=f"Log(base={base}, offset={offset}) on {cols}",
            kind="scale.log",
            columns=cols,
            params={"base": base, "offset": offset},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Power (Yeo-Johnson)
    # ------------------------------------------------------------------

    def power(self) -> "Dataset":
        """
        Yeo-Johnson power transform — makes distributions more Gaussian.

        Works with positive and negative values.

        >>> ds.cols(["revenue"]).scale.power()
        """
        from sklearn.preprocessing import PowerTransformer
        cols = self._validated_cols("power")
        df   = self._dataset._df.copy()

        pt = PowerTransformer(method="yeo-johnson", standardize=True)
        df[cols] = pt.fit_transform(df[cols])

        step = TransformStep(
            name=f"PowerTransformer(yeo-johnson) on {cols}",
            kind="scale.power",
            columns=cols,
            params={"method": "yeo-johnson"},
            fitted=_SklearnColScaler(pt, cols),
        )
        return self._dataset._mutate(df, step)


# ---------------------------------------------------------------------------
# Internal: thin wrapper that makes any sklearn scaler column-aware
# ---------------------------------------------------------------------------

class _SklearnColScaler:
    """Wraps a fitted sklearn scaler to transform only the specified columns."""

    def __init__(self, scaler, columns: List[str]):
        self.scaler  = scaler
        self.columns = columns

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        valid = [c for c in self.columns if c in df.columns]
        if valid:
            df[valid] = self.scaler.transform(df[valid])
        return df
