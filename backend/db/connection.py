"""
psycopg2 connection pool — the only place that touches the database.

All reads return dicts (RealDictCursor).
All writes auto-commit on success, rollback on exception.
"""

import math
from contextlib import contextmanager
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def init_pool(database_url: str, min_conn: int = 1, max_conn: int = 10) -> None:
    global _pool
    _pool = psycopg2.pool.ThreadedConnectionPool(
        min_conn,
        max_conn,
        database_url,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


@contextmanager
def get_conn():
    if _pool is None:
        raise RuntimeError("DB pool not initialized — check DATABASE_URL in .env")
    conn = _pool.getconn()
    broken = False
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        _pool.putconn(conn, close=broken)


def _sanitize(obj: Any) -> Any:
    """Recursively replace NaN/Inf floats with None so PostgreSQL JSON accepts them."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _json(val: Any) -> psycopg2.extras.Json:
    return psycopg2.extras.Json(_sanitize(val))


def query(sql: str, params: tuple = ()) -> list[dict]:
    # Retry once on a dead connection — Supabase's pooler can silently drop a
    # pooled connection server-side; the pool only discovers this when the
    # connection is next used, so a single retry against a fresh connection
    # is needed instead of surfacing a transient error to the caller.
    for attempt in range(2):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    if cur.description:
                        return [dict(row) for row in cur.fetchall()]
                    return []
        except psycopg2.OperationalError:
            if attempt == 1:
                raise


def query_one(sql: str, params: tuple = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> None:
    for attempt in range(2):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            return
        except psycopg2.OperationalError:
            if attempt == 1:
                raise
