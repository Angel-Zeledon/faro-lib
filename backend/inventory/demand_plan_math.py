"""Demand plan versions: the pure half (no I/O).

A demand plan version freezes, per SKU and per forecast period, three numbers
and their sum:

    plan = statistical forecast + manual adjustment + committed units

None of the three is computed a second way here:

* the statistical forecast is the champion forecast the planner already reads
  (`forecast_check.service._champion_forecasts`), summed over stores;
* the adjustment is the forecast times `forecast_adjustment_service.
  demand_multiplier(...) - 1`, the very function the purchase recommendation uses,
  evaluated over the period's own window (an adjustment covering 3 days of a
  7-day period moves it by 3/7 of its percentage, exactly as it moves a lead time);
* the committed units are what `committed_demand_service.committed_units(...)`
  counts (open, on top of the baseline, quantity x probability, company-wide),
  laid into the period that contains each delivery date. An overdue commitment
  lands in the first period, the same rule the optimizer's buckets follow.

The version is a RECORD and a MEASUREMENT. Nothing here feeds a purchase
recommendation.

`compare_with_actuals` grades the frozen plan and the frozen statistical forecast
on the same (SKU, period) points against real sales, through ForecastingCore's
forecast-value-added maths, so a company can see whether its consensus beat the
model.
"""

from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from typing import Any, Optional

from backend.errors import AppError
from backend.inventory.committed_demand_service import committed_units
from backend.inventory.forecast_adjustment_service import demand_multiplier
from backend.inventory.series import split_key

SNAPSHOT_FORMAT = 1
# SKU x period cells one version may hold. 250k is ~5,000 SKUs over a year of
# weeks; past it the refusal says to shorten the horizon (never a silent cut).
MAX_CELLS = 250_000
MAX_HORIZON_PERIODS = 520
# Per-SKU rows a diff or an accuracy reading returns (totals always cover all).
MAX_ROWS_RETURNED = 200
_EPS = 1e-6


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 4)


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


# ── Periods ──────────────────────────────────────────────────────────────────

