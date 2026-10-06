"""The per-warehouse digest: a user limited to some warehouses receives the
daily alert and the freshness reminder computed over THEIR warehouses, through
the same rows the scoped screens read.

Every assertion reads what was actually rendered and sent (captured transport)
and `activity_logs`. The monthly recap stays company-only: its ROI report has no
warehouse filter, so a scoped user is withheld from it and told so.
"""

import json
from uuid import uuid4

import pytest

from backend.db.connection import execute, query


def _user(tenant_id, role, *, status="active", scope=None, whatsapp=None):
    from backend.users import service as user_svc
    u = user_svc.create_user(
        tenant_id=tenant_id, email=f"{role}-{uuid4().hex[:8]}@example.com",
        password="TestPass123!", role=role, full_name=role.title(),
    )
    execute(
        "UPDATE users SET status = %s, warehouse_scope = %s::jsonb, whatsapp_number = %s "
        "WHERE id = %s",
        (status, json.dumps(scope) if scope is not None else None, whatsapp, u["id"]),
    )
    return u


def _wh_id(tid, name):
    execute("INSERT INTO warehouses (tenant_id, name, is_default) VALUES (%s, %s, false)",
            (tid, name))
    return query("SELECT id FROM warehouses WHERE tenant_id = %s AND name = %s",
                 (tid, name))[0]["id"]


def _row(sku, wh, signal="PEDIR_YA"):
    return {"sku": sku, "display_name": f"Product {sku}", "warehouse": wh, "signal": signal,
            "coverage_days": 1.0, "recommended_qty": 10, "supplier": "Acme"}


@pytest.fixture
def people(test_tenant):
    tid = test_tenant["id"]
    norte, sur = _wh_id(tid, "Norte"), _wh_id(tid, "Sur")
    return {
        "tid": tid,
        "admin": _user(tid, "admin", whatsapp="+50670000001"),
        "scoped_norte": _user(tid, "analyst", scope=[norte], whatsapp="+50670000002"),
        "scoped_sur_only_ok": _user(tid, "analyst", scope=[sur]),
        "scoped_none": _user(tid, "analyst", scope=[]),
        "inactive_scoped": _user(tid, "analyst", status="inactive", scope=[norte],
                                 whatsapp="+50670000003"),
        "inactive": _user(tid, "analyst", status="inactive", whatsapp="+50670000004"),
    }


@pytest.fixture
def mails(monkeypatch):
    """Everything that left through the e-mail transport: {to: (subject, html)}."""
    sent: dict[str, tuple[str, str]] = {}
    monkeypatch.setattr("backend.notifications.email._send",
                        lambda to, subject, html, **kw: sent.__setitem__(to, (subject, html)))
    return sent


def _stub_alert_inputs(monkeypatch, tid, wh_rows):
    from backend.db import session_store
    from backend.inventory import service as inv_svc
    from backend.sessions import planning_service
    monkeypatch.setattr(inv_svc, "get_tenants_with_active_sessions", lambda: [{"tenant_id": tid}])
    monkeypatch.setattr(planning_service, "resolve_active_session", lambda t: "sess-test")
    monkeypatch.setattr(session_store, "get_forecasts", lambda t, s: {})
    monkeypatch.setattr(inv_svc, "list_stock", lambda t, **kw: [])
    monkeypatch.setattr(inv_svc, "get_learned_lead_times", lambda t: {})
    monkeypatch.setattr(inv_svc, "_compute_inventory_status",
                        lambda *a, **kw: [dict(r) for r in wh_rows])
    monkeypatch.setattr(inv_svc, "get_inventory_status_by_warehouse",
                        lambda *a, **kw: [dict(r) for r in wh_rows])


def _log(tid, user_id, action):
    return query("SELECT status, context FROM activity_logs WHERE tenant_id = %s "
                 "AND user_id = %s AND action = %s", (tid, user_id, action))


