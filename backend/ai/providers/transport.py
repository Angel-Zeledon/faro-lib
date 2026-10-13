"""The one way this package opens a connection: checked, pinned, bounded.

A company types the base URL of a "custom" provider, so — exactly like a SQL
data source or a webhook — our server would otherwise make requests to any
address a tenant names: the cloud metadata service, our own Postgres, the
docker network. The rules, reusing `backend/datasources/network.py`:

* Every address the host resolves to is checked; link-local and metadata
  addresses are always refused; loopback and private ranges only when the
  operator set AI_PROVIDER_ALLOW_PRIVATE_HOSTS=true (a self-hosted Ollama).
* The connection goes to the address that was CHECKED (Host header and TLS SNI
  carry the name), so a DNS answer that changes between check and connect
  ("rebinding") cannot pick a different target.
* `https` is required for anything public. Plain `http` is accepted only when
  every address is private AND the operator allowed private hosts.
* Redirects are never followed: a 3xx is a refusal. The key travels in a
  header, and following a redirect would hand it to whatever host the
  provider (or an attacker controlling it) points at.
* The response is read with a byte ceiling and a wall-clock deadline, so a
  provider that streams forever or sends a gigabyte cannot hold a worker.
"""

from __future__ import annotations

import ipaddress
import json as _json
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from backend.ai.providers.errors import ProviderError
from backend.datasources import network
from backend.errors import AppError

ALLOW_PRIVATE_SETTING = "AI_PROVIDER_ALLOW_PRIVATE_HOSTS"
MAX_RESPONSE_BYTES = 2_000_000
_CHUNK = 64 * 1024


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_ms: int

    def json(self) -> Any:
        return _json.loads(self.body.decode("utf-8"))


def allow_private() -> bool:
    from backend.service_config.resolver import effective
    return bool(effective().ai_provider_allow_private_hosts)


def _refused(reason: str, **params) -> ProviderError:
    return ProviderError("base_url_refused", f"base URL refused: {reason}",
                         params={"reason": reason, **params})


def check_base_url(url: str, *, allow_private_hosts: bool) -> None:
    """Static checks, without touching the network (used at save time).
    A host NAME is checked again, with its resolved addresses, at every call."""
    raw = (url or "").strip()
    if not raw:
        raise _refused("missing")
    if len(raw) > 500:
        raise _refused("too_long")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise _refused("scheme")
    if parts.username or parts.password:
        raise _refused("credentials_in_url")
    if parts.fragment:
        raise _refused("fragment")
    host = parts.hostname
    if not host:
        raise _refused("missing_host")
    try:
        parts.port
    except ValueError:
        raise _refused("bad_port")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        verdict = network.classify(ip, allow_private=allow_private_hosts)
        if not verdict.allowed:
            raise _refused("private_address" if verdict.category in ("private", "loopback")
                           else "forbidden_address",
                           host=host, category=verdict.category,
                           setting=ALLOW_PRIVATE_SETTING)
        if parts.scheme == "http" and verdict.category == "public":
            raise _refused("https_required", host=host)
    elif parts.scheme == "http" and not allow_private_hosts:
        # A name over plain http can only be acceptable if it resolves to a
        # private address, and private addresses are off on this installation.
        raise _refused("https_required", host=host)


def _resolve(host: str, port: int, *, allow_private_hosts: bool, timeout_s: float):
    try:
        return network.resolve_allowed(host, port, allow_private=allow_private_hosts,
                                       timeout_s=max(0.5, timeout_s))
    except AppError as exc:
        p = exc.params or {}
        if exc.code == "data_source_host_not_allowed":
            raise _refused("private_address", host=host, category=p.get("category"),
                           setting=ALLOW_PRIVATE_SETTING)
        if exc.code == "data_source_host_forbidden":
            raise _refused("forbidden_address", host=host, category=p.get("category"))
        if exc.code == "data_source_dns_failed":
            raise ProviderError("unreachable", f"DNS lookup for {host} failed",
                                params={"reason": "dns", "host": host})
        raise


def request(method: str, url: str, *, headers: dict[str, str],
            json_body: Optional[dict] = None, connect_timeout: float,
            read_timeout: float, deadline: float,
            allow_private_hosts: bool) -> HttpResult:
    """One HTTP exchange under every rule in the module header.

    `deadline` is a `time.monotonic()` instant; nothing here runs past it.
    Raises `ProviderError` for every failure of the exchange itself; an HTTP
    error status is RETURNED (the caller classifies it with the body)."""
    started = time.monotonic()
    check_base_url(url, allow_private_hosts=allow_private_hosts)
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderError("timeout", "no time left before the call", phase="connect")
    addresses = _resolve(host, port, allow_private_hosts=allow_private_hosts,
                         timeout_s=min(connect_timeout, remaining))
    if parts.scheme == "http":
        for ip in addresses:
            if network.classify(ip, allow_private=True).category == "public":
                raise _refused("https_required", host=host)

    path = (parts.path or "") + (f"?{parts.query}" if parts.query else "")
    host_header = f"{host}:{parts.port}" if parts.port else host
    send_headers = {**headers, "Host": host_header, "Accept-Encoding": "identity"}
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}

    last: ProviderError = ProviderError("unreachable", f"could not connect to {host}",
                                        params={"reason": "connect", "host": host})
    for ip in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("timeout", "deadline reached before connecting", phase="connect")
        literal = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
        target = f"{parts.scheme}://{literal}:{port}{path}"
        timeout = httpx.Timeout(connect=min(connect_timeout, remaining),
                                read=min(read_timeout, remaining),
                                write=min(read_timeout, remaining),
                                pool=min(connect_timeout, remaining))
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                with client.stream(method, target, headers=send_headers, json=json_body,
                                   extensions=extensions) as response:
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                        raise ProviderError("malformed_response", "response too large",
                                            params={"reason": "too_large"}, phase="read")
                    buf = bytearray()
                    for chunk in response.iter_bytes(_CHUNK):
                        buf.extend(chunk)
                        if len(buf) > MAX_RESPONSE_BYTES:
                            raise ProviderError("malformed_response", "response too large",
                                                params={"reason": "too_large"}, phase="read")
                        if time.monotonic() > deadline:
                            raise ProviderError("timeout", "answer still arriving at the deadline",
                                                phase="read")
                    return HttpResult(
                        status=response.status_code,
                        headers={k.lower(): v for k, v in response.headers.items()},
                        body=bytes(buf),
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
        except ProviderError:
            raise
        except httpx.ConnectTimeout:
            last = ProviderError("timeout", f"connecting to {host} timed out", phase="connect")
            continue
        except httpx.ConnectError as exc:
            reason = "tls" if "ssl" in str(exc).lower() or "certificate" in str(exc).lower() else "connect"
            last = ProviderError("unreachable", f"could not connect to {host} ({reason})",
                                 params={"reason": reason, "host": host})
            continue
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
            raise ProviderError("timeout", f"{host} did not answer in time", phase="read")
        except httpx.HTTPError as exc:
            # Connection dropped mid-answer and the like: the request reached
            # the provider, so this is NOT safe to repeat.
            raise ProviderError("unreachable", f"connection to {host} failed: {type(exc).__name__}",
                                params={"reason": "dropped", "host": host}, phase="read")
    raise last
