import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("access")


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        tenant = getattr(request.state, "tenant_id", "-") or "-"
        log.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} [{elapsed_ms}ms] tenant={tenant}"
        )
        return response
