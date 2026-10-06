"""SAML 2.0 sign-in end to end: start, the identity provider's POST, the tenant.

The configuration routes are Rust and cannot be called from this suite, so the
`saml_providers` and `sso_domains` rows are written here with SQL in exactly the
shape `backend-rs/src/routes/saml.rs` writes them (the contract test walks the
two halves together against real servers). Everything this file asserts is the
Python half: the AuthnRequest, the ACS, the tenant rules, enforcement on the
password path, and that nothing is created or logged in unless every check held.

State is asserted in the database - users, identities, flows, handoffs,
activity rows - not from a redirect alone.
"""

from __future__ import annotations

import base64
import json
import zlib
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from xml.dom import minidom

import pytest
from cryptography.fernet import Fernet

from backend.auth.saml import service as saml_service
from backend.auth.social import flow
from backend.db.connection import execute, query, query_one
from backend.tests import saml_fixtures as fx
from backend.tests.saml_fixtures import b64

FRONTEND = "http://localhost:5000"
ACS = f"{FRONTEND}/api/v1/auth/saml/acs"


@pytest.fixture
def sso_on(monkeypatch, client):
    execute("DELETE FROM service_config WHERE service = 'enterprise_sso'")
    from backend.service_config import store
    store.invalidate()
    monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", True)
    monkeypatch.setattr("backend.config.settings.frontend_url", FRONTEND)
    monkeypatch.setattr("backend.config.settings.integrations_secret_key", Fernet.generate_key().decode())
    client.cookies.clear()
    yield
    client.cookies.clear()


@pytest.fixture(scope="module")
def idp():
    return fx.FakeIdp()


def _make_tenant(domain: str, local: str = "boss", role: str = "admin"):
    from backend.tenants.service import create_tenant
    from backend.users import service as user_svc

    tenant = create_tenant(f"pytest-saml-{uuid4().hex[:8]}")
    user = user_svc.create_user(tenant_id=tenant["id"], email=f"{local}@{domain}",
                                password="TestPass123!", role=role, full_name="Boss")
    user_svc.mark_verified(tenant["id"], user["id"])
    return tenant, user


class Company:
    def __init__(self, idp, domain=None):
        self.idp = idp
        self.domain = domain or f"c{uuid4().hex[:10]}.example.com"
        self.tenant, self.admin = _make_tenant(self.domain)
        self.tid = self.tenant["id"]

    @property
    def sp(self) -> str:
        return saml_service.sp_entity_id(self.tid)

    def configure(self, **over):
        """What the Rust PUT writes: the provider row and the domain rows."""
        cfg = dict(idp_entity_id=self.idp.entity_id, sso_url=self.idp.sso_url,
                   certs=[self.idp.cert_b64], domains=[self.domain], default_role="viewer",
                   enforce_sso=False, email_attribute=None, groups_attribute=None,
                   group_roles={}, enabled=True)
        cfg.update(over)
        execute("DELETE FROM saml_providers WHERE tenant_id = %s", (self.tid,))
        execute(
            """INSERT INTO saml_providers
                   (tenant_id, idp_entity_id, sso_url, idp_certificates, allowed_domains,
                    default_role, enforce_sso, email_attribute, groups_attribute, group_roles, enabled)
               VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s)""",
            (self.tid, cfg["idp_entity_id"], cfg["sso_url"], json.dumps(cfg["certs"]),
             json.dumps(cfg["domains"]), cfg["default_role"], cfg["enforce_sso"],
             cfg["email_attribute"], cfg["groups_attribute"], json.dumps(cfg["group_roles"]),
             cfg["enabled"]),
        )
        execute("DELETE FROM sso_domains WHERE tenant_id = %s", (self.tid,))
        for d in cfg["domains"]:
            execute("INSERT INTO sso_domains (domain, tenant_id) VALUES (%s, %s)", (d, self.tid))
        return self

    def person(self, local: str, role="analyst", verified=True):
        from backend.users import service as user_svc
        u = user_svc.create_user(tenant_id=self.tid, email=f"{local}@{self.domain}",
                                 password="TestPass123!", role=role)
        if verified:
            user_svc.mark_verified(self.tid, u["id"])
        return u


