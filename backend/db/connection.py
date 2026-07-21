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


@contextmanager
def transaction():
    """
    Explicit multi-statement transaction for callers that need several writes
    to commit (or roll back) as one unit — e.g. reception_service.receive_po,
    which otherwise updates inventory_po_items, inventory_stock, inventory_snapshots,
    inventory_po_log and supplier_lead_time_obs as 5+ independent auto-committing
    calls, so a failure mid-sequence leaves genuinely partial state behind.

    Usage:

        with transaction() as conn:
            execute(sql1, params1, conn=conn)
            execute(sql2, params2, conn=conn)

    Every write inside the block MUST pass this `conn` through to
    execute()/query()/query_one() via their optional `conn` kwarg — passing
    `conn` tells those helpers to run on THIS connection and not commit
    themselves; the block below commits once at the end, or rolls back
    everything if any statement raises. Callers that omit `conn` on some
    calls inside the block would silently return to today's non-atomic
    behavior for those calls, so every write in the sequence needs it.

    Implemented on top of `get_conn` so it shares the exact same
    commit/rollback/dead-connection handling as every other pooled connection
    in this module.
    """
    with get_conn() as conn:
        yield conn


def query(sql: str, params: tuple = (), conn: Optional[Any] = None) -> list[dict]:
    # `conn` provided: caller is inside a transaction() block — run on THAT
    # connection and let the block's own commit/rollback own the outcome.
    # This must be a plain, no-retry path: retrying on this shared connection
    # after a failed statement would run against a connection whose
    # transaction is already in an aborted state.
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description:
                return [dict(row) for row in cur.fetchall()]
            return []

    # Retry once on a dead connection — Supabase's pooler can silently drop a
    # pooled connection server-side; the pool only discovers this when the
    # connection is next used, so a single retry against a fresh connection
    # is needed instead of surfacing a transient error to the caller.
    for attempt in range(2):
        try:
            with get_conn() as pooled_conn:
                with pooled_conn.cursor() as cur:
                    cur.execute(sql, params)
                    if cur.description:
                        return [dict(row) for row in cur.fetchall()]
                    return []
        except psycopg2.OperationalError:
            if attempt == 1:
                raise


def query_one(sql: str, params: tuple = (), conn: Optional[Any] = None) -> Optional[dict]:
    rows = query(sql, params, conn=conn)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = (), conn: Optional[Any] = None) -> None:
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        return

    for attempt in range(2):
        try:
            with get_conn() as pooled_conn:
                with pooled_conn.cursor() as cur:
                    cur.execute(sql, params)
            return
        except psycopg2.OperationalError:
            if attempt == 1:
                raise
