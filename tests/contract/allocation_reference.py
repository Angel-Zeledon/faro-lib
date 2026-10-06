"""Plain-Python reference for the stock allocation core
(`backend-rs/src/allocation/core.rs`).

Written independently of the Rust code on purpose: where Rust keeps running
slack tables and integer cross-multiplication, this uses `fractions.Fraction`
and re-evaluates every constraint from scratch, so a mistake in one is not
copied into the other. Both work in integer micro-units and integer day
numbers, so the two must agree EXACTLY, not approximately.

The model, for one SKU:

* supply(t) = max(0, stock) + the sum of arrivals dated on or before day t
  (an arrival with no date, or dated in the past, is available today);
* a claim is a commitment's expected units due on max(delivery day, today);
* an allocation x is feasible when, for every day t, the units allocated to
  claims due on or before t never exceed supply(t);
* tiers are served lowest number first; inside a tier either earliest date
  first (ties by id), or, for a fair-share tier, by progressive filling: every
  active member gets the same fraction of its units, the day with the least
  slack per active unit freezes the members due by it at floor(fraction *
  units), and the others continue.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Optional

MICRO = 1_000_000


def _supply(stock: int, arrivals: list[dict], today: int, t: int) -> int:
    total = max(0, stock)
    for a in arrivals:
        if a["qty"] <= 0:
            continue
        day = today if a["day"] is None else max(a["day"], today)
        if day <= t:
            total += a["qty"]
    return total


def allocate(claims: list[dict], today: int, stock: int, arrivals: list[dict],
             fair_tiers: list[int]) -> list[int]:
    """claims: [{id, tier, day, units}], arrivals: [{day|None, qty}] -> allocated
    micro-units per claim, in the order given."""
    eff = [max(c["day"], today) for c in claims]
    units = [max(0, c["units"]) for c in claims]
    alloc = [0] * len(claims)

    candidate_days = sorted(set(eff) | {
        today if a["day"] is None else max(a["day"], today)
        for a in arrivals if a["qty"] > 0})

    def committed(t: int, skip: Optional[set] = None) -> int:
        return sum(alloc[j] for j in range(len(claims))
                   if eff[j] <= t and not (skip and j in skip))

    for tier in sorted({c["tier"] for c in claims}):
        members = [i for i, c in enumerate(claims) if c["tier"] == tier]
        if tier in fair_tiers:
            active = set(members)
            while active:
                binding = None  # (ratio, day)
                for t in candidate_days:
                    wanted = sum(units[m] for m in active if eff[m] <= t)
                    if wanted == 0:
                        continue
                    slack = max(0, _supply(stock, arrivals, today, t) - committed(t, active))
                    if slack >= wanted:
                        continue
                    ratio = Fraction(slack, wanted)
                    if binding is None or ratio <= binding[0]:
                        binding = (ratio, t)
                if binding is None:
                    for m in active:
                        alloc[m] = units[m]
                    break
                ratio, t_star = binding
                frozen = {m for m in active if eff[m] <= t_star}
                for m in frozen:
                    alloc[m] = (ratio * units[m]).__floor__()
                active -= frozen
        else:
            order = sorted(members, key=lambda i: (claims[i]["day"], claims[i]["id"]))
            for i in order:
                room = min(
                    (_supply(stock, arrivals, today, t) - committed(t)
                     for t in candidate_days if t >= eff[i]),
                    default=units[i])
                alloc[i] = max(0, min(units[i], room))
    return alloc
