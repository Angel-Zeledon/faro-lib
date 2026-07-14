"""
Reception must not inflate stock across every warehouse for a SKU — only
the PO line's own destination bodega should receive the incoming units.
"""
from uuid import uuid4


def _sku():
    return f"RCV_{uuid4().hex[:8]}"


class TestReceptionRespectsBodega:
    def test_receiving_a_po_only_increments_its_own_bodega(
        self, client, auth_headers, analyst_headers, test_tenant,
    ):
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()

        # Seed the SAME sku in two warehouses with known starting stock.
        inv_svc.upsert_stock(tid, sku, {"stock_actual": 100, "bodega": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"stock_actual": 50, "bodega": "Sur"})

        # Log a PO destined for "Norte" only.
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "cantidad_final": 20, "status": "approved",
            "bodega": "Norte",
        }])

        rec_svc.receive_po(tid, po["id"], user_id="u1")

        norte = query_one(
            "SELECT stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND bodega='Norte'",
            (tid, sku),
        )
        sur = query_one(
            "SELECT stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND bodega='Sur'",
            (tid, sku),
        )
        assert float(norte["stock_actual"]) == 120.0  # 100 + 20 received
        assert float(sur["stock_actual"]) == 50.0      # untouched — this is the regression guard

    def test_receiving_into_a_bodega_with_no_existing_row_does_not_drop_stock(
        self, client, auth_headers, analyst_headers, test_tenant,
    ):
        """
        The SKU already has a stock row in "Norte" but NOT in "principal".
        A PO destined for "principal" (the default bodega) must create that
        row and add the received units — not find the Norte row via a
        bodega-blind existence check, then silently no-op an UPDATE scoped
        to a bodega that has no row yet.
        """
        from backend.inventory import service as inv_svc
        from backend.inventory import roi_service
        from backend.inventory import reception_service as rec_svc
        from backend.db.connection import query_one

        tid = test_tenant["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"stock_actual": 100, "bodega": "Norte"})

        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "cantidad_final": 30, "status": "approved",
            "bodega": "principal",
        }])

        rec_svc.receive_po(tid, po["id"], user_id="u1")

        principal = query_one(
            "SELECT stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND bodega='principal'",
            (tid, sku),
        )
        norte = query_one(
            "SELECT stock_actual FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND bodega='Norte'",
            (tid, sku),
        )
        assert principal is not None
        assert float(principal["stock_actual"]) == 30.0  # created, not silently dropped
        assert float(norte["stock_actual"]) == 100.0      # untouched
