"""Tabular file reads returning plain Python rows."""
from __future__ import annotations

import io as _io
import math
from typing import Optional, Union

import pandas as pd

_Source = Union[str, bytes]


def _fmt_from_path(path: str) -> str:
    p = path.lower()
    if p.endswith((".xlsx", ".xls")):
        return "excel"
    if p.endswith(".json"):
        return "json"
    if p.endswith(".parquet"):
        return "parquet"
    return "csv"


def _to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list[dict] with NaN -> None and numpy scalars -> Python."""
    df = df.where(pd.notna(df), None)
    records = df.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if hasattr(v, "item"):          # numpy scalar
                row[k] = v.item()
            elif isinstance(v, float) and math.isnan(v):
                row[k] = None
    return records


def _read_df(source: _Source, fmt: Optional[str], nrows: Optional[int]) -> pd.DataFrame:
    if isinstance(source, bytes):
        if fmt is None:
            raise ValueError("fmt is required when reading from bytes")
        buf: object = _io.BytesIO(source)
    else:
        fmt = fmt or _fmt_from_path(source)
        buf = source
    if fmt == "excel":
        return pd.read_excel(buf, nrows=nrows)
    if fmt == "json":
        df = pd.read_json(buf)
        return df.head(nrows) if nrows is not None else df
    if fmt == "parquet":
        df = pd.read_parquet(buf)
        return df.head(nrows) if nrows is not None else df
    return pd.read_csv(buf, nrows=nrows)


def read_rows(source: _Source, fmt: Optional[str] = None,
              nrows: Optional[int] = None) -> list[dict]:
    """Read a tabular file/bytes into plain row dicts. NaN -> None."""
    return _to_records(_read_df(source, fmt, nrows))


def read_columns(path: str, cols: list[str]) -> list[dict]:
    """Read only `cols` from a CSV/Excel file into plain row dicts."""
    fmt = _fmt_from_path(path)
    if fmt == "excel":
        df = pd.read_excel(path, usecols=cols)
    else:
        df = pd.read_csv(path, usecols=cols)
    return _to_records(df)
