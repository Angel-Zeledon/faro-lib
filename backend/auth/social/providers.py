"""Google, Microsoft, Apple and Facebook — what each provider needs and how it answers.

Everything here talks to a provider; nothing here touches our database. The
flow (state, accounts, tokens) lives in `flow.py`.

The providers are deliberately NOT forced through one abstraction any
deeper than `Identity`:

  - **Google** is plain OpenID Connect: PKCE, a nonce, an ID token signed with
    a key from its JWKS, and an `email_verified` claim.
  - **Microsoft** is OpenID Connect against the multi-tenant `common`
    endpoint (work/school accounts AND personal accounts), with PKCE, a nonce
    and a confidential-client secret. Two twists: the ID token's issuer is
    tenant-specific (`https://login.microsoftonline.com/{tid}/v2.0`), so it is
    checked against the token's own signed `tid` claim after the signature
    passes; and Microsoft does NOT verify the `email` claim — it is a mutable
    attribute of the directory object (the "nOAuth" class of account takeover).
    An address is therefore treated as verified only when Microsoft says the
    tenant OWNS the email's domain (`xms_edov`, an optional claim the operator
    must add to the app registration) or sends `email_verified` itself.
    Without that proof the person is refused for a new account or an account
    link; an identity already linked by `sub` still signs in.
  - **Apple** is OpenID Connect with three twists: the client secret is a JWT
    this server signs (ES256) with the operator's .p8 key, the callback is a
    cross-site POST (`response_mode=form_post`, required whenever `email` is in
    the scope), and the email may be a private relay address — which is a real,
    verified mailbox and is accepted as such. PKCE is not sent: Apple does not
    document it for the web flow, and the client secret already authenticates
    the code exchange.
  - **Facebook** is not OIDC at all on the web: an access token, then the
    Graph API `/me`, signed with `appsecret_proof`. Facebook only returns an
    email it has confirmed, and returns none at all for accounts registered
    with a phone number — that person is told to use another method rather
    than being given an account with no address.

Configuration is read through `effective()` on every call, never cached: a
credential pasted into /instalacion takes effect on the next click.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
import jwt

from backend.service_config.resolver import effective

log = logging.getLogger(__name__)

# Display order of the buttons.
PROVIDERS: tuple[str, ...] = ("google", "microsoft", "apple", "facebook")

# Facebook pins its Graph API by version and retires each about two years after
# release. Bump it here when Meta's dashboard warns about the deprecation.
FACEBOOK_GRAPH_VERSION = "v23.0"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

# `common` accepts work/school (Entra ID) AND personal Microsoft accounts. The
# app registration must be "Accounts in any organizational directory and
# personal Microsoft accounts" or Microsoft refuses the sign-in itself.
MICROSOFT_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MICROSOFT_JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
MICROSOFT_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tid}/v2.0"
_GUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I,
)

APPLE_AUTH_URL = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"

FACEBOOK_AUTH_URL = f"https://www.facebook.com/{FACEBOOK_GRAPH_VERSION}/dialog/oauth"
FACEBOOK_TOKEN_URL = f"https://graph.facebook.com/{FACEBOOK_GRAPH_VERSION}/oauth/access_token"
FACEBOOK_ME_URL = f"https://graph.facebook.com/{FACEBOOK_GRAPH_VERSION}/me"

HTTP_TIMEOUT_S = 10.0

# Tests swap this for an `httpx.MockTransport`; nothing else should touch it.
# Real providers are never called from the suite.
_transport: httpx.BaseTransport | None = None


def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_S, transport=_transport)


class SocialAuthError(Exception):
    """A refusal with a stable code the frontend translates (`errors.<code>`)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Identity:
    """What a provider vouched for, normalised across the three."""

    provider: str
    subject: str
    email: str | None
    email_verified: bool
    full_name: str | None = None
    private_relay: bool = False


# ── Configuration ────────────────────────────────────────────────────────────

_FIELDS: dict[str, tuple[str, ...]] = {
    "google": ("google_oauth_client_id", "google_oauth_client_secret"),
    "facebook": ("facebook_oauth_app_id", "facebook_oauth_app_secret"),
    "microsoft": ("microsoft_oauth_client_id", "microsoft_oauth_client_secret"),
    "apple": ("apple_oauth_service_id", "apple_oauth_team_id",
              "apple_oauth_key_id", "apple_oauth_private_key"),
}


