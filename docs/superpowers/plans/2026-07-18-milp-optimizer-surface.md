# MW-3: MILP Optimizer Purchasing/Transfers Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the already-built MILP inventory optimizer (`ForecastingCore/forecasting_core/business/optimizer.py`, merged to `main`) through a backend endpoint and a frontend view, so users see suggested purchase quantities per warehouse and recommended inter-warehouse transfers, and can convert a suggestion straight into a PO.

**Architecture:** A new backend service module assembles an `OptimizationInput` from existing data (per-bodega `inventory_stock`, session forecasts, `business_cfg`), calls the pure `optimize()` function, and collapses its per-day-bucket result into one actionable total per (SKU, bodega) — matching how the rest of the app already treats PO lines (no day-by-day granularity). A new `GET /inventory/optimize` endpoint wires this together. The frontend adds a "recomendaciones de compra y transferencias" section to `/hoy`, reusing the existing `logPOGeneration` PO-creation path for the "convert to PO" action.

**Tech Stack:** FastAPI (backend), `scipy.optimize.milp` via the existing `ForecastingCore` optimizer (no new dependency), Next.js/React + TypeScript (frontend), pytest (backend tests).

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-07-12-multi-warehouse-milp-design.md`, "Sub-proyecto 6 — Superficie de compras/transferencias (Plan MW-3)".
- Permission: `GET /inventory/optimize` uses `Depends(get_current_user)` (any authenticated role, including viewer) — matches the house precedent for every other expensive *computed but read-only* endpoint in `backend/api/v1/inventory.py` (`/suppliers/scorecard`, `/dead-stock`, `/morning-briefing`, `/production-requirements`), none of which use `require_analyst_or_above` despite doing real computation.
- SKU scoping: only SKUs present in `session_store.get_forecasts(tenant_id, session_id)` are considered — `inventory_stock` is tenant-wide (accumulates across sessions) and must never itself drive which SKUs appear. This mirrors `get_inventory_status`'s existing rule (`backend/inventory/service.py:406`, code comment there explains why).
- Bucket granularity: one bucket = one day. `horizon_days` (default 14, range 1-30) is both the MILP horizon `H` and the query param name, mirroring the existing `production_requirements` endpoint's `horizon_days` param style (`backend/api/v1/inventory.py:851-854`).
- Cost sourcing (no schema migration — derived from existing fields, per the spec's explicit "a decidir en el plan MW-3" for anything not already modeled):
  - `order_cost[sku]` = the SKU's `costo_unitario` (a representative row's value if it varies by bodega) — this literally matches the optimizer's own docstring definition, "cost per unit purchased" (`optimizer.py:44`).
  - `holding_cost[sku]` = `costo_unitario[sku] * business_cfg.holding_cost_pct / 365` (annualized percentage → per-day $, since a bucket is a day).
  - `stockout_cost[sku]` = `order_cost[sku] * business_cfg.stockout_cost_multiplier / max(lead_time_buckets[sku], 1)` — **not** scaled off `holding_cost` (an earlier draft of this plan did that, but `holding_cost` and `order_cost` differ by ~3 orders of magnitude — a per-day flow cost vs. a one-time per-unit price — so a real shortage would need ~600 days to ever outweigh placing an order, meaning the solver would never recommend buying within any realistic horizon; caught by Task 3's integration test). Scaling off `order_cost` divided by lead time means "running out for this SKU's full lead time costs about `multiplier`x its purchase price."
  - When a SKU has no `costo_unitario` anywhere (missing cost data), default it to `1.0` rather than `0.0` — a `0.0` unit cost would zero out both holding and stockout cost, making the solver indifferent to ever ordering that SKU. `1.0` is a documented placeholder assumption, not a real price.
  - `transfer_cost` = a flat constant `0.5` (module-level, not sourced from config) — the design spec explicitly leaves this as "a flat cost per unit transferred," not part of `business_cfg`.
  - `business_cfg` defaults when unconfigured: `holding_cost_pct=0.20`, `stockout_cost_multiplier=3.0` (same defaults as `BusinessConfigRequest`, `backend/schemas/configuration.py`).
- Demand splitting: forecast is per-SKU (not per SKU×bodega) today. Split each SKU's forecast demand across its warehouses proportional to that warehouse's **current stock share** for that SKU (per the design spec: *"si el forecast es a nivel SKU sin bodega, se reparte proporcional al stock/histórico por bodega"*). If a SKU has zero stock in every warehouse, split evenly across all warehouses instead (there's no stock signal to weight by).
- Lead time: `lead_time_buckets[sku]` = the **maximum** `lead_time_dias` across that SKU's bodega rows (conservative — never lets the model assume an order can arrive sooner than the slowest-to-supply warehouse needs).
- Warehouses in the model = the distinct `bodega` values present in `list_stock(tenant_id)` (not the separate `warehouses` table) — this guarantees the optimizer only ever reasons about warehouses that actually hold stock for this tenant, and stays trivially consistent with `stock0`.
- Degenerate case: if there are no in-scope SKUs (empty forecasts) or no warehouses (empty `inventory_stock`), the service returns an empty result directly (`status="optimal"`, `total_cost=0.0`, empty `orders`/`transfers`) **without calling `optimize()`** — an empty `OptimizationInput` is not a case the optimizer's own tests cover, and there is nothing useful to solve for.
- Result serialization collapses the optimizer's per-day-bucket dicts into ONE total per (SKU, bodega) for orders and ONE total per (SKU, from-bodega, to-bodega) for transfers, summed across the full horizon — matching how the rest of the app already treats a PO/transfer line (no day-by-day granularity anywhere else in the UI). Zero-quantity lines are omitted from the response.

---

### Task 1: `build_optimization_input` — assemble a MILP input from DB state

**Files:**
- Create: `backend/inventory/optimizer_service.py`
- Test: `backend/tests/test_optimizer_service.py`

**Interfaces:**
- Consumes: `backend.db.session_store.get_forecasts(tenant_id, session_id) -> dict` (shape `{sku: {model_name: {"forecast": [{"date":.., "value":.., "upper":..}, ...]}}}`), `backend.db.session_store.get_field(tenant_id, session_id, "business_cfg") -> dict | None`, `backend.inventory.service.list_stock(tenant_id) -> list[dict]` (rows with `sku`, `bodega`, `stock_actual`, `lead_time_dias`, `costo_unitario`), `backend.inventory.service._avg_forecast_curve(model_forecasts: dict, max_steps: int) -> list[dict]` (shape `[{"step": int, "date": str|None, "value": float}, ...]`, sorted by step).
- Produces: `build_optimization_input(tenant_id: str, session_id: str, horizon_days: int = 14) -> OptimizationInput | None` — returns `None` for the degenerate case (no in-scope SKUs or no warehouses), so Task 3's endpoint knows to skip calling `optimize()` entirely. `OptimizationInput` is `forecasting_core.business.optimizer.OptimizationInput` (already defined, do not redefine).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_optimizer_service.py
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
        assert inp.stockout_cost[sku] == 20.0 * 3.0 / 10  # order_cost * multiplier / lead_time
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_optimizer_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.inventory.optimizer_service'`

