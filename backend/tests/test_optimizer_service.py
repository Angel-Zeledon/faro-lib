from uuid import uuid4


def _sku():
    return f"OPT_{uuid4().hex[:8]}"


class TestBuildOptimizationInput:
    def test_returns_none_when_no_forecasts(self, test_tenant, test_session):
        from backend.inventory.optimizer_service import build_optimization_input

        tid = test_tenant["id"]
        sid = test_session["id"]

        result = build_optimization_input(tid, sid, horizon_days=7)
        assert result is None

    def test_splits_demand_proportional_to_stock_share(self, test_tenant, test_session):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.inventory.optimizer_service import build_optimization_input

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = _sku()

        # Norte holds 3x the stock of Sur -> demand should split 75/25.
        inv_svc.upsert_stock(tid, sku, {
            "stock_actual": 300, "lead_time_dias": 10, "costo_unitario": 20.0, "bodega": "Norte",
        })
        inv_svc.upsert_stock(tid, sku, {
            "stock_actual": 100, "lead_time_dias": 5, "costo_unitario": 20.0, "bodega": "Sur",
        })
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 40.0}] * 7}},
        })

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert inp is not None
        assert sku in inp.skus
        assert set(inp.warehouses) == {"Norte", "Sur"}
        assert inp.stock0[(sku, "Norte")] == 300.0
        assert inp.stock0[(sku, "Sur")] == 100.0
        assert inp.demand[(sku, "Norte")][0] == 30.0  # 40 * (300/400)
        assert inp.demand[(sku, "Sur")][0] == 10.0     # 40 * (100/400)
        assert inp.lead_time_buckets[sku] == 10        # max(10, 5)
        assert inp.holding_cost[sku] == 20.0 * 0.20 / 365
        assert inp.stockout_cost[sku] == inp.holding_cost[sku] * 3.0
        assert inp.order_cost[sku] == 20.0

    def test_splits_evenly_when_sku_has_zero_stock_everywhere(self, test_tenant, test_session):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.inventory.optimizer_service import build_optimization_input

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"stock_actual": 0, "bodega": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"stock_actual": 0, "bodega": "Sur"})
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 10.0}] * 7}},
        })

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert inp.demand[(sku, "Norte")][0] == 5.0
        assert inp.demand[(sku, "Sur")][0] == 5.0

    def test_missing_cost_data_defaults_to_one(self, test_tenant, test_session):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.inventory.optimizer_service import build_optimization_input

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"stock_actual": 10, "bodega": "principal"})
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 5.0}] * 7}},
        })

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert inp.order_cost[sku] == 1.0
        assert inp.holding_cost[sku] == 1.0 * 0.20 / 365
