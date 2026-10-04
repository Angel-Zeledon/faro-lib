"""Social sign-in (Google, Apple, Facebook) — backend/auth/social/.

Every provider is faked with an `httpx.MockTransport`: the token endpoint, the
JWKS and the Graph API answer from this file, and ID tokens are signed with a
key generated here. No test reaches a real provider.

State is asserted in the database, not from the redirect alone: a sign-in that
redirects to the right page while creating the wrong rows is the failure this
file exists to catch.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from backend.auth.social import flow, providers
from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.service_config import store

FRONTEND = "http://localhost:5000"

# ── Fake providers ───────────────────────────────────────────────────────────

_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-kid-1"
_JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_RSA.public_key())),
        "kid": _KID, "alg": "RS256", "use": "sig"}

_APPLE_EC = ec.generate_private_key(ec.SECP256R1())
_APPLE_PEM = _APPLE_EC.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()


def _id_token(*, iss: str, aud: str, sub: str, nonce: str, email: str | None,
              email_verified=True, extra: dict | None = None, key=_RSA, kid=_KID) -> str:
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": sub, "nonce": nonce,
              "iat": now, "exp": now + 600, **(extra or {})}
    if email is not None:
        claims["email"] = email
        claims["email_verified"] = email_verified
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class FakeProviders:
    """Answers the provider URLs; records every request it saw."""

    def __init__(self):
        self.id_token: str | None = None
        self.fb_me: dict = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        if url in (providers.GOOGLE_JWKS_URL, providers.APPLE_JWKS_URL):
            return httpx.Response(200, json={"keys": [_JWK]})
        if url in (providers.GOOGLE_TOKEN_URL, providers.APPLE_TOKEN_URL):
            return httpx.Response(200, json={"id_token": self.id_token, "access_token": "x"})
        if url == providers.FACEBOOK_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "fb-access-token"})
        if url == providers.FACEBOOK_ME_URL:
            return httpx.Response(200, json=self.fb_me)
        return httpx.Response(404, json={"error": "unexpected url " + url})


@pytest.fixture
def fake(monkeypatch):
    f = FakeProviders()
    monkeypatch.setattr(providers, "_transport", httpx.MockTransport(f))
    providers.clear_jwks_cache()
    yield f
    providers.clear_jwks_cache()


def _set(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setattr(f"backend.config.settings.{k}", v)


@pytest.fixture
def social_off(monkeypatch, client):
    """Every social setting at its default, and no stored override shadowing it."""
    execute("DELETE FROM service_config WHERE service = 'social_login'")
    store.invalidate()
    _set(monkeypatch,
         social_login_enabled=False,
         google_oauth_client_id="", google_oauth_client_secret="",
         facebook_oauth_app_id="", facebook_oauth_app_secret="",
         apple_oauth_service_id="", apple_oauth_team_id="",
         apple_oauth_key_id="", apple_oauth_private_key="",
         frontend_url=FRONTEND)
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.fixture
def all_on(monkeypatch, social_off):
    _set(monkeypatch,
         social_login_enabled=True,
         google_oauth_client_id="google-client.apps.googleusercontent.com",
         google_oauth_client_secret="google-secret",
         facebook_oauth_app_id="fb-app-id", facebook_oauth_app_secret="fb-app-secret",
         apple_oauth_service_id="es.stockai.signin", apple_oauth_team_id="TEAM123456",
         apple_oauth_key_id="KEY1234567", apple_oauth_private_key=_APPLE_PEM)


@pytest.fixture
def cleanup_emails():
    """Accounts a test creates through a provider get their tenant erased."""
    emails: list[str] = []
    yield emails
    for e in emails:
        row = query_one("SELECT tenant_id FROM users WHERE email = %s", (e.lower(),))
        if row:
            execute("DELETE FROM tenants WHERE id = %s", (row["tenant_id"],))


def _start(client, provider: str, *, intent="login", terms=1):
    """Run /start; return (provider url params, state, binding cookie value)."""
    client.cookies.clear()
    r = client.get(
        f"/api/v1/auth/oauth/{provider}/start",
        params={"intent": intent, "terms": terms}, follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    params = {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()}
    binding = r.cookies.get(flow.BINDING_COOKIE)
    client.cookies.clear()
    return loc, params, binding


def _callback(client, provider: str, params: dict, binding: str | None, *, method="GET",
              code="auth-code-from-provider", extra: dict | None = None):
    client.cookies.clear()
    if binding:
        client.cookies.set(flow.BINDING_COOKIE, binding)
    data = {"code": code, "state": params.get("state"), **(extra or {})}
    if method == "GET":
        r = client.get(f"/api/v1/auth/oauth/{provider}/callback", params=data,
                       follow_redirects=False)
    else:
        r = client.post(f"/api/v1/auth/oauth/{provider}/callback", data=data,
                        follow_redirects=False)
    client.cookies.clear()
    assert r.status_code == 302, r.text
    return r.headers["location"]


def _google_token(params, email, *, sub=None, verified=True, aud=None, nonce=None):
    return _id_token(
        iss="https://accounts.google.com",
        aud=aud or settings.google_oauth_client_id,
        sub=sub or f"g-{uuid4().hex}", nonce=nonce or params["nonce"],
        email=email, email_verified=verified,
    )


def _error_of(location: str) -> str | None:
    return parse_qs(urlparse(location).query).get("oauth_error", [None])[0]


def _handoff_code(location: str) -> str:
    assert location.startswith(f"{FRONTEND}/auth/callback#code="), location
    return location.split("#code=", 1)[1]


# ── Which buttons ────────────────────────────────────────────────────────────

class TestProvidersEndpoint:
    def test_off_by_default(self, client, social_off):
        r = client.get("/api/v1/auth/providers")
        assert r.status_code == 200
        assert r.json()["data"]["providers"] == []

    def test_master_switch_off_hides_fully_configured_providers(
        self, client, all_on, monkeypatch,
    ):
        monkeypatch.setattr("backend.config.settings.social_login_enabled", False)
        assert client.get("/api/v1/auth/providers").json()["data"]["providers"] == []

    def test_all_three_when_configured(self, client, all_on):
        assert client.get("/api/v1/auth/providers").json()["data"]["providers"] == [
            "google", "apple", "facebook",
        ]

    def test_a_half_configured_provider_is_not_offered(self, client, all_on, monkeypatch):
        monkeypatch.setattr("backend.config.settings.facebook_oauth_app_secret", "")
        assert "facebook" not in client.get("/api/v1/auth/providers").json()["data"]["providers"]

    def test_an_apple_key_that_does_not_parse_is_not_offered(self, client, all_on, monkeypatch):
        monkeypatch.setattr("backend.config.settings.apple_oauth_private_key", "not a key")
        assert client.get("/api/v1/auth/providers").json()["data"]["providers"] == [
            "google", "facebook",
        ]

    def test_an_apple_key_pasted_on_one_line_still_works(self, client, all_on, monkeypatch):
        """The panel's input strips newlines from a pasted .p8."""
        monkeypatch.setattr(
            "backend.config.settings.apple_oauth_private_key", _APPLE_PEM.replace("\n", ""),
        )
        assert "apple" in client.get("/api/v1/auth/providers").json()["data"]["providers"]

    def test_the_panel_reports_the_paused_service_as_off(self, all_on, monkeypatch):
        from backend.service_config.registry import BY_KEY
        from backend.service_config.status import forget_probe, service_report

        # A probe another test ran against the unconfigured service would read
        # as `degraded`; the panel forgets it on a config change, and so do we.
        forget_probe("social_login")
        assert service_report(BY_KEY["social_login"])["state"] == "ready"
        monkeypatch.setattr("backend.config.settings.social_login_enabled", False)
        assert service_report(BY_KEY["social_login"])["state"] == "off"


