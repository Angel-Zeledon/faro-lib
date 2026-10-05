"""Pure validation for the feedback endpoint: no database, no framework.

Everything the endpoint refuses is decided here so it can be tested without a
server. A refusal is an `AppError` with a stable code; the frontend renders
`errors.<code>` in the person's language.

The screenshot is the only risky field: it arrives as base64 inside the JSON
body and ends up as a file on disk and as an e-mail attachment. So its type is
decided by READING THE BYTES (magic numbers), never by trusting a declared
content type or a file name, and the extension of the stored file is chosen by
the server from that answer.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from backend.errors import AppError

# The decoded screenshot may not exceed this. The request body cap below is
# derived from it (base64 is 4/3 of the bytes) plus room for the text fields.
MAX_SCREENSHOT_BYTES = int(2.5 * 1024 * 1024)
MAX_MESSAGE_CHARS = 4000
MIN_MESSAGE_CHARS = 3

# Text fields beyond the message are metadata the client reads from the
# browser; they are cut, not refused, because a long user agent is not abuse.
MAX_ERROR_CODE_CHARS = 120
MAX_PAGE_PATH_CHARS = 300
MAX_USER_AGENT_CHARS = 300
MAX_APP_VERSION_CHARS = 40

# 2.5 MB of bytes is ~3.34 MB of base64; 64 KB covers every other field.
MAX_BODY_BYTES = (MAX_SCREENSHOT_BYTES * 4 // 3) + 4 + 64 * 1024

# Reports per person per rolling hour. A person describing a failure sends one,
# maybe two; ten is generous and still stops a script from filling the disk.
RATE_MAX_PER_HOUR = 10
RATE_WINDOW_SECONDS = 3600

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


@dataclass(frozen=True)
class Screenshot:
    content: bytes
    ext: str          # "png" | "jpg"
    media_type: str   # "image/png" | "image/jpeg"


def sniff_image(content: bytes) -> tuple[str, str] | None:
    """(extension, media type) from the leading bytes, or None if not PNG/JPEG."""
    if content.startswith(_PNG_MAGIC):
        return "png", "image/png"
    if content.startswith(_JPEG_MAGIC):
        return "jpg", "image/jpeg"
    return None


def _too_large() -> AppError:
    return AppError(
        "feedback_screenshot_too_large",
        "The screenshot is too large.",
        status_code=413,
        params={"max_mb": "2.5"},
    )


def _invalid() -> AppError:
    return AppError(
        "feedback_screenshot_invalid",
        "The screenshot is not a valid PNG or JPEG image.",
        status_code=422,
    )


def decode_screenshot(data: str | None) -> Screenshot | None:
    """Turn the base64 field into a checked screenshot.

    Accepts plain base64 or a `data:image/...;base64,` URL (what a canvas
    produces). Empty / missing means "no screenshot", which is valid: the
    person unchecked the box, or capture failed in their browser.
    """
    if data is None or not data.strip():
        return None
    raw = data.strip()
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    # Refuse before decoding: 4 base64 chars carry 3 bytes, so this bounds the
    # memory a hostile payload can make us allocate.
    if len(raw) > (MAX_SCREENSHOT_BYTES * 4 // 3) + 8:
        raise _too_large()
    try:
        content = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise _invalid()
    if len(content) > MAX_SCREENSHOT_BYTES:
        raise _too_large()
    kind = sniff_image(content)
    if kind is None or len(content) < 16:
        raise _invalid()
    return Screenshot(content=content, ext=kind[0], media_type=kind[1])


def _no_nul(value: str | None) -> str:
    # Postgres refuses a NUL in text with a driver error (a 500 for the sender).
    return (value or "").replace(chr(0), "")


def clean_message(message: str | None) -> str:
    text = _no_nul(message).strip()
    if len(text) < MIN_MESSAGE_CHARS:
        raise AppError(
            "feedback_message_required",
            "Tell us what happened.",
            status_code=422,
        )
    if len(text) > MAX_MESSAGE_CHARS:
        raise AppError(
            "feedback_message_too_long",
            "The message is too long.",
            status_code=422,
            params={"max": MAX_MESSAGE_CHARS},
        )
    return text


def clip(value: str | None, limit: int) -> str | None:
    """Trim and cut a metadata field; empty becomes None."""
    text = " ".join(_no_nul(value).split())[:limit]
    return text or None


def clean_page_path(value: str | None) -> str | None:
    """Only the path: a query string or fragment can carry ids and tokens."""
    text = _no_nul(value).strip()
    for sep in ("?", "#"):
        text = text.split(sep, 1)[0]
    return clip(text, MAX_PAGE_PATH_CHARS)


def is_rate_limited(recent_count: int) -> bool:
    """True when the person already sent the maximum in the window."""
    return recent_count >= RATE_MAX_PER_HOUR
