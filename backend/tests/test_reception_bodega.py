"""
Reception must not inflate stock across every warehouse for a SKU — only
the PO line's own destination warehouse should receive the incoming units.
"""
from uuid import uuid4

import pytest


def _sku():
    return f"RCV_{uuid4().hex[:8]}"


class TestReceptionRespectsWarehouse:
    def test_receiving_a_po_only_increments_its_own_warehouse(
        self, client, auth_headers, analyst_headers, test_tenant,
    ):
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()

        # Seed the SAME sku in two warehouses with known starting stock.
        inv_svc.upsert_stock(tid, sku, {"current_stock": 100, "warehouse": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"current_stock": 50, "warehouse": "Sur"})

        # Log a PO destined for "Norte" only.
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "final_qty": 20, "status": "approved",
            "warehouse": "Norte",
        }])

        rec_svc.receive_po(tid, po["id"], user_id="u1")

        norte = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse='Norte'",
            (tid, sku),
        )
        sur = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse='Sur'",
            (tid, sku),
        )
        assert float(norte["current_stock"]) == 120.0  # 100 + 20 received
        assert float(sur["current_stock"]) == 50.0      # untouched — this is the regression guard

    def test_receiving_into_a_warehouse_with_no_existing_row_does_not_drop_stock(
        self, client, auth_headers, analyst_headers, test_tenant,
    ):
        """
        The SKU already has a stock row in "Norte" but NOT in "principal".
        A PO destined for "principal" (the default warehouse) must create that
        row and add the received units — not find the Norte row via a
        warehouse-blind existence check, then silently no-op an UPDATE scoped
        to a warehouse that has no row yet.
        """
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"current_stock": 100, "warehouse": "Norte"})

        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "final_qty": 30, "status": "approved",
            "warehouse": "principal",
        }])

        rec_svc.receive_po(tid, po["id"], user_id="u1")

        principal = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse='principal'",
            (tid, sku),
        )
        norte = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse='Norte'",
            (tid, sku),
        )
        assert principal is not None
        assert float(principal["current_stock"]) == 30.0  # created, not silently dropped
        assert float(norte["current_stock"]) == 100.0      # untouched


class TestReceptionOverReceiptCap:
    """QA received 5000 units against a line ordered for 312, inflating stock.
    A reception must not book more than the line's outstanding quantity."""

    def test_receiving_more_than_outstanding_is_rejected(self, client, test_tenant):
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "warehouse": "principal"})
        po = roi_service.log_po_generation(tid, "sess-test", [
            {"sku": sku, "final_qty": 312, "status": "approved", "supplier": "Prov A"},
        ])

        with pytest.raises(ValueError):
            rec_svc.receive_po(tid, po["id"], user_id="u1",
                               lines=[{"sku": sku, "received_qty": 5000}])

        # Nothing booked; stock unchanged.
        row = query_one(
            "SELECT current_stock FROM inventory_stock "
            "WHERE tenant_id=%s AND sku=%s AND warehouse='principal'", (tid, sku))
        assert float(row["current_stock"]) == 0.0

    def test_partial_then_receive_complete_books_only_outstanding(self, client, test_tenant):
        """QA NEW-1: after a partial reception, 'Llegó todo completo' (lines=None)
        must book only the remaining units, never re-book the full final_qty."""
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "warehouse": "principal"})
        po = roi_service.log_po_generation(tid, "sess-test", [
            {"sku": sku, "final_qty": 300, "status": "approved", "supplier": "Prov A"},
        ])
        # First shipment: 100 of 300.
        rec_svc.receive_po(tid, po["id"], user_id="u1",
                           lines=[{"sku": sku, "received_qty": 100}])
        # "Everything arrived complete" — must book only the outstanding 200.
        rec_svc.receive_po(tid, po["id"], user_id="u1", lines=None)

        item = query_one(
            "SELECT received_qty, final_qty FROM inventory_po_items "
            "WHERE po_log_id=%s AND sku=%s", (po["id"], sku))
        assert float(item["received_qty"]) == 300.0  # not 400
        assert float(item["received_qty"]) <= float(item["final_qty"])
        stock = query_one(
            "SELECT current_stock FROM inventory_stock "
            "WHERE tenant_id=%s AND sku=%s AND warehouse='principal'", (tid, sku))
        assert float(stock["current_stock"]) == 300.0  # 100 + 200, not 100 + 300

    def test_partial_then_over_remaining_is_rejected(self, client, test_tenant):
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc

        tid = test_tenant["id"]
        sku = _sku()
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "warehouse": "principal"})
        po = roi_service.log_po_generation(tid, "sess-test", [
            {"sku": sku, "final_qty": 100, "status": "approved", "supplier": "Prov A"},
        ])
        rec_svc.receive_po(tid, po["id"], user_id="u1",
                           lines=[{"sku": sku, "received_qty": 60}])
        # 40 outstanding — receiving 50 must be rejected.
        with pytest.raises(ValueError):
            rec_svc.receive_po(tid, po["id"], user_id="u1",
                               lines=[{"sku": sku, "received_qty": 50}])


