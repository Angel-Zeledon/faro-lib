"""
EncodeAccessor — categorical encoding transformations.

Accessed via:
    dataset.categorical().encode.one_hot()
    dataset.cols(["country"]).encode.label()
    dataset.categorical().encode.auto()
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

import pandas as pd

from forecastlib.data.validators import DatasetValidator
from forecastlib.exceptions import TransformError
from forecastlib.preprocessing.transformers import TransformStep

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


# Cardinality thresholds for auto-encoding
_ONE_HOT_MAX_CARDINALITY  = 15
_LABEL_MAX_CARDINALITY    = 200


class EncodeAccessor:
    """
    Categorical encoding transformations.

    Methods
    -------
    one_hot(drop_first)      Expand to binary indicator columns
    label()                  Replace with integer codes (0..n-1)
    ordinal(order)           Ordinal encoding with a custom order
    binary()                 Binary (hash-based) encoding for high cardinality
    auto()                   Smart encoding based on cardinality analysis
    """

    def __init__(self, dataset: "Dataset", columns: List[str]):
        self._dataset = dataset
        self._columns = columns

    def _cat_cols(self, operation: str) -> List[str]:
        DatasetValidator.require_nonempty_selection(self._columns, f"encode.{operation}")
        return self._columns

    # ------------------------------------------------------------------
    # One-hot
    # ------------------------------------------------------------------

    def one_hot(self, drop_first: bool = False) -> "Dataset":
        """
        One-hot encode categorical columns.

        Creates binary indicator columns named <col>_<value>.
        The original column is dropped.

        Parameters
        ----------
        drop_first : bool, default False
            Drop the first category to avoid multicollinearity.

        >>> ds.categorical().encode.one_hot()
        >>> ds.cols(["country"]).encode.one_hot(drop_first=True)
        """
        cols = self._cat_cols("one_hot")
        df   = self._dataset._df.copy()

        for col in cols:
            if col not in df.columns:
                continue
            n_unique = df[col].nunique()
            if n_unique > 100:
                import warnings
                warnings.warn(
                    f"Column '{col}' has {n_unique} unique values. "
                    f"One-hot encoding will create {n_unique} new columns. "
                    f"Consider dataset.cols(['{col}']).encode.binary() instead.",
                    UserWarning, stacklevel=3,
                )
            dummies = pd.get_dummies(df[col], prefix=col, drop_first=drop_first, dtype=int)
            df = pd.concat([df.drop(columns=[col]), dummies], axis=1)

        step = TransformStep(
            name=f"OneHotEncoder on {cols}",
            kind="encode.one_hot",
            columns=cols,
            params={"drop_first": drop_first},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Label encoding
    # ------------------------------------------------------------------

    def label(self) -> "Dataset":
        """
        Label-encode each categorical column to integer codes (0..n-1).

        The original column is replaced in-place.

        >>> ds.cols(["country", "brand"]).encode.label()
        """
        from sklearn.preprocessing import LabelEncoder

        cols       = self._cat_cols("label")
        df         = self._dataset._df.copy()
        encoders: Dict[str, LabelEncoder] = {}

        for col in cols:
            if col not in df.columns:
                continue
            le = LabelEncoder()
            df[col] = le.fit_transform(df[col].astype(str))
            encoders[col] = le

        step = TransformStep(
            name=f"LabelEncoder on {cols}",
            kind="encode.label",
            columns=cols,
            params={},
            fitted=_MultiColLabelEncoder(encoders),
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Ordinal encoding
    # ------------------------------------------------------------------

    def ordinal(self, order: Optional[Dict[str, List]] = None) -> "Dataset":
        """
        Ordinal encoding with an optional explicit order per column.

        Parameters
        ----------
        order : dict of {col: [val1, val2, ...]}, optional
            If provided, maps categories to integers following the given order.
            If None, falls back to label encoding.

        >>> ds.cols(["size"]).encode.ordinal(order={"size": ["S", "M", "L", "XL"]})
        """
        cols = self._cat_cols("ordinal")
        df   = self._dataset._df.copy()
        order = order or {}

        for col in cols:
            if col not in df.columns:
                continue
            if col in order:
                mapping = {v: i for i, v in enumerate(order[col])}
                df[col] = df[col].map(mapping)
                n_unmapped = df[col].isna().sum()
                if n_unmapped > 0:
                    import warnings
                    warnings.warn(
                        f"Column '{col}': {n_unmapped} values not in the provided order. "
                        f"They will be NaN.",
                        UserWarning, stacklevel=3,
                    )
            else:
                from sklearn.preprocessing import OrdinalEncoder
                oe = OrdinalEncoder()
                df[[col]] = oe.fit_transform(df[[col]].astype(str))

        step = TransformStep(
            name=f"OrdinalEncoder on {cols}",
            kind="encode.ordinal",
            columns=cols,
            params={"order": {k: list(v) for k, v in order.items()}},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Binary (hash) encoding for high cardinality
    # ------------------------------------------------------------------

    def binary(self, n_components: int = 8) -> "Dataset":
        """
        Binary (hash-based) encoding — compact representation for high-cardinality.

        Creates n_components binary columns per original column.

        Parameters
        ----------
        n_components : int, default 8

        >>> ds.cols(["product_id"]).encode.binary()
        """
        cols = self._cat_cols("binary")
        df   = self._dataset._df.copy()

        for col in cols:
            if col not in df.columns:
                continue
            codes = df[col].astype("category").cat.codes
            for bit in range(n_components):
                df[f"{col}_bin{bit}"] = ((codes >> bit) & 1).astype(int)
            df.drop(columns=[col], inplace=True)

        step = TransformStep(
            name=f"BinaryEncoder(n={n_components}) on {cols}",
            kind="encode.binary",
            columns=cols,
            params={"n_components": n_components},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Auto — smart choice based on cardinality
    # ------------------------------------------------------------------

    def auto(self) -> "Dataset":
        """
        Automatically choose the best encoding strategy per column.

        Decision rules:
          n_unique <= 15  → one_hot
          n_unique <= 200 → label
          n_unique > 200  → binary (8-bit hash)

        Each column is encoded independently.

        >>> ds.categorical().encode.auto()
        """
        cols = self._cat_cols("auto")
        df   = self._dataset._df

        one_hot_cols, label_cols, binary_cols = [], [], []
        for col in cols:
            if col not in df.columns:
                continue
            n = df[col].nunique()
            if n <= _ONE_HOT_MAX_CARDINALITY:
                one_hot_cols.append(col)
            elif n <= _LABEL_MAX_CARDINALITY:
                label_cols.append(col)
            else:
                binary_cols.append(col)

        # Apply in priority order (binary and label first to avoid index issues)
        if binary_cols:
            EncodeAccessor(self._dataset, binary_cols).binary()
        if label_cols:
            EncodeAccessor(self._dataset, label_cols).label()
        if one_hot_cols:
            EncodeAccessor(self._dataset, one_hot_cols).one_hot()

        step = TransformStep(
            name=f"AutoEncoder on {cols}",
            kind="encode.auto",
            columns=cols,
            params={
                "one_hot": one_hot_cols,
                "label":   label_cols,
                "binary":  binary_cols,
            },
        )
        self._dataset._registry.add(step)
        return self._dataset


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

class _MultiColLabelEncoder:
    """Applies per-column LabelEncoders for Pipeline.transform()."""

    def __init__(self, encoders: Dict):
        self.encoders = encoders

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for col, le in self.encoders.items():
            if col in df.columns:
                df[col] = le.transform(df[col].astype(str))
        return df
