"""
Tests for the one-click demo endpoint (POST /demo/quickstart) and the
WhatsApp-alert plumbing (users.whatsapp_number + alert send-now endpoint).
Feature spec: docs/features_propuestas_faro_2026-07-05.md (1.1 / 1.2).
"""

from unittest import mock

import pytest

from backend.db.connection import query_one


class TestDemoQuickstart:
    def test_viewer_denied(self, client, viewer_headers):
        resp = client.post("/api/v1/demo/quickstart", headers=viewer_headers)
        assert resp.status_code == 403

    def test_demo_seeds_everything_and_queues_training(self, client, auth_headers, test_tenant):
        resp = client.post("/api/v1/demo/quickstart", headers=auth_headers)
        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        sid, job_id, ds_id = data["session_id"], data["job_id"], data["dataset_id"]
        tid = test_tenant["id"]

        # Dataset row exists and its file is on disk
        ds = query_one("SELECT * FROM datasets WHERE id = %s AND tenant_id = %s", (ds_id, tid))
        assert ds is not None
        assert ds["file_type"] == "csv"
        from pathlib import Path
        assert Path(ds["file_path"]).exists()

        # Session is queued for training with the dataset attached
        sess = query_one("SELECT * FROM sessions WHERE id = %s AND tenant_id = %s", (sid, tid))
        assert sess is not None
        assert sess["status"] == "QUEUED"

        # All six config blobs were seeded
        cfg = query_one(
            "SELECT * FROM session_configs WHERE session_id = %s AND tenant_id = %s", (sid, tid)
        )
        assert cfg is not None
        for field in ("columns_cfg", "features_cfg", "models_cfg",
                      "validation_cfg", "forecast_cfg", "business_cfg"):
            assert cfg[field], f"{field} was not seeded"
        assert cfg["columns_cfg"]["schema_version"] == "canonical_v1"
        assert cfg["models_cfg"]["selected_models"] == ["lightgbm", "prophet", "croston", "xgboost"]

        # A real job is queued
        job = query_one("SELECT * FROM jobs WHERE id = %s AND tenant_id = %s", (job_id, tid))
        assert job is not None
        assert job["status"] in ("QUEUED", "RUNNING", "COMPLETED")

        # Demo stock was seeded (all 5 SKUs on a fresh tenant)
        row = query_one(
            "SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id = %s AND sku LIKE 'SKU-%%'",
            (tid,),
        )
        assert row["n"] >= 5

    def test_demo_does_not_overwrite_existing_stock(self, client, auth_headers, test_tenant):
        # Pre-set a stock value the demo would otherwise change
        put = client.put(
            "/api/v1/inventory/stock/SKU-001",
            json={"current_stock": 777, "lead_time_days": 3},
            headers=auth_headers,
        )
        assert put.status_code == 200

        resp = client.post("/api/v1/demo/quickstart", headers=auth_headers)
        assert resp.status_code == 202
        assert "SKU-001" not in resp.json()["data"]["stock_seeded"]

        row = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = 'SKU-001'",
            (test_tenant["id"],),
        )
        assert float(row["current_stock"]) == 777.0


