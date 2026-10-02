"""
Purchase orders get a paid state, and the cash calendar says what it does not
know (math audit 2026-10-01, O3).

Before this, nothing ever took a sent PO off the payables calendar, so the
overdue total only grew and the affordability check eventually refused every
cart; a line with no unit cost was priced at 0, and an order whose lines were
ALL uncosted vanished from the calendar entirely — so `evaluate_purchase_fit`
answered "fits" about money nobody had priced.

Pins: the permission pair on both endpoints (state read back from the DB),
idempotency, the activity trail, that paid orders leave due/overdue, that
uncosted lines are counted instead of silently zeroed, that "fits" is never
claimed while costs are missing, that un-send refuses a paid order, and that
the tenant export carries the new columns.

POs are written with SQL rather than through `/log-po`: that endpoint is being
changed in parallel (duplicate-PO idempotency) and this file is about what
happens AFTER an order exists.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.db.connection import execute, query, query_one
from backend.inventory import cash_service as cash


def _supplier(tenant_id, terms="contado", days=0):
    name = f"Prov-{uuid4().hex[:6]}"
    execute(
        "INSERT INTO suppliers (tenant_id, name, payment_terms, payment_terms_days) "
        "VALUES (%s, %s, %s, %s)",
        (tenant_id, name, terms, days),
    )
    return name


def _po(tenant_id, supplier, lines, *, sent_days_ago=3):
    """`lines`: [(qty, unit_cost or None)]. Returns the PO id."""
    po = query_one(
        "INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (tenant_id, "sess-" + uuid4().hex[:8], len(lines), sum(q for q, _ in lines)),
    )
    for qty, cost in lines:
        execute(
            "INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, supplier, "
            "recommended_qty, final_qty, unit_cost, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'approved')",
            (po["id"], tenant_id, f"SKU-{uuid4().hex[:6]}", supplier, qty, qty, cost),
        )
    if sent_days_ago is not None:
        execute(
            "UPDATE inventory_po_log SET sent_at = %s WHERE id = %s",
            (datetime.now(timezone.utc) - timedelta(days=sent_days_ago), po["id"]),
        )
    return po["id"]


def _paid(po_id):
    return query_one("SELECT paid_at, paid_by FROM inventory_po_log WHERE id = %s", (po_id,))


def _events(tenant_id, action, po_id):
    return query(
        "SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s "
        "AND resource = %s", (tenant_id, action, po_id))


# ── The endpoints ────────────────────────────────────────────────────────────

class TestMarkPaidPermissions:

    def test_viewer_cannot_mark_paid(self, client, viewer_headers, test_tenant):
        po_id = _po(test_tenant["id"], _supplier(test_tenant["id"]), [(10, 5.0)])
        resp = client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=viewer_headers)
        assert resp.status_code == 403
        row = _paid(po_id)
        assert row["paid_at"] is None and row["paid_by"] is None
        assert _events(test_tenant["id"], "purchase.order_paid", po_id) == []

    def test_analyst_marks_paid_and_the_trail_is_written(
        self, client, analyst_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)])
        resp = client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["changed"] is True

        row = _paid(po_id)
        assert row["paid_at"] is not None
        assert row["paid_by"], "who marked it paid must be recorded"
        assert data["paid_by"] == row["paid_by"]

        events = _events(tid, "purchase.order_paid", po_id)
        assert len(events) == 1
        assert events[0]["context"]["severity"] == "info"
        assert events[0]["context"]["reference"]

    def test_viewer_cannot_mark_unpaid(self, client, analyst_headers, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)])
        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=analyst_headers)
        paid_before = _paid(po_id)

        resp = client.post(f"/api/v1/inventory/po/{po_id}/mark-unpaid", headers=viewer_headers)
        assert resp.status_code == 403
        assert _paid(po_id) == paid_before
        assert _events(tid, "purchase.order_unpaid", po_id) == []

    def test_analyst_marks_unpaid_and_it_reaches_the_bell(
        self, client, analyst_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)])
        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=analyst_headers)

        resp = client.post(f"/api/v1/inventory/po/{po_id}/mark-unpaid", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["changed"] is True
        row = _paid(po_id)
        assert row["paid_at"] is None and row["paid_by"] is None

        events = _events(tid, "purchase.order_unpaid", po_id)
        assert len(events) == 1
        assert events[0]["context"]["severity"] == "warning"
        assert events[0]["context"]["reason"] == "reversed_by_user"
        assert len(_events(tid, "purchase.order_paid", po_id)) == 1, (
            "the undo must not erase the record of the payment"
        )


class TestMarkPaidSemantics:

    def test_marking_paid_twice_keeps_the_first_date_and_writes_one_event(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)])
        first = client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        assert first.status_code == 200
        paid_first = _paid(po_id)["paid_at"]

        second = client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        assert second.status_code == 200, second.text
        assert second.json()["data"]["changed"] is False
        assert _paid(po_id)["paid_at"] == paid_first
        assert len(_events(tid, "purchase.order_paid", po_id)) == 1

    def test_marking_an_unpaid_order_unpaid_changes_nothing(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)])
        resp = client.post(f"/api/v1/inventory/po/{po_id}/mark-unpaid", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["changed"] is False
        assert _events(tid, "purchase.order_unpaid", po_id) == []

    def test_an_unsent_order_cannot_be_paid(self, client, auth_headers, test_tenant):
        """A draft was never invoiced and the calendar does not show it."""
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)], sent_days_ago=None)
        resp = client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_paid_requires_sent"
        assert _paid(po_id)["paid_at"] is None

    def test_another_tenants_order_is_404_and_untouched(
        self, client, auth_headers, test_tenant,
    ):
        from backend.tenants.service import create_tenant
        other_tenant = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        try:
            other = _po(other_tenant["id"], "X", [(1, 1.0)])
            resp = client.post(f"/api/v1/inventory/po/{other}/mark-paid", headers=auth_headers)
            assert resp.status_code == 404
            assert _paid(other)["paid_at"] is None
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other_tenant["id"],))

    def test_unsend_refuses_a_paid_order(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)])
        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        resp = client.post(f"/api/v1/inventory/po/{po_id}/unsend", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_unsend_after_payment"
        assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s",
                         (po_id,))["sent_at"] is not None

    def test_history_carries_the_paid_date(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)])
        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        rows = client.get("/api/v1/inventory/po-history?limit=100",
                          headers=auth_headers).json()["data"]
        mine = next(r for r in rows if r["id"] == po_id)
        assert mine["paid_at"] is not None


# ── The cash calendar ────────────────────────────────────────────────────────

class TestPaidOrdersLeaveTheCalendar:

    def test_a_paid_overdue_order_is_no_longer_overdue(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        name = _supplier(tid, "contado", 0)
        po_id = _po(tid, name, [(10, 80.0)], sent_days_ago=40)
        before = cash.get_payables(tid, 30)
        assert before["overdue_total"] == 800.0

        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        after = cash.get_payables(tid, 30)
        assert after["overdue_total"] == 0.0
        assert all(d["po_log_id"] != po_id for d in after["due_items"])

        # ...and the undo puts it back on its original due date.
        client.post(f"/api/v1/inventory/po/{po_id}/mark-unpaid", headers=auth_headers)
        again = cash.get_payables(tid, 30)
        assert again["overdue_total"] == 800.0
        assert again["due_items"][0]["days_until_due"] == -40

    def test_a_paid_order_no_longer_eats_the_budget(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        name = _supplier(tid, "contado", 0)
        po_id = _po(tid, name, [(10, 80.0)], sent_days_ago=1)
        line = [{"sku": "S1", "supplier_name": name, "quantity": 10, "unit_cost": 50.0}]
        assert cash.evaluate_purchase_fit(tid, line, 1000.0, 30)["fits"] is False

        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        fit = cash.evaluate_purchase_fit(tid, line, 1000.0, 30)
        assert fit["committed_total"] == 0.0
        assert fit["fits"] is True


class TestUncostedLinesAreReportedNotZeroed:

    def test_an_order_with_no_costs_at_all_is_still_on_the_calendar(self, test_tenant):
        """It was dropped (`amount <= 0: continue`), even from unknown_terms."""
        tid = test_tenant["id"]
        name = _supplier(tid, "30 dias", 30)
        po_id = _po(tid, name, [(10, None), (5, None)])
        result = cash.get_payables(tid, 30)
        item = next(d for d in result["due_items"] if d["po_log_id"] == po_id)
        assert item["amount"] == 0.0
        assert item["uncosted_lines"] == 2
        assert item["amount_complete"] is False
        assert result["uncosted_lines"] == 2
        assert result["uncosted_po_count"] == 1
        assert result["totals_complete"] is False

    def test_a_partly_costed_order_says_its_amount_is_incomplete(self, test_tenant):
        tid = test_tenant["id"]
        name = _supplier(tid, "30 dias", 30)
        _po(tid, name, [(10, 100.0), (5, None), (3, 0.0)])
        result = cash.get_payables(tid, 30)
        assert result["horizon_total"] == 1000.0
        assert result["due_items"][0]["uncosted_lines"] == 2, "a stored 0 is not a price"
        assert result["uncosted_lines_committed"] == 2

    def test_uncosted_lines_with_unknown_terms_are_kept_too(self, test_tenant):
        tid = test_tenant["id"]
        name = _supplier(tid, "a convenir", None)
        po_id = _po(tid, name, [(4, None)])
        result = cash.get_payables(tid, 30)
        assert [u["po_log_id"] for u in result["unknown_terms"]] == [po_id]
        assert result["unknown_terms"][0]["amount_complete"] is False
        assert result["uncosted_lines"] == 1

    def test_a_fully_costed_calendar_says_it_is_complete(self, test_tenant):
        tid = test_tenant["id"]
        _po(tid, _supplier(tid, "30 dias", 30), [(10, 100.0)])
        result = cash.get_payables(tid, 30)
        assert result["uncosted_lines"] == 0
        assert result["totals_complete"] is True

    def test_a_paid_uncosted_order_stops_counting_as_missing(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid, "30 dias", 30), [(10, None)])
        assert cash.get_payables(tid, 30)["uncosted_lines"] == 1
        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)
        assert cash.get_payables(tid, 30)["uncosted_lines"] == 0


class TestAffordabilityNeverClaimsAFitItCannotKnow:

    def test_an_uncosted_cart_line_makes_the_verdict_unknown(self, test_tenant):
        """The cart used to skip it and say "fits" about a ₡0 purchase."""
        tid = test_tenant["id"]
        name = _supplier(tid, "contado", 0)
        fit = cash.evaluate_purchase_fit(
            tid,
            [{"sku": "PRICED", "supplier_name": name, "quantity": 10, "unit_cost": 5.0},
             {"sku": "BLANK", "supplier_name": name, "quantity": 50, "unit_cost": None}],
            budget=1000.0, horizon_days=30,
        )
        assert fit["fits"] is None
        assert fit["fits_unknown_reason"] == "missing_costs"
        assert fit["uncosted_purchase_lines"] == 1
        assert fit["uncosted_purchase_skus"] == ["BLANK"]
        assert fit["total_complete"] is False

    def test_an_uncosted_committed_order_makes_the_verdict_unknown(self, test_tenant):
        tid = test_tenant["id"]
        name = _supplier(tid, "contado", 0)
        _po(tid, name, [(10, None)], sent_days_ago=1)
        fit = cash.evaluate_purchase_fit(
            tid, [{"sku": "S", "supplier_name": name, "quantity": 1, "unit_cost": 5.0}],
            budget=1000.0, horizon_days=30,
        )
        assert fit["fits"] is None
        assert fit["fits_unknown_reason"] == "missing_costs"
        assert fit["uncosted_committed_lines"] == 1

    def test_over_budget_is_still_a_sound_no_with_costs_missing(self, test_tenant):
        """A missing cost can only add to what is required."""
        tid = test_tenant["id"]
        name = _supplier(tid, "contado", 0)
        fit = cash.evaluate_purchase_fit(
            tid,
            [{"sku": "PRICED", "supplier_name": name, "quantity": 10, "unit_cost": 500.0},
             {"sku": "BLANK", "supplier_name": name, "quantity": 5, "unit_cost": None}],
            budget=1000.0, horizon_days=30,
        )
        assert fit["fits"] is False
        assert fit["shortfall"] == 4000.0

    def test_a_complete_cart_under_budget_still_fits(self, test_tenant):
        tid = test_tenant["id"]
        name = _supplier(tid, "contado", 0)
        fit = cash.evaluate_purchase_fit(
            tid, [{"sku": "S", "supplier_name": name, "quantity": 10, "unit_cost": 5.0}],
            budget=1000.0, horizon_days=30,
        )
        assert fit["fits"] is True
        assert fit["fits_unknown_reason"] is None
        assert fit["total_complete"] is True


# ── Tenant data ─────────────────────────────────────────────────────────────

class TestTenantExportCarriesThePaymentColumns:

    def test_export_includes_paid_at_and_paid_by(self, client, auth_headers, test_tenant):
        """The export selects `*`, so the columns ride along — pinned here so
        a future explicit column list cannot drop who paid what."""
        import io
        import json
        import zipfile
        from backend.tenants.data_export import build_export_zip
        tid = test_tenant["id"]
        po_id = _po(tid, _supplier(tid), [(10, 5.0)])
        client.post(f"/api/v1/inventory/po/{po_id}/mark-paid", headers=auth_headers)

        with zipfile.ZipFile(io.BytesIO(build_export_zip(tid))) as zf:
            rows = json.loads(zf.read("inventory_po_log.json"))
        mine = next(r for r in rows if r["id"] == po_id)
        assert mine["paid_at"] is not None
        assert mine["paid_by"]
