"""
Dataset — the central object of forecastlib.

A Dataset wraps a pandas DataFrame and adds:
  * Semantic role metadata (target, datetime, group)
  * A chainable, discoverable transformation API
  * Automatic schema inference
  * Transformation logging for reproducibility
  * Self-describing inspection utilities

Design contract
---------------
Every terminal transformation method (e.g. .scale.standard(), .fill.smart())
returns `self` (the Dataset) so operations can be chained naturally:

    dataset = (
        Loader.from_csv("sales.csv")
        .select(target="sales", datetime="date", group="store")
        .clean.fix_datetime()
        .fill.smart()
        .categorical().encode.auto()
        .numeric().scale.standard()
        .target().lags([1, 7, 14])
    )
"""

from __future__ import annotations

import copy
import warnings
from typing import Dict, List, Optional, Union, TYPE_CHECKING

import pandas as pd

from forecastlib.data.schema import SchemaInfo
from forecastlib.exceptions import SchemaError, SelectionError, ConfigurationError
from forecastlib.preprocessing.transformers import TransformRegistry, TransformStep

if TYPE_CHECKING:
    from forecastlib.preprocessing.selectors import ColumnView
    from forecastlib.preprocessing.cleaners import CleanAccessor
    from forecastlib.preprocessing.missing import FillAccessor
    from forecastlib.inspection.profiling import InspectAccessor
    from forecastlib.pipeline.pipeline import Pipeline


