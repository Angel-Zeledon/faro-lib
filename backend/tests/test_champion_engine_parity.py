"""The backend and the engine must crown the same champion.

The forecast comes from the engine's choice and the purchase order, the accuracy
figure and the chart come from `best_model_by_sku`. They used to be two copies of
"lowest cost wins"; the rule is now noise-aware and lives in ONE function, and
this test fails if either side stops calling it or a caller starts feeding it one
SKU at a time.
"""
import pandas as pd

from backend.inventory.service import best_model_by_sku
from forecasting_core.evaluation.champion import select_champions
from forecasting_core.pipelines.pipeline import Pipeline


def _rows():
    """9 series where ETS is steadily a hair better than LightGBM, plus one where
    LightGBM edges ETS by 3% on a single window (inside the tolerance)."""
    rows = []
    for i in range(9):
        rows += [
            {"sku": f"s{i}", "model": "ets", "cost_horizon": 1.0, "type": "ml"},
            {"sku": f"s{i}", "model": "lightgbm", "cost_horizon": 1.04, "type": "ml"},
            {"sku": f"s{i}", "model": "naive", "cost_horizon": 0.5, "type": "baseline"},
        ]
    rows += [
        {"sku": "lucky", "model": "ets", "cost_horizon": 1.03, "type": "ml"},
        {"sku": "lucky", "model": "lightgbm", "cost_horizon": 1.0, "type": "ml"},
    ]
    return rows


def test_backend_settles_a_near_tie_by_the_catalogue_record():
    champs = best_model_by_sku(_rows())
    assert champs["lucky"] == "ets"          # the plain minimum would say lightgbm
    assert "naive" not in champs.values()    # a baseline is never crowned


def test_backend_and_engine_choose_the_same_model_for_every_sku():
    rows = _rows()
    scores = {}
    for r in rows:
        if r["type"] != "baseline":
            scores.setdefault(r["sku"], {})[r["model"]] = r["cost_horizon"]
    assert best_model_by_sku(rows) == select_champions(scores)

    pipe = Pipeline.__new__(Pipeline)  # only the selection method is exercised
    engine = pipe._select_champions(pd.DataFrame(rows), lambda s: str(s))
    assert engine == best_model_by_sku(rows)


def test_one_skus_rows_alone_cannot_see_the_catalogue_record():
    """Why the per-SKU callers pass the whole table: on its own the 'lucky' SKU
    falls back to the plain minimum and disagrees with the catalogue-wide pick."""
    rows = _rows()
    lucky_only = [r for r in rows if r["sku"] == "lucky"]
    assert best_model_by_sku(lucky_only)["lucky"] == "lightgbm"
    assert best_model_by_sku(rows)["lucky"] == "ets"
