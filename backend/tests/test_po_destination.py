"""PO destination warehouse (feature 5.4, spec §4)."""

from backend.db.connection import query_one
from backend.inventory import warehouse_service as wh_svc


def _log_po(client, headers, session_id, destination=None):
    body = {
        "items": [{
            "sku": "A", "display_name": "A", "supplier": "Acme",
            "signal": "PEDIR_YA", "status": "approved",
            "recommended_qty": 10, "final_qty": 10, "unit_cost": 2.0,
        }],
    }
    if destination:
        body["destination_warehouse"] = destination
    return client.post(f"/api/v1/inventory/log-po?session_id={session_id}",
                       json=body, headers=headers)


class TestPoDestination:
    def test_destination_persisted(self, client, auth_headers, test_tenant, completed_session):
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "Norte")
        r = _log_po(client, auth_headers, completed_session["id"], destination="Norte")
        assert r.status_code == 201, r.text
        po_id = r.json()["data"]["id"]
        row = query_one(
            "SELECT destination_warehouse FROM inventory_po_log WHERE id=%s", (po_id,))
        assert row["destination_warehouse"] == "Norte"

    def test_reception_lands_in_destination(self, client, auth_headers, test_tenant, completed_session):
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "Norte")
        r = _log_po(client, auth_headers, completed_session["id"], destination="Norte")
        po_id = r.json()["data"]["id"]
        r = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        row = query_one(
            """SELECT current_stock FROM inventory_stock
               WHERE tenant_id=%s AND sku='A' AND warehouse='Norte'""", (tid,))
        assert row is not None and float(row["current_stock"]) == 10.0
        # And it must NOT have landed in the historical default
        principal = query_one(
            """SELECT current_stock FROM inventory_stock
               WHERE tenant_id=%s AND sku='A' AND warehouse='principal'""", (tid,))
        assert principal is None

    def test_null_destination_keeps_today_behavior(self, client, auth_headers, test_tenant, completed_session):
        tid = test_tenant["id"]
        r = _log_po(client, auth_headers, completed_session["id"])
        assert r.status_code == 201
        po_id = r.json()["data"]["id"]
        row = query_one(
            "SELECT destination_warehouse FROM inventory_po_log WHERE id=%s", (po_id,))
        assert row["destination_warehouse"] is None
        # Reception falls back to 'principal' exactly as before
        r = client.post(f"/api/v1/inventory/po/{po_id}/receive", json={},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        principal = query_one(
            """SELECT current_stock FROM inventory_stock
               WHERE tenant_id=%s AND sku='A' AND warehouse='principal'""", (tid,))
        assert principal is not None and float(principal["current_stock"]) == 10.0
