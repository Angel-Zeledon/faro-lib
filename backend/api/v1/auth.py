"""
Auth routes: signup, login, refresh, verify-email, forgot/reset password.
"""

import hashlib
import hmac
import logging
import random
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_security = HTTPBearer(auto_error=False)

from backend.auth.jwt_handler import (
    create_access_token, create_refresh_token, create_signed_token,
    decode_token, hash_token,
)
from backend.auth.password import validate_strength
from backend.config import settings
from backend.db.connection import execute, query_one
from backend.errors import AppError
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

# ── Rate limiter ───────────────────────────────────────────────────────────────
# Primary store is Postgres (auth_rate_events) so the window survives restarts
# and is shared across workers/instances. The in-memory deque is only a fallback
# for when the DB is unreachable — in that case limiting is per-process again,
# which is still better than no limiting at all.
_rate_lock    = Lock()
_rate_buckets: dict[str, deque] = defaultdict(deque)

def _check_rate(key: str, max_attempts: int, window_secs: int) -> None:
    """Raise 429 if `key` has exceeded `max_attempts` in the last `window_secs`."""
    if settings.testing_mode:
        return  # testing mode: auth rate limiting disabled

    allowed: bool | None = None
    try:
        execute(
            "DELETE FROM auth_rate_events WHERE key = %s AND created_at < NOW() - make_interval(secs => %s)",
            (key, window_secs),
        )
        row = query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (key,))
        n = int(row["n"]) if row else 0
        allowed = n < max_attempts
        if allowed:
            execute("INSERT INTO auth_rate_events (key) VALUES (%s)", (key,))
    except Exception:
        allowed = None  # DB unavailable/mocked → fall back to in-memory window

    if allowed is None:
        now = time.monotonic()
        with _rate_lock:
            dq = _rate_buckets[key]
            while dq and dq[0] < now - window_secs:
                dq.popleft()
            allowed = len(dq) < max_attempts
            if allowed:
                dq.append(now)

    if not allowed:
        raise AppError(
            "too_many_attempts",
            "Too many attempts. Please try again later.",
            status_code=429,
        )


def _reject_weak_password(password: str) -> None:
    """Guard every password entry point with one localizable code.

    ``validate_strength`` returns an English sentence per broken rule; the user
    must read this in their own language, so the rules travel as a single stable
    code and the frontend renders the full requirement list from
    ``errors.password_invalid``. The English sentence stays as the fallback
    ``message`` (and in the ``rule`` param) for clients with no mapping.
    """
    valid, msg = validate_strength(password)
    if not valid:
        raise AppError("password_invalid", msg, status_code=400, params={"rule": msg})


def _lookup_email(email: str) -> dict | None:
    """Find tenant_id + user_id for a global email (users.email is UNIQUE)."""
    return query_one(
        "SELECT id AS user_id, tenant_id FROM users WHERE email = %s",
        (email.lower().strip(),),
    )


def _hash_code(code: str) -> str:
    return hmac.new(settings.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


# A 6-digit OTP has 10^6 combinations; keeping the window short and burning the
# code after a few wrong guesses is what makes brute force infeasible.
_OTP_EXPIRE_MINUTES = 15
_OTP_MAX_ATTEMPTS   = 5


def _issue_reset_otp(user_id: str, tenant_id: str) -> str:
    """Generate a 6-digit OTP for password reset (purpose='reset', expires in 15 min)."""
    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    execute(
        "DELETE FROM pw_change_codes WHERE user_id = %s AND purpose = 'reset' AND used = FALSE",
        (user_id,),
    )
    execute(
        """INSERT INTO pw_change_codes (id, user_id, tenant_id, code_hash, expires_at, purpose)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, 'reset')""",
        (user_id, tenant_id, _hash_code(code),
         datetime.now(timezone.utc) + timedelta(minutes=_OTP_EXPIRE_MINUTES)),
    )
    return code


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest):
    _reject_weak_password(body.password)

    if _lookup_email(body.email):
        raise AppError(
            "email_already_registered", "Email already registered", status_code=409,
        )

    # A number already VERIFIED by someone else authenticates the inbound
    # WhatsApp bot as that person — signup must not be able to claim it.
    from backend.whatsapp.identity import normalize_phone

    phone = normalize_phone(body.whatsapp_number)
    if query_one(
        """SELECT id FROM users
           WHERE whatsapp_number = %s AND whatsapp_verified_at IS NOT NULL""",
        (phone,),
    ):
        raise AppError(
            "whatsapp_number_taken",
            "This WhatsApp number is already linked to another account",
            status_code=409,
        )

    tenant = create_tenant(body.tenant_name)
    user = user_svc.create_user(
        tenant_id=tenant["id"],
        email=body.email,
        password=body.password,
        role="admin",
        full_name=body.full_name,
        whatsapp_number=phone,
    )

    verify_token = create_signed_token(
        {"sub": user["id"], "tenant_id": tenant["id"], "purpose": "email_verify"},
        expires_minutes=60 * 24,
    )
    verify_url = f"{settings.frontend_url}/verify-email?token={verify_token}"
    from backend.notifications import email as email_mod
    from backend.notifications.email import send_verification_email
    email_sent = send_verification_email(body.email, body.full_name or "", verify_url)
    log.info("[signup] user=%s email_sent=%s", user["id"], email_sent)

    return ok({
        "user": user,
        "tenant": {"id": tenant["id"], "name": tenant["name"]},
        "email_sent": email_sent,
        # Stable code, not prose: the UI renders the Spanish (CLAUDE.md). Without
        # it a signup with no mail transport still told the user to go check
        # their inbox for a link that was never generated a delivery attempt.
        "email_error": None if email_sent else email_mod.failure_reason(),
        "message": (
            "Account created. Check your email to verify."
            if email_sent else
            "Account created but verification email could not be sent. "
            "Contact your administrator."
        ),
    })


