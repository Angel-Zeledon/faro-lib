"""
Two screens, one product, one purchase order.

`/hoy` (the semáforo) and `/planning` (the MILP optimizer) both end in a button
that turns their number into a purchase order, so they are not allowed to plan
on different inputs. They did:

  * the semáforo resolves a SKU's lead time through the whole cascade — the row
    only when somebody actually set it, then supplier / category / global rules,
    then the system default — and lets real receptions override the result.
    `optimizer_service` read `inventory_stock.lead_time_days` RAW, which is 15
    for every row nobody has ever edited, because that is the schema default.
  * the semáforo floors an order at the supplier's MOQ. The optimizer had no
    MOQ at all: there is no such field on `OptimizationInput`.

Measured before this file existed: `/hoy` planning on 45 days *"aprendido de tus
recepciones"* with an MOQ of 500 while `/planning` solved on 15 days and offered
137 units. Same SKU, same afternoon, two purchase orders.

Every assertion here reads the resolved values the two paths actually planned
on — the built `OptimizationInput` and the serialized plan — not the request
that produced them.
"""

import uuid
from datetime import datetime, timedelta

import pytest

from backend.db import session_store
from backend.db.connection import execute, query_one
from backend.inventory import optimizer_service as opt_svc
from backend.inventory import service as inv_svc
from backend.inventory import stock_defaults_service as sd_svc
from backend.inventory.defaults import DEFAULT_LEAD_TIME_DAYS, SOURCE_LEARNED
from backend.inventory.service import MIN_LEAD_TIME_OBSERVATIONS


def _sku():
    return f"CONV_{uuid.uuid4().hex[:8].upper()}"


