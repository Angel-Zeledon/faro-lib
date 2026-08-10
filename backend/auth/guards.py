"""
FastAPI dependency guards for authentication and role-based access.

Usage:
    @router.get("/endpoint")
    async def endpoint(user: CurrentUser = Depends(get_current_user)):
        ...

    @router.post("/admin-only")
    async def admin(user: CurrentUser = Depends(require_admin)):
        ...

    @router.post("/sends-an-email")
    async def outward(user: CurrentUser = Depends(require_verified_analyst_or_above)):
        ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.errors import AppError

from backend.auth.jwt_handler import decode_token
from backend.config import settings
from backend.errors import AppError

security = HTTPBearer()


class CurrentUser:
    __slots__ = ("user_id", "tenant_id", "role", "email_verified", "api_key_id")

    def __init__(
        self, user_id: str, tenant_id: str, role: str, email_verified: bool = True,
        api_key_id: str | None = None,
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.email_verified = email_verified
        # Set only when the caller is an integration rather than a person.
        # Endpoints never read it; it exists so audit and logging can say who
        # really acted.
        self.api_key_id = api_key_id

    @property
    def is_machine(self) -> bool:
        return self.api_key_id is not None


def _authenticate_api_key(credential: str) -> CurrentUser:
    """Turn an `sk_live_*` credential into the same CurrentUser a login yields.

    The plan is checked HERE, on every request, not only when the key was
    minted. A tenant that drops off Professional keeps its key rows, and a key
    that outlived the plan that justified it would be a paid feature that
    silently survives cancellation.
    """
    from backend.auth import api_key_auth

    key = api_key_auth.resolve(credential)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is invalid or expired",
        )

    if not settings.testing_mode:
        from backend.entitlements.plans import Feature
        from backend.entitlements.service import has_feature
        from backend.tenants.service import get_tenant

        tenant = get_tenant(key["tenant_id"]) or {}
        if not has_feature(tenant, Feature.API_ACCESS):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLAN_UPGRADE_REQUIRED",
                    "feature": Feature.API_ACCESS.value,
                    "current_plan": tenant.get("plan", "starter"),
                },
            )

    api_key_auth.touch(key["id"])
    return CurrentUser(
        user_id=api_key_auth.actor_id(key["id"]),
        tenant_id=key["tenant_id"],
        # The key's own role, not its creator's: the integration must keep
        # working when that person leaves, and must not gain power when they
        # are promoted.
        role=key["role"],
        # A key belongs to a tenant that was already paying when it was minted;
        # there is no inbox to send a verification link to.
        email_verified=True,
        api_key_id=key["id"],
    )


def _reject_if_predates_password_change(payload: dict) -> None:
    """Refuse a token minted before this account last cut its sessions.

    The blocklist above can only disown a token whose `jti` somebody handed us —
    which is why /logout can revoke itself and a password RESET cannot: that
    flow is unauthenticated and never sees the intruder's token. So the cut is
    expressed per user and per time instead: `users.sessions_invalid_before`,
    set by `update_password`, against the token's own `iat`.

    Costs one primary-key lookup, in the same shape as `is_revoked` right above.

    Both sides keep sub-second precision, and that is the whole reason this
    comparison is trustworthy. A first version floored both to the second so a
    login made in the same second as a reset would not read as older than the
    cut and lock the user out of the account they had just recovered; the suite
    then caught the mirror image — under load the PRE-reset token was minted in
    that same second as well, and sailed through a password change. A second
    cannot separate "issued just before" from "issued just after". Microseconds
    can, so nothing rounds and both cases land correctly.

    A token with NO `iat` cannot prove when it was made, so it is refused — but
    only for an account that has actually cut its sessions. Accounts that never
    changed a password hold NULL here and never reach that branch, so tokens
    minted before `iat` existed keep working exactly as before.
    """
    user_id = payload.get("sub")
    if not user_id:
        return
    from backend.db.connection import query_one

    row = query_one(
        "SELECT sessions_invalid_before FROM users WHERE id = %s", (user_id,),
    )
    cut = row.get("sessions_invalid_before") if row else None
    if not cut:
        return

    iat = payload.get("iat")
    if iat is not None and float(iat) >= cut.timestamp():
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session ended by a password change",
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUser:
    # One header, two kinds of caller. Dispatching on the prefix keeps a JWT
    # from ever paying for a database lookup, and keeps a malformed key from
    # being reported as a malformed token.
    from backend.auth.api_key_auth import looks_like_api_key
    if looks_like_api_key(credentials.credentials):
        return _authenticate_api_key(credentials.credentials)

    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type"
        )

    jti = payload.get("jti")
    if jti:
        from backend.auth.blocklist import is_revoked
        if is_revoked(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
            )

    _reject_if_predates_password_change(payload)

    return CurrentUser(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        role=payload["role"],
        # Absent claim → treated as verified. Only tokens minted before the
        # claim existed lack it, and those belong to accounts that could only
        # have logged in by being verified under the old 403-at-login rule.
        email_verified=bool(payload.get("email_verified", True)),
    )


def require_role(*roles: str):
    def guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            # A structured code, not a sentence: this guard sits behind every
            # mutating endpoint, so its English `detail` was what a Spanish
            # -speaking warehouse user actually read on screen —
            # "Role 'viewer' not permitted. Required: ['admin', 'analyst']".
            # The frontend renders the Spanish from code + params (CLAUDE.md).
            raise AppError(
                "role_not_permitted",
                f"Role '{user.role}' not permitted. Required: {list(roles)}",
                status_code=status.HTTP_403_FORBIDDEN,
                params={"role": user.role, "required": sorted(roles)},
            )
        return user

    return guard


require_admin = require_role("admin")


def require_analyst_or_above(
    user: CurrentUser = Depends(require_role("admin", "analyst")),
) -> CurrentUser:
    # Delegates to the entitlements guard so trial read-only is enforced
    # on every mutating endpoint. Imported lazily to avoid a circular import
    # (entitlements.guards imports from this module).
    from backend.entitlements.guards import require_active_analyst as _active
    return _active(user)


require_any = require_role("admin", "analyst", "viewer")


# ── Email verification ─────────────────────────────────────────────────────
# An unverified user is NOT locked out: login succeeds and they can explore,
# upload data and run the demo. Verification is demanded only where an action
# leaves the tenant — inviting people, wiring an integration, sending a
# notification — because those are the ones that hurt if the address turns out
# not to belong to whoever signed up. Everything else stays open, so the user
# sees value before being asked to go dig through their spam folder.
#
# Composed with the role guards (never inline in an endpoint body) so the order
# is uniform everywhere: role first, then verification. A viewer therefore still
# gets the plain role 403 on a mutating endpoint, unchanged by this feature.

def require_verified_email(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Block the caller unless their email address has been verified."""
    if not user.email_verified:
        raise AppError(
            "email_not_verified",
            "Verify your email address to use this feature",
            status_code=403,
        )
    return user


def require_verified_analyst_or_above(
    user: CurrentUser = Depends(require_analyst_or_above),
) -> CurrentUser:
    return require_verified_email(user)


def require_verified_admin(
    user: CurrentUser = Depends(require_admin),
) -> CurrentUser:
    return require_verified_email(user)
