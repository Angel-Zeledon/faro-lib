"""Per-tenant session and password policy: the enforcement half.

The admin routes (`GET/PUT/DELETE /session-policy`, `POST
/session-policy/unlock/{user_id}`) are served by the Rust API
(`backend-rs/src/routes/session_policy.rs`) and have no Python twin. This module
READS what they store (`tenant_session_policies`) and enforces it where the
Python app still owns the path: login, refresh, password entry points, and the
JWT guard (the Rust auth path mirrors the guard half, check for check).

THE RULE THAT MATTERS: a tenant with no row, or a row with every limit unset,
behaves exactly as before. Every function here starts by asking for the policy
and returns without a query, a write or a refusal when there is none.

What each setting does, and where:

* `max_session_hours`      - the session (login to now) may not outlive it. The
                             access token carries `sat` (session auth time, the
                             refresh token's creation) or, for a token minted by
                             the login itself, its `iat`. Checked by the guard
                             and again at refresh, which also deletes the refresh
                             token so the session cannot be renewed.
* `idle_timeout_minutes`   - no authenticated request for that long ends the
                             session (`users.last_activity_at`). Per USER, not
                             per device: a second device in use keeps the
                             account "active". A request that says it is a
                             background poll (`X-StockAI-Background`) is checked
                             but does not count as activity, or an open tab
                             would keep the session alive forever.
* `min_password_length`, `require_mixed_case`, `require_symbol`
                           - on top of the product rules (8+, a letter, a digit,
                             72 bytes), at every place a password is chosen.
* `password_max_age_days`  - a password older than that is refused at login
                             (`password_expired`); the person resets it with the
                             normal forgot-password flow. The clock for
                             passwords that are already old starts when the
                             limit is set or changed (`password_max_age_since`),
                             so switching it on never locks the tenant out.
* `max_concurrent_sessions`- at login the oldest refresh tokens beyond the limit
                             are dropped (newest login wins). The evicted
                             session's access token lives out its <= 15 minutes
                             and cannot be renewed.
* `lockout_threshold`, `lockout_minutes`
                           - that many wrong passwords in a row lock the
                             account for the period; a correct password does
                             not unlock early, an admin (or a password reset)
                             does.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.db.connection import execute, query, query_one
from backend.errors import AppError

log = logging.getLogger(__name__)

# A request that is the page polling for a badge, not a person acting.
BACKGROUND_HEADER = "x-stockai-background"
# Do not write `last_activity_at` more than once per this many seconds per user:
# a page fires a dozen requests per click, and the smallest idle limit is 5 min.
ACTIVITY_WRITE_THROTTLE_SECONDS = 30
DEFAULT_LOCKOUT_MINUTES = 15
# What `add_refresh_token` has always kept per user, with no policy.
DEFAULT_MAX_REFRESH_TOKENS = 5

# Bounds, shared with the Rust admin route and the table's CHECK constraints.
BOUNDS = {
    "max_session_hours": (1, 168),
    "idle_timeout_minutes": (5, 1440),
    "min_password_length": (8, 64),
    "password_max_age_days": (7, 730),
    "max_concurrent_sessions": (1, 20),
    "lockout_threshold": (3, 20),
    "lockout_minutes": (1, 1440),
}


@dataclass(frozen=True)
class SessionPolicy:
    max_session_hours: Optional[int] = None
    idle_timeout_minutes: Optional[int] = None
    min_password_length: Optional[int] = None
    require_mixed_case: bool = False
    require_symbol: bool = False
    password_max_age_days: Optional[int] = None
    password_max_age_since: Optional[datetime] = None
    max_concurrent_sessions: Optional[int] = None
    lockout_threshold: Optional[int] = None
    lockout_minutes: Optional[int] = None

    @property
    def effective_lockout_minutes(self) -> int:
        return self.lockout_minutes or DEFAULT_LOCKOUT_MINUTES

    @property
    def has_password_rules(self) -> bool:
        return bool(self.min_password_length or self.require_mixed_case or self.require_symbol)


_COLUMNS = (
    "max_session_hours, idle_timeout_minutes, min_password_length, require_mixed_case, "
    "require_symbol, password_max_age_days, password_max_age_since, "
    "max_concurrent_sessions, lockout_threshold, lockout_minutes"
)


def _from_row(row: Optional[dict]) -> Optional[SessionPolicy]:
    """The policy a row describes, or None when it restricts nothing."""
    if not row:
        return None
    policy = SessionPolicy(
        max_session_hours=row.get("max_session_hours"),
        idle_timeout_minutes=row.get("idle_timeout_minutes"),
        min_password_length=row.get("min_password_length"),
        require_mixed_case=bool(row.get("require_mixed_case")),
        require_symbol=bool(row.get("require_symbol")),
        password_max_age_days=row.get("password_max_age_days"),
        password_max_age_since=row.get("password_max_age_since"),
        max_concurrent_sessions=row.get("max_concurrent_sessions"),
        lockout_threshold=row.get("lockout_threshold"),
        lockout_minutes=row.get("lockout_minutes"),
    )
    restricts = (
        policy.max_session_hours or policy.idle_timeout_minutes or policy.has_password_rules
        or policy.password_max_age_days or policy.max_concurrent_sessions
        or policy.lockout_threshold
    )
    return policy if restricts else None


def get_policy(tenant_id: str) -> Optional[SessionPolicy]:
    """The tenant's active policy, or None (no row, or nothing set)."""
    if not tenant_id:
        return None
    return _from_row(query_one(
        f"SELECT {_COLUMNS} FROM tenant_session_policies WHERE tenant_id = %s",
        (tenant_id,),
    ))


