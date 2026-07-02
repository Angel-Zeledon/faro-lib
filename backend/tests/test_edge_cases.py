"""
PHASE 2 — Edge cases, error handling, security, and boundary tests.
Covers: invalid inputs, authorization boundaries, quota enforcement,
token lifecycle, malformed requests, and schema validation.
"""

import pytest
from uuid import uuid4


# ── Authentication edge cases ────────────────────────────────────────────────

class TestAuthEdgeCases:
    def test_missing_auth_header_returns_403(self, client):
        resp = client.get("/api/v1/sessions")
        assert resp.status_code in (401, 403)

    def test_malformed_bearer_token_returns_401(self, client):
        resp = client.get("/api/v1/sessions", headers={"Authorization": "Bearer not.a.jwt"})
        assert resp.status_code == 401

    def test_wrong_token_type_prefix_returns_401(self, client):
        resp = client.get("/api/v1/sessions", headers={"Authorization": "Token sometoken"})
        assert resp.status_code in (401, 403)

    def test_refresh_token_cannot_be_used_as_access_token(self, client, registered_user):
        login = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        refresh_token = login.json()["data"]["refresh_token"]
        # Try using refresh token as Bearer
        resp = client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
        assert resp.status_code == 401

    def test_expired_token_returns_401(self, client, registered_user):
        from backend.auth.jwt_handler import create_access_token
        from datetime import datetime, timedelta, timezone
        import jwt
        from backend.config import settings

        payload = {
            "sub": registered_user["user"]["id"],
            "tenant_id": registered_user["tenant"]["id"],
            "role": "admin",
            "jti": "expired_jti",
            "type": "access",
            "exp": (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp(),
        }
        expired_token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        resp = client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401

    def test_revoked_token_returns_401(self, client, registered_user):
        login = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Logout to revoke
        client.post("/api/v1/auth/logout", headers=headers)

        # Subsequent request should be rejected
        resp = client.get("/api/v1/sessions", headers=headers)
        assert resp.status_code == 401

    def test_forgot_password_always_returns_200(self, client):
        # Must not reveal whether email exists (timing-safe)
        resp = client.post("/api/v1/auth/forgot-password", json={
            "email": f"ghost-{uuid4().hex}@example.com"
        })
        assert resp.status_code == 200


# ── Authorization / RBAC edge cases ──────────────────────────────────────────

class TestRBAC:
    def test_viewer_cannot_create_session(self, client, viewer_headers):
        resp = client.post(
            "/api/v1/sessions",
            json={"name": "viewer-attempt"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_viewer_cannot_upload_dataset(self, client, viewer_headers, csv_bytes):
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("data.csv", csv_bytes, "text/csv")},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_viewer_cannot_configure_columns(self, client, viewer_headers, test_session, auth_headers):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_viewer_cannot_start_training(self, client, viewer_headers, configured_session):
        sid = configured_session["id"]
        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=viewer_headers)
        assert resp.status_code == 403

    def test_viewer_can_read_sessions(self, client, viewer_headers):
        resp = client.get("/api/v1/sessions", headers=viewer_headers)
        assert resp.status_code == 200

    def test_analyst_can_create_session(self, client, analyst_headers):
        resp = client.post(
            "/api/v1/sessions",
            json={"name": f"analyst-sess-{uuid4().hex[:4]}"},
            headers=analyst_headers,
        )
        assert resp.status_code == 201

    def test_analyst_can_upload_dataset(self, client, analyst_headers, csv_bytes):
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("data.csv", csv_bytes, "text/csv")},
            headers=analyst_headers,
        )
        assert resp.status_code == 201

    def test_analyst_can_configure_columns(self, client, analyst_headers, test_session, uploaded_dataset):
        sid = test_session["id"]
        attach = client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": uploaded_dataset["id"]},
            headers=analyst_headers,
        )
        assert attach.status_code == 200
        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales"},
            headers=analyst_headers,
        )
        assert resp.status_code == 200

    def test_analyst_can_start_training(self, client, analyst_headers, configured_session):
        sid = configured_session["id"]
        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=analyst_headers)
        assert resp.status_code == 202

    def test_tenant_isolation_sessions(self, client, registered_user):
        """User from tenant A cannot see sessions of tenant B."""
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        from backend.db.connection import execute

        # Create second isolated tenant
        t2 = create_tenant(f"isolated-{uuid4().hex[:8]}")
        e2 = f"iso-{uuid4().hex[:8]}@example.com"
        u2 = user_svc.create_user(t2["id"], e2, "TestPass123!", "admin")
        user_svc.mark_verified(t2["id"], u2["id"])

        # Login as t2 user
        login = client.post("/api/v1/auth/login", json={"email": e2, "password": "TestPass123!"})
        token2 = login.json()["data"]["access_token"]
        headers2 = {"Authorization": f"Bearer {token2}"}

        # Create session in t2
        s2 = client.post("/api/v1/sessions", json={"name": "t2-session"}, headers=headers2)
        s2_id = s2.json()["data"]["id"]

        # T1 user cannot see T2's session
        resp = client.get(f"/api/v1/sessions/{s2_id}", headers={
            "Authorization": f"Bearer {client.post('/api/v1/auth/login', json={'email': registered_user['email'], 'password': registered_user['password']}).json()['data']['access_token']}"
        })
        assert resp.status_code == 404

        execute("DELETE FROM tenants WHERE id = %s", (t2["id"],))


