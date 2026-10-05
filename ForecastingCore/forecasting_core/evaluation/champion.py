"""
Noise-aware champion choice.

The plain rule ("lowest validation cost wins") treats a 1 % lead on one
held-out window as a real difference. With a handful of windows per series it
is not: the winner is partly the model that got lucky on that window, and the
benchmark (docs/benchmark-results.md, finding #4) shows the per-series winner
doing worse out of sample than the average of its own rivals.

The rule here is deliberately small and explainable:

1. A model is *tied* with the leader when its cost is within `tolerance`
   (relative) of the series' lowest cost. A lead that small is inside the
   window-to-window noise, so it says nothing about which model is better.
2. Among the tied models, pick the one with the best record ACROSS ALL SERIES
   of the run (median of its cost relative to each series' leader). That is a
   far larger sample than one series' single window.
3. A clear winner (nobody else within the tolerance) is untouched, and with too
   few series to estimate a record the rule falls back to the plain minimum.

It only ever chooses among the models it is handed, so a model the user did
not select can never appear.

ONE copy, two callers: the engine (`Pipeline._select_champions`) and the backend
(`inventory.service.best_model_by_sku`, which every purchase order, accuracy
figure and chart reads) both call `select_champions`. Two copies of this rule
would put a different model behind the forecast and behind the order. It is
standard library only so the backend can import it without the engine's ML
stack, and the record is only meaningful over the WHOLE session's rows, so
callers must pass every series' scores, never one series on its own.
"""
from __future__ import annotations

import math
import statistics
from typing import Dict, Mapping

# Relative lead below which two models are considered tied.
DEFAULT_TOLERANCE = 0.10
# Series needed before a cross-series record means anything.
MIN_SERIES_FOR_RECORD = 5


def pooled_record(scores: Mapping[str, Mapping[str, float]]) -> Dict[str, float]:
    """{model: median(cost / series leader's cost)} over series with >= 2 models."""
    ratios: Dict[str, list] = {}
    for per_model in scores.values():
        usable = {m: float(v) for m, v in per_model.items() if v is not None and math.isfinite(float(v))}
        if len(usable) < 2:
            continue
        best = min(usable.values())
        for m, v in usable.items():
            if best > 0:
                ratios.setdefault(m, []).append(v / best)
            else:
                ratios.setdefault(m, []).append(1.0 if v <= 0 else float("inf"))
    return {m: float(statistics.median(r)) for m, r in ratios.items() if len(r) >= MIN_SERIES_FOR_RECORD}


def select_champions(
    scores: Mapping[str, Mapping[str, float]],
    tolerance: float = DEFAULT_TOLERANCE,
) -> Dict[str, str]:
    """{sku: champion} from {sku: {model: cost}} (lower is better, baselines excluded)."""
    record = pooled_record(scores)
    out: Dict[str, str] = {}
    for sku, per_model in scores.items():
        usable = {m: float(v) for m, v in per_model.items() if v is not None and math.isfinite(float(v))}
        if not usable:
            continue
        leader = min(usable, key=usable.get)
        best = usable[leader]
        tied = [m for m, v in usable.items() if v <= best * (1.0 + tolerance) + 1e-12]
        if len(tied) > 1 and all(m in record for m in tied):
            # ties on the record fall back to the lower own cost
            leader = min(tied, key=lambda m: (record[m], usable[m]))
        out[sku] = leader
    return out