# ── Passwords ────────────────────────────────────────────────────────────────

def password_rule_violations(password: str, policy: Optional[SessionPolicy]) -> list[str]:
    """Which tenant rules `password` breaks, as stable codes (empty = fine)."""
    if policy is None:
        return []
    broken: list[str] = []
    if policy.min_password_length and len(password) < policy.min_password_length:
        broken.append("min_length")
    if policy.require_mixed_case and not (
        any(c.isupper() for c in password) and any(c.islower() for c in password)
    ):
        broken.append("mixed_case")
    if policy.require_symbol and not any(
        not c.isalnum() and not c.isspace() for c in password
    ):
        broken.append("symbol")
    return broken


def reject_password_against_policy(password: str, tenant_id: Optional[str]) -> None:
    """Raise `password_policy` when the tenant's rules refuse `password`.

    Called AFTER the product-wide `validate_strength`, so a password that fails
    both reports the product rule first, as it always did.
    """
    if not tenant_id:
        return
    policy = get_policy(tenant_id)
    broken = password_rule_violations(password, policy)
    if not broken or policy is None:
        return
    raise AppError(
        "password_policy",
        "The password does not meet your organization's password policy.",
        status_code=400,
        params={
            "min_length": policy.min_password_length or 8,
            "require_mixed_case": policy.require_mixed_case,
            "require_symbol": policy.require_symbol,
            "broken": broken,
        },
    )


def password_age_refusal(user: dict, policy: Optional[SessionPolicy]) -> Optional[AppError]:
    """`password_expired` when the tenant's age limit has passed, else None.

    Not applied to an account with no usable password (provider-only), which has
    nothing to expire.
    """
    if policy is None or not policy.password_max_age_days:
        return None
    if user.get("has_password", True) is False:
        return None
    base = user.get("password_changed_at") or user.get("created_at")
    if base is None:
        return None
    since = policy.password_max_age_since
    if since is not None and since > base:
        base = since
    if datetime.now(timezone.utc) - base <= timedelta(days=policy.password_max_age_days):
        return None
    return AppError(
        "password_expired",
        "Your password is older than your organization allows. Reset it to sign in.",
        status_code=403,
        params={"max_age_days": policy.password_max_age_days},
    )


