"""
Cancelling a purchase order, and reopening it (owner's decision, 2026-10-01).

Before: an abandoned order had no way out — it counted as "on the way" until
somebody received it (holding every recommendation for its SKUs down by its
units), sat on the overdue list and on the payables calendar for good.

Pins: permission pair with DB read-back, the two refusals (goods received,
order paid), idempotency, the activity trail, netting before/after cancel
through the ONE rule (`get_incoming_detail`) and the semáforo that reads it,
the overdue list, the calendar, the guards on receive/send/mark-paid, and that
reopening restores all of it.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.db.connection import execute, query, query_one
from backend.inventory import cash_service as cash
from backend.inventory import reception_service as rec_svc
from backend.inventory import service as inv_svc


def _po(tenant_id, sku, qty, *, supplier="Prov C", sent_days_ago=3, cost=5.0,
        generated_days_ago=0):
    po = query_one(
        "INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units, "
        "generated_at) VALUES (%s, %s, 1, %s, %s) RETURNING id",
        (tenant_id, "sess-" + uuid4().hex[:8], qty,
         datetime.now(timezone.utc) - timedelta(days=generated_days_ago)),
    )
    execute(
        "INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, supplier, "
        "recommended_qty, final_qty, unit_cost, status, warehouse) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'approved', 'principal')",
        (po["id"], tenant_id, sku, supplier, qty, qty, cost),
    )
    if sent_days_ago is not None:
        execute("UPDATE inventory_po_log SET sent_at = %s WHERE id = %s",
                (datetime.now(timezone.utc) - timedelta(days=sent_days_ago), po["id"]))
    return po["id"]


def _row(po_id):
    return query_one("SELECT cancelled_at, cancelled_by, cancel_reason, paid_at "
                     "FROM inventory_po_log WHERE id = %s", (po_id,))


def _incoming(tid, sku):
    return inv_svc.get_incoming_qty(tid).get((sku, "principal"), 0.0)


def _events(tid, action, po_id):
    return query("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s "
                 "AND resource = %s", (tid, action, po_id))


class TestPermissions:

    def test_viewer_cannot_cancel(self, client, viewer_headers, test_tenant):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 50)
        resp = client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=viewer_headers)
        assert resp.status_code == 403
        assert _row(po_id)["cancelled_at"] is None
        assert _incoming(tid, sku) == 50
        assert _events(tid, "purchase.order_cancelled", po_id) == []

    def test_analyst_cancels_with_a_reason_and_the_trail_is_written(
        self, client, analyst_headers, test_tenant,
    ):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 50)
        resp = client.post(f"/api/v1/inventory/po/{po_id}/cancel",
                           json={"reason": "  supplier out of stock  "}, headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["changed"] is True
        row = _row(po_id)
        assert row["cancelled_at"] is not None
        assert row["cancelled_by"]
        assert row["cancel_reason"] == "supplier out of stock"
        ev = _events(tid, "purchase.order_cancelled", po_id)
        assert len(ev) == 1
        assert ev[0]["context"]["severity"] == "warning"
        assert ev[0]["context"]["reason"] == "cancelled_by_user"
        assert ev[0]["context"]["cancel_reason"] == "supplier out of stock"

    def test_viewer_cannot_uncancel(self, client, analyst_headers, viewer_headers, test_tenant):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 50)
        client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=analyst_headers)
        before = _row(po_id)
        resp = client.post(f"/api/v1/inventory/po/{po_id}/uncancel", headers=viewer_headers)
        assert resp.status_code == 403
        assert _row(po_id) == before
        assert _incoming(tid, sku) == 0

    def test_analyst_uncancels_and_everything_comes_back(
        self, client, analyst_headers, test_tenant,
    ):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 50)
        client.post(f"/api/v1/inventory/po/{po_id}/cancel", json={"reason": "x"},
                    headers=analyst_headers)
        resp = client.post(f"/api/v1/inventory/po/{po_id}/uncancel", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["changed"] is True
        row = _row(po_id)
        assert row["cancelled_at"] is None and row["cancelled_by"] is None
        assert row["cancel_reason"] is None
        assert _incoming(tid, sku) == 50
        ev = _events(tid, "purchase.order_uncancelled", po_id)
        assert len(ev) == 1 and ev[0]["context"]["reason"] == "reversed_by_user"
        assert len(_events(tid, "purchase.order_cancelled", po_id)) == 1


class TestRefusals:

    def test_a_partly_received_order_cannot_be_cancelled(self, client, auth_headers, test_tenant):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "warehouse": "principal"})
        po_id = _po(tid, sku, 50)
        recv = client.post(f"/api/v1/inventory/po/{po_id}/receive",
                           json={"lines": [{"sku": sku, "received_qty": 20}]},
                           headers=auth_headers)
        assert recv.status_code == 200, recv.text
        resp = client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_cancel_after_reception"
        assert _row(po_id)["cancelled_at"] is None
        assert _incoming(tid, sku) == 30, "the 30 still owed keep counting"

    def test_a_paid_order_cannot_be_cancelled(self, client, auth_headers, test_tenant):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 50)
        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        resp = client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_cancel_after_payment"
        assert _row(po_id)["cancelled_at"] is None

    def test_cancel_twice_keeps_the_first_and_writes_one_event(
        self, client, auth_headers, test_tenant,
    ):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 50)
        client.post(f"/api/v1/inventory/po/{po_id}/cancel", json={"reason": "first"},
                    headers=auth_headers)
        first = _row(po_id)
        again = client.post(f"/api/v1/inventory/po/{po_id}/cancel", json={"reason": "second"},
                            headers=auth_headers)
        assert again.status_code == 200
        assert again.json()["data"]["changed"] is False
        assert _row(po_id) == first
        assert len(_events(tid, "purchase.order_cancelled", po_id)) == 1

    def test_uncancel_of_an_open_order_changes_nothing(self, client, auth_headers, test_tenant):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 50)
        resp = client.post(f"/api/v1/inventory/po/{po_id}/uncancel", headers=auth_headers)
        assert resp.status_code == 200 and resp.json()["data"]["changed"] is False
        assert _events(tid, "purchase.order_uncancelled", po_id) == []

    def test_a_cancelled_order_cannot_be_received_sent_or_paid(
        self, client, auth_headers, test_tenant,
    ):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 7, "warehouse": "principal"})
        po_id = _po(tid, sku, 50)
        client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=auth_headers)

        recv = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=auth_headers)
        assert recv.status_code == 409 and recv.json()["error_code"] == "po_cancelled"
        assert inv_svc.get_stock(tid, sku, warehouse="principal")["current_stock"] == 7

        send = client.post(f"/api/v1/inventory/po/{po_id}/send", headers=auth_headers)
        assert send.status_code == 409 and send.json()["error_code"] == "po_cancelled"

        paid = client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        assert paid.status_code == 409 and paid.json()["error_code"] == "po_cancelled"
        assert _row(po_id)["paid_at"] is None

    def test_another_tenants_order_is_404(self, client, auth_headers):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        try:
            po_id = _po(other["id"], "X", 5)
            resp = client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=auth_headers)
            assert resp.status_code == 404
            assert _row(po_id)["cancelled_at"] is None
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


class TestEveryReaderFollows:

    def test_netting_before_and_after_cancel(
        self, client, auth_headers, test_tenant, test_session,
    ):
        """The Panel's quantity, through the one "on the way" rule."""
        from backend.db import session_store
        tid, sid, sku = test_tenant["id"], test_session["id"], f"C-{uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "lead_time_days": 10,
                                        "unit_cost": 5.0, "moq": 1,
                                        "warehouse": "principal"})
        session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [
            {"date": f"2026-01-{i + 1:02d}", "value": 10.0, "lower": 10.0, "upper": 10.0}
            for i in range(20)]}}})

        def panel():
            return next(r for r in inv_svc.get_inventory_status(tid, sid)
                        if r["sku"] == sku)["recommended_qty"]

        without = panel()
        po_id = _po(tid, sku, 60)
        with_order = panel()
        assert with_order == without - 60

        client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=auth_headers)
        assert _incoming(tid, sku) == 0
        assert panel() == without, "a cancelled order no longer holds the quantity down"

        client.post(f"/api/v1/inventory/po/{po_id}/uncancel", headers=auth_headers)
        assert panel() == with_order

    def test_the_overdue_list_drops_it(self, client, auth_headers, test_tenant):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 50, generated_days_ago=60)
        assert any(r["po_log_id"] == po_id for r in rec_svc.get_overdue_receptions(tid))
        client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=auth_headers)
        assert all(r["po_log_id"] != po_id for r in rec_svc.get_overdue_receptions(tid))
        client.post(f"/api/v1/inventory/po/{po_id}/uncancel", headers=auth_headers)
        assert any(r["po_log_id"] == po_id for r in rec_svc.get_overdue_receptions(tid))

    def test_the_cash_calendar_drops_it(self, client, auth_headers, test_tenant):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        supplier = f"Prov-{uuid4().hex[:6]}"
        execute("INSERT INTO suppliers (tenant_id, name, payment_terms, payment_terms_days) "
                "VALUES (%s, %s, 'contado', 0)", (tid, supplier))
        po_id = _po(tid, sku, 10, supplier=supplier, cost=80.0, sent_days_ago=5)
        assert cash.get_payables(tid, 30)["overdue_total"] == 800.0
        client.post(f"/api/v1/inventory/po/{po_id}/cancel", headers=auth_headers)
        assert cash.get_payables(tid, 30)["overdue_total"] == 0.0
        client.post(f"/api/v1/inventory/po/{po_id}/uncancel", headers=auth_headers)
        assert cash.get_payables(tid, 30)["overdue_total"] == 800.0

    def test_history_carries_it(self, client, auth_headers, test_tenant):
        tid, sku = test_tenant["id"], f"C-{uuid4().hex[:6]}"
        po_id = _po(tid, sku, 5)
        client.post(f"/api/v1/inventory/po/{po_id}/cancel", json={"reason": "dup"},
                    headers=auth_headers)
        rows = client.get("/api/v1/inventory/po-history?limit=100",
                          headers=auth_headers).json()["data"]
        mine = next(r for r in rows if r["id"] == po_id)
        assert mine["cancelled_at"] is not None and mine["cancel_reason"] == "dup"
