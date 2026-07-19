"""
Tests for the supplier scorecard (feature 2.5): real lead-time range,
on-time rate, and fill rate computed entirely from PO reception data
already recorded by feature 1.4. No external services involved.
"""

import uuid

from backend.db.connection import execute


def _make_supplier(tenant_id: str, name: str, lead_time_days: int = 10) -> None:
    execute(
        "INSERT INTO suppliers (tenant_id, name, lead_time_days) VALUES (%s, %s, %s)",
        (tenant_id, name, lead_time_days),
    )


def _make_po(client, auth_headers, *, sku: str, qty: float, supplier: str, unit_cost: float = 2.0) -> str:
    resp = client.post(
        "/api/v1/inventory/log-po",
        params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
        json={"items": [{
            "sku": sku, "display_name": f"Prod {sku}", "supplier": supplier,
            "signal": "PEDIR_YA", "recommended_qty": qty,
            "final_qty": qty, "unit_cost": unit_cost, "status": "approved",
        }]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _set_generated_at(po_log_id: str, iso_dt: str) -> None:
    execute(
        "UPDATE inventory_po_log SET generated_at = %s WHERE id = %s",
        (iso_dt, po_log_id),
    )


class TestGetSupplierScorecard:
    def test_computes_lead_time_range_on_time_rate_and_fill_rate(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_supplier_scorecard

        tid = test_tenant["id"]
        prov = f"Prov-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=10)

        # PO 1: generated 2026-01-01, received 2026-01-09 -> 8 days, on time (<=10).
        sku1 = f"SC1-{uuid.uuid4().hex[:6]}"
        po1 = _make_po(client, auth_headers, sku=sku1, qty=50, supplier=prov, unit_cost=2.0)
        _set_generated_at(po1, "2026-01-01T00:00:00Z")
        resp1 = client.post(
            f"/api/v1/inventory/po/{po1}/receive",
            json={"received_at": "2026-01-09T00:00:00Z"},
            headers=auth_headers,
        )
        assert resp1.status_code == 200, resp1.text

        # PO 2: generated 2026-02-01, received 2026-02-15 -> 14 days, late (>10).
        sku2 = f"SC2-{uuid.uuid4().hex[:6]}"
        po2 = _make_po(client, auth_headers, sku=sku2, qty=20, supplier=prov, unit_cost=3.0)
        _set_generated_at(po2, "2026-02-01T00:00:00Z")
        resp2 = client.post(
            f"/api/v1/inventory/po/{po2}/receive",
            json={"received_at": "2026-02-15T00:00:00Z"},
            headers=auth_headers,
        )
        assert resp2.status_code == 200, resp2.text

        # PO 3: never received (still pending) -> must be excluded from fill_rate/purchased_value.
        sku3 = f"SC3-{uuid.uuid4().hex[:6]}"
        _make_po(client, auth_headers, sku=sku3, qty=30, supplier=prov, unit_cost=1.0)

        rows = get_supplier_scorecard(tid)
        row = next(r for r in rows if r["supplier"] == prov)

        assert row["n_recepciones"] == 2
        assert row["lead_time_real_min"] == 8.0
        assert row["lead_time_real_max"] == 14.0
        assert row["lead_time_real_avg"] == 11.0
        assert row["lead_time_declarado"] == 10
        assert row["on_time_rate"] == 0.5          # 1 of 2 receptions on time
        assert row["fill_rate"] == 1.0              # 70/70 received, PO3 excluded (pending)
        assert row["purchased_value"] == 160.0        # 50*2.0 + 20*3.0, PO3's 30*1.0 excluded

    def test_supplier_without_receptions_not_included(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_supplier_scorecard

        tid = test_tenant["id"]
        prov = f"NoRecep-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=5)

        rows = get_supplier_scorecard(tid)

        assert not any(r["supplier"] == prov for r in rows)

    def test_supplier_with_fill_data_but_no_lead_time_observation_still_appears(
        self, client, auth_headers, test_tenant
    ):
        from backend.inventory.reception_service import get_supplier_scorecard

        tid = test_tenant["id"]
        prov = f"ZeroRecv-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=7)

        sku = f"SC0-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku, qty=40, supplier=prov, unit_cost=5.0)

        # Reception with 0 units received for every line: leaves 'pending',
        # sets reception_status to 'not_received', and writes NO row to
        # supplier_lead_time_obs (no units received => no supplier observed).
        resp = client.post(
            f"/api/v1/inventory/po/{po}/receive",
            json={"lines": [{"sku": sku, "received_qty": 0}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["reception_status"] == "not_received"

        rows = get_supplier_scorecard(tid)
        row = next(r for r in rows if r["supplier"] == prov)

        assert row["n_recepciones"] == 0
        assert row["lead_time_real_min"] is None
        assert row["lead_time_real_max"] is None
        assert row["lead_time_real_avg"] is None
        assert row["on_time_rate"] is None
        assert row["deviation_days"] is None
        assert row["ultima_recepcion"] is None
        assert row["fill_rate"] == 0.0
        assert row["purchased_value"] == 200.0  # 40 * 5.0, based on what was ordered


class TestSupplierScorecardEndpoint:
    def test_viewer_can_read(self, client, viewer_headers):
        resp = client.get("/api/v1/inventory/suppliers/scorecard", headers=viewer_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/suppliers/scorecard")
        assert resp.status_code == 401
