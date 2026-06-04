"""
FastAPI dependency guards for authentication and role-based access.

Usage:
    @router.get("/endpoint")
    async def endpoint(user: CurrentUser = Depends(get_current_user)):
        ...

    @router.post("/admin-only")
    async def admin(user: CurrentUser = Depends(require_admin)):
        ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.jwt_handler import decode_token

security = HTTPBearer()


class CurrentUser:
    __slots__ = ("user_id", "tenant_id", "role")

    def __init__(self, user_id: str, tenant_id: str, role: str):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role


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
require_analyst_or_above = require_role("admin", "analyst")
require_any = require_role("admin", "analyst", "viewer")