@pytest.fixture
def company(client, sso_on, idp):
    c = Company(idp)
    yield c
    execute("DELETE FROM tenants WHERE id = %s", (c.tid,))


@pytest.fixture
def configured(company):
    return company.configure()


class Started:
    def __init__(self, location, binding):
        self.location, self.binding = location, binding
        q = {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}
        self.relay_state = q["RelayState"]
        raw = zlib.decompress(base64.b64decode(q["SAMLRequest"]), -15).decode()
        self.authn_xml = raw
        self.doc = minidom.parseString(raw).documentElement
        self.request_id = self.doc.getAttribute("ID")


def _start(client, email) -> Started:
    client.cookies.clear()
    r = client.get("/api/v1/auth/saml/start", params={"email": email}, follow_redirects=False)
    assert r.status_code == 302, r.text
    binding = r.cookies.get(flow.BINDING_COOKIE)
    client.cookies.clear()
    return Started(r.headers["location"], binding)


def _acs(client, relay_state, saml_xml, binding, *, raw_response=None):
    client.cookies.clear()
    if binding:
        client.cookies.set(flow.BINDING_COOKIE, binding)
    data = {"RelayState": relay_state} if relay_state is not None else {}
    data["SAMLResponse"] = raw_response if raw_response is not None else b64(saml_xml)
    r = client.post("/api/v1/auth/saml/acs", data=data, follow_redirects=False)
    client.cookies.clear()
    assert r.status_code == 303, r.text
    return r.headers["location"]


def _error_of(location: str):
    return parse_qs(urlparse(location).query).get("oauth_error", [None])[0]


def _handoff(location: str) -> str:
    assert location.startswith(f"{FRONTEND}/auth/callback#code="), location
    return location.split("#code=", 1)[1]


def _sign_in(company, email, *, local_email=None, sign="assertion", assertion_kw=None,
             response_kw=None, sig_kw=None, idp=None, started=None, binding="use-started"):
    """start + the IdP's POST for `email`. Returns the ACS location."""
    st = started or _start(company_client(), email)
    idp = idp or company.idp
    xml = idp.signed_response(
        request_id=st.request_id, sp_entity=company.sp, acs=ACS,
        email=local_email or email, sign=sign, assertion_kw=assertion_kw,
        response_kw=response_kw, sig_kw=sig_kw)
    return _acs(company_client(), st.relay_state, xml,
                st.binding if binding == "use-started" else binding)


_CLIENT = {}


def company_client():
    return _CLIENT["c"]


@pytest.fixture(autouse=True)
def _remember_client(client):
    _CLIENT["c"] = client


def _user(email):
    return query_one("SELECT * FROM users WHERE email = %s", (email.lower(),))


def _count_users(tid):
    return query_one("SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s", (tid,))["n"]


def _handoffs():
    return query_one("SELECT COUNT(*) AS n FROM oauth_handoffs")["n"]


def _events(tid, action):
    return query("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = %s", (tid, action))


# ── Off by default ───────────────────────────────────────────────────────────

class TestOffByDefault:
    def test_a_default_installation_offers_nothing(self, client, monkeypatch):
        execute("DELETE FROM service_config WHERE service = 'enterprise_sso'")
        from backend.service_config import store
        store.invalidate()
        monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", False)
        r = client.post("/api/v1/auth/sso/discover", json={"email": "a@whatever-co.com"})
        assert r.json()["data"] == {"available": False, "enforced": False}
        r = client.get("/api/v1/auth/saml/start", params={"email": "a@whatever-co.com"}, follow_redirects=False)
        assert r.status_code == 302 and _error_of(r.headers["location"]) == "sso_not_available"

    def test_a_configured_tenant_is_invisible_while_the_instance_switch_is_off(
        self, client, configured, monkeypatch,
    ):
        monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", False)
        r = client.post("/api/v1/auth/sso/discover", json={"email": f"x@{configured.domain}"})
        assert r.json()["data"] == {"available": False, "enforced": False}
        r = client.get("/api/v1/auth/saml/start", params={"email": f"x@{configured.domain}"},
                       follow_redirects=False)
        assert _error_of(r.headers["location"]) == "sso_not_available"
        assert query_one("SELECT COUNT(*) AS n FROM oauth_flows WHERE provider = %s",
                         (f"saml:{configured.tid}",))["n"] == 0

    def test_the_switch_turning_off_between_start_and_post_refuses_the_post(
        self, client, configured, monkeypatch,
    ):
        email = f"ana@{configured.domain}"
        st = _start(client, email)
        monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", False)
        loc = _sign_in(configured, email, started=st)
        assert _error_of(loc) == "sso_not_available"
        assert _user(email) is None


