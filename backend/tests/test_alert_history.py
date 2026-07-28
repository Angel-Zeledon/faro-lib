"""
The alert bell: `GET /alerts` + `POST /alerts/read`.

The daily loop already writes one activity_logs row per alert per recipient
(see test_notification_delivery_honesty.py). Until now nothing read them back,
so the only copy of a stockout digest was the email. These tests pin the four
properties that make the read side worth having:

1. What the loop wrote is what the bell shows — arranged by running the real
   `run_daily_inventory_alerts()`, not by hand-inserting rows.
2. A send that FAILED is present and labelled, never omitted.
3. Unread is derived from the user's own marker row, and marking read is a
   mutation with a proper permission pair (viewer denied + state unchanged).
4. Tenant A cannot see tenant B's alerts.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.notifications import alert_history


# ── Arrangement helpers ───────────────────────────────────────────────────────

def _critical(n: int) -> list[dict]:
    return [
        {"sku": f"SKU-{i:03d}", "display_name": f"Producto {i}", "signal": "PEDIR_YA",
         "coverage_days": 1.0, "recommended_qty": 100 + i, "supplier": "Acme"}
        for i in range(n)
    ]


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
    monkeypatch.setattr(inv_svc, "_compute_inventory_status", lambda *a, **kw: items)


def _seed(tenant_id: str, user_id: str, action: str, status: str,
          context: dict, minutes_ago: int = 0) -> str:
    """Insert one delivery row directly, with control over its age.

    Used only where the age itself is the thing under test (fan-out grouping,
    ordering, unread). The "what the loop writes" contract is covered by the
    tests that run the real loop.
    """
    from backend.db.connection import _json
    from backend.utils.ids import generate_id
    row_id = generate_id("act")
    execute(
        """INSERT INTO activity_logs (id, tenant_id, user_id, action, context, status, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (row_id, tenant_id, user_id, action, _json(context), status,
         datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)),
    )
    return row_id


