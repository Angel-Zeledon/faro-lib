"""SCIM 2.0 provisioning routes, and the admin routes that manage its token.

Protocol (tag `scim`, INTERNAL in public_surface.py - an API key never gets in):

    GET    /scim/v2/ServiceProviderConfig
    GET    /scim/v2/ResourceTypes[/{name}]
    GET    /scim/v2/Schemas[/{urn}]
    GET    /scim/v2/Users                 filter=userName eq "x", startIndex, count
    POST   /scim/v2/Users
    GET    /scim/v2/Users/{id}
    PUT    /scim/v2/Users/{id}
    PATCH  /scim/v2/Users/{id}
    DELETE /scim/v2/Users/{id}            deactivates; nothing is deleted
    GET    /scim/v2/Groups                the three roles
    POST   /scim/v2/Groups                refused (roles are fixed)
    GET    /scim/v2/Groups/{id}
    PUT    /scim/v2/Groups/{id}           full membership
    PATCH  /scim/v2/Groups/{id}           add / remove / replace members
    DELETE /scim/v2/Groups/{id}           refused

Every protocol answer - success or refusal - is `application/scim+json`, and
every refusal is a SCIM Error body (never this API's usual envelope), because
the caller is an identity provider that parses exactly that.

Administration (tag `auth`, tenant admins, JWT):

    GET    /auth/sso/scim                 status, base URL, token metadata, log
    POST   /auth/sso/scim/token           mint or rotate (the token is shown ONCE)
    PATCH  /auth/sso/scim/token           allow / forbid managing administrators
    DELETE /auth/sso/scim/token           revoke
    GET    /auth/sso/scim/events          the provisioning log, paginated
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.activity.events import record_event
from backend.api.v1.trial import _client_address
from backend.auth.guards import CurrentUser, require_admin
from backend.errors import AppError
from backend.schemas.common import ok
from backend.scim import protocol, service, tokens
from backend.scim.protocol import MEDIA_TYPE, ScimError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/scim/v2", tags=["scim"])
admin_router = APIRouter(prefix="/auth/sso/scim", tags=["auth"])

MAX_BODY_BYTES = 1_000_000


# ── Plumbing ─────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, status: int, body: Optional[dict] = None,
                 headers: Optional[dict[str, str]] = None):
        self.status, self.body, self.headers = status, body, headers or {}


def _scim_json(status: int, body: dict, headers: Optional[dict] = None) -> Response:
    return Response(
        content=json.dumps(body, default=str), status_code=status,
        media_type=MEDIA_TYPE, headers=headers or {},
    )


def _error(err: ScimError) -> Response:
    headers = dict(err.headers)
    if err.status == 401:
        headers.setdefault("WWW-Authenticate", 'Bearer realm="scim"')
    return _scim_json(err.status, err.body(), headers)


def _authenticate(request: Request) -> service.ScimContext:
    """A live SCIM token for a tenant whose SSO is ready - or a ScimError.

    Runs in the worker thread (it touches the database). Accepts ONLY a SCIM
    token: a JWT or an `sk_live_*` key is a 401 here, as a SCIM token is a 401
    everywhere else.
    """
    address = _client_address(request)
    if service.auth_failures_exceeded(address):
        raise ScimError(429, "Too many failed attempts. Try again later.",
                        code="scim_too_many_attempts",
                        headers={"Retry-After": str(service.AUTH_FAILURE_WINDOW_SECS)})
    header = request.headers.get("authorization") or ""
    scheme, _, credential = header.partition(" ")
    row = tokens.authenticate(credential.strip()) if scheme.lower() == "bearer" else None
    if row is None:
        service.record_auth_failure(address)
        raise ScimError(401, "A valid SCIM bearer token is required.", code="scim_unauthorized")
    ctx = service.context_for(row)
    if not service.token_within_rate(row["id"]):
        raise ScimError(429, "Too many requests for this token.", code="scim_rate_limited",
                        headers={"Retry-After": "60"})
    tokens.touch(row["id"])
    return ctx


async def _read_raw(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ScimError(413, "The request body is too large.", code="scim_body_too_large")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ScimError(413, "The request body is too large.", code="scim_body_too_large")
    return raw


def _parse_body(raw: bytes) -> dict:
    try:
        body = json.loads(raw or b"null")
    except ValueError:
        raise protocol.invalid_syntax("The request body is not valid JSON.")
    if not isinstance(body, dict):
        raise protocol.invalid_syntax("The request body must be a JSON object.")
    return body


async def _handle(request: Request, work: Callable[[service.ScimContext, Any], _Result],
                  *, with_body: bool = False) -> Response:
    from backend.db.connection import PoolExhausted
    try:
        raw = await _read_raw(request) if with_body else b""

        def run() -> _Result:
            # Authenticated BEFORE the body is looked at: an anonymous caller
            # learns nothing from how its payload is judged.
            ctx = _authenticate(request)
            return work(ctx, _parse_body(raw) if with_body else None)

        result = await run_in_threadpool(run)
    except ScimError as err:
        return _error(err)
    except PoolExhausted:
        return _error(ScimError(503, "The service is busy. Retry shortly.",
                                code="server_busy", headers={"Retry-After": "2"}))
    except Exception:  # noqa: BLE001 - the IdP must get a SCIM body, not our envelope
        log.exception("[scim] %s %s failed", request.method, request.url.path)
        return _error(ScimError(500, "An unexpected error occurred.", code="internal_error"))
    if result.status == 204:
        return Response(status_code=204, headers=result.headers)
    return _scim_json(result.status, result.body or {}, result.headers)


def _user_result(ctx: service.ScimContext, row: dict, status: int = 200) -> _Result:
    resource = protocol.user_resource(row, ctx.base_url)
    headers = {"ETag": resource["meta"]["version"]}
    if status == 201:
        headers["Location"] = resource["meta"]["location"]
    return _Result(status, resource, headers)


# ── Discovery ────────────────────────────────────────────────────────────────

@router.get("/ServiceProviderConfig")
async def service_provider_config(request: Request):
    return await _handle(request, lambda ctx, _: _Result(
        200, protocol.service_provider_config(ctx.base_url)))


@router.get("/ResourceTypes")
async def resource_types(request: Request):
    def work(ctx, _):
        items = protocol.resource_types(ctx.base_url)
        return _Result(200, protocol.list_response(items, len(items), 1))
    return await _handle(request, work)


@router.get("/ResourceTypes/{name}")
async def resource_type(name: str, request: Request):
    def work(ctx, _):
        for item in protocol.resource_types(ctx.base_url):
            if item["id"].lower() == name.lower():
                return _Result(200, item)
        raise ScimError(404, "No such resource type.", code="scim_resource_type_not_found")
    return await _handle(request, work)


@router.get("/Schemas")
async def schemas(request: Request):
    def work(ctx, _):
        items = protocol.schemas(ctx.base_url)
        return _Result(200, protocol.list_response(items, len(items), 1))
    return await _handle(request, work)


@router.get("/Schemas/{urn}")
async def schema(urn: str, request: Request):
    def work(ctx, _):
        for item in protocol.schemas(ctx.base_url):
            if item["id"] == urn:
                return _Result(200, item)
        raise ScimError(404, "No such schema.", code="scim_schema_not_found")
    return await _handle(request, work)


# ── Users ────────────────────────────────────────────────────────────────────

@router.get("/Users")
async def list_users(
    request: Request, filter: Optional[str] = None,  # noqa: A002 - SCIM's own name
    startIndex: Optional[str] = None, count: Optional[str] = None,  # noqa: N803
):
    return await _handle(request, lambda ctx, _: _Result(
        200, service.list_users(ctx, filter, startIndex, count)))


@router.post("/Users")
async def create_user(request: Request):
    return await _handle(request, lambda ctx, body: _user_result(
        ctx, service.create_user(ctx, body), 201), with_body=True)


@router.get("/Users/{user_id}")
async def get_user(user_id: str, request: Request):
    return await _handle(request, lambda ctx, _: _user_result(
        ctx, service.get_user(ctx, user_id)))


@router.put("/Users/{user_id}")
async def replace_user(user_id: str, request: Request):
    if_match = request.headers.get("if-match")
    return await _handle(request, lambda ctx, body: _user_result(
        ctx, service.replace_user(ctx, user_id, body, if_match)), with_body=True)


@router.patch("/Users/{user_id}")
async def patch_user(user_id: str, request: Request):
    if_match = request.headers.get("if-match")
    return await _handle(request, lambda ctx, body: _user_result(
        ctx, service.patch_user(ctx, user_id, body, if_match)), with_body=True)


@router.delete("/Users/{user_id}")
async def delete_user(user_id: str, request: Request):
    if_match = request.headers.get("if-match")

    def work(ctx, _):
        service.deactivate_user(ctx, user_id, if_match)
        return _Result(204)
    return await _handle(request, work)


# ── Groups ───────────────────────────────────────────────────────────────────

@router.get("/Groups")
async def list_groups(
    request: Request, filter: Optional[str] = None,  # noqa: A002
    startIndex: Optional[str] = None, count: Optional[str] = None,  # noqa: N803
    excludedAttributes: Optional[str] = None,  # noqa: N803
):
    return await _handle(request, lambda ctx, _: _Result(
        200, service.list_groups(ctx, filter, startIndex, count, excludedAttributes)))


@router.post("/Groups")
async def create_group(request: Request):
    def work(ctx, body):
        service.create_group(ctx, body)
        return _Result(500)  # unreachable: create_group always refuses
    return await _handle(request, work, with_body=True)


@router.get("/Groups/{group_id}")
async def get_group(group_id: str, request: Request,
                    excludedAttributes: Optional[str] = None):  # noqa: N803
    def work(ctx, _):
        resource = service.get_group(ctx, group_id, excludedAttributes)
        return _Result(200, resource, {"ETag": resource["meta"]["version"]})
    return await _handle(request, work)


@router.put("/Groups/{group_id}")
async def replace_group(group_id: str, request: Request):
    def work(ctx, body):
        resource = service.replace_group(ctx, group_id, body)
        return _Result(200, resource, {"ETag": resource["meta"]["version"]})
    return await _handle(request, work, with_body=True)


@router.patch("/Groups/{group_id}")
async def patch_group(group_id: str, request: Request):
    def work(ctx, body):
        service.patch_group(ctx, group_id, body)
        return _Result(204)
    return await _handle(request, work, with_body=True)


@router.delete("/Groups/{group_id}")
async def delete_group(group_id: str, request: Request):
    def work(ctx, _):
        service.delete_group(ctx, group_id)
        return _Result(500)  # unreachable: delete_group always refuses
    return await _handle(request, work)


# ── Administration ───────────────────────────────────────────────────────────

class TokenBody(BaseModel):
    manage_admins: bool = False


def _require_unscoped(user: CurrentUser) -> None:
    """The token creates people who see every warehouse (a new user has no
    scope) and may manage administrators. An administrator limited to some
    warehouses cannot widen anybody's access on the users screen, so they
    cannot hand that power to an identity provider either."""
    from backend.auth import warehouse_scope as wscope
    if wscope.is_scoped(user):
        raise AppError(
            "scim_scoped_admin_forbidden",
            "Only an administrator with access to every warehouse can manage provisioning.",
            status_code=403,
        )


@admin_router.get("")
def scim_status(user: CurrentUser = Depends(require_admin)):
    return ok(service.admin_status(user.tenant_id))


@admin_router.post("/token")
def mint_token(body: TokenBody, user: CurrentUser = Depends(require_admin)):
    """Mint a token, or rotate: the previous one stops working at once."""
    _require_unscoped(user)
    if not service.sso_row_ready(user.tenant_id):
        raise AppError(
            "scim_requires_sso",
            "Configure and enable company sign-in before turning on provisioning.",
            status_code=409,
        )
    raw, public, rotated = tokens.create(user.tenant_id, user.user_id,
                                         manage_admins=body.manage_admins)
    record_event(
        user.tenant_id, user.user_id, "account.scim_token_created", resource="scim",
        details={"manage_admins": body.manage_admins, "rotated": rotated},
        reason="changed_by_an_account_admin",
    )
    # The only time the token ever leaves the server.
    return ok({"token": raw, "token_info": public, "rotated": rotated,
               "base_url": service.base_url()})


@admin_router.patch("/token")
def update_token(body: TokenBody, user: CurrentUser = Depends(require_admin)):
    _require_unscoped(user)
    public = tokens.set_manage_admins(user.tenant_id, body.manage_admins)
    if not public:
        raise AppError("scim_token_not_found", "Provisioning is not turned on.",
                       status_code=404)
    record_event(
        user.tenant_id, user.user_id, "account.scim_settings_changed", resource="scim",
        details={"manage_admins": body.manage_admins},
        reason="changed_by_an_account_admin",
    )
    return ok({"token_info": public})


@admin_router.delete("/token")
def revoke_token(user: CurrentUser = Depends(require_admin)):
    if not tokens.revoke(user.tenant_id, user.user_id):
        raise AppError("scim_token_not_found", "Provisioning is not turned on.",
                       status_code=404)
    record_event(
        user.tenant_id, user.user_id, "account.scim_token_revoked", resource="scim",
        reason="changed_by_an_account_admin",
    )
    return ok({"revoked": True})


@admin_router.get("/events")
def scim_events(
    user: CurrentUser = Depends(require_admin),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return ok(service.recent_events(user.tenant_id, limit, offset))
