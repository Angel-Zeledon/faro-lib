"""
Session flow integration tests.
Tests the complete forecast session lifecycle:
  create session -> list -> get -> patch -> delete
  upload dataset -> attach -> configure columns/features/models
  events CRUD
  AI suggested-questions endpoint
"""

import io
import csv
import pytest
from uuid import uuid4


# ── Fixture overrides: @example.com avoids email-validator rejection ───────────

@pytest.fixture
def registered_user(test_tenant):
    from backend.users import service as user_svc
    email = f"sess-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(test_tenant["id"], email, password, "admin", "Sess")
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def auth_headers(client, registered_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": registered_user["email"],
        "password": registered_user["password"],
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


# ── CSV helpers ────────────────────────────────────────────────────────────────

def _make_csv(n_skus: int = 2, n_days: int = 60) -> bytes:
    """Generate a minimal valid sales CSV with columns: date, sku, sales."""
    from datetime import date, timedelta
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "sku", "sales"])
    start = date(2024, 1, 1)
    for day in range(n_days):
        d = start + timedelta(days=day)
        for i in range(1, n_skus + 1):
            w.writerow([d.isoformat(), f"SKU-{i:03d}", (i * 10) + (day % 5)])
    return buf.getvalue().encode("utf-8")


def _make_header_only_csv() -> bytes:
    """CSV with only the header row and no data rows."""
    return b"date,sku,sales\n"


# ── Session CRUD ───────────────────────────────────────────────────────────────

