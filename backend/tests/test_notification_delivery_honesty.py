"""
Three HIGH-severity silent-failure bugs, each pinned to its own consequence:

1. No email transport configured was reported as a successful send, so invites,
   verification links and purchase orders were "sent" to nobody.
2. The daily stockout digest counted the rows it had listed (10/5) instead of
   the SKUs actually at risk (47), and never said how many were hidden.
3. Alert delivery failures were swallowed, so expired credentials silently
   stopped every alert with nothing in the product saying so.

conftest patches `backend.notifications.email._send` session-wide (the local
.env holds real provider credentials). Tests that need the real dispatch point
`_send` back at `_transport_send` for their own duration.
"""

from uuid import uuid4

import pytest

from backend.db.connection import query, query_one


def _no_transport(monkeypatch):
    """Strip every credential and restore the real dispatch path."""
    from backend.notifications import email as email_mod

    monkeypatch.setattr(email_mod.settings, "resend_api_key", "")
    monkeypatch.setattr(email_mod.settings, "smtp_user", "")
    monkeypatch.setattr(email_mod.settings, "smtp_pass", "")
    monkeypatch.setattr(email_mod, "_send", email_mod._transport_send)


def _critical(n: int) -> list[dict]:
    return [
        {"sku": f"SKU-{i:03d}", "display_name": f"Producto {i}", "signal": "PEDIR_YA",
         "coverage_days": 1.0, "recommended_qty": 100 + i, "supplier": "Acme"}
        for i in range(n)
    ]


def _warning(n: int) -> list[dict]:
    return [
        {"sku": f"WRN-{i:03d}", "display_name": f"Aviso {i}", "signal": "PEDIR_PRONTO",
         "coverage_days": 9.0, "recommended_qty": 20, "supplier": "Acme"}
        for i in range(n)
    ]


# ── Bug 1: an unconfigured transport must never read as "sent" ────────────────

class TestUnconfiguredTransportIsNotASend:
    def test_transport_send_raises_when_no_credentials(self, monkeypatch):
        from backend.notifications import email as email_mod

        _no_transport(monkeypatch)
        assert email_mod.is_configured() is False
        with pytest.raises(email_mod.EmailNotConfigured):
            email_mod._transport_send("nobody@example.com", "s", "<p>x</p>")

    def test_every_public_sender_reports_false_with_no_transport(self, monkeypatch):
        from backend.notifications import email as email_mod

        _no_transport(monkeypatch)
        assert email_mod.send_verification_email("a@b.co", "A", "http://x") is False
        assert email_mod.send_account_setup_email("a@b.co", "A", "http://x") is False
        assert email_mod.send_password_reset_email("a@b.co", "http://x") is False
        assert email_mod.send_change_password_code("a@b.co", "123456") is False
        assert email_mod.send_password_reset_otp("a@b.co", "123456") is False
        assert email_mod.send_inventory_alert_email("a@b.co", _critical(1), [], "http://x") is False
        assert email_mod.send_supplier_lead_time_alert_email("a@b.co", [], "http://x") is False
        assert email_mod.send_training_complete_email("a@b.co", "s", "http://x") is False
        assert email_mod.send_po_to_supplier_email(
            to="a@b.co", supplier_name="X", po_log_id="po_1",
            items=[], pdf_bytes=b"", pdf_filename="po_1_x.pdf",
        ) is False

    def test_a_configured_transport_still_reports_true(self, monkeypatch):
        """The fix must not turn every send into a failure."""
        from backend.notifications import email as email_mod

        monkeypatch.setattr(email_mod.settings, "resend_api_key", "")
        monkeypatch.setattr(email_mod.settings, "smtp_user", "user@example.com")
        monkeypatch.setattr(email_mod.settings, "smtp_pass", "app-password")
        monkeypatch.setattr(email_mod, "_send_smtp", lambda *a, **kw: None)
        monkeypatch.setattr(email_mod, "_send", email_mod._transport_send)

        assert email_mod.is_configured() is True
        assert email_mod.send_verification_email("a@b.co", "A", "http://x") is True

    def test_failure_reason_distinguishes_missing_config_from_provider_error(self, monkeypatch):
        from backend.notifications import email as email_mod

        _no_transport(monkeypatch)
        assert email_mod.failure_reason() == "not_configured"

        monkeypatch.setattr(email_mod.settings, "resend_api_key", "re_live_key")
        assert email_mod.failure_reason() == "transport_error"


