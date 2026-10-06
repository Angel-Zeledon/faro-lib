"""Organization hierarchy: what PYTHON must honour.

The links, grants and consolidated reads are served by Rust
(`backend-rs/src/routes/org*.rs`, exercised end to end by
`tests/contract/contract_test.py::run_org`). Python owns the schema and the
paths that still run here: whole-tenant erasure, the data export, deactivating a
person, the audit/event vocabulary and the public surface. These tests assert
state with direct database queries.
"""
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest

from backend.db.connection import execute, query, query_one
from backend.tests.test_enterprise_sso import company, configured, idp, sso_on  # noqa: F401 - fixtures
from backend.tests.test_scim_provisioning import PATCH, SCIM, Idp, _mint  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


# ── fixtures ─────────────────────────────────────────────────────────────────

def _tenant(name_prefix: str) -> dict:
    from backend.tenants.service import create_tenant
    return create_tenant(f"{name_prefix}-{uuid4().hex[:8]}")


def _user(tenant_id: str, role: str = "analyst") -> dict:
    from backend.users import service as user_svc
    email = f"{role}-{uuid4().hex[:8]}@pytest.example.com"
    u = user_svc.create_user(tenant_id=tenant_id, email=email, password="TestPass123!", role=role,
                             full_name="Org Test")
    user_svc.mark_verified(tenant_id, u["id"])
    return u


def _link(parent: str, child: str | None, *, status: str = "active", label: str = "Norte") -> str:
    link_id = f"olnk_{uuid4().hex[:12]}"
    if status == "pending":
        execute(
            """INSERT INTO org_links (id, parent_tenant_id, label, status, code_hash, code_expires_at, created_by)
               VALUES (%s, %s, %s, 'pending', %s, NOW() + INTERVAL '1 day', 'u')""",
            (link_id, parent, label, uuid4().hex + uuid4().hex),
        )
    else:
        execute(
            """INSERT INTO org_links (id, parent_tenant_id, child_tenant_id, label, status, created_by,
                                      accepted_by, accepted_at)
               VALUES (%s, %s, %s, %s, 'active', 'u', 'c', NOW())""",
            (link_id, parent, child, label),
        )
    return link_id


def _grant(link_id: str, user_id: str) -> None:
    execute("INSERT INTO org_link_grants (link_id, user_id, granted_by) VALUES (%s, %s, 'admin')",
            (link_id, user_id))


@pytest.fixture
def org():
    """A holding with an analyst, one subsidiary and an active link granted to the analyst."""
    parent, child = _tenant("hold"), _tenant("sub")
    analyst = _user(parent["id"])
    link = _link(parent["id"], child["id"])
    _grant(link, analyst["id"])
    yield {"parent": parent["id"], "child": child["id"], "analyst": analyst["id"], "link": link}
    for t in (parent["id"], child["id"]):
        execute("DELETE FROM tenants WHERE id = %s", (t,))


def _links_naming(tenant_id: str) -> int:
    return query_one(
        "SELECT COUNT(*) AS n FROM org_links WHERE parent_tenant_id = %s OR child_tenant_id = %s",
        (tenant_id, tenant_id),
    )["n"]


def _feed(tenant_id: str, action: str) -> list[dict]:
    return query("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = %s", (tenant_id, action))


# ── the schema refuses what the model forbids ────────────────────────────────

