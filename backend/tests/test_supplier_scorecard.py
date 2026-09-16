"""
Tests for the supplier scorecard (feature 2.5): real lead-time range,
on-time rate, and fill rate computed entirely from PO reception data
already recorded by feature 1.4. No external services involved.
"""

import uuid

from backend.db.connection import execute


def _make_supplier(tenant_id: str, name: str, lead_time_days: int = 10) -> None:
    # `lead_time_set_by` matters: the column is NOT NULL DEFAULT 15, so without
    # this stamp "the card says 10" and "nobody filled the card" are the same
    # row, and the scorecard now refuses to report an unset value as the
    # supplier's DECLARED promise. These tests model a supplier who really did
    # declare one, so they have to say so.
    execute(
        """INSERT INTO suppliers (tenant_id, name, lead_time_days, lead_time_set_by)
           VALUES (%s, %s, %s, 'user')""",
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

        assert row["n_receptions"] == 2
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

        assert row["n_receptions"] == 0
        assert row["lead_time_real_min"] is None
        assert row["lead_time_real_max"] is None
        assert row["lead_time_real_avg"] is None
        assert row["on_time_rate"] is None
        assert row["deviation_days"] is None
        assert row["last_reception"] is None
        # Nothing has arrived, and nothing is LATE either: the order was placed
        # moments ago against a 7-day lead time. Since 2026-09-16 (estabilidad
        # 11.12) fill rate judges only deliveries whose window has closed, so
        # this reads "not measurable yet" rather than a 0% verdict on a supplier
        # who is still well inside the time they promised.
        assert row["fill_rate"] is None
        assert row["orders_in_transit"] == 1
        # The money is not held back by the window: it left when the order was
        # placed, whatever is still on the road.
        assert row["purchased_value"] == 200.0  # 40 * 5.0, based on what was ordered
        assert row["purchased_value_complete"] is True

        # And once that window has closed, the same order is judged — at 0%,
        # because nothing ever came.
        execute(
            "UPDATE inventory_po_log SET generated_at = NOW() - INTERVAL '60 days' "
            "WHERE id = %s", (po,),
        )
        late = next(r for r in get_supplier_scorecard(tid) if r["supplier"] == prov)
        assert late["fill_rate"] == 0.0
        assert late["orders_in_transit"] == 0


class TestPurchasedValueDoesNotInventAZero:
    """
    `SUM(final_qty * unit_cost)` drops any line whose cost is NULL, because NULL
    annuls the product. COALESCEd to 0, a supplier you bought ₡40M from with no
    costs on file printed a confident ₡0 in bold green — while /impacto, one
    screen away, reports None for exactly the same quantity and says the figure
    is unavailable. Same data, two policies.
    """

    def _po_without_costs(self, client, auth_headers, *, sku, qty, supplier):
        resp = client.post(
            "/api/v1/inventory/log-po",
            params={"session_id": f"sess_test_{uuid.uuid4().hex[:6]}"},
            json={"items": [{
                "sku": sku, "display_name": f"Prod {sku}", "supplier": supplier,
                "signal": "PEDIR_YA", "recommended_qty": qty,
                "final_qty": qty, "status": "approved",
            }]},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["data"]["id"]

    def test_no_costs_at_all_reports_none_not_zero(self, client, auth_headers, test_tenant):
        from backend.inventory.reception_service import get_supplier_scorecard

        tid = test_tenant["id"]
        prov = f"NoCost-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=7)

        sku = f"NC-{uuid.uuid4().hex[:6]}"
        po = self._po_without_costs(client, auth_headers, sku=sku, qty=40, supplier=prov)
        resp = client.post(
            f"/api/v1/inventory/po/{po}/receive",
            json={"lines": [{"sku": sku, "received_qty": 40}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        row = next(r for r in get_supplier_scorecard(tid) if r["supplier"] == prov)
        assert row["purchased_value"] is None, (
            "a zero here reads as 'you bought nothing from them' — 40 units were ordered")
        assert row["purchased_value_complete"] is False

    def test_partial_cost_coverage_is_marked_rather_than_passed_off_as_the_total(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory.reception_service import get_supplier_scorecard

        tid = test_tenant["id"]
        prov = f"HalfCost-{uuid.uuid4().hex[:6]}"
        _make_supplier(tid, prov, lead_time_days=7)

        sku_costed, sku_bare = f"HC-{uuid.uuid4().hex[:6]}", f"HB-{uuid.uuid4().hex[:6]}"
        po = _make_po(client, auth_headers, sku=sku_costed, qty=10,
                      supplier=prov, unit_cost=3.0)
        po_bare = self._po_without_costs(
            client, auth_headers, sku=sku_bare, qty=500, supplier=prov)
        # BOTH orders have to be received: the fill query only counts lines from
        # POs that left 'pending', so an untouched order contributes nothing at
        # all — including nothing to the cost-coverage count.
        for po_id, sku, qty in ((po, sku_costed, 10), (po_bare, sku_bare, 500)):
            resp = client.post(
                f"/api/v1/inventory/po/{po_id}/receive",
                json={"lines": [{"sku": sku, "received_qty": qty}]},
                headers=auth_headers,
            )
            assert resp.status_code == 200, resp.text

        row = next(r for r in get_supplier_scorecard(tid) if r["supplier"] == prov)
        # The 500 uncosted units contribute nothing, so 30 is a floor and the
        # flag is what stops the UI presenting it as the whole story.
        assert row["purchased_value"] == 30.0
        assert row["purchased_value_complete"] is False


class TestSupplierScorecardEndpoint:
    def test_viewer_can_read(self, client, viewer_headers):
        resp = client.get("/api/v1/inventory/suppliers/scorecard", headers=viewer_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/inventory/suppliers/scorecard")
        assert resp.status_code == 401
