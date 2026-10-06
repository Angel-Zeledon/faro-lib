"""Custom roles are ENFORCED on the routes Python serves.

The role CRUD and assignment routes are Rust-only, so these tests write the
`custom_roles` row and `users.custom_role_id` directly (the way the Rust routes
do) and assert what the Python guards then do with real requests and direct
database reads.

What is pinned here:
  * a user with no custom role behaves exactly as before;
  * a custom role narrows the built-in role and never widens it;
  * a permission removed (or added) takes effect on the very next request, with
    the same token: there is no cache;
  * every failure mode is closed: a dangling role id, a foreign tenant's role,
    a permission name outside the catalogue, a database error, a mutating route
    nobody classified;
  * API keys keep their read / write scope, whatever the key creator's role;
  * the 12 legacy `user_permissions` stay informational (nothing reads them).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.auth import permissions as perms
from backend.db.connection import execute, query, query_one
from backend.errors import AppError


def make_role(tenant_id: str, permissions: list[str], name: str | None = None) -> str:
    role_id = f"role-{uuid4().hex[:10]}"
    execute(
        "INSERT INTO custom_roles (id, tenant_id, name, permissions) VALUES (%s, %s, %s, %s)",
        (role_id, tenant_id, name or f"role {role_id}", permissions),
    )
    return role_id


def assign(tenant_id: str, user_id: str, role_id: str | None) -> None:
    execute("UPDATE users SET custom_role_id = %s WHERE id = %s AND tenant_id = %s",
            (role_id, user_id, tenant_id))


def warehouse_names(tenant_id: str) -> set[str]:
    return {r["name"] for r in query("SELECT name FROM warehouses WHERE tenant_id = %s", (tenant_id,))}


def new_wh() -> dict:
    return {"name": f"RBAC-{uuid4().hex[:8]}"}


class TestNoCustomRoleBehavesAsBefore:
    def test_analyst_without_role_can_still_write(self, client, analyst_headers, analyst_user):
        body = new_wh()
        r = client.post("/api/v1/inventory/warehouses", json=body, headers=analyst_headers)
        assert r.status_code == 201, r.text
        assert body["name"] in warehouse_names(analyst_user["tenant"]["id"])

    def test_viewer_without_role_is_still_denied_by_the_builtin_role(self, client, viewer_headers, viewer_user):
        r = client.post("/api/v1/inventory/warehouses", json=new_wh(), headers=viewer_headers)
        assert r.status_code == 403
        assert r.json()["error_code"] == "role_not_permitted"
        assert warehouse_names(viewer_user["tenant"]["id"]) == set()

    def test_legacy_user_permissions_stay_informational(self, client, auth_headers, analyst_headers, analyst_user):
        """The 12 stored permissions are written and listed but nothing reads
        them to decide access: emptying them locks nobody out."""
        uid, tid = analyst_user["user"]["id"], analyst_user["tenant"]["id"]
        r = client.patch(f"/api/v1/users/{uid}/permissions", json={"permissions": []}, headers=auth_headers)
        assert r.status_code == 200
        assert query("SELECT 1 FROM user_permissions WHERE user_id = %s", (uid,)) == []
        w = client.post("/api/v1/inventory/warehouses", json=new_wh(), headers=analyst_headers)
        assert w.status_code == 201, w.text

    def test_legacy_endpoint_still_offers_exactly_the_twelve(self, client, auth_headers, analyst_user):
        uid = analyst_user["user"]["id"]
        r = client.get(f"/api/v1/users/{uid}/permissions", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()["data"]["all_permissions"]) == 12


class TestACustomRoleNarrows:
    def test_missing_permission_is_denied_with_a_structured_code(
        self, client, analyst_headers, analyst_user,
    ):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        assign(tid, uid, make_role(tid, ["view_inventory"]))
        body = new_wh()
        r = client.post("/api/v1/inventory/warehouses", json=body, headers=analyst_headers)
        assert r.status_code == 403
        assert r.json()["error_code"] == "permission_denied"
        assert r.json()["error_params"] == {"permission": "manage_settings"}
        assert body["name"] not in warehouse_names(tid), "a denied write must change nothing"
        # The permission it does hold still works.
        assert client.get("/api/v1/inventory/warehouses", headers=analyst_headers).status_code == 200

    def test_granted_permission_is_allowed(self, client, analyst_headers, analyst_user):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        assign(tid, uid, make_role(tid, ["manage_settings"]))
        body = new_wh()
        r = client.post("/api/v1/inventory/warehouses", json=body, headers=analyst_headers)
        assert r.status_code == 201, r.text
        assert body["name"] in warehouse_names(tid)

    def test_a_role_never_widens_the_builtin_role(self, client, viewer_headers, viewer_user):
        tid, uid = viewer_user["tenant"]["id"], viewer_user["user"]["id"]
        assign(tid, uid, make_role(tid, list(perms.PERMISSIONS)))
        r = client.post("/api/v1/inventory/warehouses", json=new_wh(), headers=viewer_headers)
        assert r.status_code == 403
        assert r.json()["error_code"] == "role_not_permitted"
        assert warehouse_names(tid) == set()

    def test_a_restricted_admin_cannot_use_admin_routes_outside_the_role(
        self, client, auth_headers, registered_user,
    ):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        assign(tid, uid, make_role(tid, ["view_users"]))
        invite = client.post("/api/v1/users", json={
            "email": f"x-{uuid4().hex[:6]}@example.com", "role": "viewer", "full_name": "X"},
            headers=auth_headers)
        assert invite.status_code == 403
        assert invite.json()["error_params"] == {"permission": "manage_users"}
        before = query_one("SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s", (tid,))["n"]
        assert before == 1
        assert client.get("/api/v1/users", headers=auth_headers).status_code == 200

    def test_open_routes_stay_open_for_a_user_with_an_empty_role(
        self, client, analyst_headers, analyst_user,
    ):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        assign(tid, uid, make_role(tid, []))
        assert client.get("/api/v1/users/me", headers=analyst_headers).status_code == 200
        assert client.get("/api/v1/inventory/warehouses", headers=analyst_headers).status_code == 403


class TestChangesApplyOnTheNextRequest:
    def test_a_permission_removed_takes_effect_immediately_with_the_same_token(
        self, client, analyst_headers, analyst_user,
    ):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        role = make_role(tid, ["manage_settings"])
        assign(tid, uid, role)
        first = new_wh()
        assert client.post("/api/v1/inventory/warehouses", json=first, headers=analyst_headers).status_code == 201

        execute("UPDATE custom_roles SET permissions = %s WHERE id = %s", ([], role))
        second = new_wh()
        r = client.post("/api/v1/inventory/warehouses", json=second, headers=analyst_headers)
        assert r.status_code == 403
        assert second["name"] not in warehouse_names(tid)

        execute("UPDATE custom_roles SET permissions = %s WHERE id = %s", (["manage_settings"], role))
        third = new_wh()
        assert client.post("/api/v1/inventory/warehouses", json=third, headers=analyst_headers).status_code == 201

    def test_unassigning_and_deleting_the_role_row_are_felt_at_once(
        self, client, analyst_headers, analyst_user,
    ):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        role = make_role(tid, [])
        assign(tid, uid, role)
        assert client.get("/api/v1/inventory/warehouses", headers=analyst_headers).status_code == 403
        # Deleting the row while it is still assigned leaves a dangling id:
        # that must DENY, not silently widen.
        execute("DELETE FROM custom_roles WHERE id = %s", (role,))
        assert client.get("/api/v1/inventory/warehouses", headers=analyst_headers).status_code == 403
        assign(tid, uid, None)
        assert client.get("/api/v1/inventory/warehouses", headers=analyst_headers).status_code == 200


class TestFailClosed:
    def test_dangling_role_id_denies(self, client, analyst_headers, analyst_user):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        assign(tid, uid, "role-does-not-exist")
        assert client.get("/api/v1/inventory/warehouses", headers=analyst_headers).status_code == 403
        assert client.post("/api/v1/inventory/warehouses", json=new_wh(), headers=analyst_headers).status_code == 403

    def test_another_tenants_role_grants_nothing(
        self, client, analyst_headers, analyst_user, make_tenant_user_headers,
    ):
        _, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        foreign = make_role(other_tid, list(perms.PERMISSIONS))
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        assign(tid, uid, foreign)
        r = client.get("/api/v1/inventory/warehouses", headers=analyst_headers)
        assert r.status_code == 403, "a role id from another tenant must read as no permissions"

    def test_a_permission_name_outside_the_catalogue_grants_nothing(
        self, client, analyst_headers, analyst_user,
    ):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        assign(tid, uid, make_role(tid, ["root", "*", "manage_everything"]))
        assert client.get("/api/v1/inventory/warehouses", headers=analyst_headers).status_code == 403
        assert client.post("/api/v1/inventory/warehouses", json=new_wh(), headers=analyst_headers).status_code == 403

    def test_an_unreadable_row_denies(self, client, analyst_headers, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("database went away")
        monkeypatch.setattr(perms, "effective_for", boom)
        r = client.get("/api/v1/inventory/warehouses", headers=analyst_headers)
        assert r.status_code == 403
        assert r.json()["error_code"] == "permission_check_failed"
        # A route that needs no permission does not even look.
        assert client.get("/api/v1/users/me", headers=analyst_headers).status_code == 200

    def test_a_mutating_route_nobody_classified_is_denied_for_a_restricted_user(self, analyst_user):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        scope = {"route": SimpleNamespace(path="/api/v1/brand-new-router/things"), "method": "POST"}
        # Unrestricted: passes (today's behaviour).
        perms.enforce(scope, tid, uid)
        assign(tid, uid, make_role(tid, list(perms.PERMISSIONS)))
        with pytest.raises(AppError) as e:
            perms.enforce(scope, tid, uid)
        assert e.value.code == "permission_denied"
        assert e.value.params == {"permission": "unclassified_route"}
        assert e.value.status_code == 403
        # An unclassified READ stays readable.
        perms.enforce({"route": SimpleNamespace(path="/api/v1/brand-new-router/things"), "method": "GET"}, tid, uid)

    def test_effective_set_ignores_names_outside_the_catalogue(self, analyst_user):
        tid, uid = analyst_user["tenant"]["id"], analyst_user["user"]["id"]
        assign(tid, uid, make_role(tid, ["view_users", "root"]))
        eff = perms.effective_for(tid, uid)
        assert eff.restricted and eff.permissions == frozenset({"view_users"})
        assert not eff.allows("root")


class TestApiKeysKeepTheirScope:
    def test_a_key_is_not_affected_by_its_creators_custom_role(
        self, client, auth_headers, registered_user,
    ):
        created = client.post("/api/v1/api-keys", json={"name": "rbac-key", "scope": "write"}, headers=auth_headers)
        assert created.status_code == 200, created.text
        key = created.json()["data"]["key"]
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        assign(tid, uid, make_role(tid, []))
        # The person is now locked to nothing...
        assert client.get("/api/v1/inventory/warehouses", headers=auth_headers).status_code == 403
        # ...the key is exactly what it was.
        r = client.get("/api/v1/inventory/warehouses", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text


class TestTenantErasureTakesTheRolesWithIt:
    def test_delete_tenant_removes_custom_roles(self, make_tenant_user_headers):
        from backend.tenants.data_export import delete_tenant
        _, tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        make_role(tid, ["view_users"])
        assert query_one("SELECT COUNT(*) AS n FROM custom_roles WHERE tenant_id = %s", (tid,))["n"] == 1
        delete_tenant(tid)
        assert query_one("SELECT COUNT(*) AS n FROM custom_roles WHERE tenant_id = %s", (tid,))["n"] == 0

    def test_the_export_carries_the_roles(self, make_tenant_user_headers):
        import io
        import zipfile
        from backend.tenants.data_export import build_export_zip
        _, tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        make_role(tid, ["view_users"], name="Export me")
        with zipfile.ZipFile(io.BytesIO(build_export_zip(tid))) as z:
            names = [n for n in z.namelist() if "custom_roles" in n]
            assert names, z.namelist()
            rows = json.loads(z.read(names[0]))
        assert [r["name"] for r in rows] == ["Export me"]