class TestSchemaRules:

    def test_a_tenant_cannot_link_to_itself(self, org):
        with pytest.raises(psycopg2.errors.CheckViolation):
            _link(org["parent"], org["parent"])

    def test_a_subsidiary_has_at_most_one_live_parent(self, org):
        other_holding = _tenant("hold2")
        try:
            with pytest.raises(psycopg2.errors.UniqueViolation):
                _link(other_holding["id"], org["child"])
            assert _links_naming(other_holding["id"]) == 0
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other_holding["id"],))

    def test_a_revoked_link_frees_the_subsidiary_for_another_holding(self, org):
        other_holding = _tenant("hold3")
        try:
            execute(
                "UPDATE org_links SET status = 'revoked', revoked_at = NOW(), revoked_side = 'child' WHERE id = %s",
                (org["link"],))
            _link(other_holding["id"], org["child"])
            assert _links_naming(org["child"]) == 2
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other_holding["id"],))

    def test_active_needs_a_child_and_no_code_and_pending_needs_a_code(self, org):
        parent = org["parent"]
        with pytest.raises(psycopg2.errors.CheckViolation):
            execute("INSERT INTO org_links (id, parent_tenant_id, label, status, created_by) "
                    "VALUES (%s, %s, 'x', 'active', 'u')", (f"olnk_{uuid4().hex[:12]}", parent))
        with pytest.raises(psycopg2.errors.CheckViolation):
            execute("INSERT INTO org_links (id, parent_tenant_id, label, status, created_by) "
                    "VALUES (%s, %s, 'x', 'pending', 'u')", (f"olnk_{uuid4().hex[:12]}", parent))
        with pytest.raises(psycopg2.errors.CheckViolation):
            execute("INSERT INTO org_links (id, parent_tenant_id, child_tenant_id, label, status, code_hash, created_by) "
                    "VALUES (%s, %s, %s, 'x', 'active', 'abc', 'u')",
                    (f"olnk_{uuid4().hex[:12]}", parent, org["child"]))

    def test_two_links_cannot_share_a_code(self, org):
        h = uuid4().hex + uuid4().hex
        for _ in range(1):
            execute(
                """INSERT INTO org_links (id, parent_tenant_id, label, status, code_hash, code_expires_at, created_by)
                   VALUES (%s, %s, 'a', 'pending', %s, NOW() + INTERVAL '1 day', 'u')""",
                (f"olnk_{uuid4().hex[:12]}", org["parent"], h))
        with pytest.raises(psycopg2.errors.UniqueViolation):
            execute(
                """INSERT INTO org_links (id, parent_tenant_id, label, status, code_hash, code_expires_at, created_by)
                   VALUES (%s, %s, 'b', 'pending', %s, NOW() + INTERVAL '1 day', 'u')""",
                (f"olnk_{uuid4().hex[:12]}", org["parent"], h))

    def test_grants_follow_their_link_and_their_person(self, org):
        second = _user(org["parent"], "viewer")
        _grant(org["link"], second["id"])
        from backend.users import service as user_svc
        user_svc.delete_user(org["parent"], second["id"])
        left = query("SELECT user_id FROM org_link_grants WHERE link_id = %s", (org["link"],))
        assert [r["user_id"] for r in left] == [org["analyst"]]
        execute("DELETE FROM org_links WHERE id = %s", (org["link"],))
        assert query_one("SELECT COUNT(*) AS n FROM org_link_grants WHERE link_id = %s", (org["link"],))["n"] == 0


# ── whole-tenant erasure ─────────────────────────────────────────────────────

class TestErasure:

    def test_every_table_that_names_a_tenant_in_a_link_column_is_known_to_erasure(self):
        """`_DELETE_ORDER` is keyed on `tenant_id`; a link names a tenant in two
        OTHER columns. A new table with such a column must be handled by
        `organizations.service.end_links_for_erasure` or it survives erasure."""
        rows = query(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name IN ('parent_tenant_id', 'child_tenant_id')"
        )
        assert {r["table_name"] for r in rows} == {"org_links"}, rows

    def test_erasing_a_subsidiary_tells_the_holding_and_removes_every_trace(self, org):
        from backend.tenants.data_export import delete_tenant
        delete_tenant(org["child"])

        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (org["child"],)) is None
        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (org["parent"],)) is not None
        assert _links_naming(org["child"]) == 0
        assert query_one("SELECT COUNT(*) AS n FROM org_link_grants WHERE link_id = %s", (org["link"],))["n"] == 0
        # The holding's analyst no longer reaches anything through the link.
        reach = query(
            """SELECT 1 FROM org_link_grants g JOIN org_links l ON l.id = g.link_id
                WHERE g.user_id = %s AND l.status = 'active'""", (org["analyst"],))
        assert reach == []
        # ...and the holding is told why the subsidiary vanished, in its own feed.
        rows = _feed(org["parent"], "org.link_revoked")
        assert len(rows) == 1
        ctx = rows[0]["context"]
        assert ctx["reason"] == "org_tenant_erased"
        assert ctx["severity"] == "warning" and ctx["kind"] == "account"
        assert ctx["label"] == "Norte"
        assert rows[0]["resource"] == org["link"]

    def test_erasing_a_holding_tells_the_subsidiary_without_naming_the_holding(self, org):
        from backend.tenants.data_export import delete_tenant
        delete_tenant(org["parent"])

        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (org["child"],)) is not None
        assert _links_naming(org["parent"]) == 0 and _links_naming(org["child"]) == 0
        rows = _feed(org["child"], "org.link_revoked")
        assert len(rows) == 1
        ctx = rows[0]["context"]
        assert ctx["reason"] == "org_tenant_erased"
        assert "label" not in ctx, "the subsidiary must not learn the holding's name for it"
        assert org["parent"] not in json.dumps(ctx)

    def test_pending_and_revoked_links_go_with_the_tenant_and_tell_nobody(self):
        from backend.tenants.data_export import delete_tenant
        holding, other = _tenant("hold"), _tenant("sub")
        try:
            _link(holding["id"], None, status="pending")
            revoked = _link(holding["id"], other["id"])
            execute("UPDATE org_links SET status = 'revoked', revoked_at = NOW(), revoked_side = 'parent' "
                    "WHERE id = %s", (revoked,))
            delete_tenant(holding["id"])
            assert _links_naming(holding["id"]) == 0
            # Nothing was live, so the other tenant was not bothered.
            assert _feed(other["id"], "org.link_revoked") == []
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_erasing_an_unrelated_tenant_changes_nothing_here(self, org):
        from backend.tenants.data_export import delete_tenant
        stranger = _tenant("stranger")
        delete_tenant(stranger["id"])
        assert _links_naming(org["parent"]) == 1
        assert query_one("SELECT COUNT(*) AS n FROM org_link_grants WHERE link_id = %s", (org["link"],))["n"] == 1
        assert _feed(org["parent"], "org.link_revoked") == []