class TestSessionCRUD:

    def test_create_session_returns_201_with_draft_status(self, client, auth_headers, registered_user):
        """POST /sessions creates a DRAFT session and returns 201."""
        from backend.db.connection import query_one
        name = f"Test Session {uuid4().hex[:6]}"
        r = client.post("/api/v1/sessions", headers=auth_headers, json={"name": name})
        assert r.status_code == 201
        d = r.json()["data"]
        assert d["status"] == "DRAFT"
        # service adds session_id alias for id
        assert "id" in d or "session_id" in d

        row = query_one(
            "SELECT name, status, tenant_id FROM sessions WHERE id = %s",
            (d["id"],),
        )
        assert row is not None, "Session row was not persisted to the DB"
        assert row["name"] == name
        assert row["status"] == "DRAFT"
        assert row["tenant_id"] == registered_user["tenant"]["id"]

    def test_create_session_with_description(self, client, auth_headers):
        """POST /sessions accepts optional description field."""
        r = client.post("/api/v1/sessions", headers=auth_headers, json={
            "name": "Desc Session",
            "description": "Integration test session",
        })
        assert r.status_code == 201

    def test_list_sessions_returns_200(self, client, auth_headers):
        """GET /sessions returns 200 with items list."""
        # Create at least one session so the list is non-empty
        client.post("/api/v1/sessions", headers=auth_headers,
                    json={"name": f"ListTest-{uuid4().hex[:6]}"})
        r = client.get("/api/v1/sessions", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()["data"]
        # Response is {"items": [...], "total": N, ...}
        items = body["items"] if "items" in body else body
        assert isinstance(items, list)
        assert len(items) >= 1

    def test_get_session_by_id(self, client, auth_headers):
        """GET /sessions/{id} returns the session for a valid id."""
        cr = client.post("/api/v1/sessions", headers=auth_headers,
                         json={"name": f"GetMe-{uuid4().hex[:6]}"})
        assert cr.status_code == 201
        sid = cr.json()["data"]["id"]

        r = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"]["id"] == sid

    def test_get_nonexistent_session_returns_404(self, client, auth_headers):
        """GET /sessions/{bad_id} returns 404."""
        r = client.get(f"/api/v1/sessions/{uuid4().hex}", headers=auth_headers)
        assert r.status_code == 404

    def test_patch_session_name(self, client, auth_headers):
        """PATCH /sessions/{id} updates name field."""
        from backend.db.connection import query_one
        cr = client.post("/api/v1/sessions", headers=auth_headers,
                         json={"name": "Old Name"})
        assert cr.status_code == 201
        sid = cr.json()["data"]["id"]
        new_name = f"New Name {uuid4().hex[:6]}"

        pr = client.patch(f"/api/v1/sessions/{sid}", headers=auth_headers,
                          json={"name": new_name})
        assert pr.status_code == 200
        assert pr.json()["data"]["name"] == new_name

        row = query_one("SELECT name FROM sessions WHERE id = %s", (sid,))
        assert row["name"] == new_name, "PATCH response echoed the new name but the DB row was not updated"

    def test_delete_draft_session_returns_204(self, client, auth_headers):
        """DELETE /sessions/{id} on a DRAFT session returns 204."""
        from backend.db.connection import query_one
        cr = client.post("/api/v1/sessions", headers=auth_headers,
                         json={"name": "To Delete"})
        assert cr.status_code == 201
        sid = cr.json()["data"]["id"]

        dr = client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert dr.status_code == 204

        row = query_one("SELECT id FROM sessions WHERE id = %s", (sid,))
        assert row is None, "204 was returned but the session row still exists in the DB"

    def test_delete_session_writes_activity_log_entry(self, client, auth_headers, registered_user):
        """
        Template for side-effect testing: session deletion is the first endpoint wired
        to backend.activity.service.log_action (api/v1/sessions.py::delete_session).
        Verifies the side effect directly via the activity service, not just the
        DELETE response — the same pattern future endpoints' tests should copy when
        they add their own log_action() call.
        """
        from backend.activity import service as act_svc

        name = f"Logged Delete {uuid4().hex[:6]}"
        cr = client.post("/api/v1/sessions", headers=auth_headers, json={"name": name})
        assert cr.status_code == 201
        sid = cr.json()["data"]["id"]

        dr = client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert dr.status_code == 204

        tenant_id = registered_user["tenant"]["id"]
        user_id = registered_user["user"]["id"]
        logs = act_svc.get_logs(tenant_id, user_id, action_filter="session.delete")
        matching = [l for l in logs["items"] if l["resource"] == sid]
        assert len(matching) == 1, "Expected exactly one session.delete log entry for this session"
        assert matching[0]["status"] == "success"
        assert matching[0]["context"]["name"] == name

    def test_delete_nonexistent_session_returns_404(self, client, auth_headers):
        """DELETE /sessions/{bad_id} returns 404."""
        r = client.delete(f"/api/v1/sessions/{uuid4().hex}", headers=auth_headers)
        assert r.status_code == 404

    def test_deleted_session_no_longer_accessible(self, client, auth_headers):
        """After DELETE, GET on the same session_id returns 404."""
        cr = client.post("/api/v1/sessions", headers=auth_headers,
                         json={"name": "Gone Session"})
        assert cr.status_code == 201
        sid = cr.json()["data"]["id"]

        client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        r = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert r.status_code == 404

    def test_unauthenticated_list_returns_401_or_403(self, client):
        """GET /sessions without token returns 401 or 403."""
        r = client.get("/api/v1/sessions")
        assert r.status_code in (401, 403)

    def test_list_sessions_pagination(self, client, auth_headers):
        """GET /sessions?skip=0&limit=1 respects pagination parameters."""
        # Create 2 sessions
        for i in range(2):
            client.post("/api/v1/sessions", headers=auth_headers,
                        json={"name": f"Paged-{i}-{uuid4().hex[:4]}"})
        r = client.get("/api/v1/sessions?skip=0&limit=1", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()["data"]
        items = body.get("items", body)
        assert len(items) <= 1


# ── Dataset upload & attachment ────────────────────────────────────────────────

class TestDatasetUpload:

    def test_upload_csv_returns_201(self, client, auth_headers):
        """POST /datasets with a valid CSV returns 201 and dataset metadata."""
        csv_bytes = _make_csv(n_skus=3, n_days=90)
        r = client.post("/api/v1/datasets", headers=auth_headers,
                        files={"file": ("sales.csv", csv_bytes, "text/csv")})
        assert r.status_code == 201
        d = r.json()["data"]
        assert "id" in d

    def test_upload_csv_and_attach_to_session(self, client, auth_headers):
        """Uploading a dataset and attaching it to a session transitions status to DATASET_LOADED."""
        csv_bytes = _make_csv(n_skus=2, n_days=60)
        ur = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("sales.csv", csv_bytes, "text/csv")})
        assert ur.status_code == 201
        dataset_id = ur.json()["data"]["id"]

        sr = client.post("/api/v1/sessions", headers=auth_headers,
                         json={"name": f"DatasetTest-{uuid4().hex[:6]}"})
        assert sr.status_code == 201
        sid = sr.json()["data"]["id"]

        ar = client.post(f"/api/v1/sessions/{sid}/dataset", headers=auth_headers,
                         json={"dataset_id": dataset_id})
        assert ar.status_code == 200
        assert ar.json()["data"]["status"] == "DATASET_LOADED"

    def test_attach_nonexistent_dataset_returns_404(self, client, auth_headers):
        """Attaching a non-existent dataset_id returns 404."""
        sr = client.post("/api/v1/sessions", headers=auth_headers,
                         json={"name": f"NoDS-{uuid4().hex[:6]}"})
        assert sr.status_code == 201
        sid = sr.json()["data"]["id"]

        r = client.post(f"/api/v1/sessions/{sid}/dataset", headers=auth_headers,
                        json={"dataset_id": uuid4().hex})
        assert r.status_code == 404

    def test_upload_empty_csv_does_not_500(self, client, auth_headers):
        """Uploading a header-only CSV does not cause a 500 error."""
        csv_bytes = _make_header_only_csv()
        r = client.post("/api/v1/datasets", headers=auth_headers,
                        files={"file": ("empty.csv", csv_bytes, "text/csv")})
        # Accepted (201) or validation error (400/422) — never a 500
        assert r.status_code in (201, 400, 422)

    def test_list_datasets_returns_200(self, client, auth_headers):
        """GET /datasets returns 200 with an items list."""
        # Upload one so there is at least one result
        csv_bytes = _make_csv(n_skus=1, n_days=30)
        client.post("/api/v1/datasets", headers=auth_headers,
                    files={"file": ("single.csv", csv_bytes, "text/csv")})
        r = client.get("/api/v1/datasets", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()["data"]
        items = body.get("items", body)
        assert isinstance(items, list)

    def test_get_dataset_by_id(self, client, auth_headers):
        """GET /datasets/{id} returns dataset metadata for a valid id."""
        csv_bytes = _make_csv(n_skus=1, n_days=30)
        ur = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("getme.csv", csv_bytes, "text/csv")})
        assert ur.status_code == 201
        ds_id = ur.json()["data"]["id"]

        r = client.get(f"/api/v1/datasets/{ds_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"]["id"] == ds_id

    def test_get_nonexistent_dataset_returns_404(self, client, auth_headers):
        """GET /datasets/{bad_id} returns 404."""
        r = client.get(f"/api/v1/datasets/{uuid4().hex}", headers=auth_headers)
        assert r.status_code == 404


