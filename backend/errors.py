"""
User-facing application errors carrying a STABLE machine code.

This is the reusable pattern for keeping backend code English while the user
still sees Spanish. Instead of raising a hardcoded Spanish sentence, raise an
``AppError`` with:

  - ``code``   — a stable snake_case English identifier (e.g. ``reception_over_pending``)
  - ``params`` — the dynamic values that would have been interpolated (sku, qty…)
  - ``message``— an English fallback sentence, shown only when the client has no
                 mapping for ``code``
  - ``status_code`` — the HTTP status the API responds with

The FastAPI handler (``backend/main.py`` → ``app_error_handler``) serializes it
to the existing error envelope, adding ``error_code`` and ``error_params`` next
to ``detail`` (the English fallback) without breaking the ``detail`` contract.
The frontend maps ``error_code`` → an ``errors.<code>`` i18n string (es + en),
interpolating ``params`` — so the localized (Spanish) wording lives in exactly
one place and the backend stays English.

``AppError`` subclasses ``ValueError`` on purpose: existing ``except ValueError``
handlers (the API layer's service-error catches, the WhatsApp tool layer) keep
working and fall back to the English ``message`` via ``str(exc)`` until they are
migrated to consume ``code``/``params`` directly in later slices.
"""

from __future__ import annotations

from typing import Any, Optional


class AppError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        params: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.params: dict[str, Any] = dict(params or {})
