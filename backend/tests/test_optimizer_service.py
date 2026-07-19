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
            "current_stock": 300, "lead_time_days": 10, "unit_cost": 20.0, "warehouse": "Norte",
        })
        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 100, "lead_time_days": 5, "unit_cost": 20.0, "warehouse": "Sur",
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
        assert inp.stockout_cost[sku] == 20.0 * 3.0  # order_cost * multiplier
        assert inp.order_cost[sku] == 20.0

    def test_splits_evenly_when_sku_has_zero_stock_everywhere(self, test_tenant, test_session):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.inventory.optimizer_service import build_optimization_input

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "warehouse": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "warehouse": "Sur"})
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

        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "warehouse": "principal"})
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 5.0}] * 7}},
        })

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert inp.order_cost[sku] == 1.0
        assert inp.holding_cost[sku] == 1.0 * 0.20 / 365


class TestSerializeOptimizationResult:
    def test_collapses_orders_and_transfers_across_horizon_and_drops_zeros(self):
        from forecasting_core.business.optimizer import OptimizationInput, OptimizationResult
        from backend.inventory.optimizer_service import serialize_optimization_result

        inp = OptimizationInput(
            skus=["SKU1"], warehouses=["Norte", "Sur"], horizon=2,
            demand={("SKU1", "Norte"): [5.0, 5.0], ("SKU1", "Sur"): [0.0, 0.0]},
            stock0={("SKU1", "Norte"): 0.0, ("SKU1", "Sur"): 20.0},
            lead_time_buckets={"SKU1": 0},
            holding_cost={"SKU1": 1.0}, stockout_cost={"SKU1": 10.0}, order_cost={"SKU1": 2.0},
            transfer_cost=0.5,
        )
        result = OptimizationResult(
            orders={("SKU1", "Norte", 1): 3.0, ("SKU1", "Norte", 2): 0.0,
                    ("SKU1", "Sur", 1): 0.0, ("SKU1", "Sur", 2): 0.0},
            transfers={("SKU1", "Sur", "Norte", 1): 4.0, ("SKU1", "Sur", "Norte", 2): 0.0,
                       ("SKU1", "Norte", "Sur", 1): 0.0, ("SKU1", "Norte", "Sur", 2): 0.0},
            inventory={}, shortages={},
            total_cost=12.3456, status="optimal",
        )
        stock_rows = [
            {"sku": "SKU1", "warehouse": "Norte", "unit_cost": 2.0, "supplier": "ACME"},
            {"sku": "SKU1", "warehouse": "Sur", "unit_cost": 2.0, "supplier": "ACME"},
        ]

        out = serialize_optimization_result(inp, result, stock_rows)

        assert out["status"] == "optimal"
        assert out["total_cost"] == 12.35
        assert out["horizon_days"] == 2
        assert out["orders"] == [
            {"sku": "SKU1", "warehouse": "Norte", "qty": 3.0, "unit_cost": 2.0, "supplier": "ACME"},
        ]
        assert out["transfers"] == [
            {"sku": "SKU1", "from_warehouse": "Sur", "to_warehouse": "Norte", "qty": 4.0},
        ]