# ── Session configuration steps ────────────────────────────────────────────────

class TestSessionConfiguration:

    def _create_session(self, client, auth_headers) -> str:
        r = client.post("/api/v1/sessions", headers=auth_headers,
                        json={"name": f"CfgTest-{uuid4().hex[:6]}"})
        assert r.status_code == 201
        return r.json()["data"]["id"]

    def _upload_and_attach(self, client, auth_headers, sid: str):
        csv_bytes = _make_csv(n_skus=2, n_days=60)
        ur = client.post("/api/v1/datasets", headers=auth_headers,
                         files={"file": ("cfg.csv", csv_bytes, "text/csv")})
        assert ur.status_code == 201
        ds_id = ur.json()["data"]["id"]
        ar = client.post(f"/api/v1/sessions/{sid}/dataset", headers=auth_headers,
                         json={"dataset_id": ds_id})
        assert ar.status_code == 200

    def test_configure_columns(self, client, auth_headers):
        """POST /sessions/{id}/configure/columns stores column config."""
        sid = self._create_session(client, auth_headers)
        self._upload_and_attach(client, auth_headers, sid)

        r = client.post(f"/api/v1/sessions/{sid}/configure/columns",
                        headers=auth_headers,
                        json={"date_column": "date", "target_column": "sales",
                              "sku_column": "sku"})
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["date_column"] == "date"
        assert d["target_column"] == "sales"

    def test_get_columns_config(self, client, auth_headers):
        """GET /sessions/{id}/configure/columns retrieves previously set config."""
        sid = self._create_session(client, auth_headers)
        self._upload_and_attach(client, auth_headers, sid)

        client.post(f"/api/v1/sessions/{sid}/configure/columns",
                    headers=auth_headers,
                    json={"date_column": "date", "target_column": "sales",
                          "sku_column": "sku"})

        r = client.get(f"/api/v1/sessions/{sid}/configure/columns",
                       headers=auth_headers)
        assert r.status_code == 200
        d = r.json()["data"]
        assert d is not None
        assert d["date_column"] == "date"

    def test_configure_features(self, client, auth_headers):
        """POST /sessions/{id}/configure/features stores feature config."""
        sid = self._create_session(client, auth_headers)
        self._upload_and_attach(client, auth_headers, sid)
        client.post(f"/api/v1/sessions/{sid}/configure/columns",
                    headers=auth_headers,
                    json={"date_column": "date", "target_column": "sales",
                          "sku_column": "sku"})

        r = client.post(f"/api/v1/sessions/{sid}/configure/features",
                        headers=auth_headers,
                        json={"lags": [1, 7], "rolling": [7], "diffs": [1],
                              "calendar": True})
        assert r.status_code == 200

    def test_configure_models(self, client, auth_headers):
        """POST /sessions/{id}/configure/models stores model selection."""
        sid = self._create_session(client, auth_headers)
        self._upload_and_attach(client, auth_headers, sid)
        client.post(f"/api/v1/sessions/{sid}/configure/columns",
                    headers=auth_headers,
                    json={"date_column": "date", "target_column": "sales",
                          "sku_column": "sku"})
        client.post(f"/api/v1/sessions/{sid}/configure/features",
                    headers=auth_headers,
                    json={"lags": [1, 7], "rolling": [7], "diffs": [1],
                          "calendar": True})

        r = client.post(f"/api/v1/sessions/{sid}/configure/models",
                        headers=auth_headers,
                        json={"mode": "selected", "selected_models": ["lightgbm"]})
        assert r.status_code == 200

    def test_get_config_summary(self, client, auth_headers):
        """GET /sessions/{id}/config-summary returns a dict with all config keys."""
        sid = self._create_session(client, auth_headers)
        r = client.get(f"/api/v1/sessions/{sid}/config-summary",
                       headers=auth_headers)
        assert r.status_code == 200
        d = r.json()["data"]
        assert isinstance(d, dict)
        # Keys defined in configuration.py get_config_summary
        assert "columns" in d or "models" in d or "features" in d


