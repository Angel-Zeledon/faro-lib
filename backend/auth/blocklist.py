"""
JWT access-token blocklist backed by PostgreSQL.
"""

import logging
from datetime import datetime

from backend.db.connection import execute, query_one

log = logging.getLogger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti        TEXT        PRIMARY KEY,
    revoked_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
)
"""

_table_ready = False


def ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    execute(_INIT_SQL)
    _table_ready = True


def revoke(jti: str, expires_at: datetime) -> None:
    ensure_table()
    execute(
        "INSERT INTO revoked_tokens (jti, expires_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (jti, expires_at),
    )
    log.debug("Revoked token jti=%s", jti)


def is_revoked(jti: str) -> bool:
    ensure_table()
    row = query_one(
        "SELECT 1 FROM revoked_tokens WHERE jti = %s AND expires_at > NOW()",
        (jti,),
    )
    return row is not None


def purge_expired() -> int:
    """Delete expired entries — call periodically to keep the table small."""
    from backend.db.connection import execute as _exec
    _exec("DELETE FROM revoked_tokens WHERE expires_at <= NOW()")
    return 0