# ── Lockout ──────────────────────────────────────────────────────────────────

def refuse_if_locked(tenant_id: str, user_id: str, policy: Optional[SessionPolicy]) -> None:
    """Refuse a password login while the account is locked.

    Runs BEFORE the password is looked at: a correct password proves nothing to
    a locked account. A lock that has run out is cleared here, with the counter,
    so the next wrong password starts a fresh count. Ignored when the tenant no
    longer has a lockout setting (turning it off releases everyone).
    """
    if policy is None or not policy.lockout_threshold:
        return
    row = query_one(
        "SELECT locked_until, "
        "CEIL(EXTRACT(EPOCH FROM (locked_until - NOW())) / 60.0)::int AS minutes_left, "
        "(locked_until IS NOT NULL AND locked_until > NOW()) AS locked "
        "FROM users WHERE id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    if not row or row.get("locked_until") is None:
        return
    if row["locked"]:
        raise AppError(
            "account_locked",
            "This account is locked after too many failed sign-in attempts. "
            "Try again later or ask an administrator to unlock it.",
            status_code=403,
            params={"minutes": max(1, int(row["minutes_left"] or 1))},
        )
    execute(
        "UPDATE users SET failed_login_count = 0, locked_until = NULL "
        "WHERE id = %s AND tenant_id = %s AND locked_until <= NOW()",
        (user_id, tenant_id),
    )


def record_failed_login(tenant_id: str, user_id: str, policy: Optional[SessionPolicy]) -> None:
    """Count a wrong password; lock the account when the threshold is reached.

    One atomic statement, so concurrent guesses cannot read the same count and
    both stay under the threshold.
    """
    if policy is None or not policy.lockout_threshold:
        return
    rows = query(
        """UPDATE users
              SET failed_login_count = failed_login_count + 1,
                  locked_until = CASE WHEN failed_login_count + 1 >= %s
                                      THEN NOW() + make_interval(mins => %s)
                                      ELSE locked_until END
            WHERE id = %s AND tenant_id = %s
        RETURNING failed_login_count, email""",
        (policy.lockout_threshold, policy.effective_lockout_minutes, user_id, tenant_id),
    )
    if rows and int(rows[0]["failed_login_count"]) == policy.lockout_threshold:
        from backend.activity.events import record_event
        record_event(
            tenant_id, user_id, "account.user_locked_out", resource=user_id,
            details={"email": rows[0]["email"], "attempts": policy.lockout_threshold},
            reason="too_many_failed_logins",
        )


def clear_failed_logins(tenant_id: str, user_id: str, policy: Optional[SessionPolicy]) -> None:
    """A correct password resets the run of failures (a write only when needed)."""
    if policy is None or not policy.lockout_threshold:
        return
    execute(
        "UPDATE users SET failed_login_count = 0 "
        "WHERE id = %s AND tenant_id = %s AND failed_login_count <> 0",
        (user_id, tenant_id),
    )


# ── Sessions ─────────────────────────────────────────────────────────────────

def refresh_token_keep(tenant_id: str) -> int:
    """How many refresh tokens (sessions) a user may hold after a new login."""
    policy = get_policy(tenant_id)
    if policy and policy.max_concurrent_sessions:
        return policy.max_concurrent_sessions
    return DEFAULT_MAX_REFRESH_TOKENS


def _max_lifetime_error(hours: int) -> AppError:
    return AppError(
        "session_max_lifetime",
        "Your session reached the maximum length your organization allows. Sign in again.",
        status_code=401, params={"hours": hours},
    )


def _idle_error(minutes: int) -> AppError:
    return AppError(
        "session_idle_timeout",
        "You were signed out after a period of inactivity. Sign in again.",
        status_code=401, params={"minutes": minutes},
    )


def enforce_access_token(payload: dict, *, background: bool = False) -> None:
    """The guard half: refuse a token past the tenant's lifetime or idle limit.

    One primary-key query joined to the policy row. A tenant with neither limit
    set costs that one read and nothing else (no write, no refusal).
    """
    user_id = payload.get("sub")
    if not user_id:
        return
    row = query_one(
        """SELECT p.max_session_hours, p.idle_timeout_minutes,
                  EXTRACT(EPOCH FROM (NOW() - u.last_activity_at)) AS idle_seconds
             FROM users u
             JOIN tenant_session_policies p ON p.tenant_id = u.tenant_id
            WHERE u.id = %s""",
        (user_id,),
    )
    if not row:
        return
    max_hours = row.get("max_session_hours")
    idle_minutes = row.get("idle_timeout_minutes")

    if max_hours:
        started = payload.get("sat")
        if started is None:
            started = payload.get("iat")
        # A token with no clock claim cannot prove its age; those predate `iat`
        # and die within 15 minutes anyway.
        if started is not None and time.time() - float(started) > max_hours * 3600:
            raise _max_lifetime_error(int(max_hours))

    if idle_minutes:
        idle_seconds = row.get("idle_seconds")
        if idle_seconds is not None and float(idle_seconds) > idle_minutes * 60:
            raise _idle_error(int(idle_minutes))
        if not background:
            execute(
                "UPDATE users SET last_activity_at = NOW() WHERE id = %s AND "
                "(last_activity_at IS NULL OR "
                " last_activity_at < NOW() - make_interval(secs => %s))",
                (user_id, ACTIVITY_WRITE_THROTTLE_SECONDS),
            )


def enforce_refresh(user: dict, token_hash: str) -> None:
    """The refresh half: the session may not be renewed past its limits.

    `user` is the row `validate_refresh_token` returns (the user's columns plus
    `session_started_at`, the refresh token's creation). On a refusal the
    refresh token is deleted, so the same token cannot be tried again.
    """
    policy = get_policy(user["tenant_id"])
    if policy is None or not (policy.max_session_hours or policy.idle_timeout_minutes):
        return
    row = query_one(
        """SELECT EXTRACT(EPOCH FROM (NOW() - rt.created_at)) AS session_seconds,
                  EXTRACT(EPOCH FROM (NOW() - u.last_activity_at)) AS idle_seconds
             FROM refresh_tokens rt JOIN users u ON u.id = rt.user_id
            WHERE rt.hash = %s""",
        (token_hash,),
    )
    if not row:
        return
    error: Optional[AppError] = None
    if policy.max_session_hours and row["session_seconds"] is not None \
            and float(row["session_seconds"]) > policy.max_session_hours * 3600:
        error = _max_lifetime_error(int(policy.max_session_hours))
    elif policy.idle_timeout_minutes and row["idle_seconds"] is not None \
            and float(row["idle_seconds"]) > policy.idle_timeout_minutes * 60:
        error = _idle_error(int(policy.idle_timeout_minutes))
    if error is not None:
        execute("DELETE FROM refresh_tokens WHERE hash = %s", (token_hash,))
        raise error


def unlock_user(tenant_id: str, user_id: str) -> bool:
    """Clear a user's lock and failure count (Python-side twin of the Rust route,
    used by the password reset: proving the mailbox is as good as an admin)."""
    rows = query(
        "UPDATE users SET failed_login_count = 0, locked_until = NULL "
        "WHERE id = %s AND tenant_id = %s "
        "AND (failed_login_count <> 0 OR locked_until IS NOT NULL) RETURNING id",
        (user_id, tenant_id),
    )
    return bool(rows)


def is_background(headers: Any) -> bool:
    """Whether the request said it is a background poll."""
    try:
        return (headers.get(BACKGROUND_HEADER) or "").strip() == "1"
    except Exception:
        return False