# ── the data export ──────────────────────────────────────────────────────────

class TestExport:

    def test_holding_export_has_links_and_grants_and_never_the_code_hash(self, org):
        pending = _link(org["parent"], None, status="pending", label="Sur")
        from backend.tenants.data_export import build_export_zip
        zf = zipfile.ZipFile(io.BytesIO(build_export_zip(org["parent"])))
        links = json.loads(zf.read("org_links_as_parent.json"))
        assert {l["id"] for l in links} == {org["link"], pending}
        assert all("code_hash" not in l for l in links)
        grants = json.loads(zf.read("org_link_grants.json"))
        assert [(g["link_id"], g["user_id"]) for g in grants] == [(org["link"], org["analyst"])]
        assert b"code_hash" not in zf.read("org_links_as_parent.json")
        assert json.loads(zf.read("manifest.json"))["tables"]["org_links_as_parent"] == 2

    def test_subsidiary_export_never_names_the_holding(self, org):
        from backend.tenants.data_export import build_export_zip
        zf = zipfile.ZipFile(io.BytesIO(build_export_zip(org["child"])))
        everything = b"".join(zf.read(n) for n in zf.namelist())
        mine = json.loads(zf.read("org_links_as_child.json"))
        assert [l["id"] for l in mine] == [org["link"]]
        assert set(mine[0]) == {"id", "status", "accepted_by", "accepted_at", "revoked_at", "revoked_side"}
        assert org["parent"].encode() not in everything
        assert b"Norte" not in everything, "the holding's label for the subsidiary stays with the holding"
        assert zf.read("org_link_grants.json") == b"[]"
        assert org["analyst"].encode() not in everything

    def test_a_stranger_export_carries_nothing_from_the_hierarchy(self, org):
        stranger = _tenant("stranger")
        try:
            from backend.tenants.data_export import build_export_zip
            zf = zipfile.ZipFile(io.BytesIO(build_export_zip(stranger["id"])))
            for name in ("org_links_as_parent.json", "org_links_as_child.json", "org_link_grants.json"):
                assert zf.read(name) == b"[]", name
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (stranger["id"],))


# ── a person who loses their account loses their grants ─────────────────────

class TestDeactivation:

    @pytest.mark.parametrize("new_status", ["inactive", "suspended"])
    def test_deactivating_drops_the_grant_and_reactivating_does_not_bring_it_back(self, org, new_status):
        from backend.users import service as user_svc
        user_svc.update_status(org["parent"], org["analyst"], new_status)
        assert query("SELECT 1 FROM org_link_grants WHERE user_id = %s", (org["analyst"],)) == []
        user_svc.update_status(org["parent"], org["analyst"], "active")
        assert query("SELECT 1 FROM org_link_grants WHERE user_id = %s", (org["analyst"],)) == []

    def test_setting_the_status_to_active_keeps_the_grant(self, org):
        from backend.users import service as user_svc
        user_svc.update_status(org["parent"], org["analyst"], "active")
        assert len(query("SELECT 1 FROM org_link_grants WHERE user_id = %s", (org["analyst"],))) == 1

    def test_only_the_deactivated_persons_grants_go(self, org):
        other = _user(org["parent"], "viewer")
        _grant(org["link"], other["id"])
        from backend.users import service as user_svc
        user_svc.update_status(org["parent"], org["analyst"], "inactive")
        left = query("SELECT user_id FROM org_link_grants WHERE link_id = %s", (org["link"],))
        assert [r["user_id"] for r in left] == [other["id"]]

    def test_the_admin_endpoint_path_drops_it_too(self, client, org, auth_headers, registered_user):
        # `registered_user` is an admin of its own tenant; use it to suspend an
        # analyst of THAT tenant who holds a grant there.
        tenant_id = registered_user["tenant"]["id"]
        person = _user(tenant_id)
        sub = _tenant("sub")
        try:
            link = _link(tenant_id, sub["id"])
            _grant(link, person["id"])
            r = client.patch(f"/api/v1/users/{person['id']}/status", headers=auth_headers,
                             json={"status": "suspended"})
            assert r.status_code == 200, r.text
            assert query("SELECT 1 FROM org_link_grants WHERE user_id = %s", (person["id"],)) == []
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (sub["id"],))


