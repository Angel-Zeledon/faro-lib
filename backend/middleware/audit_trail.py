"""Writes the audit row for every catalogued route that succeeded.

See `backend/audit/catalog.py` for the why. The actor comes from the request's
ASGI scope (`auth/actor_context.py`), set by the auth dependency: a ContextVar
would be invisible here, because BaseHTTPMiddleware runs the app in its own task.

Only successful calls are recorded — a refused or failed call changed nothing,
and a person hammering a forbidden button must not fill the trail. The row is
written AFTER the response exists and a failure to write it is logged, never
raised: an audit hiccup must not turn a completed change into a 500.
"""

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware

from backend.audit import read_note
from backend.audit.catalog import ROUTES, audit_action

log = logging.getLogger(__name__)

_API_PREFIX = "/api/v1"

# Notes are summaries. Anything bigger is a handler putting a document in a
# note, which would bloat the table; it is cut rather than rejected.
_MAX_SUMMARY_BYTES = 4000


def _clamp(value):
    if value is None:
        return None
    try:
        if len(json.dumps(value, default=str)) <= _MAX_SUMMARY_BYTES:
            return value
    except (TypeError, ValueError):
        pass
    return {"truncated": True}


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        try:
            self._record(request, response)
        except Exception as exc:  # noqa: BLE001
            log.warning("audit trail not recorded: %s", exc)
        return response

    @staticmethod
    def _record(request, response) -> None:
        if response.status_code >= 400:
            return
        route = request.scope.get("route")
        template = getattr(route, "path", None)
        if not template:
            return
        if template.startswith(_API_PREFIX):
            template = template[len(_API_PREFIX):]
        entry = ROUTES.get((request.method, template))
        if entry is None:
            return

        from backend.auth.actor_context import get_machine_actor, get_person_actor
        machine = get_machine_actor(request.scope)
        person = get_person_actor(request.scope)
        actor = machine or person
        if actor is None:
            return
        tenant_id, actor_id = actor

        note = read_note(request.scope)
        target_id = note.get("target_id")
        if target_id is None and entry.target_param:
            target_id = request.path_params.get(entry.target_param)

        context = {
            "target_type": entry.target_type,
            "target_id": str(target_id) if target_id is not None else None,
            "target_label": note.get("label"),
            "before": _clamp(note.get("before")),
            "after": _clamp(note.get("after")),
            "actor_kind": "api_key" if machine else "user",
            "method": request.method,
            "path": template,
            "status_code": response.status_code,
        }
        from backend.activity.service import log_action
        log_action(
            tenant_id=tenant_id, user_id=actor_id,
            action=audit_action(entry.action),
            resource=context["target_id"], context=context,
        )
