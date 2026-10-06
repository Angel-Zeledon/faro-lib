"""SAML 2.0 sign-in endpoints (SP-initiated), the sibling of `/auth/sso`.

    GET  /auth/saml/start?email=   302 to the tenant's identity provider (public)
    POST /auth/saml/acs            the provider posts the signed Response here (public)

The configuration routes (`/auth/saml/config`, `/auth/saml/sp-metadata`) are
served by the Rust API only (docs/rust-migration.md, "SAML"): they have no
Python implementation. Everything here ends in session issuance, which stays
Python, and runs the assertion validation in `backend/auth/saml/`.

Tag `auth`: an API key can never reach these.

The ACS answers with a 303 to `/auth/callback#code=...`, the same one-time
handoff social and OIDC sign-in use; `/auth/oauth/exchange` trades it for the
app's own tokens. A failure redirects to `/login?oauth_error=<code>`.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from backend.api.v1.auth import _check_rate
from backend.api.v1.trial import _client_address
from backend.auth.saml import service
from backend.auth.social import flow
from backend.auth.social.providers import SocialAuthError
from backend.auth.sso import service as sso_service
from backend.config import settings
from backend.errors import AppError

router = APIRouter(prefix="/auth/saml", tags=["auth"])
log = logging.getLogger(__name__)

_COOKIE_PATH = "/api/v1/auth/saml"
# The base64 of the largest document `xmlsig` accepts, plus the other fields.
_MAX_FORM_BYTES = 768 * 1024


def _frontend() -> str:
    return settings.frontend_url.rstrip("/")


def _error_redirect(code: str, status_code: int = 302) -> RedirectResponse:
    """302 for the GET (`/start`); the ACS passes 303, so the browser that just
    POSTed to us follows with a GET and never re-sends the SAML response."""
    resp = RedirectResponse(f"{_frontend()}/login?oauth_error={code}", status_code=status_code)
    resp.delete_cookie(flow.BINDING_COOKIE, path=_COOKIE_PATH)
    return resp


def _rate(key: str, request: Request) -> None:
    _check_rate(f"{key}:{_client_address(request)}", max_attempts=20, window_secs=300)


@router.get("/start")
def start(request: Request, email: str = ""):
    try:
        _rate("saml-start", request)
    except AppError:
        return _error_redirect("too_many_attempts")
    try:
        url, started = service.begin(email)
    except SocialAuthError as exc:
        return _error_redirect(exc.code)
    except Exception:  # noqa: BLE001 - the person must land somewhere that says so
        log.exception("[saml] start failed")
        return _error_redirect("sso_failed")

    resp = RedirectResponse(url, status_code=302)
    secure = _frontend().startswith("https://")
    resp.set_cookie(
        flow.BINDING_COOKIE, started.binding,
        max_age=flow.FLOW_TTL_MINUTES * 60, path=_COOKIE_PATH, httponly=True,
        secure=secure,
        # The ACS request is a cross-site POST from the identity provider; a
        # Lax cookie is not sent on it. SameSite=None requires Secure, which
        # an https deployment has (an http one is a development setup).
        samesite="none" if secure else "lax",
    )
    return resp


async def _read_form(request: Request) -> dict[str, str] | None:
    """The urlencoded body, capped while it streams in. None when it is not
    the form the binding defines."""
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return None
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_FORM_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_FORM_BYTES:
            return None
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=20,
                          strict_parsing=False)
    except (UnicodeDecodeError, ValueError):
        return None
    # A repeated field is ambiguous: refuse rather than pick one.
    if any(len(v) != 1 for v in parsed.values()):
        return None
    return {k: v[0] for k, v in parsed.items()}


@router.post("/acs")
async def acs(request: Request):
    try:
        _rate("saml-acs", request)
    except AppError:
        return _error_redirect("too_many_attempts", 303)

    form = await _read_form(request)
    if form is None:
        return _error_redirect("saml_response_invalid", 303)
    relay_state = form.get("RelayState")
    tenant_id = service.peek_tenant(relay_state)
    try:
        user, created = service.complete(
            relay_state, request.cookies.get(flow.BINDING_COOKIE), form.get("SAMLResponse"),
        )
    except SocialAuthError as exc:
        log.info("[saml] acs refused: %s", exc.code)
        if exc.code != "oauth_state_invalid":
            sso_service.record_refusal(tenant_id, exc.code)
        return _error_redirect(exc.code, 303)
    except Exception:  # noqa: BLE001
        log.exception("[saml] acs failed")
        return _error_redirect("sso_failed", 303)

    handoff = flow.issue_handoff(user, "sso", created)
    resp = RedirectResponse(f"{_frontend()}/auth/callback#code={handoff}", status_code=303)
    resp.delete_cookie(flow.BINDING_COOKIE, path=_COOKIE_PATH)
    return resp