class TestDeprovisioning:
    """The identity provider deactivates a person: the SCIM path drops the grant
    inside the same transaction, and a later reactivation does not restore it."""

    def test_scim_deactivation_drops_the_grant_and_reactivation_does_not_restore_it(self, configured):
        scim = Idp(configured, _mint(configured))
        uid = scim.create("holder").json()["id"]
        sub = _tenant("sub")
        try:
            link = _link(configured.tid, sub["id"])
            _grant(link, uid)
            other = scim.create("keeper").json()["id"]
            _grant(link, other)

            r = scim.patch(f"/Users/{uid}", {"op": "replace", "value": {"active": False}})
            assert r.status_code == 200, r.text
            left = query("SELECT user_id FROM org_link_grants WHERE link_id = %s", (link,))
            assert [x["user_id"] for x in left] == [other], "only the deprovisioned person loses access"

            r = scim.patch(f"/Users/{uid}", {"op": "Replace", "path": "active", "value": "True"})
            assert r.status_code == 200, r.text
            left = query("SELECT user_id FROM org_link_grants WHERE link_id = %s", (link,))
            assert [x["user_id"] for x in left] == [other], "reactivating must not bring the access back"
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (sub["id"],))


# ── nothing here reaches a key or MCP ────────────────────────────────────────

class TestPublicSurface:

    def test_no_python_route_serves_the_organization_paths(self, client):
        from backend.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert not [p for p in paths if "/org/" in p or p.endswith("/org")], \
            "the organization routes are Rust-only; a Python twin would be an unreviewed second implementation"

    def test_organization_is_not_an_exposed_tag(self):
        from backend.api.public_surface import EXPOSED_TAGS
        assert not [t for t in EXPOSED_TAGS if "org" in str(t).lower()]

    def test_mcp_catalogue_never_mentions_the_organization_routes(self):
        mcp_dir = ROOT / "backend" / "mcp"
        text = "".join(p.read_text(encoding="utf-8") for p in mcp_dir.glob("*.py"))
        assert "/org/" not in text and "consolidated" not in text.lower()


# ── vocabulary ───────────────────────────────────────────────────────────────

class TestVocabulary:
    ACTIONS = ("org.link_created", "org.link_accepted", "org.link_revoked", "org.grant_added", "org.grant_removed")

    def test_every_event_is_declared_and_audited_as_an_organization_act(self):
        from backend.activity.events import EVENTS, REASONS
        from backend.audit.catalog import LEGACY, TARGET_TYPES
        for action in self.ACTIONS:
            assert action in EVENTS
            assert LEGACY[action][0] == "organization"
        assert "organization" in TARGET_TYPES
        assert EVENTS["org.link_revoked"].severity == "warning"
        for reason in ("org_revoked_by_parent", "org_revoked_by_child", "org_tenant_erased"):
            assert reason in REASONS

    def test_the_erasure_event_has_the_shape_record_event_stores(self, org):
        """`end_links_for_erasure` writes the row by hand (inside the erasure
        transaction); it must match what `record_event` would have stored."""
        from backend.activity.events import EVENTS
        from backend.tenants.data_export import delete_tenant
        delete_tenant(org["child"])
        ctx = _feed(org["parent"], "org.link_revoked")[0]["context"]
        spec = EVENTS["org.link_revoked"]
        assert set(ctx) <= set(spec.detail_keys) | {"severity", "kind", "reason", "reason_params"}
        assert (ctx["severity"], ctx["kind"]) == (spec.severity, spec.kind)

    def test_copy_exists_in_both_languages(self):
        text = (ROOT / "Frontend" / "src" / "i18n" / "translations.ts").read_text(encoding="utf-8")
        for key in [f"events.action.{a}" for a in self.ACTIONS] + [
            "events.reason.org_revoked_by_parent", "events.reason.org_revoked_by_child",
            "events.reason.org_tenant_erased", "audit.target.organization",
        ] + [f"audit.action.organization.{a.split('.', 1)[1]}" for a in self.ACTIONS]:
            assert text.count(f"'{key}'") == 2, f"{key} must be in both language blocks"
        for code in ("org_not_entitled", "org_tenant_not_found", "org_link_not_found", "org_link_code_invalid",
                     "org_link_not_active", "org_member_not_found", "org_nesting_not_allowed",
                     "org_link_not_allowed", "org_link_self", "org_already_linked",
                     "org_too_many_pending_links", "org_too_many_subsidiaries"):
            assert text.count(f"'errors.{code}'") == 2, f"errors.{code} must be in both language blocks"
