"""
Data Source service — file uploads + SQL connections, both stored in `datasets` table.
SQL passwords are encrypted with Fernet (AES-128-CBC + HMAC) keyed from settings.secret_key.
"""

import base64
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from backend.config import settings
from backend.db.connection import execute, query, query_one, _json
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

ALLOWED_FILE_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet", ".json"}
SQL_ENGINES = {"postgresql", "mysql", "mssql", "oracle"}
MAX_FILE_MB = 50


# ── Helpers ────────────────────────────────────────────────────────────────────

def _check_file_size(file_path: str) -> None:
    size = Path(file_path).stat().st_size
    if size > MAX_FILE_MB * 1024 * 1024:
        raise ValueError(
            f"File too large ({size // (1024 * 1024)} MB). Maximum allowed is {MAX_FILE_MB} MB."
        )


def _make_sql_engine(cfg: dict, statement_timeout_ms: int = 30_000):
    import sqlalchemy
    conn_str = _build_conn_str(cfg)
    engine_type = cfg.get("engine", "postgresql")
    connect_args: dict = {"connect_timeout": 15}
    if engine_type == "postgresql":
        connect_args["options"] = f"-c statement_timeout={statement_timeout_ms}"
    elif engine_type == "mysql":
        timeout_s = max(1, statement_timeout_ms // 1000)
        connect_args.update({"read_timeout": timeout_s, "write_timeout": timeout_s})
    return sqlalchemy.create_engine(conn_str, connect_args=connect_args, pool_pre_ping=True)


def _fernet():
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def _enc(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def _dec(enc: str) -> str:
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except Exception:
        # Fallback for legacy base64-encoded passwords already in the database.
        try:
            return base64.b64decode(enc.encode()).decode()
        except Exception:
            return enc


def _public(row: dict) -> dict:
    """Strip sensitive fields before returning to client."""
    if not row:
        return row
    out = dict(row)
    if out.get("sql_config"):
        cfg = dict(out["sql_config"])
        cfg.pop("password_enc", None)
        out["sql_config"] = cfg
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── File sources ───────────────────────────────────────────────────────────────

async def create_file_source(
    tenant_id: str,
    user_id: str,
    file: UploadFile,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_FILE_EXTENSIONS:
        raise ValueError(
            f"File type '{suffix}' not supported. Allowed: {sorted(ALLOWED_FILE_EXTENSIONS)}"
        )

    content = await file.read()
    size_bytes = len(content)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if not settings.testing_mode and size_bytes > max_bytes:
        raise ValueError(
            f"File {size_bytes / 1024 / 1024:.1f} MB exceeds limit of {settings.max_upload_size_mb} MB"
        )

    source_id = generate_id("ds")
    from backend.storage import paths
    dst_dir = paths.dataset_dir(tenant_id, source_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    file_path = dst_dir / f"data{suffix}"
    with open(file_path, "wb") as f:
        f.write(content)

    display_name = name or Path(file.filename or "upload").stem

    # Eagerly count rows so the UI shows a non-zero value immediately
    row_count = None
    col_count = None
    try:
        import io as _io
        if suffix == ".csv":
            row_count = max(0, content.count(b"\n") - 1)
        elif suffix in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(_io.BytesIO(content), read_only=True, data_only=True)
            row_count = sum((ws.max_row or 1) - 1 for ws in wb.worksheets)
            col_count  = max((ws.max_column or 0) for ws in wb.worksheets) or None
            wb.close()
        elif suffix == ".parquet":
            import pyarrow.parquet as pq
            meta = pq.read_metadata(_io.BytesIO(content))
            row_count = meta.num_rows
            col_count = meta.num_columns
        elif suffix == ".json":
            import json as _json
            data = _json.loads(content)
            if isinstance(data, list):
                row_count = len(data)
                if data and isinstance(data[0], dict):
                    col_count = len(data[0])
    except Exception:
        pass

    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, description, original_filename, file_type, file_path,
            size_bytes, row_count, column_count, source_type, connection_status, uploaded_by, uploaded_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'file','connected',%s,NOW(),NOW())""",
        (
            source_id, tenant_id, display_name, description,
            file.filename, suffix.lstrip("."), str(file_path),
            size_bytes, row_count, col_count, user_id,
        ),
    )
    return _public(get_source(tenant_id, source_id))


async def replace_file_source(
    tenant_id: str,
    user_id: str,
    source_id: str,
    file: UploadFile,
) -> dict:
    existing = get_source(tenant_id, source_id)
    if not existing:
        raise ValueError(f"Data source {source_id} not found")
    if existing.get("source_type") != "file":
        raise ValueError("Cannot replace file on a SQL data source")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_FILE_EXTENSIONS:
        raise ValueError(f"File type '{suffix}' not supported")

    content = await file.read()
    size_bytes = len(content)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if not settings.testing_mode and size_bytes > max_bytes:
        raise ValueError(f"File too large ({size_bytes / 1024 / 1024:.1f} MB)")

    from backend.storage import paths
    dst_dir = paths.dataset_dir(tenant_id, source_id)
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Atomic write: save to .tmp first, verify, then swap — prevents data loss
    # if disk is full or write fails partway through.
    tmp_path = dst_dir / f"data{suffix}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(content)
        if tmp_path.stat().st_size != size_bytes:
            raise ValueError("File write verification failed: size mismatch.")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    for old in dst_dir.glob("data.*"):
        if old != tmp_path:
            old.unlink(missing_ok=True)

    file_path = dst_dir / f"data{suffix}"
    tmp_path.replace(file_path)

    # Eagerly count rows on replace so the UI shows a non-zero value immediately
    row_count = None
    col_count = None
    try:
        import io as _io
        if suffix == ".csv":
            row_count = max(0, content.count(b"\n") - 1)
        elif suffix in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(_io.BytesIO(content), read_only=True, data_only=True)
            row_count = sum((ws.max_row or 1) - 1 for ws in wb.worksheets)
            col_count = max((ws.max_column or 0) for ws in wb.worksheets) or None
            wb.close()
        elif suffix == ".parquet":
            import pyarrow.parquet as pq
            meta = pq.read_metadata(_io.BytesIO(content))
            row_count = meta.num_rows
            col_count = meta.num_columns
        elif suffix == ".json":
            import json as _json_mod
            data = _json_mod.loads(content)
            if isinstance(data, list):
                row_count = len(data)
                if data and isinstance(data[0], dict):
                    col_count = len(data[0])
    except Exception:
        pass

    execute(
        """UPDATE datasets
           SET original_filename=%s, file_type=%s, file_path=%s,
               size_bytes=%s, row_count=%s, column_count=%s,
               preview_cache=NULL, connection_status='connected', updated_at=NOW()
           WHERE id=%s AND tenant_id=%s""",
        (file.filename, suffix.lstrip("."), str(file_path), size_bytes, row_count, col_count, source_id, tenant_id),
    )
    return _public(get_source(tenant_id, source_id))


# ── SQL sources ────────────────────────────────────────────────────────────────

def create_sql_source(
    tenant_id: str,
    user_id: str,
    name: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    engine: str,
    description: Optional[str] = None,
) -> dict:
    if engine not in SQL_ENGINES:
        raise ValueError(f"Unsupported engine '{engine}'. Options: {sorted(SQL_ENGINES)}")

    source_id = generate_id("ds")
    sql_config = {
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "password_enc": _enc(password),
        "engine": engine,
    }
    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, description, file_type, source_type,
            connection_status, sql_config, uploaded_by, uploaded_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,'sql','pending',%s,%s,NOW(),NOW())""",
        (source_id, tenant_id, name, description, engine, _json(sql_config), user_id),
    )
    return _public(get_source(tenant_id, source_id))


def update_sql_config(
    tenant_id: str,
    source_id: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    engine: str,
) -> dict:
    existing = get_source(tenant_id, source_id)
    if not existing:
        raise ValueError(f"Data source {source_id} not found")

    old_cfg = existing.get("sql_config") or {}
    # Preserve password if not provided
    if password:
        enc_pw = _enc(password)
    else:
        enc_pw = old_cfg.get("password_enc", "")

    sql_config = {
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "password_enc": enc_pw,
        "engine": engine,
    }
    execute(
        """UPDATE datasets
           SET sql_config=%s, file_type=%s, connection_status='pending', updated_at=NOW()
           WHERE id=%s AND tenant_id=%s""",
        (_json(sql_config), engine, source_id, tenant_id),
    )
    return _public(get_source(tenant_id, source_id))


def test_sql_connection(tenant_id: str, source_id: str) -> dict:
    existing = get_source(tenant_id, source_id)
    if not existing:
        raise ValueError(f"Data source {source_id} not found")
    cfg = existing.get("sql_config") or {}
    try:
        import sqlalchemy
        engine = _make_sql_engine(cfg, statement_timeout_ms=5_000)
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        execute(
            "UPDATE datasets SET connection_status='connected', updated_at=NOW() WHERE id=%s AND tenant_id=%s",
            (source_id, tenant_id),
        )
        return {"ok": True, "status": "connected"}
    except Exception as e:
        execute(
            "UPDATE datasets SET connection_status='error', updated_at=NOW() WHERE id=%s AND tenant_id=%s",
            (source_id, tenant_id),
        )
        return {"ok": False, "status": "error", "error": str(e)}


def execute_sql_query(tenant_id: str, source_id: str, sql: str, limit: int = 500) -> dict:
    existing = get_source(tenant_id, source_id)
    if not existing:
        raise ValueError(f"Data source {source_id} not found")
    if existing.get("connection_status") != "connected":
        raise ValueError("Data source is not connected. Test connection first.")
    cfg = existing.get("sql_config") or {}
    try:
        import sqlalchemy
        engine = _make_sql_engine(cfg, statement_timeout_ms=30_000)
        with engine.connect() as conn:
            result = conn.execute(sqlalchemy.text(sql))
            rows = result.fetchmany(limit)
            columns = list(result.keys())
        data = [dict(zip(columns, row)) for row in rows]
        return {
            "columns": columns,
            "rows": data,
            "row_count": len(data),
            "truncated": len(data) >= limit,
        }
    except Exception as e:
        raise ValueError(f"Query failed: {e}")


def save_sql_query(tenant_id: str, source_id: str, sql: str) -> dict:
    execute(
        "UPDATE datasets SET saved_query=%s, updated_at=NOW() WHERE id=%s AND tenant_id=%s",
        (sql, source_id, tenant_id),
    )
    return _public(get_source(tenant_id, source_id))


def _build_conn_str(cfg: dict) -> str:
    host = cfg.get("host", "localhost")
    port = cfg.get("port", 5432)
    database = cfg.get("database", "")
    username = cfg.get("username", "")
    password = _dec(cfg.get("password_enc", "")) if cfg.get("password_enc") else ""
    engine = cfg.get("engine", "postgresql")
    from urllib.parse import quote_plus
    pw_enc = quote_plus(password)
    user_enc = quote_plus(username)
    drivers = {
        "postgresql": "postgresql+psycopg2",
        "mysql": "mysql+pymysql",
        "mssql": "mssql+pyodbc",
        "oracle": "oracle+cx_oracle",
    }
    driver = drivers.get(engine, "postgresql+psycopg2")
    return f"{driver}://{user_enc}:{pw_enc}@{host}:{port}/{database}"


# ── Preview ────────────────────────────────────────────────────────────────────

def get_preview(tenant_id: str, source_id: str, rows: int = 100, sheet: Optional[str] = None) -> dict:
    src = get_source(tenant_id, source_id)
    if not src:
        raise ValueError(f"Data source {source_id} not found")

    if src.get("source_type") == "sql":
        saved_q = src.get("saved_query")
        if not saved_q:
            return {"columns": [], "rows": [], "row_count": 0, "sheets": None}
        return execute_sql_query(tenant_id, source_id, saved_q, limit=rows)

    # File source
    file_path = src.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise ValueError("File not found on disk. Please re-upload.")

    import pandas as pd
    suffix = Path(file_path).suffix.lower()
    sheets = None
    total_rows: Optional[int] = None

    if suffix in (".xlsx", ".xls"):
        _check_file_size(file_path)
        xf = pd.ExcelFile(file_path)
        sheets = xf.sheet_names
        target_sheet = sheet or sheets[0]
        full_df = pd.read_excel(file_path, sheet_name=target_sheet)
        total_rows = len(full_df)
        df = full_df.head(rows)
    elif suffix == ".json":
        full_df = pd.read_json(file_path)
        total_rows = len(full_df)
        df = full_df.head(rows)
    elif suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
            total_rows = pq.read_metadata(file_path).num_rows
        except Exception:
            pass
        df = pd.read_parquet(file_path).head(rows)
    else:  # CSV
        df = pd.read_csv(file_path, nrows=rows)

    df = df.where(pd.notna(df), None)
    columns = list(df.columns)
    data_rows = df.to_dict(orient="records")
    # Convert non-serializable types
    for row in data_rows:
        for k, v in row.items():
            if hasattr(v, "item"):
                row[k] = v.item()

    # Update stats if not set — single read already done above for Excel/JSON/Parquet
    if not src.get("row_count"):
        try:
            if total_rows is None:
                # CSV: count lines without loading entire file into memory
                with open(file_path, "r", encoding="utf-8", errors="replace") as _f:
                    total_rows = sum(1 for _ in _f) - 1
            execute(
                "UPDATE datasets SET row_count=%s, column_count=%s, updated_at=NOW() WHERE id=%s AND tenant_id=%s",
                (total_rows, len(df.columns), source_id, tenant_id),
            )
        except Exception as _e:
            log.warning("[preview] failed to update row count for source=%s: %s", source_id, _e)

    return {
        "columns": columns,
        "rows": data_rows,
        "row_count": len(data_rows),
        "sheets": sheets,
        "active_sheet": sheet or (sheets[0] if sheets else None),
        "truncated": len(data_rows) >= rows,
    }


# ── CRUD ───────────────────────────────────────────────────────────────────────

def get_source(tenant_id: str, source_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM datasets WHERE id=%s AND tenant_id=%s",
        (source_id, tenant_id),
    )


def list_sources(tenant_id: str, skip: int = 0, limit: int = 50) -> list[dict]:
    rows = query(
        "SELECT * FROM datasets WHERE tenant_id=%s ORDER BY updated_at DESC NULLS LAST, uploaded_at DESC LIMIT %s OFFSET %s",
        (tenant_id, limit, skip),
    )
    return [_public(r) for r in rows]


def count_sources(tenant_id: str) -> int:
    row = query_one("SELECT COUNT(*) AS cnt FROM datasets WHERE tenant_id=%s", (tenant_id,))
    return row["cnt"] if row else 0


def delete_source(tenant_id: str, source_id: str) -> None:
    src = get_source(tenant_id, source_id)
    if not src:
        return
    # Remove file if exists
    if src.get("file_path"):
        p = Path(src["file_path"])
        if p.exists():
            p.unlink(missing_ok=True)
        if p.parent.exists() and not any(p.parent.iterdir()):
            p.parent.rmdir()
    execute("DELETE FROM datasets WHERE id=%s AND tenant_id=%s", (source_id, tenant_id))


def rename_source(tenant_id: str, source_id: str, name: str, description: Optional[str] = None) -> dict:
    execute(
        "UPDATE datasets SET name=%s, description=%s, updated_at=NOW() WHERE id=%s AND tenant_id=%s",
        (name, description, source_id, tenant_id),
    )
    return _public(get_source(tenant_id, source_id))


# ── Analysis helpers ───────────────────────────────────────────────────────────

def load_dataframe(tenant_id: str, source_id: str, sheet: Optional[str] = None, max_rows: int = 50_000):
    """Load DataFrame for analysis, capped at max_rows to bound memory usage."""
    import pandas as pd

    src = get_source(tenant_id, source_id)
    if not src:
        raise ValueError(f"Data source {source_id} not found")

    if src.get("source_type") == "sql":
        saved_q = src.get("saved_query")
        if not saved_q:
            raise ValueError("SQL source has no saved query to analyze")
        cfg = src.get("sql_config") or {}
        import sqlalchemy
        eng = _make_sql_engine(cfg, statement_timeout_ms=60_000)
        with eng.connect() as conn:
            result = conn.execute(sqlalchemy.text(saved_q))
            rows = result.fetchmany(max_rows)
            cols = list(result.keys())
        return pd.DataFrame(rows, columns=cols)

    file_path = src.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise ValueError("File not found on disk. Please re-upload.")

    suffix = Path(file_path).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        _check_file_size(file_path)
        xf = pd.ExcelFile(file_path)
        target_sheet = sheet or xf.sheet_names[0]
        return pd.read_excel(file_path, sheet_name=target_sheet, nrows=max_rows)
    elif suffix == ".json":
        return pd.read_json(file_path).head(max_rows)
    else:
        return pd.read_csv(file_path, nrows=max_rows)


def detect_columns(df) -> dict:
    """Heuristic auto-detection of date, target, and group columns."""
    import pandas as pd

    cols = list(df.columns)
    lower = {c.lower(): c for c in cols}

    date_hints    = ["date", "fecha", "dt", "time", "period", "week", "month", "year", "timestamp", "dia", "semana", "mes"]
    target_hints  = ["sales", "ventas", "demand", "qty", "quantity", "units", "target", "value", "amount", "revenue", "uds", "importe", "venta"]
    sku_hints     = ["sku", "product", "item", "grupo", "group", "category", "categoria", "codigo", "code", "product_id", "item_id"]

    def find(hints):
        for h in hints:
            for lc, orig in lower.items():
                if h in lc:
                    return orig
        return None

    date_col = find(date_hints)
    if not date_col:
        for c in cols:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                date_col = c
                break

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    target_col = find(target_hints)
    if not target_col:
        for c in numeric_cols:
            if c != date_col and "id" not in c.lower():
                target_col = c
                break
    if not target_col and numeric_cols:
        target_col = numeric_cols[0]

    str_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    sku_col = find(sku_hints)
    if not sku_col:
        for c in str_cols:
            if c != date_col:
                sku_col = c
                break

    return {"date_col": date_col, "target_col": target_col, "sku_col": sku_col}
