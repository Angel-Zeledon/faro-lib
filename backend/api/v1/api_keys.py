import secrets
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from backend.auth.api_key_auth import KEY_PREFIX, hash_key
from backend.auth.guards import (
    CurrentUser, get_current_user, require_analyst_or_above,
)
from backend.db.connection import execute, query, query_one
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.schemas.common import ok

router = APIRouter(
    prefix="/api-keys", tags=["api-keys"],
    dependencies=[Depends(require_feature(Feature.API_ACCESS))],
)
log = logging.getLogger(__name__)


class CreateKeyRequest(BaseModel):
    name: str
    # What the key may do. 'viewer' reads; 'analyst' also writes — the same two
    # roles a person can hold, enforced by the same guards, so a key can never
    # reach past what the product already knows how to refuse.
    role: str = "viewer"
    # Days until the key stops working. None = never, which is what an
    # unattended nightly sync usually wants.
    expires_in_days: int | None = None

    @field_validator("name")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in ("viewer", "analyst"):
            raise ValueError("role must be 'viewer' or 'analyst'")
        return v

    @field_validator("expires_in_days")
    @classmethod
    def _positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("expires_in_days must be at least 1")
        return v


@router.post("")
def create_api_key(body: CreateKeyRequest, user: CurrentUser = Depends(require_analyst_or_above)):
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    # No role check beyond the guard on this endpoint, deliberately: 'analyst'
    # is the strongest role a key can hold, and `require_analyst_or_above` has
    # already refused anyone weaker than that. A key can never outrank the
    # person who minted it because there is no rank above the one they hold.
    execute(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4, expires_at)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s, %s,
                   CASE WHEN %s IS NULL THEN NULL
                        ELSE NOW() + (%s || ' days')::INTERVAL END)""",
        (user.tenant_id, body.name, hash_key(raw), body.role, user.user_id, raw[-4:],
         body.expires_in_days, body.expires_in_days),
    )
    # The name and role are safe to log; the key itself never is, not even
    # truncated, and not even at DEBUG.
    log.info("[api-keys] created name=%s role=%s tenant=%s", body.name, body.role, user.tenant_id)
    # The raw key is returned exactly once. Nothing stores it — not this
    # process, not the database — so a customer who loses it mints a new one.
    return ok({"key": raw, "name": body.name, "role": body.role})


@router.get("")
def list_api_keys(user: CurrentUser = Depends(get_current_user)):
    rows = query(
        """SELECT id, name, role, last4, last_used, expires_at, created_at
           FROM api_keys WHERE tenant_id = %s ORDER BY created_at DESC""",
        (user.tenant_id,),
    )
    return ok([dict(r) for r in rows])


@router.delete("/{key_id}")
def revoke_api_key(key_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    row = query_one(
        "SELECT id FROM api_keys WHERE id = %s AND tenant_id = %s",
        (key_id, user.tenant_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
    return ok({"revoked": key_id})
