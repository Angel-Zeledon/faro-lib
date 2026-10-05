import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from backend.auth.actor_context import get_machine_actor
from backend.middleware import latency_window

log = logging.getLogger("access")


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        route = request.scope.get("route")
        latency_window.record(
            latency_window.route_family(getattr(route, "path", None), request.url.path),
            elapsed_ms, response.status_code,
        )
        tenant = getattr(request.state, "tenant_id", "-") or "-"
        key_part = ""
        # An API-key call carries no JWT, so `tenant_id` above is empty for it.
        # The guard publishes the acting key in the ASGI scope once it has
        # passed every check (a refused call has no actor, and stays "-").
        machine = get_machine_actor(request.scope)
        if machine:
            tenant = machine[0]
            key_part = f" key={machine[1].removeprefix('api_key:')}"
        log.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} [{elapsed_ms}ms] tenant={tenant}{key_part}"
        )
        return response