# ── Inventory Events ───────────────────────────────────────────────────────────

class TestInventoryEvents:

    def test_create_event_returns_201(self, client, auth_headers, registered_user):
        """POST /inventory/events creates a promotional event and returns 201."""
        from backend.db.connection import query_one
        name = f"Black Friday {uuid4().hex[:4]}"
        r = client.post("/api/v1/inventory/events", headers=auth_headers, json={
            "name": name,
            "start_date": "2026-11-27",
            "end_date": "2026-11-30",
            "multiplier": 2.5,
        })
        assert r.status_code == 201
        d = r.json()["data"]
        assert "id" in d

        row = query_one(
            "SELECT name, start_date, end_date, multiplier, tenant_id FROM inventory_events WHERE id = %s",
            (d["id"],),
        )
        assert row is not None, "Event row was not persisted to the DB"
        assert row["name"] == name
        assert float(row["multiplier"]) == 2.5
        assert row["tenant_id"] == registered_user["tenant"]["id"]

    def test_created_event_appears_in_list(self, client, auth_headers):
        """A created event is visible in GET /inventory/events."""
        cr = client.post("/api/v1/inventory/events", headers=auth_headers, json={
            "name": f"Cyber Monday {uuid4().hex[:4]}",
            "start_date": "2026-12-02",
            "end_date": "2026-12-02",
            "multiplier": 1.8,
        })
        assert cr.status_code == 201
        ev_id = cr.json()["data"]["id"]

        lr = client.get("/api/v1/inventory/events", headers=auth_headers)
        assert lr.status_code == 200
        ids = [e["id"] for e in lr.json()["data"]]
        assert ev_id in ids

    def test_create_event_requires_name(self, client, auth_headers):
        """POST /inventory/events without 'name' returns 422."""
        r = client.post("/api/v1/inventory/events", headers=auth_headers, json={
            "start_date": "2026-12-01",
            "end_date": "2026-12-05",
            "multiplier": 1.5,
        })
        assert r.status_code == 422

    def test_create_event_invalid_multiplier(self, client, auth_headers):
        """POST /inventory/events with multiplier outside [0.1, 10.0] returns 422."""
        r = client.post("/api/v1/inventory/events", headers=auth_headers, json={
            "name": "Bad Multiplier",
            "start_date": "2026-12-01",
            "end_date": "2026-12-05",
            "multiplier": 99.0,
        })
        assert r.status_code == 422

    def test_patch_event_multiplier(self, client, auth_headers):
        """PATCH /inventory/events/{id} updates multiplier."""
        cr = client.post("/api/v1/inventory/events", headers=auth_headers, json={
            "name": f"Patchable {uuid4().hex[:4]}",
            "start_date": "2026-12-01",
            "end_date": "2026-12-05",
            "multiplier": 1.5,
        })
        assert cr.status_code == 201
        ev_id = cr.json()["data"]["id"]

        pr = client.patch(f"/api/v1/inventory/events/{ev_id}", headers=auth_headers,
                          json={"multiplier": 2.0})
        assert pr.status_code == 200
        assert pr.json()["data"]["multiplier"] == 2.0

    def test_patch_event_name(self, client, auth_headers):
        """PATCH /inventory/events/{id} updates name."""
        cr = client.post("/api/v1/inventory/events", headers=auth_headers, json={
            "name": "Old Event Name",
            "start_date": "2026-10-01",
            "end_date": "2026-10-03",
            "multiplier": 1.2,
        })
        assert cr.status_code == 201
        ev_id = cr.json()["data"]["id"]
        new_name = f"Updated Event {uuid4().hex[:4]}"

        pr = client.patch(f"/api/v1/inventory/events/{ev_id}", headers=auth_headers,
                          json={"name": new_name})
        assert pr.status_code == 200
        assert pr.json()["data"]["name"] == new_name

    def test_patch_nonexistent_event_returns_404(self, client, auth_headers):
        """PATCH on a non-existent event_id returns 404."""
        r = client.patch(f"/api/v1/inventory/events/{uuid4().hex}",
                         headers=auth_headers, json={"multiplier": 1.0})
        assert r.status_code == 404

    def test_delete_event_returns_204(self, client, auth_headers):
        """DELETE /inventory/events/{id} returns 204."""
        cr = client.post("/api/v1/inventory/events", headers=auth_headers, json={
            "name": f"Deletable {uuid4().hex[:4]}",
            "start_date": "2026-10-01",
            "end_date": "2026-10-03",
            "multiplier": 1.2,
        })
        assert cr.status_code == 201
        ev_id = cr.json()["data"]["id"]

        dr = client.delete(f"/api/v1/inventory/events/{ev_id}", headers=auth_headers)
        assert dr.status_code == 204

    def test_deleted_event_not_in_list(self, client, auth_headers):
        """After DELETE, event no longer appears in GET /inventory/events."""
        cr = client.post("/api/v1/inventory/events", headers=auth_headers, json={
            "name": f"ToRemove {uuid4().hex[:4]}",
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
            "multiplier": 1.1,
        })
        assert cr.status_code == 201
        ev_id = cr.json()["data"]["id"]

        client.delete(f"/api/v1/inventory/events/{ev_id}", headers=auth_headers)

        lr = client.get("/api/v1/inventory/events", headers=auth_headers)
        ids = [e["id"] for e in lr.json()["data"]]
        assert ev_id not in ids

    def test_upcoming_events_returns_list(self, client, auth_headers):
        """GET /inventory/events/upcoming returns a list of upcoming events."""
        r = client.get("/api/v1/inventory/events/upcoming?days=365",
                       headers=auth_headers)
        assert r.status_code == 200
        assert isinstance(r.json()["data"], list)

    def test_upcoming_events_custom_days(self, client, auth_headers):
        """GET /inventory/events/upcoming?days=7 accepts a custom lookahead."""
        r = client.get("/api/v1/inventory/events/upcoming?days=7",
                       headers=auth_headers)
        assert r.status_code == 200
        assert isinstance(r.json()["data"], list)


# ── AI Insights ────────────────────────────────────────────────────────────────

class TestAIInsights:

    def test_suggested_questions_returns_list(self, client, auth_headers):
        """POST /ai/suggested-questions returns a list of question objects."""
        r = client.post("/api/v1/ai/suggested-questions", headers=auth_headers,
                        json={"profile": "distributor", "has_inventory": True})
        assert r.status_code == 200
        qs = r.json()["data"]
        assert isinstance(qs, list)
        assert len(qs) >= 1
        # Each item should have a 'text' field
        assert "text" in qs[0]

    def test_suggested_questions_without_inventory(self, client, auth_headers):
        """POST /ai/suggested-questions works with has_inventory=False."""
        r = client.post("/api/v1/ai/suggested-questions", headers=auth_headers,
                        json={"profile": "distributor", "has_inventory": False})
        assert r.status_code == 200
        assert isinstance(r.json()["data"], list)

    def test_suggested_questions_default_profile(self, client, auth_headers):
        """POST /ai/suggested-questions with no profile uses defaults."""
        r = client.post("/api/v1/ai/suggested-questions", headers=auth_headers, json={})
        assert r.status_code == 200
        assert isinstance(r.json()["data"], list)
