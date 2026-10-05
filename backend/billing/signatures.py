"""Stripe webhook signature verification, with the standard library only.

Why not the `stripe` package: this is the one piece of it we would use, it is
forty lines of documented HMAC, and the package would add a dependency (and
image weight) to the API for a single function. The scheme, as Stripe
documents it:

    Stripe-Signature: t=1700000000,v1=<hex>,v1=<hex>,v0=<ignored>

    expected = HMAC_SHA256(key=<endpoint secret, the whole "whsec_..." string>,
                           msg=f"{t}." + raw_body)

The event is genuine when ANY v1 matches (Stripe sends two while a secret is
being rolled) and `t` is within the tolerance of our clock, which is what makes
a captured request useless later. Idempotency on the event id covers a replay
inside the window.

PayPal is verified differently — by asking PayPal (`paypal_api.verify_webhook`)
— because its signature is an RSA one over a certificate PayPal hosts.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

TOLERANCE_SECONDS = 300


def stripe_signature(secret: str, timestamp: int, body: bytes) -> str:
    """The v1 signature Stripe would send for this body. Used by the tests to
    build genuine requests, and by `verify_stripe_signature` to compare."""
    signed = f"{timestamp}.".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify_stripe_signature(
    secret: str,
    header: Optional[str],
    body: bytes,
    *,
    now: Optional[float] = None,
    tolerance: int = TOLERANCE_SECONDS,
) -> bool:
    """True only for a body signed with `secret` inside the tolerance window.

    Never raises: a malformed header is simply not a valid signature.
    """
    if not secret or not header:
        return False
    timestamp: Optional[int] = None
    candidates: list[str] = []
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return False
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance:
        return False
    expected = stripe_signature(secret, timestamp, body)
    return any(hmac.compare_digest(expected, c) for c in candidates)
