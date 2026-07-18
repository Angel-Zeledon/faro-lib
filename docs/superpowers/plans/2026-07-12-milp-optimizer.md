# MILP Optimizer Core (MW-2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure ForecastingCore module that decides, per SKU × warehouse × time bucket, how much to purchase from suppliers and how much to transfer between warehouses, minimizing total cost (holding + stockout + transfer + purchase), using `scipy.optimize.milp`. No DB, no API — a self-contained optimization engine consumable later by MW-3's API surface.

**Architecture:** Four pieces, each independently testable:
1. `OptimizationInput`/`OptimizationResult` — plain dataclasses describing the problem and its solution.
2. `VariableIndex` — a pure indexing scheme mapping `(sku, warehouse, bucket)` tuples to flat vector positions, so the rest of the code never hand-derives offsets.
3. `build_problem(input) -> MilpProblem` — assembles the objective vector, equality constraint matrix (inventory balance), bounds, and integrality array. **Never calls the solver** — testable by hand-computing the exact expected matrix for a tiny case.
4. `optimize(input) -> OptimizationResult` — calls `scipy.optimize.milp`, decodes the solution vector back into structured per-SKU/warehouse/bucket recommendations, and falls back to a simple per-warehouse ROP heuristic if the solve fails, times out, or the problem is too large.

**Tech Stack:** Python 3, numpy, `scipy.optimize.milp`/`LinearConstraint`/`Bounds` (already installed, no new dependency), pytest (pure Python, no DB).

## Global Constraints

- **Model (all buckets t = 1..H, 1-indexed; t=0 is "now" — a constant, not a variable):**
  - Decision variables: `order[i,w,t]` (int ≥ 0, units of SKU `i` arriving at warehouse `w` at the start of bucket `t`), `transfer[i,a,b,t]` (int ≥ 0, units of SKU `i` moved from warehouse `a` to `b`, `a ≠ b`, available at bucket `t` — MVP simplification: instantaneous, no transfer lead time), `inv[i,w,t]` (continuous ≥ 0, ending inventory), `short[i,w,t]` (continuous ≥ 0, unmet demand / stockout).
  - Given constants: `stock0[i,w]` (current stock, NOT a variable), `demand[i,w,t]`, `lead_time_buckets[i]`, `holding_cost[i]`, `stockout_cost[i]`, `order_cost[i]`, `transfer_cost` (single global scalar per unit moved — MVP simplification; per-pair costs are a documented future refinement).
  - **Lead time constraint:** `order[i,w,t]` is fixed to `0` (via bounds `[0,0]`, not omitted) for every `t <= lead_time_buckets[i]` — the MVP assumes no orders are already in transit before the planning horizon starts.
  - **Balance equality**, for every `(i, w, t)`:
    `inv[i,w,t] - inv[i,w,t-1] - order[i,w,t] - Σ_{a≠w} transfer[i,a,w,t] + Σ_{b≠w} transfer[i,w,b,t] - short[i,w,t] = -demand[i,w,t]`
    where for `t=1`, `inv[i,w,0]` is the constant `stock0[i,w]`, so it moves to the RHS: the `t=1` row's RHS is `stock0[i,w] - demand[i,w,1]` instead of `-demand[i,w,1]`.
  - **Objective (minimize):** `Σ holding_cost[i]·inv[i,w,t] + Σ stockout_cost[i]·short[i,w,t] + Σ order_cost[i]·order[i,w,t] + Σ transfer_cost·transfer[i,a,b,t]`.
  - **Integrality:** `order`/`transfer` are integer; `inv`/`short` are continuous.
- Pure function, no side effects, no DB, no persistence — `backend/` orchestration and the API surface are MW-3's job, not this plan's.
- Tests assert real, hand-computable values (exact matrix rows for the builder; known-correct purchase-vs-transfer decisions for the solver), never "the solver returned something."
- If `scipy.optimize.milp` fails, raises, times out, or the problem exceeds a size threshold, `optimize()` must fall back to a simple per-(SKU, warehouse) heuristic (reuse the existing ROP formula pattern from `forecasting_core/business/inventory.py::InventoryAdvisor`, applied independently per warehouse, ignoring transfers) rather than raising — the caller always gets a usable recommendation.
- Run tests from `ForecastingCore/`: `python -m pytest tests/test_optimizer*.py -v` (pure Python, no DB).

