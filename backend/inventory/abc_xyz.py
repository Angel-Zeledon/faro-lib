"""
ABC / XYZ classification and the service level it suggests. Pure Python: no
database, no pandas — the numbers in, the letters out, so the boundaries can be
tested without a server.

Two independent axes, each with ONE definition:

  ABC  how much money the SKU moves. Rank by demand value (demand x unit cost),
       cumulative share of the total: A while the running share BEFORE adding the
       SKU is under 80%, B under 95%, C after that. Annual value is the same
       ranking as daily value (a constant factor), so no period conversion is
       needed to classify.
  XYZ  how predictable the demand is. The coefficient of variation the ENGINE
       already computes per series (`forecasting_core/analysis/distribution.py`,
       stored in the training result's `data_quality[sku]["cv"]`). This module
       does not derive a second variability; it only buckets that number:
       X < 0.5 <= Y < 1.0 <= Z.

Nothing here writes anything. `SUGGESTED_SERVICE_LEVEL` is a suggestion the
buyer reads and applies per class (`service_level_classes.py`); no number moves
on its own.
"""

from __future__ import annotations

from typing import Iterable, Optional

# Cumulative-value cut-offs: A up to 80%, B up to 95%, C the rest.
ABC_A_CUTOFF = 0.80
ABC_B_CUTOFF = 0.95

# Coefficient-of-variation cut-offs: X below 0.5, Y below 1.0, Z from 1.0.
XYZ_X_CUTOFF = 0.5
XYZ_Y_CUTOFF = 1.0

ABC_CLASSES: tuple[str, ...] = ("A", "B", "C")

# What the market's planning suites suggest as a starting point: protect the
# few SKUs that carry the money, accept more stockout risk on the long tail.
# A suggestion, never applied by itself.
SUGGESTED_SERVICE_LEVEL: dict[str, float] = {"A": 0.98, "B": 0.95, "C": 0.90}


def classify_xyz(cv: Optional[float]) -> str:
    """X = predictable, Y = moderate, Z = erratic; '?' when the engine gave no CV."""
    if cv is None:
        return "?"
    if cv < XYZ_X_CUTOFF:
        return "X"
    if cv < XYZ_Y_CUTOFF:
        return "Y"
    return "Z"


def classify_abc(scored: Iterable[tuple[str, float]]) -> dict[str, str]:
    """`(sku, demand value)` pairs -> `{sku: 'A'|'B'|'C'}`.

    The class is decided by the cumulative share BEFORE the SKU is added, so a
    single dominant SKU (99% of the value) is an A, not a C. Equal values keep
    the order they came in (stable sort), so the result never depends on hash
    order. A total of zero (no demand anywhere) puts everything in C: with no
    value to rank, nothing deserves the highest protection.
    """
    ordered = sorted(scored, key=lambda pair: pair[1], reverse=True)
    total = sum(max(0.0, v) for _, v in ordered)
    if total <= 0:
        return {sku: "C" for sku, _ in ordered}

    result: dict[str, str] = {}
    cumulative = 0.0
    for sku, value in ordered:
        if cumulative < ABC_A_CUTOFF:
            result[sku] = "A"
        elif cumulative < ABC_B_CUTOFF:
            result[sku] = "B"
        else:
            result[sku] = "C"
        cumulative += max(0.0, value) / total
    return result


def class_summary(rows: list[dict]) -> list[dict]:
    """One row per ABC class from `[{abc, value, service_level, owned}]`.

    `owned` is True when somebody (the buyer, a file import, a rule) already
    decides that SKU's service level, so applying a suggestion would not touch
    it. Returns the three classes always, in A, B, C order, so a tenant with no
    C SKUs still shows an empty C instead of a missing row.
    """
    total_value = sum(max(0.0, float(r.get("value") or 0.0)) for r in rows)
    out: list[dict] = []
    for cls in ABC_CLASSES:
        members = [r for r in rows if r.get("abc") == cls]
        value = sum(max(0.0, float(r.get("value") or 0.0)) for r in members)
        levels = [float(r["service_level"]) for r in members if r.get("service_level") is not None]
        out.append({
            "abc": cls,
            "skus": len(members),
            "value_share": round(value / total_value, 4) if total_value > 0 else 0.0,
            "current_service_level": round(sum(levels) / len(levels), 4) if levels else None,
            "suggested_service_level": SUGGESTED_SERVICE_LEVEL[cls],
            "would_change": sum(
                1 for r in members
                if not r.get("owned")
                and r.get("service_level") is not None
                and abs(float(r["service_level"]) - SUGGESTED_SERVICE_LEVEL[cls]) > 1e-9
            ),
            "owned": sum(1 for r in members if r.get("owned")),
        })
    return out
