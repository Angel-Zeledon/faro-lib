"""Adds the `Deprecation` / `Sunset` / `Link` headers to calls to a deprecated route.

Nothing is deprecated today (`backend/api/deprecation.py`), so this adds nothing and
costs one dict lookup per request.
"""

from starlette.middleware.base import BaseHTTPMiddleware

from backend.api import deprecation

_PREFIX = "/api/v1"


class DeprecationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if not deprecation.DEPRECATIONS:
            return response
        # The matched route is put in the scope by the router, which has run by now.
        route = request.scope.get("route")
        template = getattr(route, "path", None)
        if not template:
            return response
        if template.startswith(_PREFIX):
            template = template[len(_PREFIX):]
        for name, value in deprecation.headers_for(request.method, template).items():
            response.headers[name] = value
        return response
