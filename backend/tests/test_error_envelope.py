"""
Error envelope contract (slice 1 of "no Spanish in backend code").

Backend user-facing errors now raise ``AppError`` (backend/errors.py) carrying a
stable machine ``error_code`` + ``error_params`` and an English fallback
``message``. The handler in backend/main.py serializes them to a JSON error
response that keeps the existing ``detail`` field (English fallback) and ADDS
``error_code`` + ``error_params`` — the frontend renders the localized (Spanish)
``errors.<error_code>`` string, interpolating params.

These tests pin that reusable contract on the reception-over-pending case: the
response carries the code + params, and the over-receipt guard still blocks with
NO state change (received_qty untouched, PO header still pending). Later slices
copy the same code+params convention.

``TestHotPathErrorCodes`` extends the same contract to the guards a real user
trips most often — login, signup, sessions, training and inventory. Each asserts
the machine code (not the English sentence, which is only a fallback) AND, where
the request was mutating, that nothing changed in the DB.
"""

from uuid import uuid4

from backend.db.connection import query_one
from backend.inventory import roi_service
from tests.test_endpoints import unique_phone


def _make_po(tenant_id: str, sku: str, final_qty: float) -> dict:
    """A single-line approved PO destined for the default warehouse."""
    return roi_service.log_po_generation(tenant_id, "sess-test", [{
        "sku": sku, "final_qty": final_qty, "status": "approved",
        "warehouse": "principal",
    }])


class TestReceptionOverPendingEnvelope:
    def test_over_pending_carries_code_params_and_blocks(
        self, client, analyst_headers, registered_user
    ):
        tenant_id = registered_user["tenant"]["id"]
        sku = f"OVR_{uuid4().hex[:8]}"
        po = _make_po(tenant_id, sku, final_qty=10)

        resp = client.post(
            f"/api/v1/inventory/po/{po['id']}/receive",
            json={"lines": [{"sku": sku, "received_qty": 25}]},
            headers=analyst_headers,
        )

        # Analyst passes the role gate and reaches the business rule (422, not
        # 403), and the response carries the machine code + params alongside the
        # English fallback in `detail`.
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error_code"] == "reception_over_pending"
        assert body["error_params"]["sku"] == sku
        assert float(body["error_params"]["qty"]) == 25
        assert float(body["error_params"]["pending"]) == 10
        # English fallback message, no Spanish in the backend payload.
        assert body["detail"]
        assert "excede" not in body["detail"].lower()

        # Guard still blocks with no state change: nothing received, PO pending.
        item = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE po_log_id=%s AND sku=%s",
            (po["id"], sku),
        )
        assert item["received_qty"] in (None, 0)
        log = query_one(
            "SELECT reception_status FROM inventory_po_log WHERE id=%s", (po["id"],)
        )
        assert log["reception_status"] == "pending"

    def test_viewer_denied_and_state_unchanged(
        self, client, viewer_headers, registered_user
    ):
        tenant_id = registered_user["tenant"]["id"]
        sku = f"OVRV_{uuid4().hex[:8]}"
        po = _make_po(tenant_id, sku, final_qty=10)

        resp = client.post(
            f"/api/v1/inventory/po/{po['id']}/receive",
            json={"lines": [{"sku": sku, "received_qty": 25}]},
            headers=viewer_headers,
        )
        # Viewer denied before the endpoint body runs — permission pair with the
        # analyst success above.
        assert resp.status_code == 403

        log = query_one(
            "SELECT reception_status FROM inventory_po_log WHERE id=%s", (po["id"],)
        )
        assert log["reception_status"] == "pending"
        item = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE po_log_id=%s AND sku=%s",
            (po["id"], sku),
        )
        assert item["received_qty"] in (None, 0)


