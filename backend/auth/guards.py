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

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.errors import AppError

from backend.auth.jwt_handler import decode_token
from backend.config import settings
from backend.errors import AppError


class _BearerOrApiKey(HTTPBearer):
    """`Authorization: Bearer <credential>`, or `X-API-Key: sk_live_...`.

    BI tools (Power BI's web connector, Excel Power Query, Looker) can set a
    custom header but not always the Authorization one, and a key must never be
    put in the URL (proxies and browser history keep URLs). So the same key is
    accepted in either header and ends up as the same credential: everything
    downstream (`get_current_user`, plan check, scope, rate window, meter) is
    unchanged.

    Authorization wins when both are sent. `X-API-Key` is read ONLY when it holds
    an `sk_live_*` key: a JWT in it is not a credential, so it is treated as no
    credential at all and answers exactly what a request with none answers.
    """

    async def __call__(self, request: Request):
        from backend.auth.api_key_auth import looks_like_api_key
        header_key = (request.headers.get("x-api-key") or "").strip()
        if (header_key and looks_like_api_key(header_key)
                and not request.headers.get("authorization")):
            return HTTPAuthorizationCredentials(scheme="Bearer", credentials=header_key)
        return await super().__call__(request)


security = _BearerOrApiKey()


class CurrentUser:
    # `scope_cache` is the warehouse scope resolved once per request
    # (backend/auth/warehouse_scope.py); unset until something asks for it.
    __slots__ = ("user_id", "tenant_id", "role", "email_verified", "api_key_id",
                 "scope_cache")

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


