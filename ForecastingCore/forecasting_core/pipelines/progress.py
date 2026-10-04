"""
Progress accounting for a training run.

A run is a sequence of stages whose cost is very uneven: reading the file is
instant, fitting every SKU's models is most of the wall-clock. Reporting a fixed
percentage per stage ("40% when training starts, 85% when it ends") makes the
bar sit still for minutes and then jump, so progress here is *weighted by cost*
and fed by *units of work done* inside the expensive stages (one SKU group
trained, one SKU fitted by one statistical model, ...).

Nothing is faked: the percentage only moves when real work completes, and it is
monotonic (never goes backwards, even when a stage turns out to have no work
and is dropped from the plan, which re-normalises the remaining weights).

This module is pure Python (no pandas/numpy).
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, Iterator, Optional, Sequence, Tuple

# ── Stage weights (relative cost; only the ratios matter) ──────────────────────
#
# Stages owned by the engine pipeline (Pipeline.run). Weights reflect where the
# time actually goes on a typical dataset: the ML walk-forward fits (point model
# plus three quantile refits) and the per-SKU statistical models dominate.
ENGINE_STAGES: Tuple[Tuple[str, float], ...] = (
    ("pipeline_load", 2.0),
    ("validate", 3.0),
    ("quality", 3.0),
    ("assign_models", 1.0),
    ("features", 5.0),
    ("ml_training", 22.0),
    ("ml_quantiles", 16.0),
    ("stat_training", 22.0),
    ("ensemble", 1.0),
    ("future_forecast", 5.0),
    ("inventory", 3.0),
    ("registry", 1.0),
)

# Stages the worker runs before / after the engine pipeline.
JOB_PRE_STAGES: Tuple[Tuple[str, float], ...] = (
    ("init", 1.0),
    ("load", 3.0),
    ("gap_fill", 2.0),
    ("outliers", 2.0),
    ("sync", 2.0),
    ("inspect", 3.0),
    ("routing", 2.0),
)
JOB_POST_STAGES: Tuple[Tuple[str, float], ...] = (
    ("results", 2.0),
    ("saving", 2.0),
    ("forecast", 4.0),
    ("indexing", 3.0),
    ("artifacts", 1.0),
)
JOB_STAGES: Tuple[Tuple[str, float], ...] = JOB_PRE_STAGES + ENGINE_STAGES + JOB_POST_STAGES

# Relative cost of fitting ONE SKU with each statistical model. Used to weight
# units inside the "stat_training" stage so a Prophet/LSTM SKU advances the bar
# further than a Croston one.
STAT_MODEL_COST: Dict[str, float] = {
    "croston": 0.3,
    "ets": 1.0,
    "arima": 2.0,
    "sarimax": 3.0,
    "prophet": 6.0,
    "lstm": 10.0,
}


def stat_unit_cost(model_name: str) -> float:
    return STAT_MODEL_COST.get(model_name, 2.0)


class ProgressTracker:
    """Turns stage / unit-of-work events into a monotonic 0..100 percentage.

    ``emit`` receives ``{"pct", "stage", "message", "done", "total", "status"}``
    whenever the whole percentage, the stage or the message changes.
    """

    def __init__(
        self,
        stages: Sequence[Tuple[str, float]],
        emit: Optional[Callable[[dict], None]] = None,
        cap: int = 99,
    ):
        self._order = [s for s, _ in stages]
        self._weights: Dict[str, float] = dict(stages)
        self._done: set = set()
        self._current: Optional[str] = None
        self._fraction = 0.0
        self._emit = emit
        self._cap = cap
        self._last_pct = 0.0
        self._last_key: Optional[tuple] = None

    # ── queries ────────────────────────────────────────────────────────────
    @property
    def stages(self) -> list:
        return list(self._order)

    def weight(self, stage: str) -> float:
        return self._weights.get(stage, 0.0)

    @property
    def percent(self) -> float:
        total = sum(self._weights.values())
        if total <= 0:
            return self._last_pct
        done = sum(self._weights[s] for s in self._done if s in self._weights)
        if self._current in self._weights and self._current not in self._done:
            done += self._weights[self._current] * self._fraction
        raw = min(float(self._cap), 100.0 * done / total)
        # Monotonic by construction: dropping a stage or an out-of-order event
        # can lower the raw value, never the reported one.
        self._last_pct = max(self._last_pct, raw)
        return self._last_pct

    # ── events ─────────────────────────────────────────────────────────────
    def begin(self, stage: str, message: str = "", status: str = "") -> None:
        """Start ``stage``; any stage still open before it counts as finished."""
        if stage not in self._weights:
            return
        self._close_current()
        self._current = stage
        self._fraction = 0.0
        self._publish(stage, message, None, None, status)

    def update(
        self, stage: str, done: float, total: float, message: str = "", status: str = ""
    ) -> None:
        """Report ``done`` of ``total`` units finished inside ``stage``."""
        if stage not in self._weights:
            return
        if self._current != stage:
            self._close_current()
            self._current = stage
        frac = 0.0 if total <= 0 else max(0.0, min(1.0, done / total))
        # A stage's own fraction never moves back either.
        self._fraction = max(self._fraction, frac) if self._current == stage else frac
        self._publish(stage, message, done, total, status)

    def finish(self, stage: str, message: str = "", status: str = "") -> None:
        if stage not in self._weights:
            return
        self._done.add(stage)
        if self._current == stage:
            self._current = None
            self._fraction = 0.0
        self._publish(stage, message, None, None, status)

    def drop(self, stage: str) -> None:
        """The stage has no work in this run: remove its weight from the plan."""
        if stage in self._weights and stage not in self._done:
            if stage == self._current:
                self._current = None
                self._fraction = 0.0
            del self._weights[stage]
            self._order.remove(stage)

    def apply(self, event: dict) -> None:
        """Feed an engine event (see Pipeline.run) into this tracker."""
        stage = event.get("stage")
        if not stage:
            return
        msg = event.get("message") or ""
        status = event.get("status") or ""
        if event.get("skipped"):
            self.drop(stage)
        elif event.get("finished"):
            self.finish(stage, msg, status)
        elif event.get("total"):
            self.update(stage, event.get("done") or 0, event["total"], msg, status)
        else:
            self.begin(stage, msg, status)

    # ── internals ──────────────────────────────────────────────────────────
    def _close_current(self) -> None:
        if self._current is not None:
            self._done.add(self._current)
            self._current = None
            self._fraction = 0.0

    def _publish(self, stage, message, done, total, status) -> None:
        pct = int(self.percent)
        key = (pct, stage, message)
        if key == self._last_key:
            return
        self._last_key = key
        if self._emit:
            try:
                self._emit({
                    "pct": pct, "stage": stage, "message": message,
                    "done": done, "total": total, "status": status,
                })
            except Exception:
                pass  # a reporting hiccup must never fail a training run


def ticking(items: Iterable, on_unit: Optional[Callable[[], None]]) -> Iterator:
    """Yield ``items``, calling ``on_unit`` once each time the loop body for an
    item has finished (including bodies that ``continue`` early)."""
    if on_unit is None:
        yield from items
        return
    for item in items:
        yield item
        try:
            on_unit()
        except Exception:
            pass
