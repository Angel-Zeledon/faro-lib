"""
The inverses of receive_po and mark_po_sent — docs/assistant-actions.md
section 0's founding complaint: those two actions had no way back, which is
why the WhatsApp assistant's write tools (`approve_po`, `register_reception`)
are suspended in `backend/whatsapp/tools.py` and stay suspended after this
file. Nothing here re-enables them.

Pins: what each inverse restores, what it refuses to do when stock already
moved on, that reversing twice does not double-reverse, the permission pair
on both new endpoints, and that the reversal leaves a trail in
`activity_logs` and clears what `get_incoming_qty` / `get_payables` read live.
"""

import uuid

from backend.db.connection import execute, query, query_one


def _make_po(client, auth_headers, *, skus):
    """Log a PO with explicit cart lines (like the /hoy cart does)."""
    items = [
        {
            "sku": sku, "display_name": f"Prod {sku}", "supplier": prov,
            "signal": "PEDIR_YA", "recommended_qty": qty,
            "final_qty": qty, "unit_cost": 2.5, "status": "approved",
        }
        for sku, (qty, prov) in skus.items()
    ]
    resp = client.post(
        "/api/v1/inventory/log-po",
        params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
        json={"items": items},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _stock(tenant_id, sku):
    row = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )
    return float(row["current_stock"]) if row else None


def _po_row(po_id):
    return query_one(
        "SELECT reception_status, received_at, received_by, sent_at, po_number "
        "FROM inventory_po_log WHERE id = %s", (po_id,))