def master_switch_on() -> bool:
    return bool(effective().social_login_enabled)


def provider_configured(provider: str) -> bool:
    """Every field of the provider is present (switch not considered)."""
    cfg = effective()
    fields = _FIELDS.get(provider)
    if not fields:
        return False
    if not all(str(getattr(cfg, k) or "").strip() for k in fields):
        return False
    if provider == "apple":
        # A key that does not parse would show a button that can only fail
        # after the person has already authenticated at Apple. Refuse to offer
        # it; the panel's probe says why.
        try:
            _apple_signing_key(cfg.apple_oauth_private_key)
        except Exception:  # noqa: BLE001
            log.warning("[social] APPLE_OAUTH_PRIVATE_KEY does not parse; Apple hidden")
            return False
    return True


def enabled_providers() -> list[str]:
    """Providers whose button may be shown right now, in display order."""
    if not master_switch_on():
        return []
    return [p for p in PROVIDERS if provider_configured(p)]


def is_enabled(provider: str) -> bool:
    return provider in enabled_providers()


# ── PKCE / helpers ───────────────────────────────────────────────────────────

def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def uses_pkce(provider: str) -> bool:
    return provider in ("google", "microsoft", "facebook")


def uses_form_post(provider: str) -> bool:
    return provider == "apple"


# ── Authorization URL ────────────────────────────────────────────────────────

