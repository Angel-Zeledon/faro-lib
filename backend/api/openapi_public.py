"""The API-key surface, written into the OpenAPI document.

`backend/api/public_surface.py` decides which operations a key may call. This
module makes that decision visible in `/openapi.json`, so the developer
reference (`backend/scripts/export_public_api.py` → the landing's
/desarrolladores page) is generated from the running app rather than described
beside it:

* every exposed operation carries `x-stockai-api: {"scope": "read"|"write"}`;
* a JSON success response that declares no model of its own points at the
  shared `ApiEnvelope` (`{success, data, meta}`, see `backend/schemas/common.py`);
* the error answers every key-authenticated call can get (401, 403, 429) point
  at `ApiError` (`{detail, error_code, error_params}`, see `backend/main.py`).

Annotation only: nothing here changes what a route does or returns.
"""
from __future__ import annotations

from backend.api.public_docs import DOCS, SUMMARIES
from backend.api.public_surface import API_PREFIX, exposure

API_ENVELOPE = {
    "title": "ApiEnvelope",
    "type": "object",
    "description": "Every JSON success response is wrapped: the payload is in `data`.",
    "properties": {
        "success": {"type": "boolean", "example": True},
        "data": {"description": "The endpoint's payload."},
        "meta": {
            "type": "object",
            "properties": {"timestamp": {"type": "string", "format": "date-time"}},
        },
    },
    "required": ["success", "data"],
}

API_ERROR = {
    "title": "ApiError",
    "type": "object",
    "description": (
        "Every error. Branch on `error_code` when there is one (stable, snake_case; "
        "a few older limit errors spell it in capitals, e.g. `PLAN_LIMIT_REACHED`); "
        "`detail` is an English fallback and may change. Even the framework "
        "answers carry one: `unauthenticated` (401, missing or malformed "
        "Authorization header), `not_found` (404, unknown route), "
        "`method_not_allowed` (405) and `rate_limited` (429)."
    ),
    "properties": {
        "detail": {"description": (
            "English fallback message. On 422 it is a list of {type, loc, msg, input} "
            "objects, one per invalid field; on `PLAN_LIMIT_REACHED` it is an object "
            "that repeats `error_params`.")},
        "error_code": {"type": "string", "example": "api_key_scope_insufficient"},
        "error_params": {"type": "object", "description": "The values the message is about."},
    },
    "required": ["detail"],
}

_KEY_ERRORS = {
    "401": "API key missing (`unauthenticated`), invalid, revoked or expired (`api_key_invalid`).",
    "403": (
        "The key may not call this endpoint (`api_key_route_not_exposed`), is "
        "read-only on a write (`api_key_scope_insufficient`), or another "
        "permission check refused it."
    ),
    "429": "Per-minute rate limit or the plan's daily ceiling reached (`rate_limited`). Honour `Retry-After` (seconds).",
}


def annotate(app, schema: dict) -> dict:
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.setdefault("ApiEnvelope", API_ENVELOPE)
    components.setdefault("ApiError", API_ERROR)
    paths = schema.get("paths", {})
    for route in app.routes:
        exp = exposure(route)
        if not exp.exposed:
            continue
        # `path_format`, not `path`: OpenAPI writes `{sku}` where the route
        # declares the converter `{sku:path}`.
        item = paths.get(route.path_format)
        if not item:
            continue
        for method in route.methods or ():
            op = item.get(method.lower())
            if op is None:
                continue
            op["x-stockai-api"] = {"scope": exp.scope}
            doc = DOCS.get((method.upper(), route.path_format[len(API_PREFIX):]))
            if doc:
                op["summary"], op["description"] = doc
            elif (method.upper(), route.path_format[len(API_PREFIX):]) in SUMMARIES:
                op["summary"] = SUMMARIES[(method.upper(), route.path_format[len(API_PREFIX):])]
            responses = op.setdefault("responses", {})
            for code, response in responses.items():
                if not str(code).startswith("2"):
                    continue
                content = response.get("content", {}).get("application/json")
                if content is not None and not content.get("schema"):
                    content["schema"] = {"$ref": "#/components/schemas/ApiEnvelope"}
            for code, description in _KEY_ERRORS.items():
                responses.setdefault(code, {
                    "description": description,
                    "content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/ApiError"},
                    }},
                })
    return schema


def install(app) -> None:
    """Wrap `app.openapi` so the generated document carries the annotations."""
    generate = app.openapi

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = generate()
        annotate(app, schema)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi


__all__ = ["annotate", "install", "API_PREFIX"]