class TestSignupDoesNotClaimAnUnsentVerificationLink:
    def test_email_sent_false_and_reason_reported(self, client, monkeypatch):
        _no_transport(monkeypatch)
        email = f"signup-{uuid4().hex[:8]}@example.com"

        resp = client.post("/api/v1/auth/signup", json={
            "email": email, "password": "TestPass123!",
            "full_name": "Nadie", "tenant_name": f"pytest-{uuid4().hex[:8]}",
            "whatsapp_number": f"+5730{uuid4().int % 10_000_000:07d}",
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        try:
            assert data["email_sent"] is False
            assert data["email_error"] == "not_configured"
            # The account exists but is unverified — the state the user is
            # actually in, which the old "email_sent: True" hid.
            row = query_one(
                "SELECT email_verified FROM users WHERE id = %s", (data["user"]["id"],))
            assert row is not None and row["email_verified"] is False
        finally:
            from backend.db.connection import execute
            execute("DELETE FROM tenants WHERE id = %s", (data["tenant"]["id"],))


class TestAdminInviteDoesNotClaimAnUnsentInvite:
    def test_viewer_denied_and_no_user_created(self, client, viewer_headers, test_tenant):
        email = f"invitee-{uuid4().hex[:8]}@example.com"
        resp = client.post("/api/v1/users", headers=viewer_headers, json={
            "email": email, "role": "analyst", "full_name": "Invitee",
        })
        assert resp.status_code == 403
        assert query_one("SELECT id FROM users WHERE email = %s", (email,)) is None

    def test_admin_invite_with_no_transport_reports_email_sent_false(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        _no_transport(monkeypatch)
        email = f"invitee-{uuid4().hex[:8]}@example.com"

        resp = client.post("/api/v1/users", headers=auth_headers, json={
            "email": email, "role": "analyst", "full_name": "Invitee",
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["email_sent"] is False
        assert data["email_error"] == "not_configured"

        # The row is real and unusable: created, never verified, and the person
        # never got the link. The old code returned email_sent=True here.
        row = query_one(
            "SELECT tenant_id, email_verified FROM users WHERE email = %s", (email,))
        assert row is not None
        assert row["tenant_id"] == test_tenant["id"]
        assert row["email_verified"] is False

    def test_admin_invite_reports_true_when_transport_works(
        self, client, auth_headers, monkeypatch,
    ):
        from backend.notifications import email as email_mod

        monkeypatch.setattr(email_mod.settings, "resend_api_key", "")
        monkeypatch.setattr(email_mod.settings, "smtp_user", "user@example.com")
        monkeypatch.setattr(email_mod.settings, "smtp_pass", "app-password")
        monkeypatch.setattr(email_mod, "_send_smtp", lambda *a, **kw: None)
        monkeypatch.setattr(email_mod, "_send", email_mod._transport_send)

        email = f"invitee-{uuid4().hex[:8]}@example.com"
        resp = client.post("/api/v1/users", headers=auth_headers, json={
            "email": email, "role": "analyst", "full_name": "Invitee",
        })
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["email_sent"] is True
        assert resp.json()["data"]["email_error"] is None


class TestPurchaseOrderIsNotMarkedSentWhenNothingLeft:
    """The money case: mark_po_sent starts the lead-time and payment clocks."""

    def _po_with_email_only_supplier(self, tid):
        from backend.inventory import roi_service, supplier_service as sup_svc

        supplier_name = f"Proveedor {uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {"name": supplier_name, "email": "ventas@proveedor.com"})
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": f"SEND_{uuid4().hex[:8]}", "final_qty": 20, "unit_cost": 3.0,
            "status": "approved", "supplier": supplier_name,
        }])
        return po, supplier_name

    def test_viewer_denied_and_po_not_marked_sent(self, client, viewer_headers, test_tenant):
        po, _ = self._po_with_email_only_supplier(test_tenant["id"])
        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=viewer_headers)
        assert resp.status_code == 403
        row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po["id"],))
        assert row["sent_at"] is None

    def test_no_transport_leaves_sent_at_null_and_reports_delivery_failed(
        self, client, analyst_headers, test_tenant, monkeypatch,
    ):
        _no_transport(monkeypatch)
        po, supplier_name = self._po_with_email_only_supplier(test_tenant["id"])

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["sent"] == [], "nothing reached the supplier — nothing may be reported as sent"
        assert data["skipped"] == [{"supplier": supplier_name, "reason": "delivery_failed"}]

        # The clock must not have started: no email means no supplier commitment.
        row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po["id"],))
        assert row["sent_at"] is None

    def test_analyst_success_marks_po_sent(
        self, client, analyst_headers, test_tenant, monkeypatch,
    ):
        from backend.notifications import email as email_mod

        po, supplier_name = self._po_with_email_only_supplier(test_tenant["id"])
        monkeypatch.setattr(email_mod, "send_po_to_supplier_email", lambda **kw: True)

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["sent"] == [
            {"supplier": supplier_name, "email": True, "whatsapp": False}]

        row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po["id"],))
        assert row["sent_at"] is not None