# ── Routing by e-mail domain ─────────────────────────────────────────────────

class TestRouting:
    def test_discover_names_the_protocol_for_a_saml_tenant_only(self, client, configured):
        r = client.post("/api/v1/auth/sso/discover", json={"email": f"x@{configured.domain}"})
        assert r.json()["data"] == {"available": True, "enforced": False, "protocol": "saml"}
        r = client.post("/api/v1/auth/sso/discover", json={"email": "x@elsewhere-inc.com"})
        assert r.json()["data"] == {"available": False, "enforced": False}

    def test_a_disabled_provider_is_not_offered(self, client, company):
        company.configure(enabled=False)
        r = client.post("/api/v1/auth/sso/discover", json={"email": f"x@{company.domain}"})
        assert r.json()["data"]["available"] is False
        r = client.get("/api/v1/auth/saml/start", params={"email": f"x@{company.domain}"},
                       follow_redirects=False)
        assert _error_of(r.headers["location"]) == "sso_not_available"

    def test_an_unknown_domain_and_a_junk_address_start_nothing(self, client, configured):
        for email in ("a@elsewhere-inc.com", "no-at-sign", "", "a@b@c.com"):
            r = client.get("/api/v1/auth/saml/start", params={"email": email}, follow_redirects=False)
            assert _error_of(r.headers["location"]) == "sso_not_available", email
        assert query_one("SELECT COUNT(*) AS n FROM oauth_flows WHERE provider = %s",
                         (f"saml:{configured.tid}",))["n"] == 0


# ── The AuthnRequest ─────────────────────────────────────────────────────────

class TestStart:
    def test_redirects_to_the_idp_with_a_well_formed_stored_request(self, client, configured):
        st = _start(client, f"Ana@{configured.domain}")
        assert st.location.startswith(configured.idp.sso_url + "?")
        d = st.doc
        assert (d.namespaceURI, d.localName) == ("urn:oasis:names:tc:SAML:2.0:protocol", "AuthnRequest")
        assert d.getAttribute("Version") == "2.0"
        assert d.getAttribute("AssertionConsumerServiceURL") == ACS
        assert d.getAttribute("Destination") == configured.idp.sso_url
        assert d.getAttribute("ProtocolBinding").endswith("HTTP-POST")
        assert d.getElementsByTagName("saml:Issuer")[0].firstChild.data == configured.sp
        # The request id is the stored flow's nonce, which is what a Response must answer.
        row = query_one("SELECT * FROM oauth_flows WHERE provider = %s", (f"saml:{configured.tid}",))
        assert row and "_" + row["nonce"] == st.request_id
        assert row["state_hash"] == flow._h(st.relay_state)
        assert st.binding and row["binding_hash"] == flow._h(st.binding)

    def test_the_binding_cookie_is_http_only_and_scoped_to_the_saml_path(self, client, configured):
        client.cookies.clear()
        r = client.get("/api/v1/auth/saml/start", params={"email": f"a@{configured.domain}"},
                       follow_redirects=False)
        header = r.headers["set-cookie"].lower()
        assert "httponly" in header and "path=/api/v1/auth/saml" in header
        assert "samesite=lax" in header  # http development; https gets none+secure
        client.cookies.clear()

    def test_over_https_the_cookie_can_ride_the_cross_site_post(self, client, configured, monkeypatch):
        monkeypatch.setattr("backend.config.settings.frontend_url", "https://app.example.test")
        client.cookies.clear()
        r = client.get("/api/v1/auth/saml/start", params={"email": f"a@{configured.domain}"},
                       follow_redirects=False)
        header = r.headers["set-cookie"].lower()
        assert "samesite=none" in header and "secure" in header
        client.cookies.clear()

    def test_a_stored_non_https_sign_on_url_is_never_redirected_to(self, client, company):
        company.configure(sso_url="http://idp.example-saml.test/sso")
        r = client.get("/api/v1/auth/saml/start", params={"email": f"a@{company.domain}"},
                       follow_redirects=False)
        assert _error_of(r.headers["location"]) == "saml_config_invalid"

    def test_each_start_gets_its_own_request_id(self, client, configured):
        a, b = _start(client, f"a@{configured.domain}"), _start(client, f"a@{configured.domain}")
        assert a.request_id != b.request_id and a.relay_state != b.relay_state


