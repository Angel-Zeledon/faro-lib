import hashlib
import hmac
import logging
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.v1.auth import _reject_weak_password
from backend.auth.guards import (
    CurrentUser, get_current_user, require_admin, require_verified_admin,
)
from backend.config import settings
from backend.db.connection import execute, query_one
from backend.errors import AppError
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


# Short-lived + attempt-capped: a 6-digit code only resists brute force if the
# window is minutes and a handful of wrong guesses burns it.
_CODE_EXPIRE_MINUTES = 15
_CODE_MAX_ATTEMPTS   = 5


def _issue_code(user_id: str, tenant_id: str, purpose: str = "change") -> str:
    """Generate a 6-digit OTP (change or reset), expires in 15 minutes."""
    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    execute(
        "DELETE FROM pw_change_codes WHERE user_id = %s AND purpose = %s AND used = FALSE",
        (user_id, purpose),
    )
    execute(
        """INSERT INTO pw_change_codes (id, user_id, tenant_id, code_hash, expires_at, purpose)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s)""",
        (user_id, tenant_id, _hash_code(code),
         datetime.now(timezone.utc) + timedelta(minutes=_CODE_EXPIRE_MINUTES), purpose),
    )
    return code


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    # E.164 with country code (e.g. +573001234567); empty string clears it
    whatsapp_number: Optional[str] = None


@router.get("/me")
def get_me(user: CurrentUser = Depends(get_current_user)):
    u = user_svc.get_user(user.tenant_id, user.user_id)
    if not u:
        raise AppError("user_not_found", "User not found", status_code=404)
    return ok(user_svc._public(u))


@router.patch("/me")
def update_me(body: ProfileUpdate, user: CurrentUser = Depends(get_current_user)):
    if body.full_name is not None and not body.full_name.strip():
        raise AppError(
            "full_name_required", "full_name cannot be blank", status_code=400,
        )
    if body.whatsapp_number:
        num = body.whatsapp_number.strip()
        if not re.fullmatch(r"\+[1-9]\d{7,14}", num):
            raise AppError(
                "whatsapp_number_invalid_format",
                "whatsapp_number must be E.164 format with country code, e.g. +573001234567",
                status_code=422,
            )
    if body.full_name is not None or body.whatsapp_number is not None:
        updated = user_svc.update_profile(
            user.tenant_id, user.user_id,
            full_name=body.full_name,
            whatsapp_number=body.whatsapp_number,
        )
        return ok(updated)
    u = user_svc.get_user(user.tenant_id, user.user_id)
    return ok(user_svc._public(u))


class WhatsAppLinkRequest(BaseModel):
    whatsapp_number: str


class WhatsAppConfirmRequest(BaseModel):
    code: str


# /whatsapp/link doubles as "resend": a fresh unused code younger than this
# blocks issuing a new one (429 + retry_after).
_WHATSAPP_RESEND_COOLDOWN_SECS = 60