class TestDailyAlert:
    def test_scoped_recipient_gets_only_their_warehouse_rows(self, people, mails, monkeypatch):
        from backend.inventory import service as inv_svc
        p = people
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp",
                            lambda *a, **kw: True)
        _stub_alert_inputs(monkeypatch, p["tid"], [_row("N-1", "Norte"), _row("S-1", "Sur")])

        inv_svc.run_daily_inventory_alerts()

        subject, html = mails[p["scoped_norte"]["email"]]
        assert "Norte" in subject and "Norte" in html
        assert "N-1" in html
        # No leakage: neither the other warehouse's SKU nor its name.
        assert "S-1" not in html and "Sur" not in html and "Sur" not in subject
        row = _log(p["tid"], p["scoped_norte"]["id"], "inventory_alert_email")
        assert len(row) == 1 and row[0]["status"] == "success"
        assert row[0]["context"]["warehouses"] == ["Norte"]
        assert row[0]["context"]["critical"] == 1

    def test_unscoped_admin_still_gets_the_company_digest(self, people, mails, monkeypatch):
        from backend.inventory import service as inv_svc
        p = people
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp",
                            lambda *a, **kw: True)
        _stub_alert_inputs(monkeypatch, p["tid"], [_row("N-1", "Norte"), _row("S-1", "Sur")])

        inv_svc.run_daily_inventory_alerts()

        subject, html = mails[p["admin"]["email"]]
        assert "N-1" in html and "S-1" in html
        assert "Este resumen cubre solo" not in html and "bodegas:" not in subject

    def test_inactive_users_get_nothing(self, people, mails, monkeypatch):
        from backend.inventory import service as inv_svc
        p = people
        wa_to: list[str] = []
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp",
                            lambda number, *a, **kw: wa_to.append(number) or True)
        _stub_alert_inputs(monkeypatch, p["tid"], [_row("N-1", "Norte")])

        inv_svc.run_daily_inventory_alerts()

        assert p["inactive"]["email"] not in mails
        assert p["inactive_scoped"]["email"] not in mails
        for u in (p["inactive"], p["inactive_scoped"]):
            assert query(
                "SELECT 1 FROM activity_logs WHERE user_id = %s AND action IN "
                "('inventory_alert_email', 'inventory_alert_whatsapp', "
                "'scoped_digest_empty', 'company_digest_withheld')", (u["id"],)) == []
        assert sorted(wa_to) == ["+50670000001", "+50670000002"]

    def test_scope_with_nothing_to_report_gets_no_email_and_a_line(self, people, mails, monkeypatch):
        from backend.inventory import service as inv_svc
        p = people
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp",
                            lambda *a, **kw: True)
        # Only Norte is at risk: the Sur-only buyer and the empty-scope buyer
        # have nothing to read.
        _stub_alert_inputs(monkeypatch, p["tid"], [_row("N-1", "Norte")])

        inv_svc.run_daily_inventory_alerts()

        assert p["scoped_sur_only_ok"]["email"] not in mails
        assert p["scoped_none"]["email"] not in mails
        sur = _log(p["tid"], p["scoped_sur_only_ok"]["id"], "scoped_digest_empty")
        assert len(sur) == 1 and sur[0]["context"] == {
            "digest": "inventory_alert", "warehouses": ["Sur"]}
        none = _log(p["tid"], p["scoped_none"]["id"], "scoped_digest_empty")
        assert len(none) == 1 and none[0]["context"]["warehouses"] == []

    def test_scoped_user_is_served_when_only_their_warehouse_is_at_risk(
            self, people, mails, monkeypatch):
        """The company aggregate can read fine while one warehouse is out; the
        tenant-wide 'nothing at risk' must not end the run for them."""
        from backend.inventory import service as inv_svc
        p = people
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp",
                            lambda *a, **kw: True)
        _stub_alert_inputs(monkeypatch, p["tid"], [_row("N-1", "Norte")])
        monkeypatch.setattr(inv_svc, "_compute_inventory_status",
                            lambda *a, **kw: [_row("N-1", "Norte", "OK")])

        inv_svc.run_daily_inventory_alerts()

        assert p["scoped_norte"]["email"] in mails
        assert p["admin"]["email"] not in mails

    def test_scoped_whatsapp_goes_through_the_plan_gate(self, people, mails, monkeypatch):
        from backend.inventory import service as inv_svc
        p = people
        calls: list[tuple] = []
        monkeypatch.setattr(
            "backend.notifications.whatsapp.send_whatsapp",
            lambda number, text, **kw: calls.append((number, text, kw)) or True)
        _stub_alert_inputs(monkeypatch, p["tid"], [_row("N-1", "Norte"), _row("S-1", "Sur")])

        inv_svc.run_daily_inventory_alerts()

        by_number = {c[0]: c for c in calls}
        number, text, kw = by_number["+50670000002"]
        assert kw.get("plan_gated") is True
        assert "Norte" in text and "S-1" not in text and "Product S-1" not in text

    def test_scoped_status_failure_is_recorded_and_company_still_sent(
            self, people, mails, monkeypatch):
        from backend.inventory import service as inv_svc
        p = people
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp",
                            lambda *a, **kw: True)
        _stub_alert_inputs(monkeypatch, p["tid"], [_row("N-1", "Norte")])

        def boom(*a, **kw):
            raise RuntimeError("status broke")
        monkeypatch.setattr(inv_svc, "get_inventory_status_by_warehouse", boom)

        inv_svc.run_daily_inventory_alerts()

        assert p["admin"]["email"] in mails
        failed = _log(p["tid"], p["scoped_norte"]["id"], "inventory_alert_email")
        assert len(failed) == 1 and failed[0]["status"] == "failed"
        assert "status broke" in failed[0]["context"]["reason"]


