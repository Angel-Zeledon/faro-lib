"""Pipeline._select_champions uses the noise-aware rule (training/champion.py)."""
import pandas as pd

from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline
from forecasting_core.evaluation.champion import MIN_SERIES_FOR_RECORD


def _pipeline():
    return Pipeline(SessionConfig.from_dict({
        "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
        "models": {"lightgbm": {}},
    }))


def _norm(x):
    return str(x).strip()


def _row(sku, model, cost, kind="ml"):
    return {"sku": sku, "model": model, "type": kind, "cost_horizon": cost}


def _catalogue(n):
    """n series where ETS is steadily slightly better than LightGBM."""
    rows = []
    for i in range(n):
        rows += [_row(f"s{i}", "ets", 1.0), _row(f"s{i}", "lightgbm", 1.04),
                 _row(f"s{i}", "xgboost", 1.5)]
    return rows


def test_near_tie_is_resolved_by_cross_series_record():
    rows = _catalogue(9)
    rows += [_row("lucky", "ets", 1.03), _row("lucky", "lightgbm", 1.0), _row("lucky", "xgboost", 1.5)]
    p = _pipeline()
    champs = p._select_champions(pd.DataFrame(rows), _norm)
    assert champs["lucky"] == "ets"  # plain argmin would say lightgbm
    assert p._champion_by_sku["lucky"] == "ets"


def test_clear_winner_is_untouched():
    rows = _catalogue(9)
    rows += [_row("clear", "ets", 1.0), _row("clear", "lightgbm", 0.5), _row("clear", "xgboost", 1.5)]
    champs = _pipeline()._select_champions(pd.DataFrame(rows), _norm)
    assert champs["clear"] == "lightgbm"


def test_baseline_is_never_crowned_and_is_still_reported():
    rows = _catalogue(9)
    rows += [_row("b", "naive", 0.1, "baseline"), _row("b", "ets", 1.0), _row("b", "lightgbm", 1.02)]
    p = _pipeline()
    champs = p._select_champions(pd.DataFrame(rows), _norm)
    assert champs["b"] in {"ets", "lightgbm"}
    assert all(m != "naive" for m in champs.values())
    assert [e["sku"] for e in p._outperformed_by_baseline] == ["b"]


def test_never_picks_a_model_without_a_score_for_that_sku():
    rows = _catalogue(9)
    rows += [_row("only_lgbm", "lightgbm", 1.0)]  # ets has the better record but no score here
    champs = _pipeline()._select_champions(pd.DataFrame(rows), _norm)
    assert champs["only_lgbm"] == "lightgbm"


def test_few_series_keeps_the_plain_minimum():
    assert MIN_SERIES_FOR_RECORD > 3
    rows = [_row("a", "ets", 1.03), _row("a", "lightgbm", 1.0),
            _row("b", "ets", 1.0), _row("b", "lightgbm", 1.5),
            _row("c", "ets", 1.0), _row("c", "lightgbm", 1.5)]
    champs = _pipeline()._select_champions(pd.DataFrame(rows), _norm)
    assert champs["a"] == "lightgbm"