@router.post("/me/whatsapp/link")
def link_whatsapp(body: WhatsAppLinkRequest, user: CurrentUser = Depends(get_current_user)):
    """Start linking a WhatsApp number: stores it unverified and issues a code."""
    from backend.notifications import whatsapp as wa_notify
    from backend.whatsapp import identity

    is_production = settings.environment.strip().lower() in ("production", "prod")
    twilio_configured = wa_notify.is_configured()

    # In production the code MUST reach the user's phone. Without a transport we
    # fail loudly up front (before touching any state) instead of pretending
    # the code was sent.
    if is_production and not twilio_configured:
        raise AppError(
            "whatsapp_delivery_unavailable",
            "WhatsApp delivery is not configured on this server",
            status_code=503,
        )

    # Resend cooldown. Bypassed in testing_mode (matches the auth rate-limit
    # convention); tests exercising it monkeypatch testing_mode = False.
    if not settings.testing_mode:
        row = query_one(
            """SELECT EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_secs
               FROM pw_change_codes
               WHERE user_id = %s AND purpose = 'whatsapp' AND used = FALSE
               ORDER BY created_at DESC LIMIT 1""",
            (user.user_id,),
        )
        if row and row["age_secs"] is not None and float(row["age_secs"]) < _WHATSAPP_RESEND_COOLDOWN_SECS:
            retry_after = max(1, int(_WHATSAPP_RESEND_COOLDOWN_SECS - float(row["age_secs"])))
            raise AppError(
                "whatsapp_code_resend_cooldown",
                f"A code was just sent. Retry in {retry_after}s",
                status_code=429,
                params={"retry_after": retry_after},
            )

    try:
        code = identity.start_verification(user.tenant_id, user.user_id, body.whatsapp_number)
    except ValueError as e:
        # Already-linked -> 409 with a stable code; bad format -> 422.
        if "already linked" in str(e):
            raise AppError("whatsapp_number_taken", str(e), status_code=409)
        raise HTTPException(status_code=422, detail=str(e))

    # Real delivery whenever Twilio is configured, regardless of environment.
    if twilio_configured:
        from backend.notifications.locale import render_es
        delivered = wa_notify.send_whatsapp(
            identity.normalize_phone(body.whatsapp_number),
            render_es("whatsapp_verification_code", code=code),
        )
        if not delivered:
            raise AppError(
                "whatsapp_delivery_failed",
                "WhatsApp delivery failed. Try again later.",
                status_code=503,
            )
        return ok({"sent": True})

    # No transport and not production: surface the code so the in-app
    # "type the code" flow (and tests) can complete without a live round-trip.
    return ok({"sent": False, "debug_code": code})


@router.post("/me/whatsapp/confirm")
def confirm_whatsapp(body: WhatsAppConfirmRequest, user: CurrentUser = Depends(get_current_user)):
    """Confirm the code and mark the number verified."""
    from backend.whatsapp import identity
    if not identity.confirm_verification(user.tenant_id, user.user_id, body.code.strip()):
        raise AppError(
            "whatsapp_code_invalid", "Invalid or expired code", status_code=400,
        )
    return ok({"verified": True})


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
    # Verified email required: this mails an account-setup link to a third
    # party in the tenant's name.
    user: CurrentUser = Depends(require_verified_admin),
):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Options: {sorted(VALID_ROLES)}")

    from backend.api.v1.auth import _lookup_email
    if _lookup_email(body.email):
        raise AppError(
            "email_already_registered", "Email already registered", status_code=409,
        )

    from backend.entitlements.service import enforce_limit
    enforce_limit(user.tenant_id, "max_users", user_svc.count_users(user.tenant_id))

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
    from backend.notifications import email as email_mod
    from backend.notifications.email import send_account_setup_email
    email_sent = send_account_setup_email(body.email, body.full_name or "", setup_url)
    if not email_sent:
        log.warning("[create-user] email delivery failed for user=%s — check SMTP config", new_user["id"])
    log.info("[create-user] admin=%s created user=%s email=%s email_sent=%s", user.user_id, new_user["id"], body.email, email_sent)

    # An invite that never left strands an account nobody can activate, so the
    # admin gets the reason as a stable code the UI localizes (CLAUDE.md).
    return ok({
        "user": new_user,
        "email_sent": email_sent,
        "email_error": None if email_sent else email_mod.failure_reason(),
    })


@router.post("/{user_id}/resend-verification", status_code=200)
def resend_verification_email(
    user_id: str,
    user: CurrentUser = Depends(require_verified_admin),
):
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise AppError("user_not_found", "User not found", status_code=404)
    if target.get("email_verified") and target.get("status") != "pending_confirmation":
        raise AppError(
            "user_already_verified", "User email is already verified", status_code=400,
        )

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
        raise AppError(
            "email_delivery_failed",
            "Email delivery failed. Check SMTP configuration.",
            status_code=503,
        )
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
        raise AppError("user_not_found", "User not found", status_code=404)

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
        verification_sent = send_account_setup_email(new_email, name, setup_url)
        log.info("[update-user] email changed for user=%s, verification sent=%s to %s",
                 user_id, verification_sent, new_email)
        # The account cannot be used again until the new address is verified,
        # so a failed send is part of the result, not a log line.
        return ok({**updated, "verification_email_sent": verification_sent})

    return ok(updated)