# ── Inventory permissions: intentionally ungated ─────────────────────────────
#
# Unlike sessions/datasets/training, the inventory endpoints (stock, events,
# suppliers, BOM, bulk-import) have no role gate in source — every route depends
# only on `get_current_user`, not `require_analyst_or_above`. This was confirmed
# with the user to be intentional (any authenticated user, including viewer, may
# mutate inventory), not an oversight. These tests lock in that decision: if
# someone later adds a role gate here, these tests will fail and force an
# explicit, conscious update rather than a silent behavior change.

class TestInventoryPermissionsIntentionallyOpen:
    def test_viewer_can_upsert_and_delete_stock(self, client, viewer_headers):
        sku = f"PYTEST-VWR-{uuid4().hex[:8]}"
        put = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"stock_actual": 10},
            headers=viewer_headers,
        )
        assert put.status_code == 200
        assert put.json()["data"]["stock_actual"] == 10

        delete = client.delete(f"/api/v1/inventory/stock/{sku}", headers=viewer_headers)
        assert delete.status_code == 204

    def test_viewer_can_create_patch_delete_event(self, client, viewer_headers):
        create = client.post(
            "/api/v1/inventory/events",
            json={"name": "viewer-event", "start_date": "2026-11-01", "end_date": "2026-11-30"},
            headers=viewer_headers,
        )
        assert create.status_code == 201
        event_id = create.json()["data"]["id"]

        patch = client.patch(
            f"/api/v1/inventory/events/{event_id}",
            json={"multiplier": 2.0},
            headers=viewer_headers,
        )
        assert patch.status_code == 200
        assert patch.json()["data"]["multiplier"] == 2.0

        delete = client.delete(f"/api/v1/inventory/events/{event_id}", headers=viewer_headers)
        assert delete.status_code == 204

    def test_viewer_can_create_patch_delete_supplier(self, client, viewer_headers):
        create = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": "viewer-supplier"},
            headers=viewer_headers,
        )
        assert create.status_code == 201
        supplier_id = create.json()["data"]["id"]

        patch = client.patch(
            f"/api/v1/inventory/suppliers/{supplier_id}",
            json={"name": "viewer-supplier-renamed"},
            headers=viewer_headers,
        )
        assert patch.status_code == 200
        assert patch.json()["data"]["name"] == "viewer-supplier-renamed"

        delete = client.delete(f"/api/v1/inventory/suppliers/{supplier_id}", headers=viewer_headers)
        assert delete.status_code == 204

    def test_viewer_can_upsert_and_delete_bom_item(self, client, viewer_headers):
        parent, child = f"VWR-P-{uuid4().hex[:6]}", f"VWR-C-{uuid4().hex[:6]}"
        for sku in (parent, child):
            assert client.put(
                f"/api/v1/inventory/stock/{sku}", json={"stock_actual": 1}, headers=viewer_headers
            ).status_code == 200

        put = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 5.0},
            headers=viewer_headers,
        )
        assert put.status_code == 200
        assert put.json()["data"]["quantity"] == 5.0

        delete = client.delete(f"/api/v1/inventory/bom/{parent}/{child}", headers=viewer_headers)
        assert delete.status_code == 204

    def test_viewer_can_bulk_import_stock(self, client, viewer_headers):
        sku = f"PYTEST-BULK-{uuid4().hex[:8]}"
        csv_content = f"sku,stock_actual,lead_time_dias\n{sku},25,10\n".encode()
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", csv_content, "text/csv")},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["imported"] == 1

        check = client.get(f"/api/v1/inventory/stock/{sku}", headers=viewer_headers)
        assert check.status_code == 200
        assert check.json()["data"]["stock_actual"] == 25


# ── Input validation edge cases ───────────────────────────────────────────────

