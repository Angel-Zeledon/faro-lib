"""
DatasetValidator — static validation utilities used across the library.

All methods raise descriptive errors with suggestions, never silent failures.
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

import pandas as pd

from forecastlib.exceptions import (
    SchemaError, TransformError, ValidationError, SelectionError, LeakageError,
)

if TYPE_CHECKING:
    from forecastlib.data.schema import SchemaInfo, ColumnType


class DatasetValidator:
    """Static helpers for common validation checks."""

    # ------------------------------------------------------------------
    # Column existence
    # ------------------------------------------------------------------

    @staticmethod
    def require_columns(df: pd.DataFrame, columns: List[str], context: str = "") -> None:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise SelectionError(
                f"{context}Column(s) not found in dataset: {missing}.\n"
                f"Available columns: {list(df.columns)}",
            )

    @staticmethod
    def require_nonempty_selection(columns: List[str], selection_expr: str = "") -> None:
        if not columns:
            raise SelectionError(
                f"Column selection{' (' + selection_expr + ')' if selection_expr else ''} "
                f"returned no columns. Nothing to transform.",
                suggestions=[
                    "Check that the DataFrame has columns matching your selection.",
                    "Use dataset.inspect.types() to see available columns and their types.",
                ],
            )

    # ------------------------------------------------------------------
    # Type checks
    # ------------------------------------------------------------------

    @staticmethod
    def require_numeric(df: pd.DataFrame, columns: List[str], operation: str) -> None:
        bad = [c for c in columns if not pd.api.types.is_numeric_dtype(df[c])]
        if bad:
            raise TransformError(
                f"Cannot apply '{operation}' to non-numeric column(s): {bad}.",
                suggestions=[
                    f"dataset.categorical().encode.one_hot()  # encode first",
                    f"dataset.cols({bad}).encode.label()       # or label-encode",
                ],
            )

    @staticmethod
    def require_categorical(df: pd.DataFrame, columns: List[str], operation: str) -> None:
        bad = [c for c in columns
               if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
        if bad:
            raise TransformError(
                f"Cannot apply '{operation}' to numeric column(s): {bad}.",
                suggestions=[
                    f"dataset.numeric().scale.standard()   # scale numerics instead",
                    f"dataset.cols({bad}).clean.fix_dtypes()  # or fix their types first",
                ],
            )

    @staticmethod
    def warn_target_scaling(columns: List[str], schema: "SchemaInfo") -> None:
        import warnings
        if schema.target_col and schema.target_col in columns:
            warnings.warn(
                f"You are scaling the target column '{schema.target_col}'. "
                f"Remember to inverse-transform predictions before computing business metrics. "
                f"If this is intentional, ignore this warning.",
                UserWarning,
                stacklevel=4,
            )

    # ------------------------------------------------------------------
    # Dataset health
    # ------------------------------------------------------------------

    @staticmethod
    def require_not_empty(df: pd.DataFrame) -> None:
        if df.empty:
            raise ValidationError("Dataset is empty (0 rows). Cannot proceed.")

    @staticmethod
    def require_target_set(schema: "SchemaInfo") -> None:
        if not schema.target_col:
            raise SchemaError(
                "No target column configured.",
                suggestions=[
                    "dataset.select(target='your_column')",
                    "dataset.inspect.summary()  # to see column suggestions",
                ],
            )

    @staticmethod
    def require_datetime_set(schema: "SchemaInfo") -> None:
        if not schema.datetime_col:
            raise SchemaError(
                "No datetime column configured.",
                suggestions=[
                    "dataset.select(datetime='your_date_column')",
                    "dataset.clean.fix_datetime()  # to parse string dates first",
                ],
            )

    @staticmethod
    def require_group_set(schema: "SchemaInfo") -> None:
        if not schema.group_col:
            raise SchemaError(
                "No group column configured.",
                suggestions=["dataset.select(group='your_group_column')"],
            )

    # ------------------------------------------------------------------
    # Leakage
    # ------------------------------------------------------------------

    @staticmethod
    def check_target_in_features(columns: List[str], schema: "SchemaInfo") -> None:
        if schema.target_col and schema.target_col in columns:
            raise LeakageError(
                f"The target column '{schema.target_col}' is included in the selected columns.\n"
                f"Using the raw target as a feature causes data leakage.",
                suggestions=[
                    f"dataset.target().lags([1, 7, 14])  # use lagged target instead",
                    f"dataset.exclude(['{schema.target_col}']).scale.standard()",
                ],
            )

    # ------------------------------------------------------------------
    # Positive values (for log transforms, multiplicative models)
    # ------------------------------------------------------------------

    @staticmethod
    def require_positive(df: pd.DataFrame, columns: List[str], operation: str) -> None:
        for col in columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                min_val = df[col].min()
                if min_val <= 0:
                    raise TransformError(
                        f"Column '{col}' has values <= 0 (min={min_val:.4g}). "
                        f"'{operation}' requires strictly positive values.",
                        suggestions=[
                            f"dataset.cols(['{col}']).clean.clip(lower=1e-8)  # clip first",
                            f"dataset.cols(['{col}']).scale.standard()        # use standard scaling instead",
                        ],
                    )
