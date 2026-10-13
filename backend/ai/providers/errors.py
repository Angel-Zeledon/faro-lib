"""One small, typed set of failures for every provider, and the scrubber.

Every provider says "your key is wrong" differently: OpenAI and Groq answer
401, Anthropic 401 with `authentication_error`, Gemini 400 with
`API_KEY_INVALID`, OpenRouter 401 with its own body. The screens must not care
which one answered, so every failure is turned into one of `CODES` here, once.
The sentence the person reads (and the remedy) lives in the frontend catalogue
under `errors.llm_<code>`; the backend only carries the code and its params.

Provider error bodies can echo what they were sent — including the key. Every
message that leaves this module goes through `scrub()` first, and the message
is kept short: it is a diagnostic for an admin, never the provider's raw page.
"""

from __future__ import annotations

import json
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

CODES: tuple[str, ...] = (
    "invalid_key",         # 401/403: the key is wrong, revoked or not allowed
    "quota_exceeded",      # 402, 429 insufficient_quota: no credit / billing
    "rate_limited",        # 429 with Retry-After: too many requests right now
    "model_not_found",     # 404: the model name does not exist for this key
    "bad_request",         # 400/413/422: incl. a context that is too long
    "content_blocked",     # the provider's safety filter refused the request
    "provider_down",       # 5xx / 529: the provider is failing
    "timeout",             # no answer in time
    "unreachable",         # DNS, connection refused, TLS failure
    "malformed_response",  # 200 with garbage, no choices, truncated, too large
    "base_url_refused",    # a custom URL StockAI will not call (SSRF guard)
    "circuit_open",        # paused after repeated failures; retried later
)

# Worth another attempt: the request was refused or never arrived. A READ
# timeout is deliberately NOT here (see `retryable`): the provider may have
# processed — and billed — the request we stopped waiting for.
TRANSIENT: frozenset[str] = frozenset({"rate_limited", "provider_down", "unreachable"})

# Failures that say something about the PROVIDER, so they count towards
# pausing it. A request-specific refusal (too long a context, a blocked
# prompt) says nothing about the next request and must not pause a working one.
COUNTS_FOR_BREAKER: frozenset[str] = frozenset({
    "invalid_key", "quota_exceeded", "rate_limited", "model_not_found",
    "provider_down", "timeout", "unreachable", "malformed_response",
    "base_url_refused",
})

MAX_MESSAGE_CHARS = 240
RETRY_AFTER_CAP_S = 30.0


class ProviderError(Exception):
    """A provider call failed in one of the ways listed in `CODES`."""

    def __init__(self, code: str, message: str = "", *, status: Optional[int] = None,
                 retry_after: Optional[float] = None, params: Optional[dict] = None,
                 phase: str = ""):
        if code not in CODES:
            raise ValueError(f"unknown provider error code {code!r}")
        self.code = code
        self.message = (message or code)[:MAX_MESSAGE_CHARS]
        self.status = status
        self.retry_after = retry_after
        self.params: dict[str, Any] = dict(params or {})
        # "connect" | "read" for a timeout; decides whether a retry is safe.
        self.phase = phase
        super().__init__(f"{code}: {self.message}")

    @property
    def retryable(self) -> bool:
        # Anything that failed after the request reached the provider (phase
        # "read") may already have been processed and billed: never repeated.
        if self.phase == "read":
            return False
        if self.code == "timeout":
            return self.phase == "connect"
        return self.code in TRANSIENT

    def public(self) -> dict:
        """What an API response or a stored row may carry."""
        return {"code": self.code, "params": dict(self.params),
                "message": self.message, "status": self.status}


# ── Scrubbing ────────────────────────────────────────────────────────────────

# Shapes of keys we know, so a key we were never told about (one pasted into a
# base URL, a different key the provider echoes) is still masked.
_KEY_SHAPES = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{8,}|sk-or-[A-Za-z0-9_\-]{8,}|sk-proj-[A-Za-z0-9_\-]{8,}"
    r"|sk-[A-Za-z0-9_\-]{16,}|gsk_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_\-]{20,}"
    r"|Bearer\s+[A-Za-z0-9_\-\.]{12,})"
)


def scrub(text: Any, secrets: Iterable[str] = ()) -> str:
    """`text` with every given secret and anything key-shaped masked."""
    out = str(text or "")
    for s in secrets:
        if s and len(s) >= 6:
            out = out.replace(s, "***")
    return _KEY_SHAPES.sub("***", out)


