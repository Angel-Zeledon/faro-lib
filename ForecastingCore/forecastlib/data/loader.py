"""
Loader — unified entry point for loading data from any source.

All class methods return a Dataset with inferred schema, type optimisation
and automatic warnings about data quality issues.

Supported sources
-----------------
* CSV files          Loader.from_csv(path)
* Excel files        Loader.from_excel(path)
* Parquet files      Loader.from_parquet(path)
* JSON files         Loader.from_json(path)
* Pandas DataFrames  Loader.from_dataframe(df)
* SQL databases      Loader.from_sql(db, host, database, user, password, ...)

SQL databases supported: postgresql, mysql, sqlite, mssql (SQL Server).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from forecastlib.data.dataset import Dataset
from forecastlib.data.schema import SchemaInfo
from forecastlib.exceptions import LoadError


# Memory warning threshold: datasets larger than this trigger a warning
_LARGE_ROW_THRESHOLD = 1_000_000
_LARGE_MB_THRESHOLD  = 500


class Loader:
    """
    Factory class for loading data into a Dataset.

    All methods are class methods — do not instantiate Loader directly.

    Examples
    --------
    >>> from forecastlib.data import Loader
    >>> ds = Loader.from_csv("sales.csv")
    >>> ds = Loader.from_sql(db="postgresql", host="localhost",
    ...                      database="sales", user="admin",
    ...                      password="secret", table="transactions")
    """

    # ------------------------------------------------------------------
    # File loaders
    # ------------------------------------------------------------------

    @classmethod
    def from_csv(
        cls,
        path: Union[str, Path],
        *,
        sep: str = ",",
        encoding: str = "utf-8",
        parse_dates: bool = True,
        dtype_backend: str = "numpy_nullable",
        low_memory: bool = False,
        **kwargs,
    ) -> Dataset:
        """
        Load a CSV file.

        Parameters
        ----------
        path : str or Path
        sep : str, default ","
        encoding : str, default "utf-8"
        parse_dates : bool
            Attempt to detect and parse date columns automatically.
        **kwargs
            Passed to pandas.read_csv.

        Returns
        -------
        Dataset
        """
        path = Path(path)
        if not path.exists():
            raise LoadError(
                f"File not found: {path}",
                suggestions=["Check the file path and working directory."],
            )
        try:
            df = pd.read_csv(
                path,
                sep=sep,
                encoding=encoding,
                low_memory=low_memory,
                **kwargs,
            )
        except Exception as e:
            raise LoadError(f"Failed to read CSV '{path}': {e}") from e

        if parse_dates:
            df = _try_parse_dates(df)

        df = _optimize_dtypes(df)
        return cls._build(df, source=f"csv:{path.name}")

    @classmethod
    def from_excel(
        cls,
        path: Union[str, Path],
        *,
        sheet_name: Union[str, int] = 0,
        parse_dates: bool = True,
        **kwargs,
    ) -> Dataset:
        """
        Load an Excel file (.xlsx or .xls).

        Parameters
        ----------
        path : str or Path
        sheet_name : str or int, default 0 (first sheet)
        **kwargs
            Passed to pandas.read_excel.
        """
        path = Path(path)
        if not path.exists():
            raise LoadError(f"File not found: {path}")
        try:
            df = pd.read_excel(path, sheet_name=sheet_name, **kwargs)
        except Exception as e:
            raise LoadError(f"Failed to read Excel '{path}': {e}") from e

        if parse_dates:
            df = _try_parse_dates(df)

        df = _optimize_dtypes(df)
        return cls._build(df, source=f"excel:{path.name}")

    @classmethod
    def from_parquet(
        cls,
        path: Union[str, Path],
        **kwargs,
    ) -> Dataset:
        """Load a Parquet file."""
        path = Path(path)
        if not path.exists():
            raise LoadError(f"File not found: {path}")
        try:
            df = pd.read_parquet(path, **kwargs)
        except Exception as e:
            raise LoadError(f"Failed to read Parquet '{path}': {e}") from e
        return cls._build(df, source=f"parquet:{path.name}")

    @classmethod
    def from_json(
        cls,
        path: Union[str, Path],
        *,
        orient: Optional[str] = None,
        parse_dates: bool = True,
        **kwargs,
    ) -> Dataset:
        """Load a JSON file."""
        path = Path(path)
        if not path.exists():
            raise LoadError(f"File not found: {path}")
        try:
            df = pd.read_json(path, orient=orient, **kwargs)
        except Exception as e:
            raise LoadError(f"Failed to read JSON '{path}': {e}") from e

        if parse_dates:
            df = _try_parse_dates(df)

        return cls._build(df, source=f"json:{path.name}")

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, source: str = "dataframe") -> Dataset:
        """
        Wrap an existing pandas DataFrame.

        >>> import pandas as pd
        >>> ds = Loader.from_dataframe(my_df)
        """
        if not isinstance(df, pd.DataFrame):
            raise LoadError(
                f"Expected a pandas DataFrame, got {type(df).__name__}.",
                suggestions=["Convert your data to a DataFrame first."],
            )
        df = _try_parse_dates(_optimize_dtypes(df.copy()))
        return cls._build(df, source=source)

    # ------------------------------------------------------------------
    # SQL loader
    # ------------------------------------------------------------------

    @classmethod
    def from_sql(
        cls,
        *,
        db: str,
        host: str = "localhost",
        port: Optional[int] = None,
        database: str,
        user: str,
        password: str,
        table: Optional[str] = None,
        query: Optional[str] = None,
        schema: Optional[str] = None,
        chunk_size: Optional[int] = None,
        parse_dates: bool = True,
    ) -> Dataset:
        """
        Load data from a SQL database.

        Parameters
        ----------
        db : str
            Database type. One of: "postgresql", "mysql", "sqlite", "mssql".
        host : str
            Database host.
        port : int, optional
            Port number.  Defaults depend on the database type.
        database : str
            Database name (or path for SQLite).
        user : str
            Username.
        password : str
            Password.
        table : str, optional
            Table name to read.  Either `table` or `query` must be provided.
        query : str, optional
            Custom SQL query.  Either `table` or `query` must be provided.
        schema : str, optional
            Database schema (PostgreSQL/SQL Server).
        chunk_size : int, optional
            If set, reads in chunks and concatenates (for large tables).
        parse_dates : bool, default True
            Attempt to parse datetime columns.

        Examples
        --------
        >>> ds = Loader.from_sql(
        ...     db="postgresql", host="localhost", database="sales",
        ...     user="admin", password="secret", table="transactions"
        ... )
        >>> ds = Loader.from_sql(
        ...     db="mysql", host="localhost", database="mydb",
        ...     user="root", password="pass",
        ...     query="SELECT * FROM sales WHERE year = 2024"
        ... )
        """
        if table is None and query is None:
            raise LoadError(
                "Either 'table' or 'query' must be provided.",
                suggestions=[
                    "Loader.from_sql(..., table='my_table')",
                    "Loader.from_sql(..., query='SELECT * FROM my_table WHERE ...')",
                ],
            )

        conn_str = _build_connection_string(db, host, port, database, user, password)
        sql_expr = query if query else (
            f'SELECT * FROM "{schema}"."{table}"' if schema
            else f'SELECT * FROM "{table}"'
        )

        try:
            from sqlalchemy import create_engine, text
        except ImportError:
            raise LoadError(
                "SQLAlchemy is required for SQL loading.",
                suggestions=["pip install sqlalchemy"],
            )

        try:
            engine = create_engine(conn_str)
            with engine.connect() as conn:
                if chunk_size:
                    chunks = pd.read_sql(
                        text(sql_expr), conn,
                        chunksize=chunk_size,
                    )
                    df = pd.concat(list(chunks), ignore_index=True)
                else:
                    df = pd.read_sql(text(sql_expr), conn)
        except Exception as e:
            raise LoadError(
                f"SQL query failed: {e}\n\nQuery: {sql_expr}",
                suggestions=[
                    "Check database credentials and network connectivity.",
                    "Verify that the table/query is correct.",
                    "Ensure the database driver is installed (e.g. psycopg2, pymysql).",
                ],
            ) from e

        if parse_dates:
            df = _try_parse_dates(df)

        df = _optimize_dtypes(df)
        return cls._build(df, source=f"sql:{db}:{database}.{table or 'custom_query'}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @classmethod
    def _build(cls, df: pd.DataFrame, source: str = "unknown") -> Dataset:
        """Infer schema, run health checks, and return Dataset."""
        _warn_health_issues(df)
        schema = SchemaInfo.infer(df)
        return Dataset(df, schema=schema, source=source)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _build_connection_string(
    db: str, host: str, port: Optional[int],
    database: str, user: str, password: str,
) -> str:
    db = db.lower().strip()
    defaults = {"postgresql": 5432, "mysql": 3306, "mssql": 1433, "sqlite": None}
    if db not in defaults:
        raise LoadError(
            f"Unsupported database type '{db}'.",
            suggestions=["Supported: postgresql, mysql, sqlite, mssql"],
        )

    if db == "sqlite":
        return f"sqlite:///{database}"

    port = port or defaults[db]
    drivers = {
        "postgresql": "postgresql+psycopg2",
        "mysql":      "mysql+pymysql",
        "mssql":      "mssql+pyodbc",
    }
    driver = drivers[db]
    return f"{driver}://{user}:{password}@{host}:{port}/{database}"


def _try_parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Attempt to parse object columns that look like dates."""
    for col in df.select_dtypes(include="object").columns:
        try:
            parsed = pd.to_datetime(df[col], infer_datetime_format=True, errors="coerce")
            if parsed.notna().mean() >= 0.85:
                df[col] = parsed
        except Exception:
            pass
    return df


