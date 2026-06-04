"""
Schema module — metadata layer for Dataset objects.

SchemaInfo stores semantic roles (target, datetime, group) and per-column
metadata (type, cardinality, null rate).  It is inferred automatically on
load and updated whenever the user calls .select(...) or a transformation
changes the column set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ColumnRole(str, Enum):
    TARGET   = "target"
    DATETIME = "datetime"
    GROUP    = "group"
    FEATURE  = "feature"
    ID       = "id"
    UNKNOWN  = "unknown"


class ColumnType(str, Enum):
    NUMERIC     = "numeric"
    CATEGORICAL = "categorical"
    DATETIME    = "datetime"
    BOOLEAN     = "boolean"
    TEXT        = "text"
    UNKNOWN     = "unknown"


# ---------------------------------------------------------------------------
# ColumnMeta
# ---------------------------------------------------------------------------

@dataclass
class ColumnMeta:
    """Metadata for a single column."""

    name: str
    dtype: str
    col_type: ColumnType = ColumnType.UNKNOWN
    role: ColumnRole = ColumnRole.UNKNOWN
    n_unique: int = 0
    null_pct: float = 0.0
    is_constant: bool = False
    is_id: bool = False
    sample_values: List = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "dtype":       self.dtype,
            "type":        self.col_type.value,
            "role":        self.role.value,
            "n_unique":    self.n_unique,
            "null_pct":    round(self.null_pct, 3),
            "is_constant": self.is_constant,
            "is_id":       self.is_id,
        }


# ---------------------------------------------------------------------------
# SchemaInfo
# ---------------------------------------------------------------------------

@dataclass
class SchemaInfo:
    """Semantic schema for a Dataset."""

    target_col:   Optional[str] = None
    datetime_col: Optional[str] = None
    group_col:    Optional[str] = None
    feature_cols: List[str]     = field(default_factory=list)
    columns:      Dict[str, ColumnMeta] = field(default_factory=dict)
    n_rows: int = 0
    n_cols: int = 0
    freq:   Optional[str] = None   # inferred time-series frequency

    # ------------------------------------------------------------------
    # Setters
    # ------------------------------------------------------------------

    def set_target(self, col: str) -> None:
        self.target_col = col
        if col in self.columns:
            self.columns[col].role = ColumnRole.TARGET

    def set_datetime(self, col: str) -> None:
        self.datetime_col = col
        if col in self.columns:
            self.columns[col].role = ColumnRole.DATETIME

    def set_group(self, col: str) -> None:
        self.group_col = col
        if col in self.columns:
            self.columns[col].role = ColumnRole.GROUP

    # ------------------------------------------------------------------
    # Role helpers
    # ------------------------------------------------------------------

    @property
    def role_cols(self) -> Dict[ColumnRole, Optional[str]]:
        return {
            ColumnRole.TARGET:   self.target_col,
            ColumnRole.DATETIME: self.datetime_col,
            ColumnRole.GROUP:    self.group_col,
        }

    def get_numeric_cols(self) -> List[str]:
        return [n for n, m in self.columns.items() if m.col_type == ColumnType.NUMERIC]

    def get_categorical_cols(self) -> List[str]:
        return [n for n, m in self.columns.items() if m.col_type == ColumnType.CATEGORICAL]

    def get_datetime_cols(self) -> List[str]:
        return [n for n, m in self.columns.items() if m.col_type == ColumnType.DATETIME]

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    @classmethod
    def infer(cls, df: pd.DataFrame) -> "SchemaInfo":
        """Infer schema from a DataFrame automatically."""
        schema = cls(n_rows=len(df), n_cols=len(df.columns))

        for col in df.columns:
            s = df[col]
            meta = _infer_column(col, s)
            schema.columns[col] = meta

        # Auto-detect roles
        schema.target_col   = _guess_target(df, schema)
        schema.datetime_col = _guess_datetime(df, schema)
        schema.group_col    = _guess_group(df, schema)

        if schema.datetime_col:
            schema.freq = _infer_frequency(df, schema.datetime_col, schema.group_col)

        return schema

    def copy(self) -> "SchemaInfo":
        import copy
        return copy.deepcopy(self)


# ---------------------------------------------------------------------------
# Column inference helpers
# ---------------------------------------------------------------------------

_ID_PATTERNS = re.compile(r"^(id|_id|uuid|key|pk|index|idx)$", re.I)
_DT_PATTERNS = re.compile(r"(date|time|dt|timestamp|period|week|month|year)", re.I)
_TGT_PATTERNS = re.compile(r"(sale|demand|qty|quantity|unit|revenue|target|y$|value)", re.I)
_GRP_PATTERNS = re.compile(r"(sku|product|store|item|category|group|brand|region)", re.I)


def _infer_column(name: str, s: pd.Series) -> ColumnMeta:
    n_unique = int(s.nunique())
    null_pct = float(s.isna().mean())
    sample   = s.dropna().head(3).tolist()
    is_const = n_unique <= 1

    # Datetime detection
    if pd.api.types.is_datetime64_any_dtype(s):
        return ColumnMeta(
            name=name, dtype=str(s.dtype),
            col_type=ColumnType.DATETIME,
            n_unique=n_unique, null_pct=null_pct,
            is_constant=is_const, sample_values=sample,
        )

    # Try to parse object columns as datetime
    if s.dtype == object:
        try:
            parsed = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
            if parsed.notna().mean() >= 0.8:
                return ColumnMeta(
                    name=name, dtype=str(s.dtype),
                    col_type=ColumnType.DATETIME,
                    n_unique=n_unique, null_pct=null_pct,
                    is_constant=is_const, sample_values=sample,
                )
        except Exception:
            pass

    # Boolean
    if s.dtype == bool or (s.dtype == object and set(s.dropna().unique()).issubset({True, False, 0, 1, "0", "1"})):
        return ColumnMeta(
            name=name, dtype=str(s.dtype),
            col_type=ColumnType.BOOLEAN,
            n_unique=n_unique, null_pct=null_pct,
            is_constant=is_const, sample_values=sample,
        )

    # Numeric
    if pd.api.types.is_numeric_dtype(s):
        n = len(s.dropna())
        is_id = (n_unique == n and n > 10 and bool(_ID_PATTERNS.match(name)))
        return ColumnMeta(
            name=name, dtype=str(s.dtype),
            col_type=ColumnType.NUMERIC,
            n_unique=n_unique, null_pct=null_pct,
            is_constant=is_const, is_id=is_id,
            sample_values=sample,
        )

    # Categorical / text
    ratio = n_unique / max(len(s.dropna()), 1)
    col_type = ColumnType.TEXT if ratio > 0.8 else ColumnType.CATEGORICAL
    return ColumnMeta(
        name=name, dtype=str(s.dtype),
        col_type=col_type,
        n_unique=n_unique, null_pct=null_pct,
        is_constant=is_const, sample_values=sample,
    )


def _guess_target(df: pd.DataFrame, schema: SchemaInfo) -> Optional[str]:
    numerics = [c for c, m in schema.columns.items()
                if m.col_type == ColumnType.NUMERIC and not m.is_id and not m.is_constant]
    if not numerics:
        return None
    # Prefer columns whose name matches target patterns
    named = [c for c in numerics if _TGT_PATTERNS.search(c)]
    if named:
        return named[0]
    # Fall back to highest-variance numeric
    try:
        return max(numerics, key=lambda c: float(df[c].std()))
    except Exception:
        return numerics[0]


def _guess_datetime(df: pd.DataFrame, schema: SchemaInfo) -> Optional[str]:
    dt_cols = [c for c, m in schema.columns.items() if m.col_type == ColumnType.DATETIME]
    if not dt_cols:
        return None
    named = [c for c in dt_cols if _DT_PATTERNS.search(c)]
    return named[0] if named else dt_cols[0]


def _guess_group(df: pd.DataFrame, schema: SchemaInfo) -> Optional[str]:
    cats = [c for c, m in schema.columns.items()
            if m.col_type == ColumnType.CATEGORICAL
            and 2 <= m.n_unique <= len(df) * 0.3]
    if not cats:
        return None
    named = [c for c in cats if _GRP_PATTERNS.search(c)]
    return named[0] if named else cats[0]


def _infer_frequency(df: pd.DataFrame, dt_col: str, group_col: Optional[str]) -> Optional[str]:
    try:
        src = df
        if group_col and group_col in df.columns:
            first_group = df[group_col].iloc[0]
            src = df[df[group_col] == first_group]
        dates = pd.to_datetime(src[dt_col], errors="coerce").dropna().sort_values().drop_duplicates()
        if len(dates) < 3:
            return None
        md = dates.diff().dropna().median()
        if md.days == 1:
            return "D"
        if 5 <= md.days <= 8:
            return "W"
        if 25 <= md.days <= 35:
            return "MS"
        if 85 <= md.days <= 95:
            return "QS"
        if 360 <= md.days <= 370:
            return "YS"
    except Exception:
        pass
    return None
