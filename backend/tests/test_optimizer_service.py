from uuid import uuid4

import pytest


def _sku():
    return f"OPT_{uuid4().hex[:8]}"


class TestBuildOptimizationInput:
    def test_returns_none_when_no_forecasts(self, test_tenant, test_session):
        from backend.inventory.optimizer_service import build_optimization_input

        tid = test_tenant["id"]
        sid = test_session["id"]

        result = build_optimization_input(tid, sid, horizon_days=7)
        assert result is None

    def test_demand_follows_configured_shares_not_where_the_stock_sits(
        self, test_tenant, test_session,
    ):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.inventory.optimizer_service import build_optimization_input

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = _sku()

        # Norte holds 3x the stock of Sur, which must NOT move the demand split.
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
        # Demand follows the tenant's configured demand shares, NOT the stock
        # split. This assertion used to read 30/10 — 40 * (300/400) — which
        # pinned the defect: a warehouse's need was defined as proportional to
        # what it already held, so the depot holding everything was told to buy
        # more and a store holding none of a SKU it sells was assigned zero
        # demand and could never be a transfer destination. Neither warehouse
        # here has a `demand_share`, so `get_demand_shares` gives the whole
        # demand to the default one — the same answer the per-warehouse
        # semáforo gives, which is the point: one authority, not two.
        assert inp.demand[(sku, "Norte")][0] == 40.0
        assert inp.demand[(sku, "Sur")][0] == 0.0
        # The SKU's lead time, resolved once for the whole product by
        # `resolve_planning_inputs` — the same cascade `/hoy` runs, on the same
        # representative row (`service._aggregate_stock_rows_by_sku` picks
        # Norte: no anchored default warehouse, and "norte" sorts before "sur").
        # It used to be `max(10, 5)` across the raw rows, which was the
        # optimizer answering a question every other screen answers differently.
        assert inp.lead_time_buckets[sku] == 10
        assert inp.holding_cost[sku] == 20.0 * 0.20 / 365
        assert inp.stockout_cost[sku] == 20.0 * 3.0  # order_cost * multiplier
        assert inp.order_cost[sku] == 20.0

    def test_zero_stock_everywhere_does_not_invent_an_even_split(self, test_tenant, test_session):
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

        # This used to assert 5.0 / 5.0 — an even split invented because the
        # stock-share denominator was zero. Splitting evenly is not a neutral
        # default: it orders a SKU into warehouses that have never stocked it.
        # With no demand shares configured the demand belongs to the default
        # warehouse, and that is a statement the tenant can see and change.
        assert inp.demand[(sku, "Norte")][0] == 10.0
        assert inp.demand[(sku, "Sur")][0] == 0.0
        # Whatever the split, it must still be the whole demand — never more.
        assert (inp.demand[(sku, "Norte")][0] + inp.demand[(sku, "Sur")][0]) == 10.0

    def test_configured_shares_are_what_actually_splits_the_demand(
        self, test_tenant, test_session,
    ):
        """The other half of the fix: /bodegas is the one place a tenant says
        where a SKU sells, and the optimizer must read it — the same numbers the
        per-warehouse semáforo reads, so a buy line and a semáforo row can no
        longer disagree about the same warehouse."""
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc
        from backend.db import session_store
        from backend.inventory.optimizer_service import build_optimization_input

        tid, sid = test_tenant["id"], test_session["id"]
        sku = _sku()

        # Sur holds far more stock; the shares say the demand is mostly Norte's.
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "warehouse": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"current_stock": 900, "warehouse": "Sur"})
        wh_svc.set_demand_share(tid, "Norte", 80)
        wh_svc.set_demand_share(tid, "Sur", 20)
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 10.0}] * 7}},
        })

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert inp.demand[(sku, "Norte")][0] == pytest.approx(8.0)
        assert inp.demand[(sku, "Sur")][0] == pytest.approx(2.0)

    def test_a_share_on_a_warehouse_with_no_stock_rows_is_not_swallowed(
        self, test_tenant, test_session,
    ):
        """Shares are configured over ALL warehouses; the optimizer only plans
        for the ones that have inventory rows. Applied raw, the half assigned to
        a warehouse the model cannot buy into would simply vanish and every
        remaining location would be planned short of what it sells."""
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc
        from backend.db import session_store
        from backend.inventory.optimizer_service import build_optimization_input

        tid, sid = test_tenant["id"], test_session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "warehouse": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "warehouse": "Sur"})
        wh_svc.create_warehouse(tid, "Bodega Vacia")
        wh_svc.set_demand_share(tid, "Norte", 25)
        wh_svc.set_demand_share(tid, "Sur", 25)
        wh_svc.set_demand_share(tid, "Bodega Vacia", 50)
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 10.0}] * 7}},
        })

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert "Bodega Vacia" not in inp.warehouses
        # Renormalized over the two planned warehouses: 50/50 of the whole
        # demand, not 25/25 of it with half quietly dropped on the floor.
        assert inp.demand[(sku, "Norte")][0] == pytest.approx(5.0)
        assert inp.demand[(sku, "Sur")][0] == pytest.approx(5.0)
        total = sum(inp.demand[(sku, w)][0] for w in inp.warehouses)
        assert total == pytest.approx(10.0)

    def test_store_keyed_forecasts_beat_the_configured_shares(
        self, test_tenant, test_session,
    ):
        """A measurement of what a location actually sold outranks a percentage
        somebody typed — the same preference order the per-warehouse semáforo
        uses, deliberately."""
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc
        from backend.db import session_store
        from backend.inventory.series import SERIES_SEPARATOR
        from backend.inventory.optimizer_service import build_optimization_input

        tid, sid = test_tenant["id"], test_session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "warehouse": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "warehouse": "Sur"})
        wh_svc.set_demand_share(tid, "Norte", 90)
        wh_svc.set_demand_share(tid, "Sur", 10)
        session_store.set_forecasts(tid, sid, {
            f"{sku}{SERIES_SEPARATOR}Norte": {
                "lightgbm": {"forecast": [{"date": "2026-01-01", "value": 3.0}] * 7},
            },
            f"{sku}{SERIES_SEPARATOR}Sur": {
                "lightgbm": {"forecast": [{"date": "2026-01-01", "value": 7.0}] * 7},
            },
        })

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert inp.demand[(sku, "Norte")][0] == pytest.approx(3.0)
        assert inp.demand[(sku, "Sur")][0] == pytest.approx(7.0)

    def test_a_sku_with_no_stock_on_file_is_not_optimized_at_all(
        self, test_tenant, test_session,
    ):
        """
        How much to buy is a function of how much is left, and how much is left
        is exactly what nobody told us. The optimizer used to answer anyway:
        `float(current_stock or 0)` assumed an empty shelf — the assumption that
        produces the LARGEST possible order.

        Measured before this: with SKU A carrying no stock row, the purchasing
        panel told the buyer to order 130 units of it, complete with a
        Convert-to-PO button, while the inventory screen was refusing to give
        the same SKU any signal ("SIN_DATOS — agrega stock actual para ver la
        señal"). Two screens, one product, opposite advice, and the one with
        the buy button was the one that had invented its input.
        """
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.inventory.optimizer_service import (
            build_optimization_input, skus_missing_stock,
        )

        tid, sid = test_tenant["id"], test_session["id"]
        counted, uncounted = _sku(), _sku()

        inv_svc.upsert_stock(tid, counted, {
            "current_stock": 50, "lead_time_days": 5, "unit_cost": 3.0,
            "warehouse": "principal",
        })
        forecast = {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 9.0}] * 7}}
        session_store.set_forecasts(tid, sid, {counted: forecast, uncounted: forecast})

        inp = build_optimization_input(tid, sid, horizon_days=7)

        assert inp is not None
        assert counted in inp.skus, "a counted SKU must still be optimized"
        assert uncounted not in inp.skus, (
            "a SKU nobody has counted must not get a purchase quantity"
        )

    def test_the_uncounted_skus_are_named_rather_than_silently_dropped(self):
        """Dropping them quietly is the same defect wearing a different hat:
        "no suggestions" and "no suggestions BECAUSE nobody counted" look
        identical on screen, and only one of them is the user's to fix."""
        from backend.inventory.optimizer_service import skus_missing_stock

        rows = [
            {"sku": "HAS", "warehouse": "principal", "current_stock": 10},
            {"sku": "NULL_STOCK", "warehouse": "principal", "current_stock": None},
        ]
        forecasts = {"HAS": {}, "NULL_STOCK": {}, "NO_ROW_AT_ALL": {}}

        assert skus_missing_stock(forecasts, rows) == ["NO_ROW_AT_ALL", "NULL_STOCK"]

    def test_a_counted_zero_is_not_the_same_as_uncounted(self):
        """Zero on the shelf is a fact and must still be optimized; the whole
        point is that "we counted none" and "nobody counted" are different."""
        from backend.inventory.optimizer_service import skus_missing_stock

        rows = [{"sku": "EMPTY", "warehouse": "principal", "current_stock": 0}]
        assert skus_missing_stock({"EMPTY": {}}, rows) == []

    def test_uses_provided_stock_rows_without_requerying(
        self, test_tenant, test_session, monkeypatch,
    ):
        """
        When the caller passes stock_rows, build_optimization_input must NOT
        call list_stock again — the optimize endpoint relies on this to read
        inventory_stock once per request instead of twice, cutting the path's
        pooled-connection checkouts under concurrent load.
        """
        from backend.db import session_store
        import backend.inventory.optimizer_service as opt_svc

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = _sku()

        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 5.0}] * 7}},
        })

        def _boom(*args, **kwargs):
            raise AssertionError("list_stock must not be called when stock_rows is provided")

        monkeypatch.setattr(opt_svc, "list_stock", _boom)

        provided = [{
            "sku": sku, "warehouse": "principal", "current_stock": 12,
            "lead_time_days": 4, "unit_cost": 7.0,
        }]
        inp = opt_svc.build_optimization_input(
            tid, sid, horizon_days=7, stock_rows=provided,
        )

        assert inp is not None
        assert inp.warehouses == ["principal"]
        assert inp.stock0[(sku, "principal")] == 12.0
        assert inp.order_cost[sku] == 7.0

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
        # `assumed_unit_cost` says whether this line's share of `total_cost` was
        # computed from a real cost or from the optimizer's placeholder. It is
        # False here because the stock row carries one.
        assert out["orders"] == [
            {"sku": "SKU1", "warehouse": "Norte", "qty": 3.0, "unit_cost": 2.0,
             "supplier": "ACME", "assumed_unit_cost": False,
             # What the line covers (math audit O1): the horizon it was
             # planned on, not extended, no extrapolated demand.
             "effective_horizon_days": 2, "horizon_extended": False,
             "demand_extrapolated": False},
        ]
        assert out["extended_lines"] == 0
        assert out["transfers"] == [
            {"sku": "SKU1", "from_warehouse": "Sur", "to_warehouse": "Norte", "qty": 4.0},
        ]

    def test_a_line_with_no_cost_on_file_says_its_total_is_a_placeholder(self):
        """`total_cost` is priced at _DEFAULT_UNIT_COST when nobody gave us one,
        so the line has to admit that rather than look like a real amount."""
        from forecasting_core.business.optimizer import OptimizationInput, OptimizationResult
        from backend.inventory.optimizer_service import serialize_optimization_result

        inp = OptimizationInput(
            skus=["SKU1"], warehouses=["Norte"], horizon=1,
            demand={("SKU1", "Norte"): [0.0]},
            stock0={("SKU1", "Norte"): 0.0},
            lead_time_buckets={"SKU1": 0},
            holding_cost={"SKU1": 1.0}, stockout_cost={"SKU1": 10.0},
            order_cost={"SKU1": 1.0},
        )
        result = OptimizationResult(
            status="optimal", total_cost=1.0,
            orders={("SKU1", "Norte", 0): 3.0}, transfers={},
            inventory={}, shortages={},
        )
        out = serialize_optimization_result(
            inp, result, [{"sku": "SKU1", "warehouse": "Norte", "unit_cost": None}])

        assert out["orders"][0]["assumed_unit_cost"] is True
        assert out["orders"][0]["unit_cost"] is None

    def test_fractional_quantities_round_up_to_whole_units(self):
        """Coarser periods (weekly/monthly) can produce fractional solve totals;
        you cannot order or move a fraction of a unit, so quantities must be
        whole numbers rounded up (never under-order)."""
        from forecasting_core.business.optimizer import OptimizationInput, OptimizationResult
        from backend.inventory.optimizer_service import serialize_optimization_result

        inp = OptimizationInput(
            skus=["SKU1"], warehouses=["Norte", "Sur"], horizon=1,
            demand={("SKU1", "Norte"): [0.0], ("SKU1", "Sur"): [0.0]},
            stock0={("SKU1", "Norte"): 0.0, ("SKU1", "Sur"): 0.0},
            lead_time_buckets={"SKU1": 0},
            holding_cost={"SKU1": 1.0}, stockout_cost={"SKU1": 10.0}, order_cost={"SKU1": 2.0},
            transfer_cost=0.5,
        )
        result = OptimizationResult(
            orders={("SKU1", "Norte", 1): 3692.67},
            transfers={("SKU1", "Sur", "Norte", 1): 0.01},
            inventory={}, shortages={},
            total_cost=1.0, status="optimal",
        )
        stock_rows = [
            {"sku": "SKU1", "warehouse": "Norte", "unit_cost": 2.0, "supplier": "ACME"},
            {"sku": "SKU1", "warehouse": "Sur", "unit_cost": 2.0, "supplier": "ACME"},
        ]

        out = serialize_optimization_result(inp, result, stock_rows)

        assert out["orders"][0]["qty"] == 3693
        assert isinstance(out["orders"][0]["qty"], int)
        # The two sides round in OPPOSITE directions, and both directions are the
        # safe one for what they do. An order rounds UP: buying 3693 instead of
        # 3692.67 cannot cause a stockout. A transfer rounds DOWN: rounding 0.01
        # units up to 1 would take a real unit off a real shelf and send a person
        # to move it, on the strength of solver noise. Sub-unit moves are dropped.
        assert out["transfers"] == [], (
            "0.01 units became a warehouse trip")