# ── Signing in ───────────────────────────────────────────────────────────────

class TestSignIn:
    def test_a_first_sign_in_creates_a_viewer_links_the_identity_and_hands_off(self, client, configured):
        email = f"ana@{configured.domain}"
        loc = _sign_in(configured, email)
        code = _handoff(loc)
        u = _user(email)
        assert u["tenant_id"] == configured.tid
        assert u["role"] == "viewer"                       # the tenant default, never admin
        assert u["email_verified"] is True and u["has_password"] is False and u["status"] == "active"
        ident = query_one("SELECT * FROM user_identities WHERE user_id = %s", (u["id"],))
        assert ident["provider"] == f"saml:{configured.tid}" and ident["subject"] == email
        assert [e["context"].get("email") for e in _events(configured.tid, "account.sso_user_created")] == [email]
        assert len(_events(configured.tid, "account.sso_sign_in")) == 1
        row = query_one("SELECT user_id, provider, is_new_account FROM oauth_handoffs WHERE code_hash = %s",
                        (flow._h(code),))
        assert row["user_id"] == u["id"] and row["is_new_account"] is True
        # The flow is spent, and the browser cookie is cleared.
        assert query_one("SELECT COUNT(*) AS n FROM oauth_flows WHERE provider = %s",
                         (f"saml:{configured.tid}",))["n"] == 0

    def test_the_handoff_trades_for_the_apps_own_tokens(self, client, configured):
        email = f"ana@{configured.domain}"
        code = _handoff(_sign_in(configured, email))
        r = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["user"]["email"] == email and data["user"]["tenant_id"] == configured.tid
        assert data["access_token"] and data["refresh_token"]

    def test_a_second_sign_in_reuses_the_user(self, client, configured):
        email = f"ana@{configured.domain}"
        _handoff(_sign_in(configured, email))
        before = _count_users(configured.tid)
        _handoff(_sign_in(configured, email))
        assert _count_users(configured.tid) == before
        assert len(_events(configured.tid, "account.sso_user_created")) == 1
        assert len(_events(configured.tid, "account.sso_sign_in")) == 2

    def test_a_replayed_response_is_refused_and_creates_nothing(self, client, configured):
        email = f"ana@{configured.domain}"
        st = _start(client, email)
        xml = configured.idp.signed_response(request_id=st.request_id, sp_entity=configured.sp,
                                             acs=ACS, email=email)
        assert _handoff(_acs(client, st.relay_state, xml, st.binding))
        handoffs, users = _handoffs(), _count_users(configured.tid)
        again = _acs(client, st.relay_state, xml, st.binding)
        assert _error_of(again) == "oauth_state_invalid"
        assert _handoffs() == handoffs and _count_users(configured.tid) == users

    def test_a_missing_or_wrong_browser_cookie_is_refused_and_the_flow_is_spent(self, client, configured):
        email = f"ana@{configured.domain}"
        for cookie in (None, "somebody-elses-cookie"):
            st = _start(client, email)
            loc = _sign_in(configured, email, started=st, binding=cookie)
            assert _error_of(loc) == "oauth_state_invalid"
            assert query_one("SELECT 1 AS x FROM oauth_flows WHERE state_hash = %s",
                             (flow._h(st.relay_state),)) is None
        assert _user(email) is None

    def test_a_response_with_no_relay_state_or_an_invented_one_is_refused(self, client, configured):
        email = f"ana@{configured.domain}"
        st = _start(client, email)
        xml = configured.idp.signed_response(request_id=st.request_id, sp_entity=configured.sp,
                                             acs=ACS, email=email)
        assert _error_of(_acs(client, None, xml, st.binding)) == "oauth_state_invalid"
        assert _error_of(_acs(client, "invented-" + uuid4().hex, xml, st.binding)) == "oauth_state_invalid"
        # The legitimate flow is untouched by those and still works.
        assert _handoff(_acs(client, st.relay_state, xml, st.binding))
        assert _user(email)

    def test_an_unsolicited_idp_initiated_response_cannot_sign_anyone_in(self, client, configured):
        email = f"ana@{configured.domain}"
        xml = configured.idp.signed_response(request_id="_nobody-asked", sp_entity=configured.sp,
                                             acs=ACS, email=email)
        assert _error_of(_acs(client, "x", xml, "y")) == "oauth_state_invalid"
        assert _user(email) is None

    def test_a_forged_signature_is_refused_logged_and_creates_nothing(self, client, configured):
        email = f"ana@{configured.domain}"
        attacker = fx.FakeIdp()
        handoffs = _handoffs()
        loc = _sign_in(configured, email, idp=attacker)
        assert _error_of(loc) == "saml_signature_invalid"
        assert _user(email) is None and _handoffs() == handoffs
        refused = _events(configured.tid, "account.sso_sign_in_refused")
        assert len(refused) == 1
        assert refused[0]["status"] == "error"
        assert refused[0]["context"]["reason_params"] == {"code": "saml_signature_invalid"}

    def test_an_unsigned_response_is_refused(self, client, configured):
        email = f"ana@{configured.domain}"
        assert _error_of(_sign_in(configured, email, sign="none")) == "saml_unsigned"
        assert _user(email) is None

    def test_a_response_for_another_tenants_connection_is_refused(self, client, configured, idp):
        other = Company(idp).configure()
        try:
            email = f"ana@{configured.domain}"
            st = _start(client, email)
            # The IdP (same key for both tenants, the worst case) signs for the OTHER tenant's SP.
            xml = idp.signed_response(request_id=st.request_id, sp_entity=other.sp, acs=ACS, email=email)
            assert _error_of(_acs(client, st.relay_state, xml, st.binding)) == "saml_audience_mismatch"
            assert _user(email) is None and _count_users(other.tid) == 1
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other.tid,))

    def test_an_email_outside_the_tenants_domains_is_refused(self, client, configured):
        email = f"ana@{configured.domain}"
        loc = _sign_in(configured, email, local_email="ana@some-other-company.com",
                       assertion_kw=dict(name_id="ana@some-other-company.com"))
        assert _error_of(loc) == "sso_domain_not_allowed"
        assert _user("ana@some-other-company.com") is None

    def test_a_lookalike_domain_is_not_the_tenants_domain(self, client, configured):
        email = f"ana@{configured.domain}"
        evil = f"ana@{configured.domain}.evil.test"
        loc = _sign_in(configured, email, local_email=evil, assertion_kw=dict(name_id=evil))
        assert _error_of(loc) == "sso_domain_not_allowed"
        evil2 = f"ana@evil{configured.domain}"
        loc = _sign_in(configured, email, local_email=evil2, assertion_kw=dict(name_id=evil2))
        assert _error_of(loc) == "sso_domain_not_allowed"

    def test_a_missing_email_is_refused(self, client, configured):
        email = f"ana@{configured.domain}"
        loc = _sign_in(configured, email, assertion_kw=dict(
            name_id="opaque-1", name_id_format=fx.PERSISTENT, attributes={}))
        assert _error_of(loc) == "sso_email_missing"

    def test_the_named_email_attribute_is_used(self, client, company):
        company.configure(email_attribute="corp_mail")
        email = f"ana@{company.domain}"
        loc = _sign_in(company, email, assertion_kw=dict(
            name_id="u-100", name_id_format=fx.PERSISTENT,
            attributes={"corp_mail": [email], "mail": ["somebody-else@" + company.domain]}))
        assert _handoff(loc)
        assert _user(email) and _user("somebody-else@" + company.domain) is None
        assert query_one("SELECT subject FROM user_identities WHERE user_id = %s",
                         (_user(email)["id"],))["subject"] == "u-100"

    def test_an_email_of_another_tenant_is_never_touched_or_reused(self, client, configured, idp):
        other_domain_user = f"ana@{configured.domain}"
        other_tenant, other_user = _make_tenant("another-corp.example.com")
        from backend.users import service as user_svc
        # A person of ANOTHER tenant who happens to hold an address on this domain.
        stray = user_svc.create_user(tenant_id=other_tenant["id"], email=other_domain_user,
                                     password="TestPass123!", role="analyst")
        try:
            loc = _sign_in(configured, other_domain_user)
            assert _error_of(loc) == "sso_account_conflict"
            assert query_one("SELECT tenant_id, role FROM users WHERE id = %s", (stray["id"],)) == {
                "tenant_id": other_tenant["id"], "role": "analyst"}
            assert query_one("SELECT 1 AS x FROM user_identities WHERE user_id = %s", (stray["id"],)) is None
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other_tenant["id"],))

    def test_a_suspended_user_is_refused_like_a_password_login(self, client, configured):
        u = configured.person("sus", role="viewer")
        execute("UPDATE users SET status = 'inactive' WHERE id = %s", (u["id"],))
        loc = _sign_in(configured, u["email"])
        assert _error_of(loc) == "account_not_active"
        assert query_one("SELECT COUNT(*) AS n FROM oauth_handoffs WHERE user_id = %s", (u["id"],))["n"] == 0

    def test_an_existing_user_with_an_unverified_password_is_linked_and_loses_the_password(
        self, client, configured,
    ):
        squatted = configured.person("squat", role="viewer", verified=False)
        loc = _sign_in(configured, squatted["email"])
        assert _handoff(loc)
        row = query_one("SELECT email_verified, has_password, role FROM users WHERE id = %s", (squatted["id"],))
        assert row == {"email_verified": True, "has_password": False, "role": "viewer"}
        assert _count_users(configured.tid) == 2  # the admin and the squatted account, no duplicate


