import hashlib
import hmac
import logging
import random
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth.guards import CurrentUser, get_current_user, require_admin
from backend.auth.password import validate_strength
from backend.config import settings
from backend.db.connection import execute, query_one
from backend.schemas.auth import (
    CreateUserRequest, UpdateUserRequest,
    UpdateUserStatusRequest, UpdatePermissionsRequest,
    InviteUserRequest,
)
from backend.schemas.common import ok
from backend.users import service as user_svc
from backend.users.roles import VALID_ROLES

router = APIRouter(prefix="/users", tags=["users"])
log = logging.getLogger(__name__)

VALID_STATUSES = {"active", "inactive", "suspended", "pending_confirmation"}


def _hash_code(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def _issue_code(user_id: str, tenant_id: str, purpose: str = "change") -> str:
    """Generate a 6-digit OTP (change or reset), expires 30 hours."""
    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    execute(
        "DELETE FROM pw_change_codes WHERE user_id = %s AND purpose = %s AND used = FALSE",
        (user_id, purpose),
    )
    execute(
        """INSERT INTO pw_change_codes (id, user_id, tenant_id, code_hash, expires_at, purpose)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s)""",
        (user_id, tenant_id, _hash_code(code), datetime.utcnow() + timedelta(hours=30), purpose),
    )
    return code


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None


@router.get("/me")
def get_me(user: CurrentUser = Depends(get_current_user)):
    u = user_svc.get_user(user.tenant_id, user.user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return ok(user_svc._public(u))


@router.patch("/me")
def update_me(body: ProfileUpdate, user: CurrentUser = Depends(get_current_user)):
    if body.full_name is not None:
        if not body.full_name.strip():
            raise HTTPException(status_code=400, detail="full_name cannot be blank")
        updated = user_svc.update_profile(user.tenant_id, user.user_id, body.full_name)
        return ok(updated)
    u = user_svc.get_user(user.tenant_id, user.user_id)
    return ok(user_svc._public(u))


@router.get("")
def list_users(
    user: CurrentUser = Depends(require_admin),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return ok(user_svc.list_users_admin(user.tenant_id, search, status, role, limit, offset))


@router.post("", status_code=201)
def create_user_admin(
    body: CreateUserRequest,
    user: CurrentUser = Depends(require_admin),
):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Options: {sorted(VALID_ROLES)}")

    from backend.api.v1.auth import _lookup_email
    if _lookup_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    temp_password = secrets.token_urlsafe(16)
    new_user = user_svc.create_user_admin(
        user.tenant_id, body.email, temp_password, body.role, body.full_name
    )

    from backend.auth.jwt_handler import create_signed_token
    verify_token = create_signed_token(
        {"sub": new_user["id"], "tenant_id": user.tenant_id, "purpose": "email_verify"},
        expires_minutes=60 * 30,
    )
    setup_url = f"{settings.frontend_url}/verify-email?token={verify_token}"
    from backend.notifications.email import send_account_setup_email
    email_sent = send_account_setup_email(body.email, body.full_name or "", setup_url)
    if not email_sent:
        log.warning("[create-user] email delivery failed for user=%s — check SMTP config", new_user["id"])
    log.info("[create-user] admin=%s created user=%s email=%s email_sent=%s", user.user_id, new_user["id"], body.email, email_sent)

    return ok({"user": new_user, "email_sent": email_sent})


@router.post("/{user_id}/resend-verification", status_code=200)
def resend_verification_email(
    user_id: str,
    user: CurrentUser = Depends(require_admin),
):
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("email_verified") and target.get("status") != "pending_confirmation":
        raise HTTPException(status_code=400, detail="User email is already verified")

    from backend.auth.jwt_handler import create_signed_token
    verify_token = create_signed_token(
        {"sub": user_id, "tenant_id": user.tenant_id, "purpose": "email_verify"},
        expires_minutes=60 * 30,
    )
    setup_url = f"{settings.frontend_url}/verify-email?token={verify_token}"
    from backend.notifications.email import send_account_setup_email
    name = (target.get("full_name") or "").strip()
    sent = send_account_setup_email(target["email"], name, setup_url)
    if not sent:
        log.warning("[resend-verification] email delivery failed for user=%s — check SMTP config", user_id)
        raise HTTPException(status_code=503, detail="Email delivery failed. Check SMTP configuration.")
    log.info("[resend-verification] sent to user=%s by admin=%s", user_id, user.user_id)
    return ok({"message": "Verification email sent.", "email": target["email"]})


@router.patch("/{user_id}")
def update_user_admin(
    user_id: str,
    body: UpdateUserRequest,
    user: CurrentUser = Depends(require_admin),
):
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if body.role is not None and body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Options: {sorted(VALID_ROLES)}")

    email_changed = (
        body.email is not None
        and body.email.lower().strip() != target["email"]
    )

    updated = user_svc.update_user_admin(
        user.tenant_id, user_id,
        full_name=body.full_name,
        role=body.role,
        email=str(body.email) if body.email else None,
    )

    if email_changed:
        from backend.auth.jwt_handler import create_signed_token
        verify_token = create_signed_token(
            {"sub": user_id, "tenant_id": user.tenant_id, "purpose": "email_verify"},
            expires_minutes=60 * 30,
        )
        setup_url = f"{settings.frontend_url}/verify-email?token={verify_token}"
        from backend.notifications.email import send_account_setup_email
        new_email = str(body.email)
        name = (body.full_name or target.get("full_name") or "").strip()
        send_account_setup_email(new_email, name, setup_url)
        log.info("[update-user] email changed for user=%s, verification sent to %s", user_id, new_email)

    return ok(updated)


@router.delete("/{user_id}")
def delete_user_admin(
    user_id: str,
    user: CurrentUser = Depends(require_admin),
):
    if user_id == user.user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    user_svc.delete_user(user.tenant_id, user_id)
    log.info("[delete-user] admin=%s deleted user=%s", user.user_id, user_id)
    return ok({"deleted": user_id})


@router.patch("/{user_id}/status")
def set_user_status(
    user_id: str,
    body: UpdateUserStatusRequest,
    user: CurrentUser = Depends(require_admin),
):
    if user_id == user.user_id:
        raise HTTPException(status_code=400, detail="Cannot change your own status")
    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Options: {sorted(VALID_STATUSES)}")
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    user_svc.update_status(user.tenant_id, user_id, body.status)
    updated = user_svc.get_user(user.tenant_id, user_id)
    return ok(user_svc._public(updated))


@router.get("/{user_id}/permissions")
def get_user_permissions(
    user_id: str,
    user: CurrentUser = Depends(require_admin),
):
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return ok({
        "user_id": user_id,
        "permissions": user_svc.get_permissions(user.tenant_id, user_id),
        "all_permissions": user_svc.ALL_PERMISSIONS,
    })


@router.patch("/{user_id}/permissions")
def set_user_permissions(
    user_id: str,
    body: UpdatePermissionsRequest,
    user: CurrentUser = Depends(require_admin),
):
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    user_svc.set_permissions(user.tenant_id, user_id, body.permissions)
    return ok({
        "user_id": user_id,
        "permissions": user_svc.get_permissions(user.tenant_id, user_id),
    })


# ── Change password (authenticated, OTP via email) ────────────────────────

class ChangePasswordRequest(BaseModel):
    new_password: str


class ChangePasswordConfirm(BaseModel):
    code: str
    new_password: str


@router.post("/me/change-password/request")
def request_password_change(
    body: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
):
    valid, msg = validate_strength(body.new_password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    u = user_svc.get_user(user.tenant_id, user.user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    code = _issue_code(user.user_id, user.tenant_id, purpose="change")

    from backend.notifications.email import send_change_password_code
    send_change_password_code(u["email"], code)
    log.info("[change-password] code issued for user=%s", user.user_id)

    return ok({"message": "Verification code sent to your email."})


@router.post("/me/change-password/confirm")
def confirm_password_change(
    body: ChangePasswordConfirm,
    user: CurrentUser = Depends(get_current_user),
):
    valid, msg = validate_strength(body.new_password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    row = query_one(
        """SELECT id, code_hash FROM pw_change_codes
           WHERE user_id = %s AND purpose = 'change' AND used = FALSE AND expires_at > NOW()
           ORDER BY created_at DESC LIMIT 1""",
        (user.user_id,),
    )
    if not row:
        raise HTTPException(status_code=400, detail="No active code found. Request a new one.")

    if not hmac.compare_digest(row["code_hash"], _hash_code(body.code.strip())):
        raise HTTPException(status_code=400, detail="Invalid verification code.")

    execute("UPDATE pw_change_codes SET used = TRUE WHERE id = %s", (row["id"],))
    user_svc.update_password(user.tenant_id, user.user_id, body.new_password)
    log.info("[change-password] password updated for user=%s", user.user_id)

    return ok({"message": "Password updated successfully."})


# ── Legacy invite (kept for backwards compat) ─────────────────────────────

@router.post("/invite", status_code=201)
def invite_user(
    body: InviteUserRequest,
    user: CurrentUser = Depends(require_admin),
):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Options: {sorted(VALID_ROLES)}")

    from backend.api.v1.auth import _lookup_email
    if _lookup_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    temp_password = secrets.token_urlsafe(16)
    new_user = user_svc.create_user_admin(
        user.tenant_id, body.email, temp_password, body.role, body.full_name
    )

    from backend.auth.jwt_handler import create_signed_token
    verify_token = create_signed_token(
        {"sub": new_user["id"], "tenant_id": user.tenant_id, "purpose": "email_verify"},
        expires_minutes=60 * 30,
    )
    setup_url = f"{settings.frontend_url}/verify-email?token={verify_token}"
    from backend.notifications.email import send_account_setup_email
    send_account_setup_email(body.email, body.full_name or "", setup_url)

    return ok({"user": new_user})
