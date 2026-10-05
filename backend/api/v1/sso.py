"""Enterprise single sign-on routes (OpenID Connect, one provider per tenant).

All under `/auth/sso` with tag `auth`, which an API key can never reach.

    GET    /auth/sso/availability    does this installation offer it at all (public)
    POST   /auth/sso/discover        work e-mail -> is there a company sign-in (public)
    GET    /auth/sso/start           302 to the tenant's provider (public)
    GET    /auth/sso/callback        the provider comes back
    GET    /auth/sso/config          the tenant's configuration (admin)
    PUT    /auth/sso/config          save it, after discovery (admin)
    DELETE /auth/sso/config          remove it (admin)

The callback ends exactly where social login does: a redirect to
`/auth/callback#code=...`, which the page trades at `/auth/oauth/exchange` for
the app's own access and refresh tokens. The design is in
`backend/auth/sso/__init__.py`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.api.v1.auth import _check_rate
from backend.api.v1.trial import _client_address
from backend.auth.guards import CurrentUser, require_admin
from backend.auth.social import flow
from backend.auth.social.providers import SocialAuthError
from backend.auth.sso import service
from backend.config import settings
from backend.errors import AppError
from backend.schemas.common import ok
from backend.service_config import crypto

router = APIRouter(prefix="/auth/sso", tags=["auth"])
log = logging.getLogger(__name__)

_COOKIE_PATH = "/api/v1/auth/sso"


def _frontend() -> str:
    return settings.frontend_url.rstrip("/")


def redirect_uri() -> str:
    """What the tenant's IdP administrator registers - sent byte for byte on
    both legs of the exchange."""
    return f"{_frontend()}/api/v1/auth/sso/callback"


def _error_redirect(code: str) -> RedirectResponse:
    resp = RedirectResponse(f"{_frontend()}/login?oauth_error={code}", status_code=302)
    resp.delete_cookie(flow.BINDING_COOKIE, path=_COOKIE_PATH)
    return resp


def _rate(key: str, request: Request) -> None:
    _check_rate(f"{key}:{_client_address(request)}", max_attempts=20, window_secs=300)


# ── Public ───────────────────────────────────────────────────────────────────

@router.get("/availability")
def availability():
    return ok({"enabled": service.instance_enabled()})


class DiscoverRequest(BaseModel):
    email: str = Field(..., max_length=320)


@router.post("/discover")
def discover(body: DiscoverRequest, request: Request):
    """Whether this work e-mail signs in through a company provider.

    Answers `available: false` for an unknown domain, a disabled provider and a
    switched-off installation alike: the login page then shows the password
    form and there is nothing to tell the cases apart for.
    """
    _rate("sso-discover", request)
    row = service.active_row_for_domain(service.email_domain(body.email))
    return ok({
        "available": bool(row),
        "enforced": bool(row and row["enforce_sso"]),
    })


@router.get("/start")
def start(request: Request, email: str = ""):
    try:
        _rate("sso-start", request)
    except AppError:
        return _error_redirect("too_many_attempts")
    try:
        url, started = service.begin(email, redirect_uri())
    except SocialAuthError as exc:
        return _error_redirect(exc.code)
    except Exception:  # noqa: BLE001 - the person must land somewhere that says so
        log.exception("[sso] start failed")
        return _error_redirect("sso_failed")

    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        flow.BINDING_COOKIE, started.binding,
        max_age=flow.FLOW_TTL_MINUTES * 60, path=_COOKIE_PATH, httponly=True,
        secure=_frontend().startswith("https://"), samesite="lax",
    )
    return resp


@router.get("/callback")
def callback(
    request: Request, code: str | None = None, state: str | None = None,
    error: str | None = None,
):
    try:
        _rate("sso-callback", request)
    except AppError:
        return _error_redirect("too_many_attempts")

    tenant_id = service.peek_tenant(state)
    try:
        user, created = service.complete(
            state, request.cookies.get(flow.BINDING_COOKIE), code, error, redirect_uri(),
        )
    except SocialAuthError as exc:
        # The detail can name an address or an upstream status; the log keeps
        # the code, the tenant's activity feed keeps who was refused and why.
        log.info("[sso] callback refused: %s", exc.code)
        if exc.code not in ("oauth_state_invalid", "sso_cancelled"):
            service.record_refusal(tenant_id, exc.code)
        return _error_redirect(exc.code)
    except Exception:  # noqa: BLE001
        log.exception("[sso] callback failed")
        return _error_redirect("sso_failed")

    handoff = flow.issue_handoff(user, "sso", created)
    resp = RedirectResponse(f"{_frontend()}/auth/callback#code={handoff}", status_code=302)
    resp.delete_cookie(flow.BINDING_COOKIE, path=_COOKIE_PATH)
    return resp


# ── Administration ───────────────────────────────────────────────────────────

class SsoConfigBody(BaseModel):
    issuer: str = Field(..., max_length=2048)
    client_id: str = Field(..., max_length=512)
    # Absent or empty on an update means "keep the stored one".
    client_secret: str | None = Field(None, max_length=1024)
    allowed_domains: list[str] = Field(..., max_length=50)
    default_role: str = Field("viewer", max_length=20)
    enforce_sso: bool = False
    groups_claim: str | None = Field(None, max_length=100)
    group_roles: dict[str, str] = Field(default_factory=dict, max_length=200)
    enabled: bool = True


@router.get("/config")
def get_config(user: CurrentUser = Depends(require_admin)):
    return ok({
        "instance_enabled": service.instance_enabled(),
        "secret_storage": crypto.secret_storage_enabled(),
        "redirect_uri": redirect_uri(),
        "config": service.public_view(service.get_row(user.tenant_id)),
    })


@router.put("/config")
def put_config(body: SsoConfigBody, user: CurrentUser = Depends(require_admin)):
    cfg = service.save_config(
        user.tenant_id, user.user_id,
        issuer=body.issuer, client_id=body.client_id, client_secret=body.client_secret,
        allowed_domains=body.allowed_domains, default_role=body.default_role,
        enforce_sso=body.enforce_sso, groups_claim=body.groups_claim,
        group_roles=body.group_roles, enabled=body.enabled,
    )
    record_event(
        user.tenant_id, user.user_id, "account.sso_config_changed",
        resource="sso",
        details={"issuer": cfg["issuer"], "enabled": cfg["enabled"],
                 "enforce_sso": cfg["enforce_sso"],
                 "domains": ", ".join(cfg["allowed_domains"])},
        reason="changed_by_an_account_admin",
    )
    return ok({"config": cfg})


@router.delete("/config")
def delete_config(user: CurrentUser = Depends(require_admin)):
    existing = service.get_row(user.tenant_id)
    if not existing:
        raise AppError("sso_not_configured", "No company sign-in is configured.",
                       status_code=404)
    service.delete_config(user.tenant_id)
    record_event(
        user.tenant_id, user.user_id, "account.sso_config_removed", resource="sso",
        details={"issuer": existing["issuer"]}, reason="changed_by_an_account_admin",
    )
    return ok({"removed": True})
