"""Progress accounting: stage weights, monotonicity, and real pipeline events."""

import random

import pytest

from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline
from forecasting_core.pipelines.progress import (
    ENGINE_STAGES,
    JOB_PRE_STAGES,
    JOB_POST_STAGES,
    JOB_STAGES,
    STAT_MODEL_COST,
    ProgressTracker,
    stat_unit_cost,
    ticking,
)


def _collect(stages=JOB_STAGES):
    events = []
    return ProgressTracker(stages, events.append), events


class TestStageWeights:

    def test_job_plan_contains_engine_stage_exactly_once_in_order(self):
        keys = [k for k, _ in JOB_STAGES]
        assert len(keys) == len(set(keys))
        engine_keys = [k for k, _ in ENGINE_STAGES]
        start = keys.index(engine_keys[0])
        assert keys[start:start + len(engine_keys)] == engine_keys
        assert keys[:start] == [k for k, _ in JOB_PRE_STAGES]
        assert keys[start + len(engine_keys):] == [k for k, _ in JOB_POST_STAGES]

    def test_all_weights_positive(self):
        assert all(w > 0 for _, w in JOB_STAGES)

    def test_training_dominates_the_plan(self):
        """The fits are where the time goes: they must carry most of the bar,
        and the old 'training' plateau (40 -> 85) must be split into pieces."""
        w = dict(JOB_STAGES)
        total = sum(w.values())
        fit = w["ml_training"] + w["global_model"] + w["stat_training"]
        assert 0.4 < fit / total < 0.7
        # No single stage may own more than a quarter of the bar, otherwise it
        # would show as a long plateau followed by a jump.
        assert max(w.values()) / total < 0.25

    def test_stat_model_costs_are_ordered_by_real_expense(self):
        assert STAT_MODEL_COST["croston"] < STAT_MODEL_COST["ets"] < STAT_MODEL_COST["arima"]
        assert STAT_MODEL_COST["arima"] < STAT_MODEL_COST["prophet"] < STAT_MODEL_COST["lstm"]
        assert stat_unit_cost("never-heard-of-it") > 0

    def test_percent_after_stage_matches_its_weight_share(self):
        tracker, _ = _collect()
        total = sum(w for _, w in JOB_STAGES)
        tracker.begin("init")
        tracker.finish("init")
        expected = 100 * dict(JOB_STAGES)["init"] / total
        assert tracker.percent == pytest.approx(expected)


class TestMonotonic:

    def test_units_inside_a_stage_move_the_bar_smoothly(self):
        tracker, events = _collect()
        for k, _ in JOB_STAGES:
            if k == "ml_training":
                break
            tracker.begin(k)
        before = tracker.percent
        tracker.begin("ml_training")
        for i in range(1, 101):
            tracker.update("ml_training", i, 100, "Training ML models")
        assert [e["pct"] for e in events] == sorted(e["pct"] for e in events)
        pcts = [e["pct"] for e in events if e["stage"] == "ml_training"]
        # No jump larger than 2 points while 100 units of the biggest stage land.
        steps = [b - a for a, b in zip(pcts, pcts[1:])]
        assert max(steps) <= 2
        ml_share = 100 * dict(JOB_STAGES)["ml_training"] / sum(w for _, w in JOB_STAGES)
        # The whole stage landed (plus the stage that was still open when it began).
        assert tracker.percent - before >= ml_share - 0.01

    def test_never_decreases_under_random_events(self):
        rng = random.Random(7)
        keys = [k for k, _ in JOB_STAGES]
        for _ in range(50):
            tracker, events = _collect()
            for k in keys:
                action = rng.random()
                if action < 0.15:
                    tracker.drop(k)
                    continue
                tracker.begin(k)
                total = rng.randint(1, 30)
                for d in sorted(rng.sample(range(0, total + 5), min(total, 8))):
                    tracker.update(k, d, total)       # may even exceed total
                if rng.random() < 0.5:
                    tracker.finish(k)
            pcts = [e["pct"] for e in events]
            assert pcts == sorted(pcts), pcts

    def test_dropping_a_stage_never_lowers_the_bar(self):
        tracker, _ = _collect()
        tracker.begin("init")
        tracker.begin("load")
        tracker.update("load", 1, 2)
        before = tracker.percent
        tracker.drop("gap_fill")
        tracker.drop("outliers")
        assert tracker.percent >= before

    def test_out_of_order_unit_report_cannot_move_a_stage_back(self):
        tracker, _ = _collect()
        tracker.begin("ml_training")
        tracker.update("ml_training", 8, 10)
        high = tracker.percent
        tracker.update("ml_training", 3, 10)   # a slower thread reporting late
        assert tracker.percent == high

    def test_never_reaches_100_until_the_job_says_so(self):
        tracker, _ = _collect()
        for k, _w in JOB_STAGES:
            tracker.begin(k)
            tracker.finish(k)
        assert tracker.percent <= 99

    def test_dropping_every_optional_stage_still_ends_near_the_top(self):
        tracker, _ = _collect()
        for k in ("gap_fill", "outliers", "global_model", "stat_training"):
            tracker.drop(k)
        for k in tracker.stages:
            tracker.begin(k)
            tracker.finish(k)
        assert tracker.percent == 99

    def test_emits_only_on_visible_change(self):
        tracker, events = _collect()
        tracker.begin("ml_training", "Training ML models")
        n = len(events)
        # Same whole percent, same stage, same message: nothing to say.
        tracker.update("ml_training", 0, 1000, "Training ML models")
        assert len(events) == n

    def test_a_raising_emit_never_breaks_the_run(self):
        def boom(_):
            raise RuntimeError("socket gone")
        tracker = ProgressTracker(JOB_STAGES, boom)
        tracker.begin("init")
        tracker.finish("init")
        assert tracker.percent > 0


