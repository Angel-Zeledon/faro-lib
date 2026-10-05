"""Aggregation of per-series records into segment tables (JSON + markdown)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, List

from .metrics import SEGMENTS, STOCKOUT_MULTIPLIER, fva, nanmean

REFERENCE = "naive"


def _pooled_ratio(num: float, den: float) -> float:
    return num / den if den and den > 0 else float("nan")


def _agg_method(recs: List[dict], method: str) -> dict:
    """Aggregate one method over the records that contain it."""
    ms = [(r["methods"][method], r["methods"].get(REFERENCE), r["methods"].get("seasonal_naive"))
          for r in recs if method in r["methods"]]
    if not ms:
        return {}
    err = sum(m["abs_err_sum"] for m, _, _ in ms)
    act = sum(m["actual_sum"] for m, _, _ in ms)
    signed = sum(m["signed_err_sum"] for m, _, _ in ms)
    out = {
        "n": len(ms),
        "mase": nanmean(m["mase"] for m, _, _ in ms),
        "wape": _pooled_ratio(err, act),
        "smape": nanmean(m["smape"] for m, _, _ in ms),
        "bias": _pooled_ratio(signed, act) if act > 0 else float("nan"),
    }
    # Records written before cost_sum existed simply lack it; no number is
    # invented for them.
    if all("cost_sum" in m for m, _, _ in ms):
        out["cost_ratio"] = _pooled_ratio(sum(m["cost_sum"] for m, _, _ in ms), act)
    # The same two errors on the HORIZON TOTAL per series-origin — the number
    # an order quantity is built from. Per-period cost on a zero-inflated
    # series is minimised by forecasting zero whenever P(0) > 3/4 (the
    # optimum of a 3:1 linear loss is the 75th percentile), so per-period
    # metrics alone cannot rank intermittent models for a purchase decision.
    totals = [m["signed_err_sum"] for m, _, _ in ms]
    out["total_wape"] = _pooled_ratio(sum(abs(s) for s in totals), act)
    out["total_cost_ratio"] = _pooled_ratio(
        sum(max(s, 0.0) + STOCKOUT_MULTIPLIER * max(-s, 0.0) for s in totals), act)
    if method != REFERENCE:
        ref_err = sum(n["abs_err_sum"] for _, n, _ in ms if n)
        out["fva_vs_naive"] = fva(err, ref_err)
        sn_err = sum(s["abs_err_sum"] for _, _, s in ms if s)
        out["fva_vs_seasonal_naive"] = fva(err, sn_err)
        wins = losses = ties = 0
        for m, n, _ in ms:
            if not n:
                continue
            if m["mae"] < n["mae"] - 1e-12:
                wins += 1
            elif m["mae"] > n["mae"] + 1e-12:
                losses += 1
            else:
                ties += 1
        out.update(wins_vs_naive=wins, losses_vs_naive=losses, ties_vs_naive=ties)
    if any("pinball_scaled" in m for m, _, _ in ms):
        out["pinball_scaled"] = nanmean(m.get("pinball_scaled", float("nan")) for m, _, _ in ms)
    if any("coverage80" in m for m, _, _ in ms):
        out["coverage80"] = nanmean(m.get("coverage80", float("nan")) for m, _, _ in ms)
    return out


def aggregate(records: List[dict]) -> dict:
    """{segment|'all': {method: metrics}} on the *common* set, i.e. records where
    the engine produced a forecast (so every method is scored on the same
    series-origins)."""
    common = [r for r in records if "engine" in r["methods"]]
    groups: Dict[str, List[dict]] = {"all": common}
    for seg in SEGMENTS:
        groups[seg] = [r for r in common if r["segment"] == seg]
    out = {}
    for seg, recs in groups.items():
        if not recs:
            continue
        methods = sorted({m for r in recs for m in r["methods"]})
        out[seg] = {"n_series_origins": len(recs),
                    "methods": {m: _agg_method(recs, m) for m in methods}}
    return out


def champion_mix(records: List[dict]) -> dict:
    by_seg = defaultdict(Counter)
    for r in records:
        if r.get("champion"):
            by_seg[r["segment"]][r["champion"]] += 1
            by_seg["all"][r["champion"]] += 1
    return {k: dict(v) for k, v in by_seg.items()}


def _f(x, nd=3, pct=False) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    return f"{x * 100:.1f}%" if pct else f"{x:.{nd}f}"


def to_markdown(dataset_label: str, agg: dict, mix: dict, meta: dict) -> str:
    lines = [f"### {dataset_label}", ""]
    lines.append(f"series={meta['n_series']}, origins={meta['n_origins']}, horizon={meta['horizon']}, "
                 f"scored series-origins={meta['n_scored']}, engine produced no forecast for "
                 f"{meta['n_engine_failed']} series-origins.")
    lines.append("")
    for seg in ("all",) + SEGMENTS:
        if seg not in agg:
            continue
        a = agg[seg]
        lines += [f"**Segment `{seg}`** (n={a['n_series_origins']} series-origins)", "",
                  "| method | n | MASE | WAPE | sMAPE | bias | asym. cost / demand | total WAPE | total asym. cost / demand | FVA vs naive | FVA vs seasonal naive | wins/losses vs naive | pinball (scaled) | cov80 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for m, v in a["methods"].items():
            wl = (f"{v['wins_vs_naive']}/{v['losses_vs_naive']}" if "wins_vs_naive" in v else "-")
            lines.append(
                f"| {m} | {v.get('n', '-')} | {_f(v.get('mase'))} | {_f(v.get('wape'), pct=True)} | {_f(v.get('smape'), 1)} "
                f"| {_f(v.get('bias'), pct=True)} | {_f(v.get('cost_ratio'))} "
                f"| {_f(v.get('total_wape'), pct=True)} | {_f(v.get('total_cost_ratio'))} "
                f"| {_f(v.get('fva_vs_naive'), pct=True)} "
                f"| {_f(v.get('fva_vs_seasonal_naive'), pct=True)} | {wl} "
                f"| {_f(v.get('pinball_scaled'))} | {_f(v.get('coverage80'), pct=True)} |")
        lines.append("")
    if mix.get("all"):
        lines.append("Champion model chosen by the engine: " +
                     ", ".join(f"{k}={v}" for k, v in sorted(mix['all'].items(), key=lambda kv: -kv[1])))
        lines.append("")
    return "\n".join(lines)
