"""
Data Source service — file uploads + SQL connections, both stored in `datasets` table.

SQL secrets (password, CA certificate) are encrypted at rest with Fernet
(`secrets.py`). Everything that touches a customer database goes through
`client.open_session` (SSRF policy, TLS, timeouts, read-only, per-tenant
concurrency, transient-only retries) and every failure leaves as a coded
`AppError` (`errors.classify`) — never as raw driver text.
"""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import UploadFile

from backend.config import settings
from backend.datasources import connection as conn_mod
from backend.datasources import secrets as ds_secrets
from backend.db.connection import execute, query, query_one, _json
from backend.errors import AppError
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

ALLOWED_FILE_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet", ".json"}
SQL_ENGINES = set(conn_mod.ENGINES)
FETCH_BATCH = 10_000
# How far into a result the query editor may page (rows skipped server-side by
# streaming them past). Beyond this, narrow the query.
MAX_QUERY_OFFSET = 100_000
EXPORT_FORMATS = ("xlsx", "csv")
MAX_FILE_MB = 50

# Every rejection a user can trip in this module answers with 400, the status
# the API's `_service_error` wrapper used before these became AppErrors — the
# wire contract is unchanged, only `error_code` + `error_params` are added.
_REJECTED = 400


def _mb(size_bytes: float) -> float:
    """Bytes as megabytes, rounded for display. A param, never baked into prose."""
    return round(size_bytes / 1024 / 1024, 1)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _check_file_size(file_path: str) -> None:
    size = Path(file_path).stat().st_size
    if size > MAX_FILE_MB * 1024 * 1024:
        raise AppError(
            "data_source_file_too_large_to_read",
            f"File too large ({_mb(size)} MB). Maximum allowed is {MAX_FILE_MB} MB.",
            status_code=_REJECTED,
            params={"size_mb": _mb(size), "max_mb": MAX_FILE_MB},
        )


def _reject_unsupported_extension(suffix: str) -> None:
    """Upload/replace guard. The extension and the allowed set travel as params
    so the frontend can build the sentence in the user's language."""
    if suffix not in ALLOWED_FILE_EXTENSIONS:
        allowed = sorted(ALLOWED_FILE_EXTENSIONS)
        raise AppError(
            "data_source_file_type_unsupported",
            f"File type '{suffix}' is not supported. Allowed: {', '.join(allowed)}",
            status_code=_REJECTED,
            params={"extension": suffix, "allowed": ", ".join(allowed)},
        )


def _reject_oversized_upload(size_bytes: int) -> None:
    """Upload/replace size guard, against the configured upload ceiling."""
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if not settings.testing_mode and size_bytes > max_bytes:
        raise AppError(
            "data_source_file_too_large",
            f"File is {_mb(size_bytes)} MB, over the {settings.max_upload_size_mb} MB limit.",
            status_code=_REJECTED,
            params={"size_mb": _mb(size_bytes), "max_mb": settings.max_upload_size_mb},
        )


def _secrets_of(cfg: dict) -> tuple[str, ...]:
    """The cleartext secrets of a stored config, for scrubbing error text.
    Never raises: a config whose password cannot be decrypted has nothing to
    leak."""
    try:
        password = ds_secrets.decrypt(cfg.get("password_enc") or "")
    except Exception:  # noqa: BLE001
        return ()
    return (password,) if password else ()


def _classified(exc: BaseException, cfg: dict, *, during: str = "query") -> AppError:
    """Any failure on a customer database as the coded `AppError` to raise."""
    if isinstance(exc, AppError):
        return exc
    from backend.datasources.errors import as_app_error
    return as_app_error(exc, engine=cfg.get("engine", ""), secrets=_secrets_of(cfg),
                        host=str(cfg.get("host") or ""), database=str(cfg.get("database") or ""),
                        during=during)


