"""
DataLoader — loads datasets from multiple sources.

Supported formats: CSV, Excel (.xlsx/.xls), Parquet, SQL query file.

Example:
    loader = DataLoader()
    df = loader.load("sales.csv")
    df = loader.load("sales.parquet")
    df = loader.load("query.sql", sql_engine="postgresql://user:pw@host/db")
"""

import os
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


class LoadError(Exception):
    pass


class DataLoader:
    """Loads a DataFrame from file path or SQL."""

    SUPPORTED = {".csv", ".xlsx", ".xls", ".parquet", ".sql"}

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
            return pd.read_csv(path, sep=_sniff_separator(path), encoding="utf-8-sig")
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(path)
        if ext == ".parquet":
            return pd.read_parquet(path)
        if ext == ".sql":
            return self._load_sql(path, sql_engine)

    def load_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Accept a DataFrame directly (for programmatic use)."""
        if not isinstance(df, pd.DataFrame):
            raise LoadError("Expected a pandas DataFrame")
        return df.copy()

    def _load_sql(self, path: str, engine_str: str) -> pd.DataFrame:
        if not engine_str:
            raise LoadError("sql_engine connection string is required for .sql files")
        from sqlalchemy import create_engine
        with open(path, "r", encoding="utf-8") as f:
            query = f.read()
        engine = create_engine(engine_str)
        return pd.read_sql(query, engine)