---

### Task 1: Data structures + `VariableIndex`

**Files:**
- Create: `ForecastingCore/forecasting_core/business/optimizer.py`
- Test: `ForecastingCore/tests/test_optimizer_index.py` (new)

**Interfaces:**
- Produces: `OptimizationInput` (dataclass: `skus: list[str]`, `warehouses: list[str]`, `horizon: int`, `demand: dict[tuple[str,str], list[float]]` — one list of length `horizon` per `(sku, warehouse)`, `stock0: dict[tuple[str,str], float]`, `lead_time_buckets: dict[str, int]`, `holding_cost: dict[str, float]`, `stockout_cost: dict[str, float]`, `order_cost: dict[str, float]`, `transfer_cost: float`).
- Produces: `OptimizationResult` (dataclass: `orders: dict[tuple[str,str,int], float]`, `transfers: dict[tuple[str,str,str,int], float]`, `inventory: dict[tuple[str,str,int], float]`, `shortages: dict[tuple[str,str,int], float]`, `total_cost: float`, `status: str` — `"optimal"` | `"fallback"`).
- Produces: `VariableIndex(skus, warehouses, horizon)` with methods `order_idx(i,w,t)`, `transfer_idx(i,a,b,t)`, `inv_idx(i,w,t)`, `short_idx(i,w,t)` (all take SKU/warehouse NAMES and a 1-indexed bucket `t`, return an `int` flat index) and attribute `n_vars: int`.

- [ ] **Step 1: Write the failing tests**

Create `ForecastingCore/tests/test_optimizer_index.py`:

```python
import pytest
from forecasting_core.business.optimizer import VariableIndex


class TestVariableIndex:
    def test_all_indices_unique_and_in_range(self):
        idx = VariableIndex(skus=["A", "B"], warehouses=["W1", "W2"], horizon=2)
        seen = set()
        for i in ["A", "B"]:
            for w in ["W1", "W2"]:
                for t in [1, 2]:
                    for method, args in [
                        (idx.order_idx, (i, w, t)),
                        (idx.inv_idx, (i, w, t)),
                        (idx.short_idx, (i, w, t)),
                    ]:
                        v = method(*args)
                        assert 0 <= v < idx.n_vars
                        assert v not in seen
                        seen.add(v)
            for a in ["W1", "W2"]:
                for b in ["W1", "W2"]:
                    if a == b:
                        continue
                    for t in [1, 2]:
                        v = idx.transfer_idx(i, a, b, t)
                        assert 0 <= v < idx.n_vars
                        assert v not in seen
                        seen.add(v)
        assert len(seen) == idx.n_vars  # every slot used exactly once

    def test_n_vars_matches_expected_count(self):
        # 2 SKUs, 3 warehouses, 4 buckets:
        #   order + inv + short: 2*3*4 each = 24 each -> 72
        #   transfer: 2 SKUs * (3*2 ordered pairs) * 4 buckets = 2*6*4 = 48
        idx = VariableIndex(skus=["A", "B"], warehouses=["W1", "W2", "W3"], horizon=4)
        assert idx.n_vars == 72 + 48

    def test_transfer_idx_rejects_same_warehouse(self):
        idx = VariableIndex(skus=["A"], warehouses=["W1", "W2"], horizon=1)
        with pytest.raises(ValueError):
            idx.transfer_idx("A", "W1", "W1", 1)

    def test_order_and_inv_and_short_indices_are_distinct_blocks(self):
        idx = VariableIndex(skus=["A"], warehouses=["W1"], horizon=1)
        o = idx.order_idx("A", "W1", 1)
        v = idx.inv_idx("A", "W1", 1)
        s = idx.short_idx("A", "W1", 1)
        assert len({o, v, s}) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ForecastingCore && python -m pytest tests/test_optimizer_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'forecasting_core.business.optimizer'`.

- [ ] **Step 3: Implement the data structures and `VariableIndex`**

Create `ForecastingCore/forecasting_core/business/optimizer.py`:

