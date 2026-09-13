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


def sniff_separator(sample: str) -> str:
    """Guess a CSV's column separator from its header line.

    Excel in Spanish locales exports with ';' (and tabs show up in
    copy-pasted exports), so assuming ',' silently collapsed those files into
    a single unusable column named "sku;fecha;cantidad". Picks whichever
    candidate splits the header into the most fields; ties fall back to ','.
    """
    # `splitlines()[0]` is not safe on its own: a file that is nothing but the
    # BOM Excel writes into an EMPTY export survives `sample.strip()` (U+FEFF is
    # not whitespace to Python), and then `lstrip` leaves the empty string,
    # whose `splitlines()` is `[]`. That was an IndexError — so uploading an
    # empty export answered "an unexpected error occurred", blaming the server
    # for a file the user could see was empty.
    lines = (sample or "").lstrip("﻿").splitlines()
    header = lines[0] if lines else ""
    best, best_count = ",", 1
    for candidate in (",", ";", "\t", "|"):
        count = len(header.split(candidate))
        if count > best_count:
            best, best_count = candidate, count
    return best


def _csv_sep(source: _Source) -> str:
    """Separator for a CSV path or in-memory bytes."""
    try:
        if isinstance(source, bytes):
            sample = source[:8192].decode("utf-8", errors="replace")
        else:
            with open(source, "r", encoding="utf-8-sig", errors="replace") as fh:
                sample = fh.read(8192)
    except Exception:
        return ","
    return sniff_separator(sample)


CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


def read_csv_any_encoding(source, **kwargs) -> pd.DataFrame:
    """``pd.read_csv`` that survives a file its exporter did not write in UTF-8.

    An ERP export from a Windows machine in a Spanish locale is cp1252, not
    UTF-8, and ``utf-8-sig`` raises ``UnicodeDecodeError`` on the first accented
    byte — so a whole year of history was refused over one "Camión", with a
    failure that reads as a broken file rather than a wrong encoding. The stock
    importer already fell back (``backend/api/v1/inventory.py::_decode_csv``);
    the sales path, where most files actually enter, did not.

    UTF-8 is tried first so a correctly encoded file is never reinterpreted.
    latin-1 goes last because it maps all 256 byte values and therefore always
    succeeds — the chain never ends without a DataFrame for an encoding reason.
    Only ``UnicodeDecodeError`` is retried: a malformed or empty CSV must still
    surface as itself, not as an encoding problem.
    """
    last: Optional[UnicodeDecodeError] = None
    for encoding in CSV_ENCODINGS:
        try:
            if hasattr(source, "seek"):
                source.seek(0)
            return pd.read_csv(source, encoding=encoding, **kwargs)
        except UnicodeDecodeError as exc:
            last = exc
    raise last                                  # pragma: no cover - latin-1 cannot fail


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


# Formats whose bytes legitimately contain NUL. An .xlsx is a ZIP and a
# .parquet is a binary column store — both are full of them, and scanning
# either for NUL refuses every valid file. Only the text formats can be
# checked, which is also the only place the truncation bug exists: the CSV/JSON
# reader is the one that stops at a NUL mid-field.
_BINARY_FORMATS = {"excel", "parquet"}


def _refuse_nul_bytes(source: _Source, fmt: Optional[str] = None) -> None:
    """Refuse a file whose cells contain NUL, before pandas silently eats them.

    pandas' C parser treats NUL as end-of-field: `SKU-\x00-1` is read as
    `SKU-`, with no error and no warning. The file says one thing and the
    import stores another — a buyer then orders against a product code their
    supplier has never heard of.

    Refusing is the only honest option at this layer. A DataFrame has no
    channel to report "and by the way, 12 rows were dropped", so silently
    filtering them would trade one invisible corruption for another. The stock
    CSV importer, which DOES have a per-row error report, rejects the
    individual rows instead — see `_parse_stock_rows`.

    Only the first 1 MB is scanned when the source is a path: a NUL is a
    corrupt-export symptom, and a corrupt export is corrupt from the start.
    Reading the whole file here would double the cost of every import.

    Binary formats are skipped entirely — see _BINARY_FORMATS.
    """
    fmt = fmt or (None if isinstance(source, bytes) else _fmt_from_path(source))
    if fmt in _BINARY_FORMATS:
        return
    try:
        if isinstance(source, bytes):
            sample = source
        else:
            with open(source, "rb") as fh:
                sample = fh.read(1_048_576)
    except OSError:
        return                      # unreadable is the reader's problem, not ours
    if b"\x00" in sample:
        line = sample[: sample.index(b"\x00")].count(b"\n") + 1
        raise ValueError(
            f"The file contains a NUL byte (first seen on line {line}). "
            f"Rows containing one cannot be imported, because the value would "
            f"be silently truncated. Re-export the file from your system."
        )