@contextmanager
def _read_only_rows(cfg: dict, sql: str, statement_timeout_ms: Optional[int] = None, *,
                    stream: bool = False, tenant_id: Optional[str] = None, bulk: bool = False):
    """Validate `sql`, run it on the customer's database inside a read-only
    session (`client.open_session`), yield the SQLAlchemy result, and ALWAYS
    roll back.

    The single door every user- or saved-query execution goes through: the
    preview, the analysis load, execute, export, materialize and the scheduled
    refresh. A statement that is not one plain read never reaches a
    connection.

    The statement is sent as DRIVER SQL (no SQLAlchemy bind-parameter
    parsing), so ``WHERE note = 'a :b'`` is the literal it looks like instead
    of a missing bind parameter, and a ``%`` in a LIKE pattern is not a format
    directive (no parameters are ever passed with user SQL).
    """
    from backend.datasources.client import open_session
    from backend.datasources.sql_guard import validate_read_only_sql

    engine_type = cfg.get("engine", "postgresql")
    statement = validate_read_only_sql(sql, engine_type)
    timeout_s = max(1, int(statement_timeout_ms) // 1000) if statement_timeout_ms else None
    with open_session(cfg, tenant_id=tenant_id, statement_timeout_s=timeout_s, bulk=bulk) as session:
        # no_parameters: the DBAPI cursor gets the statement ALONE, so neither a
        # '%' (psycopg2/PyMySQL format directives) nor a ':name' (SQLAlchemy's
        # bind syntax) inside a literal is read as a parameter.
        options: dict = {"no_parameters": True}
        if stream:
            options["stream_results"] = True
        yield session.conn.execution_options(**options).exec_driver_sql(statement)


def _public(row: dict) -> dict:
    """Strip every secret before a row leaves the service.

    The stored config keeps `password_enc` and `ssl_ca_enc` (ciphertext); the
    client gets only whether they are set, plus what is safe to show about a
    CA certificate (subject, expiry, fingerprint)."""
    if not row:
        return row
    out = dict(row)
    if out.get("sql_config"):
        cfg = dict(out["sql_config"])
        cfg["has_password"] = bool(cfg.pop("password_enc", None))
        cfg["has_ssl_ca"] = bool(cfg.pop("ssl_ca_enc", None))
        engine = cfg.get("engine") or "postgresql"
        cfg.setdefault("ssl_mode", conn_mod.LEGACY_SSL_MODE.get(engine, "prefer"))
        cfg.setdefault("connect_timeout_s", conn_mod.DEFAULT_CONNECT_TIMEOUT_S)
        cfg.setdefault("statement_timeout_s", conn_mod.DEFAULT_STATEMENT_TIMEOUT_S)
        out["sql_config"] = cfg
    return out


def _track_new_sales(tenant_id: str, dataset_id: str) -> None:
    """A sales file just landed: grade the live forecasts against it in the
    background (notification only; see forecast_check/tracking.py). Never
    raises and never delays the upload response."""
    try:
        from backend.forecast_check.tracking import schedule_tracking
        schedule_tracking(tenant_id, dataset_id)
    except Exception:  # noqa: BLE001
        log.exception("could not schedule accuracy tracking for dataset=%s", dataset_id)


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
    _reject_unsupported_extension(suffix)

    content = await file.read()
    size_bytes = len(content)
    _reject_oversized_upload(size_bytes)

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
            import csv as _csv
            header = content.split(b"\n", 1)[0].decode("utf-8", "replace")
            if header.strip():
                col_count = len(next(_csv.reader([header])))
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
    _track_new_sales(tenant_id, source_id)
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
        raise AppError(
            "data_source_not_replaceable",
            "Only file data sources can have their file replaced.",
            status_code=_REJECTED,
        )

    suffix = Path(file.filename or "").suffix.lower()
    _reject_unsupported_extension(suffix)

    content = await file.read()
    size_bytes = len(content)
    _reject_oversized_upload(size_bytes)

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

    # The file being replaced is what any session trained on this dataset read.
    # It is moved aside, never unlinked: a forecast that cites "sales through
    # March" must stay reproducible after somebody uploads a corrected file.
    superseded = [old for old in dst_dir.glob("data.*") if old != tmp_path]
    if superseded:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        keep_dir = dst_dir / "previous" / stamp
        keep_dir.mkdir(parents=True, exist_ok=True)
        for old in superseded:
            old.replace(keep_dir / old.name)

    file_path = dst_dir / f"data{suffix}"
    tmp_path.replace(file_path)

    # Eagerly count rows on replace so the UI shows a non-zero value immediately
    row_count = None
    col_count = None
    try:
        import io as _io
        if suffix == ".csv":
            row_count = max(0, content.count(b"\n") - 1)
            import csv as _csv
            header = content.split(b"\n", 1)[0].decode("utf-8", "replace")
            if header.strip():
                col_count = len(next(_csv.reader([header])))
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
    _track_new_sales(tenant_id, source_id)
    return _public(get_source(tenant_id, source_id))


# ── SQL sources ────────────────────────────────────────────────────────────────

def _not_found(source_id: str) -> AppError:
    return AppError("data_source_not_found", f"Data source {source_id} not found",
                    status_code=404, params={"source_id": source_id})


def _require_sql(src: dict, action_code: str = "data_source_not_sql") -> None:
    if src.get("source_type") != "sql":
        raise AppError(action_code, "This action only applies to SQL data sources.",
                       status_code=_REJECTED)


def _merge_connection_string(fields: dict, connection_string: Optional[str]) -> tuple[dict, Optional[str]]:
    """Fill the fields a person did NOT type from a pasted connection string.
    Typed fields win. Returns (fields, password-from-the-string)."""
    if not connection_string or not connection_string.strip():
        return fields, None
    parsed = conn_mod.parse_connection_string(connection_string)
    merged = dict(fields)
    for key in ("engine", "host", "port", "database", "username", "ssl_mode"):
        if merged.get(key) in (None, "") and parsed.get(key) not in (None, ""):
            merged[key] = parsed[key]
    return merged, parsed.get("password")


def _ca_fields(engine: str, ssl_mode: str, ssl_ca: Optional[str],
               existing: Optional[dict] = None, clear: bool = False) -> dict:
    """The stored CA-related keys: the ciphertext and what is safe to show."""
    if ssl_ca and ssl_ca.strip():
        info = conn_mod.inspect_ca_pem(ssl_ca)
        conn_mod.check_ca_compatible(engine, ssl_mode, True)
        return {"ssl_ca_enc": ds_secrets.encrypt(ssl_ca.strip()), "ssl_ca": info}
    keep = (existing or {}) if not clear else {}
    has = bool(keep.get("ssl_ca_enc"))
    conn_mod.check_ca_compatible(engine, ssl_mode, has)
    if has:
        return {"ssl_ca_enc": keep["ssl_ca_enc"], "ssl_ca": keep.get("ssl_ca")}
    return {}


def _check_reachable_literal(host: str, engine: str) -> None:
    """Refuse at SAVE time a host that is a forbidden IP literal. A host name
    is checked when it is resolved, on every connection."""
    from backend.datasources import network
    base = conn_mod.split_mssql_instance(host)[0] if engine == "mssql" else host
    network.check_literal(base, allow_private=network.allow_private_hosts())


def connection_summary(cfg: dict) -> dict:
    """The non-secret description of a stored config, for the audit trail."""
    return {
        "engine": cfg.get("engine"), "host": cfg.get("host"), "port": cfg.get("port"),
        "database": cfg.get("database"), "username": cfg.get("username"),
        "ssl_mode": cfg.get("ssl_mode"), "ssl_ca": bool(cfg.get("ssl_ca_enc")),
        "connect_timeout_s": cfg.get("connect_timeout_s"),
        "statement_timeout_s": cfg.get("statement_timeout_s"),
    }


def build_sql_config(fields: dict, password: Optional[str], *, ssl_ca: Optional[str] = None,
                     connection_string: Optional[str] = None) -> dict:
    """Validate a new connection's fields and return the config to store."""
    fields, cs_password = _merge_connection_string(fields, connection_string)
    norm = conn_mod.normalize_fields(fields)
    _check_reachable_literal(norm["host"], norm["engine"])
    secret = password if password else (cs_password or "")
    cfg = dict(norm)
    cfg["password_enc"] = ds_secrets.encrypt(secret)
    cfg.update(_ca_fields(norm["engine"], norm["ssl_mode"], ssl_ca))
    return cfg


def create_sql_source(
    tenant_id: str,
    user_id: str,
    name: str,
    host: Optional[str],
    port: Any,
    database: Optional[str],
    username: Optional[str],
    password: Optional[str],
    engine: Optional[str],
    description: Optional[str] = None,
    *,
    ssl_mode: Optional[str] = None,
    ssl_ca: Optional[str] = None,
    connect_timeout_s: Optional[int] = None,
    statement_timeout_s: Optional[int] = None,
    connection_string: Optional[str] = None,
) -> dict:
    sql_config = build_sql_config(
        {"engine": engine, "host": host, "port": port, "database": database,
         "username": username, "ssl_mode": ssl_mode,
         "connect_timeout_s": connect_timeout_s, "statement_timeout_s": statement_timeout_s},
        password, ssl_ca=ssl_ca, connection_string=connection_string,
    )
    source_id = generate_id("ds")
    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, description, file_type, source_type,
            connection_status, sql_config, uploaded_by, uploaded_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,'sql','pending',%s,%s,NOW(),NOW())""",
        (source_id, tenant_id, name, description, sql_config["engine"], _json(sql_config), user_id),
    )
    return _public(get_source(tenant_id, source_id))


# Fields whose change points the stored password at a DIFFERENT server. Keeping
# the password across such a change would let anyone allowed to edit the
# connection send the company's ERP password to a host of their choosing.
_RETARGETING_FIELDS = ("engine", "host", "port")


def _stored_target(old: dict) -> dict:
    """The stored engine/host/port in normalised form, so a legacy row whose
    host was saved as "DB.Example.com" is not mistaken for a change when the
    form sends back "db.example.com"."""
    engine = old.get("engine") or "postgresql"
    host = str(old.get("host") or "")
    try:
        host = conn_mod.normalize_host(host, engine)
    except AppError:
        pass
    try:
        port = int(old.get("port") or conn_mod.DEFAULT_PORTS.get(engine, 0))
    except (TypeError, ValueError):
        port = None
    return {"engine": engine, "host": host, "port": port}


def update_sql_config(
    tenant_id: str,
    source_id: str,
    host: Optional[str] = None,
    port: Any = None,
    database: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    engine: Optional[str] = None,
    *,
    ssl_mode: Optional[str] = None,
    ssl_ca: Optional[str] = None,
    clear_ssl_ca: bool = False,
    connect_timeout_s: Optional[int] = None,
    statement_timeout_s: Optional[int] = None,
    connection_string: Optional[str] = None,
) -> dict:
    """Change a connection without re-entering what did not change.

    Every field is optional: an omitted one keeps its stored value, the
    password included — EXCEPT when the engine, host or port changes, which
    requires the password again (`data_source_password_required`)."""
    existing = get_source(tenant_id, source_id)
    if not existing:
        raise _not_found(source_id)
    _require_sql(existing)
    old = dict(existing.get("sql_config") or {})
    old_engine = old.get("engine") or "postgresql"

    given = {"engine": engine, "host": host, "port": port, "database": database,
             "username": username, "ssl_mode": ssl_mode,
             "connect_timeout_s": connect_timeout_s, "statement_timeout_s": statement_timeout_s}
    given, cs_password = _merge_connection_string(given, connection_string)
    password = password or cs_password

    merged = {k: (v if v not in (None, "") else old.get(k)) for k, v in given.items()}
    if not merged.get("ssl_mode"):
        merged["ssl_mode"] = conn_mod.LEGACY_SSL_MODE.get(merged.get("engine") or old_engine, "prefer")
    if merged.get("engine") != old_engine and given.get("ssl_mode") in (None, ""):
        # A mode the old engine supported may not exist on the new one.
        merged["ssl_mode"] = conn_mod.DEFAULT_SSL_MODE.get(merged.get("engine") or "", "prefer")
    norm = conn_mod.normalize_fields(merged)

    changed = [f for f in _RETARGETING_FIELDS if norm.get(f) != _stored_target(old).get(f)]
    if changed and not password:
        raise AppError(
            "data_source_password_required",
            "Enter the password again: the connection now points at a different "
            "server, and the stored password is never sent to a new one.",
            status_code=_REJECTED, params={"fields": ", ".join(changed)},
        )
    _check_reachable_literal(norm["host"], norm["engine"])

    cfg = dict(norm)
    cfg["password_enc"] = ds_secrets.encrypt(password) if password else old.get("password_enc", "")
    cfg.update(_ca_fields(norm["engine"], norm["ssl_mode"], ssl_ca, existing=old, clear=clear_ssl_ca))
    execute(
        """UPDATE datasets
           SET sql_config=%s, file_type=%s, connection_status='pending', updated_at=NOW()
           WHERE id=%s AND tenant_id=%s""",
        (_json(cfg), norm["engine"], source_id, tenant_id),
    )
    from backend.datasources.schema_cache import invalidate
    invalidate(tenant_id, source_id)
    return _public(get_source(tenant_id, source_id))


def parse_connection_string_public(text: str) -> dict:
    """What a pasted connection string says, normalised where possible, with
    the password reduced to whether there was one. The password never comes
    back to the browser: the form sends the string again on save."""
    parsed = conn_mod.parse_connection_string(text)
    password = parsed.pop("password", None)
    out = dict(parsed)
    engine = out.get("engine")
    if engine in conn_mod.ENGINES:
        if out.get("host"):
            out["host"] = conn_mod.normalize_host(out["host"], engine)
        out["port"] = conn_mod.normalize_port(out["port"]) if out.get("port") else conn_mod.DEFAULT_PORTS[engine]
        if out.get("ssl_mode"):
            out["ssl_mode"] = conn_mod.normalize_ssl_mode(out["ssl_mode"], engine)
    out["has_password"] = bool(password)
    return out


def _probe_summary(result: dict) -> dict:
    """What is kept of the last connection test on the source row, so the
    screen can show it again without re-running it."""
    return {
        "ok": result.get("ok"),
        "tested_at": result.get("tested_at"),
        "failed_stage": result.get("failed_stage"),
        "error": result.get("error"),
        "stages": [{k: s.get(k) for k in ("stage", "status", "code", "params") if s.get(k) is not None}
                   for s in result.get("stages") or []],
        "can_write": (result.get("write_access") or {}).get("can_write"),
        "table_count": result.get("table_count"),
        "server_version": result.get("server_version"),
    }


def test_sql_connection(tenant_id: str, source_id: str) -> dict:
    """Run the staged connection test and store its verdict.

    `connection_status` becomes `connected` only when no stage failed; the
    stage summary lands in `sql_config.last_test` (no secrets in it)."""
    from backend.datasources.probe import run_probe
    from backend.datasources.schema_cache import invalidate

    existing = get_source(tenant_id, source_id)
    if not existing:
        raise _not_found(source_id)
    _require_sql(existing)
    cfg = existing.get("sql_config") or {}
    result = run_probe(cfg, tenant_id=tenant_id)
    execute(
        "UPDATE datasets SET connection_status=%s, "
        "sql_config = COALESCE(sql_config, '{}'::jsonb) || jsonb_build_object('last_test', %s::jsonb), "
        "updated_at=NOW() WHERE id=%s AND tenant_id=%s",
        ("connected" if result["ok"] else "error", _json(_probe_summary(result)), source_id, tenant_id),
    )
    invalidate(tenant_id, source_id)
    return result


def _connected_sql_source(tenant_id: str, source_id: str) -> dict:
    src = get_source(tenant_id, source_id)
    if not src:
        raise _not_found(source_id)
    _require_sql(src)
    if src.get("connection_status") != "connected":
        raise AppError(
            "data_source_not_connected",
            "This data source is not connected. Test the connection first.",
            status_code=_REJECTED,
        )
    return src


def execute_sql_query(tenant_id: str, source_id: str, sql: str, limit: int = 500,
                      offset: int = 0) -> dict:
    """One page of a query's result for the editor.

    Streams past `offset` rows and reads `limit + 1`, so `has_more` is exact
    (the old `truncated = len(rows) >= limit` said "truncated" for a result of
    exactly `limit` rows). Values are converted for JSON (`values.json_cell`)."""
    from backend.datasources.values import json_cell

    src = get_source(tenant_id, source_id)
    if not src:
        raise _not_found(source_id)
    if src.get("connection_status") != "connected":
        raise AppError(
            "data_source_not_connected",
            "This data source is not connected. Test the connection first.",
            status_code=_REJECTED,
        )
    if offset < 0 or offset > MAX_QUERY_OFFSET:
        raise AppError("sql_offset_out_of_range",
                       f"The offset must be between 0 and {MAX_QUERY_OFFSET}.",
                       status_code=_REJECTED, params={"max_offset": MAX_QUERY_OFFSET})
    cfg = src.get("sql_config") or {}
    started = time.monotonic()
    try:
        with _read_only_rows(cfg, sql, stream=True, tenant_id=tenant_id) as result:
            columns = [str(c) for c in result.keys()]
            skipped = 0
            while skipped < offset:
                chunk = result.fetchmany(min(FETCH_BATCH, offset - skipped))
                if not chunk:
                    break
                skipped += len(chunk)
            rows = result.fetchmany(limit + 1) if skipped >= offset else []
    except Exception as exc:  # noqa: BLE001
        raise _classified(exc, cfg) from None
    has_more = len(rows) > limit
    rows = rows[:limit]
    data = [{columns[i]: json_cell(v) for i, v in enumerate(row)} for row in rows]
    return {
        "columns": columns,
        "rows": data,
        "row_count": len(data),
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "truncated": has_more,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _too_large(max_rows: int, what: str) -> AppError:
    return AppError(
        "sql_result_too_large",
        f"The query returned more than {max_rows} rows, the {what} limit. "
        "Narrow it with WHERE or LIMIT.",
        status_code=_REJECTED, params={"max_rows": max_rows},
    )


def materialize_sql_source(
    tenant_id: str,
    user_id: str,
    source_id: str,
    sql: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """Run a SQL source's query and snapshot the result as a NEW CSV dataset
    (parent_id = the SQL source). From that dataset on, the pipeline treats it
    exactly like an uploaded CSV — the wizard, training and freshness never
    need to know SQL was involved. A snapshot (not a live query) is deliberate:
    a forecast must be reproducible against the data it actually trained on.

    Streams the result in batches so the row cap bounds memory, and refuses —
    rather than silently truncates — when the result exceeds it. Values are
    written by `values.csv_cell` (exact decimals, ISO dates with offsets, hex
    bytes, JSON as JSON)."""
    src = get_source(tenant_id, source_id)
    if not src:
        raise _not_found(source_id)
    if src.get("source_type") != "sql":
        raise AppError(
            "data_source_not_sql",
            "Only SQL data sources can be materialized into a dataset.",
            status_code=_REJECTED,
        )
    if src.get("connection_status") != "connected":
        raise AppError(
            "data_source_not_connected",
            "This data source is not connected. Test the connection first.",
            status_code=_REJECTED,
        )
    query_sql = (sql or src.get("saved_query") or "").strip()
    if not query_sql:
        raise AppError(
            "sql_source_no_saved_query",
            "This SQL data source has no query to materialize yet.",
            status_code=_REJECTED,
        )

    import csv as _csv

    from backend.datasources.sql_guard import validate_read_only_sql
    from backend.datasources.values import csv_cell
    from backend.storage import paths
    from backend.utils.csv_safe import csv_safe

    cfg = src.get("sql_config") or {}
    # Refuse before a directory is created for a dataset that will not exist.
    validate_read_only_sql(query_sql, cfg.get("engine", "postgresql"))

    max_rows = settings.sql_materialize_max_rows
    new_id = generate_id("ds")
    dst_dir = paths.dataset_dir(tenant_id, new_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    file_path = dst_dir / "data.csv"
    tmp_path = dst_dir / "data.csv.tmp"

    def _discard() -> None:
        tmp_path.unlink(missing_ok=True)
        try:
            if dst_dir.exists() and not any(dst_dir.iterdir()):
                dst_dir.rmdir()
        except OSError:
            pass

    row_count = 0
    columns: list[str] = []
    try:
        with _read_only_rows(cfg, query_sql, stream=True, tenant_id=tenant_id, bulk=True) as result:
            columns = [str(c) for c in result.keys()]
            with open(tmp_path, "w", newline="", encoding="utf-8") as f:
                writer = _csv.writer(f)
                writer.writerow([csv_safe(c) for c in columns])
                while True:
                    batch = result.fetchmany(FETCH_BATCH)
                    if not batch:
                        break
                    row_count += len(batch)
                    if row_count > max_rows:
                        raise _too_large(max_rows, "materialization")
                    for r in batch:
                        writer.writerow([csv_cell(v) for v in r])
    except Exception as exc:  # noqa: BLE001
        _discard()
        raise _classified(exc, cfg) from None

    if row_count == 0:
        _discard()
        raise AppError(
            "sql_result_empty",
            "The query returned no rows — there is nothing to materialize.",
            status_code=_REJECTED,
        )

    size_bytes = tmp_path.stat().st_size
    try:
        # Same plan quota an uploaded file of this size would face.
        from backend.datasets.service import _enforce_dataset_size
        _enforce_dataset_size(tenant_id, size_bytes)
    except Exception:
        _discard()
        raise
    tmp_path.replace(file_path)

    # English fallback on purpose: this becomes the dataset's persisted NAME
    # and backend logic must not hardcode Spanish — the UI sends a localized
    # name (same convention as save_edited_as_new).
    display_name = name.strip() if (name and name.strip()) else f"{src.get('name')} (SQL)"

    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, description, original_filename, file_type, file_path,
            size_bytes, row_count, column_count, source_type, connection_status,
            parent_id, uploaded_by, uploaded_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,'csv',%s,%s,%s,%s,'file','connected',%s,%s,NOW(),NOW())""",
        (
            new_id, tenant_id, display_name, src.get("description"),
            f"{display_name}.csv", str(file_path),
            size_bytes, row_count, len(columns), source_id, user_id,
        ),
    )
    # Remember the query that produced the snapshot, so re-materializing after
    # fresh rows land in the customer's DB is one click, not a rewrite. The
    # row/column counts land on the SOURCE too: its metadata card would
    # otherwise show dashes forever, since a connection has no file to measure.
    execute(
        "UPDATE datasets SET saved_query=%s, row_count=%s, column_count=%s, "
        "updated_at=NOW() WHERE id=%s AND tenant_id=%s",
        (query_sql, row_count, len(columns), source_id, tenant_id),
    )
    _track_new_sales(tenant_id, new_id)
    return _public(get_source(tenant_id, new_id))


