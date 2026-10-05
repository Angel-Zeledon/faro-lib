"""Who the scheduled e-mails go to: active users, and company totals only to
users who may see the whole company.

Before this, the daily inventory alert, the monthly recap and the freshness
reminder went to every admin/analyst row: a user limited to one warehouse got
by e-mail the company totals the screens refuse them
(`warehouse_scope_company_totals`), and a deactivated or suspended user kept
receiving stock and money figures after losing access to the app.

A scoped user is now WITHHELD from company-wide digests and told so in their
own activity log (`company_digest_withheld`): a missing e-mail must not read as
"nothing to report". The supplier lead-time alert is supplier-level (no stock,
no warehouse names — what the scorecard shows them on screen), so a scoped user
keeps getting it; a deactivated one does not.

Every assertion reads what was sent (captured transport) and `activity_logs`.
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


@pytest.fixture
def people(test_tenant):
    tid = test_tenant["id"]
    execute("INSERT INTO warehouses (tenant_id, name, is_default) VALUES (%s, 'Norte', false)",
            (tid,))
    wh_id = query("SELECT id FROM warehouses WHERE tenant_id = %s AND name = 'Norte'", (tid,))[0]["id"]
    return {
        "tid": tid,
        "admin": _user(tid, "admin", whatsapp="+50670000001"),
        "analyst": _user(tid, "analyst"),
        "scoped": _user(tid, "analyst", scope=[wh_id], whatsapp="+50670000002"),
        "inactive": _user(tid, "analyst", status="inactive", whatsapp="+50670000003"),
        "suspended": _user(tid, "admin", status="suspended"),
        "viewer": _user(tid, "viewer"),
    }


def _withheld(tid):
    return query(
        "SELECT user_id, context FROM activity_logs WHERE tenant_id = %s AND action = %s",
        (tid, "company_digest_withheld"),
    )


class TestRecipientLists:
    def test_company_digest_lists_hold_only_active_unscoped(self, people):
        from backend.inventory import service as inv_svc
        p = people
        emails = set(inv_svc.get_tenant_admin_emails(p["tid"]))
        assert emails == {p["admin"]["email"], p["analyst"]["email"]}

        numbers = set(inv_svc.get_tenant_admin_whatsapps(p["tid"]))
        assert numbers == {"+50670000001"}

        ids = {r["id"] for r in inv_svc.get_tenant_alert_recipients(p["tid"])}
        assert ids == {p["admin"]["id"], p["analyst"]["id"]}

    def test_supplier_level_list_adds_scoped_but_never_inactive(self, people):
        from backend.inventory import service as inv_svc
        p = people
        ids = {r["id"] for r in inv_svc.get_tenant_alert_recipients(p["tid"], include_scoped=True)}
        assert ids == {p["admin"]["id"], p["analyst"]["id"], p["scoped"]["id"]}

    def test_scoped_list(self, people):
        from backend.inventory import service as inv_svc
        p = people
        assert {r["id"] for r in inv_svc.get_scoped_alert_recipients(p["tid"])} == {p["scoped"]["id"]}

    def test_a_json_null_scope_is_company_wide(self, people):
        """`warehouse_scope` NULL and JSON null both mean every warehouse
        (auth/warehouse_scope.py reads both as None)."""
        from backend.inventory import service as inv_svc
        p = people
        execute("UPDATE users SET warehouse_scope = 'null'::jsonb WHERE id = %s", (p["analyst"]["id"],))
        assert p["analyst"]["email"] in inv_svc.get_tenant_admin_emails(p["tid"])


class TestDailyAlert:
    def test_only_active_unscoped_receive_and_scoped_is_told(self, people, monkeypatch):
        from backend.db import session_store
        from backend.inventory import service as inv_svc
        from backend.sessions import planning_service
        p = people

        sent_to: list[str] = []
        wa_to: list[str] = []
        monkeypatch.setattr("backend.notifications.email.send_inventory_alert_email",
                            lambda **kw: sent_to.append(kw["to"]) or True)
        monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp",
                            lambda number, *a, **kw: wa_to.append(number) or True)
        monkeypatch.setattr(inv_svc, "get_tenants_with_active_sessions",
                            lambda: [{"tenant_id": p["tid"]}])
        monkeypatch.setattr(planning_service, "resolve_active_session", lambda t: "sess-test")
        monkeypatch.setattr(session_store, "get_forecasts", lambda t, s: {})
        monkeypatch.setattr(inv_svc, "list_stock", lambda t, **kw: [])
        monkeypatch.setattr(inv_svc, "get_learned_lead_times", lambda t: {})
        monkeypatch.setattr(
            inv_svc, "_compute_inventory_status",
            lambda *a, **kw: [{"sku": "SKU-1", "signal": "PEDIR_YA", "coverage_days": 1.0,
                               "recommended_qty": 10, "display_name": "P1", "supplier": "Acme"}],
        )

        inv_svc.run_daily_inventory_alerts()

        assert sorted(sent_to) == sorted([p["admin"]["email"], p["analyst"]["email"]])
        assert wa_to == ["+50670000001"], "a scoped or inactive number got the company digest"

        withheld = _withheld(p["tid"])
        assert [w["user_id"] for w in withheld] == [p["scoped"]["id"]]
        assert withheld[0]["context"] == {"digest": "inventory_alert", "reason": "warehouse_scope"}

        delivered_to = {r["user_id"] for r in query(
            "SELECT user_id FROM activity_logs WHERE tenant_id = %s AND action = 'inventory_alert_email'",
            (p["tid"],))}
        assert delivered_to == {p["admin"]["id"], p["analyst"]["id"]}


class TestMonthlyRecap:
    def test_only_active_unscoped_receive_and_scoped_is_told(self, people, monkeypatch):
        from backend.inventory import roi_service
        from backend.inventory import service as inv_svc
        p = people

        sent_to: list[str] = []
        monkeypatch.setattr("backend.notifications.email.send_monthly_roi_email",
                            lambda **kw: sent_to.append(kw["to"]) or True)
        monkeypatch.setattr(inv_svc, "get_tenants_with_active_sessions",
                            lambda: [{"tenant_id": p["tid"]}])
        monkeypatch.setattr(roi_service, "get_month_report",
                            lambda tid, y, m: {"has_sufficient_history": True})

        assert roi_service.run_monthly_roi_emails() == 1

        assert sorted(sent_to) == sorted([p["admin"]["email"], p["analyst"]["email"]])
        withheld = _withheld(p["tid"])
        assert [w["user_id"] for w in withheld] == [p["scoped"]["id"]]
        assert withheld[0]["context"]["digest"] == "monthly_roi"


class TestSupplierLeadTimeAlert:
    def test_scoped_receives_inactive_does_not(self, people, monkeypatch):
        from backend.inventory import supplier_health_service as sh
        p = people

        sent_to: list[str] = []
        monkeypatch.setattr("backend.notifications.email.send_supplier_lead_time_alert_email",
                            lambda **kw: sent_to.append(kw["to"]) or True)
        monkeypatch.setattr(sh, "query", lambda sql, *a, **kw: [{"tenant_id": p["tid"]}])
        monkeypatch.setattr(sh, "get_lead_time_deviations",
                            lambda tid: [{"supplier": "Acme", "lead_time_days": 12}])

        sh.run_daily_supplier_lead_time_alerts()

        assert sorted(sent_to) == sorted(
            [p["admin"]["email"], p["analyst"]["email"], p["scoped"]["email"]])
        assert _withheld(p["tid"]) == []


class TestFreshnessReminder:
    def test_recipients_are_active_and_unscoped(self, people):
        from backend.notifications import freshness_service as fresh
        p = people
        assert {r["id"] for r in fresh._recipients(p["tid"])} == {p["admin"]["id"], p["analyst"]["id"]}
