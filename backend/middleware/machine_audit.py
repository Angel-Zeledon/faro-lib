"""Every write an integration performs leaves a row.

The question a customer asks the morning after is "what did my integration do
last night?", and until this existed the honest answer was: partly. A training
run was attributable, because the job stores `created_by = api_key:<id>`. The
file upload that fed it and the purchase order that closed the loop left nothing
at all.

Auditing here rather than inside each endpoint is deliberate. Three endpoints
needed it today; the fourth one added next month would be added by somebody who
never read this file, and the gap would reopen silently. A middleware cannot be
forgotten.

Only writes, only machines, only successes:

- Reads are not audited. A GET that changed nothing is noise, and at 120 calls a
  minute the noise would bury the signal. `POST /api/v1/mcp` counts as a read
  for the same reason and is exempted by path — see the constant below.
- People are not audited here. Their actions are already attributable through
  their session, and logging every UI click would drown the trail.
- Refused calls are not audited: 401/403/429 mean nothing happened. A failed
  WRITE (4xx from validation, 5xx) is recorded, because "it tried and failed" is
  exactly what someone debugging a silent integration needs to see.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)

_MUTATIONS = ("POST", "PUT", "PATCH", "DELETE")

# The one POST in this API that is not a write.
#
# MCP is JSON-RPC: the method lives inside the body, so every call — including
# `tools/list` and every read tool — arrives as a POST. Auditing by HTTP verb
# would file each of them as `api_write`, which is wrong twice over: it labels a
# read as a write, and at this endpoint's 120-per-minute ceiling it buries the
# genuine writes this trail exists to show. There is no write to miss by
# skipping it: `backend/mcp/catalog.py` is a closed catalogue of reads, pinned
# by `test_mcp_server.py::test_every_tool_actually_only_calls_GET_endpoints`.
#
# If a write tool is ever added there, this exemption has to come off in the
# same change — which is why it is a named constant and not a condition buried
# in the dispatch below.
_NOT_A_MUTATION_DESPITE_THE_VERB = ("/api/v1/mcp",)

# Refusals by the auth layer itself. Nothing was attempted, so nothing is
# recorded — otherwise a misconfigured client hammering with a dead key would
# write one audit row per attempt.
_NOT_AN_ATTEMPT = (401, 403, 429)


class MachineAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        from backend.auth.actor_context import get_machine_actor

        response = await call_next(request)

        if request.method not in _MUTATIONS:
            return response
        if request.url.path in _NOT_A_MUTATION_DESPITE_THE_VERB:
            return response
        # Read from the SCOPE, not a ContextVar: BaseHTTPMiddleware runs the
        # downstream app in its own task, so anything a dependency sets in a
        # context variable is gone by the time the response reaches here. The
        # scope dict is the same object throughout, which is why this works.
        actor = get_machine_actor(request.scope)
        if actor is None:                       # a person, or an unauthenticated call
            return response
        if response.status_code in _NOT_AN_ATTEMPT:
            return response

        tenant_id, actor_id = actor
        try:
            from backend.activity.service import log_action
            log_action(
                tenant_id=tenant_id,
                user_id=actor_id,
                action="api_write",
                resource=f"{request.method} {request.url.path}",
                context={"status_code": response.status_code},
                status="success" if response.status_code < 400 else "error",
            )
        except Exception as exc:
            # Never fail the customer's call because the audit could not be
            # written. A missing row is a gap in a trail; a 500 here is an
            # integration that stops working for a logging problem.
            log.warning("machine audit not recorded: %s", exc)

        return response