@dataclass
class ExportFile:
    """A finished export, spooled (memory up to 8 MB, then a temp file)."""
    file: Any
    rows: int
    media_type: str
    extension: str

    def chunks(self, size: int = 256 * 1024):
        try:
            self.file.seek(0)
            while True:
                block = self.file.read(size)
                if not block:
                    break
                yield block
        finally:
            self.file.close()


def export_sql_query(tenant_id: str, source_id: str, sql: Optional[str] = None,
                     fmt: str = "xlsx") -> ExportFile:
    """Run a SQL source's query and return the FULL result as a spooled
    .xlsx or .csv file. The whole result is read BEFORE the response starts,
    so a failure is still a coded JSON error, not a truncated download.

    Same streaming fetch and row ceiling as materialize (refuse, never
    truncate). Text cells go through the formula-injection guard — Excel
    executes a leading '=' even more eagerly than a re-imported CSV does."""
    import csv as _csv
    import io
    import tempfile

    from backend.datasources.values import csv_cell
    from backend.utils.csv_safe import csv_safe

    if fmt not in EXPORT_FORMATS:
        raise AppError("sql_export_format_unsupported", f"Unsupported export format {fmt!r}.",
                       status_code=_REJECTED, params={"format": fmt, "allowed": ", ".join(EXPORT_FORMATS)})
    src = get_source(tenant_id, source_id)
    if not src:
        raise _not_found(source_id)
    if src.get("source_type") != "sql":
        raise AppError(
            "data_source_not_sql",
            "Only SQL data sources can be exported this way.",
            status_code=_REJECTED,
        )
    if src.get("connection_status") != "connected":
        raise AppError(
            "data_source_not_connected",
            "This data source is not connected. Test the connection first.",
            status_code=_REJECTED,
        )
    query_sql = (sql or src.get("saved_query") or "").strip()
    if not query_sql:
        raise AppError(
            "sql_source_no_saved_query",
            "This SQL data source has no query to export yet.",
            status_code=_REJECTED,
        )

    cfg = src.get("sql_config") or {}
    max_rows = settings.sql_materialize_max_rows
    row_count = 0
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with _read_only_rows(cfg, query_sql, stream=True, tenant_id=tenant_id, bulk=True) as result:
            columns = [str(c) for c in result.keys()]
            if fmt == "csv":
                text = io.TextIOWrapper(spool, encoding="utf-8-sig", newline="", write_through=True)
                writer = _csv.writer(text)
                writer.writerow([csv_safe(c) for c in columns])
                append = writer.writerow
            else:
                from openpyxl import Workbook
                wb = Workbook(write_only=True)
                ws = wb.create_sheet(title="data")
                ws.append([csv_safe(c) for c in columns])
                append = ws.append
            while True:
                batch = result.fetchmany(FETCH_BATCH)
                if not batch:
                    break
                row_count += len(batch)
                if row_count > max_rows:
                    raise _too_large(max_rows, "export")
                for r in batch:
                    append([_xlsx_cell(v) if fmt == "xlsx" else csv_cell(v) for v in r])
        if fmt == "csv":
            text.flush()
            text.detach()
    except Exception as exc:  # noqa: BLE001
        spool.close()
        raise _classified(exc, cfg) from None

    if row_count == 0:
        spool.close()
        raise AppError(
            "sql_result_empty",
            "The query returned no rows — there is nothing to export.",
            status_code=_REJECTED,
        )
    if fmt == "xlsx":
        wb.save(spool)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        media = "text/csv; charset=utf-8"
    return ExportFile(spool, row_count, media, fmt)