def _flat_forecast(daily: float, days: int = 30) -> dict:
    return {"lightgbm": {"forecast": [
        {
            "date": (datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat(),
            "value": daily,
            "lower": daily,
            "upper": daily,
        }
        for i in range(days)
    ]}}


def _record_receptions(tenant_id: str, supplier: str, days: float, n: int) -> None:
    """`n` real reception observations for `supplier`, through the same table
    `reception_service.receive_po` writes (po_log_id is a FK, so each one needs
    a PO row)."""
    for _ in range(n):
        po = query_one(
            "INSERT INTO inventory_po_log (tenant_id, session_id) VALUES (%s, %s) "
            "RETURNING id",
            (tenant_id, f"ses_{uuid.uuid4().hex[:8]}"),
        )
        execute(
            "INSERT INTO supplier_lead_time_obs (tenant_id, po_log_id, supplier, "
            "lead_time_days) VALUES (%s, %s, %s, %s)",
            (tenant_id, po["id"], supplier, days),
        )


def _ok(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestTheOptimizerPlansOnTheSameSupplierInputs:

    def test_the_learned_lead_time_and_the_supplier_moq_reach_the_plan(
        self, client, auth_headers, test_tenant, test_session,
    ):
        """The convergence test: what `/hoy` shows and what `/planning` solves
        must be the same lead time and the same minimum.

        The stock row deliberately carries NO lead time of its own, so its
        column holds the schema's untouched 15 — the exact value the optimizer
        used to read straight out of the table and call a plan.
        """
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        supplier = f"Acme-{uuid.uuid4().hex[:6]}"

        _record_receptions(tid, supplier, 20.0, MIN_LEAD_TIME_OBSERVATIONS)
        sd_svc.set_stock_default(tid, "supplier", supplier, {"moq": 500})
        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 0, "unit_cost": 10.0, "supplier": supplier,
            "warehouse": "principal",
        })
        session_store.set_forecasts(tid, sid, {sku: _flat_forecast(5.0)})

        # The row really does still hold the default nobody chose — otherwise
        # this test would prove nothing about reading it raw.
        row = query_one(
            "SELECT lead_time_days, lead_time_set_by, moq, moq_set_by "
            "FROM inventory_stock WHERE tenant_id = %s AND sku = %s", (tid, sku))
        assert int(row["lead_time_days"]) == DEFAULT_LEAD_TIME_DAYS
        assert row["lead_time_set_by"] is None
        assert row["moq_set_by"] is None

        # ── What /hoy plans on ────────────────────────────────────────────────
        items = _ok(client.get(
            f"/api/v1/inventory/status?session_id={sid}", headers=auth_headers,
        ))["items"]
        item = next(i for i in items if i["sku"] == sku)
        assert item["lead_time_days"] == 20, "the learned average, not the raw 15"
        assert item["lead_time_source"] == SOURCE_LEARNED
        assert item["moq"] == 500.0
        assert item["recommended_qty"] >= 500.0

        # ── What /planning plans on ───────────────────────────────────────────
        inp = opt_svc.build_optimization_input(tid, sid, horizon_days=30)
        assert inp is not None and sku in inp.skus
        assert inp.lead_time_buckets[sku] == item["lead_time_days"], (
            "the optimizer solved on a different lead time than the semáforo "
            "showed — two screens, two purchase orders")

        plan = _ok(client.get(
            f"/api/v1/inventory/optimize?session_id={sid}&horizon_days=30",
            headers=auth_headers,
        ))
        line = next(o for o in plan["orders"] if o["sku"] == sku)
        assert line["qty"] >= item["moq"], (
            f"the plan offers {line['qty']} units against a supplier minimum of "
            f"{item['moq']} — an order this supplier will refuse")
        # Nothing above the minimum was invented either: the horizon's demand
        # net of the lead time is far below 500, so the MOQ is the whole number.
        assert line["qty"] == 500

    def test_a_supplier_rule_lead_time_reaches_the_optimizer(
        self, test_tenant, test_session,
    ):
        """A distributor configures 12 suppliers, not 2.000 SKUs. The rule that
        moves the semáforo has to move the plan, with no per-SKU row at all."""
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()
        supplier = f"Rule-{uuid.uuid4().hex[:6]}"

        sd_svc.set_stock_default(tid, "supplier", supplier, {"lead_time_days": 40})
        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 10, "unit_cost": 4.0, "supplier": supplier,
            "warehouse": "principal",
        })
        session_store.set_forecasts(tid, sid, {sku: _flat_forecast(3.0, days=60)})

        inp = opt_svc.build_optimization_input(tid, sid, horizon_days=60)

        assert inp.lead_time_buckets[sku] == 40, (
            "the supplier rule the semáforo plans on never reached the MILP")

    def test_the_moq_floor_is_one_order_not_one_per_warehouse(self):
        """A minimum order quantity is the floor under ONE purchase order to the
        supplier. Applied per line it would multiply by the number of warehouses
        the solve happens to spread the buy over — 2x the minimum, on money the
        buyer only gets back when the units sell."""
        from forecasting_core.business.optimizer import (
            OptimizationInput, OptimizationResult,
        )

        inp = OptimizationInput(
            skus=["SKU1"], warehouses=["Norte", "Sur"], horizon=1,
            demand={("SKU1", "Norte"): [0.0], ("SKU1", "Sur"): [0.0]},
            stock0={("SKU1", "Norte"): 0.0, ("SKU1", "Sur"): 0.0},
            lead_time_buckets={"SKU1": 0},
            holding_cost={"SKU1": 1.0}, stockout_cost={"SKU1": 10.0},
            order_cost={"SKU1": 2.0},
        )
        result = OptimizationResult(
            orders={("SKU1", "Norte", 1): 30.0, ("SKU1", "Sur", 1): 10.0},
            transfers={}, inventory={}, shortages={},
            total_cost=1.0, status="optimal",
        )
        stock_rows = [
            {"sku": "SKU1", "warehouse": "Norte", "unit_cost": 2.0, "supplier": "ACME"},
            {"sku": "SKU1", "warehouse": "Sur", "unit_cost": 2.0, "supplier": "ACME"},
        ]

        out = opt_svc.serialize_optimization_result(
            inp, result, stock_rows, planning={"SKU1": {"moq": 100.0}})

        by_wh = {o["warehouse"]: o["qty"] for o in out["orders"]}
        assert sum(by_wh.values()) == 100, (
            "the SKU's whole buy must reach the minimum exactly once")
        # The shortfall lands on the biggest line, deterministically.
        assert by_wh == {"Norte": 90, "Sur": 10}

    def test_a_well_stocked_sku_is_not_handed_a_minimum_order(self):
        """`nothing to order` has to keep meaning nothing — a floor applied to a
        zero would buy a full MOQ of a SKU nobody needs."""
        from forecasting_core.business.optimizer import (
            OptimizationInput, OptimizationResult,
        )

        inp = OptimizationInput(
            skus=["SKU1"], warehouses=["Norte"], horizon=1,
            demand={("SKU1", "Norte"): [0.0]}, stock0={("SKU1", "Norte"): 900.0},
            lead_time_buckets={"SKU1": 0}, holding_cost={"SKU1": 1.0},
            stockout_cost={"SKU1": 10.0}, order_cost={"SKU1": 2.0},
        )
        result = OptimizationResult(
            orders={("SKU1", "Norte", 1): 0.0}, transfers={}, inventory={},
            shortages={}, total_cost=0.0, status="optimal",
        )

        out = opt_svc.serialize_optimization_result(
            inp, result, [{"sku": "SKU1", "warehouse": "Norte", "unit_cost": 2.0}],
            planning={"SKU1": {"moq": 500.0}})

        assert out["orders"] == []