class TestUnreceive:
    def test_viewer_cannot_unreceive(self, client, auth_headers, viewer_headers, test_tenant):
        sku = f"UR-V-{uuid.uuid4().hex[:6]}"
        po_id = _make_po(client, auth_headers, skus={sku: (10, "Prov X")})
        client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=auth_headers)
        before = _stock(test_tenant["id"], sku)

        resp = client.post(f"/api/v1/inventory/po/{po_id}/unreceive", headers=viewer_headers)
        assert resp.status_code == 403

        row = _po_row(po_id)
        assert row["reception_status"] == "received"
        assert _stock(test_tenant["id"], sku) == before

    def test_analyst_can_unreceive_a_full_reception(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"UR-F-{uuid.uuid4().hex[:6]}"
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 100},
                  headers=auth_headers)
        po_id = _make_po(client, auth_headers, skus={sku: (40, "Prov Reversible")})

        recv = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=auth_headers)
        assert recv.status_code == 200, recv.text
        assert _stock(tid, sku) == 140.0
        assert query_one(
            "SELECT 1 FROM supplier_lead_time_obs WHERE tenant_id=%s AND po_log_id=%s "
            "AND supplier='Prov Reversible'", (tid, po_id)) is not None

        resp = client.post(f"/api/v1/inventory/po/{po_id}/unreceive", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["reception_status"] == "pending"
        assert data["units_removed"] == 40.0
        assert "Prov Reversible" in data["suppliers_lead_time_unlearned"]

        # DB: stock is back to what it was before the reception
        assert _stock(tid, sku) == 100.0
        # DB: the PO line's received_qty is reset
        item = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE po_log_id=%s AND sku=%s",
            (po_id, sku))
        assert float(item["received_qty"] or 0) == 0.0
        # DB: the header is back to pending, not just relabelled
        row = _po_row(po_id)
        assert row["reception_status"] == "pending"
        assert row["received_at"] is None
        assert row["received_by"] is None
        # DB: the lead-time observation this reception taught is gone —
        # the supplier's learned lead time is no longer moved by a mistake.
        assert query_one(
            "SELECT 1 FROM supplier_lead_time_obs WHERE tenant_id=%s AND po_log_id=%s",
            (tid, po_id)) is None

        # A trail exists that a person can see, distinct from the original
        # reception's own row.
        act = query_one(
            "SELECT context FROM activity_logs WHERE tenant_id=%s "
            "AND action='purchase.reception_undone' AND resource=%s", (tid, po_id))
        assert act is not None
        assert act["context"]["units"] == 40.0
        assert act["context"]["sku_count"] == 1
        assert act["context"]["severity"] == "warning"
        assert act["context"]["reason"] == "reversed_by_user"
        original = query_one(
            "SELECT 1 FROM activity_logs WHERE tenant_id=%s "
            "AND action='purchase.reception_recorded' AND resource=%s", (tid, po_id))
        assert original is not None, "the undo must not erase the record of what happened"

    def test_a_partial_reception_can_be_undone(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"UR-P-{uuid.uuid4().hex[:6]}"
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 10},
                  headers=auth_headers)
        po_id = _make_po(client, auth_headers, skus={sku: (50, "Prov Parcial")})

        recv = client.post(
            f"/api/v1/inventory/po/{po_id}/receive",
            json={"lines": [{"sku": sku, "received_qty": 30}]},
            headers=auth_headers,
        )
        assert recv.status_code == 200, recv.text
        assert _stock(tid, sku) == 40.0
        assert _po_row(po_id)["reception_status"] == "partial"
        # A partial reception never completes the PO, so no lead time was
        # ever learned from it — nothing for the undo to remove.
        assert query_one(
            "SELECT 1 FROM supplier_lead_time_obs WHERE tenant_id=%s AND po_log_id=%s",
            (tid, po_id)) is None

        resp = client.post(f"/api/v1/inventory/po/{po_id}/unreceive", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert _stock(tid, sku) == 10.0
        row = _po_row(po_id)
        assert row["reception_status"] == "pending"
        assert row["received_at"] is None

        # And the PO is receivable again — pending is one of RECEIVABLE_STATES.
        resp2 = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=auth_headers)
        assert resp2.status_code == 200, resp2.text
        assert _stock(tid, sku) == 60.0  # 10 + the full 50 this time

    def test_refuses_when_stock_already_moved_on(self, client, auth_headers, test_tenant):
        """Received units that were sold/transferred/shrunk since must not be
        taken back below zero, and must not be silently clamped either — the
        whole undo has to refuse so nothing (stock, received_qty, the lead
        time observation) is half-reverted."""
        tid = test_tenant["id"]
        sku = f"UR-S-{uuid.uuid4().hex[:6]}"
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 5},
                  headers=auth_headers)
        po_id = _make_po(client, auth_headers, skus={sku: (40, "Prov Vendido")})
        client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=auth_headers)
        assert _stock(tid, sku) == 45.0

        # 40 units sold off since the reception — only 3 left, less than the
        # 40 the undo would need to remove.
        execute("UPDATE inventory_stock SET current_stock = 3 WHERE tenant_id=%s AND sku=%s",
                (tid, sku))

        resp = client.post(f"/api/v1/inventory/po/{po_id}/unreceive", headers=auth_headers)
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error_code"] == "reception_undo_insufficient_stock"
        shortfall = body["error_params"]["shortfalls"][0]
        assert shortfall["sku"] == sku
        assert shortfall["available"] == 3.0
        assert shortfall["needed"] == 40.0

        # Nothing moved: not stock (still 3, not negative, not clamped to 0),
        # not the PO line, not the header.
        assert _stock(tid, sku) == 3.0
        item = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE po_log_id=%s AND sku=%s",
            (po_id, sku))
        assert float(item["received_qty"]) == 40.0
        assert _po_row(po_id)["reception_status"] == "received"
        assert query_one(
            "SELECT 1 FROM supplier_lead_time_obs WHERE tenant_id=%s AND po_log_id=%s",
            (tid, po_id)) is not None

    def test_reversing_twice_does_not_double_reverse(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"UR-I-{uuid.uuid4().hex[:6]}"
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 20},
                  headers=auth_headers)
        po_id = _make_po(client, auth_headers, skus={sku: (15, "Prov Doble")})
        client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=auth_headers)
        assert _stock(tid, sku) == 35.0

        first = client.post(f"/api/v1/inventory/po/{po_id}/unreceive", headers=auth_headers)
        assert first.status_code == 200, first.text
        assert _stock(tid, sku) == 20.0

        second = client.post(f"/api/v1/inventory/po/{po_id}/unreceive", headers=auth_headers)
        assert second.status_code == 409
        assert second.json()["error_code"] == "reception_nothing_to_undo"
        # Stock was NOT decremented a second time for units already given back.
        assert _stock(tid, sku) == 20.0

        undo_rows = query(
            "SELECT id FROM activity_logs WHERE tenant_id=%s AND action='purchase.reception_undone' "
            "AND resource=%s", (tid, po_id))
        assert len(undo_rows) == 1, "a refused second call must not write a second undo event"

    def test_unreceive_unknown_po_is_404(self, client, auth_headers):
        resp = client.post("/api/v1/inventory/po/not-a-real-po/unreceive", headers=auth_headers)
        assert resp.status_code == 404


