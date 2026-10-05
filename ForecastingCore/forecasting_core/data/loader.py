"""
DataLoader — loads datasets from multiple sources.

Supported formats: CSV, Excel (.xlsx/.xls), Parquet, JSON, SQL query file.

Example:
    loader = DataLoader()
    df = loader.load("sales.csv")
    df = loader.load("sales.parquet")
    df = loader.load("query.sql", sql_engine="postgresql://user:pw@host/db")
"""

import os
import re
import pandas as pd


def _sniff_separator(path: str) -> str:
    """Guess a CSV's separator from its header line.

    Spanish-locale Excel exports use ';', so assuming ',' collapsed the whole
    file into one unusable column. Picks whichever candidate yields the most
    header fields; anything ambiguous stays ','.
    """
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            header = fh.readline()
    except OSError:
        return ","
    best, best_count = ",", 1
    for candidate in (",", ";", "\t", "|"):
        count = len(header.split(candidate))
        if count > best_count:
            best, best_count = candidate, count
    return best


CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


def _read_csv(path: str, sep: str, **kwargs) -> pd.DataFrame:
    """Read a CSV whose exporter may not have written UTF-8.

    An ERP export from a Windows machine in a Spanish locale is cp1252, and
    ``utf-8-sig`` raises ``UnicodeDecodeError`` on the first accented byte — so
    training refused a whole file over one "Camión". UTF-8 is tried first so a
    correct file is never reinterpreted; latin-1 goes last because it maps all
    256 byte values and therefore always succeeds. Only ``UnicodeDecodeError``
    is retried, so a genuinely malformed CSV still surfaces as itself.

    Mirrors ``backend/dataframes/io.py::read_csv_any_encoding``; the duplication
    is deliberate — this package must not import the backend.
    """
    last: "UnicodeDecodeError | None" = None
    for encoding in CSV_ENCODINGS:
        try:
            return pd.read_csv(path, sep=sep, encoding=encoding, **kwargs)
        except UnicodeDecodeError as exc:
            last = exc
    raise last                                  # pragma: no cover - latin-1 cannot fail


def _fix_day_first_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Parse `dd/mm/yyyy` columns as day-first when no other reading is possible.

    Every consumer downstream — profiler, gate, quality check, trainer — calls
    `pd.to_datetime` on its own, and pandas defaults to month-first. On a Latin
    American export (`13/03/2025`) that dropped every row whose day is past the
    12th as "unparseable" and quietly moved the rest to another month; the run
    then died on the first row month-first cannot explain. Settling it once,
    here, is the only way all of them agree.

    Deliberately narrow. A column qualifies only when EVERY non-null value has
    the `d/m/y` shape AND at least one first component is > 12 — so it cannot be
    anything but a date, and cannot be month-first. Ambiguous columns (all
    components <= 12) are left as text on purpose: that is a real question for
    the user, and `gate.detect_ambiguous_date_format` asks it.
    """
    from forecasting_core.data.gate import (
        DATE_ORDER_DAY_FIRST, detect_determined_date_order,
    )

    for col in df.columns:
        # Text, whichever way this pandas spells it: 3.x reads CSV strings as
        # the `str` dtype, so an is_object_dtype check silently matched nothing.
        if not (pd.api.types.is_object_dtype(df[col])
                or pd.api.types.is_string_dtype(df[col])):
            continue
        try:
            if detect_determined_date_order(df, col) != DATE_ORDER_DAY_FIRST:
                continue
            parsed = pd.to_datetime(df[col], dayfirst=True, errors="coerce")
        except Exception:
            continue
        # Never trade a readable column for a mostly-empty one.
        if int(parsed.notna().sum()) == int(df[col].notna().sum()):
            df[col] = parsed
    return df


_CODE_COLUMN = re.compile(
    r"(?i)sku|codigo|c[oó]digo|code|(?:^|_)cod(?:_|$)|referencia|(?:^|_)ref(?:_|$)|item|articulo|"
    r"art[ií]culo|producto|product|ean|upc|barcode|material|(?:^|_)id(?:_|$)")
_LEADING_ZERO = re.compile(r"^\s*0\d+\s*$")


def _keep_leading_zeros(df: pd.DataFrame, reread) -> pd.DataFrame:
    """Give product codes back the zeros the number parser took away.

    ``00123`` read as a number is ``123``: a different product code, with no
    error and no warning. Only columns that are NAMED like a code and came out
    numeric are re-read as text, and only those that really hold a value with a
    leading zero are replaced, so an ordinary integer column is never turned
    into text. ``reread(columns)`` returns those columns as strings.
    """
    cols = [c for c in df.columns
            if pd.api.types.is_numeric_dtype(df[c]) and _CODE_COLUMN.search(str(c))]
    if not cols:
        return df
    try:
        raw = reread(cols)
    except Exception:
        return df          # the plain reading stands; never fail a load over this
    for c in cols:
        if c in raw.columns and raw[c].dropna().astype(str).str.match(_LEADING_ZERO).any():
            df[c] = raw[c].astype(object).where(raw[c].notna(), None)
    return df


class LoadError(Exception):
    pass


class DataLoader:
    """Loads a DataFrame from file path or SQL."""

    SUPPORTED = {".csv", ".xlsx", ".xls", ".parquet", ".json", ".sql"}

    def load(self, path: str, sql_engine: str = "") -> pd.DataFrame:
        """
        Load dataset from file.

        Args:
            path:       Absolute or relative path to the data file.
            sql_engine: SQLAlchemy connection string (required for .sql files).

        Returns:
            pd.DataFrame with the loaded data.

        Raises:
            LoadError: If file not found or format not supported.
        """
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)

        if not os.path.exists(path):
            raise LoadError(f"File not found: {path}")

        ext = os.path.splitext(path)[1].lower()
        if ext not in self.SUPPORTED:
            raise LoadError(f"Unsupported format '{ext}'. Supported: {self.SUPPORTED}")

        if ext == ".csv":
            sep = _sniff_separator(path)
            df = _read_csv(path, sep)
            df = _keep_leading_zeros(
                df, lambda cols: _read_csv(path, sep, usecols=cols, dtype=str))
            return _fix_day_first_dates(df)
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(path)
            df = _keep_leading_zeros(
                df, lambda cols: pd.read_excel(path, usecols=cols, dtype=str))
            return _fix_day_first_dates(df)
        if ext == ".parquet":
            return _fix_day_first_dates(pd.read_parquet(path))
        if ext == ".json":
            # Accepted at upload and handled everywhere in backend/dataframes/io.py;
            # this loader was the one place that refused it, so a .json dataset
            # uploaded and previewed fine and then died at training.
            return _fix_day_first_dates(pd.read_json(path))
        if ext == ".sql":
            return _fix_day_first_dates(self._load_sql(path, sql_engine))

    def load_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Accept a DataFrame directly (for programmatic use)."""
        if not isinstance(df, pd.DataFrame):
            raise LoadError("Expected a pandas DataFrame")
        return _fix_day_first_dates(df.copy())

    def _load_sql(self, path: str, engine_str: str) -> pd.DataFrame:
        if not engine_str:
            raise LoadError("sql_engine connection string is required for .sql files")
        from sqlalchemy import create_engine
        with open(path, "r", encoding="utf-8") as f:
            query = f.read()
        engine = create_engine(engine_str)
        return pd.read_sql(query, engine)
