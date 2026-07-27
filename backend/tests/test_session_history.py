"""
Session history endpoint (GET /sessions/summary) + rename/delete permission pairs.

The summary endpoint powers the /sessions history page: one batched query that
joins datasets, session_configs and session_results, so every assertion here
checks the enriched fields against rows seeded directly in the DB.
"""

from uuid import uuid4

from backend.db.connection import execute, query_one
from backend.db import session_store
from backend.tests.fixtures.synthetic_data import seed_completed_session
from backend.utils.ids import generate_id


def _create_session(client, headers, name: str) -> str:
    r = client.post("/api/v1/sessions", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _attach_dataset_row(tenant_id: str, session_id: str, filename: str) -> str:
    """Insert a datasets row directly and point the session at it."""
    ds_id = generate_id("ds")
    execute(
        """INSERT INTO datasets (id, tenant_id, name, original_filename,
                                 file_type, file_path, size_bytes, uploaded_by)
           VALUES (%s, %s, %s, %s, 'csv', '/dev/null', 100, 'usr_test_seed')""",
        (ds_id, tenant_id, f"dataset-{filename}", filename),
    )
    execute(
        "UPDATE sessions SET dataset_id = %s WHERE id = %s AND tenant_id = %s",
        (ds_id, session_id, tenant_id),
    )
    return ds_id


def _summary_item(client, headers, session_id: str) -> dict:
    r = client.get("/api/v1/sessions/summary", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    matches = [i for i in body["items"] if i["id"] == session_id]
    assert len(matches) == 1, f"Session {session_id} not present exactly once in summary"
    return matches[0]


class TestSessionSummary:

    def test_summary_fields_match_seeded_rows(self, client, auth_headers, test_tenant):
        """A COMPLETED session with dataset + forecast_cfg + training result
        comes back with the correct filename, horizon, sku count, granularity."""
        tid = test_tenant["id"]
        name = f"History-{uuid4().hex[:6]}"
        sid = _create_session(client, auth_headers, name)
        _attach_dataset_row(tid, sid, "ventas_enero.csv")
        session_store.set_field(tid, sid, "forecast_cfg", {"horizon": 21})
        seed_completed_session(tid, sid, n_skus=4)  # metrics.n_skus = 4, status -> COMPLETED
        execute(
            "UPDATE sessions SET granularity = 'weekly' WHERE id = %s AND tenant_id = %s",
            (sid, tid),
        )

        item = _summary_item(client, auth_headers, sid)
        assert item["name"] == name
        assert item["status"] == "COMPLETED"
        assert item["dataset_filename"] == "ventas_enero.csv"
        assert item["dataset_name"] == "dataset-ventas_enero.csv"
        assert item["horizon"] == 21
        assert item["sku_count"] == 4
        assert item["granularity"] == "weekly"
        assert item["created_at"] is not None
        # frontend consumes session_id alias
        assert item["session_id"] == sid

    def test_summary_draft_session_has_null_enrichment(self, client, auth_headers):
        """A bare DRAFT session (no dataset, no configs, no result) must not
        error out — enrichment fields come back null."""
        sid = _create_session(client, auth_headers, f"Draft-{uuid4().hex[:6]}")
        item = _summary_item(client, auth_headers, sid)
        assert item["status"] == "DRAFT"
        assert item["dataset_filename"] is None
        assert item["horizon"] is None
        assert item["sku_count"] is None
        assert item["granularity"] is None

    def test_summary_sku_count_falls_back_to_distinct_metric_rows(
        self, client, auth_headers, test_tenant,
    ):
        """Real training results carry no metrics.n_skus — the summary counts
        distinct SKUs across the metrics rows instead (2 SKUs x 2 models -> 2)."""
        tid = test_tenant["id"]
        sid = _create_session(client, auth_headers, f"Fallback-{uuid4().hex[:6]}")
        session_store.set_training_result(tid, sid, {
            "metrics": {
                "rows": [
                    {"sku": "SKU_A", "model": "lightgbm", "wape": 0.1},
                    {"sku": "SKU_A", "model": "prophet", "wape": 0.2},
                    {"sku": "SKU_B", "model": "lightgbm", "wape": 0.3},
                    {"sku": "SKU_B", "model": "prophet", "wape": 0.4},
                ],
            },
        })
        item = _summary_item(client, auth_headers, sid)
        assert item["sku_count"] == 2

    def test_summary_horizon_falls_back_to_validation_cfg(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sid = _create_session(client, auth_headers, f"ValHorizon-{uuid4().hex[:6]}")
        session_store.set_field(tid, sid, "validation_cfg", {"horizon": 7})
        item = _summary_item(client, auth_headers, sid)
        assert item["horizon"] == 7

    def test_summary_ordered_newest_first(self, client, auth_headers):
        _create_session(client, auth_headers, f"Order-A-{uuid4().hex[:6]}")
        _create_session(client, auth_headers, f"Order-B-{uuid4().hex[:6]}")
        r = client.get("/api/v1/sessions/summary", headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        assert len(items) >= 2
        created = [i["created_at"] for i in items]
        assert created == sorted(created, reverse=True), (
            "Summary items are not ordered created_at DESC"
        )

    def test_summary_readable_by_viewer(self, client, viewer_headers, auth_headers):
        """Read-only endpoint: a viewer gets 200, not 403."""
        _create_session(client, auth_headers, f"ViewerRead-{uuid4().hex[:6]}")
        r = client.get("/api/v1/sessions/summary", headers=viewer_headers)
        assert r.status_code == 200
        assert len(r.json()["data"]["items"]) >= 1

    def test_summary_does_not_leak_other_tenants(self, client, auth_headers, make_tenant_user_headers):
        """Tenant isolation: another tenant's sessions never appear."""
        sid = _create_session(client, auth_headers, f"Mine-{uuid4().hex[:6]}")
        other_headers = make_tenant_user_headers(role="admin")
        r = client.get("/api/v1/sessions/summary", headers=other_headers)
        assert r.status_code == 200
        ids = [i["id"] for i in r.json()["data"]["items"]]
        assert sid not in ids


class TestSessionRenamePermissionPair:
    """PATCH /sessions/{id}: viewer denied (state unchanged), analyst succeeds.
    (The admin-success case already lives in test_sessions_flow.py.)"""

    def test_viewer_rename_denied_and_name_unchanged(
        self, client, auth_headers, viewer_headers,
    ):
        original = f"Keep-{uuid4().hex[:6]}"
        sid = _create_session(client, auth_headers, original)

        r = client.patch(f"/api/v1/sessions/{sid}", headers=viewer_headers,
                         json={"name": "Hacked Name"})
        assert r.status_code == 403

        row = query_one("SELECT name FROM sessions WHERE id = %s", (sid,))
        assert row["name"] == original, "Viewer got 403 but the DB name changed anyway"

    def test_analyst_rename_succeeds_and_persists(
        self, client, auth_headers, analyst_headers,
    ):
        sid = _create_session(client, auth_headers, "Before Rename")
        new_name = f"After-{uuid4().hex[:6]}"

        r = client.patch(f"/api/v1/sessions/{sid}", headers=analyst_headers,
                         json={"name": new_name})
        assert r.status_code == 200
        assert r.json()["data"]["name"] == new_name

        row = query_one("SELECT name FROM sessions WHERE id = %s", (sid,))
        assert row["name"] == new_name, "PATCH echoed the name but the DB row was not updated"


class TestSessionDeletePermissionPair:
    """DELETE /sessions/{id}: viewer denied (row survives), analyst succeeds."""

    def test_viewer_delete_denied_and_row_survives(
        self, client, auth_headers, viewer_headers,
    ):
        sid = _create_session(client, auth_headers, f"NoDelete-{uuid4().hex[:6]}")
        r = client.delete(f"/api/v1/sessions/{sid}", headers=viewer_headers)
        assert r.status_code == 403
        row = query_one("SELECT id FROM sessions WHERE id = %s", (sid,))
        assert row is not None, "Viewer got 403 but the session row was deleted"

    def test_analyst_delete_succeeds_and_row_gone(
        self, client, auth_headers, analyst_headers,
    ):
        sid = _create_session(client, auth_headers, f"AnalystDel-{uuid4().hex[:6]}")
        r = client.delete(f"/api/v1/sessions/{sid}", headers=analyst_headers)
        assert r.status_code == 204
        row = query_one("SELECT id FROM sessions WHERE id = %s", (sid,))
        assert row is None, "204 was returned but the session row still exists"