def _xlsx_cell(value: Any) -> Any:
    """One value as an Excel cell. Dates stay dates and numbers stay numbers
    (so the sheet sorts and sums), except where Excel would silently change
    them: it holds 15 significant digits, so a longer integer or decimal is
    written as text; and it has no time zones, so an aware timestamp is
    written as ISO text with its offset rather than with the offset dropped.
    Everything else goes through the CSV rules (formula guard included)."""
    import datetime as _dt
    from decimal import Decimal

    from backend.datasources.values import csv_cell
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) >= 10 ** 15 else value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return csv_cell(value) if len(value.as_tuple().digits) > 15 else value
    if isinstance(value, _dt.datetime):
        return value.isoformat() if value.utcoffset() is not None else value
    if isinstance(value, (_dt.date, _dt.time)):
        return value
    return csv_cell(value)


def export_sql_query_xlsx(tenant_id: str, source_id: str, sql: Optional[str] = None) -> tuple[bytes, int]:
    """Back-compatible wrapper: the whole .xlsx as bytes, and its row count."""
    out = export_sql_query(tenant_id, source_id, sql=sql, fmt="xlsx")
    return b"".join(out.chunks()), out.rows


def save_sql_query(tenant_id: str, source_id: str, sql: str) -> dict:
    """Store the query a source's preview, analysis and scheduled refresh will
    run later — possibly for a viewer, or unattended. It is checked now, so a
    statement that would be refused at run time is never stored."""
    from backend.datasources.sql_guard import validate_read_only_sql
    src = get_source(tenant_id, source_id)
    if not src:
        raise _not_found(source_id)
    _require_sql(src)
    validate_read_only_sql(sql, (src.get("sql_config") or {}).get("engine", "postgresql"))
    execute(
        "UPDATE datasets SET saved_query=%s, updated_at=NOW() WHERE id=%s AND tenant_id=%s",
        (sql, source_id, tenant_id),
    )
    return _public(get_source(tenant_id, source_id))


