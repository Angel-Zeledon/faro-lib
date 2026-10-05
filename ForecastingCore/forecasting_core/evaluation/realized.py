"""
Forecast vs. what actually happened.

Backtests grade a model on history it was held out from. This grades the
forecast the product really published, against sales that arrived *after* it was
made — the number a buyer should trust. Pure Python on plain dicts: the caller
(backend) lines the dates up and hands the series in.

Metrics, over the (SKU, period) points where both a forecast and an actual
exist:

  WAPE  = sum|forecast - actual| / sum(actual)      (volume-weighted, robust to zeros)
  MAPE  = mean(|forecast - actual| / actual)        (only points where actual > 0)
  bias  = (sum(forecast) - sum(actual)) / sum(actual)
          positive = the forecast ran HIGH (over-forecasting, excess stock risk),
          negative = it ran LOW (stockout risk)

The verdict is a stable code plus numbers; the frontend renders the sentence.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

# Verdict thresholds on the pooled WAPE.
WAPE_GOOD = 0.20
WAPE_FAIR = 0.40
# |bias| above this is called out as a direction.
BIAS_NOTABLE = 0.10
# Fewer compared points than this is not enough to judge anything.
MIN_POINTS_FOR_VERDICT = 5


def _safe_div(a: float, b: float) -> Optional[float]:
    return None if b == 0 else a / b


def _metrics(points: Sequence[Tuple[float, float]]) -> dict:
    """points: (forecast, actual) pairs."""
    n = len(points)
    sum_f = sum(f for f, _ in points)
    sum_a = sum(a for _, a in points)
    abs_err = sum(abs(f - a) for f, a in points)
    apes = [abs(f - a) / a for f, a in points if a > 0]
    return {
        "n_points": n,
        "wape": _safe_div(abs_err, sum_a),
        "mape": (sum(apes) / len(apes)) if apes else None,
        "bias": _safe_div(sum_f - sum_a, sum_a),
        "total_forecast": sum_f,
        "total_actual": sum_a,
    }


def verdict_for(wape: Optional[float], bias: Optional[float], n_points: int) -> dict:
    """A stable code (never prose) with the numbers the sentence needs.

    level: no_data | too_little | good | fair | poor
    direction: over | under | balanced   (only meaningful with a level that judges)
    """
    if n_points == 0 or wape is None:
        return {"level": "no_data", "direction": "balanced",
                "wape": None, "bias": None, "n_points": n_points}
    direction = "balanced"
    if bias is not None and bias > BIAS_NOTABLE:
        direction = "over"
    elif bias is not None and bias < -BIAS_NOTABLE:
        direction = "under"
    if n_points < MIN_POINTS_FOR_VERDICT:
        level = "too_little"
    elif wape <= WAPE_GOOD:
        level = "good"
    elif wape <= WAPE_FAIR:
        level = "fair"
    else:
        level = "poor"
    return {"level": level, "direction": direction,
            "wape": wape, "bias": bias, "n_points": n_points}


def compare_forecast_to_actuals(
    forecasts: Dict[str, Dict[str, float]],
    actuals: Dict[str, Dict[str, float]],
) -> dict:
    """Compare ``{sku: {iso_date: forecast}}`` with ``{sku: {iso_date: actual}}``.

    Only dates present in BOTH are compared; everything else is counted in
    ``skipped_points`` rather than guessed (a missing actual is not a zero).

    Returns::

        {
          "aggregate": {metrics..., "n_skus", "series": [{date, forecast, actual}], "verdict": {...}},
          "skus": [{"sku", metrics..., "points": [{date, forecast, actual}]}],  # worst WAPE first
          "skipped_points": int,
        }
    """
    per_sku: List[dict] = []
    pooled: List[Tuple[float, float]] = []
    by_date: Dict[str, List[float]] = {}
    skipped = 0

    for sku, fc in forecasts.items():
        act = actuals.get(sku) or {}
        pts = []
        for date, f in sorted(fc.items()):
            if f is None:
                continue
            a = act.get(date)
            if a is None:
                skipped += 1
                continue
            pts.append({"date": date, "forecast": float(f), "actual": float(a)})
        if not pts:
            continue
        pairs = [(p["forecast"], p["actual"]) for p in pts]
        pooled.extend(pairs)
        for p in pts:
            agg = by_date.setdefault(p["date"], [0.0, 0.0])
            agg[0] += p["forecast"]
            agg[1] += p["actual"]
        m = _metrics(pairs)
        per_sku.append({"sku": sku, **m, "points": pts})

    # Worst first; SKUs whose WAPE is undefined (no actual volume) go last.
    per_sku.sort(key=lambda r: (r["wape"] is None, -(r["wape"] or 0.0)))

    agg_metrics = _metrics(pooled)
    series = [{"date": d, "forecast": v[0], "actual": v[1]} for d, v in sorted(by_date.items())]
    return {
        "aggregate": {
            **agg_metrics,
            "n_skus": len(per_sku),
            "series": series,
            "verdict": verdict_for(agg_metrics["wape"], agg_metrics["bias"], agg_metrics["n_points"]),
        },
        "skus": per_sku,
        "skipped_points": skipped,
    }


# ── Forecast value added: did a manual adjustment beat the untouched forecast? ─
#
# A planner who nudges a forecast ("+15%, promotion") is making a bet. Once the
# real sales arrive the bet can be graded against the forecast the model made on
# its own, on the very same (SKU, period) points: if the adjusted forecast ran
# closer to what sold, the adjustment ADDED value; if farther, it destroyed some.
#
# Error is absolute error summed over the points (the numerator of WAPE), so a
# group's figure is volume-weighted exactly like every other accuracy number in
# the product. ``improvement_pct`` is the relative change of that error:
#   +20  the adjusted forecast's error was 20% SMALLER than the model's
#   -35  it was 35% LARGER
# ``None`` when the model alone was exact (nothing to improve on, so no ratio).

# Compared points below which a group's verdict is "too_little", never a claim.
FVA_MIN_POINTS = 5
# |improvement| under this many percent reads as "no real difference".
FVA_NEUTRAL_BAND_PCT = 2.0


def forecast_value_added(points: Sequence[dict]) -> dict:
    """Grade adjusted forecasts against the unadjusted ones.

    ``points``: ``{"base", "adjusted", "actual"}`` per (SKU, period), all
    numbers. Returns the two absolute errors, their WAPEs, ``improvement_pct``
    and a stable verdict code (``improved`` / ``worsened`` / ``neutral`` /
    ``too_little`` / ``no_data``); the frontend renders the sentence.
    """
    n = len(points)
    if n == 0:
        return {"n_points": 0, "base_error": 0.0, "adjusted_error": 0.0, "actual_total": 0.0,
                "base_wape": None, "adjusted_wape": None, "improvement_pct": None,
                "better_points": 0, "worse_points": 0, "verdict": "no_data"}
    base_err = sum(abs(float(p["base"]) - float(p["actual"])) for p in points)
    adj_err = sum(abs(float(p["adjusted"]) - float(p["actual"])) for p in points)
    total = sum(float(p["actual"]) for p in points)
    better = sum(1 for p in points
                 if abs(float(p["adjusted"]) - float(p["actual"]))
                 < abs(float(p["base"]) - float(p["actual"])))
    worse = sum(1 for p in points
                if abs(float(p["adjusted"]) - float(p["actual"]))
                > abs(float(p["base"]) - float(p["actual"])))
    improvement = None if base_err <= 0 else (base_err - adj_err) / base_err * 100.0
    if n < FVA_MIN_POINTS:
        verdict = "too_little"
    elif improvement is None:
        verdict = "neutral"
    elif improvement > FVA_NEUTRAL_BAND_PCT:
        verdict = "improved"
    elif improvement < -FVA_NEUTRAL_BAND_PCT:
        verdict = "worsened"
    else:
        verdict = "neutral"
    return {
        "n_points": n, "base_error": base_err, "adjusted_error": adj_err,
        "actual_total": total,
        "base_wape": _safe_div(base_err, total), "adjusted_wape": _safe_div(adj_err, total),
        "improvement_pct": improvement, "better_points": better, "worse_points": worse,
        "verdict": verdict,
    }


def forecast_value_added_by(points: Sequence[dict], key: str) -> List[dict]:
    """``forecast_value_added`` per distinct ``p[key]`` (a user, a reason), each
    row carrying that value under ``key``; best improvement first, groups whose
    ratio is undefined last."""
    groups: Dict[str, List[dict]] = {}
    for p in points:
        groups.setdefault(str(p.get(key)), []).append(p)
    rows = [{key: k, **forecast_value_added(v)} for k, v in groups.items()]
    rows.sort(key=lambda r: (r["improvement_pct"] is None, -(r["improvement_pct"] or 0.0)))
    return rows


# ── Where a dataset sits relative to a forecast window ───────────────────────

def describe_overlap(
    forecast_from: Optional[str], forecast_to: Optional[str],
    data_first: Optional[str], data_last: Optional[str],
) -> dict:
    """How the dates of a dataset line up with the window a forecast covers.

    Inputs are ISO dates (``YYYY-MM-DD``) or None. The result is a stable code
    plus numbers; the frontend renders the sentence, so nothing here is prose::

        {"relation": ..., "overlap_from": date|None, "overlap_to": date|None,
         "gap_days": int|None}

    relation:
      ``covers``                the data spans the whole forecast window
      ``partial``               the data covers only part of it
      ``ends_before_forecast``  the data stops before the forecast starts;
                                ``gap_days`` is the distance between the two
      ``starts_after_forecast`` the data begins after the forecast ends
      ``unknown``               a date is missing, so nothing can be said
    """
    from datetime import date

    def _d(x: Optional[str]) -> Optional[date]:
        try:
            return date.fromisoformat(str(x)[:10]) if x else None
        except ValueError:
            return None

    ff, ft, df, dl = _d(forecast_from), _d(forecast_to), _d(data_first), _d(data_last)
    out = {"relation": "unknown", "overlap_from": None, "overlap_to": None, "gap_days": None}
    if None in (ff, ft, df, dl):
        return out
    if dl < ff:
        return {**out, "relation": "ends_before_forecast", "gap_days": (ff - dl).days}
    if df > ft:
        return {**out, "relation": "starts_after_forecast", "gap_days": (df - ft).days}
    lo, hi = max(ff, df), min(ft, dl)
    out["overlap_from"], out["overlap_to"] = lo.isoformat(), hi.isoformat()
    out["relation"] = "covers" if (df <= ff and dl >= ft) else "partial"
    return out

# ── Is the live forecast holding up against its training-time accuracy? ──────
#
# The question behind "retrain?": the forecast was graded at training time (a
# validation WAPE per series) and is now being graded against what really sold.
# Both numbers are volume-weighted WAPEs so they can be compared like for like:
# the training figures are re-weighted by the volume that was actually sold.
#
# `DriftDetector.performance_decay` (monitoring/drift.py) asks the same question
# of MAE history, but WAPE is a ratio, its +1e-8 guard turns a near-zero
# baseline into an astronomic "degradation", and it has no notion of how many
# points were compared. The rule below is the same relative comparison with
# those three guards.

# Relative worsening (percent) at which the forecast counts as degraded.
DEGRADATION_THRESHOLD_PCT = 25.0
# The worsening must also be at least this many WAPE points (0.05 = 5 pp):
# 0.04 -> 0.06 is +50% relative and still an excellent forecast.
DEGRADATION_MIN_ABS_INCREASE = 0.05
# Compared (series, period) points needed before the rule may say anything.
DEGRADATION_MIN_POINTS = 10
# A training WAPE this large is the engine's `sum|e| / (0 + 1e-8)` on a
# validation window with no demand: not an error rate, so it is not a baseline.
_WAPE_UNDEFINED = 1e6


def training_wape_by_series(
    rows: Sequence[dict], champions: Dict[str, str],
) -> Dict[str, float]:
    """``{series: validation WAPE of the model it is bought from}``.

    ``rows`` are the engine's per-(series, model) metric rows; ``champions``
    maps each series to the model the product actually uses. Baseline rows are
    ignored, and so is a WAPE that measures nothing: exactly 0 with MAE 0 (no
    demand in the window, 0/0), non-finite, or the epsilon artefact above.
    """
    import math

    out: Dict[str, float] = {}
    for r in rows:
        if r.get("type") == "baseline":
            continue
        wape, sku = r.get("wape"), r.get("sku")
        if wape is None or sku is None or champions.get(str(sku)) != r.get("model"):
            continue
        wape = float(wape)
        if wape == 0.0 and float(r.get("mae") or 0.0) == 0.0:
            continue
        if not math.isfinite(wape) or wape >= _WAPE_UNDEFINED:
            continue
        out[str(sku)] = wape
    return out


def baseline_wape(
    training_wape: Dict[str, float], actual_volume: Dict[str, float],
) -> Optional[float]:
    """The training-time WAPE restated over the series now being compared,
    weighted by their realised volume (``None`` when nothing overlaps or no
    volume was sold). Volume-weighting makes it the same statistic as the
    pooled realised WAPE, so the two can be subtracted."""
    shared = [k for k in training_wape if actual_volume.get(k, 0.0) > 0]
    total = sum(actual_volume[k] for k in shared)
    if not shared or total <= 0:
        return None
    return sum(training_wape[k] * actual_volume[k] for k in shared) / total


def assess_degradation(
    baseline: Optional[float],
    realised: Optional[float],
    n_points: int,
    threshold_pct: float = DEGRADATION_THRESHOLD_PCT,
    min_points: int = DEGRADATION_MIN_POINTS,
    min_abs_increase: float = DEGRADATION_MIN_ABS_INCREASE,
) -> dict:
    """Is the realised WAPE materially worse than the training-time one?

    ``status``: ``no_baseline`` (nothing to compare with), ``too_little`` (fewer
    than ``min_points`` compared points), ``degraded`` or ``stable``. The numbers
    are always returned when they exist; ``degradation_pct`` is negative when the
    forecast is doing better than it did at training.
    """
    out = {
        "status": "no_baseline", "baseline_wape": baseline, "realised_wape": realised,
        "degradation_pct": None, "threshold_pct": float(threshold_pct),
        "n_points": int(n_points),
    }
    if baseline is None or realised is None or baseline <= 0:
        return out
    pct = (realised - baseline) / baseline * 100.0
    out["degradation_pct"] = round(pct, 2)
    if n_points < min_points:
        out["status"] = "too_little"
    elif pct >= threshold_pct and (realised - baseline) >= min_abs_increase:
        out["status"] = "degraded"
    else:
        out["status"] = "stable"
    return out
