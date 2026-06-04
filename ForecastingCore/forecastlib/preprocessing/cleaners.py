"""
CleanAccessor — data cleaning operations.

Accessed via:
    dataset.clean.fix_datetime()
    dataset.clean.drop_duplicates()
    dataset.cols(["price"]).clean.clip(lower=0)
    dataset.clean.drop_nulls()
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

import pandas as pd

from forecastlib.data.validators import DatasetValidator
from forecastlib.preprocessing.transformers import TransformStep

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


class CleanAccessor:
    """
    Data cleaning operations.

    Methods
    -------
    fix_datetime()             Parse string/object columns to datetime
    drop_duplicates(subset)    Remove duplicate rows
    drop_nulls(subset, thresh) Remove rows with nulls
    drop_constant()            Remove constant columns
    clip(lower, upper)         Clip numeric values
    strip()                    Strip whitespace from string columns
    fix_dtypes()               Auto-cast columns to best dtypes
    rename(mapping)            Rename columns
    sort(by, ascending)        Sort rows
    reset_index()              Reset DataFrame index
    """

    def __init__(self, dataset: "Dataset", columns: List[str]):
        self._dataset = dataset
        self._columns = columns

    # ------------------------------------------------------------------
    # DateTime parsing
    # ------------------------------------------------------------------

    def fix_datetime(self, format: Optional[str] = None) -> "Dataset":
        """
        Parse string/object columns to datetime dtype.

        Operates on the configured datetime column (if set) or all
        columns in the current selection that look like dates.

        Parameters
        ----------
        format : str, optional
            Explicit strftime format, e.g. "%Y-%m-%d".

        >>> ds.clean.fix_datetime()
        >>> ds.cols(["date_str"]).clean.fix_datetime(format="%d/%m/%Y")
        """
        df = self._dataset._df.copy()

        # Prioritise the configured datetime column
        schema   = self._dataset._schema
        targets  = self._columns
        if schema.datetime_col and schema.datetime_col in self._columns:
            targets = [schema.datetime_col] + [c for c in self._columns if c != schema.datetime_col]

        converted = []
        for col in targets:
            if col not in df.columns:
                continue
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                continue  # already datetime
            try:
                parsed = pd.to_datetime(df[col], format=format, errors="coerce",
                                        infer_datetime_format=(format is None))
                if parsed.notna().mean() >= 0.8:
                    df[col] = parsed
                    converted.append(col)
            except Exception:
                pass

        step = TransformStep(
            name=f"fix_datetime on {converted or self._columns}",
            kind="clean.fix_datetime",
            columns=converted,
            params={"format": format},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Duplicate rows
    # ------------------------------------------------------------------

    def drop_duplicates(
        self,
        subset: Optional[List[str]] = None,
        keep: str = "first",
    ) -> "Dataset":
        """
        Remove duplicate rows.

        Parameters
        ----------
        subset : list of str, optional
            Columns to consider.  Defaults to all columns.
        keep : {"first", "last", False}

        >>> ds.clean.drop_duplicates()
        >>> ds.clean.drop_duplicates(subset=["date", "store"])
        """
        df    = self._dataset._df
        before = len(df)
        df    = df.drop_duplicates(subset=subset, keep=keep).reset_index(drop=True)
        removed = before - len(df)

        step = TransformStep(
            name=f"drop_duplicates (removed {removed:,} rows)",
            kind="clean.drop_duplicates",
            columns=subset or list(df.columns),
            params={"subset": subset, "keep": keep, "removed": removed},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Null rows
    # ------------------------------------------------------------------

    def drop_nulls(
        self,
        subset: Optional[List[str]] = None,
        thresh: Optional[int] = None,
    ) -> "Dataset":
        """
        Remove rows with null values.

        Parameters
        ----------
        subset : list of str, optional
            Only consider these columns.  Defaults to all.
        thresh : int, optional
            Minimum number of non-null values required to keep the row.

        >>> ds.clean.drop_nulls()
        >>> ds.clean.drop_nulls(subset=["sales", "date"])
        """
        df    = self._dataset._df
        before = len(df)
        df    = df.dropna(subset=subset, thresh=thresh).reset_index(drop=True)
        removed = before - len(df)

        step = TransformStep(
            name=f"drop_nulls (removed {removed:,} rows)",
            kind="clean.drop_nulls",
            columns=subset or list(df.columns),
            params={"subset": subset, "thresh": thresh},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Constant columns
    # ------------------------------------------------------------------

    def drop_constant(self) -> "Dataset":
        """
        Drop columns that have only one unique non-null value.

        >>> ds.clean.drop_constant()
        """
        df   = self._dataset._df
        drop = [c for c in self._columns if c in df.columns and df[c].nunique() <= 1]

        if drop:
            df = df.drop(columns=drop).copy()

        step = TransformStep(
            name=f"drop_constant (dropped {drop})",
            kind="clean.drop_constant",
            columns=drop,
            params={"dropped": drop},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Clip numeric values
    # ------------------------------------------------------------------

    def clip(
        self,
        lower: Optional[float] = None,
        upper: Optional[float] = None,
    ) -> "Dataset":
        """
        Clip numeric column values to [lower, upper].

        Parameters
        ----------
        lower : float, optional
        upper : float, optional

        >>> ds.cols(["price"]).clean.clip(lower=0)
        >>> ds.cols(["age"]).clean.clip(lower=0, upper=120)
        """
        df = self._dataset._df.copy()
        valid = [c for c in self._columns if c in df.columns
                 and pd.api.types.is_numeric_dtype(df[c])]

        for col in valid:
            df[col] = df[col].clip(lower=lower, upper=upper)

        step = TransformStep(
            name=f"clip(lower={lower}, upper={upper}) on {valid}",
            kind="clean.clip",
            columns=valid,
            params={"lower": lower, "upper": upper},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # String whitespace
    # ------------------------------------------------------------------

    def strip(self) -> "Dataset":
        """
        Strip leading/trailing whitespace from string columns.

        >>> ds.categorical().clean.strip()
        """
        df = self._dataset._df.copy()
        for col in self._columns:
            if col in df.columns and df[col].dtype in ("object", "string"):
                df[col] = df[col].str.strip()

        step = TransformStep(
            name=f"strip on {self._columns}",
            kind="clean.strip",
            columns=self._columns,
            params={},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Auto dtype fixing
    # ------------------------------------------------------------------

    def fix_dtypes(self) -> "Dataset":
        """
        Auto-cast columns to the most efficient dtype.

        - Numeric strings → float/int
        - Low-cardinality objects → category
        - Boolean-looking columns → bool

        >>> ds.clean.fix_dtypes()
        """
        df = self._dataset._df.copy()
        for col in self._columns:
            if col not in df.columns:
                continue
            s = df[col]
            if s.dtype == object:
                # Try numeric
                as_num = pd.to_numeric(s, errors="coerce")
                if as_num.notna().mean() >= 0.95:
                    df[col] = as_num
                    continue
                # Try category
                if s.nunique() / max(len(s), 1) < 0.5:
                    df[col] = s.astype("category")

        step = TransformStep(
            name="fix_dtypes", kind="clean.fix_dtypes",
            columns=list(self._columns), params={},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Rename columns
    # ------------------------------------------------------------------

    def rename(self, mapping: Dict[str, str]) -> "Dataset":
        """
        Rename columns.

        >>> ds.clean.rename({"old_name": "new_name"})
        """
        df = self._dataset._df.copy().rename(columns=mapping)

        # Update schema if renamed columns have roles
        schema = self._dataset._schema
        if schema.target_col in mapping:
            schema.target_col = mapping[schema.target_col]
        if schema.datetime_col in mapping:
            schema.datetime_col = mapping[schema.datetime_col]
        if schema.group_col in mapping:
            schema.group_col = mapping[schema.group_col]

        step = TransformStep(
            name=f"rename {mapping}",
            kind="clean.rename",
            columns=list(mapping.keys()),
            params={"mapping": mapping},
        )
        return self._dataset._mutate(df, step)

    # ------------------------------------------------------------------
    # Sort
    # ------------------------------------------------------------------

    def sort(
        self,
        by: Optional[List[str]] = None,
        ascending: bool = True,
    ) -> "Dataset":
        """
        Sort the dataset.

        Defaults to sorting by the configured datetime column (if set).

        >>> ds.clean.sort()
        >>> ds.clean.sort(by=["store", "date"])
        """
        schema = self._dataset._schema
        sort_by = by or ([schema.datetime_col] if schema.datetime_col else [])
        if not sort_by:
            return self._dataset

        df = self._dataset._df.sort_values(sort_by, ascending=ascending).reset_index(drop=True)
        step = TransformStep(
            name=f"sort by {sort_by}",
            kind="clean.sort",
            columns=sort_by,
            params={"by": sort_by, "ascending": ascending},
        )
        return self._dataset._mutate(df, step)
