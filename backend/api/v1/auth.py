"""
Auth routes: signup, login, refresh, verify-email, forgot/reset password.
"""

import hashlib
import hmac
import logging
import random
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_security = HTTPBearer(auto_error=False)

from backend.auth.jwt_handler import (
    create_access_token, create_refresh_token, create_signed_token,
    decode_token, hash_token,
)
from backend.auth.password import validate_strength
from backend.config import settings
from backend.db.connection import execute, query_one
from backend.schemas.auth import (
    ForgotPasswordRequest, ForgotPasswordVerifyRequest,
    LoginRequest, RefreshRequest,
    ResetPasswordRequest, SignupRequest, VerifyEmailRequest,
)
from backend.schemas.common import ok
from backend.tenants.service import create_tenant
from backend.users import service as user_svc

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)

# ── In-memory rate limiter ─────────────────────────────────────────────────────
_rate_lock    = Lock()
_rate_buckets: dict[str, deque] = defaultdict(deque)

def _check_rate(key: str, max_attempts: int, window_secs: int) -> None:
    """Raise 429 if `key` has exceeded `max_attempts` in the last `window_secs`."""
    now = time.monotonic()
    with _rate_lock:
        dq = _rate_buckets[key]
        while dq and dq[0] < now - window_secs:
            dq.popleft()
        if len(dq) >= max_attempts:
            raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
        dq.append(now)


def _lookup_email(email: str) -> dict | None:
    """Find tenant_id + user_id for a global email (users.email is UNIQUE)."""
    return query_one(
        "SELECT id AS user_id, tenant_id FROM users WHERE email = %s",
        (email.lower().strip(),),
    )


def _hash_code(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def _issue_reset_otp(user_id: str, tenant_id: str) -> str:
    """Generate a 6-digit OTP for password reset (purpose='reset', expires 30 hours)."""
    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    execute(
        "DELETE FROM pw_change_codes WHERE user_id = %s AND purpose = 'reset' AND used = FALSE",
        (user_id,),
    )
    execute(
        """INSERT INTO pw_change_codes (id, user_id, tenant_id, code_hash, expires_at, purpose)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, 'reset')""",
        (user_id, tenant_id, _hash_code(code), datetime.utcnow() + timedelta(hours=30)),
    )
    return code


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest):
    valid, msg = validate_strength(body.password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    if _lookup_email(body.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    tenant = create_tenant(body.tenant_name)
    user = user_svc.create_user(
        tenant_id=tenant["id"],
        email=body.email,
        password=body.password,
        role="admin",
        full_name=body.full_name,
    )

    verify_token = create_signed_token(
        {"sub": user["id"], "tenant_id": tenant["id"], "purpose": "email_verify"},
        expires_minutes=60 * 24,
    )
    verify_url = f"{settings.frontend_url}/verify-email?token={verify_token}"
    from backend.notifications.email import send_verification_email
    email_sent = send_verification_email(body.email, body.full_name or "", verify_url)
    log.info("[signup] user=%s email_sent=%s", user["id"], email_sent)

    return ok({
        "user": user,
        "tenant": {"id": tenant["id"], "name": tenant["name"]},
        "email_sent": email_sent,
        "message": (
            "Account created. Check your email to verify."
            if email_sent else
            "Account created but verification email could not be sent. "
            "Contact your administrator."
        ),
    })


@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest):
    try:
        payload = decode_token(body.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if payload.get("purpose") != "email_verify":
        raise HTTPException(status_code=400, detail="Invalid token purpose")

    user_svc.mark_verified(payload["tenant_id"], payload["sub"])
    return ok({"message": "Email verified. You can now log in."})


@router.post("/login")
async def login(body: LoginRequest):
    _check_rate(f"login:{body.email.lower()}", max_attempts=5, window_secs=300)
    entry = _lookup_email(body.email)
    if not entry:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user = user_svc.verify_credentials(entry["tenant_id"], body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Please verify your email first")

    user_status = user.get("status", "active")
    if user_status != "active":
        if user_status == "pending_confirmation":
            raise HTTPException(status_code=403, detail="Account pending email verification. Check your inbox.")
        raise HTTPException(status_code=403, detail="Account is not active. Contact your administrator.")

    user_svc.update_last_login(entry["tenant_id"], user["id"])

    access_token = create_access_token(user["id"], user["tenant_id"], user["role"])
    raw_refresh, hashed_refresh = create_refresh_token()
    user_svc.add_refresh_token(user["tenant_id"], user["id"], hashed_refresh)

    return ok({
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "bearer",
        "expires_in": 15 * 60,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user.get("full_name"),
            "role": user["role"],
            "tenant_id": user["tenant_id"],
        },
    })


@router.post("/refresh")
async def refresh(body: RefreshRequest):
    token_hash = hash_token(body.refresh_token)
    user = user_svc.validate_refresh_token(token_hash)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    access_token = create_access_token(user["id"], user["tenant_id"], user["role"])
    return ok({
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 15 * 60,
    })


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    entry = _lookup_email(body.email)
    if entry:
        code = _issue_reset_otp(entry["user_id"], entry["tenant_id"])
        from backend.notifications.email import send_password_reset_otp
        send_password_reset_otp(body.email, code)
        log.info("[forgot-password] OTP issued for email=%s", body.email)

    return ok({"message": "If the email exists, a verification code has been sent."})


@router.post("/forgot-password/verify")
async def forgot_password_verify(body: ForgotPasswordVerifyRequest):
    _check_rate(f"otp:{body.email.lower()}", max_attempts=10, window_secs=600)
    entry = _lookup_email(body.email)
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    row = query_one(
        """SELECT id, code_hash FROM pw_change_codes
           WHERE user_id = %s AND purpose = 'reset' AND used = FALSE AND expires_at > NOW()
           ORDER BY created_at DESC LIMIT 1""",
        (entry["user_id"],),
    )
    if not row or not hmac.compare_digest(row["code_hash"], _hash_code(body.code.strip())):
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    execute("UPDATE pw_change_codes SET used = TRUE WHERE id = %s", (row["id"],))

    reset_token = create_signed_token(
        {"sub": entry["user_id"], "tenant_id": entry["tenant_id"], "purpose": "password_reset"},
        expires_minutes=10,
    )
    log.info("[forgot-password/verify] OTP verified for email=%s", body.email)
    return ok({"reset_token": reset_token})


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest):
    try:
        payload = decode_token(body.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if payload.get("purpose") != "password_reset":
        raise HTTPException(status_code=400, detail="Invalid token")

    valid, msg = validate_strength(body.new_password)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    user_svc.update_password(payload["tenant_id"], payload["sub"], body.new_password)
    return ok({"message": "Password updated. All sessions have been revoked."})


@router.post("/logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(_security)):
    if credentials:
        try:
            payload = decode_token(credentials.credentials)
            jti     = payload.get("jti")
            exp     = payload.get("exp")
            user_id = payload.get("sub")
            if jti and exp:
                from backend.auth.blocklist import revoke
                revoke(jti, datetime.utcfromtimestamp(exp))
            if user_id:
                user_svc.revoke_all_tokens(payload.get("tenant_id", ""), user_id)
        except ValueError:
            pass
    return ok({"message": "Logged out"})