# ── Schema browser ─────────────────────────────────────────────────────────────

def get_schema(tenant_id: str, source_id: str, *, refresh: bool = False) -> dict:
    """Tables and views the connection can read, with row estimates. Cached
    per source for a few minutes (`schema_cache`); `refresh` re-reads."""
    from backend.datasources import catalog
    from backend.datasources.client import open_session
    from backend.datasources.schema_cache import get_or_load

    src = _connected_sql_source(tenant_id, source_id)
    cfg = src.get("sql_config") or {}

    def _load() -> dict:
        try:
            with open_session(cfg, tenant_id=tenant_id) as session:
                return catalog.list_tables(session.conn, session.spec.engine)
        except Exception as exc:  # noqa: BLE001
            raise _classified(exc, cfg) from None

    data, cached_at = get_or_load(tenant_id, source_id, cfg, ("tables",), _load, refresh=refresh)
    return {**data, "cached_at": cached_at}


def get_table_columns(tenant_id: str, source_id: str, schema: str, table: str, *,
                      refresh: bool = False) -> dict:
    from backend.datasources import catalog
    from backend.datasources.client import open_session
    from backend.datasources.schema_cache import get_or_load

    src = _connected_sql_source(tenant_id, source_id)
    cfg = src.get("sql_config") or {}
    engine = cfg.get("engine", "postgresql")

    def _load() -> dict:
        try:
            with open_session(cfg, tenant_id=tenant_id) as session:
                return catalog.list_columns(session.conn, engine, schema, table)
        except Exception as exc:  # noqa: BLE001
            raise _classified(exc, cfg) from None

    data, cached_at = get_or_load(tenant_id, source_id, cfg, ("columns", schema, table), _load,
                                  refresh=refresh)
    return {**data, "cached_at": cached_at,
            "select_sql": catalog.select_preview_sql(engine, schema, table)}


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
        raise AppError(
            "data_source_file_missing",
            "The file is no longer on disk. Upload it again.",
            status_code=_REJECTED,
        )

    suffix = Path(file_path).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        _check_file_size(file_path)

    from backend.dataframes.io import dataset_preview
    preview = dataset_preview(file_path, rows, sheet=sheet)
    columns = preview["columns"]
    data_rows = preview["rows"]
    sheets = preview["sheets"]
    total_rows = preview["total_rows"]

    # Update stats if not set — the preview already read the full row count for
    # Excel/JSON/Parquet; CSV counts lines without loading the whole file.
    # Also backfill column_count when missing (older CSV uploads stored only a
    # row count, so the metadata card showed "COLUMNAS —" despite the preview
    # rendering all columns).
    if not src.get("row_count") or not src.get("column_count"):
        try:
            if total_rows is None:
                if src.get("row_count"):
                    total_rows = src["row_count"]
                else:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as _f:
                        total_rows = sum(1 for _ in _f) - 1
            execute(
                "UPDATE datasets SET row_count=%s, column_count=%s, updated_at=NOW() WHERE id=%s AND tenant_id=%s",
                (total_rows, len(columns), source_id, tenant_id),
            )
        except Exception as _e:
            log.warning("[preview] failed to update stats for source=%s: %s", source_id, _e)

    return {
        "columns": columns,
        "rows": data_rows,
        "row_count": len(data_rows),
        "sheets": sheets,
        "active_sheet": sheet or (sheets[0] if sheets else None),
        "truncated": len(data_rows) >= rows,
    }


