"""PENDIENTES #1: the buyer picks the supplier per PO line.

Covers the three pieces: the primary-supplier fallback in the recommendation
payload, persistence of the chosen supplier_id, and the send path reporting
unresolvable lines instead of dropping them in silence."""

from unittest import mock

from backend.db.connection import query, query_one
from backend.inventory import service as svc
from backend.inventory import supplier_service as sup_svc


class TestPrimarySupplierFallback:
    def test_sku_without_a_stock_supplier_inherits_its_primary(
        self, client, auth_headers, test_tenant, completed_session
    ):
        tid = test_tenant["id"]
        sid = completed_session["id"]
        items = svc.get_inventory_status(tid, sid)
        assert items, "session fixture should expose forecast SKUs"
        sku = items[0]["sku"]

        supplier = sup_svc.create_supplier(tid, {"name": "Primario SA"})
        sup_svc.upsert_sku_supplier(tid, sku, supplier["id"], {"is_primary": True})

        after = {i["sku"]: i for i in svc.get_inventory_status(tid, sid)}[sku]
        assert after["supplier"] == "Primario SA"
        assert after["supplier_id"] == supplier["id"]

    def test_free_text_supplier_on_the_stock_row_still_wins(
        self, client, auth_headers, test_tenant, completed_session
    ):
        tid = test_tenant["id"]
        sid = completed_session["id"]
        sku = svc.get_inventory_status(tid, sid)[0]["sku"]

        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 10, "supplier": "Lo Que Escribí"},
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        supplier = sup_svc.create_supplier(tid, {"name": "Otro Primario"})
        sup_svc.upsert_sku_supplier(tid, sku, supplier["id"], {"is_primary": True})

        after = {i["sku"]: i for i in svc.get_inventory_status(tid, sid)}[sku]
        assert after["supplier"] == "Lo Que Escribí"
        # No id to resolve: the typed name is not a supplier record.
        assert after["supplier_id"] is None


class TestChosenSupplierIsPersisted:
    def test_log_po_stores_the_picked_supplier_id(
        self, client, analyst_headers, test_tenant, completed_session
    ):
        tid = test_tenant["id"]
        supplier = sup_svc.create_supplier(tid, {"name": "Elegido SA"})
        r = client.post(
            f"/api/v1/inventory/log-po?session_id={completed_session['id']}",
            json={"items": [{
                "sku": "P-1", "display_name": "Producto 1",
                "supplier": "Elegido SA", "supplier_id": supplier["id"],
                "signal": "PEDIR_YA", "status": "modified",
                "recommended_qty": 10, "final_qty": 12, "unit_cost": 1.5,
            }]},
            headers=analyst_headers)
        assert r.status_code == 201, r.text

        line = query_one(
            "SELECT supplier, supplier_id FROM inventory_po_items WHERE po_log_id = %s",
            (r.json()["data"]["id"],))
        assert line["supplier"] == "Elegido SA"
        assert line["supplier_id"] == supplier["id"]

    def test_line_without_a_pick_keeps_a_null_supplier_id(
        self, client, analyst_headers, test_tenant, completed_session
    ):
        r = client.post(
            f"/api/v1/inventory/log-po?session_id={completed_session['id']}",
            json={"items": [{
                "sku": "P-2", "supplier": "Nombre Suelto",
                "signal": "PEDIR_YA", "status": "approved",
                "recommended_qty": 4, "final_qty": 4,
            }]},
            headers=analyst_headers)
        assert r.status_code == 201, r.text
        line = query_one(
            "SELECT supplier, supplier_id FROM inventory_po_items WHERE po_log_id = %s",
            (r.json()["data"]["id"],))
        assert line["supplier"] == "Nombre Suelto"
        assert line["supplier_id"] is None