class TestApplyEngineEvents:

    def test_apply_routes_begin_update_finish_skip(self):
        tracker, events = _collect()
        tracker.apply({"stage": "ml_training", "message": "m"})
        tracker.apply({"stage": "ml_training", "done": 5, "total": 10, "message": "m"})
        mid = tracker.percent
        tracker.apply({"stage": "ml_training", "finished": True})
        assert tracker.percent > mid
        tracker.apply({"stage": "stat_training", "skipped": True})
        assert "stat_training" not in tracker.stages
        tracker.apply({"stage": "done", "pct": 100})   # unknown stage: ignored
        assert tracker.percent <= 99
        assert events


class TestTicking:

    def test_ticks_once_per_item_even_when_the_body_continues_early(self):
        ticks = []
        seen = []
        for item in ticking([1, 2, 3], lambda: ticks.append(1)):
            if item == 2:
                continue
            seen.append(item)
        assert seen == [1, 3]
        assert len(ticks) == 3

    def test_without_callback_is_a_plain_iteration(self):
        assert list(ticking([1, 2], None)) == [1, 2]


class TestRealPipelineEvents:
    """The engine itself must report fine-grained, monotonic progress."""

    def _config(self, tmp_path, models):
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(0)
        rows = []
        for sku in ("A", "B", "C", "D"):
            for i, d in enumerate(pd.date_range("2021-01-01", periods=90, freq="D")):
                rows.append({"date": d, "sku": sku, "sales": float(max(1, rng.normal(50, 8)))})
        csv = str(tmp_path / "data.csv")
        pd.DataFrame(rows).to_csv(csv, index=False)
        return SessionConfig.from_dict({
            "columns": {"target": "sales", "date": "date", "group": "sku"},
            "data": {"path": csv},
            "features": {"lags": [1, 7], "rolling": [7], "diffs": [1],
                         "calendar": False, "ewm_spans": []},
            "models": models,
            "training": {"train_ratio": 0.8, "walk_forward": False, "wfv_splits": 3,
                         "min_history": 20, "seasonal_period": 7},
            "forecast": {"horizon": 7},
            "business": {"service_level": 0.95, "lead_time_days": 7,
                         "holding_cost_pct": 0.20, "stockout_cost_multiplier": 3.0},
        })

    def test_pipeline_progress_is_monotonic_and_per_sku(self, tmp_path):
        cfg = self._config(tmp_path, {"lightgbm": {"n_estimators": 10}, "ets": {}})
        events = []
        Pipeline(cfg).run(on_progress=events.append)

        pcts = [e["pct"] for e in events]
        assert pcts == sorted(pcts), pcts
        assert pcts[-1] == 100 and events[-1]["status"] == "done"
        # Every pct before the final one stays under 100.
        assert max(pcts[:-1]) < 100

        # The ML stage reported one unit per SKU group (4 SKUs), not one jump.
        ml_units = [e for e in events if e["stage"] == "ml_training" and e["total"]]
        assert [e["done"] for e in ml_units] == [1, 2, 3, 4]
        assert {e["total"] for e in ml_units} == {4}

        # Moving through the old 40 -> 85 plateau now takes several distinct values.
        distinct = {p for p in pcts if 40 <= p <= 85}
        assert len(distinct) >= 4

    def test_stages_without_work_are_dropped_not_skipped_over(self, tmp_path):
        cfg = self._config(tmp_path, {"lightgbm": {"n_estimators": 10}})
        events = []
        Pipeline(cfg).run(on_progress=events.append)
        stat_events = [e for e in events if e["stage"] == "stat_training"]
        assert any(e["skipped"] for e in stat_events)
