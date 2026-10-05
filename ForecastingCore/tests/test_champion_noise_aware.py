"""Noise-aware champion choice (forecasting_core/evaluation/champion.py)."""
import pytest

from forecasting_core.evaluation.champion import (
    DEFAULT_TOLERANCE, MIN_SERIES_FOR_RECORD, pooled_record, select_champions,
)


def _catalogue(n=10, ets_cost=1.0, lgbm_cost=1.0):
    """n series where ETS is steadily as good as LightGBM across the catalogue."""
    return {f"s{i}": {"ets": ets_cost, "lightgbm": lgbm_cost, "xgboost": 1.3} for i in range(n)}


def test_clear_single_winner_still_wins():
    scores = _catalogue()
    # On s0 LightGBM is 40 % better than anything else: far outside the noise.
    scores["s0"] = {"ets": 1.0, "lightgbm": 0.6, "xgboost": 1.3}
    assert select_champions(scores)["s0"] == "lightgbm"


def test_near_tie_goes_to_the_model_with_the_better_record():
    scores = {f"s{i}": {"ets": 1.0, "lightgbm": 1.04, "xgboost": 1.3} for i in range(9)}
    # LightGBM edges ETS by 3 % on one window only: inside the tolerance.
    scores["lucky"] = {"ets": 1.03, "lightgbm": 1.0, "xgboost": 1.3}
    champs = select_champions(scores)
    assert champs["lucky"] == "ets"
    # The plain argmin would have crowned LightGBM there.
    assert min(scores["lucky"], key=scores["lucky"].get) == "lightgbm"


def test_series_without_a_tie_are_unchanged():
    scores = {f"s{i}": {"ets": 1.0, "lightgbm": 2.0} for i in range(8)}
    assert set(select_champions(scores).values()) == {"ets"}


def test_too_few_series_falls_back_to_plain_minimum():
    assert MIN_SERIES_FOR_RECORD > 2
    scores = {"a": {"ets": 1.03, "lightgbm": 1.0}, "b": {"ets": 1.0, "lightgbm": 1.5}}
    assert pooled_record(scores) == {}
    assert select_champions(scores)["a"] == "lightgbm"


def test_never_returns_a_model_that_was_not_scored():
    scores = _catalogue()
    for sku, champ in select_champions(scores).items():
        assert champ in scores[sku]
    # A model absent from a series (not selected / failed there) cannot be picked.
    scores["s0"] = {"lightgbm": 1.0, "xgboost": 1.01}
    assert select_champions(scores)["s0"] in {"lightgbm", "xgboost"}


def test_non_finite_scores_are_ignored():
    scores = _catalogue()
    scores["s1"]["ets"] = float("nan")
    scores["s1"]["lightgbm"] = 1.0
    assert select_champions(scores)["s1"] != "ets"


def test_tolerance_boundary():
    base = {f"s{i}": {"ets": 1.0, "lightgbm": 1.2} for i in range(8)}
    base["edge"] = {"ets": 1.0 + DEFAULT_TOLERANCE + 0.01, "lightgbm": 1.0}
    assert select_champions(base)["edge"] == "lightgbm"  # just outside: not a tie