class TestHotPathErrorCodes:
    """The guards a user meets while just using the app. Every one of these used
    to answer with an English sentence the UI rendered verbatim to a
    Spanish-speaking user."""

    # ── Auth ────────────────────────────────────────────────────────────────

    def test_wrong_password_returns_invalid_credentials_code(
        self, client, registered_user
    ):
        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": "definitely-not-the-password",
        })
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "invalid_credentials"

    def test_unknown_email_reuses_the_same_code(self, client):
        """Same code as a wrong password on purpose — a distinct one would make
        login an account-enumeration oracle."""
        resp = client.post("/api/v1/auth/login", json={
            "email": f"nobody-{uuid4().hex[:8]}@example.com",
            "password": "TestPass123!",
        })
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "invalid_credentials"

    def test_duplicate_signup_returns_code_and_creates_no_second_user(
        self, client, registered_user
    ):
        resp = client.post("/api/v1/auth/signup", json={
            "email": registered_user["email"],
            "password": "TestPass123!",
            "tenant_name": f"tenant-{uuid4().hex[:6]}",
            "whatsapp_number": unique_phone(),
        })
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "email_already_registered"

        row = query_one(
            "SELECT COUNT(*) AS n FROM users WHERE email = %s",
            (registered_user["email"],),
        )
        assert int(row["n"]) == 1

    def test_weak_password_signup_returns_code_and_creates_nothing(self, client):
        email = f"weak-{uuid4().hex[:8]}@example.com"
        resp = client.post("/api/v1/auth/signup", json={
            "email": email,
            "password": "abc",
            "tenant_name": f"tenant-{uuid4().hex[:6]}",
            "whatsapp_number": unique_phone(),
        })
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "password_invalid"
        assert query_one("SELECT id FROM users WHERE email = %s", (email,)) is None

    # ── Sessions ────────────────────────────────────────────────────────────

    def test_unknown_session_returns_session_not_found_code(self, client, auth_headers):
        resp = client.get(f"/api/v1/sessions/{uuid4()}", headers=auth_headers)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "session_not_found"

    def test_deleting_a_running_session_is_blocked_with_a_code(
        self, client, auth_headers, test_session, registered_user
    ):
        from backend.db.connection import execute

        sid = test_session["id"]
        execute("UPDATE sessions SET status = 'RUNNING' WHERE id = %s", (sid,))

        resp = client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "session_running_cannot_delete"
        # Guard held: the session is still there.
        assert query_one("SELECT id FROM sessions WHERE id = %s", (sid,)) is not None

    def test_viewer_cannot_delete_a_session(
        self, client, viewer_headers, test_session
    ):
        """Permission pair for the delete above: the viewer is stopped by the
        role gate before the guard, and the row survives either way."""
        sid = test_session["id"]
        resp = client.delete(f"/api/v1/sessions/{sid}", headers=viewer_headers)
        assert resp.status_code == 403
        assert query_one("SELECT id FROM sessions WHERE id = %s", (sid,)) is not None

    # ── Training ────────────────────────────────────────────────────────────

    def test_training_an_already_running_session_returns_code_and_no_job(
        self, client, auth_headers, configured_session, registered_user
    ):
        """Re-launching a COMPLETED session is legal (PRE_TRAIN_STATES) — the
        state that must be refused is a run already in flight."""
        from backend.db.connection import execute

        tid = registered_user["tenant"]["id"]
        sid = configured_session["id"]
        execute("UPDATE sessions SET status = 'RUNNING' WHERE id = %s", (sid,))
        before = query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s AND session_id = %s",
            (tid, sid),
        )

        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error_code"] == "session_not_trainable"
        assert body["error_params"]["status"] == "RUNNING"

        after = query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s AND session_id = %s",
            (tid, sid),
        )
        assert int(after["n"]) == int(before["n"])

    def test_unknown_job_returns_job_not_found_code(self, client, auth_headers):
        resp = client.get(f"/api/v1/jobs/{uuid4()}", headers=auth_headers)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "job_not_found"

    # ── Inventory ───────────────────────────────────────────────────────────

    def test_unknown_sku_returns_code_with_the_sku_as_a_param(
        self, client, auth_headers
    ):
        sku = f"GHOST_{uuid4().hex[:8]}"
        resp = client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers)
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["error_code"] == "stock_sku_not_found"
        assert body["error_params"]["sku"] == sku

    def test_csv_without_sku_column_returns_code_and_imports_nothing(
        self, client, auth_headers, registered_user
    ):
        tid = registered_user["tenant"]["id"]
        csv_bytes = b"current_stock,lead_time\n100,15\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("bad.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "inventory_import_no_valid_rows"

        row = query_one(
            "SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id = %s", (tid,)
        )
        assert int(row["n"]) == 0

    def test_status_without_a_completed_session_returns_a_code(
        self, client, auth_headers
    ):
        """What every brand-new tenant sees on /hoy before their first run."""
        resp = client.get("/api/v1/inventory/status", headers=auth_headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "no_completed_session"

    # ── Results ─────────────────────────────────────────────────────────────

    def test_results_while_still_training_returns_a_code_with_the_status(
        self, client, auth_headers, configured_session
    ):
        from backend.db.connection import execute

        sid = configured_session["id"]
        execute("UPDATE sessions SET status = 'RUNNING' WHERE id = %s", (sid,))

        resp = client.get(f"/api/v1/sessions/{sid}/results", headers=auth_headers)
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error_code"] == "session_still_training"
        assert body["error_params"]["status"] == "RUNNING"
