"""Manual purchase orders (PENDIENTES #1): POST /inventory/po creates a PO with
no forecast session behind it — supplier chosen explicitly, lines typed in,
source='manual', session_id NULL, adoption counters untouched."""

from backend.db.connection import query, query_one
from backend.inventory import supplier_service as sup_svc


def _make_supplier(tenant_id, name="Acme Manual"):
    return sup_svc.create_supplier(tenant_id, {"name": name})


def _body(supplier_id, **overrides):
    body = {
        "supplier_id": supplier_id,
        "lines": [
            {"sku": "M-1", "qty": 12, "unit_cost": 3.5, "display_name": "Widget"},
            {"sku": "M-2", "qty": 5},
        ],
    }
    body.update(overrides)
    return body


class TestManualPOCreation:
    def test_analyst_creates_manual_po_and_db_rows_are_complete(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        supplier = _make_supplier(tid)
        r = client.post("/api/v1/inventory/po", json=_body(supplier["id"]),
                        headers=analyst_headers)
        assert r.status_code == 201, r.text
        po = r.json()["data"]

        header = query_one(
            "SELECT * FROM inventory_po_log WHERE id=%s AND tenant_id=%s",
            (po["id"], tid))
        assert header["session_id"] is None
        assert header["source"] == "manual"
        assert header["sku_count"] == 2
        assert float(header["total_units"]) == 17.0
        assert float(header["total_value"]) == 12 * 3.5  # only the costed line
        assert header["po_number"] is not None
        # Manual orders must not inflate recommendation-adoption metrics.
        assert header["suggested_count"] == 0
        assert header["approved_count"] == 0

        items = query(
            "SELECT * FROM inventory_po_items WHERE po_log_id=%s ORDER BY sku",
            (po["id"],))
        assert [i["sku"] for i in items] == ["M-1", "M-2"]
        assert all(i["status"] == "approved" for i in items)
        assert all(float(i["recommended_qty"]) == 0 for i in items)
        assert [float(i["final_qty"]) for i in items] == [12.0, 5.0]
        assert all(i["supplier"] == "Acme Manual" for i in items)

    def test_viewer_gets_403_and_no_row_is_created(
        self, client, viewer_headers, test_tenant
    ):
        tid = test_tenant["id"]
        supplier = _make_supplier(tid, name="Acme Viewer")
        before = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s", (tid,))
        r = client.post("/api/v1/inventory/po", json=_body(supplier["id"]),
                        headers=viewer_headers)
        assert r.status_code == 403
        after = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s", (tid,))
        assert after["n"] == before["n"]

    def test_unknown_supplier_404_and_no_row(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        r = client.post("/api/v1/inventory/po", json=_body("sup_does_not_exist"),
                        headers=analyst_headers)
        assert r.status_code == 404
        row = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s AND source='manual'",
            (tid,))
        assert row["n"] == 0

    def test_zero_qty_line_is_rejected_by_validation(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        supplier = _make_supplier(tid, name="Acme Zero")
        body = _body(supplier["id"])
        body["lines"][0]["qty"] = 0
        r = client.post("/api/v1/inventory/po", json=body, headers=analyst_headers)
        assert r.status_code == 422
        row = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s AND source='manual'",
            (tid,))
        assert row["n"] == 0

    def test_empty_lines_rejected(self, client, analyst_headers, test_tenant):
        supplier = _make_supplier(test_tenant["id"], name="Acme Empty")
        r = client.post("/api/v1/inventory/po",
                        json={"supplier_id": supplier["id"], "lines": []},
                        headers=analyst_headers)
        assert r.status_code == 422


class TestManualPOLifecycle:
    def test_manual_po_can_be_received_and_stock_lands(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        supplier = _make_supplier(tid, name="Acme Recv")
        r = client.post("/api/v1/inventory/po", json=_body(supplier["id"]),
                        headers=analyst_headers)
        po_id = r.json()["data"]["id"]

        r = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        stock = query_one(
            """SELECT current_stock FROM inventory_stock
               WHERE tenant_id=%s AND sku='M-1' AND warehouse='principal'""",
            (tid,))
        assert stock is not None and float(stock["current_stock"]) == 12.0

    def test_manual_po_shows_in_history_with_source(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        supplier = _make_supplier(tid, name="Acme Hist")
        r = client.post("/api/v1/inventory/po", json=_body(supplier["id"]),
                        headers=analyst_headers)
        po_id = r.json()["data"]["id"]

        r = client.get("/api/v1/inventory/po-history", headers=analyst_headers)
        assert r.status_code == 200
        entries = {e["id"]: e for e in r.json()["data"]}
        assert po_id in entries
        assert entries[po_id]["source"] == "manual"
        assert entries[po_id]["session_id"] is None
