"""
PHASE 2 — Endpoint integration tests.
Covers: auth flow, sessions CRUD, datasets CRUD, configuration wizard, users.
All tests use the shared `client` (session-scoped) and isolated `test_tenant`.
"""

import pytest
from uuid import uuid4


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestSignup:
    def test_signup_creates_user_and_tenant(self, client):
        email = f"new-{uuid4().hex[:8]}@example.com"
        resp = client.post("/api/v1/auth/signup", json={
            "email": email,
            "password": "StrongPass123!",
            "tenant_name": f"tenant-{uuid4().hex[:6]}",
            "full_name": "Test User",
        })
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["user"]["email"] == email
        assert "tenant" in data
        # Cleanup
        from backend.db.connection import execute
        execute("DELETE FROM tenants WHERE id = %s", (data["tenant"]["id"],))

    def test_signup_duplicate_email_returns_409(self, client, registered_user):
        resp = client.post("/api/v1/auth/signup", json={
            "email": registered_user["email"],
            "password": "StrongPass123!",
            "tenant_name": "other-tenant",
        })
        assert resp.status_code == 409

    def test_signup_weak_password_returns_400(self, client):
        resp = client.post("/api/v1/auth/signup", json={
            "email": f"weak-{uuid4().hex[:6]}@example.com",
            "password": "abc",
            "tenant_name": "weak-tenant",
        })
        assert resp.status_code == 400

    def test_signup_invalid_email_returns_422(self, client):
        resp = client.post("/api/v1/auth/signup", json={
            "email": "not-an-email",
            "password": "StrongPass123!",
            "tenant_name": "tenant",
        })
        assert resp.status_code == 422


class TestLogin:
    def test_login_returns_tokens(self, client, registered_user):
        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password_returns_401(self, client, registered_user):
        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    def test_login_unknown_email_returns_401(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "email": f"nobody-{uuid4().hex}@nowhere.com",
            "password": "AnyPass123!",
        })
        assert resp.status_code == 401

    def test_login_unverified_user_returns_403(self, client, test_tenant):
        from backend.users import service as user_svc
        email = f"unverified-{uuid4().hex[:8]}@example.com"
        user_svc.create_user(
            tenant_id=test_tenant["id"],
            email=email,
            password="TestPass123!",
            role="analyst",
        )
        resp = client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "TestPass123!",
        })
        assert resp.status_code == 403


class TestRefresh:
    def test_refresh_returns_new_access_token(self, client, registered_user):
        login = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        refresh_token = login.json()["data"]["refresh_token"]

        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert "access_token" in resp.json()["data"]

    def test_refresh_invalid_token_returns_401(self, client):
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "invalid_token"})
        assert resp.status_code == 401


class TestLogout:
    def test_logout_succeeds(self, client, auth_headers):
        resp = client.post("/api/v1/auth/logout", headers=auth_headers)
        assert resp.status_code == 200

    def test_token_revoked_after_logout(self, client, registered_user):
        login = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        client.post("/api/v1/auth/logout", headers=headers)
        resp = client.get("/api/v1/sessions", headers=headers)
        assert resp.status_code == 401


# ── Sessions CRUD ─────────────────────────────────────────────────────────────