class TestInputValidation:
    def test_create_session_without_name_returns_422(self, client, auth_headers):
        resp = client.post("/api/v1/sessions", json={}, headers=auth_headers)
        assert resp.status_code == 422

    def test_configure_columns_empty_date_column_returns_422(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "", "target_column": ""},
            headers=auth_headers,
        )
        # Either 422 (Pydantic) or 400/409 from app logic
        assert resp.status_code in (400, 409, 422)

    def test_training_config_train_ratio_has_no_range_validation(self, client, auth_headers, test_session):
        """
        ValidationConfigRequest.train_ratio (schemas/configuration.py:64) is a bare
        `float = 0.8` with no Field(ge=.., le=..) constraint, so an out-of-range value
        like 1.5 (>100% train split) is accepted and persisted as-is — there is no
        code path that rejects it. This pins down that gap with a real assertion
        instead of treating either outcome as a pass.
        """
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/validation",
            json={"train_ratio": 1.5},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        check = client.get(f"/api/v1/sessions/{sid}/configure/validation", headers=auth_headers)
        assert check.status_code == 200
        assert check.json()["data"]["train_ratio"] == 1.5

    def test_signup_missing_required_fields_returns_422(self, client):
        resp = client.post("/api/v1/auth/signup", json={"email": "test@example.com"})
        assert resp.status_code == 422

    def test_upload_oversized_file_returns_400(self, client, auth_headers):
        # Create fake oversized content (>200MB limit)
        from backend.config import settings
        fake_big = b"a" * (settings.max_upload_size_mb * 1024 * 1024 + 1)
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("big.csv", fake_big, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_attach_dataset_with_empty_id_returns_422(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": ""},
            headers=auth_headers,
        )
        assert resp.status_code in (404, 422)

    def test_reset_password_with_invalid_token_returns_400(self, client):
        resp = client.post("/api/v1/auth/reset-password", json={
            "token": "invalid.token.here",
            "new_password": "NewPass123!",
        })
        assert resp.status_code == 400

    def test_reset_password_weak_returns_400(self, client, registered_user):
        from backend.auth.jwt_handler import create_signed_token
        token = create_signed_token({
            "sub": registered_user["user"]["id"],
            "tenant_id": registered_user["tenant"]["id"],
            "purpose": "password_reset",
        }, expires_minutes=15)
        resp = client.post("/api/v1/auth/reset-password", json={
            "token": token,
            "new_password": "weak",
        })
        assert resp.status_code == 400


# ── Session state machine edge cases ─────────────────────────────────────────

class TestSessionStateEdgeCases:
    def test_cannot_delete_running_session(self, client, auth_headers, test_tenant):
        from backend.sessions.service import create_session, force_status

        s = create_session(test_tenant["id"], "usr_test", "running-session")
        force_status(test_tenant["id"], s["id"], "RUNNING")
        resp = client.delete(f"/api/v1/sessions/{s['id']}", headers=auth_headers)
        assert resp.status_code == 409

    def test_cannot_start_training_on_running_session(self, client, auth_headers, test_tenant):
        from backend.sessions.service import create_session, force_status

        s = create_session(test_tenant["id"], "usr_test", "already-running")
        force_status(test_tenant["id"], s["id"], "RUNNING")
        resp = client.post(f"/api/v1/sessions/{s['id']}/train", headers=auth_headers)
        assert resp.status_code == 409

    def test_results_on_draft_session_returns_409(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/results", headers=auth_headers)
        assert resp.status_code == 409

    def test_results_on_running_session_returns_409(self, client, auth_headers, test_tenant):
        from backend.sessions.service import create_session, force_status

        s = create_session(test_tenant["id"], "usr_test", "running-results-test")
        force_status(test_tenant["id"], s["id"], "RUNNING")
        resp = client.get(f"/api/v1/sessions/{s['id']}/results", headers=auth_headers)
        assert resp.status_code == 409

    def test_cancel_completed_job_returns_409(self, client, auth_headers, completed_session, registered_user):
        from backend.training.job_service import create_job, mark_completed

        tid = registered_user["tenant"]["id"]
        sid = completed_session["id"]
        job = create_job(tid, sid, "usr_test")
        mark_completed(tid, job["id"])
        resp = client.delete(f"/api/v1/jobs/{job['id']}", headers=auth_headers)
        assert resp.status_code == 409


# ── Quota edge cases ──────────────────────────────────────────────────────────

class TestQuotaLimits:
    def test_concurrent_job_limit_enforced(self, client, auth_headers, configured_session, test_tenant):
        from backend.training.job_service import create_job, mark_running
        from backend.db.connection import execute

        # Fill up to limit (3) with fake RUNNING jobs
        sid = configured_session["id"]
        job_ids = []
        for _ in range(3):
            j = create_job(test_tenant["id"], sid, "usr_test")
            mark_running(test_tenant["id"], j["id"], "worker_test")
            job_ids.append(j["id"])

        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert resp.status_code == 429

        # Cleanup: cancel the fake jobs
        for jid in job_ids:
            execute(
                "UPDATE jobs SET status = 'CANCELLED', completed_at = NOW() WHERE id = %s",
                (jid,),
            )