def _optimize_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Downcast numeric columns to save memory."""
    for col in df.select_dtypes(include="float").columns:
        df[col] = pd.to_numeric(df[col], downcast="float")
    for col in df.select_dtypes(include="int").columns:
        df[col] = pd.to_numeric(df[col], downcast="integer")
    for col in df.select_dtypes(include="object").columns:
        if df[col].nunique() / max(len(df), 1) < 0.5:
            df[col] = df[col].astype("category")
    return df


def _warn_health_issues(df: pd.DataFrame) -> None:
    rows, cols = df.shape
    mb = df.memory_usage(deep=True).sum() / 1024 ** 2

    if rows == 0:
        warnings.warn("Loaded dataset has 0 rows.", UserWarning, stacklevel=4)
        return

    if rows > _LARGE_ROW_THRESHOLD:
        warnings.warn(
            f"Large dataset: {rows:,} rows ({mb:.0f} MB). "
            f"Consider using chunked reading or sampling.",
            UserWarning, stacklevel=4,
        )
    elif mb > _LARGE_MB_THRESHOLD:
        warnings.warn(
            f"Dataset is {mb:.0f} MB. Monitor memory usage.",
            UserWarning, stacklevel=4,
        )

    # Duplicate rows
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        warnings.warn(
            f"Dataset contains {dup_count:,} duplicate row(s). "
            f"Call dataset.clean.drop_duplicates() to remove them.",
            UserWarning, stacklevel=4,
        )

    # High null rates
    null_cols = {c: round(df[c].isna().mean() * 100, 1)
                 for c in df.columns if df[c].isna().mean() > 0.2}
    if null_cols:
        cols_str = ", ".join(f"'{c}'={v}%" for c, v in list(null_cols.items())[:5])
        warnings.warn(
            f"Columns with >20% nulls: {cols_str}. "
            f"Call dataset.fill.smart() or dataset.inspect.nulls() for details.",
            UserWarning, stacklevel=4,
        )
