from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from backend.auth.jwt_handler import decode_token


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Extracts tenant_id, user_id, and role from the JWT and injects them
    into request.state for use by logging and monitoring middleware.
    Guards are still responsible for enforcing authentication.
    """

    async def dispatch(self, request: Request, call_next):
        request.state.tenant_id = None
        request.state.user_id = None
        request.state.role = None

        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            try:
                payload = decode_token(auth[7:])
                if payload.get("type") == "access":
                    request.state.tenant_id = payload.get("tenant_id")
                    request.state.user_id = payload.get("sub")
                    request.state.role = payload.get("role")
            except ValueError:
                pass

        return await call_next(request)