# ── Start ────────────────────────────────────────────────────────────────────

class TestStart:
    def test_google_redirect_carries_state_pkce_and_nonce(self, client, all_on):
        loc, p, binding = _start(client, "google")
        assert loc.startswith(providers.GOOGLE_AUTH_URL + "?")
        assert p["client_id"] == "google-client.apps.googleusercontent.com"
        assert p["redirect_uri"] == f"{FRONTEND}/api/v1/auth/oauth/google/callback"
        assert p["response_type"] == "code"
        assert set(p["scope"].split()) == {"openid", "email", "profile"}
        assert p["code_challenge_method"] == "S256"
        assert len(p["state"]) >= 32 and len(p["nonce"]) >= 32
        assert binding, "the browser-binding cookie was not set"

        row = query_one(
            "SELECT * FROM oauth_flows WHERE state_hash = %s",
            (hashlib.sha256(p["state"].encode()).hexdigest(),),
        )
        assert row is not None and row["provider"] == "google"
        assert row["nonce"] == p["nonce"]
        assert p["code_challenge"] == providers.pkce_challenge(row["code_verifier"])
        assert row["binding_hash"] == hashlib.sha256(binding.encode()).hexdigest()
        # Raw state is never stored.
        assert query_one("SELECT 1 FROM oauth_flows WHERE state_hash = %s", (p["state"],)) is None

    def test_apple_uses_form_post(self, client, all_on):
        loc, p, _ = _start(client, "apple")
        assert loc.startswith(providers.APPLE_AUTH_URL + "?")
        assert p["response_mode"] == "form_post"
        assert p["client_id"] == "es.stockai.signin"
        assert p["redirect_uri"] == f"{FRONTEND}/api/v1/auth/oauth/apple/callback"

    def test_facebook_redirect(self, client, all_on):
        loc, p, _ = _start(client, "facebook")
        assert loc.startswith(providers.FACEBOOK_AUTH_URL + "?")
        assert "email" in p["scope"]
        assert p["code_challenge_method"] == "S256"

    def test_a_disabled_provider_sends_back_to_login_and_writes_nothing(self, client, social_off):
        before = query_one("SELECT COUNT(*) AS n FROM oauth_flows")["n"]
        r = client.get("/api/v1/auth/oauth/google/start", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == f"{FRONTEND}/login?oauth_error=social_provider_unavailable"
        assert query_one("SELECT COUNT(*) AS n FROM oauth_flows")["n"] <= before


# ── Callback ─────────────────────────────────────────────────────────────────

class TestGoogleCallback:
    def test_new_person_gets_a_tenant_and_an_admin(self, client, all_on, fake, cleanup_emails):
        email = f"new-{uuid4().hex[:8]}@gmail.com"
        cleanup_emails.append(email)
        _, p, binding = _start(client, "google", intent="signup")
        fake.id_token = _google_token(p, email)
        loc = _callback(client, "google", p, binding)

        assert "token" not in loc.lower(), "a token travelled in a URL"
        code = _handoff_code(loc)

        user = query_one("SELECT * FROM users WHERE email = %s", (email,))
        assert user is not None
        assert user["role"] == "admin"
        assert user["email_verified"] is True
        assert user["has_password"] is False
        tenant = query_one("SELECT * FROM tenants WHERE id = %s", (user["tenant_id"],))
        assert tenant is not None and tenant["tier"] == "free"
        ident = query("SELECT * FROM user_identities WHERE user_id = %s", (user["id"],))
        assert len(ident) == 1 and ident[0]["provider"] == "google"
        assert ident[0]["tenant_id"] == user["tenant_id"]
        ev = query_one(
            "SELECT * FROM activity_logs WHERE user_id = %s AND action = %s",
            (user["id"], "account.signed_up_with_provider"),
        )
        assert ev is not None

        # The token exchange token request carried our PKCE verifier.
        token_req = next(r for r in fake.requests if str(r.url) == providers.GOOGLE_TOKEN_URL)
        assert b"code_verifier=" in token_req.content

        # The handoff code buys tokens exactly once.
        r = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["is_new_account"] is True and data["user"]["email"] == email
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        me = client.get("/api/v1/auth/identities", headers=headers)
        assert me.status_code == 200
        assert me.json()["data"]["has_password"] is False
        assert [i["provider"] for i in me.json()["data"]["identities"]] == ["google"]

        again = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
        assert again.status_code == 400
        assert again.json()["error_code"] == "oauth_exchange_invalid"

    def test_a_provider_only_account_cannot_be_opened_with_a_password(
        self, client, all_on, fake, cleanup_emails,
    ):
        email = f"nopw-{uuid4().hex[:8]}@gmail.com"
        cleanup_emails.append(email)
        _, p, binding = _start(client, "google")
        fake.id_token = _google_token(p, email)
        _handoff_code(_callback(client, "google", p, binding))
        # Whatever the hash is, no password opens it.
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "TestPass123!"})
        assert r.status_code == 401

    def test_existing_verified_email_is_linked_not_duplicated(
        self, client, all_on, fake, registered_user,
    ):
        email = registered_user["email"]
        users_before = query_one("SELECT COUNT(*) AS n FROM users")["n"]
        tenants_before = query_one("SELECT COUNT(*) AS n FROM tenants")["n"]
        _, p, binding = _start(client, "google")
        fake.id_token = _google_token(p, email.upper())  # case must not matter
        code = _handoff_code(_callback(client, "google", p, binding))

        assert query_one("SELECT COUNT(*) AS n FROM users")["n"] == users_before
        assert query_one("SELECT COUNT(*) AS n FROM tenants")["n"] == tenants_before
        uid = registered_user["user"]["id"]
        assert query_one(
            "SELECT 1 FROM user_identities WHERE user_id = %s AND provider = 'google'", (uid,),
        ) is not None
        ev = query_one(
            "SELECT context FROM activity_logs WHERE user_id = %s AND action = %s",
            (uid, "account.provider_linked"),
        )
        assert ev is not None and ev["context"].get("reason") == "linked_at_provider_sign_in"
        # Their password still works: a verified account loses nothing.
        r = client.post("/api/v1/auth/login", json={
            "email": email, "password": registered_user["password"]})
        assert r.status_code == 200
        r = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
        assert r.json()["data"]["user"]["id"] == uid
        assert r.json()["data"]["is_new_account"] is False

    def test_linking_an_unverified_account_drops_the_squatters_password(
        self, client, all_on, fake, test_tenant,
    ):
        from backend.users import service as user_svc

        email = f"squat-{uuid4().hex[:8]}@example.com"
        squatter = user_svc.create_user(
            tenant_id=test_tenant["id"], email=email, password="Squatter123!", role="admin",
        )
        _, p, binding = _start(client, "google")
        fake.id_token = _google_token(p, email)
        _handoff_code(_callback(client, "google", p, binding))

        row = query_one("SELECT * FROM users WHERE id = %s", (squatter["id"],))
        assert row["email_verified"] is True
        assert row["has_password"] is False
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "Squatter123!"})
        assert r.status_code == 401, "the unverified password still opens the account"
        ev = query_one(
            "SELECT context FROM activity_logs WHERE user_id = %s AND action = %s",
            (squatter["id"], "account.provider_linked"),
        )
        assert ev["context"].get("reason") == "linked_unverified_password_removed"

    def test_a_returning_identity_signs_in_even_after_its_email_changed(
        self, client, all_on, fake, registered_user,
    ):
        sub = f"g-{uuid4().hex}"
        _, p, b = _start(client, "google")
        fake.id_token = _google_token(p, registered_user["email"], sub=sub)
        _handoff_code(_callback(client, "google", p, b))
        _, p, b = _start(client, "google")
        fake.id_token = _google_token(p, f"renamed-{uuid4().hex[:6]}@gmail.com", sub=sub)
        code = _handoff_code(_callback(client, "google", p, b))
        r = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
        assert r.json()["data"]["user"]["id"] == registered_user["user"]["id"]

    def test_unverified_provider_email_is_refused(self, client, all_on, fake):
        email = f"unv-{uuid4().hex[:8]}@gmail.com"
        _, p, binding = _start(client, "google")
        fake.id_token = _google_token(p, email, verified=False)
        loc = _callback(client, "google", p, binding)
        assert _error_of(loc) == "oauth_email_unverified"
        assert query_one("SELECT 1 FROM users WHERE email = %s", (email,)) is None

    def test_a_replayed_state_is_refused(self, client, all_on, fake, cleanup_emails):
        email = f"replay-{uuid4().hex[:8]}@gmail.com"
        cleanup_emails.append(email)
        _, p, binding = _start(client, "google")
        fake.id_token = _google_token(p, email)
        _handoff_code(_callback(client, "google", p, binding))
        handoffs = query_one("SELECT COUNT(*) AS n FROM oauth_handoffs")["n"]
        loc = _callback(client, "google", p, binding)
        assert _error_of(loc) == "oauth_state_invalid"
        assert query_one("SELECT COUNT(*) AS n FROM oauth_handoffs")["n"] == handoffs

    def test_a_callback_from_another_browser_is_refused(self, client, all_on, fake):
        """Login CSRF: the attacker's half-finished flow, opened by a victim."""
        email = f"csrf-{uuid4().hex[:8]}@gmail.com"
        _, p, _binding = _start(client, "google")
        fake.id_token = _google_token(p, email)
        loc = _callback(client, "google", p, binding=None)
        assert _error_of(loc) == "oauth_state_invalid"
        assert query_one("SELECT 1 FROM users WHERE email = %s", (email,)) is None
        loc = _callback(client, "google", p, binding="someone-elses-cookie")
        assert _error_of(loc) == "oauth_state_invalid"

    def test_an_expired_state_is_refused(self, client, all_on, fake):
        _, p, binding = _start(client, "google")
        execute(
            "UPDATE oauth_flows SET expires_at = NOW() - INTERVAL '1 minute' WHERE state_hash = %s",
            (hashlib.sha256(p["state"].encode()).hexdigest(),),
        )
        fake.id_token = _google_token(p, f"late-{uuid4().hex[:6]}@gmail.com")
        assert _error_of(_callback(client, "google", p, binding)) == "oauth_state_invalid"

    @pytest.mark.parametrize("bad", ["aud", "nonce", "signature"])
    def test_an_id_token_that_fails_validation_is_refused(self, client, all_on, fake, bad):
        email = f"bad-{uuid4().hex[:8]}@gmail.com"
        _, p, binding = _start(client, "google")
        if bad == "aud":
            fake.id_token = _google_token(p, email, aud="someone-elses-client")
        elif bad == "nonce":
            fake.id_token = _google_token(p, email, nonce="a-different-nonce")
        else:
            other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            fake.id_token = _id_token(
                iss="https://accounts.google.com", aud=settings.google_oauth_client_id,
                sub="x", nonce=p["nonce"], email=email, key=other,
            )
        assert _error_of(_callback(client, "google", p, binding)) == "oauth_token_invalid"
        assert query_one("SELECT 1 FROM users WHERE email = %s", (email,)) is None

    def test_a_suspended_user_is_refused(self, client, all_on, fake, registered_user):
        uid = registered_user["user"]["id"]
        execute("UPDATE users SET status = 'inactive' WHERE id = %s", (uid,))
        _, p, binding = _start(client, "google")
        fake.id_token = _google_token(p, registered_user["email"])
        loc = _callback(client, "google", p, binding)
        assert _error_of(loc) == "account_not_active"
        assert query_one("SELECT 1 FROM oauth_handoffs WHERE user_id = %s", (uid,)) is None

    def test_an_expired_trial_is_refused(self, client, all_on, fake, registered_user):
        from backend.entitlements.plans import DEMO

        execute(
            "UPDATE tenants SET tier = %s, trial_ends_at = %s WHERE id = %s",
            (DEMO, datetime.now(timezone.utc) - timedelta(hours=1),
             registered_user["tenant"]["id"]),
        )
        _, p, binding = _start(client, "google")
        fake.id_token = _google_token(p, registered_user["email"])
        assert _error_of(_callback(client, "google", p, binding)) == "trial_account_expired"

    def test_cancelling_at_the_provider_consumes_the_state(self, client, all_on, fake):
        _, p, binding = _start(client, "google", intent="signup")
        loc = _callback(client, "google", p, binding, extra={"error": "access_denied"})
        assert loc == f"{FRONTEND}/signup?oauth_error=oauth_cancelled"
        assert query_one(
            "SELECT 1 FROM oauth_flows WHERE state_hash = %s",
            (hashlib.sha256(p["state"].encode()).hexdigest(),),
        ) is None

    def test_no_new_account_without_the_terms_statement(self, client, all_on, fake):
        email = f"noterms-{uuid4().hex[:8]}@gmail.com"
        _, p, binding = _start(client, "google", terms=0)
        fake.id_token = _google_token(p, email)
        assert _error_of(_callback(client, "google", p, binding)) == "oauth_terms_required"
        assert query_one("SELECT 1 FROM users WHERE email = %s", (email,)) is None


