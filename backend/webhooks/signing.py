"""Signing outbound webhooks. Pure.

Header:  X-StockAI-Signature: t=<unix seconds>,v1=<hex>
         v1 = HMAC-SHA256(secret, "<t>" + "." + <raw request body bytes>)

Verification recipe for a receiver (any language):

  1. Read the RAW body bytes - before any JSON parsing or re-serialising.
  2. Parse `t` and `v1` out of the X-StockAI-Signature header.
  3. Reject when |now - t| > 300 seconds (replay protection).
  4. expected = hex(HMAC_SHA256(secret, f"{t}.".encode() + raw_body))
  5. Accept only when a constant-time comparison of `expected` and `v1` matches
     (hmac.compare_digest in Python, crypto.timingSafeEqual in Node).
  6. Deduplicate on the envelope's `id`: a delivery can arrive more than once.

The signing secret is shown ONCE, when the webhook is created or its secret is
rotated. Nothing returns it afterwards.

The older `X-Signature: sha256=<HMAC(secret, body)>` and `X-Event` headers are
still sent so receivers written against the first version keep verifying.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

TOLERANCE_SECONDS = 300


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body,
                    hashlib.sha256).hexdigest()


def signature_header(secret: str, timestamp: int, body: bytes) -> str:
    return f"t={timestamp},v1={sign(secret, timestamp, body)}"


def legacy_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, header: str, body: bytes, *, now: Optional[float] = None,
           tolerance: int = TOLERANCE_SECONDS) -> bool:
    """The receiver's side, kept here so the recipe is tested, not just written."""
    parts = dict(p.split("=", 1) for p in (header or "").split(",") if "=" in p)
    try:
        timestamp = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - timestamp) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), given)