# ── In-app editor ────────────────────────────────────────────────────────────

def _safe_cell(value):
    """CSV formula-injection guard for editor cells that preserves numbers.

    A genuine number — including negatives like ``-5`` and signed/scientific
    forms — is never a spreadsheet formula, so it must NOT be quote-prefixed:
    doing so (``-5`` -> ``'-5``) would turn the value into text on re-read and
    corrupt the column. Numeric values pass through unchanged; only non-numeric
    text goes through the formula guard (``csv_safe``)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):   # bool is an int subclass; harmless here
        return value
    text = str(value)
    try:
        float(text)
        return text                        # numeric string ("-5", "+3", "1e3")
    except ValueError:
        from backend.utils.csv_safe import csv_safe
        return csv_safe(value)


def _not_editable() -> AppError:
    """Both editor entry points (load + save-as-new) refuse a SQL source with
    the same code, so the UI shows one sentence for one rule."""
    return AppError(
        "data_source_not_editable",
        "Only file data sources can be edited online.",
        status_code=_REJECTED,
    )


def load_editable_table(tenant_id: str, source_id: str) -> dict:
    """Load a file dataset's full table for in-browser editing. Enforces the
    size guard from the STORED row_count/size_bytes before touching the file, so
    an over-threshold dataset is never read into memory. SQL sources rejected."""
    src = get_source(tenant_id, source_id)
    if not src:
        raise ValueError(f"Data source {source_id} not found")
    if src.get("source_type") == "sql":
        raise _not_editable()

    max_rows = settings.dataset_editor_max_rows
    max_bytes = settings.dataset_editor_max_mb * 1024 * 1024
    row_count = src.get("row_count")
    size_bytes = src.get("size_bytes")
    if row_count is not None and row_count > max_rows:
        raise AppError(
            "dataset_too_many_rows_to_edit",
            f"This dataset has {row_count} rows, over the {max_rows}-row online "
            "editing limit. Edit it offline and upload it again.",
            status_code=_REJECTED,
            params={"rows": row_count, "max_rows": max_rows},
        )
    if size_bytes is not None and size_bytes > max_bytes:
        raise AppError(
            "dataset_too_large_to_edit",
            f"This dataset is {_mb(size_bytes)} MB, over the "
            f"{settings.dataset_editor_max_mb} MB online editing limit. "
            "Edit it offline and upload it again.",
            status_code=_REJECTED,
            params={"size_mb": _mb(size_bytes), "max_mb": settings.dataset_editor_max_mb},
        )

    file_path = src.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise AppError(
            "data_source_file_missing",
            "The file is no longer on disk. Upload it again.",
            status_code=_REJECTED,
        )

    from backend.dataframes.io import read_table
    return read_table(file_path)


def save_edited_as_new(
    tenant_id: str,
    user_id: str,
    source_id: str,
    name: Optional[str],
    columns: list[str],
    rows: list[dict],
) -> dict:
    """Write the edited table as a NEW CSV dataset with `parent_id` = source id.
    The original dataset row and file are never touched. Every cell (and header)
    is passed through the CSV formula-injection guard before it is written."""
    src = get_source(tenant_id, source_id)
    if not src:
        raise ValueError(f"Data source {source_id} not found")
    if src.get("source_type") == "sql":
        raise _not_editable()
    if not columns or not isinstance(columns, list):
        raise ValueError("At least one column is required")

    from backend.utils.csv_safe import csv_safe
    from backend.dataframes.io import write_rows
    from backend.storage import paths

    safe_columns = [csv_safe(c) for c in columns]
    safe_rows = [
        {safe_columns[i]: _safe_cell(row.get(columns[i])) for i in range(len(columns))}
        for row in rows
    ]

    new_id = generate_id("ds")
    dst_dir = paths.dataset_dir(tenant_id, new_id)
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to .tmp, verify, then swap — a partial write never
    # leaves a corrupt dataset (mirrors replace_file_source).
    file_path = dst_dir / "data.csv"
    tmp_path = dst_dir / "data.csv.tmp"
    try:
        write_rows(str(tmp_path), safe_columns, safe_rows, fmt="csv")
        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            raise ValueError("File write verification failed.")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    tmp_path.replace(file_path)

    size_bytes = file_path.stat().st_size
    row_count = len(rows)
    col_count = len(columns)
    # English fallback on purpose: this string becomes the dataset's persisted
    # NAME, and a Spanish literal in backend logic breaks CLAUDE.md. The UI
    # always sends `name` (defaulted from i18n, so a Spanish user still gets
    # "… (editado)"); this only fires for a direct API caller.
    display_name = name.strip() if (name and name.strip()) else f"{src.get('name')} (edited)"

    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, description, original_filename, file_type, file_path,
            size_bytes, row_count, column_count, source_type, connection_status,
            parent_id, uploaded_by, uploaded_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,'csv',%s,%s,%s,%s,'file','connected',%s,%s,NOW(),NOW())""",
        (
            new_id, tenant_id, display_name, src.get("description"),
            f"{display_name}.csv", str(file_path),
            size_bytes, row_count, col_count, source_id, user_id,
        ),
    )
    return _public(get_source(tenant_id, new_id))


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


