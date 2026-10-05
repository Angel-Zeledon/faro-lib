"""Which addresses StockAI may open a database connection to.

A SQL data source makes OUR server open a TCP connection to an address a
tenant typed. Without a check, that is a server-side request forgery: a tenant
on the hosted product could point a "database" at the cloud metadata service
(169.254.169.254), at StockAI's own Postgres on localhost, or at anything else
on the private network the server sits in, and read the error messages to map
it.

The rule, applied to EVERY address a host name resolves to (one bad answer is
enough to refuse — otherwise a DNS server that returns two records picks which
one we hit):

* always refused — link-local (169.254/16, fe80::/10, which includes every
  cloud metadata endpoint), well-known metadata addresses outside that range,
  multicast, unspecified (0.0.0.0, ::), broadcast and reserved ranges;
* refused unless the installation allows private hosts — loopback, RFC 1918,
  carrier-grade NAT (100.64/10), IPv6 unique-local (fc00::/7) and the other
  "not globally reachable" ranges.

``SQL_SOURCES_ALLOW_PRIVATE_HOSTS`` is the switch. Off by default: the hosted
product must never reach its own network. A self-hosted installation whose ERP
database lives on the same LAN turns it on — the refusal names the variable so
the operator knows what to set.

IPv4-mapped (``::ffff:10.0.0.1``), NAT64 (``64:ff9b::/96``) and 6to4
(``2002::/16``) addresses are unwrapped and judged by the IPv4 address they
carry; otherwise they would be a way around the IPv4 rules.

The connection is then made to the address that was checked (see
`client.py`), not to the name — re-resolving at connect time would let a
DNS record that changes between the two lookups ("DNS rebinding") pick a
different, unchecked target.
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Optional, Union

from backend.errors import AppError

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

ALLOW_PRIVATE_SETTING = "SQL_SOURCES_ALLOW_PRIVATE_HOSTS"
DNS_TIMEOUT_S = 5.0

# Metadata services that do NOT live in link-local space.
_METADATA = {
    ipaddress.ip_address("100.100.100.200"),    # Alibaba Cloud
    ipaddress.ip_address("168.63.129.16"),      # Azure WireServer
    ipaddress.ip_address("fd00:ec2::254"),      # AWS IMDS over IPv6
}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIXTOFOUR = ipaddress.ip_network("2002::/16")

# A small, bounded pool: getaddrinfo has no timeout of its own, so a lookup that
# hangs runs out its time here instead of holding a request thread forever.
_dns_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sqlsrc-dns")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    category: str          # "public", "private", "loopback", "link_local", ...


def _unwrap(ip: IPAddress) -> IPAddress:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip in _SIXTOFOUR:
            return ip.sixtofour  # type: ignore[return-value]
    return ip


def classify(ip: IPAddress, *, allow_private: bool) -> Verdict:
    """Whether one address may be connected to, and which range it is in."""
    inner = _unwrap(ip)
    if ip in _METADATA or inner in _METADATA:
        return Verdict(False, "metadata")
    if inner.is_link_local:
        return Verdict(False, "link_local")
    if inner.is_multicast:
        return Verdict(False, "multicast")
    if inner.is_unspecified:
        return Verdict(False, "unspecified")
    if isinstance(inner, ipaddress.IPv4Address) and inner == ipaddress.IPv4Address("255.255.255.255"):
        return Verdict(False, "reserved")
    if inner.is_loopback:
        return Verdict(allow_private, "loopback")
    if isinstance(inner, ipaddress.IPv4Address) and inner in _CGNAT:
        return Verdict(allow_private, "private")
    if inner.is_private:
        return Verdict(allow_private, "private")
    if inner.is_reserved:
        return Verdict(False, "reserved")
    if not inner.is_global:
        # Documentation, benchmarking and other special-purpose ranges.
        return Verdict(allow_private, "private")
    return Verdict(True, "public")


def _refuse(host: str, address: str, category: str) -> AppError:
    if category in ("private", "loopback"):
        return AppError(
            "data_source_host_not_allowed",
            f"The host {host} resolves to {address} ({category}), which this "
            f"installation does not connect to. A self-hosted installation can "
            f"allow private networks with {ALLOW_PRIVATE_SETTING}=true.",
            status_code=400,
            params={"host": host, "address": address, "category": category,
                    "setting": ALLOW_PRIVATE_SETTING},
        )
    # Link-local, cloud metadata, multicast, reserved: never a database, and
    # no setting allows them.
    return AppError(
        "data_source_host_forbidden",
        f"The host {host} resolves to {address} ({category}), an address no "
        "database connection may use.",
        status_code=400,
        params={"host": host, "address": address, "category": category},
    )


def check_literal(host: str, *, allow_private: bool) -> None:
    """Save-time check for a host that is already an IP address. A host name
    is checked when it is resolved, at connect time."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    verdict = classify(ip, allow_private=allow_private)
    if not verdict.allowed:
        raise _refuse(host, ip.compressed, verdict.category)


def resolve(host: str, port: int, *, timeout_s: float = DNS_TIMEOUT_S) -> list[IPAddress]:
    """Every address `host` resolves to, deduplicated, in resolver order.

    Raises `data_source_dns_failed` when the name does not resolve or the
    lookup takes longer than `timeout_s`."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:  # a host name, not an IP literal: resolve it below
        pass

    def _lookup():
        return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)

    future = _dns_pool.submit(_lookup)
    try:
        infos = future.result(timeout=timeout_s)
    except FutureTimeout:
        future.cancel()
        raise AppError("data_source_dns_failed",
                       f"Looking up {host} took longer than {int(timeout_s)} seconds.",
                       status_code=400, params={"host": host, "reason": "timeout"})
    except (socket.gaierror, UnicodeError, OSError):
        raise AppError("data_source_dns_failed", f"The host name {host} does not resolve.",
                       status_code=400, params={"host": host, "reason": "not_found"})
    seen: list[IPAddress] = []
    for info in infos:
        addr = info[4][0]
        if "%" in addr:          # scoped link-local; classify refuses it anyway
            addr = addr.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip not in seen:
            seen.append(ip)
    if not seen:
        raise AppError("data_source_dns_failed", f"The host name {host} does not resolve.",
                       status_code=400, params={"host": host, "reason": "not_found"})
    return seen


def resolve_allowed(host: str, port: int, *, allow_private: bool,
                    timeout_s: float = DNS_TIMEOUT_S) -> list[IPAddress]:
    """Resolve `host` and refuse unless EVERY address it resolves to is
    allowed. Returns the addresses, to be connected to directly."""
    addresses = resolve(host, port, timeout_s=timeout_s)
    for ip in addresses:
        verdict = classify(ip, allow_private=allow_private)
        if not verdict.allowed:
            raise _refuse(host, ip.compressed, verdict.category)
    return addresses


def allow_private_hosts() -> bool:
    from backend.config import settings
    return bool(getattr(settings, "sql_sources_allow_private_hosts", False))


def tcp_reachable(addresses: list[IPAddress], port: int, timeout_s: float) -> tuple[Optional[IPAddress], Optional[str]]:
    """Open (and close) a plain TCP connection to the first address that
    answers. Returns (address, None) or (None, failure kind) where the kind is
    ``refused``, ``timeout`` or ``unreachable``."""
    failure = "unreachable"
    for ip in addresses:
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout_s)
        try:
            sock.connect((ip.compressed, port))
            return ip, None
        except socket.timeout:
            failure = "timeout"
        except ConnectionRefusedError:
            failure = "refused"
        except OSError:
            failure = "unreachable" if failure != "refused" else failure
        finally:
            sock.close()
    return None, failure
