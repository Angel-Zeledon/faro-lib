"""Per-tenant IP allowlist: the client address, the match and the refusal.

Enforced here for everything Python still serves (JWT and `sk_live_*` calls in
`backend/auth/guards.py`, login and refresh in `backend/api/v1/auth.py`, the
training-progress websocket). The same rules are implemented in
`backend-rs/src/ip_allowlist.rs`, which also owns the admin routes. The schema
(`ip_allowlist_policies`, `ip_allowlist_entries`) is owned by Python.

With no policy row, or a disabled one, nothing here changes any answer.

THE CLIENT ADDRESS IS THE WHOLE FEATURE. `X-Forwarded-For` is a header the
caller writes, so reading its first entry (as `api/v1/trial.py` does for a
speed bump) would let anyone walk past the allowlist by claiming an allowed
address. Proxies APPEND the address they saw to the right end of the chain, so
the only entries that can be trusted are the ones the deployment's own proxies
wrote: counted from the right, as many as `TRUSTED_PROXY_HOPS` says. Whatever a
client put further left is ignored. Behind Caddy -> Next.js -> gateway -> API
that is a count of the proxies that append (see docs/rust-migration.md); with
the default 0 the header is not read at all and the socket peer is the client.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Optional, Union

from starlette.requests import HTTPConnection

from backend.config import settings
from backend.db.connection import query, query_one
from backend.errors import AppError

log = logging.getLogger(__name__)

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

REFUSAL_EVENT = "account.ip_access_refused"
# One refusal row per (tenant, address) per window: a stolen key hammering the
# API would otherwise write a row per call.
REFUSAL_EVENT_WINDOW_MINUTES = 10


def parse_ip(text: str) -> Optional[IPAddress]:
    """A bare IPv4/IPv6 literal, or None. An IPv4-mapped IPv6 address
    (`::ffff:1.2.3.4`) is the IPv4 address: a dual-stack listener reports IPv4
    clients that way and an allowlist entry says `1.2.3.4`."""
    text = (text or "").strip()
    if not text or any(c in text for c in " ,/[]%"):
        return None
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def client_ip(forwarded_for: str, peer: Optional[str], hops: int) -> Optional[IPAddress]:
    """The caller's address: the chain is `X-Forwarded-For` entries then the
    socket peer, and the client is `hops` entries from the right end of it.

    A chain shorter than that (a request that did not come through every proxy)
    resolves to its first entry, which can only be an address a trusted hop
    wrote or the peer itself. None means the address is not an IP at all, which
    an enabled allowlist treats as outside."""
    chain = [e.strip() for e in (forwarded_for or "").split(",") if e.strip()] if hops > 0 else []
    chain.append((peer or "").strip())
    index = max(len(chain) - 1 - max(hops, 0), 0)
    return parse_ip(chain[index])


def client_ip_of(conn: HTTPConnection) -> Optional[IPAddress]:
    peer = conn.client.host if conn.client else None
    return client_ip(conn.headers.get("x-forwarded-for", ""), peer, settings.trusted_proxy_hops)


def covers(cidr: str, ip: IPAddress) -> bool:
    try:
        return ip in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        # A row nobody should have written (the Rust routes normalise before
        # storing): covers nothing, which only ever narrows access.
        log.warning("ip_allowlist: unreadable entry %r ignored", cidr)
        return False


def policy_entries(tenant_id: str) -> Optional[list[str]]:
    """The tenant's CIDRs when the policy is ENABLED, else None (no filtering)."""
    row = query_one(
        "SELECT enabled FROM ip_allowlist_policies WHERE tenant_id = %s", (tenant_id,),
    )
    if not row or not row["enabled"]:
        return None
    return [r["cidr"] for r in query(
        "SELECT cidr FROM ip_allowlist_entries WHERE tenant_id = %s", (tenant_id,),
    )]


def _record_refusal(tenant_id: str, actor_id: str, ip_text: str, via: str) -> None:
    recent = query_one(
        "SELECT 1 AS x FROM activity_logs WHERE tenant_id = %s AND action = %s "
        "AND context->>'ip' = %s "
        f"AND created_at > NOW() - INTERVAL '{REFUSAL_EVENT_WINDOW_MINUTES} minutes' LIMIT 1",
        (tenant_id, REFUSAL_EVENT, ip_text),
    )
    if recent:
        return
    from backend.activity.events import record_event
    record_event(
        tenant_id, actor_id, REFUSAL_EVENT,
        details={"ip": ip_text, "via": via}, reason="ip_not_in_allowlist",
    )


def enforce(conn: HTTPConnection, tenant_id: str, actor_id: str, via: str) -> None:
    """Refuse (403 `ip_not_allowed`) a caller outside the tenant's enabled
    allowlist. `via` is the door: login, refresh, token, api_key, websocket."""
    entries = policy_entries(tenant_id)
    if entries is None:
        return
    ip = client_ip_of(conn)
    if ip is not None and any(covers(c, ip) for c in entries):
        return
    ip_text = str(ip) if ip is not None else "unknown"
    _record_refusal(tenant_id, actor_id, ip_text, via)
    raise AppError(
        "ip_not_allowed",
        "This account only accepts access from approved IP addresses.",
        status_code=403,
        params={"ip": ip_text},
    )
