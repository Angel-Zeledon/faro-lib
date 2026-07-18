from uuid import uuid4


def _sku():
    return f"OPTEP_{uuid4().hex[:8]}"


class TestOptimizeEndpoint:
    def test_viewer_can_read(self, client, viewer_headers, test_session):
        resp = client.get(
            "/api/v1/inventory/optimize",
            params={"session_id": test_session["id"], "horizon_days": 7},
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["orders"] == []
        assert data["transfers"] == []
        assert data["status"] == "optimal"

    def test_unauthenticated_rejected(self, client, test_session):
        resp = client.get(
            "/api/v1/inventory/optimize",
            params={"session_id": test_session["id"]},
        )
        assert resp.status_code == 401

    def test_returns_real_order_recommendation_for_understocked_sku(
        self, client, auth_headers, test_tenant, test_session,
    ):
        from backend.inventory import service as inv_svc
        from backend.db import session_store

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {
            "stock_actual": 0, "lead_time_dias": 0, "costo_unitario": 5.0, "bodega": "principal",
        })
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 10.0}] * 7}},
        })

        resp = client.get(
            "/api/v1/inventory/optimize",
            params={"session_id": sid, "horizon_days": 7},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        order = next(o for o in data["orders"] if o["sku"] == sku)
        assert order["bodega"] == "principal"
        assert order["qty"] > 0