@router.delete("/{user_id}")
def delete_user_admin(
    user_id: str,
    user: CurrentUser = Depends(require_admin),
):
    if user_id == user.user_id:
        raise AppError(
            "cannot_delete_own_account", "Cannot delete your own account", status_code=400,
        )
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise AppError("user_not_found", "User not found", status_code=404)
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
        raise AppError(
            "cannot_change_own_status", "Cannot change your own status", status_code=400,
        )
    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Options: {sorted(VALID_STATUSES)}")
    target = user_svc.get_user(user.tenant_id, user_id)
    if not target:
        raise AppError("user_not_found", "User not found", status_code=404)
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
        raise AppError("user_not_found", "User not found", status_code=404)
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
        raise AppError("user_not_found", "User not found", status_code=404)
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
    _reject_weak_password(body.new_password)

    u = user_svc.get_user(user.tenant_id, user.user_id)
    if not u:
        raise AppError("user_not_found", "User not found", status_code=404)

    code = _issue_code(user.user_id, user.tenant_id, purpose="change")

    from backend.notifications import email as email_mod
    from backend.notifications.email import send_change_password_code
    sent = send_change_password_code(u["email"], code)
    log.info("[change-password] code issued for user=%s sent=%s", user.user_id, sent)

    # The confirm step is unreachable without this code, so "we sent it" when
    # nothing left leaves the user waiting on an email that will never arrive.
    return ok({
        "message": "Verification code sent to your email.",
        "email_sent": sent,
        "email_error": None if sent else email_mod.failure_reason(),
    })


@router.post("/me/change-password/confirm")
def confirm_password_change(
    body: ChangePasswordConfirm,
    user: CurrentUser = Depends(get_current_user),
):
    _reject_weak_password(body.new_password)

    row = query_one(
        """SELECT id, code_hash FROM pw_change_codes
           WHERE user_id = %s AND purpose = 'change' AND used = FALSE AND expires_at > NOW()
                 AND attempts < %s
           ORDER BY created_at DESC LIMIT 1""",
        (user.user_id, _CODE_MAX_ATTEMPTS),
    )
    if not row:
        raise AppError(
            "change_password_code_missing",
            "No active code found. Request a new one.",
            status_code=400,
        )

    if not hmac.compare_digest(row["code_hash"], _hash_code(body.code.strip())):
        execute(
            "UPDATE pw_change_codes SET attempts = attempts + 1 WHERE id = %s",
            (row["id"],),
        )
        raise AppError(
            "change_password_code_invalid", "Invalid verification code.", status_code=400,
        )

    execute("UPDATE pw_change_codes SET used = TRUE WHERE id = %s", (row["id"],))
    user_svc.update_password(user.tenant_id, user.user_id, body.new_password)
    log.info("[change-password] password updated for user=%s", user.user_id)

    return ok({"message": "Password updated successfully."})


# ── Legacy invite (kept for backwards compat) ─────────────────────────────

@router.post("/invite", status_code=201)
def invite_user(
    body: InviteUserRequest,
    user: CurrentUser = Depends(require_verified_admin),
):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Options: {sorted(VALID_ROLES)}")

    from backend.api.v1.auth import _lookup_email
    if _lookup_email(body.email):
        raise AppError(
            "email_already_registered", "Email already registered", status_code=409,
        )

    from backend.entitlements.service import enforce_limit
    enforce_limit(user.tenant_id, "max_users", user_svc.count_users(user.tenant_id))

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
    from backend.notifications import email as email_mod
    from backend.notifications.email import send_account_setup_email
    email_sent = send_account_setup_email(body.email, body.full_name or "", setup_url)

    return ok({
        "user": new_user,
        "email_sent": email_sent,
        "email_error": None if email_sent else email_mod.failure_reason(),
    })