def sessions_using(tenant_id: str, source_id: str) -> list[dict]:
    """Every session that reads this dataset — archived ones included, since an
    archived session is kept precisely so it can be reviewed later, and a review
    needs the data it was trained on."""
    return query(
        "SELECT id, name, status, archived_at FROM sessions "
        "WHERE tenant_id = %s AND (dataset_id = %s OR backtest_source_dataset_id = %s) "
        "ORDER BY created_at DESC",
        (tenant_id, source_id, source_id),
    )


def schedules_fed_by(tenant_id: str, source_id: str) -> list[dict]:
    """Enabled schedules whose template trains on a snapshot of this SQL
    source — the ones that re-run its query on every due run."""
    return query(
        """SELECT j.id, s.name
             FROM scheduled_jobs j
             JOIN sessions s ON s.id = j.session_id AND s.tenant_id = j.tenant_id
             JOIN datasets d ON d.id = s.dataset_id AND d.tenant_id = s.tenant_id
            WHERE j.tenant_id = %s AND j.enabled AND d.parent_id = %s""",
        (tenant_id, source_id),
    ) or []


def delete_source(tenant_id: str, source_id: str) -> None:
    src = get_source(tenant_id, source_id)
    if not src:
        return
    # Refuse BEFORE touching anything. This used to unlink the file first and
    # only then let the database object, so a dataset a session depended on was
    # reported as "in use" with its file already gone from disk.
    users = sessions_using(tenant_id, source_id)
    if users:
        raise AppError(
            "data_source_in_use",
            "Cannot delete: this data source is still used by "
            f"{len(users)} session(s). Sessions are permanent and keep their data.",
            status_code=409,
            params={"count": len(users),
                    "sessions": ", ".join(u["name"] for u in users[:3])},
        )
    if src.get("source_type") == "sql":
        # A schedule that retrains from this connection re-runs its query every
        # night. With the connection gone, it would quietly fall back to
        # retraining the last snapshot and report "no new data" forever.
        feeding = schedules_fed_by(tenant_id, source_id)
        if feeding:
            raise AppError(
                "data_source_feeds_schedule",
                f"Cannot delete: {len(feeding)} scheduled retraining(s) refresh "
                "their data from this connection. Turn them off first.",
                status_code=409,
                params={"count": len(feeding),
                        "sessions": ", ".join(f["name"] for f in feeding[:3])},
            )
    # Database first, file second: if the row cannot go, the file is untouched.
    # The datasets materialized FROM a SQL source are separate rows (parent_id
    # points here, with no cascade): they stay, like everything a session may
    # have trained on.
    execute("DELETE FROM datasets WHERE id=%s AND tenant_id=%s", (source_id, tenant_id))
    if src.get("source_type") == "sql":
        from backend.datasources.schema_cache import invalidate
        invalidate(tenant_id, source_id)
    if src.get("file_path"):
        p = Path(src["file_path"])
        if p.exists():
            p.unlink(missing_ok=True)
        if p.parent.exists() and not any(p.parent.iterdir()):
            p.parent.rmdir()