# ── Roles ────────────────────────────────────────────────────────────────────

class TestRoles:
    def test_the_default_role_applies_and_is_never_admin_even_in_a_hand_edited_row(self, client, company):
        company.configure(default_role="analyst")
        a = f"a@{company.domain}"
        assert _handoff(_sign_in(company, a)) and _user(a)["role"] == "analyst"
        # Somebody edits the table by hand: the read side still never mints an administrator.
        execute("UPDATE saml_providers SET default_role = 'admin' WHERE tenant_id = %s", (company.tid,))
        b = f"b@{company.domain}"
        assert _handoff(_sign_in(company, b)) and _user(b)["role"] == "viewer"

    def test_a_group_maps_to_a_role(self, client, company):
        company.configure(groups_attribute="groups", group_roles={"Planners": "analyst"})
        email = f"p@{company.domain}"
        loc = _sign_in(company, email, assertion_kw=dict(attributes={
            "email": [email], "groups": ["Everyone", "Planners"]}))
        assert _handoff(loc) and _user(email)["role"] == "analyst"

    def test_a_hand_edited_group_rule_to_admin_is_ignored(self, client, company):
        company.configure(groups_attribute="groups", group_roles={"Root": "admin"})
        email = f"r@{company.domain}"
        loc = _sign_in(company, email, assertion_kw=dict(attributes={"email": [email], "groups": ["Root"]}))
        assert _handoff(loc) and _user(email)["role"] == "viewer"

    def test_a_group_change_changes_the_role_and_cuts_the_sessions(self, client, company):
        company.configure(groups_attribute="groups", group_roles={"Planners": "analyst"})
        email = f"p@{company.domain}"
        _handoff(_sign_in(company, email, assertion_kw=dict(attributes={"email": [email], "groups": ["Planners"]})))
        uid = _user(email)["id"]
        execute("INSERT INTO refresh_tokens (user_id, tenant_id, hash, expires_at) "
                "VALUES (%s, %s, %s, NOW() + interval '1 day')", (uid, company.tid, uuid4().hex))
        _handoff(_sign_in(company, email, assertion_kw=dict(attributes={"email": [email], "groups": []})))
        assert _user(email)["role"] == "viewer"
        assert query_one("SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = %s", (uid,))["n"] == 0
        mapped = _events(company.tid, "account.sso_role_mapped")
        assert len(mapped) == 1 and mapped[0]["context"]["previous_role"] == "analyst"

    def test_an_administrator_signs_in_but_a_group_never_touches_their_role(self, client, company):
        company.configure(groups_attribute="groups", group_roles={"Planners": "analyst"})
        loc = _sign_in(company, company.admin["email"], assertion_kw=dict(attributes={
            "email": [company.admin["email"]], "groups": []}))
        assert _handoff(loc)
        assert _user(company.admin["email"])["role"] == "admin"
        assert _events(company.tid, "account.sso_role_mapped") == []


