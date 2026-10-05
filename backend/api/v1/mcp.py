"""`POST /api/v1/mcp` — the MCP endpoint a customer's AI client connects to.

Same key, same limit, same tenant. An `sk_live_*` key pasted into Claude (or any
other MCP client) reaches exactly the five read tools in `backend/mcp/catalog.py`
and nothing else — not because the client is trusted to stay inside them, but
because this endpoint offers nothing else to call.

What this deliberately does NOT do:

* **No session id.** The key is the session (`backend/mcp/protocol.py` says why).
* **No SSE.** `GET /mcp` answers 405, which the transport spec allows for a
  server with nothing to push. Every answer fits in the POST's response.
* **No OAuth.** StockAI's machine credential is a static key the customer generates
  in the app. A client that expects an OAuth dance gets a plain
  `WWW-Authenticate: Bearer` and a message naming the screen the key comes from.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from backend.auth.guards import CurrentUser, get_current_user
from backend.mcp import protocol

router = APIRouter(tags=["mcp"])
log = logging.getLogger(__name__)

_UNAUTHENTICATED_HEADERS = {"WWW-Authenticate": 'Bearer realm="stockai"'}


def _bearer_header_present(request: Request) -> None:
    """Turn a MISSING credential into a 401 before `HTTPBearer` makes it a 403.

    `get_current_user` does the real work below, and it is the right guard: the
    same key, the same rate limit, the same `CurrentUser` every other endpoint
    is written against. Its `HTTPBearer(auto_error=True)`, though, answers an
    ABSENT Authorization header with **403**, and an MCP client reads 403 as
    "this key is not allowed here" rather than "you sent no key" — which is the
    whole first minute of setting a connector up.

    So this runs first and only for the one case it can improve: no header at
    all. Everything else — a malformed token, a revoked key, one over its
    ceiling — falls through to `get_current_user` and answers exactly as it does
    on any REST call.

    It is a declared dependency rather than a call inside the handler for a
    reason worth writing down: `test_edge_cases.py` walks the app's route table
    and asks whether `get_current_user` is in each route's dependency tree.
    Authenticating by hand inside the body passes no audit — it just makes the
    route look unguarded, and the first version of this file did exactly that.
    """
    header = request.headers.get("authorization") or ""
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "This endpoint needs a StockAI API key. Generate one in the app "
                "at /automatizacion, under the API Keys tab, and send it as "
                "'Authorization: Bearer sk_live_...'."
            ),
            headers=_UNAUTHENTICATED_HEADERS,
        )


def mcp_user(
    _present: None = Depends(_bearer_header_present),
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """The caller, however they authenticated.

    An `sk_live_*` key is what a connector uses; a browser session works too,
    which is what makes the endpoint callable from the app's own `/api` console.

    MCP is a paid feature. A key is checked when it authenticates (the route
    names the feature, see `auth.guards._authenticate_api_key`); a browser
    session reaches the same endpoint without that path, so it is checked here.
    """
    if not user.is_machine:
        from backend.entitlements.service import ensure_feature
        ensure_feature(user.tenant_id, "mcp")
    return user


def _check_protocol_version(value: str | None) -> None:
    """Refuse a revision we do not speak, as the transport spec instructs.

    Absent is fine and means the client is on `2025-03-26` or has not finished
    initializing — both of which this server answers identically, because it
    keeps no per-connection state to answer differently with.
    """
    if value and value not in protocol.SUPPORTED_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported MCP protocol version {value!r}. "
                f"Supported: {', '.join(protocol.SUPPORTED_VERSIONS)}."
            ),
        )


@router.post("/mcp", summary="MCP server (JSON-RPC, read-only)")
async def mcp_endpoint(
    request: Request,
    user: CurrentUser = Depends(mcp_user),
    mcp_protocol_version: str | None = Header(default=None, alias="MCP-Protocol-Version"),
):
    """Model Context Protocol endpoint, for an AI client the customer runs.

    This is JSON-RPC 2.0, not a REST resource: the method lives in the body, so
    OpenAPI can say very little about it beyond the fact that it exists. Point
    an MCP client here with `Authorization: Bearer sk_live_…` and it will
    discover the tools itself with `tools/list`.

    Five tools, **all of them reads** — the planning context, the morning
    briefing, the stock signal per SKU, the data sources and a training run's
    status. Nothing here uploads, trains or records an order; those stay on the
    REST endpoints, where a person triggers them. `docs/public-api.md` and
    `mcp_server/README.md` are the customer-facing versions of this.
    """
    _check_protocol_version(mcp_protocol_version)

    raw = await request.body()
    if not raw:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=protocol.error_response(None, protocol.INVALID_REQUEST, "Empty request body"),
        )

    import json
    try:
        message = json.loads(raw)
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=protocol.error_response(None, protocol.PARSE_ERROR, "Invalid JSON"),
        )

    # A batch is an array. Batching was dropped from the protocol in 2025-06-18,
    # but an older client may still send one and answering it costs four lines.
    if isinstance(message, list):
        if not message:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=protocol.error_response(None, protocol.INVALID_REQUEST, "Empty batch"),
            )
        responses = [r for r in (protocol.dispatch(m, user) for m in message) if r is not None]
        if not responses:
            return Response(status_code=status.HTTP_202_ACCEPTED)
        return JSONResponse(content=responses)

    response = protocol.dispatch(message, user)
    if response is None:
        # A notification or a response: accepted, nothing to say back.
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return JSONResponse(content=response)


@router.get("/mcp", summary="MCP server-to-client stream (not offered)")
async def mcp_stream_not_offered(user: CurrentUser = Depends(mcp_user)):
    """The optional server->client SSE stream, declined explicitly.

    A 405 here is the spec's way of saying "this server has nothing to push",
    and a client that reads it stops waiting on a stream that would never carry
    anything. Answering 404 instead would read as "wrong URL" and send whoever
    is wiring it up to check their configuration.

    It asks for the key it has no use for so that no route on this server is
    open: the 405 is for a client that is wiring itself up, and a client wiring
    itself up has a key. A stranger gets 401, which is the more useful first
    thing to be told anyway.
    """
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail=(
            "This MCP server is stateless and does not offer a server-to-client "
            "stream. Send JSON-RPC messages with POST to this same URL."
        ),
    )