def rename_source(tenant_id: str, source_id: str, name: str, description: Optional[str] = None) -> dict:
    execute(
        "UPDATE datasets SET name=%s, description=%s, updated_at=NOW() WHERE id=%s AND tenant_id=%s",
        (name, description, source_id, tenant_id),
    )
    return _public(get_source(tenant_id, source_id))


# ── Analysis helpers ───────────────────────────────────────────────────────────

def load_dataframe(tenant_id: str, source_id: str, sheet: Optional[str] = None, max_rows: int = 50_000):
    """Load DataFrame for analysis, capped at max_rows to bound memory usage."""
    from backend.dataframes.io import read_dataframe, dataframe_from_records

    src = get_source(tenant_id, source_id)
    if not src:
        raise ValueError(f"Data source {source_id} not found")

    if src.get("source_type") == "sql":
        saved_q = src.get("saved_query")
        if not saved_q:
            raise AppError(
                "sql_source_no_saved_query",
                "This SQL data source has no saved query to analyze yet.",
                status_code=_REJECTED,
            )
        cfg = src.get("sql_config") or {}
        from decimal import Decimal
        try:
            with _read_only_rows(cfg, saved_q, stream=True, tenant_id=tenant_id, bulk=True) as result:
                cols = [str(c) for c in result.keys()]
                rows = result.fetchmany(max_rows)
        except Exception as exc:  # noqa: BLE001
            raise _classified(exc, cfg) from None
        # The analysis is a read-only view: a NUMERIC column must be a number
        # to it (Decimal would make the column text and hide it from every
        # numeric check). The persisted snapshot keeps exact decimals.
        rows = [tuple(float(v) if isinstance(v, Decimal) and v.is_finite() else v for v in r)
                for r in rows]
        return dataframe_from_records(rows, cols)

    file_path = src.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise AppError(
            "data_source_file_missing",
            "The file is no longer on disk. Upload it again.",
            status_code=_REJECTED,
        )

    suffix = Path(file_path).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        _check_file_size(file_path)
        return read_dataframe(file_path, sheet=sheet, nrows=max_rows)
    elif suffix == ".json":
        return read_dataframe(file_path, nrows=max_rows)
    else:
        return read_dataframe(file_path, nrows=max_rows)


def detect_columns(df) -> dict:
    """Heuristic auto-detection of date, target, and group columns."""
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
            if str(df[c].dtype).startswith("datetime"):
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