- [ ] **Step 3: Write the implementation**

```python
# backend/inventory/optimizer_service.py
"""
Assembles a forecasting_core MILP OptimizationInput from live DB state
(inventory_stock across bodegas, session forecasts, business_cfg), and
collapses an OptimizationResult back into one actionable total per line —
see docs/superpowers/specs/2026-07-12-multi-warehouse-milp-design.md,
"Sub-proyecto 6", for the design this implements.
"""

from __future__ import annotations

from typing import Optional

from forecasting_core.business.optimizer import OptimizationInput, OptimizationResult

from backend.db import session_store
from backend.inventory.service import list_stock, _avg_forecast_curve

_DEFAULT_UNIT_COST = 1.0
_DEFAULT_TRANSFER_COST_PER_UNIT = 0.5
_DEFAULT_LEAD_TIME_DAYS = 15


def build_optimization_input(
    tenant_id: str, session_id: str, horizon_days: int = 14,
) -> Optional[OptimizationInput]:
    forecasts: dict = session_store.get_forecasts(tenant_id, session_id) or {}
    skus = sorted(forecasts.keys())

    stock_rows = list_stock(tenant_id)
    warehouses = sorted({r["bodega"] for r in stock_rows if r.get("bodega")})

    if not skus or not warehouses:
        return None

    business_cfg: dict = session_store.get_field(tenant_id, session_id, "business_cfg") or {}
    holding_cost_pct = float(business_cfg.get("holding_cost_pct", 0.20))
    stockout_cost_multiplier = float(business_cfg.get("stockout_cost_multiplier", 3.0))

    # rows_by_sku[sku] -> {bodega: row}, only for bodegas that actually have a row.
    rows_by_sku: dict[str, dict[str, dict]] = {}
    for r in stock_rows:
        rows_by_sku.setdefault(r["sku"], {})[r["bodega"]] = r

    stock0: dict[tuple[str, str], float] = {}
    demand: dict[tuple[str, str], list[float]] = {}
    lead_time_buckets: dict[str, int] = {}
    holding_cost: dict[str, float] = {}
    stockout_cost: dict[str, float] = {}
    order_cost: dict[str, float] = {}

    for sku in skus:
        sku_rows = rows_by_sku.get(sku, {})

        for w in warehouses:
            stock0[(sku, w)] = float(sku_rows[w]["stock_actual"] or 0) if w in sku_rows else 0.0

        total_stock = sum(stock0[(sku, w)] for w in warehouses)

        model_forecasts = forecasts.get(sku, {})
        curve = _avg_forecast_curve(model_forecasts, max_steps=horizon_days)
        daily_total = [0.0] * horizon_days
        for point in curve:
            step = point["step"]
            if step < horizon_days:
                daily_total[step] = point["value"]

        for w in warehouses:
            if total_stock > 0:
                share = stock0[(sku, w)] / total_stock
            else:
                share = 1.0 / len(warehouses)
            demand[(sku, w)] = [v * share for v in daily_total]

        lead_times = [int(row["lead_time_dias"]) for row in sku_rows.values() if row.get("lead_time_dias") is not None]
        lead_time_buckets[sku] = max(lead_times) if lead_times else _DEFAULT_LEAD_TIME_DAYS

        costs = [float(row["costo_unitario"]) for row in sku_rows.values() if row.get("costo_unitario") is not None]
        unit_cost = max(costs) if costs else _DEFAULT_UNIT_COST

        order_cost[sku] = unit_cost
        holding_cost[sku] = unit_cost * holding_cost_pct / 365
        # Stockout is a per-day-of-shortage flow cost, but order_cost is a
        # one-time per-unit purchase price — scaling stockout off holding_cost
        # (also a tiny daily fraction of unit_cost) made a real shortage need
        # ~600 days to ever outweigh placing an order, so the solver would
        # never recommend buying within any realistic horizon. Scaling off
        # order_cost directly, divided by lead time, means "running out for
        # this SKU's full lead time costs about `multiplier`x its purchase
        # price" — a shortage that persists that long reliably triggers a
        # real order, while a day or two of shortage still doesn't dominate
        # holding/transfer costs. max(..., 1) guards lead_time_dias == 0.
        stockout_cost[sku] = order_cost[sku] * stockout_cost_multiplier / max(lead_time_buckets[sku], 1)

    return OptimizationInput(
        skus=skus,
        warehouses=warehouses,
        horizon=horizon_days,
        demand=demand,
        stock0=stock0,
        lead_time_buckets=lead_time_buckets,
        holding_cost=holding_cost,
        stockout_cost=stockout_cost,
        order_cost=order_cost,
        transfer_cost=_DEFAULT_TRANSFER_COST_PER_UNIT,
    )
```

