"""Python reference for the S&OP consensus math that backend-rs implements.

The Rust service (`backend-rs/src/consensus/`) owns the arithmetic; this module is
the spec it is held to. `gen_consensus_fixtures.py` runs the reference over random
cases and the Rust unit test demands EXACT equality (integers for the consensus,
f64-for-f64 for the accuracy figures), so neither can drift quietly.

Design choices that make exact equality possible:

* Percentages are integer basis points (1 bp = 0.01%). The consensus is integer
  arithmetic, rounded half away from zero, never a float.
* Accuracy sums are plain left-to-right float additions. Python 3.12 changed the
  built-in `sum()` to compensated summation, so the engine's own
  `forecast_value_added` can differ from a naive loop in the last bit; the test
  `test_consensus_reference.py` checks this reference against the engine within
  1e-9 relative, and the Rust port against this reference exactly.
"""

from __future__ import annotations

FUNCTIONS = ("sales", "finance", "operations")
FVA_MIN_POINTS = 5
FVA_NEUTRAL_BAND_PCT = 2.0


class InconsistentSubmissions(ValueError):
    """Two current submissions of one function overlap on one SKU."""


def div_round_half_away(num: int, den: int) -> int:
    """num / den rounded to the nearest integer, halves away from zero (den > 0)."""
    q, r = divmod(abs(num), den)
    if 2 * r >= den:
        q += 1
    return q if num >= 0 else -q


def clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)


def consensus_lines(config: dict, submissions: list[dict]) -> list[dict]:
    """The consensus per SKU and period.

    config: rule ('priority' | 'weighted'), priority (the three functions, first
    wins), weights ({function: int 0..1000}), cap_down_bp (<= 0), cap_up_bp (>= 0).
    submissions: id, sku, function, start, end (ints, inclusive day numbers), pct_bp.

    A SKU's dates are cut at every submission boundary; each piece covered by at
    least one submission becomes one line. Every submitted figure is first clamped
    to the caps, then:
      priority  the first function in `priority` that submitted decides;
      weighted  the integer weighted average of the functions that submitted
                (renormalised over them; a weight of 0 is no vote).
    A piece nobody can decide (only zero-weight functions covered it) has no line.
    """
    by_sku: dict[str, list[dict]] = {}
    for s in submissions:
        by_sku.setdefault(s["sku"], []).append(s)
    lines: list[dict] = []
    for sku in sorted(by_sku):
        subs = by_sku[sku]
        points = sorted({s["start"] for s in subs} | {s["end"] + 1 for s in subs})
        for a, b in zip(points, points[1:]):
            covering = [s for s in subs if s["start"] <= a <= s["end"]]
            if not covering:
                continue
            funcs = [s["function"] for s in covering]
            if len(set(funcs)) != len(funcs):
                raise InconsistentSubmissions(sku)
            covering.sort(key=lambda s: FUNCTIONS.index(s["function"]))
            inputs = []
            for s in covering:
                clamped = clamp(s["pct_bp"], config["cap_down_bp"], config["cap_up_bp"])
                inputs.append({"function": s["function"], "pct_bp": s["pct_bp"],
                               "submission_id": s["id"], "capped": clamped != s["pct_bp"],
                               "_clamped": clamped})
            source, pct = None, None
            if config["rule"] == "priority":
                for name in config["priority"]:
                    hit = next((i for i in inputs if i["function"] == name), None)
                    if hit is not None:
                        source, pct = name, hit["_clamped"]
                        break
            else:
                voters = [i for i in inputs if config["weights"][i["function"]] > 0]
                if voters:
                    num = sum(config["weights"][i["function"]] * i["_clamped"] for i in voters)
                    den = sum(config["weights"][i["function"]] for i in voters)
                    pct = div_round_half_away(num, den)
            if pct is None:
                continue
            lines.append({"sku": sku, "start": a, "end": b - 1, "pct_bp": pct, "source": source,
                          "inputs": [{k: v for k, v in i.items() if k != "_clamped"} for i in inputs]})
    return lines


# -- Forecast value added -----------------------------------------------------

def _safe_div(a: float, b: float):
    return None if b == 0 else a / b


def _seq_sum(values) -> float:
    total = 0.0
    for v in values:
        total += v
    return total


def adjusted_value(base: float, pct_bp: int) -> float:
    """The forecast after a percentage given in basis points: base * (1 + pct/100)."""
    return base * (1.0 + (pct_bp / 100.0) / 100.0)


def forecast_value_added(points: list[dict]) -> dict:
    """The engine's `forecast_value_added` over {base, pct_bp, actual} points, with
    sequential sums (see the module docstring), and one deliberate difference: a
    perfect statistical forecast that an adjustment spoiled is "worsened", not
    "neutral"."""
    n = len(points)
    if n == 0:
        return {"n_points": 0, "base_error": 0.0, "adjusted_error": 0.0, "actual_total": 0.0,
                "base_wape": None, "adjusted_wape": None, "base_bias": None, "adjusted_bias": None,
                "improvement_pct": None, "better_points": 0, "worse_points": 0, "verdict": "no_data"}
    adj = [adjusted_value(p["base"], p["pct_bp"]) for p in points]
    base_err = _seq_sum(abs(p["base"] - p["actual"]) for p in points)
    adj_err = _seq_sum(abs(a - p["actual"]) for a, p in zip(adj, points))
    total = _seq_sum(p["actual"] for p in points)
    better = sum(1 for a, p in zip(adj, points) if abs(a - p["actual"]) < abs(p["base"] - p["actual"]))
    worse = sum(1 for a, p in zip(adj, points) if abs(a - p["actual"]) > abs(p["base"] - p["actual"]))
    base_bias = _safe_div(_seq_sum(p["base"] for p in points) - total, total)
    adjusted_bias = _safe_div(_seq_sum(adj) - total, total)
    improvement = None if base_err <= 0 else (base_err - adj_err) / base_err * 100.0
    if n < FVA_MIN_POINTS:
        verdict = "too_little"
    elif improvement is None:
        # The engine reads this as "neutral". When the statistical forecast was
        # perfect, any adjustment that moved it made it worse, and saying "neutral"
        # would hide exactly the case the report exists for.
        verdict = "worsened" if adj_err > 0 else "neutral"
    elif improvement > FVA_NEUTRAL_BAND_PCT:
        verdict = "improved"
    elif improvement < -FVA_NEUTRAL_BAND_PCT:
        verdict = "worsened"
    else:
        verdict = "neutral"
    return {"n_points": n, "base_error": base_err, "adjusted_error": adj_err, "actual_total": total,
            "base_wape": _safe_div(base_err, total), "adjusted_wape": _safe_div(adj_err, total),
            "base_bias": base_bias, "adjusted_bias": adjusted_bias, "improvement_pct": improvement,
            "better_points": better, "worse_points": worse, "verdict": verdict}


def forecast_value_added_by(points: list[dict], key: str) -> list[dict]:
    """One reading per distinct `p[key]`, best improvement first, undefined last
    (a stable sort, so equal readings keep their first-seen order)."""
    groups: dict[str, list[dict]] = {}
    for p in points:
        groups.setdefault(str(p.get(key)), []).append(p)
    rows = [{key: k, **forecast_value_added(v)} for k, v in groups.items()]
    rows.sort(key=lambda r: (r["improvement_pct"] is None, -(r["improvement_pct"] or 0.0)))
    return rows
