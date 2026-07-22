"""_generate_forecast_series must keep the store dimension when present."""

import pandas as pd

from backend.workers.runner import _generate_forecast_series
from backend.inventory.series import SERIES_SEPARATOR


class _StubEngine:
    def __init__(self, rows, df):
        self._rows = rows
        self._df = df

    def get_forecast(self):
        return {"rows": self._rows, "n_skus": 2, "horizon": 1}


def _row(sku_key, model="lightgbm", value=5.0):
    return {"sku": sku_key, "model": model, "date": "2026-08-01",
            "forecast": value, "p90_lo": 1.0, "p90_hi": 9.0, "step": 1}


def test_two_group_keys_produce_store_keys():
    key_a = f"A{SERIES_SEPARATOR}Norte"
    key_b = f"A{SERIES_SEPARATOR}Sur"
    df = pd.DataFrame({
        "sku":   ["A", "A"],
        "store": ["Norte", "Sur"],
        "date":  ["2026-07-01", "2026-07-01"],
        "sales": [3.0, 4.0],
    })
    config = {"columns": {"target": "sales", "date": "date",
                          "group_keys": ["sku", "store"]}}
    out = _generate_forecast_series(_StubEngine([_row(key_a), _row(key_b)], df), config)
    assert set(out) == {key_a, key_b}
    # Historical series split per store, not shared
    assert [p["value"] for p in out[key_a]["lightgbm"]["historical"]] == [3.0]
    assert [p["value"] for p in out[key_b]["lightgbm"]["historical"]] == [4.0]


def test_single_group_key_unchanged():
    df = pd.DataFrame({
        "sku": ["A"], "date": ["2026-07-01"], "sales": [3.0],
    })
    config = {"columns": {"target": "sales", "date": "date", "group_keys": ["sku"]}}
    out = _generate_forecast_series(_StubEngine([_row("A")], df), config)
    assert set(out) == {"A"}
    assert [p["value"] for p in out["A"]["lightgbm"]["historical"]] == [3.0]