class TestFreshnessReminder:
    def _freshness(self):
        return {
            "sales": {"age_days": 2, "state": "current"},
            "stock": {"age_days": 2, "state": "current"},
            "warehouses": {"items": [
                {"name": "Norte", "lagging": True, "silent_days": 20},
                {"name": "Sur", "lagging": False, "silent_days": 1},
            ]},
        }

    def test_scoped_reminder_names_only_their_silent_warehouse(self, people, mails, monkeypatch):
        from backend.notifications import freshness_service as fresh
        p = people
        monkeypatch.setattr(fresh, "_tenants_with_completed_sessions", lambda: [p["tid"]])
        monkeypatch.setattr(fresh, "get_tenant_freshness", lambda tid, now=None: self._freshness())
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp",
                            lambda *a, **kw: True)

        fresh.run_daily_freshness_reminders()

        subject, html = mails[p["scoped_norte"]["email"]]
        assert "Norte" in subject and "Norte (20 d)" in html
        assert "Sur" not in html
        assert p["scoped_sur_only_ok"]["email"] not in mails
        empty = _log(p["tid"], p["scoped_sur_only_ok"]["id"], "scoped_digest_empty")
        assert len(empty) == 1 and empty[0]["context"]["digest"] == "freshness_reminder"
        # The unscoped admin gets the company reminder, which names every silent warehouse.
        assert "Norte (20 d)" in mails[p["admin"]["email"]][1]
        assert p["inactive"]["email"] not in mails


class TestMonthlyRecapStaysCompanyOnly:
    def test_scoped_user_is_withheld_and_told(self, people, mails, monkeypatch):
        from backend.inventory import roi_service
        from backend.inventory import service as inv_svc
        p = people
        monkeypatch.setattr("backend.notifications.email.send_monthly_roi_email",
                            lambda **kw: True)
        monkeypatch.setattr(inv_svc, "get_tenants_with_active_sessions",
                            lambda: [{"tenant_id": p["tid"]}])
        monkeypatch.setattr(roi_service, "get_month_report",
                            lambda tid, y, m: {"has_sufficient_history": True})

        roi_service.run_monthly_roi_emails()

        for key in ("scoped_norte", "scoped_sur_only_ok", "scoped_none"):
            rows = _log(p["tid"], p[key]["id"], "company_digest_withheld")
            assert len(rows) == 1 and rows[0]["context"]["digest"] == "monthly_roi"
        assert _log(p["tid"], p["inactive_scoped"]["id"], "company_digest_withheld") == []
