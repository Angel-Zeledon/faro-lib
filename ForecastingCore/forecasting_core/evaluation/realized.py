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