# ── Facebook ─────────────────────────────────────────────────────────────────

class TestFacebook:
    def test_without_an_email_the_person_is_asked_to_use_another_method(
        self, client, all_on, fake,
    ):
        _, p, binding = _start(client, "facebook")
        fake.fb_me = {"id": f"fb-{uuid4().hex}", "name": "Phone Only"}
        loc = _callback(client, "facebook", p, binding)
        assert _error_of(loc) == "oauth_email_missing"
        assert query_one(
            "SELECT 1 FROM user_identities WHERE subject = %s", (fake.fb_me["id"],),
        ) is None

    def test_graph_calls_are_signed_with_appsecret_proof(
        self, client, all_on, fake, cleanup_emails,
    ):
        email = f"fb-{uuid4().hex[:8]}@example.com"
        cleanup_emails.append(email)
        _, p, binding = _start(client, "facebook")
        fake.fb_me = {"id": f"fb-{uuid4().hex}", "name": "Ana Pérez", "email": email}
        _handoff_code(_callback(client, "facebook", p, binding))
        me_req = next(r for r in fake.requests if str(r.url).startswith(providers.FACEBOOK_ME_URL))
        q = parse_qs(urlparse(str(me_req.url)).query)
        assert q["appsecret_proof"][0] == providers.appsecret_proof("fb-access-token", "fb-app-secret")
        user = query_one("SELECT * FROM users WHERE email = %s", (email,))
        assert user is not None and user["full_name"] == "Ana Pérez"


