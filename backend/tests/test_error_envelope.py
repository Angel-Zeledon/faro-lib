"""
Error envelope contract (slice 1 of "no Spanish in backend code").

Backend user-facing errors now raise ``AppError`` (backend/errors.py) carrying a
stable machine ``error_code`` + ``error_params`` and an English fallback
``message``. The handler in backend/main.py serializes them to a JSON error
response that keeps the existing ``detail`` field (English fallback) and ADDS
``error_code`` + ``error_params`` — the frontend renders the localized (Spanish)
``errors.<error_code>`` string, interpolating params.

These tests pin that reusable contract on the reception-over-pending case: the
response carries the code + params, and the over-receipt guard still blocks with
NO state change (received_qty untouched, PO header still pending). Later slices
copy the same code+params convention.
"""

from uuid import uuid4

from backend.db.connection import query_one
from backend.inventory import roi_service


def _make_po(tenant_id: str, sku: str, final_qty: float) -> dict:
    """A single-line approved PO destined for the default warehouse."""
    return roi_service.log_po_generation(tenant_id, "sess-test", [{
        "sku": sku, "final_qty": final_qty, "status": "approved",
        "warehouse": "principal",
    }])


class TestReceptionOverPendingEnvelope:
    def test_over_pending_carries_code_params_and_blocks(
        self, client, analyst_headers, registered_user
    ):
        tenant_id = registered_user["tenant"]["id"]
        sku = f"OVR_{uuid4().hex[:8]}"
        po = _make_po(tenant_id, sku, final_qty=10)

        resp = client.post(
            f"/api/v1/inventory/po/{po['id']}/receive",
            json={"lines": [{"sku": sku, "received_qty": 25}]},
            headers=analyst_headers,
        )

        # Analyst passes the role gate and reaches the business rule (422, not
        # 403), and the response carries the machine code + params alongside the
        # English fallback in `detail`.
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error_code"] == "reception_over_pending"
        assert body["error_params"]["sku"] == sku
        assert float(body["error_params"]["qty"]) == 25
        assert float(body["error_params"]["pending"]) == 10
        # English fallback message, no Spanish in the backend payload.
        assert body["detail"]
        assert "excede" not in body["detail"].lower()

        # Guard still blocks with no state change: nothing received, PO pending.
        item = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE po_log_id=%s AND sku=%s",
            (po["id"], sku),
        )
        assert item["received_qty"] in (None, 0)
        log = query_one(
            "SELECT reception_status FROM inventory_po_log WHERE id=%s", (po["id"],)
        )
        assert log["reception_status"] == "pending"

    def test_viewer_denied_and_state_unchanged(
        self, client, viewer_headers, registered_user
    ):
        tenant_id = registered_user["tenant"]["id"]
        sku = f"OVRV_{uuid4().hex[:8]}"
        po = _make_po(tenant_id, sku, final_qty=10)

        resp = client.post(
            f"/api/v1/inventory/po/{po['id']}/receive",
            json={"lines": [{"sku": sku, "received_qty": 25}]},
            headers=viewer_headers,
        )
        # Viewer denied before the endpoint body runs — permission pair with the
        # analyst success above.
        assert resp.status_code == 403

        log = query_one(
            "SELECT reception_status FROM inventory_po_log WHERE id=%s", (po["id"],)
        )
        assert log["reception_status"] == "pending"
        item = query_one(
            "SELECT received_qty FROM inventory_po_items WHERE po_log_id=%s AND sku=%s",
            (po["id"], sku),
        )
        assert item["received_qty"] in (None, 0)