```python
"""
MILP inventory optimizer — decides, per SKU x warehouse x time bucket, how
much to purchase from suppliers and how much to transfer between warehouses,
minimizing total cost. Pure function, no DB/API — see the Multi-Warehouse
design spec (docs/superpowers/specs/2026-07-12-multi-warehouse-milp-design.md)
for the full model.

Model (buckets t = 1..H, 1-indexed; t=0 is "now", a constant not a variable):
  order[i,w,t]:    int >= 0, units of SKU i arriving at warehouse w at t.
  transfer[i,a,b,t]: int >= 0, units of SKU i moved a->b (a != b), at t.
  inv[i,w,t]:      float >= 0, ending inventory of SKU i at warehouse w at t.
  short[i,w,t]:    float >= 0, unmet demand of SKU i at warehouse w at t.

Balance (per i,w,t):
  inv[i,w,t] - inv[i,w,t-1] - order[i,w,t]
    - sum_{a!=w} transfer[i,a,w,t] + sum_{b!=w} transfer[i,w,b,t]
    - short[i,w,t] = -demand[i,w,t]
  (inv[i,w,0] is the constant stock0[i,w], folded into the t=1 row's RHS.)

Objective (minimize):
  sum holding_cost[i]*inv + stockout_cost[i]*short
    + order_cost[i]*order + transfer_cost*transfer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class OptimizationInput:
    skus: List[str]
    warehouses: List[str]
    horizon: int
    demand: Dict[Tuple[str, str], List[float]]       # (sku, warehouse) -> [horizon values]
    stock0: Dict[Tuple[str, str], float]              # (sku, warehouse) -> current stock
    lead_time_buckets: Dict[str, int]                 # sku -> lead time in buckets
    holding_cost: Dict[str, float]                    # sku -> cost per unit per bucket held
    stockout_cost: Dict[str, float]                   # sku -> penalty per unit short
    order_cost: Dict[str, float]                      # sku -> cost per unit purchased
    transfer_cost: float = 1.0                        # global cost per unit transferred


@dataclass
class OptimizationResult:
    orders: Dict[Tuple[str, str, int], float]          # (sku, warehouse, t) -> qty
    transfers: Dict[Tuple[str, str, str, int], float]  # (sku, from_wh, to_wh, t) -> qty
    inventory: Dict[Tuple[str, str, int], float]       # (sku, warehouse, t) -> ending inv
    shortages: Dict[Tuple[str, str, int], float]       # (sku, warehouse, t) -> unmet demand
    total_cost: float
    status: str = "optimal"  # "optimal" | "fallback"


class VariableIndex:
    """
    Maps (sku, warehouse[, warehouse], bucket) tuples to flat vector positions
    for the MILP decision vector. Four contiguous blocks, in this order:
    order, transfer, inv, short. Nothing outside this class ever hand-derives
    an offset.
    """

    def __init__(self, skus: List[str], warehouses: List[str], horizon: int):
        self.skus = list(skus)
        self.warehouses = list(warehouses)
        self.horizon = horizon
        self._sku_pos = {s: k for k, s in enumerate(self.skus)}
        self._wh_pos = {w: k for k, w in enumerate(self.warehouses)}

        n_sku = len(self.skus)
        n_wh = len(self.warehouses)
        # Ordered (a, b) pairs with a != b, in a fixed deterministic order.
        self._transfer_pairs = [
            (a, b) for a in self.warehouses for b in self.warehouses if a != b
        ]
        self._pair_pos = {pair: k for k, pair in enumerate(self._transfer_pairs)}
        n_pairs = len(self._transfer_pairs)

        self._block_size_owis = n_sku * n_wh * horizon       # order / inv / short block size
        self._block_size_transfer = n_sku * n_pairs * horizon

        self._order_offset = 0
        self._transfer_offset = self._order_offset + self._block_size_owis
        self._inv_offset = self._transfer_offset + self._block_size_transfer
        self._short_offset = self._inv_offset + self._block_size_owis

        self.n_vars = self._short_offset + self._block_size_owis

    def _owis_flat(self, i: str, w: str, t: int) -> int:
        """Flat position WITHIN an order/inv/short-shaped block (before adding
        that block's offset)."""
        si = self._sku_pos[i]
        wi = self._wh_pos[w]
        ti = t - 1  # 1-indexed bucket -> 0-indexed slot
        return (si * len(self.warehouses) + wi) * self.horizon + ti

    def order_idx(self, i: str, w: str, t: int) -> int:
        return self._order_offset + self._owis_flat(i, w, t)

    def inv_idx(self, i: str, w: str, t: int) -> int:
        return self._inv_offset + self._owis_flat(i, w, t)

    def short_idx(self, i: str, w: str, t: int) -> int:
        return self._short_offset + self._owis_flat(i, w, t)

    def transfer_idx(self, i: str, a: str, b: str, t: int) -> int:
        if a == b:
            raise ValueError(f"transfer_idx: source and destination warehouse are both '{a}'")
        si = self._sku_pos[i]
        pi = self._pair_pos[(a, b)]
        ti = t - 1
        flat = (si * len(self._transfer_pairs) + pi) * self.horizon + ti
        return self._transfer_offset + flat

    def transfer_pairs(self) -> List[Tuple[str, str]]:
        """All valid (from_warehouse, to_warehouse) pairs, a != b."""
        return list(self._transfer_pairs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ForecastingCore && python -m pytest tests/test_optimizer_index.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/business/optimizer.py ForecastingCore/tests/test_optimizer_index.py
git commit -m "feat(optimizer): OptimizationInput/Result dataclasses + VariableIndex

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `build_problem` — matrix/constraint builder (no solver call)

**Files:**
- Modify: `ForecastingCore/forecasting_core/business/optimizer.py` (add `MilpProblem` dataclass + `build_problem`)
- Test: `ForecastingCore/tests/test_optimizer_builder.py` (new)

**Interfaces:**
- Consumes: `OptimizationInput`, `VariableIndex` (Task 1).
- Produces: `MilpProblem(c: np.ndarray, A_eq: np.ndarray, b_eq: np.ndarray, bounds: Bounds, integrality: np.ndarray, index: VariableIndex)` and `build_problem(input: OptimizationInput) -> MilpProblem`.

- [ ] **Step 1: Write the failing test — hand-computed exact matrix for a tiny case**

Create `ForecastingCore/tests/test_optimizer_builder.py`:

```python
import numpy as np
from forecasting_core.business.optimizer import OptimizationInput, VariableIndex, build_problem


