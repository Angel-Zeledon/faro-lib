"""Per-tenant IP allowlist, as Python enforces it.

The admin routes (list, add, delete, enable) live in Rust and are covered by
the contract harness; here the rows are written the way those routes write them
and every assertion is about what the guard does with them and what the
database says afterwards.

The TestClient's socket peer is the string "testclient", not an address, so the
tests that need a client address set `trusted_proxy_hops = 1` and send the
address in `X-Forwarded-For`, exactly the shape one proxy produces.
"""

from uuid import uuid4

import pytest

from backend.auth.api_key_auth import hash_key
from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.ip_allowlist.service import client_ip

URL = "/api/v1/entitlements"
OFFICE = "203.0.113.0/24"


def _policy(tenant_id: str, entries: list[str], enabled: bool = True) -> None:
    execute(
        "INSERT INTO ip_allowlist_policies (tenant_id, enabled) VALUES (%s, %s) "
        "ON CONFLICT (tenant_id) DO UPDATE SET enabled = EXCLUDED.enabled",
        (tenant_id, enabled),
    )
    for cidr in entries:
        execute(
            "INSERT INTO ip_allowlist_entries (id, tenant_id, cidr, label) VALUES (%s, %s, %s, 'office')",
            (f"ipa_{uuid4().hex[:12]}", tenant_id, cidr),
        )


def _from(ip: str, headers: dict) -> dict:
    return {**headers, "X-Forwarded-For": ip}