def _authenticate_api_key(credential: str, scope: dict | None = None) -> CurrentUser:
    """Turn an `sk_live_*` credential into the same CurrentUser a login yields."""
    from backend.auth import api_key_auth

    key = api_key_auth.resolve(credential)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is invalid or expired",
        )

    # Plan entitlement, checked on EVERY call and not only when the key was
    # minted: a tenant that is (or falls back to) a tier without the API keeps
    # its old keys in the table, and they must stop working — and say why,
    # instead of answering as an invalid key. Before the rate window and the
    # meter, so a refused call is neither counted nor billed. The MCP endpoint
    # is its own feature, so the error names the one actually being used.
    from backend.entitlements.service import ensure_feature
    route_path = getattr(scope.get("route"), "path", "") if scope is not None else ""
    if route_path.endswith("/mcp"):
        ensure_feature(key["tenant_id"], "mcp",
                       "Existing API keys stop working on plans without MCP access.")
    else:
        ensure_feature(key["tenant_id"], "api",
                       "This key still exists but the tenant's plan no longer "
                       "includes API access, so it cannot be used.")

    # Which route this key was presented to, and whether a key may call it at
    # all. Decided by rule in `backend/api/public_surface.py`; refused here,
    # before the rate window and before metering, so a refused call is neither
    # counted against the ceiling nor billed.
    from backend.api.public_surface import ROLE_SCOPE, exposure
    route = scope.get("route") if scope is not None else None
    exp = exposure(route)
    if not exp.exposed:
        raise AppError(
            "api_key_route_not_exposed",
            "This endpoint cannot be called with an API key.",
            status_code=status.HTTP_403_FORBIDDEN,
            params={"reason": exp.reason},
        )
    key_scope = ROLE_SCOPE.get(key["role"], "read")
    if exp.scope == "write" and key_scope != "write":
        raise AppError(
            "api_key_scope_insufficient",
            "This endpoint writes and the API key is read-only. Use a write key.",
            status_code=status.HTTP_403_FORBIDDEN,
            params={"required_scope": "write", "key_scope": key_scope},
        )

    # Before any work: a key over its window costs one counter read, not a
    # forecast. 429 with Retry-After is the answer an integration can act on —
    # a bare 429 makes it guess, and a guessing client retries in a tighter
    # loop than the one being limited.
    if not settings.testing_mode and not api_key_auth.check_rate(
        key["id"], key["tenant_id"]
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: {api_key_auth.RATE_MAX_PER_MINUTE} "
                f"requests per minute per API key, and the daily ceiling of "
                f"this tenant's plan."
            ),
            headers={"Retry-After": str(api_key_auth.RATE_WINDOW_SECONDS)},
        )

    # Counted for billing only once every check above has passed. A metering
    # failure never fails the call — `meter` logs it at ERROR instead.
    api_key_auth.meter(key["id"], key["tenant_id"], key.get("name") or "")
    api_key_auth.touch(key["id"])
    # Publish who is acting so the audit middleware does not have to resolve the
    # credential a second time. Set after every check has passed: a refused
    # request has no actor to record.
    if scope is not None:
        from backend.auth.actor_context import set_machine_actor
        set_machine_actor(scope, key["tenant_id"], api_key_auth.actor_id(key["id"]))

    return CurrentUser(
        user_id=api_key_auth.actor_id(key["id"]),
        tenant_id=key["tenant_id"],
        # The key's own role, not its creator's: the integration must keep
        # working when that person leaves, and must not gain power when they
        # are promoted.
        role=key["role"],
        # A key has no inbox of its own. Whether it may take an action that
        # leaves the tenant (send a PO, an alert) is decided per call by
        # `require_verified_email`, against the tenant's verified admins.
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
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUser:
    # One header, two kinds of caller. Dispatching on the prefix keeps a JWT
    # from ever paying for a database lookup, and keeps a malformed key from
    # being reported as a malformed token.
    from backend.auth.api_key_auth import looks_like_api_key
    if looks_like_api_key(credentials.credentials):
        return _authenticate_api_key(credentials.credentials, request.scope)

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

    # The tenant's session policy (maximum lifetime, idle timeout). No policy
    # row for the tenant: one joined read, no write, no refusal.
    from backend.auth import session_policy
    session_policy.enforce_access_token(
        payload, background=session_policy.is_background(request.headers),
    )

    from backend.auth.actor_context import set_person_actor
    set_person_actor(request.scope, payload["tenant_id"], payload["sub"])

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
        if user.role not in roles and user.is_machine:
            # Normally unreachable: `_authenticate_api_key` already refused a
            # read key on a write route. Kept so a key that slips past that
            # check still gets the answer it can act on, not a person's role.
            raise AppError(
                "api_key_scope_insufficient",
                "This endpoint writes and the API key is read-only. Use a write key.",
                status_code=status.HTTP_403_FORBIDDEN,
                params={"required_scope": "write", "key_scope": "read"},
            )
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

    # Read by `backend/api/public_surface.py` to work out which key scope a
    # route needs (or that no key can call it) without hand-maintaining a list.
    guard.allowed_roles = tuple(roles)
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
# leaves the tenant — inviting people, sending a notification — because those
# are the ones that hurt if the address turns out
# not to belong to whoever signed up. Everything else stays open, so the user
# sees value before being asked to go dig through their spam folder.
#
# Composed with the role guards (never inline in an endpoint body) so the order
# is uniform everywhere: role first, then verification. A viewer therefore still
# gets the plain role 403 on a mutating endpoint, unchanged by this feature.

def require_verified_email(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Block the caller unless their email address has been verified.

    An API key has no address to verify. It may take an outward action only
    when its tenant has at least one ACTIVE admin with a verified address —
    the same bar a person on that tenant would have had to clear to send
    anything. Otherwise a key minted on an unverified signup would be a way
    round the gate the gate exists for.
    """
    if user.is_machine:
        from backend.db.connection import query_one
        row = query_one(
            """SELECT 1 AS ok FROM users
                WHERE tenant_id = %s AND role = 'admin'
                  AND email_verified = TRUE AND status = 'active'
                LIMIT 1""",
            (user.tenant_id,),
        )
        if not row:
            raise AppError(
                "api_key_tenant_unverified",
                "No verified admin on this account: verify an admin's email "
                "address before an API key can send anything outside it.",
                status_code=403,
            )
        return user
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