def _tiny_input() -> OptimizationInput:
    """1 SKU, 2 warehouses, 2 buckets, lead time 0 (orders can arrive from t=1)."""
    return OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=2,
        demand={("A", "W1"): [5.0, 5.0], ("A", "W2"): [3.0, 3.0]},
        stock0={("A", "W1"): 10.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 50.0},
        order_cost={"A": 2.0},
        transfer_cost=0.5,
    )


class TestBuildProblem:
    def test_objective_vector_matches_costs_at_each_index(self):
        inp = _tiny_input()
        problem = build_problem(inp)
        idx = problem.index

        assert problem.c[idx.order_idx("A", "W1", 1)] == inp.order_cost["A"]
        assert problem.c[idx.inv_idx("A", "W1", 1)] == inp.holding_cost["A"]
        assert problem.c[idx.short_idx("A", "W1", 1)] == inp.stockout_cost["A"]
        assert problem.c[idx.transfer_idx("A", "W1", "W2", 1)] == inp.transfer_cost

    def test_balance_row_t1_matches_hand_computed_equation(self):
        """
        Row for (sku=A, warehouse=W1, t=1):
          inv[A,W1,1] - order[A,W1,1] - transfer[A,W2,W1,1] + transfer[A,W1,W2,1]
            - short[A,W1,1] = stock0[A,W1] - demand[A,W1,1] = 10 - 5 = 5
        """
        inp = _tiny_input()
        problem = build_problem(inp)
        idx = problem.index

        row = _find_row_for(problem, idx.inv_idx("A", "W1", 1))
        expected = np.zeros(idx.n_vars)
        expected[idx.inv_idx("A", "W1", 1)] = 1.0
        expected[idx.order_idx("A", "W1", 1)] = -1.0
        expected[idx.transfer_idx("A", "W2", "W1", 1)] = -1.0  # inbound to W1
        expected[idx.transfer_idx("A", "W1", "W2", 1)] = 1.0   # outbound from W1
        expected[idx.short_idx("A", "W1", 1)] = -1.0

        np.testing.assert_array_equal(problem.A_eq[row], expected)
        assert problem.b_eq[row] == 5.0  # stock0(10) - demand(5)

    def test_balance_row_t2_references_prior_bucket_inventory(self):
        """
        Row for (sku=A, warehouse=W2, t=2):
          inv[A,W2,2] - inv[A,W2,1] - order[A,W2,2]
            - transfer[A,W1,W2,2] + transfer[A,W2,W1,2] - short[A,W2,2]
            = -demand[A,W2,2] = -3
        """
        inp = _tiny_input()
        problem = build_problem(inp)
        idx = problem.index

        row = _find_row_for(problem, idx.inv_idx("A", "W2", 2))
        expected = np.zeros(idx.n_vars)
        expected[idx.inv_idx("A", "W2", 2)] = 1.0
        expected[idx.inv_idx("A", "W2", 1)] = -1.0
        expected[idx.order_idx("A", "W2", 2)] = -1.0
        expected[idx.transfer_idx("A", "W1", "W2", 2)] = -1.0
        expected[idx.transfer_idx("A", "W2", "W1", 2)] = 1.0
        expected[idx.short_idx("A", "W2", 2)] = -1.0

        np.testing.assert_array_equal(problem.A_eq[row], expected)
        assert problem.b_eq[row] == -3.0

    def test_lead_time_fixes_early_orders_to_zero_bound(self):
        inp = _tiny_input()
        inp.lead_time_buckets = {"A": 1}  # order can't arrive before t=2
        problem = build_problem(inp)
        idx = problem.index
        o1 = idx.order_idx("A", "W1", 1)
        o2 = idx.order_idx("A", "W1", 2)
        assert problem.bounds.lb[o1] == 0 and problem.bounds.ub[o1] == 0
        assert problem.bounds.ub[o2] > 0  # not fixed to zero

    def test_integrality_marks_order_and_transfer_as_integer_only(self):
        inp = _tiny_input()
        problem = build_problem(inp)
        idx = problem.index
        assert problem.integrality[idx.order_idx("A", "W1", 1)] == 1
        assert problem.integrality[idx.transfer_idx("A", "W1", "W2", 1)] == 1
        assert problem.integrality[idx.inv_idx("A", "W1", 1)] == 0
        assert problem.integrality[idx.short_idx("A", "W1", 1)] == 0