def _mint_key(tenant_id: str) -> dict:
    raw = f"sk_live_{uuid4().hex}{uuid4().hex}"
    execute(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4)
           VALUES (gen_random_uuid()::text, %s, 'ipa-key', %s, 'viewer', 'usr_test', %s)""",
        (tenant_id, hash_key(raw), raw[-4:]),
    )
    return {"Authorization": f"Bearer {raw}"}


def _refusals(tenant_id: str) -> list[dict]:
    return query(
        "SELECT user_id, context FROM activity_logs "
        "WHERE tenant_id = %s AND action = 'account.ip_access_refused' ORDER BY created_at",
        (tenant_id,),
    )


@pytest.fixture(autouse=True)
def _one_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)


class TestClientAddress:
    """The trap: a header the caller writes must never decide who they are."""

    def test_the_entry_the_trusted_proxy_wrote_is_the_client(self):
        assert str(client_ip("198.51.100.7", "10.0.0.2", 1)) == "198.51.100.7"

    def test_entries_a_client_prepended_are_ignored(self):
        # The caller claims an office address; the proxy appended the real one.
        assert str(client_ip("203.0.113.9, 198.51.100.7", "10.0.0.2", 1)) == "198.51.100.7"

    def test_two_hops_skip_the_inner_proxy_entry(self):
        assert str(client_ip("203.0.113.9, 198.51.100.7, 10.1.1.1", "10.0.0.2", 2)) == "198.51.100.7"

    def test_zero_hops_ignores_the_header_entirely(self):
        assert str(client_ip("203.0.113.9", "198.51.100.7", 0)) == "198.51.100.7"

    def test_a_chain_shorter_than_the_hops_falls_back_to_its_first_entry(self):
        assert str(client_ip("", "198.51.100.7", 3)) == "198.51.100.7"

    def test_ipv4_mapped_ipv6_is_the_ipv4_address(self):
        assert str(client_ip("::ffff:203.0.113.5", "10.0.0.2", 1)) == "203.0.113.5"

    def test_garbage_is_no_address_at_all(self):
        assert client_ip("not-an-ip", "10.0.0.2", 1) is None
        assert client_ip("203.0.113.5:8080", "10.0.0.2", 1) is None


class TestTokenEnforcement:

    def test_no_policy_changes_nothing(self, client, auth_headers):
        assert client.get(URL, headers=_from("198.51.100.7", auth_headers)).status_code == 200

    def test_an_allowed_address_passes_and_an_outside_one_is_refused(
            self, client, auth_headers, test_tenant):
        _policy(test_tenant["id"], [OFFICE])
        assert client.get(URL, headers=_from("203.0.113.50", auth_headers)).status_code == 200

        resp = client.get(URL, headers=_from("198.51.100.7", auth_headers))
        assert resp.status_code == 403
        body = resp.json()
        assert body["error_code"] == "ip_not_allowed"
        assert body["error_params"] == {"ip": "198.51.100.7"}

    def test_a_forged_leading_entry_does_not_open_the_door(
            self, client, auth_headers, test_tenant):
        _policy(test_tenant["id"], [OFFICE])
        resp = client.get(URL, headers=_from("203.0.113.9, 198.51.100.7", auth_headers))
        assert resp.status_code == 403
        assert resp.json()["error_params"] == {"ip": "198.51.100.7"}

    def test_a_disabled_policy_filters_nobody(self, client, auth_headers, test_tenant):
        _policy(test_tenant["id"], [OFFICE], enabled=False)
        assert client.get(URL, headers=_from("198.51.100.7", auth_headers)).status_code == 200

    def test_an_enabled_policy_with_no_entries_refuses_everyone(
            self, client, auth_headers, test_tenant):
        _policy(test_tenant["id"], [])
        assert client.get(URL, headers=_from("203.0.113.5", auth_headers)).status_code == 403

    def test_ipv6_ranges_match(self, client, auth_headers, test_tenant):
        _policy(test_tenant["id"], ["2001:db8::/32"])
        assert client.get(URL, headers=_from("2001:db8:1::5", auth_headers)).status_code == 200
        assert client.get(URL, headers=_from("2001:db9::5", auth_headers)).status_code == 403

    def test_an_unreadable_address_is_outside(self, client, auth_headers, test_tenant):
        _policy(test_tenant["id"], ["0.0.0.0/0"])
        resp = client.get(URL, headers=_from("garbage", auth_headers))
        assert resp.status_code == 403
        assert resp.json()["error_params"] == {"ip": "unknown"}

    def test_another_tenants_policy_does_not_apply(
            self, client, test_tenant, make_tenant_user_headers):
        _policy(test_tenant["id"], [])
        other = make_tenant_user_headers("admin")
        assert client.get(URL, headers=_from("198.51.100.7", other)).status_code == 200


class TestKeyEnforcement:

    def test_a_key_outside_the_allowlist_is_refused_and_not_metered(
            self, client, test_tenant):
        _policy(test_tenant["id"], [OFFICE])
        key = _mint_key(test_tenant["id"])
        assert client.get(URL, headers=_from("203.0.113.8", key)).status_code == 200
        count = ("SELECT COALESCE(SUM(calls), 0) AS n FROM api_usage_daily WHERE tenant_id = %s")
        before = query_one(count, (test_tenant["id"],))["n"]
        assert before >= 1

        resp = client.get(URL, headers=_from("198.51.100.7", key))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "ip_not_allowed"
        assert query_one(count, (test_tenant["id"],))["n"] == before, "a refused call must not be metered"

    def test_a_key_is_unaffected_without_a_policy(self, client, test_tenant):
        key = _mint_key(test_tenant["id"])
        assert client.get(URL, headers=_from("198.51.100.7", key)).status_code == 200

    def test_the_refusal_names_the_key_as_actor(self, client, test_tenant):
        _policy(test_tenant["id"], [OFFICE])
        key = _mint_key(test_tenant["id"])
        client.get(URL, headers=_from("198.51.100.7", key))
        rows = _refusals(test_tenant["id"])
        assert len(rows) == 1
        assert rows[0]["user_id"].startswith("api_key:")


class TestLoginAndRefresh:

    def test_login_from_outside_is_refused_only_after_the_password_matched(
            self, client, registered_user, test_tenant):
        _policy(test_tenant["id"], [OFFICE])
        outside = {"X-Forwarded-For": "198.51.100.7"}

        wrong = client.post("/api/v1/auth/login", headers=outside, json={
            "email": registered_user["email"], "password": "WrongPass123!"})
        assert wrong.status_code == 401, "a wrong password must not reveal that the tenant filters"

        right = client.post("/api/v1/auth/login", headers=outside, json={
            "email": registered_user["email"], "password": registered_user["password"]})
        assert right.status_code == 403
        assert right.json()["error_code"] == "ip_not_allowed"
        assert query_one(
            "SELECT 1 AS x FROM refresh_tokens WHERE user_id = %s", (registered_user["user"]["id"],)
        ) is None, "a refused login must not mint a refresh token"

        inside = client.post("/api/v1/auth/login", headers={"X-Forwarded-For": "203.0.113.4"}, json={
            "email": registered_user["email"], "password": registered_user["password"]})
        assert inside.status_code == 200

    def test_refresh_from_outside_is_refused(self, client, registered_user, test_tenant):
        login = client.post("/api/v1/auth/login", headers={"X-Forwarded-For": "203.0.113.4"}, json={
            "email": registered_user["email"], "password": registered_user["password"]})
        refresh_token = login.json()["data"]["refresh_token"]
        _policy(test_tenant["id"], [OFFICE])

        out = client.post("/api/v1/auth/refresh", headers={"X-Forwarded-For": "198.51.100.7"},
                          json={"refresh_token": refresh_token})
        assert out.status_code == 403
        assert out.json()["error_code"] == "ip_not_allowed"
        ok = client.post("/api/v1/auth/refresh", headers={"X-Forwarded-For": "203.0.113.4"},
                         json={"refresh_token": refresh_token})
        assert ok.status_code == 200


class TestRefusalEvent:

    def test_a_refusal_is_recorded_once_per_address_per_window(
            self, client, auth_headers, registered_user, test_tenant):
        _policy(test_tenant["id"], [OFFICE])
        for _ in range(3):
            assert client.get(URL, headers=_from("198.51.100.7", auth_headers)).status_code == 403
        client.get(URL, headers=_from("198.51.100.8", auth_headers))

        rows = _refusals(test_tenant["id"])
        assert [r["context"]["ip"] for r in rows] == ["198.51.100.7", "198.51.100.8"]
        first = rows[0]
        assert first["user_id"] == registered_user["user"]["id"]
        assert first["context"]["severity"] == "warning"
        assert first["context"]["reason"] == "ip_not_in_allowlist"

    def test_nothing_is_recorded_for_an_allowed_call(self, client, auth_headers, test_tenant):
        _policy(test_tenant["id"], [OFFICE])
        client.get(URL, headers=_from("203.0.113.5", auth_headers))
        assert _refusals(test_tenant["id"]) == []
