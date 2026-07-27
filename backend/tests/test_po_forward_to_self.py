"""PENDIENTES #1: the PO is delivered to the BUYER's own WhatsApp so they
forward it to the supplier — no Faro↔supplier integration needed. The endpoint
always returns the text and a wa.me link so the flow works with no Twilio and
no number on file."""

from unittest import mock
from urllib.parse import unquote

import pytest

from backend.db.connection import execute, query_one
from backend.inventory import supplier_service as sup_svc


def _make_po(client, headers, tenant_id):
    supplier = sup_svc.create_supplier(tenant_id, {"name": "Distribuidora Sur"})
    r = client.post("/api/v1/inventory/po", json={
        "supplier_id": supplier["id"],
        "lines": [
            {"sku": "F-1", "qty": 30, "display_name": "Aceite 1L"},
            {"sku": "F-2", "qty": 8, "display_name": "Arroz 5kg"},
        ],
    }, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _set_phone(tenant_id, number):
    execute(
        """UPDATE users SET whatsapp_number = %s
           WHERE tenant_id = %s AND id = (
               SELECT id FROM users WHERE tenant_id = %s AND role = 'analyst' LIMIT 1)""",
        (number, tenant_id, tenant_id))


class TestForwardText:
    @pytest.mark.offline
    def test_groups_lines_by_supplier_and_reads_in_spanish(self):
        from backend.notifications.whatsapp import build_po_forward_text

        text = build_po_forward_text("OC-000042", [
            {"supplier": "Distribuidora Sur", "items": [
                {"sku": "F-1", "display_name": "Aceite 1L", "final_qty": 30},
            ]},
            {"supplier": "Granos SA", "items": [
                {"sku": "F-2", "display_name": "Arroz 5kg", "final_qty": 8},
            ]},
        ])
        assert "OC-000042" in text
        assert "Distribuidora Sur" in text and "Granos SA" in text
        assert "Aceite 1L" in text and "30" in text
        assert "Arroz 5kg" in text and "8" in text
        # It is the buyer who forwards it, so the closing line must say so.
        assert "Reenvía" in text

    @pytest.mark.offline
    def test_long_supplier_section_is_truncated_with_a_counter(self):
        from backend.notifications.whatsapp import build_po_forward_text

        items = [{"sku": f"S-{i}", "display_name": f"Producto {i}", "final_qty": i}
                 for i in range(1, 21)]
        text = build_po_forward_text("OC-1", [{"supplier": "Mayorista", "items": items}])
        assert "Producto 15" in text
        assert "Producto 16" not in text
        assert "5 más" in text


class TestSendToMe:
    def test_analyst_with_number_gets_it_sent_and_link_returned(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        po_id = _make_po(client, analyst_headers, tid)
        _set_phone(tid, "+50670000001")

        with mock.patch("backend.notifications.whatsapp._send") as send:
            r = client.post(f"/api/v1/inventory/po/{po_id}/send-to-me",
                            headers=analyst_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["sent"] is True and data["has_number"] is True

        send.assert_called_once()
        to_number, body, _media = send.call_args[0]
        assert to_number == "+50670000001"
        assert "Distribuidora Sur" in body and "Aceite 1L" in body
        # The same text the buyer sees is the one that got sent.
        assert data["message_text"] == body
        assert unquote(data["wa_me_url"].split("text=", 1)[1]) == body

    def test_without_a_number_nothing_is_sent_but_link_still_returned(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        po_id = _make_po(client, analyst_headers, tid)
        _set_phone(tid, None)

        with mock.patch("backend.notifications.whatsapp._send") as send:
            r = client.post(f"/api/v1/inventory/po/{po_id}/send-to-me",
                            headers=analyst_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["sent"] is False and data["has_number"] is False
        send.assert_not_called()
        # The copy/deep-link path must still work — that's the whole point.
        assert data["message_text"] and data["wa_me_url"].startswith("https://wa.me/?text=")

    def test_viewer_is_denied(self, client, viewer_headers, analyst_headers, test_tenant):
        po_id = _make_po(client, analyst_headers, test_tenant["id"])
        with mock.patch("backend.notifications.whatsapp._send") as send:
            r = client.post(f"/api/v1/inventory/po/{po_id}/send-to-me",
                            headers=viewer_headers)
        assert r.status_code == 403
        send.assert_not_called()

    def test_unknown_po_returns_404(self, client, analyst_headers):
        r = client.post("/api/v1/inventory/po/po_does_not_exist/send-to-me",
                        headers=analyst_headers)
        assert r.status_code == 404

    def test_sending_to_self_does_not_stamp_the_supplier_send_clock(
        self, client, analyst_headers, test_tenant
    ):
        """sent_at starts the payment clock for the SUPPLIER — forwarding the
        order to yourself has not reached them yet."""
        tid = test_tenant["id"]
        po_id = _make_po(client, analyst_headers, tid)
        _set_phone(tid, "+50670000002")

        with mock.patch("backend.notifications.whatsapp._send"):
            r = client.post(f"/api/v1/inventory/po/{po_id}/send-to-me",
                            headers=analyst_headers)
        assert r.status_code == 200
        row = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))
        assert row["sent_at"] is None