def _read_df(source: _Source, fmt: Optional[str], nrows: Optional[int]) -> pd.DataFrame:
    _refuse_nul_bytes(source, fmt)
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
    return read_csv_any_encoding(buf, nrows=nrows, sep=_csv_sep(source))


def read_rows(source: _Source, fmt: Optional[str] = None,
              nrows: Optional[int] = None) -> list[dict]:
    """Read a tabular file/bytes into plain row dicts. NaN -> None."""
    return _to_records(_read_df(source, fmt, nrows))


def read_dataframe(source: _Source, fmt: Optional[str] = None,
                   nrows: Optional[int] = None, sheet: Optional[str] = None):
    """ForecastingCore bridge: return a raw pandas DataFrame for components that
    require one (TimeSeriesAnalyzer, DriftDetector). One of the few boundary
    functions that returns a DataFrame — callers pass it straight to
    ForecastingCore and never call pandas themselves. For Excel, ``sheet``
    defaults to the first sheet."""
    _refuse_nul_bytes(source, fmt)
    if isinstance(source, bytes):
        if fmt is None:
            raise ValueError("fmt is required when reading from bytes")
        buf: object = _io.BytesIO(source)
    else:
        fmt = fmt or _fmt_from_path(source)
        buf = source
    if fmt == "excel":
        if sheet is None and not isinstance(source, bytes):
            sheet = pd.ExcelFile(source).sheet_names[0]
        if sheet is not None:
            return pd.read_excel(buf, sheet_name=sheet, nrows=nrows)
        return pd.read_excel(buf, nrows=nrows)
    if fmt == "json":
        df = pd.read_json(buf)
        return df.head(nrows) if nrows is not None else df
    if fmt == "parquet":
        df = pd.read_parquet(buf)
        return df.head(nrows) if nrows is not None else df
    return read_csv_any_encoding(buf, nrows=nrows, sep=_csv_sep(source))


def dataframe_from_records(rows, columns: list[str]):
    """ForecastingCore bridge: build a DataFrame from row records + column names
    (used for SQL result sets fed to the analysis path)."""
    return pd.DataFrame(rows, columns=columns)


def read_columns(path: str, cols: list[str]) -> list[dict]:
    """Read only `cols` from a CSV/Excel file into plain row dicts."""
    fmt = _fmt_from_path(path)
    if fmt == "excel":
        df = pd.read_excel(path, usecols=cols)
    else:
        df = read_csv_any_encoding(path, usecols=cols, sep=_csv_sep(path))
    return _to_records(df)


def dataset_preview(path: str, rows: int, sheet: Optional[str] = None) -> dict:
    """Preview shape for the datasources UI: first `rows` rows + column names,
    plus Excel sheet names and the full row count (for the caller's DB update).
    Returns plain Python; the DB write stays in the caller."""
    fmt = _fmt_from_path(path)
    _refuse_nul_bytes(path, fmt)
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
        df = read_csv_any_encoding(path, nrows=rows, sep=_csv_sep(path))
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


def read_table(path: str, sheet: Optional[str] = None) -> dict:
    """Full-table load for the in-app editor: all columns + all rows as plain
    Python. Columns are preserved even when there are zero data rows."""
    fmt = _fmt_from_path(path)
    if fmt == "excel":
        target = sheet or (pd.ExcelFile(path).sheet_names[0])
        df = pd.read_excel(path, sheet_name=target)
    elif fmt == "json":
        df = pd.read_json(path)
    elif fmt == "parquet":
        df = pd.read_parquet(path)
    else:
        df = read_csv_any_encoding(path, sep=_csv_sep(path))
    return {"columns": list(df.columns), "rows": _to_records(df)}


def write_rows(path: str, columns: list[str], rows: list[dict],
               fmt: str = "csv") -> None:
    """Write an edited table to disk — the ONE place edits get persisted.
    Column order follows `columns`; a header is written even with zero rows.
    Cells are written verbatim (callers sanitize before passing them in)."""
    df = pd.DataFrame([{c: r.get(c) for c in columns} for r in rows], columns=columns)
    if fmt == "excel":
        df.to_excel(path, index=False)
    elif fmt == "json":
        df.to_json(path, orient="records")
    elif fmt == "parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)