# ── Apple ────────────────────────────────────────────────────────────────────

class TestApple:
    def test_client_secret_is_an_es256_jwt_apple_can_verify(self, all_on):
        token = providers.apple_client_secret()
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "ES256" and header["kid"] == "KEY1234567"
        claims = jwt.decode(
            token, _APPLE_EC.public_key(), algorithms=["ES256"],
            audience="https://appleid.apple.com",
        )
        assert claims["iss"] == "TEAM123456"
        assert claims["sub"] == "es.stockai.signin"
        assert 0 < claims["exp"] - claims["iat"] <= 15777000  # Apple's 6-month cap

    def test_form_post_callback_with_a_private_relay_email(
        self, client, all_on, fake, cleanup_emails,
    ):
        email = f"{uuid4().hex[:10]}@privaterelay.appleid.com"
        cleanup_emails.append(email)
        _, p, binding = _start(client, "apple")
        fake.id_token = _id_token(
            iss="https://appleid.apple.com", aud="es.stockai.signin",
            sub=f"apple-{uuid4().hex}", nonce=p["nonce"], email=email,
            email_verified="true", extra={"is_private_email": "true"},
        )
        loc = _callback(
            client, "apple", p, binding, method="POST",
            extra={"user": json.dumps({"name": {"firstName": "Lucía", "lastName": "Mora"}})},
        )
        _handoff_code(loc)
        user = query_one("SELECT * FROM users WHERE email = %s", (email,))
        assert user is not None and user["full_name"] == "Lucía Mora"
        # The token request authenticated with a signed client secret.
        token_req = next(r for r in fake.requests if str(r.url) == providers.APPLE_TOKEN_URL)
        form = parse_qs(token_req.content.decode())
        assert jwt.get_unverified_header(form["client_secret"][0])["alg"] == "ES256"


