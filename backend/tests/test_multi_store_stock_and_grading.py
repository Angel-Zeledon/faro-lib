"""Multi-store files: the SKU's stock and the forecast-vs-actual grading.

Two more places where a file with several stores per SKU produced a wrong
number and said nothing:

  * the stock sync took the latest ROW per SKU — one store's shelf — so the
    purchase covered the whole network's demand against one store's stock;
  * grading keyed actuals `sku│store` while the forecast (stores summed before
    training) is keyed by SKU, so not one point ever lined up.

Pure: no database. The DB side is `test_multi_store_stock_and_grading_db.py`.
"""
import csv

import pandas as pd
import pytest

from backend.dataframes.actuals import load_actual_series
from backend.dataframes.stock import last_row_per_group, stock_summed_over_stores
from backend.forecast_check.service import graded_group_cols
from backend.workers.runner import STOCK_SUMMED_CODE, _note_stock_summed, _store_col


def _stock_frame():
    # Norte counts stock on day 1 and day 3; Sur only on day 2; Centro never.
    return pd.DataFrame({
        "producto": ["A"] * 6 + ["B"] * 2,
        "tienda":   ["Norte", "Sur", "Norte", "Centro", "Sur", "Centro",
                     "Norte", "Norte"],
        "fecha":    ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-03",
                     "2026-01-03", "2026-01-01",
                     "2026-01-01", "2026-01-02"],
        "current_stock": [50.0, 7.0, 40.0, None, None, None, 9.0, 8.0],
    })


class TestStockSummedOverStores:
    def test_each_stores_latest_reading_is_summed(self):
        out = stock_summed_over_stores(_stock_frame(), "producto", "tienda",
                                       "fecha", "current_stock")
        # Norte's latest is 40 (not 50, not 50+40), Sur's is 7, Centro has none.
        assert out["by_sku"] == {"A": 47.0}
        assert out["n_skus"] == 1
        assert out["n_stores"] == 3
        assert out["missing_pairs"] == 1           # (A, Centro)
        assert out["n_skus_with_missing"] == 1

    def test_the_old_single_row_reading_was_one_store(self):
        # What the sync used before: the latest row of A is a store with no
        # reading, so A's stock came from whichever row sorted last.
        rows = dict(last_row_per_group(_stock_frame(), "producto", "fecha",
                                       ["current_stock"]))
        assert rows["A"].get("current_stock") != 47.0

    def test_single_store_skus_are_left_to_the_latest_row(self):
        out = stock_summed_over_stores(_stock_frame(), "producto", "tienda",
                                       "fecha", "current_stock")
        assert "B" not in out["by_sku"]

    def test_no_store_column_or_one_store_everywhere_does_nothing(self):
        df = _stock_frame()
        assert stock_summed_over_stores(df, "producto", None, "fecha",
                                        "current_stock")["by_sku"] == {}
        one = df[df["tienda"] == "Norte"]
        assert stock_summed_over_stores(one, "producto", "tienda", "fecha",
                                        "current_stock")["by_sku"] == {}

    def test_a_negative_latest_reading_is_not_replaced_by_an_older_one(self):
        df = pd.DataFrame({
            "producto": ["A", "A", "A"], "tienda": ["Norte", "Norte", "Sur"],
            "fecha": ["2026-01-01", "2026-01-02", "2026-01-02"],
            "current_stock": [30.0, -5.0, 4.0],
        })
        out = stock_summed_over_stores(df, "producto", "tienda", "fecha", "current_stock")
        assert out["by_sku"] == {"A": 4.0}
        assert out["missing_pairs"] == 1


class TestStockFinding:
    def test_the_run_says_how_many_store_readings_were_missing(self):
        notes: list = []
        _note_stock_summed({"n_skus": 3, "n_stores": 2, "missing_pairs": 4,
                            "n_skus_with_missing": 2}, notes)
        assert [n["error_id"] for n in notes] == [STOCK_SUMMED_CODE]
        assert notes[0]["severity"] == "warning"
        assert notes[0]["context"]["missing_pairs"] == 4

    def test_no_gaps_is_informational_and_nothing_summed_is_silent(self):
        notes: list = []
        _note_stock_summed({"n_skus": 3, "n_stores": 2, "missing_pairs": 0,
                            "n_skus_with_missing": 0}, notes)
        assert notes[0]["severity"] == "info"
        quiet: list = []
        _note_stock_summed({}, quiet)
        _note_stock_summed(None, quiet)
        assert quiet == []

    def test_store_col_is_the_second_series_key(self):
        assert _store_col({"group_keys": ["producto", "tienda"]}) == "tienda"
        assert _store_col({"group_keys": ["producto"]}) is None


class TestGradingKeys:
    def test_a_summed_run_is_graded_on_the_sku(self):
        assert graded_group_cols(["producto", "tienda"],
                                 {"store_rollup": {"applied": True}}) == ["producto"]

    def test_everything_else_keeps_its_keys(self):
        assert graded_group_cols(["producto", "tienda"], {}) == ["producto", "tienda"]
        assert graded_group_cols(["producto", "tienda"], None) == ["producto", "tienda"]
        assert graded_group_cols(["producto"], {"store_rollup": {"applied": True}}) == ["producto"]
        assert graded_group_cols([], None) == []

    def test_actuals_line_up_with_a_per_sku_forecast(self, tmp_path):
        from forecasting_core.evaluation.realized import compare_forecast_to_actuals

        path = tmp_path / "later.csv"
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["fecha", "producto", "tienda", "ventas"])
            for d in ("2026-05-01", "2026-05-02"):
                w.writerow([d, "A", "Norte", 100])
                w.writerow([d, "A", "Sur", 10])
        forecast = {"A": {"2026-05-01": 110.0, "2026-05-02": 110.0}}

        before = load_actual_series(str(path), "fecha", "ventas", ["producto", "tienda"])
        assert "A" not in before["series"]         # keyed A│Norte / A│Sur: nothing matched

        keys = graded_group_cols(["producto", "tienda"], {"store_rollup": {"applied": True}})
        after = load_actual_series(str(path), "fecha", "ventas", keys)
        assert after["series"]["A"] == {"2026-05-01": 110.0, "2026-05-02": 110.0}
        compared = compare_forecast_to_actuals(forecast, after["series"])
        assert compared["aggregate"]["n_points"] == 2
        assert compared["aggregate"]["wape"] == pytest.approx(0.0)