# ── Classification ───────────────────────────────────────────────────────────

def _parse_retry_after(headers: dict[str, str]) -> Optional[float]:
    h = {k.lower(): v for k, v in (headers or {}).items()}
    raw_ms = h.get("retry-after-ms")
    if raw_ms:
        try:
            return min(max(float(raw_ms) / 1000.0, 0.0), RETRY_AFTER_CAP_S)
        except ValueError:
            pass
    raw = h.get("retry-after")
    if not raw:
        return None
    try:
        return min(max(float(raw), 0.0), RETRY_AFTER_CAP_S)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
        delta = (when - datetime.now(timezone.utc)).total_seconds()
        return min(max(delta, 0.0), RETRY_AFTER_CAP_S)
    except (TypeError, ValueError):
        return None


def _error_fields(body: bytes) -> tuple[str, str, str]:
    """(message, type, code) from any of the error shapes providers use:

    OpenAI / Groq / DeepSeek: {"error": {"message", "type", "code"}}
    Anthropic:                {"type": "error", "error": {"type", "message"}}
    Gemini (compat layer):    [{"error": {"code", "message", "status"}}] or the dict
    OpenRouter:               {"error": {"code": 402, "message", "metadata"}}
    """
    try:
        data = json.loads(body.decode("utf-8", "replace")) if body else None
    except (ValueError, UnicodeDecodeError):
        data = None
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        text = body.decode("utf-8", "replace") if body else ""
        return text[:MAX_MESSAGE_CHARS], "", ""
    err = data.get("error")
    if isinstance(err, str):
        return err, "", ""
    if not isinstance(err, dict):
        err = data
    message = str(err.get("message") or data.get("message") or "")
    etype = str(err.get("type") or err.get("status") or "")
    code = err.get("code")
    return message, etype, "" if code is None else str(code)


_CONTEXT_TOO_LONG = ("context length", "context_length", "maximum context", "too many tokens",
                     "prompt is too long", "too long", "reduce the length")
_BLOCKED = ("content_filter", "content filter", "safety", "moderation", "flagged",
            "guardrail", "content_policy", "responsible ai")
_BAD_KEY = ("api key not valid", "api_key_invalid", "invalid api key", "invalid_api_key",
            "incorrect api key", "invalid x-api-key", "authentication")


def classify_http(status: int, body: bytes, headers: dict[str, str], *,
                  model: str = "", secrets: Iterable[str] = ()) -> ProviderError:
    """Turn a non-2xx answer (or a 2xx error body) into a `ProviderError`."""
    message, etype, code = _error_fields(body)
    message = scrub(message, secrets)
    blob = f"{message} {etype} {code}".lower()

    def err(c: str, **kw) -> ProviderError:
        return ProviderError(c, message or f"HTTP {status}", status=status, **kw)

    if status in (401,):
        return err("invalid_key")
    if status == 403:
        if any(w in blob for w in _BLOCKED):
            return err("content_blocked")
        return err("invalid_key")
    if status == 402 or "insufficient_quota" in blob or "billing" in blob \
            or "credit balance" in blob or "insufficient credits" in blob:
        return err("quota_exceeded")
    if status == 429:
        return err("rate_limited", retry_after=_parse_retry_after(headers))
    if status == 404:
        return err("model_not_found", params={"model": model})
    if status == 408:
        return err("timeout", phase="read")
    if status in (400, 413, 422):
        if any(w in blob for w in _BAD_KEY):
            return err("invalid_key")
        if any(w in blob for w in _CONTEXT_TOO_LONG) or status == 413:
            return err("bad_request", params={"reason": "context_too_long"})
        if any(w in blob for w in _BLOCKED):
            return err("content_blocked")
        if "model" in blob and ("not found" in blob or "does not exist" in blob
                                or "not supported" in blob or "invalid model" in blob):
            return err("model_not_found", params={"model": model})
        return err("bad_request", params={"reason": "rejected"})
    if status >= 500:
        # 529 is Anthropic's "overloaded"; 502/503 OpenRouter's "model down".
        return err("provider_down", retry_after=_parse_retry_after(headers))
    if 300 <= status < 400:
        return ProviderError("base_url_refused", f"HTTP {status} redirect not followed",
                             status=status, params={"reason": "redirect"})
    return err("bad_request", params={"reason": "rejected"})
