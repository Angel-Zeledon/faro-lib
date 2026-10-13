"""`Idempotency-Key` for the mutating inventory-sync routes.

A CI job that retries a POST after a timeout must not create a second profile
or start a second run. With the header, the first call's response is stored
(per tenant, per key, for a day) and a retry of the SAME request answers it
again; the same key on a DIFFERENT request is refused
(409 `inventory_sync_idempotency_key_reused`), and a retry that arrives while
the first is still running is told so (409 `inventory_sync_idempotency_in_progress`).
Without the header nothing changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Optional

from backend.db.connection import _json, execute, query_one
from backend.errors import AppError

_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def fingerprint(route: str, body: Any) -> str:
    return hashlib.sha256(json.dumps({"route": route, "body": body}, sort_keys=True,
                                     default=str).encode()).hexdigest()


def run(tenant_id: str, key: Optional[str], route: str, body: Any, fn: Callable[[], Any]) -> tuple[Any, bool]:
    """(result, replayed)."""
    if not key:
        return fn(), False
    if not _KEY.match(key):
        raise AppError("inventory_sync_idempotency_key_invalid",
                       "Idempotency-Key: 1-128 characters of A-Z a-z 0-9 . _ : -", status_code=422)
    fp = fingerprint(route, body)
    got = query_one(
        "INSERT INTO inventory_sync_idempotency (tenant_id, idem_key, route, fingerprint, status_code, response) "
        "VALUES (%s, %s, %s, %s, 0, '{}'::jsonb) ON CONFLICT (tenant_id, idem_key) DO NOTHING RETURNING idem_key",
        (tenant_id, key, route, fp))
    if not got:
        prev = query_one("SELECT route, fingerprint, status_code, response FROM inventory_sync_idempotency "
                         "WHERE tenant_id = %s AND idem_key = %s", (tenant_id, key))
        if not prev or prev["fingerprint"] != fp:
            raise AppError("inventory_sync_idempotency_key_reused",
                           "This Idempotency-Key was used for a different request.", status_code=409)
        if prev["status_code"] == 0:
            raise AppError("inventory_sync_idempotency_in_progress",
                           "The first request with this Idempotency-Key is still running.", status_code=409)
        return prev["response"].get("result"), True
    try:
        result = fn()
    except BaseException:
        execute("DELETE FROM inventory_sync_idempotency WHERE tenant_id = %s AND idem_key = %s",
                (tenant_id, key))
        raise
    execute("UPDATE inventory_sync_idempotency SET status_code = 200, response = %s "
            "WHERE tenant_id = %s AND idem_key = %s", (_json({"result": result}), tenant_id, key))
    return result, False
