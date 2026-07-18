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

    def test_returns_real_transfer_recommendation_when_surplus_exists_elsewhere(
        self, client, auth_headers, test_session, monkeypatch,
    ):
        """
        build_optimization_input's demand-splitting (proportional to stock
        share) makes a genuine surplus/deficit pair hard to produce through
        real DB rows alone — a warehouse with less stock is also allocated
        proportionally less demand, so supply and demand are largely
        self-balancing by construction. Monkeypatching build_optimization_input
        isolates the endpoint's OWN wiring (optimize -> serialize) against a
        clear transfer-preferred input, the same way the solver's own transfer
        preference is proven in ForecastingCore/tests/test_optimizer_solve.py.
        """
        import backend.inventory.optimizer_service as opt_svc
        from forecasting_core.business.optimizer import OptimizationInput

        def _fake_build(tenant_id, session_id, horizon_days):
            return OptimizationInput(
                skus=["XFER-SKU"], warehouses=["Norte", "Sur"], horizon=horizon_days,
                demand={("XFER-SKU", "Norte"): [0.0] * horizon_days,
                        ("XFER-SKU", "Sur"): [20.0] * horizon_days},
                stock0={("XFER-SKU", "Norte"): 500.0, ("XFER-SKU", "Sur"): 0.0},
                lead_time_buckets={"XFER-SKU": 5},
                holding_cost={"XFER-SKU": 1.0}, stockout_cost={"XFER-SKU": 100.0},
                order_cost={"XFER-SKU": 10.0}, transfer_cost=0.1,
            )

        monkeypatch.setattr(opt_svc, "build_optimization_input", _fake_build)

        resp = client.get(
            "/api/v1/inventory/optimize",
            params={"session_id": test_session["id"], "horizon_days": 7},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        transfer = next(t for t in data["transfers"] if t["sku"] == "XFER-SKU")
        assert transfer["from_bodega"] == "Norte"
        assert transfer["to_bodega"] == "Sur"
        assert transfer["qty"] > 0
        assert not any(o["sku"] == "XFER-SKU" for o in data["orders"])