@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest):
    # Expired and malformed collapse into one code on purpose: the user's next
    # step is identical (request a new link), and `str(e)` is jwt's own English.
    try:
        payload = decode_token(body.token)
    except ValueError as e:
        raise AppError("verification_token_invalid", str(e), status_code=400)

    if payload.get("purpose") != "email_verify":
        raise AppError(
            "verification_token_invalid", "Invalid token purpose", status_code=400,
        )

    user_svc.mark_verified(payload["tenant_id"], payload["sub"])
    return ok({"message": "Email verified. You can now log in."})


@router.post("/login")
async def login(body: LoginRequest):
    _check_rate(f"login:{body.email.lower()}", max_attempts=5, window_secs=300)
    entry = _lookup_email(body.email)
    if not entry:
        # Unknown address and wrong password share one code deliberately —
        # distinguishing them would make login an account-enumeration oracle.
        raise AppError("invalid_credentials", "Invalid credentials", status_code=401)

    user = user_svc.verify_credentials(entry["tenant_id"], body.email, body.password)
    if not user:
        raise AppError("invalid_credentials", "Invalid credentials", status_code=401)

    if not user.get("email_verified"):
        raise AppError(
            "email_not_verified", "Please verify your email first", status_code=403,
        )

    user_status = user.get("status", "active")
    if user_status != "active":
        if user_status == "pending_confirmation":
            raise AppError(
                "account_pending_confirmation",
                "Account pending email verification. Check your inbox.",
                status_code=403,
            )
        raise AppError(
            "account_not_active",
            "Account is not active. Contact your administrator.",
            status_code=403,
            params={"status": user_status},
        )

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
        raise AppError(
            "refresh_token_invalid", "Invalid or expired refresh token", status_code=401,
        )

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
        sent = send_password_reset_otp(body.email, code)
        log.info("[forgot-password] OTP issued for email=%s sent=%s", body.email, sent)

    # Deliberately never reports delivery: the response is identical for an
    # unknown address, and leaking "sent/not sent" would turn this into an
    # account-enumeration oracle. The outcome goes to the server log only.
    return ok({"message": "If the email exists, a verification code has been sent."})


@router.post("/forgot-password/verify")
async def forgot_password_verify(body: ForgotPasswordVerifyRequest):
    _check_rate(f"otp:{body.email.lower()}", max_attempts=10, window_secs=600)
    entry = _lookup_email(body.email)
    if not entry:
        raise AppError("reset_code_invalid", "Invalid or expired code.", status_code=400)

    row = query_one(
        """SELECT id, code_hash FROM pw_change_codes
           WHERE user_id = %s AND purpose = 'reset' AND used = FALSE AND expires_at > NOW()
                 AND attempts < %s
           ORDER BY created_at DESC LIMIT 1""",
        (entry["user_id"], _OTP_MAX_ATTEMPTS),
    )
    if not row:
        raise AppError("reset_code_invalid", "Invalid or expired code.", status_code=400)
    if not hmac.compare_digest(row["code_hash"], _hash_code(body.code.strip())):
        # Count the failure; after _OTP_MAX_ATTEMPTS wrong guesses the code is dead
        # and the user must request a new one.
        execute(
            "UPDATE pw_change_codes SET attempts = attempts + 1 WHERE id = %s",
            (row["id"],),
        )
        raise AppError("reset_code_invalid", "Invalid or expired code.", status_code=400)

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
        raise AppError("reset_token_invalid", str(e), status_code=400)

    if payload.get("purpose") != "password_reset":
        raise AppError("reset_token_invalid", "Invalid token", status_code=400)

    _reject_weak_password(body.new_password)

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
                revoke(jti, datetime.fromtimestamp(exp, tz=timezone.utc))
            if user_id:
                user_svc.revoke_all_tokens(payload.get("tenant_id", ""), user_id)
        except ValueError:
            pass
    return ok({"message": "Logged out"})
