"""
psycopg2 connection pool — the only place that touches the database.

All reads return dicts (RealDictCursor).
All writes auto-commit on success, rollback on exception.
"""

import math
import time
from contextlib import contextmanager
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None

# psycopg2's ThreadedConnectionPool does not queue: the moment every connection
# is checked out it raises PoolError("connection pool exhausted") instead of
# waiting for one to come back. With max_conn=20 that turns any burst of >20
# concurrent writes into failed writes, even though each of them only needs the
# connection for a millisecond. Wait (bounded) for a free connection instead;
# past the deadline the PoolError still surfaces, so real exhaustion is never
# silently converted into an unbounded hang.
_POOL_WAIT_SECONDS = 10.0
_POOL_WAIT_POLL_SECONDS = 0.005


def init_pool(database_url: str, min_conn: int = 1, max_conn: int = 10) -> None:
    global _pool
    _pool = psycopg2.pool.ThreadedConnectionPool(
        min_conn,
        max_conn,
        database_url,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def pool_is_initialized() -> bool:
    """Whether this PROCESS has opened its pool.

    The pool is per-process state, not per-machine: a second process (the
    standalone worker) reaches the same database only after calling `init_pool`
    itself. Exposed so that caller can tell "not connected yet" apart from "the
    database is down" instead of reading the private global.
    """
    return _pool is not None


class _NotSent(Exception):
    """Internal marker: a failure that provably happened *before* the statement
    could reach the server, so re-running it cannot apply anything twice.

    Only failures carrying this marker are retried. Everything else — above all
    a failure of the COMMIT itself — leaves the outcome unknown and must be
    surfaced to the caller instead of being retried.
    """

    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.cause = cause


def _acquire():
    """Check out a pooled connection, waiting briefly if the pool is exhausted."""
    if _pool is None:
        raise RuntimeError("DB pool not initialized — check DATABASE_URL in .env")
    deadline = time.monotonic() + _POOL_WAIT_SECONDS
    while True:
        try:
            return _pool.getconn()
        except psycopg2.pool.PoolError as exc:
            if "exhausted" not in str(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(_POOL_WAIT_POLL_SECONDS)


@contextmanager
def get_conn():
    conn = _acquire()
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


def _run_pooled(sql: str, params: tuple, fetch_rows: bool) -> list[dict]:
    """One attempt at `sql` on its own pooled connection.

    Classifies every failure so the caller knows whether a retry is safe:
    failures raised while checking out/opening the connection, and failures
    raised while the statement itself was in flight, provably left the database
    untouched (no COMMIT was ever sent, so the implicit transaction is gone) —
    those are wrapped in `_NotSent`. A failure of the COMMIT is deliberately
    NOT wrapped: the server may have applied the statement and only the
    acknowledgement got lost, so re-running it would apply it twice.
    """
    rows: list[dict] = []
    acquired = False
    try:
        with get_conn() as pooled_conn:
            acquired = True
            try:
                with pooled_conn.cursor() as cur:
                    cur.execute(sql, params)
                    if fetch_rows and cur.description:
                        rows = [dict(row) for row in cur.fetchall()]
            except psycopg2.OperationalError as exc:
                raise _NotSent(exc) from exc
    except psycopg2.OperationalError as exc:
        if acquired:
            # Raised by the COMMIT — outcome unknown, never retry.
            raise
        raise _NotSent(exc) from exc
    return rows


def _run_with_retry(sql: str, params: tuple, fetch_rows: bool) -> list[dict]:
    """Run `sql`, retrying once — and only — when the first attempt provably
    never reached the server.

    The retry exists because a pooled connection can be dropped server-side
    (Supabase's pooler does this silently); the pool only discovers it when the
    connection is next used, and surfacing that as an error to the caller would
    be a false failure. It must stay narrow: retrying a statement that may
    already have committed silently duplicates every INSERT and re-applies
    every non-idempotent UPDATE under load.
    """
    try:
        return _run_pooled(sql, params, fetch_rows)
    except _NotSent as exc:
        first = exc.cause
    try:
        return _run_pooled(sql, params, fetch_rows)
    except _NotSent as exc:
        raise exc.cause from first


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

    return _run_with_retry(sql, params, fetch_rows=True)


def query_one(sql: str, params: tuple = (), conn: Optional[Any] = None) -> Optional[dict]:
    rows = query(sql, params, conn=conn)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = (), conn: Optional[Any] = None) -> None:
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        return

    _run_with_retry(sql, params, fetch_rows=False)
