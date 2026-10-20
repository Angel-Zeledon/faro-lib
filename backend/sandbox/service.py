"""A sandbox to build an integration against, without touching real data.

A developer wiring StockAI into their systems should not have to test against the
company's real purchase orders. A sandbox is a throwaway tenant, on the same
`demo` tier as a trial (30 SKUs, one key, 200 calls a day, no outbound messages),
seeded with the bundled demo history, with an API key already issued.

Why it is safe, in order of how much it matters:

* **Isolation is the tenant, not a flag.** The sandbox key belongs to the sandbox
  tenant, and every query in this API is scoped to the caller's tenant, so a
  sandbox key cannot read or write a real tenant and a real key cannot read or
  write the sandbox. `tenants.sandbox_of` only records who asked for it.
* **It cannot reach anybody.** The account is created by the trial machinery: an
  `@stockai.demo` login that is never verified, which the email transport refuses.
* **It ends.** The `trial-reaper` loop erases it when `trial_ends_at` passes
  (SANDBOX_DAYS), like any trial; an administrator can erase it sooner.
* **It cannot pile up.** At most MAX_LIVE_PER_COMPANY live sandboxes per company,
  and it counts toward the installation's trial ceilings.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from backend.auth.api_key_auth import KEY_PREFIX, hash_key
from backend.db.connection import execute, query, query_one
from backend.entitlements.plans import DEMO
from backend.errors import AppError

log = logging.getLogger(__name__)

SANDBOX_DAYS = 7
MAX_LIVE_PER_COMPANY = 2
KEY_NAME = "sandbox"


def _public(row: dict) -> dict:
    ends = row.get("trial_ends_at")
    return {
        "id": row["id"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "expires_at": ends.isoformat() if ends else None,
    }


def list_sandboxes(parent_tenant_id: str) -> list[dict]:
    rows = query(
        """SELECT id, created_at, trial_ends_at FROM tenants
            WHERE sandbox_of = %s AND tier = %s AND trial_ends_at > NOW()
            ORDER BY created_at DESC""",
        (parent_tenant_id, DEMO))
    return [_public(r) for r in rows]


def _mint_key(tenant_id: str, user_id: str) -> str:
    """The sandbox's one key, write scope (role analyst). Same row shape as
    `POST /api-keys`; the key is returned once and stored only as its hash."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    execute(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4,
                                 expires_at, warehouse_scope)
           VALUES (gen_random_uuid()::text, %s, %s, %s, 'analyst', %s, %s,
                   NOW() + (%s || ' days')::INTERVAL, NULL)""",
        (tenant_id, KEY_NAME, hash_key(raw), user_id, raw[-4:], SANDBOX_DAYS))
    return raw


def _seed(tenant_id: str, user_id: str) -> bool:
    """The bundled demo history, its stock and one training run. A failure here
    leaves the sandbox usable (empty), and the answer says so."""
    from backend.api.v1.demo import demo_quickstart
    from backend.auth.guards import CurrentUser
    try:
        demo_quickstart(None, CurrentUser(user_id, tenant_id, "admin", True))
        return True
    except Exception:  # noqa: BLE001 - reported to the caller, not hidden
        log.exception("[sandbox] seeding tenant=%s failed", tenant_id)
        return False


def create_sandbox(parent_tenant_id: str) -> dict:
    from backend.trial import service as trial

    parent = query_one("SELECT tier FROM tenants WHERE id = %s", (parent_tenant_id,))
    if not parent or parent["tier"] == DEMO:
        # A trial (and a sandbox is one) cannot mint more throwaway tenants.
        raise AppError("sandbox_not_for_trial", "A trial account cannot create a sandbox",
                       status_code=403)
    if len(list_sandboxes(parent_tenant_id)) >= MAX_LIVE_PER_COMPANY:
        raise AppError(
            "sandbox_limit_reached",
            "This company already has the maximum number of live sandboxes",
            status_code=409, params={"max": MAX_LIVE_PER_COMPANY})
    account = trial.create_trial_account(
        hours=SANDBOX_DAYS * 24, tenant_name="Sandbox", include_ids=True)
    tenant_id, user_id = account["tenant_id"], account["user_id"]
    execute("UPDATE tenants SET sandbox_of = %s WHERE id = %s", (parent_tenant_id, tenant_id))
    key = _mint_key(tenant_id, user_id)
    seeded = _seed(tenant_id, user_id)
    log.info("[sandbox] created tenant=%s for company=%s seeded=%s",
             tenant_id, parent_tenant_id, seeded)
    return {
        "id": tenant_id,
        "api_key": key,                       # shown once
        "environment": "sandbox",
        "expires_at": account["expires_at"],
        "seeded": seeded,
        # The same account the screens use, so a developer can look at what their
        # calls did. It cannot receive mail (the domain does not exist).
        "login": {"email": account["email"], "password": account["password"]},
    }


def delete_sandbox(parent_tenant_id: str, sandbox_id: str) -> None:
    """Erase a sandbox early. Only a sandbox this company asked for, and only
    while it is still on the throwaway tier: never a real tenant."""
    from backend.tenants.data_export import delete_tenant
    row = query_one(
        "SELECT id FROM tenants WHERE id = %s AND sandbox_of = %s AND tier = %s",
        (sandbox_id, parent_tenant_id, DEMO))
    if not row:
        raise AppError("sandbox_not_found", "Sandbox not found", status_code=404)
    delete_tenant(sandbox_id)