class TestSessionsCRUD:
    def test_create_session_returns_201(self, client, auth_headers):
        resp = client.post("/api/v1/sessions", json={
            "name": f"s-{uuid4().hex[:6]}",
            "description": "test session",
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["status"] == "DRAFT"
        assert data["name"].startswith("s-")

    def test_create_session_with_tags(self, client, auth_headers):
        resp = client.post("/api/v1/sessions", json={
            "name": f"tagged-{uuid4().hex[:4]}",
            "tags": ["prod", "weekly"],
        }, headers=auth_headers)
        assert resp.status_code == 201

    def test_list_sessions_returns_created(self, client, auth_headers, test_session):
        resp = client.get("/api/v1/sessions", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()["data"]
        ids = [s["id"] for s in body["items"]]
        assert test_session["id"] in ids

    def test_get_session_returns_correct(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == sid

    def test_get_nonexistent_session_returns_404(self, client, auth_headers):
        resp = client.get("/api/v1/sessions/sess_nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_update_session_name(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.patch(
            f"/api/v1/sessions/{sid}",
            json={"name": "renamed-session"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "renamed-session"

    def test_delete_session(self, client, auth_headers):
        create = client.post("/api/v1/sessions", json={"name": "to-delete"}, headers=auth_headers)
        sid = create.json()["data"]["id"]
        resp = client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert resp.status_code == 204
        get_resp = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert get_resp.status_code == 404

    def test_viewer_cannot_create_session(self, client, viewer_headers):
        resp = client.post("/api/v1/sessions", json={"name": "viewer-session"}, headers=viewer_headers)
        assert resp.status_code == 403

    def test_pagination(self, client, auth_headers):
        for _ in range(3):
            client.post("/api/v1/sessions", json={"name": f"page-{uuid4().hex[:4]}"}, headers=auth_headers)
        resp = client.get("/api/v1/sessions?skip=0&limit=2", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert len(body["items"]) <= 2
        assert "total" in body

    def test_unauthenticated_request_returns_403_or_401(self, client):
        resp = client.get("/api/v1/sessions")
        assert resp.status_code in (401, 403)


# ── Datasets CRUD ─────────────────────────────────────────────────────────────

class TestDatasetsCRUD:
    def test_upload_csv_returns_201(self, client, auth_headers, csv_bytes):
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("sales.csv", csv_bytes, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["file_type"] == "csv"
        assert data["original_filename"] == "sales.csv"

    def test_list_datasets_includes_upload(self, client, auth_headers, uploaded_dataset):
        resp = client.get("/api/v1/datasets", headers=auth_headers)
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()["data"]["items"]]
        assert uploaded_dataset["id"] in ids

    def test_get_dataset_by_id(self, client, auth_headers, uploaded_dataset):
        did = uploaded_dataset["id"]
        resp = client.get(f"/api/v1/datasets/{did}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == did

    def test_get_nonexistent_dataset_returns_404(self, client, auth_headers):
        resp = client.get("/api/v1/datasets/ds_nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_upload_unsupported_extension_returns_400(self, client, auth_headers):
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("data.txt", b"col1,col2\n1,2", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_viewer_cannot_upload(self, client, viewer_headers, csv_bytes):
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("data.csv", csv_bytes, "text/csv")},
            headers=viewer_headers,
        )
        assert resp.status_code == 403


# ── Configuration wizard ──────────────────────────────────────────────────────

class TestConfigurationWizard:
    def test_attach_dataset_to_session(self, client, auth_headers, test_session, uploaded_dataset):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": uploaded_dataset["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["dataset_id"] == uploaded_dataset["id"]

    def test_attach_nonexistent_dataset_returns_404(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": "ds_nonexistent"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_configure_columns_saves_config(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/configure/columns", headers=auth_headers)
        assert resp.status_code == 200
        cfg = resp.json()["data"]
        assert cfg["date_column"] == "date"
        assert cfg["target_column"] == "sales"

    def test_configure_features_saves_config(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/configure/features", headers=auth_headers)
        assert resp.status_code == 200
        cfg = resp.json()["data"]
        assert "lags" in cfg

    def test_configure_models_saves_config(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/configure/models", headers=auth_headers)
        assert resp.status_code == 200
        cfg = resp.json()["data"]
        assert "selected_models" in cfg

    def test_session_status_advances_through_wizard(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
        status = resp.json()["data"]["status"]
        assert status in ("MODELS_CONFIGURED", "FEATURES_CONFIGURED", "COLUMNS_CONFIGURED",
                          "DATASET_LOADED", "INSPECTED")

    def test_config_summary_returns_all_steps(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/config-summary", headers=auth_headers)
        assert resp.status_code == 200
        cfg = resp.json()["data"]
        assert cfg["columns"] is not None
        assert cfg["features"] is not None
        assert cfg["models"] is not None

    def test_upload_and_attach_endpoint(self, client, auth_headers, test_session, csv_bytes):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/upload",
            files={"file": ("sales2.csv", csv_bytes, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["file_type"] == "csv"

    def test_business_config(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"service_level": 0.98, "lead_time_days": 5},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["service_level"] == 0.98

    def test_forecast_config(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/config/forecast",
            json={"horizon": 21, "quantiles": [0.1, 0.9]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["horizon"] == 21


# ── User management ───────────────────────────────────────────────────────────

class TestUsers:
    def test_list_users_returns_own_tenant_users(self, client, auth_headers, registered_user):
        resp = client.get("/api/v1/users", headers=auth_headers)
        assert resp.status_code == 200
        users = resp.json()["data"]["items"]
        emails = [u["email"] for u in users]
        assert registered_user["email"] in emails

    def test_hashed_password_not_exposed(self, client, auth_headers):
        resp = client.get("/api/v1/users", headers=auth_headers)
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        for u in items:
            assert "hashed_password" not in u

    def test_forgot_password_returns_ok_regardless_of_email(self, client):
        resp = client.post("/api/v1/auth/forgot-password", json={
            "email": f"nobody-{uuid4().hex}@nowhere.com"
        })
        assert resp.status_code == 200
        assert "message" in resp.json()["data"]
