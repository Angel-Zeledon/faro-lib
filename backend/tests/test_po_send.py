from uuid import uuid4


def _sku():
    return f"SEND_{uuid4().hex[:8]}"


class TestSendPOEndpoint:
    def test_viewer_denied(self, client, viewer_headers, test_tenant):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        sku = _sku()
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "final_qty": 10, "status": "approved", "supplier": "Acme",
        }])
        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=viewer_headers)
        assert resp.status_code == 403

    def test_sends_email_and_whatsapp_when_supplier_has_both(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        from backend.inventory import roi_service, supplier_service as sup_svc
        from backend.notifications import email as email_mod, whatsapp as wa_mod

        tid = test_tenant["id"]
        sku = _sku()
        supplier_name = f"Proveedor {uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {
            "name": supplier_name, "email": "ventas@proveedor.com", "whatsapp": "+15551234567",
        })
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "final_qty": 20, "unit_cost": 3.0,
            "status": "approved", "supplier": supplier_name,
        }])

        email_calls = []
        wa_calls = []
        monkeypatch.setattr(email_mod, "send_po_to_supplier_email",
                            lambda **kw: email_calls.append(kw) or True)
        monkeypatch.setattr(wa_mod, "send_whatsapp",
                            lambda *a, **kw: wa_calls.append((a, kw)) or True)

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["sent"]) == 1
        assert data["sent"][0]["supplier"] == supplier_name
        assert data["sent"][0]["email"] is True
        assert data["sent"][0]["whatsapp"] is True
        assert data["skipped"] == []
        assert len(email_calls) == 1
        assert len(wa_calls) == 1

    def test_skips_supplier_with_no_contact_info_on_file(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        sku = _sku()
        unknown_supplier = f"Proveedor Desconocido {uuid4().hex[:6]}"
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "final_qty": 5, "status": "approved", "supplier": unknown_supplier,
        }])

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sent"] == []
        assert len(data["skipped"]) == 1
        assert data["skipped"][0]["supplier"] == unknown_supplier

    def test_pdf_endpoint_is_reachable_without_auth(self, client, auth_headers, test_tenant, monkeypatch):
        from backend.inventory import roi_service, supplier_service as sup_svc
        from backend.notifications import email as email_mod, whatsapp as wa_mod

        tid = test_tenant["id"]
        sku = _sku()
        supplier_name = f"Proveedor {uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {"name": supplier_name, "email": "ventas@proveedor.com"})
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": sku, "final_qty": 8, "unit_cost": 2.0,
            "status": "approved", "supplier": supplier_name,
        }])
        monkeypatch.setattr(email_mod, "send_po_to_supplier_email", lambda **kw: True)
        monkeypatch.setattr(wa_mod, "send_whatsapp", lambda *a, **kw: True)

        send_resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=auth_headers)
        assert send_resp.status_code == 200

        from backend.inventory.po_pdf import slugify_supplier_name
        slug = slugify_supplier_name(supplier_name)
        # No Authorization header — this route must be publicly reachable
        # (Twilio's MediaUrl fetch can't carry our Bearer token).
        pdf_resp = client.get(f"/api/v1/inventory/po/{po['id']}/pdf/{slug}")
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"] == "application/pdf"

    def test_email_subject_uses_readable_po_number(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        from backend.db.connection import query_one
        from backend.inventory import roi_service, supplier_service as sup_svc
        from backend.notifications import email as email_mod, whatsapp as wa_mod

        tid = test_tenant["id"]
        supplier_name = f"Proveedor {uuid4().hex[:6]}"
        sup_svc.create_supplier(tid, {"name": supplier_name, "email": "ventas@proveedor.com"})
        po = roi_service.log_po_generation(tid, "sess-test", [{
            "sku": _sku(), "final_qty": 20, "status": "approved", "supplier": supplier_name,
        }])

        email_calls = []
        monkeypatch.setattr(email_mod, "send_po_to_supplier_email",
                            lambda **kw: email_calls.append(kw) or True)
        monkeypatch.setattr(wa_mod, "send_whatsapp", lambda *a, **kw: True)

        resp = client.post(f"/api/v1/inventory/po/{po['id']}/send", headers=auth_headers)
        assert resp.status_code == 200

        row = query_one("SELECT po_number FROM inventory_po_log WHERE id = %s", (po["id"],))
        expected_ref = f"OC-{row['po_number']:06d}"
        assert len(email_calls) == 1
        assert email_calls[0]["po_ref"] == expected_ref