class TestSendReportsUnresolvedLines:
    def _log(self, client, headers, session_id, items):
        r = client.post(f"/api/v1/inventory/log-po?session_id={session_id}",
                        json={"items": items}, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()["data"]["id"]

    def test_unknown_supplier_name_is_reported_not_silently_dropped(
        self, client, analyst_headers, test_tenant, completed_session
    ):
        po_id = self._log(client, analyst_headers, completed_session["id"], [{
            "sku": "U-1", "supplier": "Proveedor Fantasma",
            "signal": "PEDIR_YA", "status": "approved",
            "recommended_qty": 5, "final_qty": 5,
        }])
        r = client.post(f"/api/v1/inventory/po/{po_id}/send", headers=analyst_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["sent"] == []
        assert [u["sku"] for u in data["unresolved"]] == ["U-1"]

    def test_line_with_no_supplier_at_all_is_reported(
        self, client, analyst_headers, test_tenant, completed_session
    ):
        po_id = self._log(client, analyst_headers, completed_session["id"], [{
            "sku": "U-2", "supplier": None,
            "signal": "PEDIR_YA", "status": "approved",
            "recommended_qty": 3, "final_qty": 3,
        }])
        r = client.post(f"/api/v1/inventory/po/{po_id}/send", headers=analyst_headers)
        assert r.status_code == 200, r.text
        unresolved = r.json()["data"]["unresolved"]
        assert [u["sku"] for u in unresolved] == ["U-2"]

    def test_picked_supplier_id_resolves_even_when_the_name_does_not_match(
        self, client, analyst_headers, test_tenant, completed_session
    ):
        """The id is the buyer's actual decision; a stale display name on the
        line must not stop the order from reaching the right supplier."""
        tid = test_tenant["id"]
        supplier = sup_svc.create_supplier(
            tid, {"name": "Contacto SA", "whatsapp": "+50670009999"})
        po_id = self._log(client, analyst_headers, completed_session["id"], [{
            "sku": "U-3", "supplier": "Nombre Viejo Que No Existe",
            "supplier_id": supplier["id"],
            "signal": "PEDIR_YA", "status": "approved",
            "recommended_qty": 7, "final_qty": 7, "unit_cost": 2.0,
        }])

        with mock.patch("backend.notifications.whatsapp._send"):
            r = client.post(f"/api/v1/inventory/po/{po_id}/send", headers=analyst_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["unresolved"] == []
        assert len(data["sent"]) == 1 and data["sent"][0]["whatsapp"] is True

    def test_known_supplier_without_contact_details_reports_a_code_not_spanish(
        self, client, analyst_headers, test_tenant, completed_session
    ):
        tid = test_tenant["id"]
        sup_svc.create_supplier(tid, {"name": "Sin Contacto SA"})
        po_id = self._log(client, analyst_headers, completed_session["id"], [{
            "sku": "U-4", "supplier": "Sin Contacto SA",
            "signal": "PEDIR_YA", "status": "approved",
            "recommended_qty": 2, "final_qty": 2,
        }])
        r = client.post(f"/api/v1/inventory/po/{po_id}/send", headers=analyst_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["unresolved"] == []
        # A stable code the frontend localizes — not a Spanish string from the API.
        assert data["skipped"][0]["reason"] == "no_contact_details"


class TestPermissionPair:
    def test_viewer_cannot_log_a_po_and_nothing_is_written(
        self, client, viewer_headers, test_tenant, completed_session
    ):
        tid = test_tenant["id"]
        before = query("SELECT id FROM inventory_po_log WHERE tenant_id = %s", (tid,))
        r = client.post(
            f"/api/v1/inventory/log-po?session_id={completed_session['id']}",
            json={"items": [{
                "sku": "V-1", "supplier": "X", "signal": "PEDIR_YA",
                "status": "approved", "recommended_qty": 1, "final_qty": 1,
            }]},
            headers=viewer_headers)
        assert r.status_code == 403
        after = query("SELECT id FROM inventory_po_log WHERE tenant_id = %s", (tid,))
        assert len(after) == len(before)
