"""Enterprise SSO (OpenID Connect, one provider per tenant) - backend/auth/sso/.

The identity provider is faked with an `httpx.MockTransport`: discovery, the
token endpoint and the JWKS answer from this file and ID tokens are signed with
a key generated here. No test reaches a real provider and no DNS lookup leaves
the machine.

State is asserted in the database - users, identities, handoffs, activity rows -
not from a redirect alone: a sign-in that lands on the right page while writing
the wrong rows is the failure this file exists to catch.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.auth.social import flow, providers
from backend.auth.sso import oidc
from backend.db.connection import execute, query, query_one

FRONTEND = "http://localhost:5000"
ISSUER = "https://idp.example-sso.test"
AUTH_EP = f"{ISSUER}/authorize"
TOKEN_EP = f"{ISSUER}/token"
JWKS_URI = f"{ISSUER}/jwks"
CLIENT_ID = "stockai-client"
CLIENT_SECRET = "s3cret-value-xyz"

_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "sso-kid-1"
_JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_RSA.public_key())),
        "kid": _KID, "alg": "RS256", "use": "sig"}


class FakeIdp:
    """Discovery + token endpoint + JWKS of one pretend provider."""

    def __init__(self):
        self.id_token: str | None = None
        self.used_codes: set[str] = set()
        self.token_requests: list[httpx.Request] = []
        self.discovery_overrides: dict = {}
        self.token_status = 200

    def discovery(self) -> dict:
        doc = {
            "issuer": ISSUER, "authorization_endpoint": AUTH_EP,
            "token_endpoint": TOKEN_EP, "jwks_uri": JWKS_URI,
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        }
        doc.update(self.discovery_overrides)
        return {k: v for k, v in doc.items() if v is not None}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery())
        if url == JWKS_URI:
            return httpx.Response(200, json={"keys": [_JWK]})
        if url == TOKEN_EP:
            self.token_requests.append(request)
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            code = form.get("code", "")
            if code in self.used_codes:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.used_codes.add(code)
            return httpx.Response(200, json={"id_token": self.id_token, "access_token": "x"})
        return httpx.Response(404, json={"error": "unexpected " + url})


def _token(*, nonce: str, email: str | None, sub: str | None = None, aud=CLIENT_ID,
           iss=ISSUER, verified=True, exp_in=600, iat_offset=0, key=_RSA,
           alg="RS256", extra: dict | None = None) -> str:
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": sub or f"sub-{uuid4().hex}",
              "nonce": nonce, "iat": now + iat_offset, "exp": now + exp_in,
              **(extra or {})}
    if email is not None:
        claims["email"] = email
        claims["email_verified"] = verified
    if alg == "HS256":
        return jwt.encode(claims, "x" * 40, algorithm="HS256", headers={"kid": _KID})
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": _KID})


@pytest.fixture
def idp(monkeypatch):
    f = FakeIdp()
    monkeypatch.setattr(providers, "_transport", httpx.MockTransport(f))
    monkeypatch.setattr(oidc, "_resolve_host", lambda host: ["93.184.216.34"])
    providers.clear_jwks_cache()
    yield f
    providers.clear_jwks_cache()


@pytest.fixture
def sso_on(monkeypatch, client):
    execute("DELETE FROM service_config WHERE service = 'enterprise_sso'")
    from backend.service_config import store
    store.invalidate()
    monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", True)
    monkeypatch.setattr("backend.config.settings.frontend_url", FRONTEND)
    monkeypatch.setattr("backend.config.settings.integrations_secret_key",
                        Fernet.generate_key().decode())
    client.cookies.clear()
    yield
    client.cookies.clear()


def _make_tenant_with_admin(domain: str, local: str = "boss"):
    """A tenant, and a verified admin whose e-mail is on `domain`."""
    from backend.tenants.service import create_tenant
    from backend.users import service as user_svc

    tenant = create_tenant(f"pytest-sso-{uuid4().hex[:8]}")
    email = f"{local}@{domain}"
    password = "TestPass123!"
    admin = user_svc.create_user(tenant_id=tenant["id"], email=email, password=password,
                                 role="admin", full_name="Boss")
    user_svc.mark_verified(tenant["id"], admin["id"])
    return tenant, admin, email, password


class Company:
    def __init__(self, client, tenant, admin, email, password, domain):
        self.client, self.tenant, self.admin = client, tenant, admin
        self.email, self.password, self.domain = email, password, domain
        r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.headers = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    @property
    def tid(self):
        return self.tenant["id"]

    def config_body(self, **over):
        body = {"issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                "allowed_domains": [self.domain], "default_role": "viewer",
                "enforce_sso": False, "groups_claim": None, "group_roles": {},
                "enabled": True}
        body.update(over)
        return body

    def save(self, **over):
        return self.client.put("/api/v1/auth/sso/config", json=self.config_body(**over),
                               headers=self.headers)

    def person(self, local: str, role="analyst", verified=True):
        from backend.users import service as user_svc
        u = user_svc.create_user(tenant_id=self.tid, email=f"{local}@{self.domain}",
                                 password="TestPass123!", role=role)
        if verified:
            user_svc.mark_verified(self.tid, u["id"])
        return u


@pytest.fixture
def company(client, sso_on, idp):
    domain = f"c{uuid4().hex[:10]}.example.com"
    tenant, admin, email, password = _make_tenant_with_admin(domain)
    c = Company(client, tenant, admin, email, password, domain)
    yield c
    execute("DELETE FROM tenants WHERE id = %s", (tenant["id"],))


@pytest.fixture
def configured(company):
    r = company.save()
    assert r.status_code == 200, r.text
    return company


def _start(client, email: str):
    client.cookies.clear()
    r = client.get("/api/v1/auth/sso/start", params={"email": email}, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    params = {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()}
    binding = r.cookies.get(flow.BINDING_COOKIE)
    client.cookies.clear()
    return loc, params, binding


def _callback(client, params, binding, *, code="code-1", state=None, error=None):
    client.cookies.clear()
    if binding:
        client.cookies.set(flow.BINDING_COOKIE, binding)
    q = {"state": state if state is not None else params.get("state")}
    if code:
        q["code"] = code
    if error:
        q["error"] = error
    r = client.get("/api/v1/auth/sso/callback", params=q, follow_redirects=False)
    client.cookies.clear()
    assert r.status_code == 302, r.text
    return r.headers["location"]


def _error_of(location: str):
    return parse_qs(urlparse(location).query).get("oauth_error", [None])[0]


def _handoff(location: str) -> str:
    assert location.startswith(f"{FRONTEND}/auth/callback#code="), location
    return location.split("#code=", 1)[1]


def _sign_in(company, idp, email, *, code=None, omit_email=False, token_email=None,
             **token_kw):
    """Run start + callback with an ID token for `email` (or `token_email`, when
    the provider vouches for somebody other than the address typed); returns the
    location. Every call uses a fresh authorization code unless one is given,
    because a real provider never honours the same code twice."""
    code = code or f"code-{uuid4().hex}"
    _, p, binding = _start(company.client, email)
    kw = dict(nonce=p["nonce"], email=None if omit_email else (token_email or email))
    kw.update(token_kw)
    idp.id_token = _token(**kw)
    return _callback(company.client, p, binding, code=code)


def _user(email):
    return query_one("SELECT * FROM users WHERE email = %s", (email.lower(),))


def _count_users(tenant_id):
    return query_one("SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s", (tenant_id,))["n"]


def _handoffs():
    return query_one("SELECT COUNT(*) AS n FROM oauth_handoffs")["n"]


# ── Off by default ───────────────────────────────────────────────────────────

class TestOffByDefault:
    def test_a_default_installation_offers_nothing(self, client, monkeypatch):
        execute("DELETE FROM service_config WHERE service = 'enterprise_sso'")
        from backend.service_config import store
        store.invalidate()
        monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", False)
        assert client.get("/api/v1/auth/sso/availability").json()["data"]["enabled"] is False
        r = client.post("/api/v1/auth/sso/discover", json={"email": "a@whatever-co.com"})
        assert r.json()["data"] == {"available": False, "enforced": False}
        r = client.get("/api/v1/auth/sso/start", params={"email": "a@whatever-co.com"},
                       follow_redirects=False)
        assert r.status_code == 302
        assert _error_of(r.headers["location"]) == "sso_not_available"

    def test_the_setting_itself_defaults_to_off(self):
        from backend.config import Settings
        assert Settings.model_fields["enterprise_sso_enabled"].default is False

    def test_the_admin_is_told_plainly_when_the_installation_has_it_off(
        self, client, company, monkeypatch,
    ):
        monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", False)
        r = company.save()
        assert r.status_code == 409
        assert r.json()["error_code"] == "sso_instance_disabled"
        assert query_one("SELECT 1 AS x FROM sso_providers WHERE tenant_id = %s", (company.tid,)) is None
        got = client.get("/api/v1/auth/sso/config", headers=company.headers).json()["data"]
        assert got["instance_enabled"] is False and got["config"] is None


# ── Configuration ────────────────────────────────────────────────────────────

class TestConfiguration:
    def test_viewer_and_analyst_are_refused_and_nothing_is_written(self, client, company):
        from backend.users import service as user_svc
        for role in ("viewer", "analyst"):
            u = company.person(f"p-{role}", role=role)
            tok = client.post("/api/v1/auth/login", json={
                "email": u["email"], "password": "TestPass123!"}).json()["data"]["access_token"]
            h = {"Authorization": f"Bearer {tok}"}
            r = client.put("/api/v1/auth/sso/config", json=company.config_body(), headers=h)
            assert r.status_code == 403 and r.json()["error_code"] == "role_not_permitted"
            assert client.get("/api/v1/auth/sso/config", headers=h).status_code == 403
            assert client.delete("/api/v1/auth/sso/config", headers=h).status_code == 403
        assert query_one("SELECT 1 AS x FROM sso_providers WHERE tenant_id = %s", (company.tid,)) is None
        assert query_one("SELECT 1 AS x FROM sso_domains WHERE tenant_id = %s", (company.tid,)) is None

    def test_admin_saves_and_the_secret_is_stored_encrypted_and_never_returned(
        self, client, company,
    ):
        r = company.save(default_role="analyst")
        assert r.status_code == 200, r.text
        assert CLIENT_SECRET not in r.text
        row = query_one("SELECT * FROM sso_providers WHERE tenant_id = %s", (company.tid,))
        assert row["issuer"] == ISSUER and row["default_role"] == "analyst"
        assert row["authorization_endpoint"] == AUTH_EP and row["jwks_uri"] == JWKS_URI
        assert row["client_secret_enc"] != CLIENT_SECRET
        assert CLIENT_SECRET not in row["client_secret_enc"]
        from backend.service_config import crypto
        assert crypto.decrypt_value(row["client_secret_enc"]) == CLIENT_SECRET
        assert [d["domain"] for d in query(
            "SELECT domain FROM sso_domains WHERE tenant_id = %s", (company.tid,))] == [company.domain]
        got = client.get("/api/v1/auth/sso/config", headers=company.headers)
        assert CLIENT_SECRET not in got.text
        data = got.json()["data"]
        assert data["config"]["has_client_secret"] is True
        assert data["redirect_uri"] == f"{FRONTEND}/api/v1/auth/sso/callback"
        ev = query_one("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s",
                       (company.tid, "account.sso_config_changed"))
        assert ev and ev["context"]["reason"] == "changed_by_an_account_admin"
        assert CLIENT_SECRET not in json.dumps(ev["context"])

    def test_updating_without_a_secret_keeps_the_stored_one(self, configured):
        before = query_one("SELECT client_secret_enc FROM sso_providers WHERE tenant_id = %s",
                           (configured.tid,))["client_secret_enc"]
        r = configured.save(client_secret=None, default_role="analyst")
        assert r.status_code == 200
        after = query_one("SELECT * FROM sso_providers WHERE tenant_id = %s", (configured.tid,))
        assert after["client_secret_enc"] == before and after["default_role"] == "analyst"

    def test_a_first_save_without_a_secret_is_refused(self, company):
        r = company.save(client_secret=None)
        assert r.status_code == 422 and r.json()["error_code"] == "sso_config_incomplete"

    @pytest.mark.parametrize("domains,code", [
        (["gmail.com"], "sso_domain_not_allowed"),
        (["not a domain"], "sso_domain_invalid"),
        ([], "sso_domain_required"),
    ])
    def test_bad_domains_are_refused(self, company, domains, code):
        r = company.save(allowed_domains=domains + [company.domain] if domains else domains)
        assert r.status_code == 422 and r.json()["error_code"] == code
        assert query_one("SELECT 1 AS x FROM sso_providers WHERE tenant_id = %s", (company.tid,)) is None

    def test_the_admin_must_own_one_of_the_domains(self, company):
        r = company.save(allowed_domains=["somebody-elses-company.com"])
        assert r.status_code == 422 and r.json()["error_code"] == "sso_domain_not_owned"

    def test_a_domain_belongs_to_one_tenant(self, client, configured):
        # A second tenant whose admin's own domain is the same one.
        other, other_admin, email, pw = _make_tenant_with_admin(configured.domain, "other")
        try:
            c2 = Company(client, other, other_admin, email, pw, configured.domain)
            r = c2.save()
            assert r.status_code == 409 and r.json()["error_code"] == "sso_domain_taken"
            assert query_one("SELECT tenant_id FROM sso_domains WHERE domain = %s",
                             (configured.domain,))["tenant_id"] == configured.tid
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    @pytest.mark.parametrize("role", ["admin", "owner"])
    def test_a_group_can_never_map_to_admin(self, company, role):
        r = company.save(groups_claim="groups", group_roles={"everyone": role})
        assert r.status_code == 422 and r.json()["error_code"] == "sso_group_role_invalid"

    def test_default_role_cannot_be_admin(self, company):
        r = company.save(default_role="admin")
        assert r.status_code == 422 and r.json()["error_code"] == "sso_default_role_invalid"

    @pytest.mark.parametrize("issuer", [
        "http://idp.example-sso.test", "https://localhost", "https://127.0.0.1",
        "https://10.0.0.5", "https://169.254.169.254", "https://user:pw@idp.example-sso.test",
        "https://[::1]",
    ])
    def test_the_server_never_fetches_a_non_public_or_non_https_issuer(
        self, company, idp, issuer,
    ):
        r = company.save(issuer=issuer)
        assert r.status_code == 422 and r.json()["error_code"] == "sso_url_not_allowed"
        assert idp.token_requests == []

    def test_a_hostname_resolving_to_a_private_address_is_refused(
        self, company, monkeypatch,
    ):
        monkeypatch.setattr(oidc, "_resolve_host", lambda host: ["10.1.2.3"])
        r = company.save()
        assert r.status_code == 422 and r.json()["error_code"] == "sso_url_not_allowed"

    def test_discovery_whose_issuer_differs_is_refused(self, company, idp):
        idp.discovery_overrides = {"issuer": "https://evil.example-sso.test"}
        r = company.save()
        assert r.status_code == 422 and r.json()["error_code"] == "sso_discovery_failed"

    def test_a_token_endpoint_on_a_private_address_in_discovery_is_refused(self, company, idp):
        idp.discovery_overrides = {"token_endpoint": "https://192.168.1.10/token"}
        r = company.save()
        assert r.status_code == 422 and r.json()["error_code"] == "sso_url_not_allowed"

    def test_a_provider_without_pkce_s256_is_refused(self, company, idp):
        idp.discovery_overrides = {"code_challenge_methods_supported": ["plain"]}
        assert company.save().json()["error_code"] == "sso_discovery_failed"

    def test_delete_removes_config_domains_and_identities(self, client, configured, idp):
        _handoff(_sign_in(configured, idp, configured.email))
        assert query_one("SELECT 1 AS x FROM user_identities WHERE provider = %s",
                         (f"sso:{configured.tid}",))
        r = client.delete("/api/v1/auth/sso/config", headers=configured.headers)
        assert r.status_code == 200
        for table in ("sso_providers", "sso_domains"):
            assert query_one(f"SELECT 1 AS x FROM {table} WHERE tenant_id = %s", (configured.tid,)) is None
        assert query_one("SELECT 1 AS x FROM user_identities WHERE provider = %s",
                         (f"sso:{configured.tid}",)) is None
        assert query_one("SELECT 1 AS x FROM activity_logs WHERE tenant_id = %s AND action = %s",
                         (configured.tid, "account.sso_config_removed"))
        assert client.delete("/api/v1/auth/sso/config", headers=configured.headers).status_code == 404

    def test_changing_the_issuer_drops_identities_and_needs_a_fresh_proof_to_enforce(
        self, client, configured, idp, monkeypatch,
    ):
        _handoff(_sign_in(configured, idp, configured.email))
        assert configured.save(enforce_sso=True).status_code == 200
        # Same provider re-saved keeps its identities...
        assert configured.save(enforce_sso=True).status_code == 200
        # ...a different client id is a different provider relationship.
        r = configured.save(client_id="another-client", enforce_sso=True)
        assert r.status_code == 409
        assert r.json()["error_code"] == "sso_enforce_needs_admin_sign_in"
        r = configured.save(client_id="another-client", enforce_sso=False)
        assert r.status_code == 200
        assert query_one("SELECT 1 AS x FROM user_identities WHERE provider = %s",
                         (f"sso:{configured.tid}",)) is None


# ── Discovery of the work e-mail ─────────────────────────────────────────────

class TestEmailRouting:
    def test_discover_routes_by_domain(self, client, configured):
        r = client.post("/api/v1/auth/sso/discover", json={"email": f"x@{configured.domain}"})
        assert r.json()["data"] == {"available": True, "enforced": False}
        r = client.post("/api/v1/auth/sso/discover", json={"email": "x@elsewhere-inc.com"})
        assert r.json()["data"]["available"] is False

    def test_a_disabled_provider_is_not_offered(self, client, configured):
        assert configured.save(enabled=False).status_code == 200
        r = client.post("/api/v1/auth/sso/discover", json={"email": f"x@{configured.domain}"})
        assert r.json()["data"]["available"] is False
        r = client.get("/api/v1/auth/sso/start", params={"email": f"x@{configured.domain}"},
                       follow_redirects=False)
        assert _error_of(r.headers["location"]) == "sso_not_available"


# ── The sign-in ──────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_authorization_request_carries_pkce_state_and_nonce(self, client, configured, idp):
        loc, p, binding = _start(client, f"ana@{configured.domain}")
        assert loc.startswith(AUTH_EP + "?")
        assert p["client_id"] == CLIENT_ID and p["response_type"] == "code"
        assert p["redirect_uri"] == f"{FRONTEND}/api/v1/auth/sso/callback"
        assert p["code_challenge_method"] == "S256"
        assert "openid" in p["scope"].split() and p["login_hint"] == f"ana@{configured.domain}"
        assert len(p["state"]) >= 32 and len(p["nonce"]) >= 32 and binding
        # Only hashes are stored, scoped to this tenant's provider.
        row = query_one("SELECT * FROM oauth_flows WHERE state_hash = %s",
                        (hashlib.sha256(p["state"].encode()).hexdigest(),))
        assert row["provider"] == f"sso:{configured.tid}"
        assert row["nonce"] == p["nonce"]
        assert p["state"] not in json.dumps(dict(row), default=str)

    def test_first_sign_in_creates_the_user_inside_the_tenant_and_issues_our_tokens(
        self, client, configured, idp,
    ):
        email = f"newhire@{configured.domain}"
        loc, p, binding = _start(client, email)
        idp.id_token = _token(nonce=p["nonce"], email=email, extra={"name": "New Hire"})
        code = _handoff(_callback(client, p, binding))

        u = _user(email)
        assert u["tenant_id"] == configured.tid
        assert u["role"] == "viewer"                      # the configured default
        assert u["email_verified"] is True and u["has_password"] is False
        assert u["status"] == "active" and u["full_name"] == "New Hire"
        ident = query_one("SELECT * FROM user_identities WHERE user_id = %s", (u["id"],))
        assert ident["provider"] == f"sso:{configured.tid}" and ident["tenant_id"] == configured.tid

        # The token request used PKCE: the verifier hashes to the challenge sent.
        form = {k: v[0] for k, v in parse_qs(idp.token_requests[-1].content.decode()).items()}
        digest = hashlib.sha256(form["code_verifier"].encode()).digest()
        assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == p["code_challenge"]
        assert form["redirect_uri"] == p["redirect_uri"] and form["grant_type"] == "authorization_code"
        # client_secret_basic: the secret is in the header, never in the body.
        auth = idp.token_requests[-1].headers["authorization"]
        assert base64.b64decode(auth.split()[1]).decode() == f"{CLIENT_ID}:{CLIENT_SECRET}"
        assert CLIENT_SECRET not in idp.token_requests[-1].content.decode()

        r = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["user"]["tenant_id"] == configured.tid and data["user"]["role"] == "viewer"
        assert data["is_new_account"] is True and data["provider"] == "sso"
        claims = jwt.decode(data["access_token"], options={"verify_signature": False})
        assert claims["tenant_id"] == configured.tid and claims["sub"] == u["id"]
        assert query_one("SELECT 1 AS x FROM refresh_tokens WHERE user_id = %s", (u["id"],))
        me = client.get("/api/v1/users/me",
                        headers={"Authorization": f"Bearer {data['access_token']}"})
        assert me.status_code == 200 and me.json()["data"]["email"] == email
        # Password sign-in stays closed for an SSO-only account.
        assert client.post("/api/v1/auth/login", json={
            "email": email, "password": "anything-at-all-1A!"}).status_code == 401

        actions = {r["action"] for r in query(
            "SELECT action FROM activity_logs WHERE tenant_id = %s AND user_id = %s",
            (configured.tid, u["id"]))}
        assert {"account.sso_user_created", "account.sso_sign_in"} <= actions

    def test_the_second_sign_in_reuses_the_user(self, client, configured, idp):
        email = f"again@{configured.domain}"
        sub = f"sub-{uuid4().hex}"
        _handoff(_sign_in(configured, idp, email, sub=sub))
        n = _count_users(configured.tid)
        code = _handoff(_sign_in(configured, idp, email, sub=sub, code="code-2"))
        assert _count_users(configured.tid) == n
        r = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
        assert r.json()["data"]["is_new_account"] is False

    def test_an_existing_user_of_the_tenant_is_linked_and_keeps_their_role(
        self, client, configured, idp,
    ):
        u = configured.person("existing", role="analyst", verified=True)
        _handoff(_sign_in(configured, idp, u["email"]))
        row = _user(u["email"])
        assert row["id"] == u["id"] and row["role"] == "analyst"
        assert row["has_password"] is True                   # verified: password kept
        assert query_one("SELECT 1 AS x FROM user_identities WHERE user_id = %s", (u["id"],))

    def test_linking_an_unverified_account_drops_its_password(self, client, configured, idp):
        squat = configured.person("squat", role="analyst", verified=False)
        _handoff(_sign_in(configured, idp, squat["email"]))
        row = _user(squat["email"])
        assert row["email_verified"] is True and row["has_password"] is False
        assert client.post("/api/v1/auth/login", json={
            "email": squat["email"], "password": "TestPass123!"}).status_code == 401

    def test_an_admin_signing_in_stays_admin(self, client, configured, idp):
        _handoff(_sign_in(configured, idp, configured.email))
        assert _user(configured.email)["role"] == "admin"

    def test_clock_skew_inside_the_leeway_is_tolerated(self, client, configured, idp):
        email = f"skew@{configured.domain}"
        _handoff(_sign_in(configured, idp, email, exp_in=-30, iat_offset=30))
        assert _user(email) is not None


# ── Refusals ─────────────────────────────────────────────────────────────────

class TestRefusals:
    def _refused(self, configured, idp, email, expected, *, users_before=None, **token_kw):
        n_users = _count_users(configured.tid)
        n_handoffs = _handoffs()
        loc = _sign_in(configured, idp, email, **token_kw)
        assert _error_of(loc) == expected, loc
        assert _count_users(configured.tid) == n_users, "a refused sign-in created a user"
        assert _handoffs() == n_handoffs, "a refused sign-in issued a handoff"
        assert query_one("SELECT 1 AS x FROM user_identities WHERE provider = %s",
                         (f"sso:{configured.tid}",)) is None

    def test_wrong_nonce(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid",
                      nonce="not-the-nonce-we-sent")

    def test_wrong_audience(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid",
                      aud="some-other-client")

    def test_wrong_issuer(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid",
                      iss="https://evil.example-sso.test")

    def test_signature_from_another_key(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid",
                      key=_OTHER_RSA)

    def test_symmetric_algorithm_is_refused(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid",
                      alg="HS256")

    def test_expired_token(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid",
                      exp_in=-3600)

    def test_token_issued_in_the_far_future(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid",
                      iat_offset=3600, exp_in=7200)

    def test_multiple_audiences_need_our_azp(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid",
                      aud=[CLIENT_ID, "other"], extra={"azp": "other"})

    def test_unverified_email(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_email_unverified",
                      verified=False)

    def test_missing_email_verified_claim_counts_as_unverified(self, configured, idp):
        email = f"a@{configured.domain}"
        _, p, binding = _start(configured.client, email)
        now = int(time.time())
        idp.id_token = jwt.encode(
            {"iss": ISSUER, "aud": CLIENT_ID, "sub": "s1", "nonce": p["nonce"],
             "iat": now, "exp": now + 600, "email": email},
            _RSA, algorithm="RS256", headers={"kid": _KID})
        assert _error_of(_callback(configured.client, p, binding)) == "sso_email_unverified"
        assert _user(email) is None

    def test_no_email_at_all(self, configured, idp):
        self._refused(configured, idp, f"a@{configured.domain}", "sso_email_missing",
                      omit_email=True)

    def test_domain_not_allowed_is_refused_and_the_tenant_is_told(self, configured, idp):
        _, p, binding = _start(configured.client, f"a@{configured.domain}")
        outsider = f"intruder@not-{configured.domain}"
        idp.id_token = _token(nonce=p["nonce"], email=outsider)
        assert _error_of(_callback(configured.client, p, binding)) == "sso_domain_not_allowed"
        assert _user(outsider) is None
        ev = query_one("SELECT context, status FROM activity_logs WHERE tenant_id = %s AND action = %s",
                       (configured.tid, "account.sso_sign_in_refused"))
        assert ev and ev["context"]["reason_params"] == {"code": "sso_domain_not_allowed"}

    def test_the_provider_refusing_the_code(self, configured, idp):
        idp.token_status = 401
        self._refused(configured, idp, f"a@{configured.domain}", "sso_token_invalid")

    def test_the_person_cancelling_at_the_provider(self, client, configured, idp):
        _, p, binding = _start(client, f"a@{configured.domain}")
        assert _error_of(_callback(client, p, binding, code=None, error="access_denied")) == "sso_cancelled"
        # ...and the state is spent either way.
        assert query_one("SELECT 1 AS x FROM oauth_flows WHERE state_hash = %s",
                         (hashlib.sha256(p["state"].encode()).hexdigest(),)) is None

    def test_unknown_state(self, client, configured, idp):
        _, p, binding = _start(client, f"a@{configured.domain}")
        loc = _callback(client, p, binding, state="made-up-state-value")
        assert _error_of(loc) == "oauth_state_invalid"

    def test_missing_or_wrong_browser_binding(self, client, configured, idp):
        email = f"a@{configured.domain}"
        for binding in (None, "somebody-elses-cookie"):
            _, p, good = _start(client, email)
            idp.id_token = _token(nonce=p["nonce"], email=email)
            assert _error_of(_callback(client, p, binding)) == "oauth_state_invalid"
            # The refused attempt burned the state: the right browser cannot reuse it.
            assert _error_of(_callback(client, p, good)) == "oauth_state_invalid"
        assert _user(email) is None

    def test_a_state_from_another_tenants_flow_cannot_sign_into_this_one(
        self, client, configured, idp,
    ):
        # A state that is not an SSO state at all (a social login's).
        started = flow.start_flow("google", intent="login", terms_accepted=False)
        client.cookies.set(flow.BINDING_COOKIE, started.binding)
        r = client.get("/api/v1/auth/sso/callback",
                       params={"state": started.state, "code": "x"}, follow_redirects=False)
        client.cookies.clear()
        assert _error_of(r.headers["location"]) == "oauth_state_invalid"

    def test_replaying_the_callback_signs_in_once(self, client, configured, idp):
        email = f"replay@{configured.domain}"
        _, p, binding = _start(client, email)
        idp.id_token = _token(nonce=p["nonce"], email=email)
        first = _callback(client, p, binding)
        _handoff(first)
        n_handoffs = _handoffs()
        second = _callback(client, p, binding)
        assert _error_of(second) == "oauth_state_invalid"
        assert _handoffs() == n_handoffs

    def test_a_replayed_authorization_code_is_refused_by_the_provider_and_by_us(
        self, client, configured, idp,
    ):
        email = f"code@{configured.domain}"
        _handoff(_sign_in(configured, idp, email, code="same-code"))
        n = _count_users(configured.tid)
        # A fresh flow (fresh state) presenting a code the provider has seen.
        loc = _sign_in(configured, idp, f"code2@{configured.domain}", code="same-code")
        assert _error_of(loc) == "sso_token_invalid"
        assert _count_users(configured.tid) == n

    def test_a_handoff_code_is_single_use(self, client, configured, idp):
        code = _handoff(_sign_in(configured, idp, f"once@{configured.domain}"))
        assert client.post("/api/v1/auth/oauth/exchange", json={"code": code}).status_code == 200
        assert client.post("/api/v1/auth/oauth/exchange", json={"code": code}).status_code == 400

    def test_a_suspended_user_is_refused_like_password_login(self, configured, idp):
        u = configured.person("susp", role="analyst")
        execute("UPDATE users SET status = 'suspended' WHERE id = %s", (u["id"],))
        loc = _sign_in(configured, idp, u["email"])
        assert _error_of(loc) == "account_not_active"

    def test_the_switch_turned_off_mid_flow_stops_the_callback(
        self, client, configured, idp, monkeypatch,
    ):
        email = f"a@{configured.domain}"
        _, p, binding = _start(client, email)
        idp.id_token = _token(nonce=p["nonce"], email=email)
        monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", False)
        assert _error_of(_callback(client, p, binding)) == "sso_not_available"
        assert _user(email) is None

    def test_an_unreadable_stored_secret_says_so_instead_of_a_500(
        self, client, configured, idp, monkeypatch,
    ):
        email = f"a@{configured.domain}"
        _, p, binding = _start(client, email)
        monkeypatch.setattr("backend.config.settings.integrations_secret_key",
                            Fernet.generate_key().decode())
        assert _error_of(_callback(client, p, binding)) == "sso_secret_unreadable"


# ── Tenant isolation ─────────────────────────────────────────────────────────

class TestTenantIsolation:
    def test_an_email_that_belongs_to_another_tenant_is_never_touched_or_reused(
        self, client, configured, idp, test_tenant,
    ):
        from backend.users import service as user_svc
        email = f"contractor@{configured.domain}"
        theirs = user_svc.create_user(tenant_id=test_tenant["id"], email=email,
                                      password="TestPass123!", role="admin")
        user_svc.mark_verified(test_tenant["id"], theirs["id"])
        before = dict(_user(email))
        n = _count_users(configured.tid)
        loc = _sign_in(configured, idp, email)
        assert _error_of(loc) == "sso_account_conflict"
        assert _count_users(configured.tid) == n
        after = _user(email)
        assert after["tenant_id"] == test_tenant["id"] and after["role"] == before["role"]
        assert query_one("SELECT 1 AS x FROM user_identities WHERE user_id = %s", (theirs["id"],)) is None

    def test_the_same_subject_at_two_tenants_providers_is_two_identities(
        self, client, configured, idp,
    ):
        other_domain = f"d{uuid4().hex[:10]}.example.com"
        tenant2, admin2, email2, pw2 = _make_tenant_with_admin(other_domain)
        try:
            c2 = Company(client, tenant2, admin2, email2, pw2, other_domain)
            assert c2.save().status_code == 200
            shared_sub = f"sub-{uuid4().hex}"
            a = f"alice@{configured.domain}"
            b = f"bob@{other_domain}"
            _handoff(_sign_in(configured, idp, a, sub=shared_sub))
            _handoff(_sign_in(c2, idp, b, sub=shared_sub))
            ua, ub = _user(a), _user(b)
            assert ua["tenant_id"] == configured.tid and ub["tenant_id"] == tenant2["id"]
            assert ua["id"] != ub["id"]
            # And a returning sign-in lands on the right one, not the first match.
            _handoff(_sign_in(c2, idp, b, sub=shared_sub, code="code-9"))
            assert _count_users(configured.tid) == 2          # admin + alice
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (tenant2["id"],))

    def test_a_token_for_another_tenants_domain_is_refused(self, client, configured, idp):
        other_domain = f"e{uuid4().hex[:10]}.example.com"
        tenant2, admin2, email2, pw2 = _make_tenant_with_admin(other_domain)
        try:
            Company(client, tenant2, admin2, email2, pw2, other_domain).save()
            # A flow started for tenant 1 whose IdP vouches for tenant 2's domain.
            loc = _sign_in(configured, idp, f"carol@{configured.domain}",
                           token_email=f"carol@{other_domain}")
            assert _error_of(loc) == "sso_domain_not_allowed"
            assert _user(f"carol@{other_domain}") is None
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (tenant2["id"],))


# ── Seat ceiling ─────────────────────────────────────────────────────────────

class TestSeatCeiling:
    def test_a_full_tenant_refuses_the_new_person_with_a_clear_code(
        self, client, configured, idp, monkeypatch,
    ):
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        execute("UPDATE tenants SET tier = 'free' WHERE id = %s", (configured.tid,))
        configured.person("second")                         # admin + 1 = the free ceiling of 2
        assert _count_users(configured.tid) == 2
        email = f"third@{configured.domain}"
        loc = _sign_in(configured, idp, email)
        assert _error_of(loc) == "sso_seat_limit_reached"
        assert _user(email) is None and _count_users(configured.tid) == 2
        # An EXISTING person still gets in: the ceiling is for new seats.
        existing = f"second@{configured.domain}"
        assert _handoff(_sign_in(configured, idp, existing, code="code-7"))

    def test_a_paid_tenant_has_no_such_ceiling(self, client, configured, idp, monkeypatch):
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        execute("UPDATE tenants SET tier = 'paid' WHERE id = %s", (configured.tid,))
        configured.person("second")
        assert _handoff(_sign_in(configured, idp, f"third@{configured.domain}"))


# ── Enforcement ──────────────────────────────────────────────────────────────

class TestEnforcement:
    def test_cannot_enforce_before_an_admin_has_signed_in_through_the_provider(
        self, configured,
    ):
        r = configured.save(enforce_sso=True)
        assert r.status_code == 409
        assert r.json()["error_code"] == "sso_enforce_needs_admin_sign_in"
        assert query_one("SELECT enforce_sso FROM sso_providers WHERE tenant_id = %s",
                         (configured.tid,))["enforce_sso"] is False

    def test_enforced_domain_users_cannot_use_a_password_even_the_right_one(
        self, client, configured, idp,
    ):
        person = configured.person("worker", role="analyst")
        _handoff(_sign_in(configured, idp, configured.email))      # the admin proves it works
        assert configured.save(enforce_sso=True).status_code == 200
        before = query_one("SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = %s",
                           (person["id"],))["n"]
        r = client.post("/api/v1/auth/login", json={"email": person["email"],
                                                    "password": "TestPass123!"})
        assert r.status_code == 403 and r.json()["error_code"] == "sso_required"
        assert "access_token" not in r.text
        assert query_one("SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = %s",
                         (person["id"],))["n"] == before
        # The admin is not exempt either: the proof of working config is the way back in.
        r = client.post("/api/v1/auth/login", json={"email": configured.email,
                                                    "password": configured.password})
        assert r.status_code == 403 and r.json()["error_code"] == "sso_required"
        # ...and SSO itself still works for them.
        assert _handoff(_sign_in(configured, idp, person["email"], code="code-3"))
        d = client.post("/api/v1/auth/sso/discover", json={"email": person["email"]})
        assert d.json()["data"]["enforced"] is True

    def test_other_domains_of_the_same_tenant_and_other_tenants_keep_their_password(
        self, client, configured, idp, test_tenant,
    ):
        from backend.users import service as user_svc
        _handoff(_sign_in(configured, idp, configured.email))
        assert configured.save(enforce_sso=True).status_code == 200
        # Same tenant, a contractor on a domain SSO does not cover.
        outside = user_svc.create_user(tenant_id=configured.tid,
                                       email=f"freelancer@outside-{configured.domain}",
                                       password="TestPass123!", role="analyst")
        user_svc.mark_verified(configured.tid, outside["id"])
        assert client.post("/api/v1/auth/login", json={
            "email": outside["email"], "password": "TestPass123!"}).status_code == 200
        # Another tenant, even with a look-alike address, is unaffected.
        mine = user_svc.create_user(tenant_id=test_tenant["id"],
                                    email=f"someone@x{configured.domain}",
                                    password="TestPass123!", role="analyst")
        user_svc.mark_verified(test_tenant["id"], mine["id"])
        assert client.post("/api/v1/auth/login", json={
            "email": mine["email"], "password": "TestPass123!"}).status_code == 200

    def test_turning_the_instance_switch_off_suspends_enforcement_so_nobody_is_locked_out(
        self, client, configured, idp, monkeypatch,
    ):
        _handoff(_sign_in(configured, idp, configured.email))
        assert configured.save(enforce_sso=True).status_code == 200
        monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", False)
        assert client.post("/api/v1/auth/login", json={
            "email": configured.email, "password": configured.password}).status_code == 200

    def test_disabling_the_provider_lifts_enforcement(self, client, configured, idp):
        _handoff(_sign_in(configured, idp, configured.email))
        assert configured.save(enforce_sso=True).status_code == 200
        assert configured.save(enforce_sso=False, enabled=False).status_code == 200
        assert client.post("/api/v1/auth/login", json={
            "email": configured.email, "password": configured.password}).status_code == 200

    def test_enforcement_needs_an_enabled_provider(self, client, configured, idp):
        _handoff(_sign_in(configured, idp, configured.email))
        r = configured.save(enforce_sso=True, enabled=False)
        assert r.status_code == 422 and r.json()["error_code"] == "sso_enforce_needs_enabled"

    def test_unenforced_password_login_is_unchanged(self, client, configured):
        assert client.post("/api/v1/auth/login", json={
            "email": configured.email, "password": configured.password}).status_code == 200


# ── Group -> role mapping ────────────────────────────────────────────────────

class TestGroupRoles:
    RULES = {"planners": "analyst", "staff": "viewer"}

    def _cfg(self, c, **over):
        r = c.save(groups_claim="groups", group_roles=self.RULES, default_role="viewer", **over)
        assert r.status_code == 200, r.text

    def test_default_is_no_mapping(self, configured, idp):
        email = f"g@{configured.domain}"
        _handoff(_sign_in(configured, idp, email, extra={"groups": ["planners"]}))
        assert _user(email)["role"] == "viewer"           # claim present, nothing configured

    def test_a_new_person_gets_the_mapped_role(self, configured, idp):
        self._cfg(configured)
        email = f"planner@{configured.domain}"
        _handoff(_sign_in(configured, idp, email, extra={"groups": ["staff", "planners"]}))
        assert _user(email)["role"] == "analyst"          # the highest mapped role

    def test_no_matching_group_falls_to_the_default_role(self, configured, idp):
        self._cfg(configured)
        email = f"nobody@{configured.domain}"
        _handoff(_sign_in(configured, idp, email, extra={"groups": ["unrelated"]}))
        assert _user(email)["role"] == "viewer"
        email2 = f"noclaim@{configured.domain}"
        _handoff(_sign_in(configured, idp, email2, code="code-5"))
        assert _user(email2)["role"] == "viewer"

    def test_a_string_claim_works(self, configured, idp):
        self._cfg(configured)
        email = f"str@{configured.domain}"
        _handoff(_sign_in(configured, idp, email, extra={"groups": "planners"}))
        assert _user(email)["role"] == "analyst"

    def test_a_custom_claim_name(self, configured, idp):
        configured.save(groups_claim="https://acme.example/roles", group_roles=self.RULES)
        email = f"url@{configured.domain}"
        _handoff(_sign_in(configured, idp, email,
                          extra={"https://acme.example/roles": ["planners"]}))
        assert _user(email)["role"] == "analyst"

    def test_losing_the_group_demotes_and_is_audited(self, client, configured, idp):
        self._cfg(configured)
        email = f"mover@{configured.domain}"
        sub = f"sub-{uuid4().hex}"
        _handoff(_sign_in(configured, idp, email, sub=sub, extra={"groups": ["planners"]}))
        assert _user(email)["role"] == "analyst"
        code = _handoff(_sign_in(configured, idp, email, sub=sub, code="code-6",
                                 extra={"groups": ["staff"]}))
        u = _user(email)
        assert u["role"] == "viewer"
        ev = query_one(
            "SELECT context FROM activity_logs WHERE user_id = %s AND action = %s",
            (u["id"], "account.sso_role_mapped"))
        assert ev["context"]["previous_role"] == "analyst"
        assert ev["context"]["reason"] == "mapped_from_identity_provider_groups"
        # The session minted right after carries the NEW role.
        data = client.post("/api/v1/auth/oauth/exchange", json={"code": code}).json()["data"]
        assert data["user"]["role"] == "viewer"

    def test_an_admin_is_never_changed_by_a_group(self, configured, idp):
        self._cfg(configured)
        _handoff(_sign_in(configured, idp, configured.email, extra={"groups": ["staff"]}))
        assert _user(configured.email)["role"] == "admin"

    def test_a_group_named_admins_does_not_make_anyone_admin(self, configured, idp):
        self._cfg(configured)
        email = f"sneaky@{configured.domain}"
        _handoff(_sign_in(configured, idp, email, extra={"groups": ["admins", "admin"]}))
        assert _user(email)["role"] == "viewer"


# ── Registry ─────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_the_instance_switch_is_declared_and_reported(self):
        from backend.service_config import registry
        svc = registry.BY_KEY["enterprise_sso"]
        assert svc.switch == "enterprise_sso_enabled"
        assert registry.all_fields()["enterprise_sso_enabled"].default == "false"