class TestAZeroUnitCostIsNotAnInvisibleSku:

    def test_the_sku_is_planned_and_its_line_admits_the_placeholder_price(
        self, client, auth_headers, test_tenant, test_session,
    ):
        """`unit_cost = 0` is a blank that happens to be a number.

        Left as a real price it zeroes the SKU's order cost, holding cost AND
        stockout penalty at once, so leaving its demand unmet costs the
        minimizer nothing and the SKU silently leaves the plan while `/hoy`
        goes on painting it PEDIR_YA. The engine input therefore substitutes the
        placeholder — and the line has to SAY it did, which is what
        `assumed_unit_cost` is for. It checked `is None`, so a stored 0.0
        produced a line claiming a real price and a `total_cost` computed from
        1.0.
        """
        tid, sid, sku = test_tenant["id"], test_session["id"], _sku()

        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 0, "unit_cost": 0.0, "warehouse": "principal",
        })
        session_store.set_forecasts(tid, sid, {sku: _flat_forecast(8.0)})

        row = query_one(
            "SELECT unit_cost FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tid, sku))
        assert float(row["unit_cost"]) == 0.0, "the zero really is on file"

        inp = opt_svc.build_optimization_input(tid, sid, horizon_days=30)
        assert inp.order_cost[sku] == 1.0, (
            "a zero cost makes every coefficient zero and the SKU free to ignore")
        assert inp.stockout_cost[sku] > 0

        plan = _ok(client.get(
            f"/api/v1/inventory/optimize?session_id={sid}&horizon_days=30",
            headers=auth_headers,
        ))
        assert sku not in plan["needs_stock"]
        line = next((o for o in plan["orders"] if o["sku"] == sku), None)
        assert line is not None, "the SKU vanished from the plan without a word"
        assert line["qty"] > 0
        assert line["assumed_unit_cost"] is True, (
            "the line was priced on the placeholder and did not say so")


class TestTwoBuyersCannotGetTwoDifferentPlans:

    def test_the_gate_admits_no_more_solves_than_the_solver_can_run(self):
        """Every HiGHS solve in the process runs on ONE dedicated thread (the
        deadlock fix — do not undo it). A gate that admits more callers than
        that does not buy concurrency: the extra caller QUEUES on that thread,
        and its wait — `time_limit_s + grace`, counted from SUBMIT — is spent
        while the first solve is still running. When it expires, `optimize()`
        treats it like any other unsolved case and returns the greedy
        `status="fallback"` plan, which ignores transfers entirely.

        So the second buyer's plan differed from the first's because of when
        they clicked. The cap has to match the executor's real width.
        """
        from forecasting_core.business import optimizer as core_opt

        assert core_opt._SOLVE_EXECUTOR._max_workers == 1, (
            "the single solver thread is the deadlock fix; if this changed, "
            "revisit the gate's cap deliberately rather than by accident")
        assert opt_svc._MAX_CONCURRENT_SOLVES <= core_opt._SOLVE_EXECUTOR._max_workers

    def test_the_second_buyer_is_refused_rather_than_served_a_different_plan(self):
        """A fast, honest 503 the browser can retry — not a quietly downgraded
        plan with the same 'plan de optimización' heading on it."""
        with opt_svc.solve_slot():
            with pytest.raises(opt_svc.OptimizerBusy):
                with opt_svc.solve_slot():
                    pass

    def test_a_caller_that_waits_behind_a_running_solve_gets_the_greedy_plan(
        self, monkeypatch,
    ):
        """The harm itself, reproduced against the real engine: this is what the
        second admitted caller used to receive. It is a property of the single
        solver thread, so the only defense is not admitting that caller."""
        import threading

        from forecasting_core.business import optimizer as core_opt
        from forecasting_core.business.optimizer import OptimizationInput

        inp = OptimizationInput(
            skus=["SKU1"], warehouses=["principal"], horizon=3,
            demand={("SKU1", "principal"): [5.0, 5.0, 5.0]},
            stock0={("SKU1", "principal"): 0.0},
            lead_time_buckets={"SKU1": 1}, holding_cost={"SKU1": 0.1},
            stockout_cost={"SKU1": 30.0}, order_cost={"SKU1": 10.0},
        )
        # Solved alone, this is a real MILP answer.
        assert core_opt.optimize(inp).status == "optimal"

        monkeypatch.setattr(core_opt, "_SOLVE_WAIT_GRACE_S", 0.05)
        occupied = threading.Event()
        release = threading.Event()

        def _hold():
            occupied.set()
            release.wait(5.0)

        holder = core_opt._SOLVE_EXECUTOR.submit(_hold)
        try:
            assert occupied.wait(2.0), "the solver thread never picked the task up"
            degraded = core_opt.optimize(inp, time_limit_s=0.05)
        finally:
            release.set()
            holder.result(5.0)

        assert degraded.status == "fallback", (
            "if a queued caller no longer degrades, the gate's cap can be "
            "raised again — deliberately")
