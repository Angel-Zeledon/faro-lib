"""
Every model family scored on the same protocol.

The per-SKU ML models used to be graded one step at a time: the validation rows
handed the model yesterday's real demand, so it answered "what happens
tomorrow?" over and over. The statistical models and the global model were
graded on the whole h-step forecast, which is the job the product actually does
and a far harder one. Both numbers went into the same `cost` column, champion
selection ranked that column, and the easy question won — on every SKU, for
structural reasons that had nothing to do with which model was better.

These tests pin the fix: the ML models now also report an h-step score produced
by the production inference path, and the champion is picked on the comparable
number.
"""

import numpy as np
import pandas as pd
import pytest

from forecasting_core.config.config import FeaturesConfig, SessionConfig
from forecasting_core.evaluation.metrics import evaluate_all
from forecasting_core.features.engineer import FeatureEngineer
from forecasting_core.models.factory import ModelFactory
from forecasting_core.pipelines.pipeline import Pipeline
from forecasting_core.training.global_trainer import GlobalTrainer
from forecasting_core.training.trainer import Trainer

HORIZON = 14
FEATURES = FeaturesConfig(lags=[1, 7], diffs=[1], rolling=[7], calendar=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _series(n_days: int = 300, seed: int = 11) -> pd.DataFrame:
    """
    One SKU whose level wanders — a random walk with a weekly shape.

    The drift is the point. On a series that just oscillates around a fixed
    mean, day 14 is as predictable as day 1 and the two protocols measure the
    same thing, so nothing here would be able to fail. A wandering level is what
    real demand does and it is what makes a recursive fourteen-step forecast
    genuinely harder than fourteen one-step ones.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="D")
    level = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.03, n_days)))
    weekly = 1.0 + 0.35 * np.sin(2 * np.pi * np.arange(n_days) / 7.0)
    demand = np.clip(level * weekly * rng.normal(1.0, 0.05, n_days), 0.0, None)
    return pd.DataFrame({"date": dates, "sku": "SKU_A", "demand": demand})


def _train(df: pd.DataFrame, horizon: int = HORIZON, **kwargs) -> dict:
    df_ml = FeatureEngineer(
        FEATURES, dt_col="date", target="demand", group_cols=["sku"],
    ).transform(df)
    trainer = Trainer(
        train_ratio=0.8, walk_forward=True, wfv_splits=3,
        gap=horizon, horizon=horizon, features_cfg=FEATURES, **kwargs,
    )
    return trainer.train(
        df_ml, ModelFactory({"lightgbm": {"n_estimators": 80}}).build_ml(),
        group_cols=["sku"], target="demand", dt="date",
    )


@pytest.fixture(scope="module")
def ml_entry() -> dict:
    results = _train(_series())
    assert results, "the fixture must actually train something"
    return next(iter(results.values()))


def _pipeline() -> Pipeline:
    return Pipeline(SessionConfig.from_dict({
        "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
        "models": {"lightgbm": {}},
        "forecast": {"horizon": HORIZON},
    }))


# ---------------------------------------------------------------------------
# The bias itself
# ---------------------------------------------------------------------------

class TestTheOneStepScoreIsOptimistic:

    def test_the_honest_score_is_worse_than_the_fold_score(self, ml_entry):
        """
        The defect, measured rather than argued.

        If these two were interchangeable there would be nothing to fix. The
        fold metrics grade rows that carry their own true lag features; the
        horizon metrics grade a forecast that had to invent them. On a series
        whose level moves, the second must cost more.
        """
        fold_cost = ml_entry["cost"]
        honest_cost = ml_entry["horizon_metrics"]["all_horizons"]["cost"]
        assert honest_cost > fold_cost, (
            f"h-step cost {honest_cost:.3f} did not exceed the 1-step cost "
            f"{fold_cost:.3f} — the two protocols would be interchangeable"
        )

    def test_the_existing_columns_still_mean_what_they_meant(self, ml_entry):
        """
        `mae`/`rmse`/`wape` are read by the registry, the ensemble's weights and
        the metrics table. The honest score was ADDED next to them, not swapped
        in underneath them — if the h-step numbers had been written into those
        keys, every consumer's meaning would have changed silently.
        """
        honest = ml_entry["horizon_metrics"]["all_horizons"]
        for key in ("mae", "rmse", "wape"):
            assert np.isfinite(ml_entry[key]), f"{key} must still be reported"
            assert ml_entry[key] < honest[key], (
                f"{key} looks like the h-step figure, not the fold figure"
            )


class TestHorizonMetricShape:

    def test_shape_is_identical_to_the_global_trainers(self, ml_entry):
        """
        The two must be structurally interchangeable or nothing downstream can
        read them with one code path.
        """
        records = [{
            "actual": np.array([10.0, 12.0]),
            "pred": np.array([9.0, 15.0]),
            "horizons": np.array([1, 2]),
        }]
        _, reference, _ = GlobalTrainer._series_metrics(
            records, profile=None,
        )
        hm = ml_entry["horizon_metrics"]
        assert set(hm) == set(reference)
        assert set(hm["all_horizons"]) == set(reference["all_horizons"])
        sample = next(iter(hm["by_horizon"].values()))
        assert set(sample) == set(next(iter(reference["by_horizon"].values())))

    def test_one_entry_per_forecast_step(self, ml_entry):
        by_h = ml_entry["horizon_metrics"]["by_horizon"]
        assert list(by_h) == [str(i) for i in range(1, HORIZON + 1)]

    def test_the_horizon_metrics_are_real_numbers(self, ml_entry):
        all_h = ml_entry["horizon_metrics"]["all_horizons"]
        assert set(all_h) == set(evaluate_all([1.0], [1.0]))
        assert all(np.isfinite(v) for v in all_h.values())


class TestDegradation:

    def test_a_short_held_out_window_reports_fewer_steps(self):
        """
        A series that cannot fund the full horizon must say so in the data.

        Reporting a 14-step metric computed on 4 steps would be the worst
        outcome: the number is real, comparable-looking, and answers a different
        question than the one its name claims.
        """
        entry = next(iter(_train(_series(n_days=60), horizon=HORIZON).values()))
        by_h = entry["horizon_metrics"]["by_horizon"]
        # `n` is the number of feature rows the trainer actually saw; the
        # held-out window is everything past the train_ratio cutoff.
        held_out = entry["n"] - int(entry["n"] * 0.8)
        assert held_out < HORIZON, "fixture assumption: this series is short"
        assert len(by_h) == held_out
        assert list(by_h) == [str(i) for i in range(1, held_out + 1)]

    def test_no_horizon_means_no_horizon_metrics(self):
        """A caller that did not ask for the evaluation must not pay for it,
        and must not receive a fabricated one either."""
        results = _train(_series(n_days=200), horizon=0)
        entry = next(iter(results.values()))
        assert entry["horizon_metrics"] == {}

    def test_missing_features_config_skips_rather_than_crashes(self):
        """The evaluation rebuilds features the way inference does; without the
        feature spec it cannot, and must degrade instead of taking the whole
        SKU's training down."""
        df_ml = FeatureEngineer(
            FEATURES, dt_col="date", target="demand", group_cols=["sku"],
        ).transform(_series(n_days=200))
        results = Trainer(
            train_ratio=0.8, walk_forward=True, wfv_splits=3,
            horizon=HORIZON, features_cfg=None,
        ).train(
            df_ml, ModelFactory({"lightgbm": {"n_estimators": 40}}).build_ml(),
            group_cols=["sku"], target="demand", dt="date",
        )
        assert results, "training itself must still succeed"
        assert next(iter(results.values()))["horizon_metrics"] == {}


# ---------------------------------------------------------------------------
# The metrics frame
# ---------------------------------------------------------------------------

class TestFlattenedFrame:

    def test_every_family_gets_a_comparable_cost(self, ml_entry):
        pipeline = _pipeline()
        frame = pipeline._flatten(
            {"lightgbm_SKU_A": ml_entry},
            {"arima": {"SKU_A": {"mae": 9.0, "cost": 21.0}}},
            {"SKU_A": {"naive": evaluate_all([10.0, 11.0], [10.5, 10.5])}},
        )
        assert "cost_horizon" in frame.columns
        by_model = frame.set_index("model")["cost_horizon"]
        assert by_model["lightgbm"] == pytest.approx(
            ml_entry["horizon_metrics"]["all_horizons"]["cost"]
        )
        # A statistical model already forecasts its whole test window from the
        # end of train, so its cost carries across untouched.
        assert by_model["arima"] == pytest.approx(21.0)
        assert np.isfinite(by_model["naive"])

    def test_the_ml_row_keeps_its_one_step_cost_too(self, ml_entry):
        """Both numbers are useful and they are different numbers. Overwriting
        `cost` would have destroyed the evidence that the gap exists."""
        frame = _pipeline()._flatten({"lightgbm_SKU_A": ml_entry}, {}, {})
        row = frame.iloc[0]
        assert row["cost"] == pytest.approx(ml_entry["cost"])
        assert row["cost_horizon"] > row["cost"]

    def test_a_model_without_horizon_metrics_reports_no_comparable_cost(self):
        """Absent, not guessed at. A missing h-step score must not be filled in
        with the 1-step one — that is precisely the substitution this whole
        change exists to stop."""
        frame = _pipeline()._flatten(
            {"lightgbm_SKU_A": {"model": "lightgbm", "sku": "SKU_A", "cost": 3.0}},
            {}, {},
        )
        assert frame.iloc[0]["cost"] == pytest.approx(3.0)
        assert pd.isna(frame.iloc[0]["cost_horizon"])


# ---------------------------------------------------------------------------
# Champion selection — the whole point
# ---------------------------------------------------------------------------

class TestChampionSelectionUsesTheComparableScore:

    def test_the_ml_model_no_longer_wins_on_an_easier_question(self, ml_entry):
        """
        THE regression this task exists to prevent.

        The rival is deliberately placed between the ML model's two scores:
        worse than its 1-step fold cost, better than its honest h-step cost. Ask
        the old question and lightgbm wins; ask the same question of both and
        the rival does. Only one of those is a statement about which model
        forecasts this SKU better.
        """
        fold_cost = ml_entry["cost"]
        honest_cost = ml_entry["horizon_metrics"]["all_horizons"]["cost"]
        assert fold_cost < honest_cost, "fixture assumption"
        rival_cost = (fold_cost + honest_cost) / 2.0

        pipeline = _pipeline()
        frame = pipeline._flatten(
            {"lightgbm_SKU_A": ml_entry},
            {"arima": {"SKU_A": {"mae": 5.0, "cost": rival_cost}}},
            {},
        )

        # What the old ranking would have concluded, asserted so the test says
        # out loud which behaviour it is replacing.
        assert frame.loc[frame["cost"].idxmin(), "model"] == "lightgbm"

        champions = pipeline._select_champions(frame, lambda s: str(s).strip())
        assert champions["SKU_A"] == "arima", (
            "champion selection is still ranking a 1-step score against an "
            "h-step one"
        )

    def test_the_ml_model_still_wins_when_it_is_genuinely_better(self, ml_entry):
        """The fix must correct a bias, not install the opposite one."""
        honest_cost = ml_entry["horizon_metrics"]["all_horizons"]["cost"]
        pipeline = _pipeline()
        frame = pipeline._flatten(
            {"lightgbm_SKU_A": ml_entry},
            {"arima": {"SKU_A": {"mae": 5.0, "cost": honest_cost * 2.0}}},
            {},
        )
        champions = pipeline._select_champions(frame, lambda s: str(s).strip())
        assert champions["SKU_A"] == "lightgbm"

    def test_cost_horizon_is_preferred_over_cost(self):
        frame = pd.DataFrame([
            {"sku": "A", "model": "cheap_on_the_easy_question",
             "cost": 1.0, "cost_horizon": 9.0},
            {"sku": "A", "model": "cheap_on_the_real_one",
             "cost": 8.0, "cost_horizon": 2.0},
        ])
        champions = _pipeline()._select_champions(frame, lambda s: str(s).strip())
        assert champions["A"] == "cheap_on_the_real_one"

    def test_falls_back_to_cost_when_no_frame_carries_the_new_column(self):
        """Metrics frames persisted before this change have no `cost_horizon`;
        they must still select rather than returning nothing."""
        frame = pd.DataFrame([
            {"sku": "A", "model": "a", "cost": 5.0},
            {"sku": "A", "model": "b", "cost": 2.0},
        ])
        assert _pipeline()._select_champions(frame, str)["A"] == "b"

    def test_falls_back_to_mae_when_neither_cost_column_exists(self):
        frame = pd.DataFrame([
            {"sku": "A", "model": "a", "mae": 5.0},
            {"sku": "A", "model": "b", "mae": 2.0},
        ])
        assert _pipeline()._select_champions(frame, str)["A"] == "b"
