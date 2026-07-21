"""CRUD for `integration_connections` — the only place credentials are
encrypted/decrypted at rest.

Every read-facing function (`list_connections`, `get_connection`) MUST NOT
return the `credentials` column. Only `get_credentials` decrypts it, and it
is for internal use (the sync worker) — never wire it to an API response.
"""
from typing import Optional

from backend.db.connection import execute, query, query_one
from backend.integrations.crypto import decrypt_credentials, encrypt_credentials
from backend.utils.ids import generate_id

# Columns safe to return to callers — credentials is deliberately excluded.
_SAFE_COLUMNS = "id, provider, status, last_sync_at, last_error, created_at"
# get_connection additionally exposes tenant_id: the sync worker (which looks
# connections up by id alone) needs it to scope its own tenant-data writes.
_SAFE_COLUMNS_WITH_TENANT = f"tenant_id, {_SAFE_COLUMNS}"


def create_connection(tenant_id: str, provider: str, credentials: dict) -> dict:
    """Create or reconnect a tenant's connection to `provider`.

    Encrypts `credentials` before storing. Upserts on (tenant_id, provider):
    reconnecting with new credentials replaces the old ones and clears any
    prior error state. Returns the row WITHOUT credentials.
    """
    encrypted = encrypt_credentials(credentials)
    row = query_one(
        f"""
        INSERT INTO integration_connections (id, tenant_id, provider, credentials, status)
        VALUES (%s, %s, %s, %s, 'connected')
        ON CONFLICT (tenant_id, provider) DO UPDATE
            SET credentials = EXCLUDED.credentials,
                status = 'connected',
                last_error = NULL
        RETURNING {_SAFE_COLUMNS}
        """,
        (generate_id("intg"), tenant_id, provider, encrypted),
    )
    return row


def list_connections(tenant_id: str) -> list[dict]:
    """All connections for a tenant, without credentials."""
    return query(
        f"SELECT {_SAFE_COLUMNS} FROM integration_connections WHERE tenant_id = %s ORDER BY created_at",
        (tenant_id,),
    )


def get_connection(connection_id: str) -> Optional[dict]:
    """A single connection by id, without credentials.

    `id` is a globally unique PK, so no tenant scoping is required here —
    callers that need tenant-scoped access control (e.g. deletion) check it
    themselves.
    """
    return query_one(
        f"SELECT {_SAFE_COLUMNS_WITH_TENANT} FROM integration_connections WHERE id = %s",
        (connection_id,),
    )


def get_credentials(connection_id: str) -> dict:
    """Decrypt and return the credential dict for a connection.

    Internal-only: never expose this through an API endpoint. Used by the
    sync worker to authenticate against the provider.
    """
    row = query_one(
        "SELECT credentials FROM integration_connections WHERE id = %s",
        (connection_id,),
    )
    if row is None:
        raise ValueError(f"No integration connection with id={connection_id!r}")
    return decrypt_credentials(row["credentials"])


def delete_connection(tenant_id: str, connection_id: str) -> bool:
    """Delete a connection, scoped to `tenant_id`. Returns True if a row was deleted."""
    rows = query(
        "DELETE FROM integration_connections WHERE id = %s AND tenant_id = %s RETURNING id",
        (connection_id, tenant_id),
    )
    return len(rows) > 0


def mark_synced(connection_id: str, error: Optional[str] = None) -> None:
    """Record the outcome of a sync attempt.

    On failure (`error` given): status='error', last_error=`error`.
    On success: status='connected', last_error cleared.
    Either way, last_sync_at is stamped with the current time.
    """
    if error is not None:
        execute(
            """UPDATE integration_connections
               SET last_sync_at = NOW(), status = 'error', last_error = %s
               WHERE id = %s""",
            (error, connection_id),
        )
    else:
        execute(
            """UPDATE integration_connections
               SET last_sync_at = NOW(), status = 'connected', last_error = NULL
               WHERE id = %s""",
            (connection_id,),
        )
