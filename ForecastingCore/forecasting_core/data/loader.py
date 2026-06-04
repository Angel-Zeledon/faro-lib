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
            return pd.read_csv(path)
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