class TestReceptionLeadTimeObservationPerSupplier:
    """A PO can span multiple suppliers who deliver at different times.
    Lead-time learning must record ONE observation per supplier, taken on
    THAT supplier's own first delivery against this PO — not gated on
    whether the PO header itself was still 'pending' before the event,
    which silently starves any supplier who isn't part of the very first
    reception event.
    """

    def test_second_supplier_delivering_in_a_later_event_still_gets_observed(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query

        tid = test_tenant["id"]
        sku_a = _sku()
        sku_b = _sku()

        po = roi_service.log_po_generation(tid, "sess-test", [
            {"sku": sku_a, "final_qty": 10, "status": "approved", "supplier": "Prov A"},
            {"sku": sku_b, "final_qty": 5, "status": "approved", "supplier": "Prov B"},
        ])

        # Event 1: only Prov A delivers. PO goes pending -> partial.
        rec_svc.receive_po(tid, po["id"], user_id="u1",
                            lines=[{"sku": sku_a, "received_qty": 10}])

        # Event 2: Prov B delivers for the FIRST time (PO is now 'partial').
        rec_svc.receive_po(tid, po["id"], user_id="u1",
                            lines=[{"sku": sku_b, "received_qty": 5}])

        rows = query(
            "SELECT supplier FROM supplier_lead_time_obs WHERE tenant_id=%s AND po_log_id=%s",
            (tid, po["id"]),
        )
        observed = {r["supplier"] for r in rows}
        assert observed == {"Prov A", "Prov B"}, (
            "Prov B's first-ever delivery on this PO must produce a "
            f"lead-time observation too, got {observed}"
        )


class TestReceptionIsAtomic:
    """
    receive_po writes across 5 tables (inventory_po_items, inventory_stock,
    inventory_snapshots, inventory_po_log, supplier_lead_time_obs). Before the
    atomic-transaction fix, each write auto-committed independently, so a
    failure partway through the sequence left genuinely partial state:
    received_qty incremented and stock credited, but the PO header never
    flipped out of 'pending'. This forces a failure in the LAST write of the
    sequence (the header UPDATE in step 3, which runs strictly after
    received_qty and stock have already been written earlier in the same
    call) and asserts that NOTHING committed — proving the whole reception is
    one all-or-nothing transaction, not proving the trivial case where the
    failure happens before any write at all.
    """

    def test_failure_mid_sequence_rolls_back_everything(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"current_stock": 100, "warehouse": "principal"})
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "final_qty": 20, "status": "approved",
            "warehouse": "principal", "supplier": "Prov Atomic",
        }])
        po_item = query_one(
            "SELECT id, received_qty FROM inventory_po_items WHERE po_log_id = %s AND sku = %s",
            (po["id"], sku),
        )
        assert float(po_item["received_qty"] or 0) == 0.0

        # Fail on the PO header UPDATE — the 3rd write in the sequence, after
        # received_qty (step 1) and stock (step 2) have already run inside the
        # SAME still-open transaction.
        real_execute = rec_svc.execute

        def _boom(sql, params=(), conn=None):
            if "inventory_po_log" in sql and "reception_status" in sql:
                raise RuntimeError("simulated failure mid-reception")
            return real_execute(sql, params, conn=conn)

        monkeypatch.setattr(rec_svc, "execute", _boom)

        try:
            with pytest.raises(RuntimeError, match="simulated failure mid-reception"):
                rec_svc.receive_po(tid, po["id"], user_id="u1")
        finally:
            monkeypatch.setattr(rec_svc, "execute", real_execute)

        # Nothing committed: received_qty, stock and the PO header must all
        # be exactly as they were before the call.
        po_item_after = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE id = %s",
            (po_item["id"],),
        )
        assert float(po_item_after["received_qty"] or 0) == 0.0, (
            "received_qty was persisted despite the header update failing later "
            "in the same reception — the transaction did not roll back"
        )

        stock_row = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s "
            "AND warehouse = 'principal'",
            (tid, sku),
        )
        assert float(stock_row["current_stock"]) == 100.0, (
            "stock was credited despite the reception failing later in the "
            "same sequence — the transaction did not roll back"
        )

        po_log_after = query_one(
            "SELECT reception_status, received_at, received_by FROM inventory_po_log WHERE id = %s",
            (po["id"],),
        )
        assert po_log_after["reception_status"] == "pending"
        assert po_log_after["received_at"] is None
        assert po_log_after["received_by"] is None

        obs = query_one(
            "SELECT 1 FROM supplier_lead_time_obs WHERE tenant_id = %s AND po_log_id = %s",
            (tid, po["id"]),
        )
        assert obs is None, "a lead-time observation was persisted from a rolled-back reception"

    def test_normal_reception_still_fully_commits(
        self, client, auth_headers, test_tenant,
    ):
        """Sanity companion to the rollback test above: with no injected
        failure, the same call path commits every write."""
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"current_stock": 100, "warehouse": "principal"})
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "final_qty": 20, "status": "approved",
            "warehouse": "principal", "supplier": "Prov Atomic OK",
        }])

        result = rec_svc.receive_po(tid, po["id"], user_id="u1")
        assert result["reception_status"] == "received"

        stock_row = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s "
            "AND warehouse = 'principal'",
            (tid, sku),
        )
        assert float(stock_row["current_stock"]) == 120.0

        po_log_after = query_one(
            "SELECT reception_status FROM inventory_po_log WHERE id = %s", (po["id"],),
        )
        assert po_log_after["reception_status"] == "received"

        obs = query_one(
            "SELECT lead_time_days FROM supplier_lead_time_obs "
            "WHERE tenant_id = %s AND po_log_id = %s AND supplier = %s",
            (tid, po["id"], "Prov Atomic OK"),
        )
        assert obs is not None
