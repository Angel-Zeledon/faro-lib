"""
Tests for the promo/event impact simulator (feature 2.3).
Unit-level: simulate_event_impact math with a controlled inventory status.
API-level: endpoint validation and event resolution.
"""

from datetime import date, timedelta
from unittest import mock

import pytest

from backend.inventory import service as inv_svc


def _item(sku, daily, stock, lead_time=10, moq=5, cost=2.0):
    return {
        "sku": sku, "display_name": f"Prod {sku}", "proveedor": "Prov A",
        "demanda_diaria": daily, "stock_actual": stock,
        "lead_time_dias": lead_time, "moq": moq, "costo_unitario": cost,
    }


class TestSimulationMath:
    @pytest.mark.offline
    def test_deficit_and_order_rounded_to_moq(self):
        # Event in 20 days, 10 days long, ×2.0. Daily demand 10.
        start = date.today() + timedelta(days=20)
        end = start + timedelta(days=9)
        # Stock 250 → at event start: 250 − 10×20 = 50. Event needs 10×10×2=200.
        # Deficit 150 → with MOQ 40 → order 160.
        with mock.patch.object(inv_svc, "get_inventory_status",
                               return_value=[_item("A", 10, 250, lead_time=7, moq=40)]):
            res = inv_svc.simulate_event_impact("t", "s", start, end, 2.0, event_name="Promo")

        row = res["items"][0]
        assert row["baseline_units"] == 100.0
        assert row["event_units"] == 200.0
        assert row["extra_units"] == 100.0
        assert row["stock_al_inicio"] == 50.0
        assert row["deficit"] == 150.0
        assert row["cantidad_pedir"] == 160.0          # ceil(150/40)*40
        assert row["valor_pedido"] == 320.0            # 160 × $2
        assert row["en_riesgo"] is True
        assert row["llega_tarde"] is False             # order_by = start−7d, 13d away
        assert row["order_by"] == (start - timedelta(days=7)).isoformat()

        s = res["summary"]
        assert s["skus_en_riesgo"] == 1
        assert s["total_pedir"] == 160.0
        assert s["pedir_antes_de"] == row["order_by"]

    @pytest.mark.offline
    def test_healthy_stock_no_order(self):
        start = date.today() + timedelta(days=5)
        with mock.patch.object(inv_svc, "get_inventory_status",
                               return_value=[_item("B", 5, 10000)]):
            res = inv_svc.simulate_event_impact("t", "s", start, start + timedelta(days=6), 1.5)
        row = res["items"][0]
        assert row["deficit"] == 0.0
        assert row["cantidad_pedir"] is None
        assert row["en_riesgo"] is False
        assert res["summary"]["skus_en_riesgo"] == 0
        assert res["summary"]["pedir_antes_de"] is None

    @pytest.mark.offline
    def test_late_order_flagged_when_lead_time_exceeds_days_until_event(self):
        # Event starts in 3 days but lead time is 15 → ordering today is late.
        start = date.today() + timedelta(days=3)
        with mock.patch.object(inv_svc, "get_inventory_status",
                               return_value=[_item("C", 10, 0, lead_time=15)]):
            res = inv_svc.simulate_event_impact("t", "s", start, start, 2.0)
        row = res["items"][0]
        assert row["en_riesgo"] is True
        assert row["llega_tarde"] is True
        assert res["summary"]["algun_pedido_tarde"] is True

    @pytest.mark.offline
    def test_skus_without_forecast_are_skipped(self):
        start = date.today() + timedelta(days=5)
        items = [_item("D", 10, 100), {**_item("E", 0, 100), "demanda_diaria": None}]
        with mock.patch.object(inv_svc, "get_inventory_status", return_value=items):
            res = inv_svc.simulate_event_impact("t", "s", start, start, 1.5)
        assert [r["sku"] for r in res["items"]] == ["D"]

    @pytest.mark.offline
    def test_invalid_inputs_raise(self):
        today = date.today()
        with mock.patch.object(inv_svc, "get_inventory_status", return_value=[]):
            with pytest.raises(ValueError):
                inv_svc.simulate_event_impact("t", "s", today, today - timedelta(days=1), 1.5)
            with pytest.raises(ValueError):
                inv_svc.simulate_event_impact("t", "s", today, today, 0)


class TestSimulateEndpoint:
    def test_adhoc_requires_dates_and_multiplier(self, client, auth_headers):
        resp = client.post(
            "/api/v1/inventory/events/simulate",
            json={"session_id": "sess_x"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_unknown_event_404(self, client, auth_headers):
        resp = client.post(
            "/api/v1/inventory/events/simulate",
            json={"session_id": "sess_x", "event_id": "no-existe"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_simulate_from_saved_event(self, client, auth_headers, analyst_headers):
        start = (date.today() + timedelta(days=10)).isoformat()
        end = (date.today() + timedelta(days=14)).isoformat()
        ev = client.post(
            "/api/v1/inventory/events",
            json={"name": "Semana Santa Sim", "start_date": start, "end_date": end,
                  "multiplier": 1.8},
            headers=analyst_headers,
        )
        assert ev.status_code == 201
        event_id = ev.json()["data"]["id"]

        resp = client.post(
            "/api/v1/inventory/events/simulate",
            json={"session_id": "sess_sin_forecast", "event_id": event_id},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["event_name"] == "Semana Santa Sim"
        assert data["multiplier"] == 1.8
        assert data["event_days"] == 5
        # session without forecasts → no rows, but a valid, well-formed response
        assert data["items"] == []
        assert data["summary"]["skus_simulados"] == 0

        client.delete(f"/api/v1/inventory/events/{event_id}", headers=analyst_headers)