# ── Require SSO ──────────────────────────────────────────────────────────────

class TestEnforcement:
    def _login(self, client, email, password="TestPass123!"):
        return client.post("/api/v1/auth/login", json={"email": email, "password": password})

    def test_password_login_works_until_the_tenant_requires_sso(self, client, company):
        company.configure()
        assert self._login(client, company.admin["email"]).status_code == 200

    def test_a_required_tenant_has_no_password_door_even_for_a_correct_password(self, client, company):
        company.configure(enforce_sso=True)
        r = self._login(client, company.admin["email"])
        assert r.status_code == 403 and r.json()["error_code"] == "sso_required"
        assert "access_token" not in r.text
        r = client.post("/api/v1/auth/sso/discover", json={"email": company.admin["email"]})
        assert r.json()["data"] == {"available": True, "enforced": True, "protocol": "saml"}

    def test_enforcement_is_only_applied_to_the_tenants_own_domains(self, client, company):
        company.configure(enforce_sso=True)
        other, user = _make_tenant("untouched-corp.example.com")
        try:
            assert self._login(client, user["email"]).status_code == 200
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_enforcement_is_suspended_while_the_provider_is_disabled_or_the_switch_is_off(
        self, client, company, monkeypatch,
    ):
        company.configure(enforce_sso=True, enabled=False)
        assert self._login(client, company.admin["email"]).status_code == 200
        company.configure(enforce_sso=True, enabled=True)
        monkeypatch.setattr("backend.config.settings.enterprise_sso_enabled", False)
        assert self._login(client, company.admin["email"]).status_code == 200

    def test_a_required_tenant_still_lets_its_people_in_through_saml(self, client, company):
        company.configure(enforce_sso=True)
        loc = _sign_in(company, company.admin["email"])
        assert _handoff(loc)
        assert query_one("SELECT provider FROM user_identities WHERE user_id = %s",
                         (company.admin["id"],))["provider"] == f"saml:{company.tid}"

    def test_the_identity_listing_hides_company_sign_in_rows_like_oidc_ones(self, client, company):
        company.configure()
        _handoff(_sign_in(company, company.admin["email"]))
        tok = self._login(client, company.admin["email"]).json()["data"]["access_token"]
        r = client.get("/api/v1/auth/identities", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        assert r.json()["data"]["identities"] == []


# ── One protocol per tenant ──────────────────────────────────────────────────

class TestOneProtocol:
    def test_saving_oidc_while_saml_is_configured_is_refused_and_changes_nothing(self, client, company):
        company.configure()
        r = client.post("/api/v1/auth/login", json={"email": company.admin["email"], "password": "TestPass123!"})
        headers = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
        body = {"issuer": "https://idp.example-sso.test", "client_id": "c", "client_secret": "s",
                "allowed_domains": [company.domain], "default_role": "viewer", "enforce_sso": False,
                "groups_claim": None, "group_roles": {}, "enabled": True}
        r = client.put("/api/v1/auth/sso/config", json=body, headers=headers)
        assert r.status_code == 409 and r.json()["error_code"] == "sso_protocol_conflict"
        assert query_one("SELECT 1 AS x FROM sso_providers WHERE tenant_id = %s", (company.tid,)) is None
        assert query_one("SELECT idp_entity_id FROM saml_providers WHERE tenant_id = %s",
                         (company.tid,))["idp_entity_id"] == company.idp.entity_id

    def test_an_oidc_provider_row_is_never_used_to_sign_a_saml_response_in(self, client, company):
        # A tenant with only an OIDC row has no SAML provider: start refuses.
        execute("""INSERT INTO sso_providers (tenant_id, issuer, client_id, client_secret_enc,
                       authorization_endpoint, token_endpoint, jwks_uri, allowed_domains)
                   VALUES (%s, 'https://i.test', 'c', 'x', 'https://i.test/a', 'https://i.test/t',
                           'https://i.test/j', %s::jsonb)""",
                (company.tid, json.dumps([company.domain])))
        execute("INSERT INTO sso_domains (domain, tenant_id) VALUES (%s, %s)", (company.domain, company.tid))
        r = client.get("/api/v1/auth/saml/start", params={"email": f"a@{company.domain}"}, follow_redirects=False)
        assert _error_of(r.headers["location"]) == "sso_not_available"


# ── Hardening of the endpoint itself ─────────────────────────────────────────

class TestAcsEndpoint:
    def test_only_a_urlencoded_form_is_accepted(self, client, configured):
        st = _start(client, f"a@{configured.domain}")
        client.cookies.set(flow.BINDING_COOKIE, st.binding)
        r = client.post("/api/v1/auth/saml/acs", json={"SAMLResponse": "x", "RelayState": st.relay_state},
                        follow_redirects=False)
        client.cookies.clear()
        assert r.status_code == 303 and _error_of(r.headers["location"]) == "saml_response_invalid"
        # The refusal did not consume the flow: the person can still finish.
        assert query_one("SELECT 1 AS x FROM oauth_flows WHERE state_hash = %s", (flow._h(st.relay_state),))

    def test_a_repeated_field_is_ambiguous_and_refused(self, client, configured):
        r = client.post("/api/v1/auth/saml/acs", content=b"SAMLResponse=a&SAMLResponse=b&RelayState=x",
                        headers={"content-type": "application/x-www-form-urlencoded"}, follow_redirects=False)
        assert r.status_code == 303 and _error_of(r.headers["location"]) == "saml_response_invalid"

    def test_an_oversized_body_is_refused_without_reading_it_all(self, client, configured):
        big = b"SAMLResponse=" + b"A" * (800 * 1024)
        r = client.post("/api/v1/auth/saml/acs", content=big,
                        headers={"content-type": "application/x-www-form-urlencoded"}, follow_redirects=False)
        assert r.status_code == 303 and _error_of(r.headers["location"]) == "saml_response_invalid"

    def test_garbage_in_the_saml_response_field_is_refused_and_spends_the_flow(self, client, configured):
        email = f"a@{configured.domain}"
        st = _start(client, email)
        loc = _acs(client, st.relay_state, "", st.binding, raw_response="%%%not-base64%%%")
        assert _error_of(loc) == "saml_response_invalid"
        assert query_one("SELECT 1 AS x FROM oauth_flows WHERE state_hash = %s", (flow._h(st.relay_state),)) is None
        assert _user(email) is None

    def test_the_endpoint_is_rate_limited(self, client, configured, monkeypatch):
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        execute("DELETE FROM auth_rate_events WHERE key LIKE 'saml-acs:%%'")
        codes = []
        try:
            for _ in range(25):
                r = client.post("/api/v1/auth/saml/acs", data={"SAMLResponse": "x"}, follow_redirects=False)
                codes.append(_error_of(r.headers["location"]))
        finally:
            execute("DELETE FROM auth_rate_events WHERE key LIKE 'saml-acs:%%'")
        assert codes[:20] == ["oauth_state_invalid"] * 20 and codes[20:] == ["too_many_attempts"] * 5
