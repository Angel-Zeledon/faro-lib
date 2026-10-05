"""Social sign-in routes: Google, Microsoft, Apple, Facebook.

All of them sit under `/auth` (tag `auth`, which an API key can never reach)
and every one of them answers "off" until the instance operator enables a
provider — see `backend/auth/social/providers.enabled_providers`.

    GET    /auth/providers                   which buttons to draw (public)
    GET    /auth/oauth/{provider}/start      302 to the provider (public)
    GET    /auth/oauth/{provider}/callback   provider comes back (Google, Facebook)
    POST   /auth/oauth/{provider}/callback   provider comes back (Apple, form_post)
    POST   /auth/oauth/exchange              one-time code -> our own tokens
    GET    /auth/identities                  the signed-in person's linked providers
    DELETE /auth/identities/{provider}       unlink one

The browser reaches start/callback through the frontend's `/api/v1/*` proxy,
which is why every redirect URI is built from FRONTEND_URL. The flow's design
decisions are written down once, in `backend/auth/social/flow.py`.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from backend.api.v1.auth import _check_rate
from backend.api.v1.trial import _client_address
from backend.auth.guards import CurrentUser, get_current_user
from backend.auth.jwt_handler import create_access_token, create_refresh_token
from backend.auth.social import flow, providers
from backend.auth.social.providers import SocialAuthError
from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.schemas.common import ok
from backend.users import service as user_svc

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)

_COOKIE_PATH = "/api/v1/auth/oauth"


def _frontend() -> str:
    return settings.frontend_url.rstrip("/")


def redirect_uri(provider: str) -> str:
    """What the operator pastes into the provider console — and what must be
    sent, byte for byte, on both legs of the exchange."""
    return f"{_frontend()}/api/v1/auth/oauth/{provider}/callback"


def _error_redirect(code: str, intent: str = "login") -> RedirectResponse:
    page = "signup" if intent == "signup" else "login"
    resp = RedirectResponse(f"{_frontend()}/{page}?oauth_error={code}", status_code=302)
    resp.delete_cookie(flow.BINDING_COOKIE, path=_COOKIE_PATH)
    return resp


def _rate(key: str, request: Request) -> None:
    _check_rate(f"{key}:{_client_address(request)}", max_attempts=20, window_secs=300)


# ── Public: what to show ─────────────────────────────────────────────────────

@router.get("/providers")
def list_providers():
    """Enabled providers, in display order. Empty on every install that has not
    configured one — the login page then looks exactly as it always did."""
    return ok({"providers": providers.enabled_providers()})


# ── Start ────────────────────────────────────────────────────────────────────

@router.get("/oauth/{provider}/start")
def oauth_start(provider: str, request: Request, intent: str = "login", terms: int = 0):
    intent = "signup" if intent == "signup" else "login"
    try:
        _rate("oauth-start", request)
    except AppError:
        return _error_redirect("too_many_attempts", intent)
    if provider not in providers.PROVIDERS or not providers.is_enabled(provider):
        return _error_redirect("social_provider_unavailable", intent)

    started = flow.start_flow(provider, intent=intent, terms_accepted=bool(terms))
    url = providers.authorization_url(
        provider, redirect_uri=redirect_uri(provider), state=started.state,
        nonce=started.nonce, code_verifier=started.code_verifier,
    )
    resp = RedirectResponse(url, status_code=302)
    secure = _frontend().startswith("https://")
    # Apple returns with a cross-site POST; a Lax cookie would not come along
    # and every Apple sign-in would fail the binding check. Apple only allows
    # https return URLs, so `Secure` is always satisfiable there.
    cross_site = providers.uses_form_post(provider)
    resp.set_cookie(
        flow.BINDING_COOKIE, started.binding,
        max_age=flow.FLOW_TTL_MINUTES * 60, path=_COOKIE_PATH, httponly=True,
        secure=secure or cross_site, samesite="none" if cross_site else "lax",
    )
    return resp


# ── Callback ─────────────────────────────────────────────────────────────────

def _finish(
    provider: str, request: Request, *, code: str | None, state: str | None,
    error: str | None, apple_user: dict | None = None,
) -> RedirectResponse:
    intent = flow.peek_flow_intent(state)
    try:
        _rate("oauth-callback", request)
    except AppError:
        return _error_redirect("too_many_attempts", intent)
    if provider not in providers.PROVIDERS:
        return _error_redirect("social_provider_unavailable", intent)

    binding = request.cookies.get(flow.BINDING_COOKIE)
    try:
        # Consumed FIRST, even on a provider error: the state is single-use
        # whatever the outcome, and a cancelled flow must not stay replayable.
        row = flow.consume_flow(provider, state, binding)
        if error:
            # The person said no at the provider (or the provider refused).
            raise SocialAuthError(
                "oauth_cancelled" if error in ("access_denied", "user_cancelled_authorize")
                else "oauth_provider_error",
                error,
            )
        if not providers.is_enabled(provider):
            raise SocialAuthError("social_provider_unavailable", provider)
        if not code:
            raise SocialAuthError("oauth_provider_error", "No code on the callback")
        identity = providers.exchange_code(
            provider, code=code, redirect_uri=redirect_uri(provider),
            code_verifier=row["code_verifier"], nonce=row["nonce"], apple_user=apple_user,
        )
        resolution = flow.resolve_account(identity, terms_accepted=bool(row["terms_accepted"]))
        flow.check_can_sign_in(resolution.user)
        # A provider button is not a way round a company's enforced SSO.
        from backend.auth.sso import service as sso_service
        if sso_service.enforced_for(resolution.user["tenant_id"], resolution.user["email"]):
            raise SocialAuthError("sso_required", "SSO is required for this account")
    except SocialAuthError as exc:
        log.info("[social] %s callback refused: %s (%s)", provider, exc.code, exc.detail)
        return _error_redirect(exc.code, intent)
    except Exception:  # noqa: BLE001 - the person must land somewhere that says so
        log.exception("[social] %s callback failed", provider)
        return _error_redirect("oauth_failed", intent)

    handoff = flow.issue_handoff(resolution.user, provider, resolution.is_new_account)
    # Fragment, not query: never sent to a server, so never in an access log
    # or a Referer header. And it is a one-time code, not a token.
    resp = RedirectResponse(f"{_frontend()}/auth/callback#code={handoff}", status_code=302)
    resp.delete_cookie(flow.BINDING_COOKIE, path=_COOKIE_PATH)
    return resp


@router.get("/oauth/{provider}/callback")
def oauth_callback_get(
    provider: str, request: Request,
    code: str | None = None, state: str | None = None, error: str | None = None,
):
    return _finish(provider, request, code=code, state=state, error=error)


@router.post("/oauth/{provider}/callback")
async def oauth_callback_post(provider: str, request: Request):
    """Apple's `response_mode=form_post`. Also accepted for any provider that
    POSTs, since the form carries the same fields."""
    form = await request.form()
    apple_user = None
    raw_user = form.get("user")
    if raw_user:
        try:
            apple_user = json.loads(str(raw_user))
        except ValueError:
            apple_user = None
    return _finish(
        provider, request,
        code=(str(form.get("code")) if form.get("code") else None),
        state=(str(form.get("state")) if form.get("state") else None),
        error=(str(form.get("error")) if form.get("error") else None),
        apple_user=apple_user,
    )


# ── Exchange ─────────────────────────────────────────────────────────────────

class ExchangeRequest(BaseModel):
    code: str = Field(..., min_length=10, max_length=200)


@router.post("/oauth/exchange")
def oauth_exchange(body: ExchangeRequest, request: Request):
    _rate("oauth-exchange", request)
    row = flow.consume_handoff(body.code)
    if not row:
        raise AppError(
            "oauth_exchange_invalid",
            "This sign-in link has expired or was already used. Sign in again.",
            status_code=400,
        )
    user = query_one("SELECT * FROM users WHERE id = %s", (row["user_id"],))
    if not user:
        raise AppError("oauth_exchange_invalid", "Account no longer exists.", status_code=400)
    try:
        flow.check_can_sign_in(user)
    except SocialAuthError as exc:
        raise AppError(
            exc.code, exc.detail or exc.code, status_code=403,
            params={"status": user.get("status") or ""},
        )

    user_svc.update_last_login(user["tenant_id"], user["id"])
    email_verified = bool(user.get("email_verified"))
    access_token = create_access_token(
        user["id"], user["tenant_id"], user["role"], email_verified=email_verified,
    )
    raw_refresh, hashed_refresh = create_refresh_token()
    user_svc.add_refresh_token(user["tenant_id"], user["id"], hashed_refresh)
    return ok({
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "bearer",
        "expires_in": 15 * 60,
        "provider": row["provider"],
        "is_new_account": bool(row["is_new_account"]),
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user.get("full_name"),
            "role": user["role"],
            "tenant_id": user["tenant_id"],
            "email_verified": email_verified,
        },
    })


# ── Linked identities (signed in) ────────────────────────────────────────────

@router.get("/identities")
def my_identities(user: CurrentUser = Depends(get_current_user)):
    rows = query(
        """SELECT provider, email, created_at, last_used_at FROM user_identities
            WHERE user_id = %s AND tenant_id = %s
              AND provider NOT LIKE 'sso:%%' ORDER BY created_at""",
        (user.user_id, user.tenant_id),
    )
    me = query_one(
        "SELECT has_password FROM users WHERE id = %s AND tenant_id = %s",
        (user.user_id, user.tenant_id),
    )
    return ok({
        "identities": [
            {
                "provider": r["provider"],
                "email": r["email"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            }
            for r in rows
        ],
        "has_password": bool((me or {}).get("has_password", True)),
        "providers_enabled": providers.enabled_providers(),
    })


@router.delete("/identities/{provider}")
def unlink_identity(provider: str, user: CurrentUser = Depends(get_current_user)):
    row = query_one(
        "SELECT id, email FROM user_identities WHERE user_id = %s AND tenant_id = %s AND provider = %s",
        (user.user_id, user.tenant_id, provider),
    )
    if not row:
        raise AppError("social_identity_not_found", "That provider is not linked.", status_code=404)
    me = query_one(
        "SELECT has_password FROM users WHERE id = %s AND tenant_id = %s",
        (user.user_id, user.tenant_id),
    )
    others = query_one(
        "SELECT COUNT(*) AS n FROM user_identities WHERE user_id = %s AND provider <> %s",
        (user.user_id, provider),
    )
    has_password = bool((me or {}).get("has_password", True))
    if not has_password and int((others or {}).get("n", 0)) == 0:
        # Removing the only way in would lock the person out of their own
        # account. They set a password first (Security -> change password).
        raise AppError(
            "social_identity_last_method",
            "This is your only way to sign in. Set a password before unlinking it.",
            status_code=409,
            params={"provider": provider},
        )
    execute("DELETE FROM user_identities WHERE id = %s", (row["id"],))
    from backend.activity.events import record_event

    record_event(
        user.tenant_id, user.user_id, "account.provider_unlinked",
        resource=provider, details={"provider": provider, "email": row["email"]},
        reason="unlinked_by_the_account_owner",
    )
    return ok({"unlinked": provider})
