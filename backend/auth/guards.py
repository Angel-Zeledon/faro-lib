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

from backend.auth.jwt_handler import decode_token
from backend.errors import AppError

security = HTTPBearer()


class CurrentUser:
    __slots__ = ("user_id", "tenant_id", "role", "email_verified")

    def __init__(
        self, user_id: str, tenant_id: str, role: str, email_verified: bool = True
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.email_verified = email_verified


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUser:
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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' not permitted. Required: {list(roles)}",
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