class TestWhatsappNumber:
    def test_set_and_clear_whatsapp_number(self, client, auth_headers, registered_user, test_tenant):
        resp = client.patch(
            "/api/v1/users/me",
            json={"whatsapp_number": "+573001234567"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["whatsapp_number"] == "+573001234567"

        # Independent DB verification
        row = query_one(
            "SELECT whatsapp_number FROM users WHERE tenant_id = %s AND email = %s",
            (test_tenant["id"], registered_user["email"]),
        )
        assert row["whatsapp_number"] == "+573001234567"

        # Empty string clears it (opt-out)
        resp = client.patch("/api/v1/users/me", json={"whatsapp_number": ""}, headers=auth_headers)
        assert resp.status_code == 200
        row = query_one(
            "SELECT whatsapp_number FROM users WHERE tenant_id = %s AND email = %s",
            (test_tenant["id"], registered_user["email"]),
        )
        assert row["whatsapp_number"] is None

    def test_invalid_number_rejected(self, client, auth_headers):
        for bad in ("3001234567", "+0012345", "hola", "+57 300 123"):
            resp = client.patch(
                "/api/v1/users/me", json={"whatsapp_number": bad}, headers=auth_headers
            )
            assert resp.status_code == 422, f"'{bad}' should be rejected, got {resp.status_code}"


class TestAlertSendNow:
    def test_viewer_denied(self, client, viewer_headers):
        resp = client.post(
            "/api/v1/inventory/alerts/send-now",
            params={"session_id": "sess_x"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_no_risk_no_send(self, client, auth_headers, test_session):
        # A DRAFT session has no inventory status items → nothing to alert
        resp = client.post(
            "/api/v1/inventory/alerts/send-now",
            params={"session_id": test_session["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 202
        assert resp.json()["data"]["sent"] is False


class TestWhatsappTransport:
    @pytest.mark.offline
    def test_not_configured_is_noop(self):
        from backend.notifications import whatsapp

        with mock.patch.object(whatsapp.settings, "twilio_account_sid", ""):
            assert whatsapp.send_whatsapp("+573001234567", "hola") is False

    @pytest.mark.offline
    def test_configured_posts_to_twilio(self):
        from backend.notifications import whatsapp

        fake_resp = mock.Mock(status_code=201)
        fake_resp.raise_for_status = mock.Mock()
        with mock.patch.object(whatsapp.settings, "twilio_account_sid", "AC123"), \
             mock.patch.object(whatsapp.settings, "twilio_auth_token", "tok"), \
             mock.patch.object(whatsapp.settings, "twilio_whatsapp_from", "whatsapp:+14155238886"), \
             mock.patch("httpx.post", return_value=fake_resp) as post:
            ok_sent = whatsapp.send_whatsapp("+573001234567", "hola")
        assert ok_sent is True
        args, kwargs = post.call_args
        assert "AC123" in args[0]
        assert kwargs["data"]["To"] == "whatsapp:+573001234567"
        assert kwargs["data"]["Body"] == "hola"

    @pytest.mark.offline
    def test_alert_text_contains_items_and_url(self):
        from backend.notifications.whatsapp import build_inventory_alert_text

        text = build_inventory_alert_text(
            [{"sku": "SKU-001", "display_name": "Aceite 1L", "coverage_days": 2.0,
              "recommended_qty": 120}],
            [{"sku": "SKU-002"}],
            "http://localhost:5000/inventory",
        )
        assert "Aceite 1L" in text
        assert "120" in text
        assert "http://localhost:5000/inventory" in text
        assert "🔴" in text and "🟡" in text


class TestResendTransport:
    # NOTE: these call _transport_send, not _send — conftest patches _send
    # globally (so signups in other tests never send real email), which would
    # make assertions here trivially pass against the mock.

    @pytest.mark.offline
    def test_resend_used_when_key_present(self):
        from backend.notifications import email as email_mod

        fake_resp = mock.Mock(status_code=200)
        fake_resp.raise_for_status = mock.Mock()
        with mock.patch.object(email_mod.settings, "resend_api_key", "re_test_123"), \
             mock.patch("httpx.post", return_value=fake_resp) as post:
            email_mod._transport_send("dest@example.com", "Prueba", "<b>hola</b>")
        args, kwargs = post.call_args
        assert args[0] == "https://api.resend.com/emails"
        assert kwargs["headers"]["Authorization"] == "Bearer re_test_123"
        assert kwargs["json"]["to"] == ["dest@example.com"]

    @pytest.mark.offline
    def test_no_transport_is_logged_noop(self):
        from backend.notifications import email as email_mod

        with mock.patch.object(email_mod.settings, "resend_api_key", ""), \
             mock.patch.object(email_mod.settings, "smtp_user", ""), \
             mock.patch("httpx.post") as post, \
             mock.patch("smtplib.SMTP") as smtp:
            email_mod._transport_send("dest@example.com", "Prueba", "<b>hola</b>")
        post.assert_not_called()
        smtp.assert_not_called()