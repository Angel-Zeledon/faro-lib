"""The audit trail: who did what to which object, when, and what changed.

State is asserted straight from `activity_logs`; the HTTP responses are only
used to drive the actions and to read the trail back.
"""
import csv
import io
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one


def _rows(tenant_id, action):
    return query(
        "SELECT user_id, resource, context, status, created_at FROM activity_logs "
        "WHERE tenant_id=%s AND action=%s ORDER BY created_at", (tenant_id, action))


def _one(tenant_id, action):
    rows = _rows(tenant_id, action)
    assert len(rows) == 1, f"{action}: expected one row, got {len(rows)}"
    return rows[0]


CSV_BYTES = b"sku,date,sales\na,2026-01-01,1\na,2026-01-02,2\n"


@pytest.mark.integration
class TestEveryCataloguedActionLeavesAnActorTargetAndDiff:
    def test_session_create_and_rename_record_the_target_and_the_before_after(
        self, client, auth_headers, registered_user,
    ):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        created = client.post("/api/v1/sessions", json={"name": "Q4 plan"}, headers=auth_headers)
        sid = created.json()["data"]["id"]

        row = _one(tid, "audit.session.created")
        assert row["user_id"] == uid and row["resource"] == sid
        assert row["context"]["target_type"] == "session"
        assert row["context"]["after"] == {"name": "Q4 plan"}
        assert row["created_at"] is not None

        client.patch(f"/api/v1/sessions/{sid}", json={"name": "Q4 plan v2"}, headers=auth_headers)
        upd = _one(tid, "audit.session.updated")
        assert upd["context"]["before"]["name"] == "Q4 plan"
        assert upd["context"]["after"]["name"] == "Q4 plan v2"
        assert upd["context"]["target_id"] == sid

    def test_dataset_upload_rename_and_delete(self, client, auth_headers, registered_user):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        r = client.post("/api/v1/datasets", files={"file": ("sales.csv", CSV_BYTES, "text/csv")},
                        headers=auth_headers)
        ds_id = r.json()["data"]["id"]
        created = _one(tid, "audit.dataset.created")
        assert created["user_id"] == uid and created["resource"] == ds_id
        assert created["context"]["target_label"] == "sales"

        client.patch(f"/api/v1/data-sources/{ds_id}", json={"name": "Sales 2026"}, headers=auth_headers)
        renamed = _one(tid, "audit.dataset.updated")
        assert renamed["context"]["before"]["name"] == "sales"
        assert renamed["context"]["after"]["name"] == "Sales 2026"

        client.delete(f"/api/v1/data-sources/{ds_id}", headers=auth_headers)
        deleted = _one(tid, "audit.dataset.deleted")
        assert deleted["context"]["before"]["name"] == "Sales 2026"
        assert query_one("SELECT id FROM datasets WHERE id=%s", (ds_id,)) is None

    def test_schedule_save_change_and_delete_keep_the_previous_cron(
        self, client, auth_headers, test_session, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        sid = test_session["id"]
        client.post(f"/api/v1/sessions/{sid}/schedule",
                    json={"cron_expr": "0 6 * * 1", "enabled": True}, headers=auth_headers)
        client.post(f"/api/v1/sessions/{sid}/schedule",
                    json={"cron_expr": "0 0 * * *", "enabled": False}, headers=auth_headers)
        client.delete(f"/api/v1/sessions/{sid}/schedule", headers=auth_headers)

        saved = _rows(tid, "audit.schedule.saved")
        assert len(saved) == 2
        assert saved[0]["context"]["before"] is None
        # Only the two fields this test is about: `retrain_mode` joined some of
        # the audited snapshots with the retrain-freshness work and not others.
        def core(snap):
            return {k: snap[k] for k in ("cron_expr", "enabled")}
        assert core(saved[0]["context"]["after"]) == {"cron_expr": "0 6 * * 1", "enabled": True}
        assert core(saved[1]["context"]["before"]) == {"cron_expr": "0 6 * * 1", "enabled": True}
        assert core(saved[1]["context"]["after"]) == {"cron_expr": "0 0 * * *", "enabled": False}
        gone = _one(tid, "audit.schedule.deleted")
        assert core(gone["context"]["before"]) == {"cron_expr": "0 0 * * *", "enabled": False}
        assert query_one("SELECT id FROM scheduled_jobs WHERE session_id=%s", (sid,)) is None

    def test_warehouse_create_and_demand_share_change(self, client, auth_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        client.post("/api/v1/inventory/warehouses", json={"name": "Norte"}, headers=auth_headers)
        assert _one(tid, "audit.warehouse.created")["context"]["target_id"] == "Norte"

        client.patch("/api/v1/inventory/warehouses/Norte", json={"demand_share": 40},
                     headers=auth_headers)
        upd = _one(tid, "audit.warehouse.updated")
        assert upd["context"]["before"] == {"demand_share": None}
        assert upd["context"]["after"] == {"demand_share": 40}

    def test_permissions_timezone_and_currency_changes(
        self, client, auth_headers, registered_user, analyst_user,
    ):
        tid = registered_user["tenant"]["id"]
        client.patch(f"/api/v1/users/{analyst_user['user']['id']}/permissions",
                     json={"permissions": ["view_forecasts"]}, headers=auth_headers)
        perm = _one(tid, "audit.user.permissions_changed")
        assert perm["context"]["target_id"] == analyst_user["user"]["id"]
        assert perm["context"]["after"]["permissions"] == ["view_forecasts"]

        client.patch("/api/v1/tenant/timezone", json={"timezone": "America/Costa_Rica"},
                     headers=auth_headers)
        client.patch("/api/v1/tenant/currency", json={"code": "USD"}, headers=auth_headers)
        configs = _rows(tid, "audit.config.changed")
        assert {c["context"]["target_id"] for c in configs} == {"timezone", "currency"}
        tz = [c for c in configs if c["context"]["target_id"] == "timezone"][0]
        assert tz["context"]["after"] == {"timezone": "America/Costa_Rica"}

    def test_exporting_the_tenant_data_is_recorded(self, client, auth_headers, registered_user):
        tid, uid = registered_user["tenant"]["id"], registered_user["user"]["id"]
        r = client.get("/api/v1/tenant/export", headers=auth_headers)
        assert r.status_code == 200
        row = _one(tid, "audit.export.tenant_data")
        assert row["user_id"] == uid and row["context"]["target_type"] == "tenant"

    def test_an_api_key_is_named_as_the_actor_not_the_person_who_minted_it(
        self, client, auth_headers, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        minted = client.post("/api/v1/api-keys", json={"name": "ERP", "scope": "write"},
                             headers=auth_headers).json()["data"]["key"]
        r = client.post("/api/v1/sessions", json={"name": "from the ERP"},
                        headers={"Authorization": f"Bearer {minted}"})
        assert r.status_code == 201
        row = _one(tid, "audit.session.created")
        assert row["user_id"].startswith("api_key:")
        assert row["user_id"] != registered_user["user"]["id"]
        assert row["context"]["actor_kind"] == "api_key"


@pytest.mark.integration
class TestOnlyChangesAreRecorded:
    def test_a_refused_write_leaves_no_row_and_changes_nothing(
        self, client, viewer_headers, auth_headers, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        sid = client.post("/api/v1/sessions", json={"name": "keep"},
                          headers=auth_headers).json()["data"]["id"]
        before = len(_rows(tid, "audit.session.updated"))

        r = client.patch(f"/api/v1/sessions/{sid}", json={"name": "hijacked"}, headers=viewer_headers)
        assert r.status_code == 403
        assert len(_rows(tid, "audit.session.updated")) == before
        assert query_one("SELECT name FROM sessions WHERE id=%s", (sid,))["name"] == "keep"

    def test_reading_is_not_recorded(self, client, auth_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        client.get("/api/v1/sessions", headers=auth_headers)
        client.get("/api/v1/sessions/summary", headers=auth_headers)
        assert query_one(
            "SELECT COUNT(*) AS n FROM activity_logs WHERE tenant_id=%s AND action LIKE 'audit.%%'",
            (tid,))["n"] == 0


@pytest.mark.integration
class TestTheAuditView:
    def _seed(self, client, headers):
        sid = client.post("/api/v1/sessions", json={"name": "one"}, headers=headers).json()["data"]["id"]
        client.patch(f"/api/v1/sessions/{sid}", json={"name": "two"}, headers=headers)
        client.post("/api/v1/inventory/warehouses", json={"name": "Sur"}, headers=headers)
        return sid

    def test_only_an_admin_may_read_it(
        self, client, auth_headers, analyst_headers, viewer_headers,
    ):
        for headers in (analyst_headers, viewer_headers):
            assert client.get("/api/v1/audit", headers=headers).status_code == 403
            assert client.get("/api/v1/audit/export", headers=headers).status_code == 403
            assert client.get("/api/v1/audit/filters", headers=headers).status_code == 403
        assert client.get("/api/v1/audit", headers=auth_headers).status_code == 200

    def test_it_is_filterable_paged_and_names_the_actor(
        self, client, auth_headers, registered_user,
    ):
        self._seed(client, auth_headers)
        everything = client.get("/api/v1/audit", headers=auth_headers).json()["data"]
        actions = [e["action"] for e in everything["items"]]
        assert {"session.created", "session.updated", "warehouse.created"} <= set(actions)
        first = everything["items"][0]
        assert first["actor"] == {"id": registered_user["user"]["id"], "kind": "user",
                                  "label": registered_user["email"]}
        assert first["at"]

        sessions_only = client.get("/api/v1/audit?target_type=session", headers=auth_headers).json()["data"]
        assert sessions_only["total"] == 2
        assert {e["target"]["type"] for e in sessions_only["items"]} == {"session"}

        one_action = client.get("/api/v1/audit?action=warehouse.created", headers=auth_headers).json()["data"]
        assert one_action["total"] == 1 and one_action["items"][0]["target"]["id"] == "Sur"

        page1 = client.get("/api/v1/audit?limit=1&offset=0", headers=auth_headers).json()["data"]
        page2 = client.get("/api/v1/audit?limit=1&offset=1", headers=auth_headers).json()["data"]
        assert len(page1["items"]) == len(page2["items"]) == 1
        assert page1["total"] == page2["total"] == everything["total"]
        assert page1["items"][0]["id"] != page2["items"][0]["id"]

        nobody = client.get("/api/v1/audit?actor=someone-else", headers=auth_headers).json()["data"]
        assert nobody["total"] == 0

    def test_a_date_range_excludes_what_falls_outside_it(self, client, auth_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        self._seed(client, auth_headers)
        execute("UPDATE activity_logs SET created_at = '2020-03-02T10:00:00Z' "
                "WHERE tenant_id=%s AND action='audit.warehouse.created'", (tid,))
        inside = client.get("/api/v1/audit?date_from=2020-03-02&date_to=2020-03-02",
                            headers=auth_headers).json()["data"]
        assert [e["action"] for e in inside["items"]] == ["warehouse.created"]
        outside = client.get("/api/v1/audit?date_from=2020-03-03", headers=auth_headers).json()["data"]
        assert "warehouse.created" not in [e["action"] for e in outside["items"]]

    def test_it_never_shows_another_tenants_trail(
        self, client, auth_headers, make_tenant_user_headers,
    ):
        self._seed(client, auth_headers)
        outsider = make_tenant_user_headers(role="admin")
        assert client.get("/api/v1/audit", headers=outsider).json()["data"]["total"] == 0

    def test_events_that_were_already_recorded_appear_in_the_same_shape(
        self, client, auth_headers, registered_user,
    ):
        sid = client.post("/api/v1/sessions", json={"name": "doomed"},
                          headers=auth_headers).json()["data"]["id"]
        assert client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers).status_code == 204
        client.post("/api/v1/api-keys", json={"name": "ERP"}, headers=auth_headers)

        items = client.get("/api/v1/audit", headers=auth_headers).json()["data"]["items"]
        # Sessions are permanent: DELETE archives, and that is what the trail records.
        deleted = [e for e in items if e["action"] == "session.archived"]
        assert len(deleted) == 1
        assert deleted[0]["target"] == {"type": "session", "id": sid, "label": "doomed"}
        assert deleted[0]["before"]["name"] == "doomed"
        key = [e for e in items if e["action"] == "api_key.created"]
        assert key and key[0]["target"]["label"] == "ERP"

    def test_the_csv_export_honours_the_filter_and_neutralises_formulas(
        self, client, auth_headers, registered_user,
    ):
        client.post("/api/v1/sessions", json={"name": "=HYPERLINK(\"x\")"}, headers=auth_headers)
        client.post("/api/v1/inventory/warehouses", json={"name": "Sur"}, headers=auth_headers)

        r = client.get("/api/v1/audit/export?target_type=session", headers=auth_headers)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(r.text)))
        assert len(rows) == 1
        assert rows[0]["action"] == "session.created"
        assert rows[0]["actor_label"] == registered_user["email"]
        assert rows[0]["target_label"].startswith("'="), "a formula must not survive into the cell"
        assert "warehouse" not in r.text


def test_every_catalogued_route_is_a_real_route(client):
    """The catalogue is a list of strings; a typo or a renamed route would
    silently stop auditing it."""
    from backend.audit.catalog import ROUTES, RUST_ONLY_ROUTES
    from backend.main import app

    real = {(m, r.path.replace("/api/v1", "", 1))
            for r in app.routes for m in (getattr(r, "methods", None) or ())}
    missing = sorted(set(ROUTES) - real - RUST_ONLY_ROUTES)
    assert not missing, f"audit catalogue names routes that do not exist: {missing}"