# ── Bug 2: the digest must count SKUs at risk, not rows it had room for ───────

class TestDigestCountsAreNotTruncated:
    def test_email_subject_and_body_report_47_not_10(self, monkeypatch):
        from backend.notifications import email as email_mod

        captured = {}
        monkeypatch.setattr(
            email_mod, "_send",
            lambda to, subject, html, attachment=None: captured.update(
                subject=subject, html=html),
        )

        assert email_mod.send_inventory_alert_email(
            "boss@acme.cr", _critical(47), _warning(12), "http://x/hoy") is True

        assert "47 SKUs" in captured["subject"]
        assert "10 SKU" not in captured["subject"]
        assert "47 productos en riesgo inmediato" in captured["html"]
        # Table stays trimmed, but says how much it left out.
        assert captured["html"].count("PEDIR YA") == 10
        assert captured["html"].count("PEDIR PRONTO") == 5
        assert "y 37 productos más" in captured["html"]
        assert "y 7 productos más" in captured["html"]

    def test_email_has_no_more_row_when_everything_fits(self, monkeypatch):
        from backend.notifications import email as email_mod

        captured = {}
        monkeypatch.setattr(
            email_mod, "_send",
            lambda to, subject, html, attachment=None: captured.update(
                subject=subject, html=html),
        )
        email_mod.send_inventory_alert_email("boss@acme.cr", _critical(3), [], "http://x/hoy")

        assert "3 SKUs" in captured["subject"]
        assert "más" not in captured["html"].split("PEDIR YA")[-1]

    def test_whatsapp_says_how_many_are_really_hidden(self):
        from backend.notifications.whatsapp import build_inventory_alert_text

        text = build_inventory_alert_text(_critical(47), _warning(12), "http://x/hoy")
        assert "47 productos se agotan" in text
        assert "… y 42 más" in text          # 47 - the 5 lines listed
        assert "… y 5 más" not in text       # what the truncated list used to say
        assert "🟡 12 por reabastecer" in text

    def test_daily_loop_hands_the_full_lists_to_the_notification_layer(
        self, monkeypatch, registered_user, test_tenant,
    ):
        """The counts are only honest if the call site stops pre-slicing."""
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        items = _critical(47) + _warning(12)
        _arrange_daily_loop(monkeypatch, tid, items)

        calls = []
        monkeypatch.setattr(
            "backend.notifications.email.send_inventory_alert_email",
            lambda **kw: calls.append(kw) or True,
        )

        inv_svc.run_daily_inventory_alerts()

        assert len(calls) == 1
        assert len(calls[0]["critical_items"]) == 47
        assert len(calls[0]["warning_items"]) == 12


# ── Bug 3: a failed alert must leave a trace the user can see ────────────────

def _arrange_daily_loop(monkeypatch, tid: str, items: list[dict]) -> None:
    """Point run_daily_inventory_alerts at one tenant with a known status set."""
    from backend.db import session_store
    from backend.inventory import service as inv_svc
    from backend.sessions import planning_service

    monkeypatch.setattr(
        inv_svc, "get_tenants_with_active_sessions", lambda: [{"tenant_id": tid}])
    monkeypatch.setattr(planning_service, "resolve_active_session", lambda t: "sess-test")
    monkeypatch.setattr(session_store, "get_forecasts", lambda t, s: {})
    monkeypatch.setattr(inv_svc, "list_stock", lambda t, **kw: [])
    monkeypatch.setattr(inv_svc, "get_learned_lead_times", lambda t: {})
    monkeypatch.setattr(
        inv_svc, "_compute_inventory_status", lambda *a, **kw: items)


def _activity(tenant_id: str, user_id: str, action: str) -> list[dict]:
    return [dict(r) for r in query(
        """SELECT status, context FROM activity_logs
           WHERE tenant_id = %s AND user_id = %s AND action = %s""",
        (tenant_id, user_id, action),
    )]