def _get(client, headers, limit: int | None = None):
    path = "/api/v1/alerts" + (f"?limit={limit}" if limit else "")
    resp = client.get(path, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ── 1. The loop's rows reach the bell ─────────────────────────────────────────

class TestTheBellShowsWhatTheLoopSent:
    def test_a_delivered_stockout_digest_appears_with_its_numbers(
        self, client, auth_headers, monkeypatch, registered_user, test_tenant,
    ):
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        _arrange_daily_loop(monkeypatch, tid, _critical(47))
        monkeypatch.setattr(
            "backend.notifications.email.send_inventory_alert_email", lambda **kw: True)

        inv_svc.run_daily_inventory_alerts()

        # The row the loop wrote — asserted directly, not via the response.
        stored = query(
            """SELECT status, context FROM activity_logs
               WHERE tenant_id = %s AND action = 'inventory_alert_email'""",
            (tid,),
        )
        assert len(stored) == 1 and stored[0]["status"] == "success"

        data = _get(client, auth_headers)
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["kind"] == "stockout_digest"
        assert item["status"] == "delivered"
        assert item["channel"] == "email"
        assert item["delivered_count"] == 1
        assert item["failed_count"] == 0
        assert item["failure_reason"] is None
        assert item["details"] == {"critical": 47, "warning": 0}

    def test_an_empty_tenant_has_an_empty_history(self, client, auth_headers):
        data = _get(client, auth_headers)
        assert data["items"] == []
        assert data["unread_count"] == 0

    def test_foreground_actions_are_not_alerts(
        self, client, auth_headers, registered_user, test_tenant,
    ):
        """A PO send reports its own outcome in the click that caused it; the
        bell is for what Faro sent while nobody was looking."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _seed(tid, uid, "po_sent_to_suppliers", "failed", {"delivered": []})
        _seed(tid, uid, "session.delete", "success", {})

        assert _get(client, auth_headers)["items"] == []


# ── 2. Failed sends are visible ───────────────────────────────────────────────

class TestFailedSendsAreVisible:
    def test_a_failed_stockout_digest_is_listed_with_its_reason(
        self, client, auth_headers, monkeypatch, registered_user, test_tenant,
    ):
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        _arrange_daily_loop(monkeypatch, tid, _critical(5))
        monkeypatch.setattr(
            "backend.notifications.email.send_inventory_alert_email", lambda **kw: False)
        monkeypatch.setattr("backend.notifications.email.is_configured", lambda: True)

        inv_svc.run_daily_inventory_alerts()

        item = _get(client, auth_headers)["items"][0]
        assert item["status"] == "failed", "an alert that never arrived must not read as sent"
        assert item["failure_reason"] == "transport_error"
        assert item["delivered_count"] == 0
        assert item["failed_count"] == 1

    def test_a_failed_freshness_reminder_is_listed(
        self, client, auth_headers, registered_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _seed(tid, uid, "data_freshness_reminder_email", "failed",
              {"channel": "email", "recipient": "a@b.co",
               "sales_age_days": 40, "stock_age_days": 25, "reason": "not_configured"})

        item = _get(client, auth_headers)["items"][0]
        assert item["kind"] == "data_freshness"
        assert item["status"] == "failed"
        assert item["failure_reason"] == "not_configured"
        assert item["details"] == {"sales_age_days": 40, "stock_age_days": 25}

    def test_a_partial_fanout_is_neither_delivered_nor_failed(
        self, client, auth_headers, registered_user, test_tenant,
    ):
        """Two admins, one delivery failed. Rounding this to 'delivered' is the
        silence the delivery-honesty work exists to prevent."""
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _seed(tid, uid, "inventory_alert_email", "success",
              {"channel": "email", "critical": 3, "warning": 1}, minutes_ago=1)
        _seed(tid, uid, "inventory_alert_email", "failed",
              {"channel": "email", "critical": 3, "warning": 1,
               "reason": "transport_error"}, minutes_ago=1)

        item = _get(client, auth_headers)["items"][0]
        assert item["status"] == "partial"
        assert item["delivered_count"] == 1
        assert item["failed_count"] == 1
        assert item["failure_reason"] == "transport_error"


# ── 3. Grouping and ordering ──────────────────────────────────────────────────

class TestFanOutGrouping:
    def test_email_and_whatsapp_of_one_run_are_one_entry(
        self, client, auth_headers, registered_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        ctx = {"critical": 4, "warning": 2}
        _seed(tid, uid, "inventory_alert_email", "success", {**ctx, "channel": "email"})
        _seed(tid, uid, "inventory_alert_whatsapp", "success", {**ctx, "channel": "whatsapp"})

        items = _get(client, auth_headers)["items"]
        assert len(items) == 1, "one alert over two channels is one event"
        assert items[0]["channel"] == "mixed"
        assert items[0]["delivered_count"] == 2

    def test_two_runs_a_day_apart_stay_two_entries(
        self, client, auth_headers, registered_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _seed(tid, uid, "inventory_alert_email", "success",
              {"critical": 2, "warning": 0}, minutes_ago=0)
        _seed(tid, uid, "inventory_alert_email", "success",
              {"critical": 9, "warning": 0}, minutes_ago=60 * 24)

        items = _get(client, auth_headers)["items"]
        assert len(items) == 2
        # Newest first, each keeping its own numbers.
        assert items[0]["details"]["critical"] == 2
        assert items[1]["details"]["critical"] == 9

    def test_different_kinds_at_the_same_moment_stay_separate(
        self, client, auth_headers, registered_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _seed(tid, uid, "inventory_alert_email", "success", {"critical": 1, "warning": 0})
        _seed(tid, uid, "supplier_lead_time_alert_email", "success", {"suppliers": 3})

        kinds = {i["kind"] for i in _get(client, auth_headers)["items"]}
        assert kinds == {"stockout_digest", "supplier_lead_time"}

    def test_limit_caps_the_number_of_entries(
        self, client, auth_headers, registered_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        for i in range(5):
            _seed(tid, uid, "inventory_alert_email", "success",
                  {"critical": i, "warning": 0}, minutes_ago=i * 60 * 24)

        assert len(_get(client, auth_headers, limit=3)["items"]) == 3
        assert len(_get(client, auth_headers)["items"]) == 5

    def test_limit_is_validated(self, client, auth_headers):
        assert client.get("/api/v1/alerts?limit=0", headers=auth_headers).status_code == 422
        assert client.get("/api/v1/alerts?limit=101", headers=auth_headers).status_code == 422


# ── 4. Unread + the mark-read permission pair ─────────────────────────────────

class TestUnreadIsDerivedNotInvented:
    def test_everything_is_unread_before_the_first_read(
        self, client, auth_headers, registered_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], registered_user["user"]["id"]
        _seed(tid, uid, "inventory_alert_email", "success",
              {"critical": 1, "warning": 0}, minutes_ago=0)
        _seed(tid, uid, "monthly_roi_email", "success",
              {"month": "2026-06"}, minutes_ago=60 * 24)

        data = _get(client, auth_headers)
        assert data["last_read_at"] is None
        assert data["unread_count"] == 2
        assert all(i["unread"] for i in data["items"])

    def test_marking_read_clears_the_count_and_writes_the_marker(
        self, client, analyst_headers, analyst_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], analyst_user["user"]["id"]
        _seed(tid, uid, "inventory_alert_email", "success", {"critical": 1, "warning": 0})

        resp = client.post("/api/v1/alerts/read", headers=analyst_headers)
        assert resp.status_code == 200, resp.text

        # State, from the DB — not the response echo.
        row = query_one(
            """SELECT id FROM activity_logs
               WHERE tenant_id = %s AND user_id = %s AND action = %s""",
            (tid, uid, alert_history.MARK_READ_ACTION),
        )
        assert row is not None, "marking read left no marker, so unread cannot be derived"

        data = _get(client, analyst_headers)
        assert data["unread_count"] == 0
        assert data["last_read_at"] is not None
        assert all(not i["unread"] for i in data["items"])

    def test_an_alert_arriving_after_the_marker_is_unread_again(
        self, client, analyst_headers, analyst_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], analyst_user["user"]["id"]
        _seed(tid, uid, "inventory_alert_email", "success",
              {"critical": 1, "warning": 0}, minutes_ago=60 * 24)

        assert client.post("/api/v1/alerts/read", headers=analyst_headers).status_code == 200
        assert _get(client, analyst_headers)["unread_count"] == 0

        _seed(tid, uid, "supplier_lead_time_alert_email", "success", {"suppliers": 2})
        data = _get(client, analyst_headers)
        assert data["unread_count"] == 1
        assert data["items"][0]["kind"] == "supplier_lead_time"
        assert data["items"][0]["unread"] is True
        assert data["items"][1]["unread"] is False

    def test_the_marker_is_per_user(
        self, client, auth_headers, analyst_headers, registered_user, analyst_user, test_tenant,
    ):
        """Two admins read their alerts at different times; one clearing the
        badge must not clear the other's."""
        tid = test_tenant["id"]
        _seed(tid, registered_user["user"]["id"], "inventory_alert_email", "success",
              {"critical": 1, "warning": 0})

        assert client.post("/api/v1/alerts/read", headers=analyst_headers).status_code == 200
        assert _get(client, analyst_headers)["unread_count"] == 0
        assert _get(client, auth_headers)["unread_count"] == 1

    def test_the_read_marker_is_not_itself_an_alert(
        self, client, analyst_headers, test_tenant,
    ):
        assert client.post("/api/v1/alerts/read", headers=analyst_headers).status_code == 200
        assert _get(client, analyst_headers)["items"] == []


class TestMarkReadPermissionPair:
    def test_viewer_is_denied_and_no_marker_is_written(
        self, client, viewer_headers, viewer_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], viewer_user["user"]["id"]
        resp = client.post("/api/v1/alerts/read", headers=viewer_headers)
        assert resp.status_code == 403

        assert query_one(
            """SELECT id FROM activity_logs
               WHERE tenant_id = %s AND user_id = %s AND action = %s""",
            (tid, uid, alert_history.MARK_READ_ACTION),
        ) is None, "a denied request still wrote state"

    def test_analyst_succeeds_and_the_marker_exists(
        self, client, analyst_headers, analyst_user, test_tenant,
    ):
        tid, uid = test_tenant["id"], analyst_user["user"]["id"]
        assert client.post("/api/v1/alerts/read", headers=analyst_headers).status_code == 200
        assert query_one(
            """SELECT id FROM activity_logs
               WHERE tenant_id = %s AND user_id = %s AND action = %s""",
            (tid, uid, alert_history.MARK_READ_ACTION),
        ) is not None

    def test_reading_the_history_needs_a_token(self, client):
        assert client.get("/api/v1/alerts").status_code in (401, 403)

    def test_a_viewer_may_still_read_the_history(
        self, client, viewer_headers, registered_user, test_tenant,
    ):
        """Denying the write must not deny the read."""
        _seed(test_tenant["id"], registered_user["user"]["id"],
              "inventory_alert_email", "success", {"critical": 6, "warning": 0})
        assert len(_get(client, viewer_headers)["items"]) == 1


# ── 5. Cross-tenant isolation ─────────────────────────────────────────────────

class TestTenantIsolation:
    def test_tenant_a_never_sees_tenant_b_alerts(
        self, client, auth_headers, registered_user, test_tenant, make_tenant_user_headers,
    ):
        other_headers, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        other_uid = query_one(
            "SELECT id FROM users WHERE tenant_id = %s LIMIT 1", (other_tid,))["id"]

        _seed(test_tenant["id"], registered_user["user"]["id"],
              "inventory_alert_email", "success", {"critical": 11, "warning": 0})
        _seed(other_tid, other_uid,
              "inventory_alert_email", "failed",
              {"critical": 99, "warning": 0, "reason": "not_configured"})

        mine = _get(client, auth_headers)["items"]
        theirs = _get(client, other_headers)["items"]

        assert len(mine) == 1 and mine[0]["details"]["critical"] == 11
        assert len(theirs) == 1 and theirs[0]["details"]["critical"] == 99
        assert mine[0]["id"] != theirs[0]["id"]

    def test_a_read_marker_does_not_cross_tenants(
        self, client, auth_headers, registered_user, test_tenant, make_tenant_user_headers,
    ):
        other_headers, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        other_uid = query_one(
            "SELECT id FROM users WHERE tenant_id = %s LIMIT 1", (other_tid,))["id"]

        _seed(test_tenant["id"], registered_user["user"]["id"],
              "inventory_alert_email", "success", {"critical": 1, "warning": 0})
        _seed(other_tid, other_uid, "inventory_alert_email", "success",
              {"critical": 2, "warning": 0})

        assert client.post("/api/v1/alerts/read", headers=other_headers).status_code == 200
        assert _get(client, other_headers)["unread_count"] == 0
        assert _get(client, auth_headers)["unread_count"] == 1


# ── 6. Unit-level guards on the service ───────────────────────────────────────

class TestServiceContract:
    def test_every_alert_action_the_loops_write_is_mapped(self):
        """A new alert action that nobody maps would silently never appear in
        the bell. Pin the set against the constants the senders use."""
        from backend.notifications.freshness_service import (
            REMINDER_EMAIL_ACTION, REMINDER_WHATSAPP_ACTION,
        )
        for action in (REMINDER_EMAIL_ACTION, REMINDER_WHATSAPP_ACTION,
                       "inventory_alert_email", "inventory_alert_whatsapp",
                       "supplier_lead_time_alert_email", "monthly_roi_email"):
            assert action in alert_history.ALERT_ACTIONS, action

    def test_the_read_marker_is_never_an_alert_action(self):
        assert alert_history.MARK_READ_ACTION not in alert_history.ALERT_ACTIONS

    def test_details_only_expose_whitelisted_keys(self):
        """The recipient's address is not part of "what was this about"."""
        entry = alert_history._details("stockout_digest", {
            "critical": 3, "warning": 1, "recipient": "boss@acme.cr", "channel": "email",
        })
        assert entry == {"critical": 3, "warning": 1}