def _next_month(d: date) -> date:
    return date(d.year + (d.month // 12), d.month % 12 + 1, 1)


def period_spans(labels: list[str], granularity: Optional[str] = None) -> list[tuple[date, date]]:
    """`[start, end)` of each forecast period, from its date labels.

    The engine labels months by their first day ("MS") and weeks by the Sunday
    that ENDS them (pandas "W"); days are their own date. A month labelled by its
    last day is read as that month too. With a single label the session's
    granularity decides the length (daily when unknown).
    """
    dates = sorted({_as_date(x) for x in labels})
    if not dates:
        return []
    diffs = [(b - a).days for a, b in zip(dates, dates[1:])]
    monthly = (all(27 <= x <= 31 for x in diffs) if diffs else granularity == "monthly")
    if monthly:
        out = []
        for d in dates:
            if d.day == 1:
                out.append((d, _next_month(d)))
            else:   # month-end label: the month it closes
                out.append((d.replace(day=1), d + timedelta(days=1)))
        return out
    step = min(diffs) if diffs else {"weekly": 7, "daily": 1}.get(granularity or "daily", 1)
    step = max(1, step)
    end_labelled = step >= 7 and step % 7 == 0 and all(d.weekday() == 6 for d in dates)
    if end_labelled:
        return [(d - timedelta(days=step - 1), d + timedelta(days=1)) for d in dates]
    return [(d, d + timedelta(days=step)) for d in dates]


# ── Building the snapshot ────────────────────────────────────────────────────

def _forecast_by_sku(forecasts: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """{series key: {date: value}} -> {sku: {date: value}} summed over stores."""
    out: dict[str, dict[str, float]] = {}
    for key, series in (forecasts or {}).items():
        sku = split_key(str(key))[0]
        bucket = out.setdefault(sku, {})
        for d, v in (series or {}).items():
            if v is None:
                continue
            fv = float(v)
            if not math.isfinite(fv):
                continue
            label = str(d)[:10]
            bucket[label] = bucket.get(label, 0.0) + fv
    return {s: b for s, b in out.items() if b}


def build_snapshot(forecasts: dict[str, dict[str, float]],
                   adjustments_by_sku: dict[str, list[dict]],
                   commitments_by_sku: dict[str, list[dict]],
                   *, today: date, horizon_periods: Optional[int] = None,
                   granularity: Optional[str] = None) -> dict:
    """The frozen plan, per SKU per period, from the current planning inputs.

    Only periods that have not fully passed (`end > today`) are planned; at most
    `horizon_periods` of them. Raises AppError when there is nothing to plan or
    when the plan would be too large (the caller is told to shorten the horizon,
    nothing is cut silently).
    """
    per_sku = _forecast_by_sku(forecasts)
    labels = sorted({d for s in per_sku.values() for d in s})
    if not labels:
        raise AppError("demand_plan_no_forecast",
                       "This forecast has no values to build a plan from", status_code=409)
    spans = period_spans(labels, granularity)
    keep = [i for i, (_, end) in enumerate(spans) if end > today]
    if not keep:
        raise AppError("demand_plan_no_future_periods",
                       "Every period of this forecast is already in the past; train a "
                       "newer forecast to plan ahead", status_code=409,
                       params={"last_period": labels[-1]})
    if horizon_periods is not None:
        if not 1 <= int(horizon_periods) <= MAX_HORIZON_PERIODS:
            raise AppError("demand_plan_horizon_invalid",
                           "The horizon must be between 1 and 520 periods",
                           params={"max": MAX_HORIZON_PERIODS})
        keep = keep[:int(horizon_periods)]
    labels_k = [labels[i] for i in keep]
    spans_k = [spans[i] for i in keep]
    n = len(labels_k)
    ends_ord = [e.toordinal() for _, e in spans_k]
    horizon_end = spans_k[-1][1]
    window_days = max(1, (horizon_end - today).days)

    # Committed units per SKU per period (company-wide, the Panel's own rule).
    committed: dict[str, list[float]] = {}
    committed_ids: dict[str, list[str]] = {}
    for sku, rows in (commitments_by_sku or {}).items():
        if not rows:
            continue
        _, applied = committed_units(rows, today, window_days)
        if not applied:
            continue
        series = [0.0] * n
        for e in applied:
            idx = bisect.bisect_right(ends_ord, date.fromisoformat(e["delivery_date"]).toordinal())
            if idx >= n:          # cannot happen inside the window; never guess a bucket
                continue
            series[idx] += float(e["units"])
        if any(v > 0 for v in series):
            committed[sku] = series
            committed_ids[sku] = [str(e["commitment_id"]) for e in applied]

    skus = sorted(set(per_sku) | set(committed))
    cells = len(skus) * n
    if cells > MAX_CELLS:
        raise AppError("demand_plan_too_large",
                       "This plan is too large to store as one version; choose a shorter horizon",
                       params={"skus": len(skus), "periods": n, "cells": cells,
                               "max_cells": MAX_CELLS})

    lines: dict[str, dict] = {}
    tot_f, tot_a, tot_c, tot_p = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
    without_forecast = 0
    for sku in skus:
        fc = per_sku.get(sku) or {}
        adjs = (adjustments_by_sku or {}).get(sku) or []
        c_series = committed.get(sku) or [0.0] * n
        f_list: list[Optional[float]] = []
        a_list: list[float] = []
        p_list: list[float] = []
        adj_ids: set[str] = set()
        for i, label in enumerate(labels_k):
            f = fc.get(label)
            a = 0.0
            if f is not None and adjs:
                start, end = spans_k[i]
                mult, applied = demand_multiplier(adjs, start, (end - start).days)
                a = f * (mult - 1.0)
                adj_ids.update(str(x["adjustment_id"]) for x in applied)
            c = c_series[i]
            p = max(0.0, (f or 0.0) + a + c)
            f_list.append(_r(f))
            a_list.append(_r(a))
            p_list.append(_r(p))
            tot_f[i] += f or 0.0
            tot_a[i] += a
            tot_c[i] += c
            tot_p[i] += p
        if not fc:
            without_forecast += 1
        lines[sku] = {
            "f": f_list, "a": a_list, "c": [_r(x) for x in c_series], "p": p_list,
            "adj": sorted(adj_ids), "com": committed_ids.get(sku, []),
        }

    return {
        "format": SNAPSHOT_FORMAT,
        "anchor": today.isoformat(),
        "granularity": granularity,
        "periods": labels_k,
        "starts": [s.isoformat() for s, _ in spans_k],
        "ends": [e.isoformat() for _, e in spans_k],
        "skus": lines,
        "totals": totals_of(tot_f, tot_a, tot_c, tot_p, labels_k,
                            sku_count=len(skus), without_forecast=without_forecast),
    }


def totals_of(tot_f, tot_a, tot_c, tot_p, labels, *, sku_count: int, without_forecast: int) -> dict:
    return {
        "forecast": _r(sum(tot_f)), "adjustment": _r(sum(tot_a)),
        "committed": _r(sum(tot_c)), "plan": _r(sum(tot_p)),
        "sku_count": sku_count, "skus_without_forecast": without_forecast,
        "period_count": len(labels),
        "by_period": [
            {"period": labels[i], "forecast": _r(tot_f[i]), "adjustment": _r(tot_a[i]),
             "committed": _r(tot_c[i]), "plan": _r(tot_p[i])}
            for i in range(len(labels))
        ],
    }


# ── Reading one version ──────────────────────────────────────────────────────

def line_rows(snapshot: dict, *, q: Optional[str] = None, offset: int = 0,
              limit: int = 50) -> dict:
    """Per-SKU rows of a snapshot, biggest plan first, optionally filtered by a
    case-insensitive SKU substring."""
    needle = (q or "").strip().lower()
    rows = []
    for sku, line in (snapshot.get("skus") or {}).items():
        if needle and needle not in sku.lower():
            continue
        f = line.get("f") or []
        rows.append({
            "sku": sku,
            "forecast": _r(sum(x for x in f if x is not None)),
            "adjustment": _r(sum(line.get("a") or [])),
            "committed": _r(sum(line.get("c") or [])),
            "plan": _r(sum(line.get("p") or [])),
            "has_forecast": any(x is not None for x in f),
            "adjustments": len(line.get("adj") or []),
            "commitments": len(line.get("com") or []),
            "by_period": line.get("p") or [],
        })
    rows.sort(key=lambda r: (-(r["plan"] or 0.0), r["sku"]))
    offset, limit = max(0, int(offset)), max(1, min(int(limit), MAX_ROWS_RETURNED))
    return {"total": len(rows), "offset": offset, "limit": limit,
            "periods": snapshot.get("periods") or [], "items": rows[offset:offset + limit]}


# ── Two versions side by side ────────────────────────────────────────────────

def diff_versions(snap_a: dict, snap_b: dict, limit: int = 50) -> dict:
    """Which SKUs' plan quantity moved most from version A to version B, summed
    over the periods BOTH versions plan (a different horizon is not a change).
    """
    periods_a = snap_a.get("periods") or []
    periods_b = snap_b.get("periods") or []
    common = sorted(set(periods_a) & set(periods_b))
    base = {"common_periods": common, "n_common_periods": len(common),
            "periods_only_in_a": len(set(periods_a) - set(common)),
            "periods_only_in_b": len(set(periods_b) - set(common))}
    if not common:
        return {**base, "status": "no_common_periods", "total_a": None, "total_b": None,
                "n_skus_changed": 0, "skus_only_in_a": 0, "skus_only_in_b": 0, "items": []}
    idx_a = [periods_a.index(p) for p in common]
    idx_b = [periods_b.index(p) for p in common]
    lines_a, lines_b = snap_a.get("skus") or {}, snap_b.get("skus") or {}

    def _sum(line: Optional[dict], idx: list[int]) -> float:
        if not line:
            return 0.0
        p = line.get("p") or []
        return sum(float(p[i] or 0.0) for i in idx if i < len(p))

    rows = []
    for sku in sorted(set(lines_a) | set(lines_b)):
        a = _sum(lines_a.get(sku), idx_a)
        b = _sum(lines_b.get(sku), idx_b)
        change = b - a
        rows.append({
            "sku": sku, "plan_a": _r(a), "plan_b": _r(b), "change": _r(change),
            "change_pct": _r(change / a * 100.0) if a > _EPS else None,
            "only_in": "a" if sku not in lines_b else ("b" if sku not in lines_a else None),
        })
    changed = [r for r in rows if abs(r["change"] or 0.0) > _EPS]
    changed.sort(key=lambda r: (-abs(r["change"] or 0.0), r["sku"]))
    return {
        **base, "status": "ok",
        "total_a": _r(sum(r["plan_a"] for r in rows)),
        "total_b": _r(sum(r["plan_b"] for r in rows)),
        "n_skus_changed": len(changed),
        "skus_only_in_a": sum(1 for r in rows if r["only_in"] == "a"),
        "skus_only_in_b": sum(1 for r in rows if r["only_in"] == "b"),
        "items": changed[:max(1, min(int(limit), MAX_ROWS_RETURNED))],
    }


# ── The plan against what sold ──────────────────────────────────────────────

def actuals_by_sku(series: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Loader output {series key: {date: units}} -> {sku: {date: units}}."""
    out: dict[str, dict[str, float]] = {}
    for key, s in (series or {}).items():
        sku = split_key(str(key))[0]
        bucket = out.setdefault(sku, {})
        for d, v in (s or {}).items():
            if v is None:
                continue
            fv = float(v)
            if math.isfinite(fv):
                bucket[str(d)[:10]] = bucket.get(str(d)[:10], 0.0) + fv
    return out


def passed_period_indexes(snapshot: dict, today: date) -> list[int]:
    """Periods whose whole window is behind `today`. A period still running is
    never graded: half a month of sales against a whole month of plan would read
    as a huge over-forecast that is not there."""
    return [i for i, end in enumerate(snapshot.get("ends") or [])
            if date.fromisoformat(end) <= today]


def compare_points(snapshot: dict, actuals: dict[str, dict[str, float]],
                   today: date) -> tuple[list[dict], dict]:
    """(points, counts). One point per (SKU, passed period) where the plan, the
    statistical forecast AND a real sale all exist. A missing actual is not a
    zero and is counted, not guessed; a SKU planned only from commitments has no
    statistical forecast to compare with and is counted apart."""
    periods = snapshot.get("periods") or []
    passed = passed_period_indexes(snapshot, today)
    points: list[dict] = []
    no_actual = no_forecast = 0
    for sku, line in (snapshot.get("skus") or {}).items():
        real = actuals.get(sku) or {}
        f, p = line.get("f") or [], line.get("p") or []
        for i in passed:
            if i >= len(f) or i >= len(p):
                continue
            label = periods[i]
            if f[i] is None:
                no_forecast += 1
                continue
            a = real.get(label)
            if a is None:
                no_actual += 1
                continue
            points.append({"sku": sku, "date": label, "base": float(f[i]),
                           "adjusted": float(p[i]), "actual": float(a)})
    return points, {"periods_total": len(periods), "periods_passed": len(passed),
                    "skipped_no_actual": no_actual, "skipped_no_forecast": no_forecast}


def _rename(fva: dict) -> dict:
    """ForecastingCore's FVA reading in this screen's words: `base` is the
    statistical forecast, `adjusted` the approved plan.

    One correction: when the model was exact there is no ratio, and ForecastingCore
    answers "neutral". If the plan then missed, calling it neutral would tell the
    company its consensus cost nothing when it did; it is "worsened"."""
    verdict = fva["verdict"]
    if (verdict == "neutral" and fva["improvement_pct"] is None
            and fva["adjusted_error"] > fva["base_error"] + _EPS):
        verdict = "worsened"
    return {
        "n_points": fva["n_points"],
        "actual_total": _r(fva["actual_total"]),
        "plan_error": _r(fva["adjusted_error"]), "model_error": _r(fva["base_error"]),
        "plan_wape": _r(fva["adjusted_wape"]), "model_wape": _r(fva["base_wape"]),
        "plan_bias": _r(fva["adjusted_bias"]), "model_bias": _r(fva["base_bias"]),
        "improvement_pct": _r(fva["improvement_pct"]),
        "better_points": fva["better_points"], "worse_points": fva["worse_points"],
        "verdict": verdict,
    }


def compare_with_actuals(snapshot: dict, actuals: dict[str, dict[str, float]],
                         today: date, limit: int = 100) -> dict:
    """Plan accuracy and bias, and the same for the statistical forecast, over
    identical points; `verdict` says whether the consensus beat the model.

    status: ok | periods_not_passed | no_actuals
    """
    from forecasting_core.evaluation.realized import (
        forecast_value_added, forecast_value_added_by,
    )

    points, counts = compare_points(snapshot, actuals, today)
    ends = snapshot.get("ends") or []
    base = {**counts, "first_period_end": ends[0] if ends else None,
            "periods_compared": len({p["date"] for p in points}),
            "aggregate": None, "by_sku": [], "n_skus": 0}
    if counts["periods_passed"] == 0:
        return {**base, "status": "periods_not_passed"}
    if not points:
        return {**base, "status": "no_actuals"}
    by_sku = [{"sku": r["sku"], **_rename(r)} for r in forecast_value_added_by(points, "sku")]
    # Worst plan first: the rows a planner should look at.
    by_sku.sort(key=lambda r: (r["plan_error"] is None, -(r["plan_error"] or 0.0), r["sku"]))
    return {**base, "status": "ok", "aggregate": _rename(forecast_value_added(points)),
            "n_skus": len(by_sku),
            "by_sku": by_sku[:max(1, min(int(limit), MAX_ROWS_RETURNED))]}
