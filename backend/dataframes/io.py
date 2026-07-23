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


def dataset_preview(path: str, rows: int, sheet: Optional[str] = None) -> dict:
    """Preview shape for the datasources UI: first `rows` rows + column names,
    plus Excel sheet names and the full row count (for the caller's DB update).
    Returns plain Python; the DB write stays in the caller."""
    fmt = _fmt_from_path(path)
    sheets: Optional[list] = None
    total_rows: Optional[int] = None

    if fmt == "excel":
        xf = pd.ExcelFile(path)
        sheets = list(xf.sheet_names)
        target = sheet or (sheets[0] if sheets else None)
        full = pd.read_excel(path, sheet_name=target)
        total_rows = len(full)
        df = full.head(rows)
    elif fmt == "json":
        full = pd.read_json(path)
        total_rows = len(full)
        df = full.head(rows)
    elif fmt == "parquet":
        try:
            import pyarrow.parquet as pq
            total_rows = pq.read_metadata(path).num_rows
        except Exception:
            total_rows = None
        df = pd.read_parquet(path).head(rows)
    else:
        df = pd.read_csv(path, nrows=rows)
        # Full row count without loading the whole file into memory.
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as _f:
                total_rows = sum(1 for _ in _f) - 1
        except Exception:
            total_rows = None

    return {
        "columns": list(df.columns),
        "rows": _to_records(df),
        "sheets": sheets,
        "active_sheet": sheet or (sheets[0] if sheets else None),
        "total_rows": total_rows,
    }