def authorization_url(
    provider: str, *, redirect_uri: str, state: str, nonce: str, code_verifier: str,
) -> str:
    cfg = effective()
    if provider == "google":
        params = {
            "client_id": cfg.google_oauth_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        base = GOOGLE_AUTH_URL
    elif provider == "microsoft":
        params = {
            "client_id": cfg.microsoft_oauth_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        base = MICROSOFT_AUTH_URL
    elif provider == "apple":
        params = {
            "client_id": cfg.apple_oauth_service_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "form_post",
            "scope": "name email",
            "state": state,
            "nonce": nonce,
        }
        base = APPLE_AUTH_URL
    elif provider == "facebook":
        params = {
            "client_id": cfg.facebook_oauth_app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "email,public_profile",
            "state": state,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        base = FACEBOOK_AUTH_URL
    else:
        raise SocialAuthError("social_provider_unavailable", provider)
    return f"{base}?{httpx.QueryParams(params)}"


# ── Apple client secret ──────────────────────────────────────────────────────

_PEM_BODY = re.compile(
    r"-----BEGIN (?P<label>[A-Z ]+)-----(?P<body>.*?)-----END (?P=label)-----", re.S,
)


def normalize_pem(raw: str) -> str:
    """Rebuild a PEM that lost its line breaks on the way in.

    The panel's secret input is a single-line field, and browsers strip the
    newlines out of anything pasted into one; an env file often carries them
    as a literal backslash-n. Both arrive as a PEM that `cryptography` refuses.
    The base64 body is whitespace-insensitive, so re-wrapping it is lossless.
    """
    text = (raw or "").strip().replace("\\n", "\n")
    m = _PEM_BODY.search(text)
    if not m:
        return text
    body = re.sub(r"\s+", "", m.group("body"))
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    label = m.group("label")
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


def _apple_signing_key(raw: str):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(normalize_pem(raw).encode("ascii"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("Apple keys are EC P-256 (.p8); this is another kind of key")
    return key


def apple_client_secret(now: float | None = None) -> str:
    """The ES256 JWT Apple accepts as `client_secret`.

    Short-lived on purpose (5 minutes, Apple allows six months): it is built
    per exchange, so there is no long-lived bearer secret sitting anywhere.
    """
    cfg = effective()
    issued = int(now if now is not None else time.time())
    claims = {
        "iss": cfg.apple_oauth_team_id,
        "iat": issued,
        "exp": issued + 300,
        "aud": APPLE_ISSUER,
        "sub": cfg.apple_oauth_service_id,
    }
    return jwt.encode(
        claims, _apple_signing_key(cfg.apple_oauth_private_key),
        algorithm="ES256", headers={"kid": cfg.apple_oauth_key_id},
    )


# ── JWKS ─────────────────────────────────────────────────────────────────────

_JWKS_TTL_S = 3600
_jwks_lock = threading.Lock()
_jwks_cache: dict[str, tuple[float, dict]] = {}


def _fetch_jwks(url: str, *, force: bool = False) -> dict:
    with _jwks_lock:
        hit = _jwks_cache.get(url)
        if hit and not force and time.time() - hit[0] < _JWKS_TTL_S:
            return hit[1]
    with _client() as c:
        resp = c.get(url)
        resp.raise_for_status()
        data = resp.json()
    with _jwks_lock:
        _jwks_cache[url] = (time.time(), data)
    return data


def clear_jwks_cache() -> None:
    with _jwks_lock:
        _jwks_cache.clear()


def _signing_key(url: str, kid: str | None):
    """The provider's public key for `kid`, refetching once on a miss — keys
    rotate, and a cache that never notices is a login outage on rotation day."""
    for force in (False, True):
        for jwk in _fetch_jwks(url, force=force).get("keys", []):
            if jwk.get("kid") == kid:
                return jwt.PyJWK(jwk).key
    raise SocialAuthError("oauth_token_invalid", f"No JWKS key for kid={kid!r}")


def verify_id_token(
    id_token: str, *, jwks_url: str, issuers: tuple[str, ...] | None, audience: str,
    nonce: str, issuer_check: Callable[[dict], bool] | None = None,
) -> dict:
    """Signature (provider JWKS), iss, aud, exp, iat and nonce — all of them.

    `issuers=None` is for a provider whose issuer depends on the token
    (Microsoft's `common` endpoint): PyJWT then skips its own issuer
    comparison and `issuer_check` MUST be given. It runs on the claims after
    the signature has been verified, so it can trust them. Passing neither is
    a programming error, never a silent "any issuer".
    """
    if issuers is None and issuer_check is None:
        raise ValueError("verify_id_token needs `issuers` or `issuer_check`")
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.InvalidTokenError as exc:
        raise SocialAuthError("oauth_token_invalid", f"Malformed ID token: {exc}")
    if header.get("alg") != "RS256":
        raise SocialAuthError("oauth_token_invalid", f"Unexpected alg {header.get('alg')!r}")
    key = _signing_key(jwks_url, header.get("kid"))
    try:
        claims = jwt.decode(
            id_token, key, algorithms=["RS256"], audience=audience,
            issuer=list(issuers) if issuers is not None else None,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=60,
        )
    except jwt.InvalidTokenError as exc:
        raise SocialAuthError("oauth_token_invalid", f"ID token rejected: {exc}")
    if issuer_check is not None and not issuer_check(claims):
        raise SocialAuthError("oauth_token_invalid", f"Issuer rejected: {claims.get('iss')!r}")
    if not nonce or not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise SocialAuthError("oauth_token_invalid", "Nonce mismatch")
    return claims


def _truthy(value: Any) -> bool:
    """Apple sends `email_verified` as the STRING "true"; Google as a bool."""
    return value is True or (isinstance(value, str) and value.lower() == "true")


# ── Code exchange, per provider ──────────────────────────────────────────────

def _post_form(url: str, data: dict) -> dict:
    try:
        with _client() as c:
            resp = c.post(url, data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise SocialAuthError("oauth_provider_unreachable", str(exc)[:200])
    if resp.status_code >= 400:
        raise SocialAuthError(
            "oauth_token_invalid", f"Token endpoint HTTP {resp.status_code}: {resp.text[:200]}",
        )
    return resp.json()


def _exchange_google(code: str, redirect_uri: str, verifier: str, nonce: str) -> Identity:
    cfg = effective()
    tokens = _post_form(GOOGLE_TOKEN_URL, {
        "code": code,
        "client_id": cfg.google_oauth_client_id,
        "client_secret": cfg.google_oauth_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    })
    if not tokens.get("id_token"):
        raise SocialAuthError("oauth_token_invalid", "Google returned no id_token")
    claims = verify_id_token(
        tokens["id_token"], jwks_url=GOOGLE_JWKS_URL, issuers=GOOGLE_ISSUERS,
        audience=cfg.google_oauth_client_id, nonce=nonce,
    )
    return Identity(
        provider="google",
        subject=str(claims["sub"]),
        email=(claims.get("email") or None),
        email_verified=_truthy(claims.get("email_verified")),
        full_name=claims.get("name") or None,
    )


def _microsoft_issuer_ok(claims: dict) -> bool:
    """The issuer must be exactly the v2.0 issuer of the tenant the token names.

    Both values are inside the signed payload, so this is not a self-attested
    check: it stops a token minted for the v1.0 endpoint (`sts.windows.net`),
    or one whose `tid` is not a GUID, from being accepted.
    """
    tid = str(claims.get("tid") or "")
    return bool(_GUID.match(tid)) and claims.get("iss") == MICROSOFT_ISSUER_TEMPLATE.format(tid=tid)


def _microsoft_email(claims: dict) -> tuple[str | None, bool]:
    """(address, provider-verified?) from Microsoft's claims.

    `email` is optional (absent for many work accounts) and, unlike Google's,
    NOT verified: a directory admin or a guest invitation can put any string
    in it. `preferred_username` and `upn` are display handles, never used
    here. The only proofs accepted are `xms_edov` (the tenant has verified
    ownership of the address's domain; optional claim) or an explicit
    `email_verified`; both count only as JSON true or the string "true".
    """
    email = (claims.get("email") or "").strip() or None
    if not email:
        return None, False
    return email, _truthy(claims.get("xms_edov")) or _truthy(claims.get("email_verified"))


def _exchange_microsoft(code: str, redirect_uri: str, verifier: str, nonce: str) -> Identity:
    cfg = effective()
    tokens = _post_form(MICROSOFT_TOKEN_URL, {
        "code": code,
        "client_id": cfg.microsoft_oauth_client_id,
        "client_secret": cfg.microsoft_oauth_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
        "scope": "openid email profile",
    })
    if not tokens.get("id_token"):
        raise SocialAuthError("oauth_token_invalid", "Microsoft returned no id_token")
    claims = verify_id_token(
        tokens["id_token"], jwks_url=MICROSOFT_JWKS_URL, issuers=None,
        audience=cfg.microsoft_oauth_client_id, nonce=nonce,
        issuer_check=_microsoft_issuer_ok,
    )
    email, verified = _microsoft_email(claims)
    return Identity(
        provider="microsoft",
        subject=str(claims["sub"]),
        email=email,
        email_verified=verified,
        full_name=claims.get("name") or None,
    )


def _exchange_apple(
    code: str, redirect_uri: str, nonce: str, user_json: dict | None,
) -> Identity:
    cfg = effective()
    tokens = _post_form(APPLE_TOKEN_URL, {
        "code": code,
        "client_id": cfg.apple_oauth_service_id,
        "client_secret": apple_client_secret(),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    if not tokens.get("id_token"):
        raise SocialAuthError("oauth_token_invalid", "Apple returned no id_token")
    claims = verify_id_token(
        tokens["id_token"], jwks_url=APPLE_JWKS_URL, issuers=(APPLE_ISSUER,),
        audience=cfg.apple_oauth_service_id, nonce=nonce,
    )
    # Apple sends the person's name ONCE, on the very first authorization, in
    # the form post — never in the token. Best effort; it is only a display name.
    name = None
    if isinstance(user_json, dict):
        n = user_json.get("name") or {}
        name = " ".join(x for x in (n.get("firstName"), n.get("lastName")) if x) or None
    return Identity(
        provider="apple",
        subject=str(claims["sub"]),
        email=(claims.get("email") or None),
        email_verified=_truthy(claims.get("email_verified")),
        full_name=name,
        private_relay=_truthy(claims.get("is_private_email")),
    )


def appsecret_proof(access_token: str, app_secret: str) -> str:
    return hmac.new(app_secret.encode(), access_token.encode(), hashlib.sha256).hexdigest()


def _exchange_facebook(code: str, redirect_uri: str, verifier: str) -> Identity:
    cfg = effective()
    try:
        with _client() as c:
            resp = c.get(FACEBOOK_TOKEN_URL, params={
                "client_id": cfg.facebook_oauth_app_id,
                "client_secret": cfg.facebook_oauth_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
                "code_verifier": verifier,
            })
            if resp.status_code >= 400:
                raise SocialAuthError(
                    "oauth_token_invalid", f"Facebook token HTTP {resp.status_code}: {resp.text[:200]}",
                )
            access_token = resp.json().get("access_token")
            if not access_token:
                raise SocialAuthError("oauth_token_invalid", "Facebook returned no access_token")
            me = c.get(FACEBOOK_ME_URL, params={
                "fields": "id,name,email",
                "access_token": access_token,
                "appsecret_proof": appsecret_proof(access_token, cfg.facebook_oauth_app_secret),
            })
    except httpx.HTTPError as exc:
        raise SocialAuthError("oauth_provider_unreachable", str(exc)[:200])
    if me.status_code >= 400:
        raise SocialAuthError("oauth_token_invalid", f"Graph /me HTTP {me.status_code}")
    data = me.json()
    if not data.get("id"):
        raise SocialAuthError("oauth_token_invalid", "Graph /me returned no id")
    email = data.get("email") or None
    return Identity(
        provider="facebook",
        subject=str(data["id"]),
        email=email,
        # Graph only ever returns a confirmed address; absence is the signal.
        email_verified=bool(email),
        full_name=data.get("name") or None,
    )


def exchange_code(
    provider: str, *, code: str, redirect_uri: str, code_verifier: str, nonce: str,
    apple_user: dict | None = None,
) -> Identity:
    if provider == "google":
        return _exchange_google(code, redirect_uri, code_verifier, nonce)
    if provider == "microsoft":
        return _exchange_microsoft(code, redirect_uri, code_verifier, nonce)
    if provider == "apple":
        return _exchange_apple(code, redirect_uri, nonce, apple_user)
    if provider == "facebook":
        return _exchange_facebook(code, redirect_uri, code_verifier)
    raise SocialAuthError("social_provider_unavailable", provider)


# ── Probe (for /instalacion) ─────────────────────────────────────────────────

def _probe_token_endpoint(post: Callable[[], httpx.Response]) -> str:
    """Send a deliberately bogus code. A provider that recognises the client
    answers `invalid_grant`; one that does not answers `invalid_client`. That
    distinguishes a wrong secret from a working one without a real login."""
    try:
        resp = post()
    except httpx.TimeoutException:
        return "timeout"
    except httpx.HTTPError:
        return "unreachable"
    try:
        err = (resp.json() or {}).get("error")
    except ValueError:
        err = None
    if err in ("invalid_client", "unauthorized_client"):
        return "auth_failed"
    if err == "invalid_grant":
        return "ok"
    return "rejected"


def probe_provider(provider: str) -> str:
    """One stable code per provider: ok / auth_failed / unreachable / timeout /
    rejected / not_configured."""
    if not provider_configured(provider):
        return "not_configured"
    cfg = effective()
    if provider == "google":
        def post():
            with _client() as c:
                return c.post(GOOGLE_TOKEN_URL, data={
                    "code": "stockai-probe", "client_id": cfg.google_oauth_client_id,
                    "client_secret": cfg.google_oauth_client_secret,
                    "redirect_uri": "https://example.invalid/", "grant_type": "authorization_code",
                })
        return _probe_token_endpoint(post)
    if provider == "microsoft":
        def post():
            with _client() as c:
                return c.post(MICROSOFT_TOKEN_URL, data={
                    "code": "stockai-probe", "client_id": cfg.microsoft_oauth_client_id,
                    "client_secret": cfg.microsoft_oauth_client_secret,
                    "redirect_uri": "https://example.invalid/", "grant_type": "authorization_code",
                    "scope": "openid email profile",
                })
        return _probe_token_endpoint(post)
    if provider == "apple":
        def post():
            with _client() as c:
                return c.post(APPLE_TOKEN_URL, data={
                    "code": "stockai-probe", "client_id": cfg.apple_oauth_service_id,
                    "client_secret": apple_client_secret(), "grant_type": "authorization_code",
                })
        return _probe_token_endpoint(post)
    if provider == "facebook":
        # An app access token proves the id/secret pair directly.
        try:
            with _client() as c:
                resp = c.get(FACEBOOK_TOKEN_URL, params={
                    "client_id": cfg.facebook_oauth_app_id,
                    "client_secret": cfg.facebook_oauth_app_secret,
                    "grant_type": "client_credentials",
                })
        except httpx.TimeoutException:
            return "timeout"
        except httpx.HTTPError:
            return "unreachable"
        if resp.status_code == 200 and "access_token" in resp.text:
            return "ok"
        return "auth_failed" if resp.status_code in (400, 401, 403) else "rejected"
    return "not_configured"
