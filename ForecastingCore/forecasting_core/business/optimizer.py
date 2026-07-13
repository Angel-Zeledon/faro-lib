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

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
from scipy.optimize import Bounds


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
                    # inv[i,w,0] is the constant stock0[i,w], not a variable,
                    # so it folds into this row's RHS instead of a column.
                    b_eq[row] = inp.stock0[(i, w)] - demand_t
                else:
                    A_eq[row, idx.inv_idx(i, w, t - 1)] = -1.0
                    b_eq[row] = -demand_t
                row += 1

    bounds = Bounds(lb=lb, ub=ub)
    return MilpProblem(c=c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, integrality=integrality, index=idx)