class TestUnsend:
    def _sent_po(self, client, auth_headers, tenant_id, sku="US-1"):
        po_id = _make_po(client, auth_headers, skus={sku: (10, "Prov Enviado")})
        # Stamp sent_at directly, the way seed_demo.py does — this test is
        # about the reversal, not about exercising email/WhatsApp delivery
        # (which /send itself already covers, and which requires a verified
        # email the test fixtures do not carry).
        execute("UPDATE inventory_po_log SET sent_at = NOW() WHERE id = %s", (po_id,))
        return po_id

    def test_viewer_cannot_unsend(self, client, auth_headers, viewer_headers, test_tenant):
        po_id = self._sent_po(client, auth_headers, test_tenant["id"])
        resp = client.post(f"/api/v1/inventory/po/{po_id}/unsend", headers=viewer_headers)
        assert resp.status_code == 403
        assert _po_row(po_id)["sent_at"] is not None

    def test_analyst_can_unsend_and_it_clears_incoming_and_payables(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory.cash_service import get_payables
        from backend.inventory.service import get_incoming_qty

        tid = test_tenant["id"]
        sku = f"US-{uuid.uuid4().hex[:6]}"
        po_id = self._sent_po(client, auth_headers, tid, sku=sku)

        # Before: this PO's units count as incoming and its invoice is on the
        # payables calendar — both read `sent_at` live, no separate flag.
        assert any(k[0] == sku for k in get_incoming_qty(tid))
        payables_before = get_payables(tid)
        assert any(p["po_log_id"] == po_id
                   for p in payables_before["due_items"] + payables_before["unknown_terms"])

        resp = client.post(f"/api/v1/inventory/po/{po_id}/unsend", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["sent_at"] is None

        row = _po_row(po_id)
        assert row["sent_at"] is None

        # After: neither screen still treats it as sent, with no extra write —
        # both simply stopped matching `sent_at IS NOT NULL`.
        assert not any(k[0] == sku for k in get_incoming_qty(tid))
        payables = get_payables(tid)
        assert not any(p["po_log_id"] == po_id
                       for p in payables["due_items"] + payables["unknown_terms"])

        act = query_one(
            "SELECT context FROM activity_logs WHERE tenant_id=%s "
            "AND action='purchase.order_unsent' AND resource=%s", (tid, po_id))
        assert act is not None
        assert act["context"]["severity"] == "warning"
        assert act["context"]["reason"] == "reversed_by_user"

    def test_unsend_refuses_once_a_reception_exists(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"US-R-{uuid.uuid4().hex[:6]}"
        po_id = self._sent_po(client, auth_headers, tid, sku=sku)
        client.post(f"/api/v1/inventory/po/{po_id}/receive", json={}, headers=auth_headers)
        assert _po_row(po_id)["reception_status"] == "received"

        resp = client.post(f"/api/v1/inventory/po/{po_id}/unsend", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_unsend_after_reception"
        assert _po_row(po_id)["sent_at"] is not None

    def test_unsend_twice_does_not_double_reverse(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        po_id = self._sent_po(client, auth_headers, tid, sku=f"US-D-{uuid.uuid4().hex[:6]}")

        first = client.post(f"/api/v1/inventory/po/{po_id}/unsend", headers=auth_headers)
        assert first.status_code == 200

        second = client.post(f"/api/v1/inventory/po/{po_id}/unsend", headers=auth_headers)
        assert second.status_code == 409
        assert second.json()["error_code"] == "po_not_sent"

        undo_rows = query(
            "SELECT id FROM activity_logs WHERE tenant_id=%s AND action='purchase.order_unsent' "
            "AND resource=%s", (tid, po_id))
        assert len(undo_rows) == 1

    def test_unsend_never_sent_is_409(self, client, auth_headers, test_tenant):
        po_id = _make_po(client, auth_headers, skus={f"US-N-{uuid.uuid4().hex[:6]}": (5, "P")})
        resp = client.post(f"/api/v1/inventory/po/{po_id}/unsend", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_not_sent"

    def test_unsend_unknown_po_is_404(self, client, auth_headers):
        resp = client.post("/api/v1/inventory/po/not-a-real-po/unsend", headers=auth_headers)
        assert resp.status_code == 404


class TestWriteToolsStaySuspended:
    """The whole reason this module exists (docs/assistant-actions.md section
    0) is not license to flip the switch back on. This is a tripwire: it fails
    loudly if a future change quietly repopulates WRITE_TOOLS."""

    def test_write_tools_remain_empty(self):
        from backend.whatsapp.tools import SUSPENDED_WRITE_TOOLS, WRITE_TOOLS
        assert WRITE_TOOLS == {}
        assert SUSPENDED_WRITE_TOOLS == {"approve_po", "register_reception"}
