"""The signed link at the foot of every scheduled report.

`token = <recipient_row_id>.<base64url(HMAC-SHA256(SECRET_KEY, "report-unsubscribe|<id>"))>`
(no padding). No address in the URL, no expiry (a "stop sending me this" link
that stops working is a trap), and the signature only proves the link was
minted by this installation for that recipient row. The Rust API verifies it
(`backend-rs/src/routes/scheduled_reports.rs`); both sides pin the same
vector in their tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional

_PURPOSE = "report-unsubscribe|"


def _sig(secret: str, recipient_id: str) -> str:
    digest = hmac.new(secret.encode(), (_PURPOSE + recipient_id).encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def mint(secret: str, recipient_id: str) -> str:
    return f"{recipient_id}.{_sig(secret, recipient_id)}"


def verify(secret: str, token: str) -> Optional[str]:
    """The recipient row id the token was minted for, or None."""
    recipient_id, dot, sig = (token or "").partition(".")
    if not dot or not recipient_id or not sig:
        return None
    return recipient_id if hmac.compare_digest(sig, _sig(secret, recipient_id)) else None