# ── Linked providers in Mi cuenta ────────────────────────────────────────────

class TestIdentities:
    def _link_google(self, client, fake, email):
        _, p, b = _start(client, "google")
        fake.id_token = _google_token(p, email)
        code = _handoff_code(_callback(client, "google", p, b))
        r = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
        return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    def test_unlinking_the_only_way_in_is_refused(self, client, all_on, fake, cleanup_emails):
        email = f"only-{uuid4().hex[:8]}@gmail.com"
        cleanup_emails.append(email)
        headers = self._link_google(client, fake, email)
        r = client.delete("/api/v1/auth/identities/google", headers=headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "social_identity_last_method"
        uid = query_one("SELECT id FROM users WHERE email = %s", (email,))["id"]
        assert query_one("SELECT 1 FROM user_identities WHERE user_id = %s", (uid,)) is not None

    def test_with_a_password_it_unlinks_and_is_audited(
        self, client, all_on, fake, registered_user, auth_headers,
    ):
        self._link_google(client, fake, registered_user["email"])
        uid = registered_user["user"]["id"]
        r = client.delete("/api/v1/auth/identities/google", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert query_one("SELECT 1 FROM user_identities WHERE user_id = %s", (uid,)) is None
        assert query_one(
            "SELECT 1 FROM activity_logs WHERE user_id = %s AND action = %s",
            (uid, "account.provider_unlinked"),
        ) is not None

    def test_a_viewer_manages_their_own_and_cannot_touch_anyone_elses(
        self, client, all_on, fake, registered_user, viewer_headers,
    ):
        """SELF route: a viewer may unlink THEIR provider; scoped by token, so
        another user's identity is simply not found and stays put."""
        self._link_google(client, fake, registered_user["email"])
        r = client.delete("/api/v1/auth/identities/google", headers=viewer_headers)
        assert r.status_code == 404
        assert query_one(
            "SELECT 1 FROM user_identities WHERE user_id = %s",
            (registered_user["user"]["id"],),
        ) is not None

    def test_requires_authentication(self, client, social_off):
        assert client.get("/api/v1/auth/identities").status_code == 401
        assert client.delete("/api/v1/auth/identities/google").status_code == 401
