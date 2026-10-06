"""SCIM 2.0 provisioning against the database (backend/scim/, api/v1/scim.py).

Every test asserts the rows, not the status code alone: a provisioning call
that answers 200 while writing the wrong user - or nothing - is the failure
this file exists to catch. The company tenant, its SSO configuration and the
fake identity provider come from `test_enterprise_sso.py`.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.tests.test_enterprise_sso import (  # noqa: F401 - fixtures
    company, configured, idp, sso_on,
)

SCIM = "/api/v1/scim/v2"
MEDIA = "application/scim+json"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
PASSWORD = "TestPass123!"


def _login(client, email, password=PASSWORD):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


class Idp:
    """An identity provider holding one SCIM token."""

    def __init__(self, company, token):
        self.company, self.client, self.token = company, company.client, token

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": MEDIA}

    def get(self, path, **params):
        return self.client.get(f"{SCIM}{path}", headers=self.h, params=params)

    def send(self, method, path, body):
        return self.client.request(method, f"{SCIM}{path}", headers=self.h,
                                   content=json.dumps(body))

    def create(self, local, **extra):
        body = {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": f"{local}@{self.company.domain}",
                "name": {"givenName": local.title(), "familyName": "Tester"},
                "externalId": f"ext-{local}", "active": True}
        body.update(extra)
        return self.send("POST", "/Users", body)

    def patch(self, path, *ops):
        return self.send("PATCH", path, {"schemas": [PATCH], "Operations": list(ops)})


def _mint(company, manage_admins=False):
    r = company.client.post("/api/v1/auth/sso/scim/token",
                            json={"manage_admins": manage_admins}, headers=company.headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


@pytest.fixture
def scim(configured):
    return Idp(configured, _mint(configured))


@pytest.fixture
def scim_admins(configured):
    return Idp(configured, _mint(configured, manage_admins=True))


def _user(email):
    return query_one("SELECT * FROM users WHERE email = %s", (email.lower(),))


def _events(tenant_id, action):
    return query("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = %s",
                 (tenant_id, action))


def _scim_log(tenant_id):
    return query("SELECT * FROM scim_events WHERE tenant_id = %s ORDER BY created_at",
                 (tenant_id,))


# ── The token, from the admin's side ─────────────────────────────────────────

class TestTokenAdministration:
    def test_viewer_and_analyst_cannot_mint(self, configured):
        for role in ("viewer", "analyst"):
            p = configured.person(f"{role}{uuid4().hex[:4]}", role=role)
            r = configured.client.post("/api/v1/auth/sso/scim/token", json={},
                                       headers=_login(configured.client, p["email"]))
            assert r.status_code == 403, r.text
        assert query_one("SELECT 1 AS x FROM scim_tokens WHERE tenant_id = %s",
                         (configured.tid,)) is None

    def test_admin_mints_and_only_a_hash_is_stored(self, configured):
        raw = _mint(configured)
        row = query_one("SELECT * FROM scim_tokens WHERE tenant_id = %s AND revoked_at IS NULL",
                        (configured.tid,))
        assert row is not None
        assert raw.startswith("scim_") and row["id"] in raw
        assert raw.split("_", 2)[2] not in row["secret_hash"]
        assert raw not in json.dumps({k: str(v) for k, v in row.items()})
        # The status endpoint never shows it again.
        status = configured.client.get("/api/v1/auth/sso/scim", headers=configured.headers)
        assert status.status_code == 200
        assert raw not in status.text
        assert status.json()["data"]["token"]["id"] == row["id"]
        assert _events(configured.tid, "account.scim_token_created")

    def test_a_warehouse_scoped_admin_cannot_mint(self, configured):
        p = configured.person(f"sa{uuid4().hex[:4]}", role="admin")
        execute("UPDATE users SET warehouse_scope = '[]'::jsonb WHERE id = %s", (p["id"],))
        r = configured.client.post("/api/v1/auth/sso/scim/token", json={"manage_admins": True},
                                   headers=_login(configured.client, p["email"]))
        assert r.status_code == 403
        assert r.json()["error_code"] == "scim_scoped_admin_forbidden"
        assert query_one("SELECT 1 AS x FROM scim_tokens WHERE tenant_id = %s",
                         (configured.tid,)) is None

    def test_minting_requires_sso(self, company):
        r = company.client.post("/api/v1/auth/sso/scim/token", json={}, headers=company.headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "scim_requires_sso"
        assert query_one("SELECT 1 AS x FROM scim_tokens WHERE tenant_id = %s",
                         (company.tid,)) is None

    def test_rotation_kills_the_old_token_at_once(self, configured):
        old = Idp(configured, _mint(configured))
        assert old.get("/Users").status_code == 200
        new = Idp(configured, _mint(configured))
        assert old.get("/Users").status_code == 401
        assert new.get("/Users").status_code == 200
        rows = query("SELECT * FROM scim_tokens WHERE tenant_id = %s", (configured.tid,))
        assert len(rows) == 2
        assert sum(1 for r in rows if r["revoked_at"] is None) == 1

    def test_revoke(self, scim):
        r = scim.client.delete("/api/v1/auth/sso/scim/token", headers=scim.company.headers)
        assert r.status_code == 200
        assert query_one("SELECT 1 AS x FROM scim_tokens WHERE tenant_id = %s AND revoked_at IS NULL",
                         (scim.company.tid,)) is None
        assert scim.get("/Users").status_code == 401
        assert _events(scim.company.tid, "account.scim_token_revoked")

    def test_viewer_cannot_revoke(self, scim):
        p = scim.company.person(f"v{uuid4().hex[:4]}", role="viewer")
        r = scim.client.delete("/api/v1/auth/sso/scim/token",
                               headers=_login(scim.client, p["email"]))
        assert r.status_code == 403
        assert scim.get("/Users").status_code == 200

    def test_analyst_cannot_toggle_manage_admins(self, scim):
        p = scim.company.person(f"an{uuid4().hex[:4]}", role="analyst")
        r = scim.client.patch("/api/v1/auth/sso/scim/token", json={"manage_admins": True},
                              headers=_login(scim.client, p["email"]))
        assert r.status_code == 403
        assert query_one("SELECT manage_admins FROM scim_tokens WHERE tenant_id = %s "
                         "AND revoked_at IS NULL", (scim.company.tid,))["manage_admins"] is False

    def test_manage_admins_toggle(self, scim):
        r = scim.client.patch("/api/v1/auth/sso/scim/token", json={"manage_admins": True},
                              headers=scim.company.headers)
        assert r.status_code == 200
        assert query_one("SELECT manage_admins FROM scim_tokens WHERE tenant_id = %s "
                         "AND revoked_at IS NULL", (scim.company.tid,))["manage_admins"] is True

    def test_removing_sso_revokes_the_token(self, scim):
        r = scim.client.delete("/api/v1/auth/sso/config", headers=scim.company.headers)
        assert r.status_code == 200
        assert query_one("SELECT 1 AS x FROM scim_tokens WHERE tenant_id = %s AND revoked_at IS NULL",
                         (scim.company.tid,)) is None
        assert scim.get("/Users").status_code == 401


# ── What the token can and cannot reach ──────────────────────────────────────

class TestTokenScope:
    def test_scim_token_is_refused_everywhere_else(self, scim):
        for path in ("/api/v1/users/me", "/api/v1/users", "/api/v1/auth/sso/config",
                     "/api/v1/auth/sso/scim"):
            r = scim.client.get(path, headers={"Authorization": f"Bearer {scim.token}"})
            assert r.status_code == 401, (path, r.text)

    def test_jwt_and_api_key_are_refused_on_scim(self, scim):
        from backend.auth.api_key_auth import hash_key
        r = scim.client.get(f"{SCIM}/Users", headers=scim.company.headers)
        assert r.status_code == 401
        assert r.headers["content-type"].startswith(MEDIA)
        assert r.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        raw = f"sk_live_{uuid4().hex}{uuid4().hex}"
        execute("""INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4)
                   VALUES (gen_random_uuid()::text, %s, 'k', %s, 'analyst', 'usr_x', %s)""",
                (scim.company.tid, hash_key(raw), raw[-4:]))
        r = scim.client.get(f"{SCIM}/Users", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 401

    def test_never_sees_another_tenant(self, scim):
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        other = create_tenant(f"pytest-scim-other-{uuid4().hex[:6]}")
        try:
            stranger = user_svc.create_user(other["id"], f"s{uuid4().hex[:6]}@elsewhere.test",
                                            PASSWORD, role="analyst")
            assert scim.get(f"/Users/{stranger['id']}").status_code == 404
            r = scim.patch(f"/Users/{stranger['id']}",
                           {"op": "replace", "value": {"active": False}})
            assert r.status_code == 404
            r = scim.patch("/Groups/viewer", {"op": "add", "path": "members",
                                              "value": [{"value": stranger["id"]}]})
            assert r.status_code == 400
            row = _user(stranger["email"])
            assert (row["status"], row["role"], row["tenant_id"]) == ("active", "analyst", other["id"])
            listed = scim.get("/Users", count="200").json()
            assert stranger["id"] not in {u["id"] for u in listed["Resources"]}
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_sso_disabled_stops_scim(self, scim):
        execute("UPDATE sso_providers SET enabled = FALSE WHERE tenant_id = %s",
                (scim.company.tid,))
        r = scim.get("/Users")
        assert r.status_code == 403
        assert r.json()["errorCode"] == "scim_sso_not_configured"


# ── The lifecycle ────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_create(self, scim):
        r = scim.create("ana")
        assert r.status_code == 201, r.text
        assert r.headers["content-type"].startswith(MEDIA)
        assert r.headers["location"].endswith(f"/Users/{r.json()['id']}")
        assert r.headers["etag"].startswith('W/"')
        row = _user(f"ana@{scim.company.domain}")
        assert row["tenant_id"] == scim.company.tid
        assert row["role"] == "viewer"
        assert row["status"] == "active"
        assert row["email_verified"] is True, "the IdP vouches for the address"
        assert row["has_password"] is False
        assert row["full_name"] == "Ana Tester"
        link = query_one("SELECT * FROM scim_user_links WHERE user_id = %s", (row["id"],))
        assert link["external_id"] == "ext-ana" and link["tenant_id"] == scim.company.tid
        log = _scim_log(scim.company.tid)
        assert [(e["operation"], e["outcome"]) for e in log] == [("create", "success")]
        ev = _events(scim.company.tid, "account.scim_user_created")
        assert len(ev) == 1 and ev[0]["user_id"] == "scim"

    def test_password_door_stays_shut(self, scim):
        scim.create("pw", password="Whatever123!")
        r = scim.client.post("/api/v1/auth/login",
                             json={"email": f"pw@{scim.company.domain}", "password": "Whatever123!"})
        assert r.status_code == 401

    def test_filter_finds_exactly_one(self, scim):
        scim.create("bo")
        r = scim.get("/Users", filter=f'userName eq "BO@{scim.company.domain.upper()}"')
        assert r.status_code == 200
        body = r.json()
        assert body["totalResults"] == 1 and body["Resources"][0]["userName"] == f"bo@{scim.company.domain}"
        r = scim.get("/Users", filter='externalId eq "ext-bo"')
        assert r.json()["totalResults"] == 1
        r = scim.get("/Users", filter='userName eq "nobody@x.test" or 1 eq 1')
        assert r.status_code == 400 and r.json()["scimType"] == "invalidFilter"

    def test_paging(self, scim):
        for i in range(3):
            scim.create(f"p{i}")
        r = scim.get("/Users", startIndex="2", count="2").json()
        assert r["startIndex"] == 2 and r["itemsPerPage"] == 2
        assert r["totalResults"] == query_one(
            "SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s", (scim.company.tid,))["n"]

    def test_deactivate_reactivate_delete_keeps_the_row(self, scim):
        uid = scim.create("cy").json()["id"]
        r = scim.patch(f"/Users/{uid}", {"op": "replace", "value": {"active": False}})  # Okta
        assert r.status_code == 200 and r.json()["active"] is False
        row = query_one("SELECT * FROM users WHERE id = %s", (uid,))
        assert row["status"] == "inactive"
        assert row["sessions_invalid_before"] is not None
        assert _events(scim.company.tid, "account.scim_user_deactivated")

        r = scim.patch(f"/Users/{uid}", {"op": "Replace", "path": "active", "value": "True"})  # Entra
        assert r.status_code == 200 and r.json()["active"] is True
        assert query_one("SELECT status FROM users WHERE id = %s", (uid,))["status"] == "active"
        assert _events(scim.company.tid, "account.scim_user_reactivated")

        r = scim.client.delete(f"{SCIM}/Users/{uid}", headers=scim.h)
        assert r.status_code == 204
        row = query_one("SELECT * FROM users WHERE id = %s", (uid,))
        assert row is not None, "deprovisioning must never delete the user"
        assert row["status"] == "inactive"
        assert scim.get(f"/Users/{uid}").json()["active"] is False

    def test_deactivation_ends_sessions_immediately(self, scim):
        from backend.auth.jwt_handler import create_access_token, create_refresh_token
        from backend.users import service as user_svc
        uid = scim.create("dee").json()["id"]
        access = create_access_token(uid, scim.company.tid, "viewer")
        raw_refresh, hashed = create_refresh_token()
        user_svc.add_refresh_token(scim.company.tid, uid, hashed)
        me = {"Authorization": f"Bearer {access}"}
        assert scim.client.get("/api/v1/users/me", headers=me).status_code == 200

        assert scim.client.delete(f"{SCIM}/Users/{uid}", headers=scim.h).status_code == 204
        assert scim.client.get("/api/v1/users/me", headers=me).status_code == 401
        assert scim.client.post("/api/v1/auth/refresh",
                                json={"refresh_token": raw_refresh}).status_code == 401
        assert query_one("SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = %s",
                         (uid,))["n"] == 0

    def test_deactivation_also_closes_the_whatsapp_bot(self, scim):
        from backend.whatsapp import identity
        uid = scim.create("wa").json()["id"]
        phone = f"+5067{uuid4().int % 10**7:07d}"
        execute("UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NOW() WHERE id = %s",
                (phone, uid))
        assert identity.resolve_sender(phone)["user_id"] == uid
        assert scim.client.delete(f"{SCIM}/Users/{uid}", headers=scim.h).status_code == 204
        assert identity.resolve_sender(phone) is None

    def test_put_replaces_profile_but_keeps_role_without_roles(self, scim):
        uid = scim.create("ed", roles=[{"value": "analyst"}]).json()["id"]
        r = scim.send("PUT", f"/Users/{uid}", {
            "userName": f"ed@{scim.company.domain}", "displayName": "Edward New",
            "externalId": "ext-ed", "active": True})
        assert r.status_code == 200, r.text
        row = query_one("SELECT * FROM users WHERE id = %s", (uid,))
        assert row["full_name"] == "Edward New" and row["role"] == "analyst"

    def test_if_match_mismatch(self, scim):
        uid = scim.create("fi").json()["id"]
        r = scim.client.request("PATCH", f"{SCIM}/Users/{uid}",
                                headers={**scim.h, "If-Match": 'W/"stale"'},
                                content=json.dumps({"schemas": [PATCH], "Operations": [
                                    {"op": "replace", "path": "active", "value": False}]}))
        assert r.status_code == 412
        assert query_one("SELECT status FROM users WHERE id = %s", (uid,))["status"] == "active"

    def test_role_via_patch_cuts_sessions(self, scim):
        uid = scim.create("gu").json()["id"]
        r = scim.patch(f"/Users/{uid}", {"op": "add", "path": "roles", "value": [{"value": "analyst"}]})
        assert r.status_code == 200
        row = query_one("SELECT role, sessions_invalid_before FROM users WHERE id = %s", (uid,))
        assert row["role"] == "analyst" and row["sessions_invalid_before"] is not None
        assert _events(scim.company.tid, "account.scim_role_changed")

    def test_suspended_by_an_admin_stays_suspended(self, scim):
        uid = scim.create("hu").json()["id"]
        execute("UPDATE users SET status = 'suspended' WHERE id = %s", (uid,))
        r = scim.patch(f"/Users/{uid}", {"op": "replace", "value": {"active": True}})
        assert r.status_code == 409
        assert r.json()["errorCode"] == "scim_user_suspended_by_admin"
        assert query_one("SELECT status FROM users WHERE id = %s", (uid,))["status"] == "suspended"

    def test_unchanged_sync_writes_nothing(self, scim):
        uid = scim.create("iv").json()["id"]
        before = query_one("SELECT updated_at FROM users WHERE id = %s", (uid,))["updated_at"]
        n = len(_scim_log(scim.company.tid))
        r = scim.patch(f"/Users/{uid}", {"op": "replace", "value": {"active": True}})
        assert r.status_code == 200
        assert query_one("SELECT updated_at FROM users WHERE id = %s", (uid,))["updated_at"] == before
        assert len(_scim_log(scim.company.tid)) == n


# ── Refusals ─────────────────────────────────────────────────────────────────

class TestRefusals:
    def test_duplicate_in_this_tenant(self, scim):
        scim.create("jo")
        r = scim.create("jo")
        assert r.status_code == 409 and r.json()["scimType"] == "uniqueness"
        assert r.json()["errorCode"] == "scim_user_exists"
        assert query_one("SELECT COUNT(*) AS n FROM users WHERE email = %s",
                         (f"jo@{scim.company.domain}",))["n"] == 1

    def test_email_of_another_tenant_is_never_touched(self, scim):
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        other = create_tenant(f"pytest-scim-x-{uuid4().hex[:6]}")
        try:
            email = f"ka@{scim.company.domain}"
            theirs = user_svc.create_user(other["id"], email, PASSWORD, role="analyst")
            r = scim.create("ka")
            assert r.status_code == 409
            assert r.json()["scimType"] == "uniqueness"
            assert r.json()["errorCode"] == "scim_email_in_other_tenant"
            row = _user(email)
            assert (row["id"], row["tenant_id"], row["role"]) == (theirs["id"], other["id"], "analyst")
            log = _scim_log(scim.company.tid)
            assert log[-1]["outcome"] == "error"
            assert log[-1]["error_code"] == "scim_email_in_other_tenant"
            assert _events(scim.company.tid, "account.scim_request_refused")
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_foreign_domain_is_refused(self, scim):
        r = scim.send("POST", "/Users", {"userName": f"x{uuid4().hex[:6]}@gmail.com"})
        assert r.status_code == 400
        assert r.json()["errorCode"] == "scim_email_domain_not_allowed"
        assert query_one("SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s AND email LIKE %s",
                         (scim.company.tid, "%@gmail.com"))["n"] == 0

    def test_user_ceiling(self, scim, monkeypatch):
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        n = query_one("SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s",
                      (scim.company.tid,))["n"]
        execute("UPDATE tenants SET quota = %s::jsonb WHERE id = %s",
                (json.dumps({"max_users": n}), scim.company.tid))
        r = scim.create("lu")
        assert r.status_code == 403, r.text
        assert r.json()["errorCode"] == "scim_user_limit_reached"
        assert r.json()["errorParams"]["max"] == n
        assert _user(f"lu@{scim.company.domain}") is None
        assert query_one("SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s",
                         (scim.company.tid,))["n"] == n

    def test_admins_are_read_only_without_opt_in(self, scim):
        admin_id = scim.company.admin["id"]
        r = scim.patch(f"/Users/{admin_id}", {"op": "replace", "path": "displayName", "value": "X"})
        assert r.status_code == 403
        assert r.json()["errorCode"] == "scim_admin_not_managed"
        r = scim.create("mo", roles=[{"value": "admin"}])
        assert r.status_code == 403
        assert _user(f"mo@{scim.company.domain}") is None
        r = scim.patch("/Groups/admin", {"op": "add", "path": "members",
                                         "value": [{"value": scim.create("ne").json()["id"]}]})
        assert r.status_code == 403
        assert _user(f"ne@{scim.company.domain}")["role"] == "viewer"
        assert query_one("SELECT full_name FROM users WHERE id = %s", (admin_id,))["full_name"] == "Boss"


class TestLastAdmin:
    def test_last_admin_cannot_be_deactivated(self, scim_admins):
        admin_id = scim_admins.company.admin["id"]
        r = scim_admins.patch(f"/Users/{admin_id}", {"op": "replace", "value": {"active": False}})
        assert r.status_code == 409 and r.json()["errorCode"] == "scim_last_admin"
        r = scim_admins.client.delete(f"{SCIM}/Users/{admin_id}", headers=scim_admins.h)
        assert r.status_code == 409
        assert query_one("SELECT status FROM users WHERE id = %s", (admin_id,))["status"] == "active"

    def test_last_admin_cannot_be_demoted(self, scim_admins):
        admin_id = scim_admins.company.admin["id"]
        r = scim_admins.patch(f"/Users/{admin_id}",
                              {"op": "replace", "path": "roles", "value": [{"value": "viewer"}]})
        assert r.status_code == 409
        r = scim_admins.send("PUT", "/Groups/admin", {"displayName": "StockAI Admin", "members": []})
        assert r.status_code == 409
        assert query_one("SELECT role FROM users WHERE id = %s", (admin_id,))["role"] == "admin"

    def test_with_a_second_admin_demotion_is_allowed(self, scim_admins):
        admin_id = scim_admins.company.admin["id"]
        second = scim_admins.create("oz", roles=[{"value": "admin"}])
        assert second.status_code == 201, second.text
        assert _user(f"oz@{scim_admins.company.domain}")["role"] == "admin"
        r = scim_admins.patch("/Groups/admin", {"op": "remove", "path": f'members[value eq "{admin_id}"]'})
        assert r.status_code == 204
        assert query_one("SELECT role FROM users WHERE id = %s", (admin_id,))["role"] == "viewer"

    def test_the_last_unscoped_admin_is_protected(self, scim_admins):
        """A second admin limited to some warehouses does not count: the company
        must keep somebody who sees every warehouse."""
        admin_id = scim_admins.company.admin["id"]
        scoped = scim_admins.create("pa", roles=[{"value": "admin"}]).json()["id"]
        execute("UPDATE users SET warehouse_scope = '[]'::jsonb WHERE id = %s", (scoped,))
        r = scim_admins.patch(f"/Users/{admin_id}", {"op": "replace", "value": {"active": False}})
        assert r.status_code == 409
        assert query_one("SELECT status FROM users WHERE id = %s", (admin_id,))["status"] == "active"


# ── Groups ───────────────────────────────────────────────────────────────────

class TestGroups:
    def test_lists_the_three_roles(self, scim):
        r = scim.get("/Groups")
        assert r.status_code == 200
        assert {g["id"] for g in r.json()["Resources"]} == {"admin", "analyst", "viewer"}
        r = scim.get("/Groups", filter='displayName eq "StockAI Analyst"')
        assert [g["id"] for g in r.json()["Resources"]] == ["analyst"]

    def test_membership_sets_the_role(self, scim):
        uid = scim.create("qu").json()["id"]
        r = scim.patch("/Groups/analyst", {"op": "Add", "path": "members", "value": [{"value": uid}]})
        assert r.status_code == 204
        assert query_one("SELECT role FROM users WHERE id = %s", (uid,))["role"] == "analyst"
        members = scim.get("/Groups/analyst").json()["members"]
        assert uid in {m["value"] for m in members}
        r = scim.patch("/Groups/analyst", {"op": "Remove", "path": "members", "value": [{"value": uid}]})
        assert r.status_code == 204
        assert query_one("SELECT role FROM users WHERE id = %s", (uid,))["role"] == "viewer"

    def test_unknown_member_changes_nothing(self, scim):
        uid = scim.create("ra").json()["id"]
        r = scim.patch("/Groups/analyst", {"op": "add", "path": "members",
                                           "value": [{"value": uid}, {"value": "usr_nope"}]})
        assert r.status_code == 400
        assert query_one("SELECT role FROM users WHERE id = %s", (uid,))["role"] == "viewer"

    def test_groups_cannot_be_created_renamed_or_deleted(self, scim):
        assert scim.send("POST", "/Groups", {"displayName": "Finance"}).status_code == 403
        assert scim.send("POST", "/Groups", {"displayName": "StockAI Admin"}).status_code == 409
        r = scim.patch("/Groups/viewer", {"op": "replace", "path": "displayName", "value": "Finance"})
        assert r.status_code == 400 and r.json()["scimType"] == "mutability"
        assert scim.client.delete(f"{SCIM}/Groups/viewer", headers=scim.h).status_code == 403


# ── Discovery ────────────────────────────────────────────────────────────────

class TestDiscovery:
    def test_documents(self, scim):
        spc = scim.get("/ServiceProviderConfig")
        assert spc.status_code == 200 and spc.json()["patch"]["supported"] is True
        assert spc.headers["content-type"].startswith(MEDIA)
        assert {r["id"] for r in scim.get("/ResourceTypes").json()["Resources"]} == {"User", "Group"}
        assert scim.get("/Schemas/urn:ietf:params:scim:schemas:core:2.0:User").status_code == 200

    def test_discovery_needs_the_token(self, scim):
        r = scim.client.get(f"{SCIM}/ServiceProviderConfig")
        assert r.status_code == 401
        assert r.headers.get("www-authenticate", "").startswith("Bearer")


# ── Export and erasure ───────────────────────────────────────────────────────

class TestTenantData:
    def test_export_has_no_hash_and_erase_removes_everything(self, scim):
        import io
        import zipfile
        from backend.tenants import data_export
        scim.create("su")
        blob = data_export.build_export_zip(scim.company.tid)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            exported = json.loads(zf.read("scim_tokens.json"))
            assert exported and "secret_hash" not in exported[0]
            assert json.loads(zf.read("scim_user_links.json"))
            assert json.loads(zf.read("scim_events.json"))
        data_export.delete_tenant(scim.company.tid)
        for table in ("scim_tokens", "scim_events", "scim_user_links"):
            assert query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id = %s",
                             (scim.company.tid,))["n"] == 0, table
