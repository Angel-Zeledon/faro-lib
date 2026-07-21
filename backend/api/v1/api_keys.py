import hashlib
import secrets
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from backend.auth.guards import CurrentUser, get_current_user
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

    @field_validator("name")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@router.post("")
def create_api_key(body: CreateKeyRequest, user: CurrentUser = Depends(get_current_user)):
    raw = "sk_live_" + secrets.token_urlsafe(32)
    execute(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash)
           VALUES (gen_random_uuid()::text, %s, %s, %s)""",
        (user.tenant_id, body.name, _hash_key(raw)),
    )
    log.info("[api-keys] created key=%s tenant=%s", body.name, user.tenant_id)
    # Return raw key only once — never stored in plain text
    return ok({"key": raw, "name": body.name})


@router.get("")
def list_api_keys(user: CurrentUser = Depends(get_current_user)):
    rows = query(
        """SELECT id, name, last_used, created_at
           FROM api_keys WHERE tenant_id = %s ORDER BY created_at DESC""",
        (user.tenant_id,),
    )
    return ok([dict(r) for r in rows])


@router.delete("/{key_id}")
def revoke_api_key(key_id: str, user: CurrentUser = Depends(get_current_user)):
    row = query_one(
        "SELECT id FROM api_keys WHERE id = %s AND tenant_id = %s",
        (key_id, user.tenant_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
    return ok({"revoked": key_id})
