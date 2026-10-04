"""JSON-RPC 2.0 and the MCP lifecycle, over one stateless HTTP endpoint.

## Why this is written out rather than taken from the SDK

The official `mcp` package brings an ASGI app with its own lifespan, its own
session store and its own task groups, to be mounted inside a FastAPI app that
already has all three. What StockAI needs from the protocol is small and fixed:
five read-only tools, no sampling, no roots, no server-initiated messages, no
resumability. That is a handler for six methods, and it is testable with the
same `TestClient` as every other endpoint instead of needing a live socket.

The same reasoning is already on the record for DeepSeek, which is spoken over
plain httpx because the API is OpenAI-shaped and needs no SDK (CLAUDE.md).

## Stateless on purpose

No `Mcp-Session-Id` is issued or required. The `sk_live_*` key IS the session:
it identifies the tenant, carries the role and is rate limited, and every POST
brings it. That means any instance can serve any request, an instance restart
does not strand a client mid-conversation, and there is no session table to
grow. The cost is that the server cannot push anything to the client — which is
why `GET /mcp` answers 405 rather than opening an SSE stream. Nothing here has
anything to push.

## Versions

`SUPPORTED_VERSIONS` is newest-first. On `initialize` the client's requested
version is echoed back when we speak it, and `PREFERRED_VERSION` is returned
otherwise — the spec's own instruction, and it is what lets an older client keep
working instead of being refused at the door. On every later request the
`MCP-Protocol-Version` header is validated when present; a version we do not
speak is a 400, per the transport spec.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.auth.guards import CurrentUser
from backend.mcp import catalog

log = logging.getLogger(__name__)

SERVER_NAME = "stockai"
SERVER_TITLE = "StockAI — inventory purchasing decisions"

# Newest first. The spec revision this was written against is 2025-11-25; the
# older entries are the revisions real clients still send.
SUPPORTED_VERSIONS: tuple[str, ...] = (
    "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05",
)
PREFERRED_VERSION = SUPPORTED_VERSIONS[0]

# A client that sends no `MCP-Protocol-Version` header is assumed by the spec to
# speak `2025-03-26`. That is not a constant here because nothing branches on
# it: this server keeps no per-connection state, so it answers every supported
# revision the same way.

# JSON-RPC error codes. The first four are the standard's; -32002 is MCP's
# "resource not found", unused here, and is listed so nobody reuses the number.
PARSE_ERROR      = -32700
INVALID_REQUEST  = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS   = -32602
INTERNAL_ERROR   = -32603


def server_version() -> str:
    from backend.config import settings
    return getattr(settings, "app_version", None) or "1.0.0"


# ── JSON-RPC plumbing ────────────────────────────────────────────────────────

def _result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    body: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": body}


def _tool_failure(message: str, code: str | None = None) -> dict:
    """A tool that could not answer, reported so the model can react.

    Deliberately NOT a JSON-RPC error: the spec reserves those for protocol
    faults (no such tool, malformed frame). A business refusal — no completed
    session yet, an unknown session id — is something the model should read and
    explain to the person, and a transport-level error is invisible to it.
    """
    text = message if not code else f"{message} (code: {code})"
    return {
        "content": [{"type": "text", "text": text}],
        "isError": True,
    }


# ── Method handlers ──────────────────────────────────────────────────────────

def _handle_initialize(params: dict) -> dict:
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_VERSIONS else PREFERRED_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {
            # listChanged is false and stated: the catalogue is a constant in
            # source, so a client that polls for changes would poll forever.
            "tools": {"listChanged": False},
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "title": SERVER_TITLE,
            "version": server_version(),
        },
        "instructions": (
            "StockAI decides what a distributor should buy. Call "
            "get_planning_context first: it names the active forecast session "
            "and the granularity everything else is expressed in. Then "
            "get_morning_briefing for 'what should I buy today', or "
            "get_inventory_status for the state of specific products.\n\n"
            "Two things to carry into any answer you give. SIN_DATOS means "
            "there is no stock on record — unknown, not safe. And each row says "
            "where its lead time, unit cost and MOQ came from: a `default` is "
            "an assumption StockAI made, not a number the customer gave, and "
            "should be reported as such.\n\n"
            "Every tool here reads. StockAI will not upload data, start a training "
            "run or record a purchase order through this connection — those "
            "stay on the REST API, where a person triggers them."
        ),
    }


def _handle_tools_call(params: dict, user: CurrentUser) -> dict:
    name = params.get("name")
    tool = catalog.BY_NAME.get(name)
    if tool is None:
        # Protocol-level: the client asked for something that does not exist.
        raise _MethodError(
            INVALID_PARAMS, f"Unknown tool: {name!r}",
            {"available": sorted(catalog.BY_NAME)},
        )

    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise _MethodError(INVALID_PARAMS, "`arguments` must be an object")

    from backend.errors import AppError
    from fastapi import HTTPException

    try:
        data = tool.handler(user, arguments)
    except AppError as exc:
        # The product's own refusals, with their stable code attached so the
        # model can distinguish "train something first" from "wrong id".
        return _tool_failure(exc.message, exc.code)
    except HTTPException as exc:
        return _tool_failure(str(exc.detail))
    except ValueError as exc:
        return _tool_failure(str(exc))
    except Exception:
        # Anything unforeseen is logged with its traceback and reported as a
        # tool failure rather than a 500: a broken tool must not take the
        # client's whole connection down with it.
        log.exception("[mcp] tool %s failed", name)
        return _tool_failure(
            "StockAI could not complete that request. The failure was logged on "
            "the server."
        )

    # Through FastAPI's encoder, not straight out. The REST endpoints get this
    # for free because FastAPI serialises their return value; this endpoint
    # builds its own JSONResponse, and Starlette's plain `json.dumps` raises on
    # the first `datetime` it meets — which every `created_at` in the product
    # is. Measured: `list_data_sources` took the whole connection down with a
    # 500 that named nothing.
    #
    # Encoding ONCE and deriving both halves from the result is also what keeps
    # the text block and `structuredContent` identical. Serialising them
    # separately is how a client that reads one gets different numbers from a
    # client that reads the other.
    from fastapi.encoders import jsonable_encoder
    payload = jsonable_encoder(data)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": False,
    }


class _MethodError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def dispatch(message: Any, user: CurrentUser) -> dict | None:
    """One JSON-RPC frame in, one response out — or None for a notification.

    A notification (no `id`) gets no response by the standard, and the caller
    turns that None into the 202 the transport asks for.
    """
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return error_response(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message")

    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return error_response(request_id, INVALID_PARAMS, "`params` must be an object")

    if method is None:
        # A response to something we sent. We never send requests, so there is
        # nothing this can be answering.
        return None

    is_notification = "id" not in message

    try:
        if method == "initialize":
            result = _handle_initialize(params)
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": catalog.descriptors()}
        elif method == "tools/call":
            result = _handle_tools_call(params, user)
        elif method.startswith("notifications/"):
            # initialized, cancelled, progress — nothing here reacts to any of
            # them, and a stateless server has no state to update.
            return None
        else:
            if is_notification:
                return None
            return error_response(
                request_id, METHOD_NOT_FOUND, f"Method not found: {method}",
                {"supported": ["initialize", "ping", "tools/list", "tools/call"]},
            )
    except _MethodError as exc:
        return None if is_notification else error_response(
            request_id, exc.code, exc.message, exc.data)
    except Exception:
        log.exception("[mcp] dispatch failed for method=%s", method)
        return None if is_notification else error_response(
            request_id, INTERNAL_ERROR, "Internal error")

    return None if is_notification else _result(request_id, result)
