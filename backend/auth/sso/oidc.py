"""Talking to a tenant's OpenID Connect provider — and only that.

Nothing here touches our database. The provider is whatever the tenant's admin
typed in, which makes every URL in this file attacker-influenced input: the
admin of one tenant must not be able to point the server at another tenant's
internal network. So:

  * every URL the SERVER will fetch (issuer, discovery, token endpoint, JWKS)
    must be https, carry no credentials and must not name localhost or a
    private, loopback, link-local or reserved address (`assert_public_https`);
  * redirects are never followed (httpx's default) - a public host cannot
    bounce the request inwards;
  * responses are read with a size ceiling.

Known limit, stated rather than hidden: the hostname is resolved here to vet it
and again by the HTTP stack to connect, so a DNS-rebinding provider could pass
the first and answer the second with a private address. Closing that needs a
pinned-IP transport; until then the vetting is a strong guard against the
mistake and a weak one against a determined operator-level attacker, which is
who `ENTERPRISE_SSO_ENABLED` is already trusting a tenant admin not to be.

Signature, `iss`, `aud`, `exp`, `iat` and `nonce` are checked by
`providers.verify_id_token` - the same code Google sign-in uses - and the
checks this file adds on top (`azp`, `email_verified`) are the OIDC rules that
code deliberately leaves to the caller.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse
from dataclasses import dataclass

import httpx

from backend.auth.social import providers
from backend.auth.social.providers import SocialAuthError

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 512 * 1024
SUPPORTED_ALG = "RS256"


# ── URL vetting ──────────────────────────────────────────────────────────────

def _resolve_host(host: str) -> list[str]:
    """Addresses a hostname resolves to; empty when it does not resolve.

    Its own function so the suite can answer without a network.
    """
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def _is_internal(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return True  # not an address we can reason about: treat as unsafe
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def assert_public_https(url: str, *, what: str = "url") -> None:
    """Refuse a URL the server must not fetch. Raises `sso_url_not_allowed`."""
    def refuse(why: str):
        raise SocialAuthError("sso_url_not_allowed", f"{what}: {why}")

    if not isinstance(url, str) or len(url) > 2048:
        refuse("missing or too long")
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        refuse("not a valid URL")
    if parts.scheme != "https":
        refuse("must be https")
    if not host or parts.username or parts.password:
        refuse("must name a host and carry no credentials")
    if parts.fragment:
        refuse("must not carry a fragment")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        refuse("host is not public")
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        if _is_internal(host):
            refuse("address is not public")
        return
    for addr in _resolve_host(host):
        if _is_internal(addr):
            refuse("host resolves to a non-public address")


# ── Discovery ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_auth_method: str


def _get_json(url: str) -> dict:
    try:
        with providers._client() as c:
            resp = c.get(url, headers={"Accept": "application/json"})
    except httpx.TimeoutException:
        raise SocialAuthError("sso_provider_unreachable", "timeout")
    except httpx.HTTPError as exc:
        raise SocialAuthError("sso_provider_unreachable", str(exc)[:200])
    if resp.status_code != 200:
        raise SocialAuthError("sso_discovery_failed", f"HTTP {resp.status_code}")
    if len(resp.content) > MAX_BODY_BYTES:
        raise SocialAuthError("sso_discovery_failed", "response too large")
    try:
        data = resp.json()
    except ValueError:
        raise SocialAuthError("sso_discovery_failed", "not JSON")
    if not isinstance(data, dict):
        raise SocialAuthError("sso_discovery_failed", "not a JSON object")
    return data


def discover(issuer: str) -> Discovery:
    """Fetch and validate `<issuer>/.well-known/openid-configuration`.

    Every refusal is a `sso_*` code the admin can act on, because this runs when
    they press Save - the one moment a typo can still be told apart from an
    outage.
    """
    assert_public_https(issuer, what="issuer")
    doc = _get_json(issuer.rstrip("/") + "/.well-known/openid-configuration")

    # RFC 8414 / OIDC Discovery 4.3: the issuer in the document MUST be
    # identical to the one used to retrieve it. Without this, a document served
    # at one address could vouch tokens as another provider's issuer.
    if doc.get("issuer") != issuer:
        raise SocialAuthError("sso_discovery_failed", "issuer in the document differs")

    auth_ep = doc.get("authorization_endpoint")
    token_ep = doc.get("token_endpoint")
    jwks = doc.get("jwks_uri")
    if not (auth_ep and token_ep and jwks):
        raise SocialAuthError("sso_discovery_failed", "endpoints missing")
    for name, value in (("token_endpoint", token_ep), ("jwks_uri", jwks)):
        assert_public_https(value, what=name)
    # The authorization endpoint is only ever a browser redirect, but a
    # non-https one would put the authorization request in clear text.
    if urllib.parse.urlsplit(str(auth_ep)).scheme != "https":
        raise SocialAuthError("sso_url_not_allowed", "authorization_endpoint: must be https")

    if "code" not in (doc.get("response_types_supported") or ["code"]):
        raise SocialAuthError("sso_discovery_failed", "authorization code flow unsupported")
    algs = doc.get("id_token_signing_alg_values_supported")
    if algs is not None and SUPPORTED_ALG not in algs:
        raise SocialAuthError("sso_discovery_failed", "RS256 ID tokens unsupported")
    challenge = doc.get("code_challenge_methods_supported")
    if challenge is not None and "S256" not in challenge:
        raise SocialAuthError("sso_discovery_failed", "PKCE S256 unsupported")

    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if "client_secret_basic" in methods:
        method = "client_secret_basic"
    elif "client_secret_post" in methods:
        method = "client_secret_post"
    else:
        raise SocialAuthError("sso_discovery_failed", "no client secret authentication")
    return Discovery(issuer, str(auth_ep), str(token_ep), str(jwks), method)


# ── Authorization request ────────────────────────────────────────────────────

def authorization_url(
    *, authorization_endpoint: str, client_id: str, redirect_uri: str, state: str,
    nonce: str, code_verifier: str, login_hint: str | None = None,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": providers.pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{httpx.QueryParams(params)}"


# ── Code exchange and ID token ───────────────────────────────────────────────

@dataclass(frozen=True)
class SsoClaims:
    subject: str
    email: str | None
    email_verified: bool
    full_name: str | None
    raw: dict


def exchange_code(
    *, token_endpoint: str, auth_method: str, client_id: str, client_secret: str,
    code: str, redirect_uri: str, code_verifier: str,
) -> str:
    """Trade the authorization code for the ID token string."""
    assert_public_https(token_endpoint, what="token_endpoint")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    headers = {"Accept": "application/json"}
    auth = None
    if auth_method == "client_secret_post":
        data["client_id"] = client_id
        data["client_secret"] = client_secret
    else:
        # RFC 6749 2.3.1: the id and secret are form-urlencoded BEFORE Basic.
        auth = (urllib.parse.quote(client_id, safe=""),
                urllib.parse.quote(client_secret, safe=""))
    try:
        with providers._client() as c:
            resp = c.post(token_endpoint, data=data, headers=headers, auth=auth)
    except httpx.TimeoutException:
        raise SocialAuthError("sso_provider_unreachable", "timeout")
    except httpx.HTTPError as exc:
        raise SocialAuthError("sso_provider_unreachable", str(exc)[:200])
    if resp.status_code >= 400 or len(resp.content) > MAX_BODY_BYTES:
        # The body may echo the code or the client id; log the status only.
        raise SocialAuthError("sso_token_invalid", f"token endpoint HTTP {resp.status_code}")
    try:
        tokens = resp.json()
    except ValueError:
        raise SocialAuthError("sso_token_invalid", "token endpoint answered with non-JSON")
    id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not isinstance(id_token, str) or not id_token:
        raise SocialAuthError("sso_token_invalid", "no id_token")
    return id_token


def verify(
    id_token: str, *, issuer: str, client_id: str, jwks_uri: str, nonce: str,
) -> SsoClaims:
    assert_public_https(jwks_uri, what="jwks_uri")
    try:
        claims = providers.verify_id_token(
            id_token, jwks_url=jwks_uri, issuers=(issuer,), audience=client_id,
            nonce=nonce,
        )
    except SocialAuthError as exc:
        # Same refusals as Google's, in this feature's own vocabulary.
        code = {"oauth_token_invalid": "sso_token_invalid",
                "oauth_provider_unreachable": "sso_provider_unreachable"}.get(exc.code, exc.code)
        raise SocialAuthError(code, exc.detail)
    except httpx.HTTPError as exc:
        raise SocialAuthError("sso_provider_unreachable", str(exc)[:200])

    # OIDC Core 3.1.3.7: with several audiences the authorized party MUST be
    # present and be us; with one, `azp` if present must still be us.
    aud = claims.get("aud")
    azp = claims.get("azp")
    if (isinstance(aud, list) and len(aud) > 1 and azp != client_id) or (
        azp is not None and azp != client_id
    ):
        raise SocialAuthError("sso_token_invalid", "authorized party mismatch")

    name = claims.get("name")
    email = claims.get("email")
    return SsoClaims(
        subject=str(claims["sub"]),
        email=email.strip().lower() if isinstance(email, str) and email.strip() else None,
        email_verified=providers._truthy(claims.get("email_verified")),
        full_name=name.strip()[:200] if isinstance(name, str) and name.strip() else None,
        raw=claims,
    )