Note on `test_missing_cost_data_defaults_to_one`: the `costs` list comprehension only picks up rows where `costo_unitario` is not `None` — with a single `principal` row and no `costo_unitario` passed to `upsert_stock`, `costs` is empty, so `unit_cost` falls back to `_DEFAULT_UNIT_COST` (1.0), matching the test's expectation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_optimizer_service.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/optimizer_service.py backend/tests/test_optimizer_service.py
git commit -m "feat(inventory): assemble MILP OptimizationInput from live stock/forecast/cost data"
```

---

### Task 2: `serialize_optimization_result` — collapse a solve into actionable lines

**Files:**
- Modify: `backend/inventory/optimizer_service.py` (append to the file created in Task 1)
- Test: `backend/tests/test_optimizer_service.py` (append to the file created in Task 1)

**Interfaces:**
- Consumes: `forecasting_core.business.optimizer.OptimizationInput`, `OptimizationResult` (both already defined — field shapes are in this plan's Global Constraints / Task 1).
- Produces: `serialize_optimization_result(inp: OptimizationInput, result: OptimizationResult) -> dict` returning:
  ```python
  {
      "status": str,             # "optimal" | "fallback"
      "total_cost": float,       # rounded to 2 decimals
      "horizon_days": int,
      "orders": [                # one entry per (sku, bodega) with qty > 0, summed over the horizon
          {"sku": str, "bodega": str, "qty": float, "costo_unitario": float | None, "proveedor": str | None},
          ...
      ],
      "transfers": [             # one entry per (sku, from_bodega, to_bodega) with qty > 0, summed over the horizon
          {"sku": str, "from_bodega": str, "to_bodega": str, "qty": float},
          ...
      ],
  }
  ```
  `orders[].costo_unitario`/`proveedor` come from the same per-(sku,bodega) `inventory_stock` row Task 1 already looked up — Task 3 (the endpoint) passes `stock_rows` through so this function doesn't need its own DB access; **produces** an additional parameter: `serialize_optimization_result(inp, result, stock_rows: list[dict]) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# appended to backend/tests/test_optimizer_service.py

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
            {"sku": "SKU1", "bodega": "Norte", "costo_unitario": 2.0, "proveedor": "ACME"},
            {"sku": "SKU1", "bodega": "Sur", "costo_unitario": 2.0, "proveedor": "ACME"},
        ]

        out = serialize_optimization_result(inp, result, stock_rows)

        assert out["status"] == "optimal"
        assert out["total_cost"] == 12.35
        assert out["horizon_days"] == 2
        assert out["orders"] == [
            {"sku": "SKU1", "bodega": "Norte", "qty": 3.0, "costo_unitario": 2.0, "proveedor": "ACME"},
        ]
        assert out["transfers"] == [
            {"sku": "SKU1", "from_bodega": "Sur", "to_bodega": "Norte", "qty": 4.0},
        ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_optimizer_service.py::TestSerializeOptimizationResult -v`
Expected: FAIL with `ImportError: cannot import name 'serialize_optimization_result'`

- [ ] **Step 3: Write the implementation**

```python
# appended to backend/inventory/optimizer_service.py

def serialize_optimization_result(inp, result, stock_rows: list[dict]) -> dict:
    row_by_sku_bodega = {(r["sku"], r["bodega"]): r for r in stock_rows}

    order_totals: dict[tuple[str, str], float] = {}
    for (sku, w, t), qty in result.orders.items():
        if qty > 0:
            order_totals[(sku, w)] = order_totals.get((sku, w), 0.0) + qty

    transfer_totals: dict[tuple[str, str, str], float] = {}
    for (sku, a, b, t), qty in result.transfers.items():
        if qty > 0:
            transfer_totals[(sku, a, b)] = transfer_totals.get((sku, a, b), 0.0) + qty

    orders = []
    for (sku, w) in sorted(order_totals):
        row = row_by_sku_bodega.get((sku, w), {})
        orders.append({
            "sku": sku, "bodega": w, "qty": round(order_totals[(sku, w)], 2),
            "costo_unitario": row.get("costo_unitario"),
            "proveedor": row.get("proveedor"),
        })

    transfers = []
    for (sku, a, b) in sorted(transfer_totals):
        transfers.append({
            "sku": sku, "from_bodega": a, "to_bodega": b,
            "qty": round(transfer_totals[(sku, a, b)], 2),
        })

    return {
        "status": result.status,
        "total_cost": round(result.total_cost, 2),
        "horizon_days": inp.horizon,
        "orders": orders,
        "transfers": transfers,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_optimizer_service.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/inventory/optimizer_service.py backend/tests/test_optimizer_service.py
git commit -m "feat(inventory): collapse MILP per-bucket solve into one actionable line per SKU/bodega"
```

---

### Task 3: `GET /inventory/optimize` endpoint

**Files:**
- Modify: `backend/api/v1/inventory.py` (add import + new route at the end of the file, after the `dead-stock` section which ends around line 880 in the current file — append as a new section)
- Test: `backend/tests/test_optimizer_endpoint.py`

**Interfaces:**
- Consumes: `backend.inventory.optimizer_service.build_optimization_input` and `.serialize_optimization_result` (Tasks 1-2), `forecasting_core.business.optimizer.optimize` (already on `main`), `backend.inventory.service.list_stock` (already exists), `backend.auth.guards.get_current_user`/`CurrentUser` (already imported in `inventory.py`), `backend.schemas.common.ok` (already imported in `inventory.py`).
- Produces: `GET /api/v1/inventory/optimize?session_id=...&horizon_days=14` returning `ok(<serialize_optimization_result output>)`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_optimizer_endpoint.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_optimizer_endpoint.py -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

In `backend/api/v1/inventory.py`, add to the imports near the top (alongside the existing `from backend.inventory import warehouse_service as wh_svc` line):

```python
from backend.inventory import optimizer_service as opt_svc
```

Then append this new section at the end of the file (after the `dead-stock` route):

```python
# ── MILP purchasing/transfers optimizer (MW-3) ───────────────────────────────

@router.get("/optimize")
def optimize_inventory(
    session_id:   str = Query(...),
    horizon_days: int = Query(default=14, ge=1, le=30),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Runs the MILP purchasing/transfers optimizer for this session and
    returns suggested purchase quantities per SKU x bodega, plus
    recommended inter-warehouse transfers, collapsed to one total per
    line over the full horizon.
    """
    from forecasting_core.business.optimizer import optimize

    inp = opt_svc.build_optimization_input(user.tenant_id, session_id, horizon_days)
    if inp is None:
        return ok({
            "status": "optimal", "total_cost": 0.0, "horizon_days": horizon_days,
            "orders": [], "transfers": [],
        })

    result = optimize(inp)
    stock_rows = svc.list_stock(user.tenant_id)
    return ok(opt_svc.serialize_optimization_result(inp, result, stock_rows))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/test_optimizer_endpoint.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full backend regression**

Run: `cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all passing (no regressions vs. the pre-Task-3 baseline)

- [ ] **Step 6: Commit**

```bash
git add backend/api/v1/inventory.py backend/tests/test_optimizer_endpoint.py
git commit -m "feat(inventory): expose GET /inventory/optimize for purchasing/transfer recommendations"
```

---

### Task 4: Frontend API client + types

**Files:**
- Modify: `Frontend/src/lib/types.ts` (add new interfaces near `InventoryStatusResponse`, ~line 677; add `bodega` to `POLineDecision`, ~line 833)
- Modify: `Frontend/src/lib/api.ts` (add `optimizeInventory` near the other inventory GET functions, e.g. after `getDeadStock`)

**Interfaces:**
- Consumes: the response shape from Task 3's endpoint (exact field names above).
- Produces: `OptimizationOrder`, `OptimizationTransfer`, `OptimizationResponse` types; `optimizeInventory(sessionId: string, horizonDays?: number): Promise<OptimizationResponse>`. `POLineDecision` gains an optional `bodega?: string | null` field (needed by Task 5's "convert to PO" action — the backend's `POLineItem` schema, `backend/api/v1/inventory.py:458-467`, already accepts `bodega`, but the frontend type never exposed it).

- [ ] **Step 1: Add the types**

In `Frontend/src/lib/types.ts`, add right before `export interface InventoryStatusResponse {` (~line 677):

```typescript
// ── Purchasing/transfers optimizer (MW-3) ─────────────────────────────────────

export interface OptimizationOrder {
  sku:             string
  bodega:          string
  qty:             number
  costo_unitario:  number | null
  proveedor:       string | null
}

export interface OptimizationTransfer {
  sku:          string
  from_bodega:  string
  to_bodega:    string
  qty:          number
}

export interface OptimizationResponse {
  status:        'optimal' | 'fallback'
  total_cost:    number
  horizon_days:  number
  orders:        OptimizationOrder[]
  transfers:     OptimizationTransfer[]
}
```

Then modify the existing `POLineDecision` interface (~line 833) to add the new optional field:

```typescript
export interface POLineDecision {
  sku:                   string
  display_name?:         string | null
  proveedor?:            string | null
  signal?:               string | null
  cantidad_recomendada:  number
  cantidad_final:        number
  status:                'approved' | 'modified' | 'rejected'
  costo_unitario?:       number | null
  bodega?:               string | null
}
```

- [ ] **Step 2: Add the API client function**

In `Frontend/src/lib/api.ts`, add after the existing `getDeadStock` export:

```typescript
export const optimizeInventory = (sessionId: string, horizonDays = 14) =>
  request<import('./types').OptimizationResponse>(
    'GET', `/inventory/optimize?session_id=${sessionId}&horizon_days=${horizonDays}`,
  )
```

- [ ] **Step 3: Typecheck**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no new errors (this step only adds types/a function — nothing yet consumes `OptimizationResponse`'s new fields in a way that could mismatch, and `bodega` on `POLineDecision` is optional so no existing call site breaks)

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/lib/types.ts Frontend/src/lib/api.ts
git commit -m "feat(frontend): API client for the purchasing/transfers optimizer"
```

---

### Task 5: `/hoy` recommendations section + convert-to-PO action

**Files:**
- Modify: `Frontend/src/app/hoy/page.tsx` (new section + new local state, alongside the existing `pendingPOs`/`briefing`/`narrative` state declared near line 340)
- Modify: `Frontend/src/i18n/translations.ts` (new `hoy.optimizer_*` keys, ES + EN)

**Interfaces:**
- Consumes: `optimizeInventory` and the `OptimizationResponse`/`OptimizationOrder`/`OptimizationTransfer` types (Task 4), the existing `logPOGeneration(sessionId, items?: POLineDecision[])` function (already in `api.ts`), the existing `useLanguage()` hook's `t(key)` function (already used throughout `hoy/page.tsx`).
- Produces: nothing consumed by later tasks — this is the final task in the plan.

- [ ] **Step 1: Add i18n keys**

In `Frontend/src/i18n/translations.ts`, find the Spanish block's `hoy.*` keys (search for `'hoy.narrative_card_title'`) and add nearby:

```typescript
    'hoy.optimizer_title':          'Compras y transferencias sugeridas',
    'hoy.optimizer_subtitle':       'Basado en el plan de optimización de {horizon} días',
    'hoy.optimizer_orders_title':   'Órdenes sugeridas por bodega',
    'hoy.optimizer_transfers_title':'Transferencias recomendadas',
    'hoy.optimizer_transfer_line':  'Mover {qty} uds de {sku} de {from} a {to}',
    'hoy.optimizer_convert_to_po':  'Convertir en OC',
    'hoy.optimizer_empty':          'No hay recomendaciones de compra o transferencia por ahora.',
    'hoy.optimizer_loading':        'Calculando recomendaciones…',
    'hoy.optimizer_po_created':     'Orden de compra generada',
```

Find the matching English block (search for `'hoy.narrative_card_title'` a second time) and add:

```typescript
    'hoy.optimizer_title':          'Suggested purchases and transfers',
    'hoy.optimizer_subtitle':       'Based on a {horizon}-day optimization plan',
    'hoy.optimizer_orders_title':   'Suggested orders by warehouse',
    'hoy.optimizer_transfers_title':'Recommended transfers',
    'hoy.optimizer_transfer_line':  'Move {qty} units of {sku} from {from} to {to}',
    'hoy.optimizer_convert_to_po':  'Convert to PO',
    'hoy.optimizer_empty':          'No purchase or transfer recommendations right now.',
    'hoy.optimizer_loading':        'Calculating recommendations…',
    'hoy.optimizer_po_created':     'Purchase order created',
```

- [ ] **Step 2: Add state and data loading**

In `Frontend/src/app/hoy/page.tsx`, near the existing state declarations (around line 340-344, right after `const [pendingPOs, setPendingPOs] = useState<POLogEntry[]>([])`):

```typescript
 const [optimization, setOptimization] = useState<OptimizationResponse | null>(null)
 const [optimizationLoading, setOptimizationLoading] = useState(false)
```

Add `OptimizationResponse` to the existing type-only import block at the top of the file (find the line importing `POLogEntry`/`MorningBriefing`/etc. from `@/lib/types` and add `OptimizationResponse` to that same import list). Add `optimizeInventory` to the existing import from `@/lib/api` (find the line importing `getMorningBriefing`/`getMorningNarrative`/`logPOGeneration` and add `optimizeInventory` to that same import list).

Near the existing `useEffect(() => { if (sessionId) load(sessionId) }, [sessionId, load])` (line 362-364), add a sibling effect:

```typescript
 useEffect(() => {
  if (!sessionId) return
  setOptimizationLoading(true)
  optimizeInventory(sessionId, 14)
   .then(setOptimization)
   .catch(() => setOptimization(null))
   .finally(() => setOptimizationLoading(false))
 }, [sessionId])
```

- [ ] **Step 3: Add the convert-to-PO handler**

Near the existing `downloadOC` function in the same file (search for `function downloadOC` or the sticky-cart-bar's PO generation call), add a sibling function:

```typescript
 async function convertOrderToPO(order: OptimizationOrder) {
  if (!sessionId) return
  const decision: POLineDecision = {
   sku: order.sku,
   cantidad_recomendada: order.qty,
   cantidad_final: order.qty,
   status: 'approved',
   costo_unitario: order.costo_unitario,
   proveedor: order.proveedor,
   bodega: order.bodega,
  }
  await logPOGeneration(sessionId, [decision])
  showToast(t('hoy.optimizer_po_created'), 'success')
  setOptimization(prev => prev
   ? { ...prev, orders: prev.orders.filter(o => !(o.sku === order.sku && o.bodega === order.bodega)) }
   : prev)
 }
```

(`showToast` and `OptimizationOrder` — add `OptimizationOrder`/`OptimizationTransfer` to the same `@/lib/types` import list as Step 2's `OptimizationResponse`. `showToast` is already used elsewhere in this file via `useToast()` — reuse the existing call in this file rather than importing a new hook instance.)

- [ ] **Step 4: Render the section**

Add a new section in the JSX, after the existing "URGENTE"/"ESTA SEMANA" sections and before the "Anticípate" section (search for the comment `{/* Anticípate section */}` or equivalent demand-spikes heading, and insert immediately before it):

```tsx
        {optimization && (optimization.orders.length > 0 || optimization.transfers.length > 0) && (
         <section style={{ marginTop: 32, marginBottom: 28 }}>
          <h2 style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>
           {t('hoy.optimizer_title')}
          </h2>
          <p style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 14 }}>
           {t('hoy.optimizer_subtitle').replace('{horizon}', String(optimization.horizon_days))}
          </p>

          {optimization.orders.length > 0 && (
           <div style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
             {t('hoy.optimizer_orders_title')}
            </h3>
            {optimization.orders.map(order => (
             <div key={`${order.sku}-${order.bodega}`} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 6,
             }}>
              <span style={{ fontSize: 13 }}>
               {order.sku} — {order.bodega}: <strong>{order.qty}</strong>
              </span>
              <button onClick={() => convertOrderToPO(order)} style={{
               all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600,
               color: 'var(--accent)', padding: '4px 10px', borderRadius: 6,
              }}>
               {t('hoy.optimizer_convert_to_po')}
              </button>
             </div>
            ))}
           </div>
          )}

          {optimization.transfers.length > 0 && (
           <div>
            <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
             {t('hoy.optimizer_transfers_title')}
            </h3>
            {optimization.transfers.map(tr => (
             <div key={`${tr.sku}-${tr.from_bodega}-${tr.to_bodega}`} style={{
              fontSize: 13, padding: '10px 12px', border: '1px solid var(--border)',
              borderRadius: 8, marginBottom: 6,
             }}>
              {t('hoy.optimizer_transfer_line')
               .replace('{qty}', String(tr.qty))
               .replace('{sku}', tr.sku)
               .replace('{from}', tr.from_bodega)
               .replace('{to}', tr.to_bodega)}
             </div>
            ))}
           </div>
          )}
         </section>
        )}
```

- [ ] **Step 5: Typecheck and manual verification**

Run: `cd Frontend && npx tsc --noEmit`
Expected: no errors

Manual check (dev server must already be running per `CLAUDE.md`'s run instructions — do not restart it):
1. Log in, complete `/quick-start` with the bundled demo dataset (or use an existing trained session).
2. Navigate to `/hoy`.
3. Confirm the new "Compras y transferencias sugeridas" section appears once the optimizer call resolves, showing at least one suggested order (the demo dataset seeds understocked SKUs).
4. Click "Convertir en OC" on one order line; confirm a success toast appears and that line disappears from the list; confirm a new entry shows up on `/pedidos`.

- [ ] **Step 6: Commit**

```bash
git add Frontend/src/app/hoy/page.tsx Frontend/src/i18n/translations.ts
git commit -m "feat(hoy): show suggested purchases/transfers with convert-to-PO action"
```

---

## Final Regression

After all 5 tasks:

```bash
cd backend && DATABASE_URL="postgresql://postgres:postgres@localhost:5544/forecasting" ../backend/.venv/Scripts/python.exe -m pytest tests/ -q
cd ForecastingCore && ../backend/.venv/Scripts/python.exe -m pytest tests/ -q
cd Frontend && npx tsc --noEmit
```

All three must be clean before this branch is considered ready for a final whole-branch review.