class Dataset:
    """
    A smart DataFrame wrapper for machine learning and forecasting.

    Exposes a clean, chainable API for selecting column subsets and
    applying transformations.  Every operation is logged for reproducibility.

    Parameters
    ----------
    df : pd.DataFrame
        Raw data.
    schema : SchemaInfo, optional
        Pre-built schema.  Inferred automatically if not provided.
    source : str, optional
        Human-readable description of where the data came from.

    Examples
    --------
    >>> from forecastlib.data import Loader
    >>> ds = Loader.from_csv("sales.csv")
    >>> ds = ds.select(target="sales", datetime="date", group="store")
    >>> ds = ds.numeric().scale.standard()
    """

    def __init__(
        self,
        df: pd.DataFrame,
        schema: Optional[SchemaInfo] = None,
        source: str = "unknown",
    ):
        self._df: pd.DataFrame = df.copy()
        self._schema: SchemaInfo = schema if schema is not None else SchemaInfo.infer(df)
        self._source: str = source
        self._registry: TransformRegistry = TransformRegistry()

    # ------------------------------------------------------------------
    # Semantic configuration
    # ------------------------------------------------------------------

    def select(
        self,
        target: Optional[str] = None,
        datetime: Optional[str] = None,
        group: Optional[str] = None,
        features: Optional[List[str]] = None,
    ) -> "Dataset":
        """
        Assign semantic roles to columns.

        Parameters
        ----------
        target : str
            Column to forecast / predict.
        datetime : str
            Timestamp / date column.
        group : str
            Grouping key (e.g. SKU, store, product).
        features : list of str, optional
            Explicit list of feature columns (defaults to all other columns).

        Returns
        -------
        Dataset
            Self, for chaining.

        Examples
        --------
        >>> ds = ds.select(target="sales", datetime="date", group="store")
        """
        if target:
            if target not in self._df.columns:
                raise SchemaError(
                    f"Target column '{target}' not found in dataset.",
                    suggestions=[f"Available columns: {list(self._df.columns)}"],
                )
            self._schema.set_target(target)

        if datetime:
            if datetime not in self._df.columns:
                raise SchemaError(
                    f"Datetime column '{datetime}' not found in dataset.",
                    suggestions=[f"Available columns: {list(self._df.columns)}"],
                )
            self._schema.set_datetime(datetime)

        if group:
            if group not in self._df.columns:
                raise SchemaError(
                    f"Group column '{group}' not found in dataset.",
                    suggestions=[f"Available columns: {list(self._df.columns)}"],
                )
            self._schema.set_group(group)

        if features is not None:
            missing = [c for c in features if c not in self._df.columns]
            if missing:
                raise SchemaError(f"Feature columns not found: {missing}")
            self._schema.feature_cols = features

        return self

    # ------------------------------------------------------------------
    # Column selection → ColumnView
    # ------------------------------------------------------------------

    def cols(self, names: List[str]) -> "ColumnView":
        """
        Select specific columns by name.

        >>> ds.cols(["price", "promo"]).scale.standard()
        """
        from forecastlib.preprocessing.selectors import ColumnView
        missing = [c for c in names if c not in self._df.columns]
        if missing:
            raise SelectionError(
                f"Columns not found: {missing}",
                suggestions=[f"Available: {list(self._df.columns)}"],
            )
        return ColumnView(self, names)

    def numeric(self) -> "ColumnView":
        """Select all numeric (float/int) columns."""
        from forecastlib.preprocessing.selectors import ColumnView
        cols = [c for c in self._df.columns if pd.api.types.is_numeric_dtype(self._df[c])]
        return ColumnView(self, cols, label="numeric")

    def categorical(self) -> "ColumnView":
        """Select all categorical and object-dtype columns."""
        from forecastlib.preprocessing.selectors import ColumnView
        cols = [c for c in self._df.columns
                if self._df[c].dtype in ("object", "category")
                or str(self._df[c].dtype) == "string"]
        return ColumnView(self, cols, label="categorical")

    def datetime(self) -> "ColumnView":
        """Select all datetime columns (including the configured datetime column)."""
        from forecastlib.preprocessing.selectors import ColumnView
        cols = [c for c in self._df.columns
                if pd.api.types.is_datetime64_any_dtype(self._df[c])]
        # Always include the configured datetime col even if not yet parsed
        if self._schema.datetime_col and self._schema.datetime_col not in cols:
            cols.append(self._schema.datetime_col)
        return ColumnView(self, cols, label="datetime")

    def boolean(self) -> "ColumnView":
        """Select all boolean columns."""
        from forecastlib.preprocessing.selectors import ColumnView
        cols = [c for c in self._df.columns if pd.api.types.is_bool_dtype(self._df[c])]
        return ColumnView(self, cols, label="boolean")

    def target(self) -> "ColumnView":
        """
        Select the target column.
        Requires .select(target=...) to have been called first.
        """
        from forecastlib.preprocessing.selectors import ColumnView
        from forecastlib.data.validators import DatasetValidator
        DatasetValidator.require_target_set(self._schema)
        return ColumnView(self, [self._schema.target_col], label="target")

    def group(self) -> "ColumnView":
        """
        Select the group column.
        Requires .select(group=...) to have been called first.
        """
        from forecastlib.preprocessing.selectors import ColumnView
        from forecastlib.data.validators import DatasetValidator
        DatasetValidator.require_group_set(self._schema)
        return ColumnView(self, [self._schema.group_col], label="group")

    def exclude(self, columns: List[str]) -> "ColumnView":
        """Select all columns EXCEPT the given ones."""
        from forecastlib.preprocessing.selectors import ColumnView
        remaining = [c for c in self._df.columns if c not in columns]
        return ColumnView(self, remaining, label=f"exclude({columns})")

    def regex(self, pattern: str) -> "ColumnView":
        """Select columns whose names match a regular expression."""
        import re
        from forecastlib.preprocessing.selectors import ColumnView
        cols = [c for c in self._df.columns if re.search(pattern, c)]
        if not cols:
            raise SelectionError(
                f"No columns matched pattern '{pattern}'.",
                suggestions=[f"Available columns: {list(self._df.columns)}"],
            )
        return ColumnView(self, cols, label=f"regex({pattern!r})")

    # ------------------------------------------------------------------
    # Global accessor properties
    # ------------------------------------------------------------------

    @property
    def clean(self) -> "CleanAccessor":
        """Access cleaning operations on all columns."""
        from forecastlib.preprocessing.cleaners import CleanAccessor
        return CleanAccessor(self, list(self._df.columns))

    @property
    def fill(self) -> "FillAccessor":
        """Access missing-value filling on all columns."""
        from forecastlib.preprocessing.missing import FillAccessor
        return FillAccessor(self, list(self._df.columns))

    @property
    def inspect(self) -> "InspectAccessor":
        """Access inspection and profiling utilities."""
        from forecastlib.inspection.profiling import InspectAccessor
        return InspectAccessor(self)

    # ------------------------------------------------------------------
    # Pipeline export
    # ------------------------------------------------------------------

    def to_pipeline(self) -> "Pipeline":
        """
        Export the transformation log as a reusable Pipeline.

        The returned Pipeline can be fitted on training data and then used
        to transform new data (e.g. test set) reproducibly.

        >>> pipeline = ds.to_pipeline()
        >>> pipeline.fit(train_df)
        >>> test_transformed = pipeline.transform(test_df)
        """
        from forecastlib.pipeline.pipeline import Pipeline
        return Pipeline.from_registry(self._registry, self._schema)

    # ------------------------------------------------------------------
    # Internal mutation helpers
    # ------------------------------------------------------------------

    def _mutate(self, new_df: pd.DataFrame, step: TransformStep) -> "Dataset":
        """
        Apply a DataFrame transformation in-place and record the step.

        This is the single mutation point for all transformations.
        """
        self._df = new_df
        self._registry.add(step)
        # Refresh schema stats
        self._schema.n_rows = len(new_df)
        self._schema.n_cols = len(new_df.columns)
        # Re-infer column metadata for new/changed columns
        for col in new_df.columns:
            if col not in self._schema.columns:
                from forecastlib.data.schema import _infer_column
                self._schema.columns[col] = _infer_column(col, new_df[col])
        # Remove metadata for dropped columns
        dropped = [c for c in list(self._schema.columns.keys()) if c not in new_df.columns]
        for c in dropped:
            del self._schema.columns[c]
        return self

    # ------------------------------------------------------------------
    # DataFrame API passthrough
    # ------------------------------------------------------------------

    def to_dataframe(self) -> pd.DataFrame:
        """Return the underlying DataFrame."""
        return self._df.copy()

    def copy(self) -> "Dataset":
        """Return a deep copy of this Dataset."""
        ds = Dataset.__new__(Dataset)
        ds._df       = self._df.copy()
        ds._schema   = self._schema.copy()
        ds._source   = self._source
        ds._registry = self._registry.copy()
        return ds

    @property
    def shape(self) -> tuple:
        return self._df.shape

    @property
    def columns(self) -> pd.Index:
        return self._df.columns

    @property
    def dtypes(self) -> pd.Series:
        return self._df.dtypes

    @property
    def schema(self) -> SchemaInfo:
        return self._schema

    def head(self, n: int = 5) -> pd.DataFrame:
        return self._df.head(n)

    def sample(self, n: int = 5, **kwargs) -> pd.DataFrame:
        return self._df.sample(n=min(n, len(self._df)), **kwargs)

    def __len__(self) -> int:
        return len(self._df)

    def __repr__(self) -> str:
        rows, cols = self._df.shape
        target  = self._schema.target_col   or "—"
        dt_col  = self._schema.datetime_col or "—"
        grp_col = self._schema.group_col    or "—"
        steps   = len(self._registry)
        return (
            f"Dataset({rows:,} rows × {cols} cols | "
            f"target={target!r}  datetime={dt_col!r}  group={grp_col!r} | "
            f"{steps} transform step{'s' if steps != 1 else ''})"
        )

    def __getitem__(self, key):
        return self._df[key]