class TestAlertDeliveryFailuresAreObservable:
    def test_failed_stockout_email_is_recorded_against_the_recipient(
        self, monkeypatch, registered_user, test_tenant,
    ):
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        _arrange_daily_loop(monkeypatch, tid, _critical(47))
        monkeypatch.setattr(
            "backend.notifications.email.send_inventory_alert_email", lambda **kw: False)
        monkeypatch.setattr("backend.notifications.email.is_configured", lambda: True)

        inv_svc.run_daily_inventory_alerts()

        rows = _activity(tid, uid, "inventory_alert_email")
        assert len(rows) == 1, "a stockout alert that never arrived left no trace"
        assert rows[0]["status"] == "failed"
        assert rows[0]["context"]["reason"] == "transport_error"
        assert rows[0]["context"]["critical"] == 47

    def test_unconfigured_email_is_recorded_with_its_own_reason(
        self, monkeypatch, registered_user, test_tenant,
    ):
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        _arrange_daily_loop(monkeypatch, tid, _critical(2))
        _no_transport(monkeypatch)

        inv_svc.run_daily_inventory_alerts()

        rows = _activity(tid, uid, "inventory_alert_email")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["context"]["reason"] == "not_configured"

    def test_delivered_stockout_email_is_recorded_as_success(
        self, monkeypatch, registered_user, test_tenant,
    ):
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        _arrange_daily_loop(monkeypatch, tid, _critical(2))
        monkeypatch.setattr(
            "backend.notifications.email.send_inventory_alert_email", lambda **kw: True)

        inv_svc.run_daily_inventory_alerts()

        rows = _activity(tid, uid, "inventory_alert_email")
        assert len(rows) == 1
        assert rows[0]["status"] == "success"
        assert "reason" not in rows[0]["context"]

    def test_failed_whatsapp_alert_is_recorded(
        self, monkeypatch, registered_user, test_tenant,
    ):
        from backend.db.connection import execute
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        execute("UPDATE users SET whatsapp_number = %s WHERE id = %s", ("+573001112222", uid))

        _arrange_daily_loop(monkeypatch, tid, _critical(3))
        monkeypatch.setattr(
            "backend.notifications.email.send_inventory_alert_email", lambda **kw: True)
        monkeypatch.setattr("backend.entitlements.service.has_feature", lambda *a, **kw: True)
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp", lambda *a, **kw: False)

        inv_svc.run_daily_inventory_alerts()

        rows = _activity(tid, uid, "inventory_alert_whatsapp")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["context"]["recipient"] == "+573001112222"

    def test_failed_supplier_lead_time_alert_is_recorded(
        self, monkeypatch, registered_user, test_tenant,
    ):
        from backend.inventory import supplier_health_service as sh_svc

        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        monkeypatch.setattr(sh_svc, "query", lambda *a, **kw: [{"tenant_id": tid}])
        monkeypatch.setattr(
            sh_svc, "get_lead_time_deviations",
            lambda t: [{"supplier": "Acme", "deviation_days": 4.0, "severity": "high"}])
        monkeypatch.setattr(
            "backend.notifications.email.send_supplier_lead_time_alert_email",
            lambda **kw: False)

        sh_svc.run_daily_supplier_lead_time_alerts()

        rows = _activity(tid, uid, "supplier_lead_time_alert_email")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["context"]["suppliers"] == 1

    def test_failed_monthly_roi_email_is_recorded(
        self, monkeypatch, registered_user, test_tenant,
    ):
        from datetime import datetime, timezone

        from backend.inventory import roi_service, service as inv_svc

        tid = test_tenant["id"]
        uid = registered_user["user"]["id"]
        recipient = registered_user["email"]

        monkeypatch.setattr(
            inv_svc, "get_tenants_with_active_sessions", lambda: [{"tenant_id": tid}])
        monkeypatch.setattr(roi_service, "get_month_report", lambda t, y, m: {
            "month": "2026-03", "has_sufficient_history": True,
            "recommendations_shown": 8, "recommendations_followed": 6,
            "adoption_rate": 0.75, "stockout_risks_handled": 2,
            "capital_freed": 1500.0, "managed_purchase_value": 4000.0,
        })
        monkeypatch.setattr(
            "backend.notifications.email.send_monthly_roi_email", lambda **kw: False)

        sent = roi_service.run_monthly_roi_emails(
            now=datetime(2026, 4, 1, 0, 5, tzinfo=timezone.utc))

        # Nothing was delivered, so the dedupe log must stay empty: a recorded
        # send would suppress next month's retry for a recap nobody received.
        assert sent == 0
        assert query_one(
            "SELECT id FROM inventory_roi_email_log WHERE tenant_id = %s", (tid,)) is None

        rows = _activity(tid, uid, "monthly_roi_email")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["context"]["recipient"] == recipient

    def test_po_send_failure_is_recorded_against_the_buyer(
        self, client, analyst_headers, analyst_user, test_tenant, monkeypatch,
    ):
        from backend.inventory import roi_service, supplier_service as sup_svc

        tid = test_tenant["id"]
        uid = analyst_user["user"]["id"]
        supplier_name = f"Proveedor {uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {"name": supplier_name, "email": "ventas@proveedor.com"})
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": f"SEND_{uuid4().hex[:8]}", "final_qty": 7, "unit_cost": 1.0,
            "status": "approved", "supplier": supplier_name,
        }])
        _no_transport(monkeypatch)

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=analyst_headers)
        assert resp.status_code == 200, resp.text

        rows = _activity(tid, uid, "po_sent_to_suppliers")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["context"]["not_delivered"] == [supplier_name]
        assert rows[0]["context"]["delivered"] == []