def _find_row_for(problem, inv_var_idx: int) -> int:
    """The balance-equation row for a given (sku,warehouse,t) is the one
    whose A_eq row has a +1.0 exactly at that inv[...] column (each inv
    variable appears with coefficient 1.0 in EXACTLY one row: its own
    balance equation)."""
    rows = np.where(problem.A_eq[:, inv_var_idx] == 1.0)[0]
    assert len(rows) == 1, f"expected exactly one balance row for inv col {inv_var_idx}, found {len(rows)}"
    return rows[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ForecastingCore && python -m pytest tests/test_optimizer_builder.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_problem'`.

- [ ] **Step 3: Implement `MilpProblem` and `build_problem`**

Append to `ForecastingCore/forecasting_core/business/optimizer.py`:

```python
import numpy as np
from scipy.optimize import Bounds


@dataclass
class MilpProblem:
    c: np.ndarray
    A_eq: np.ndarray
    b_eq: np.ndarray
    bounds: Bounds
    integrality: np.ndarray
    index: VariableIndex


def build_problem(inp: OptimizationInput) -> MilpProblem:
    idx = VariableIndex(inp.skus, inp.warehouses, inp.horizon)
    n = idx.n_vars

    c = np.zeros(n)
    lb = np.zeros(n)
    ub = np.full(n, np.inf)
    integrality = np.zeros(n, dtype=int)

    for i in inp.skus:
        for w in inp.warehouses:
            for t in range(1, inp.horizon + 1):
                c[idx.order_idx(i, w, t)] = inp.order_cost[i]
                c[idx.inv_idx(i, w, t)] = inp.holding_cost[i]
                c[idx.short_idx(i, w, t)] = inp.stockout_cost[i]
                integrality[idx.order_idx(i, w, t)] = 1
                # Lead time: orders can't arrive before lead_time_buckets[i]
                # has elapsed from the start of the planning horizon.
                if t <= inp.lead_time_buckets.get(i, 0):
                    ub[idx.order_idx(i, w, t)] = 0
        for a, b in idx.transfer_pairs():
            for t in range(1, inp.horizon + 1):
                c[idx.transfer_idx(i, a, b, t)] = inp.transfer_cost
                integrality[idx.transfer_idx(i, a, b, t)] = 1

    # Balance equality rows: one per (sku, warehouse, t).
    n_rows = len(inp.skus) * len(inp.warehouses) * inp.horizon
    A_eq = np.zeros((n_rows, n))
    b_eq = np.zeros(n_rows)
    row = 0
    for i in inp.skus:
        for w in inp.warehouses:
            for t in range(1, inp.horizon + 1):
                A_eq[row, idx.inv_idx(i, w, t)] = 1.0
                A_eq[row, idx.order_idx(i, w, t)] = -1.0
                A_eq[row, idx.short_idx(i, w, t)] = -1.0
                for a in inp.warehouses:
                    if a == w:
                        continue
                    A_eq[row, idx.transfer_idx(i, a, w, t)] = -1.0  # inbound
                    A_eq[row, idx.transfer_idx(i, w, a, t)] = 1.0   # outbound

                demand_t = inp.demand[(i, w)][t - 1]
                if t == 1:
                    A_eq[row, idx.inv_idx(i, w, t)] = 1.0  # (already set above; explicit for clarity)
                    b_eq[row] = inp.stock0[(i, w)] - demand_t
                else:
                    A_eq[row, idx.inv_idx(i, w, t - 1)] = -1.0
                    b_eq[row] = -demand_t
                row += 1

    bounds = Bounds(lb=lb, ub=ub)
    return MilpProblem(c=c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, integrality=integrality, index=idx)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ForecastingCore && python -m pytest tests/test_optimizer_builder.py -v`
Expected: PASS, all 5 tests. If `test_balance_row_t1_matches_hand_computed_equation` or the t2 variant fails, the sign convention or offset is wrong — do NOT adjust the test to match a wrong implementation; fix `build_problem` until the hand-derived matrix in the Global Constraints section matches exactly.

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/business/optimizer.py ForecastingCore/tests/test_optimizer_builder.py
git commit -m "feat(optimizer): build_problem assembles the MILP matrix (no solver call)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `optimize()` — solver wrapper + fallback

**Files:**
- Modify: `ForecastingCore/forecasting_core/business/optimizer.py` (add `optimize`, `_decode_solution`, `_fallback_recommend`)
- Test: `ForecastingCore/tests/test_optimizer_solve.py` (new)

**Interfaces:**
- Consumes: `OptimizationInput`, `build_problem` (Task 2).
- Produces: `optimize(inp: OptimizationInput, max_vars_before_fallback: int = 5000) -> OptimizationResult`.

- [ ] **Step 1: Write the failing tests**

Create `ForecastingCore/tests/test_optimizer_solve.py`:

```python
import pytest
from forecasting_core.business.optimizer import OptimizationInput, optimize


def test_transfer_chosen_over_purchase_when_surplus_exists_elsewhere():
    """
    W1 has a large surplus and no demand; W2 has a deficit. Transfer is much
    cheaper than ordering+holding. The optimizer must move stock from W1 to
    W2 rather than placing a new purchase order.
    """
    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=1,
        demand={("A", "W1"): [0.0], ("A", "W2"): [20.0]},
        stock0={("A", "W1"): 100.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 100.0},
        order_cost={"A": 10.0},
        transfer_cost=0.1,
    )
    result = optimize(inp)
    assert result.status == "optimal"
    assert result.transfers[("A", "W1", "W2", 1)] >= 20.0
    assert result.orders[("A", "W2", 1)] == 0.0
    assert result.shortages[("A", "W2", 1)] == 0.0


def test_purchase_chosen_when_no_surplus_anywhere():
    """Both warehouses are short — nothing to transfer from, must purchase."""
    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=1,
        demand={("A", "W1"): [10.0], ("A", "W2"): [10.0]},
        stock0={("A", "W1"): 0.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 100.0},
        order_cost={"A": 5.0},
        transfer_cost=0.5,
    )
    result = optimize(inp)
    assert result.status == "optimal"
    assert result.orders[("A", "W1", 1)] >= 10.0
    assert result.orders[("A", "W2", 1)] >= 10.0
    total_transferred = sum(v for k, v in result.transfers.items() if k[3] == 1)
    assert total_transferred == 0.0


def test_balance_conservation_holds_for_solved_result():
    """
    Structural invariant, independent of solver optimality: for every
    (sku, warehouse, t), the returned orders/transfers/inventory/shortages
    must satisfy the balance equation exactly.
    """
    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1", "W2"],
        horizon=2,
        demand={("A", "W1"): [5.0, 5.0], ("A", "W2"): [3.0, 3.0]},
        stock0={("A", "W1"): 10.0, ("A", "W2"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 50.0},
        order_cost={"A": 2.0},
        transfer_cost=0.5,
    )
    result = optimize(inp)
    for w in inp.warehouses:
        prev_inv = inp.stock0[("A", w)]
        for t in [1, 2]:
            inbound = sum(
                result.transfers.get(("A", a, w, t), 0.0)
                for a in inp.warehouses if a != w
            )
            outbound = sum(
                result.transfers.get(("A", w, b, t), 0.0)
                for b in inp.warehouses if b != w
            )
            expected_inv = (
                prev_inv + result.orders[("A", w, t)] + inbound - outbound
                - inp.demand[("A", w)][t - 1] + result.shortages[("A", w, t)]
            )
            assert abs(result.inventory[("A", w, t)] - expected_inv) < 1e-6
            prev_inv = result.inventory[("A", w, t)]


def test_fallback_used_when_solver_fails(monkeypatch):
    import forecasting_core.business.optimizer as opt_mod

    def _broken_milp(*args, **kwargs):
        class FakeResult:
            success = False
        return FakeResult()

    monkeypatch.setattr(opt_mod, "milp", _broken_milp)

    inp = OptimizationInput(
        skus=["A"],
        warehouses=["W1"],
        horizon=1,
        demand={("A", "W1"): [10.0]},
        stock0={("A", "W1"): 0.0},
        lead_time_buckets={"A": 0},
        holding_cost={"A": 1.0},
        stockout_cost={"A": 50.0},
        order_cost={"A": 2.0},
        transfer_cost=0.5,
    )
    result = optimize(inp)
    assert result.status == "fallback"
    assert result.orders[("A", "W1", 1)] > 0  # still a usable recommendation
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ForecastingCore && python -m pytest tests/test_optimizer_solve.py -v`
Expected: FAIL with `ImportError: cannot import name 'optimize'`.

- [ ] **Step 3: Implement `optimize`, `_decode_solution`, `_fallback_recommend`**

Append to `ForecastingCore/forecasting_core/business/optimizer.py`:

```python
from scipy.optimize import LinearConstraint, milp


def optimize(inp: OptimizationInput, max_vars_before_fallback: int = 5000) -> OptimizationResult:
    problem = build_problem(inp)
    if problem.index.n_vars > max_vars_before_fallback:
        return _fallback_recommend(inp, problem.index)

    try:
        constraint = LinearConstraint(problem.A_eq, lb=problem.b_eq, ub=problem.b_eq)
        res = milp(
            problem.c,
            integrality=problem.integrality,
            bounds=problem.bounds,
            constraints=[constraint],
        )
    except Exception:
        return _fallback_recommend(inp, problem.index)

    if not getattr(res, "success", False):
        return _fallback_recommend(inp, problem.index)

    return _decode_solution(inp, problem, res.x)


def _decode_solution(inp: OptimizationInput, problem: MilpProblem, x) -> OptimizationResult:
    idx = problem.index
    orders, transfers, inventory, shortages = {}, {}, {}, {}
    for i in inp.skus:
        for w in inp.warehouses:
            for t in range(1, inp.horizon + 1):
                orders[(i, w, t)] = round(float(x[idx.order_idx(i, w, t)]), 6)
                inventory[(i, w, t)] = round(float(x[idx.inv_idx(i, w, t)]), 6)
                shortages[(i, w, t)] = round(float(x[idx.short_idx(i, w, t)]), 6)
        for a, b in idx.transfer_pairs():
            for t in range(1, inp.horizon + 1):
                transfers[(i, a, b, t)] = round(float(x[idx.transfer_idx(i, a, b, t)]), 6)

    total_cost = float(problem.c @ x)
    return OptimizationResult(
        orders=orders, transfers=transfers, inventory=inventory,
        shortages=shortages, total_cost=total_cost, status="optimal",
    )


def _fallback_recommend(inp: OptimizationInput, idx: VariableIndex) -> OptimizationResult:
    """
    Per-(sku, warehouse) ROP-style fallback, ignoring transfers entirely:
    order enough each bucket to cover that bucket's demand net of running
    stock, never going negative. Used when the MILP solve fails, raises, or
    the problem is too large to solve in reasonable time.
    """
    orders, transfers, inventory, shortages = {}, {}, {}, {}
    total_cost = 0.0
    for i in inp.skus:
        for w in inp.warehouses:
            running = inp.stock0[(i, w)]
            for t in range(1, inp.horizon + 1):
                demand_t = inp.demand[(i, w)][t - 1]
                available = running if t > inp.lead_time_buckets.get(i, 0) else 0.0
                # naive: order exactly the shortfall against this bucket's demand
                needed = max(0.0, demand_t - available)
                order_qty = needed
                orders[(i, w, t)] = order_qty
                ending = available + order_qty - demand_t
                shortage = max(0.0, -ending)
                ending = max(0.0, ending)
                inventory[(i, w, t)] = ending
                shortages[(i, w, t)] = shortage
                running = ending
                total_cost += (
                    inp.order_cost[i] * order_qty
                    + inp.holding_cost[i] * ending
                    + inp.stockout_cost[i] * shortage
                )
        for a, b in idx.transfer_pairs():
            for t in range(1, inp.horizon + 1):
                transfers[(i, a, b, t)] = 0.0

    return OptimizationResult(
        orders=orders, transfers=transfers, inventory=inventory,
        shortages=shortages, total_cost=total_cost, status="fallback",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ForecastingCore && python -m pytest tests/test_optimizer_solve.py -v`
Expected: PASS, all 4 tests. If `test_transfer_chosen_over_purchase_when_surplus_exists_elsewhere` fails (optimizer orders instead of transfers), check the objective coefficients — `order_cost` must be high enough relative to `transfer_cost` in that test's input for the solver to prefer transferring (already tuned in the given test data: transfer_cost=0.1 vs order_cost=10.0).

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/business/optimizer.py ForecastingCore/tests/test_optimizer_solve.py
git commit -m "feat(optimizer): scipy.optimize.milp solve + per-warehouse ROP fallback

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Regression + wrap-up

- [ ] **Step 1: Full ForecastingCore suite**

Run: `cd ForecastingCore && python -m pytest tests/ -q`
Expected: PASS — this module is entirely new and additive; nothing existing imports it yet, so no regression risk to the rest of ForecastingCore.

- [ ] **Step 2: Sanity-check solve time on a moderately-sized problem (manual, not a hard assertion)**

Run a quick script (not a committed test) constructing e.g. 20 SKUs × 3 warehouses × 8 buckets (`n_vars` in the low thousands) and calling `optimize()`, timing it. If it takes more than a few seconds, note the `max_vars_before_fallback` threshold may need tuning down for production use — record the observation in the final report, don't change the default without discussing.

---

## Self-Review notes

- **Spec coverage:** Design spec Sub-proyecto 5 (motor MILP: compras + transferencias, `scipy.optimize.milp`, fallback) → Tasks 1-3. The API/UI surface (Sub-proyecto 6, MW-3) is explicitly out of scope for this plan.
- **Type consistency:** `VariableIndex`'s four accessor methods (`order_idx`/`transfer_idx`/`inv_idx`/`short_idx`) are used identically across `build_problem`, `optimize`, `_decode_solution`, and `_fallback_recommend` — no re-derivation of offsets anywhere outside `VariableIndex` itself.
- **The single highest-risk step is Task 2's balance-row construction** — the hand-computed test values in `test_balance_row_t1_matches_hand_computed_equation`/`test_balance_row_t2_references_prior_bucket_inventory` are the ground truth; if the implementation disagrees, fix the implementation, never the test's hand-derived expected values.
- **Documented MVP simplifications** (not gaps — explicit scope decisions, consistent with the design spec): transfers are instantaneous (no transfer lead time); `transfer_cost` is a single global scalar, not per-SKU/per-warehouse-pair; capacity constraints per warehouse are not modeled (design spec listed this as "optional, later phase").
