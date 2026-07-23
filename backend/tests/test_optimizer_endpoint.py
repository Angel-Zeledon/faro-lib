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

    def test_pool_exhaustion_returns_503_not_500(
        self, client, auth_headers, test_session, monkeypatch,
    ):
        """
        Under a concurrent burst the DB pool (ThreadedConnectionPool, max=10)
        raises PoolError rather than blocking when every connection is checked
        out. That is transient and retryable, so the optimize endpoint must map
        it to 503, not surface a bare 500 (which reads as a server bug). This
        is the defensive fix for the one-off 500 QA saw during a ~15-request
        burst against /hoy.
        """
        from psycopg2.pool import PoolError
        import backend.inventory.service as inv_service

        def _raise_pool_exhausted(*args, **kwargs):
            raise PoolError("connection pool exhausted")

        # The endpoint reads the stock snapshot via svc.list_stock before the
        # solve; make that call hit an exhausted pool.
        monkeypatch.setattr(inv_service, "list_stock", _raise_pool_exhausted)

        resp = client.get(
            "/api/v1/inventory/optimize",
            params={"session_id": test_session["id"], "horizon_days": 7},
            headers=auth_headers,
        )
        assert resp.status_code == 503

    def test_concurrency_gate_returns_503_when_full(
        self, client, auth_headers, test_tenant, test_session,
    ):
        """QA NEW-2: a MILP solve is CPU-bound; an unthrottled burst can occupy
        every thread-pool worker and wedge the server. The bounded gate rejects
        excess requests fast with 503 instead of piling onto the pool."""
        from backend.inventory import optimizer_service as opt_svc
        from backend.inventory import service as inv_svc
        from backend.db import session_store

        tid, sid = test_tenant["id"], test_session["id"]
        sku = _sku()
        # Seed real stock + forecast so the request reaches the solve (an empty
        # input returns early, before the gate — that path is cheap by design).
        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 0, "lead_time_days": 3, "unit_cost": 5.0,
            "warehouse": "principal",
        })
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 10.0}] * 7}},
        })

        # Fill every solve slot, then a request must be turned away with 503.
        acquired = []
        while opt_svc._solve_gate.acquire(blocking=False):
            acquired.append(True)
        try:
            resp = client.get(
                "/api/v1/inventory/optimize",
                params={"session_id": sid, "horizon_days": 7},
                headers=auth_headers,
            )
            assert resp.status_code == 503
        finally:
            for _ in acquired:
                opt_svc._solve_gate.release()

    def test_returns_real_order_recommendation_for_understocked_sku(
        self, client, auth_headers, test_tenant, test_session,
    ):
        from backend.inventory import service as inv_svc
        from backend.db import session_store

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {
            # lead_time_days must be >=1 and < horizon so an order arrives in time;
            # sanitized by upsert_stock, same as the API's ge=1 validation).
            "current_stock": 0, "lead_time_days": 3, "unit_cost": 5.0, "warehouse": "principal",
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
        assert order["warehouse"] == "principal"
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

        def _fake_build(tenant_id, session_id, horizon_days, stock_rows=None):
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
        assert transfer["from_warehouse"] == "Norte"
        assert transfer["to_warehouse"] == "Sur"
        assert transfer["qty"] > 0
        assert not any(o["sku"] == "XFER-SKU" for o in data["orders"])
