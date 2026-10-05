"""Summing a multi-store sales history to one series per SKU.

The engine forecasts one series per SKU. Fed several stores per SKU it returned
ONE store's number as the SKU forecast (measured 1.0/day for a SKU selling
16/day), so the runner now sums the stores first. These tests pin the sum.
"""
import numpy as np
import pandas as pd
import pytest

from forecasting_core.data.store_rollup import (
    STORE_ROLLUP_CODE,
    rollup_stores,
    rollup_stores_canonical,
    skus_with_several_stores,
)


def _frame(rows):
    return pd.DataFrame(rows, columns=["sku", "store", "date", "qty"])


def _two_skus_three_stores(days=4):
    rows = []
    for sku, mult in (("A", 1.0), ("B", 2.0)):
        for store, lvl in (("N", 10.0), ("C", 5.0), ("S", 1.0)):
            for d in pd.date_range("2026-01-01", periods=days, freq="D"):
                rows.append((sku, store, d, lvl * mult))
    return _frame(rows)


class TestSum:
    def test_demand_is_summed_per_sku_and_date(self):
        df = _two_skus_three_stores()
        out, info = rollup_stores(df, date_col="date", sku_col="sku",
                                  store_cols=["store"], sum_cols=["qty"])
        assert info.applied
        assert len(out) == 8                       # 2 SKUs x 4 days
        assert set(out["qty"][out["sku"] == "A"]) == {16.0}
        assert set(out["qty"][out["sku"] == "B"]) == {32.0}
        assert float(out["qty"].sum()) == float(df["qty"].sum())
        # No store dimension survives: nothing may pretend it is per store.
        assert "store" not in out.columns
        assert info.n_stores == 3 and info.n_skus == 2 and info.n_skus_multi_store == 2
        assert info.rows_in == 24 and info.rows_out == 8
        assert info.stores == ["C", "N", "S"]
        assert info.demand_total == pytest.approx(float(df["qty"].sum()))

    def test_input_frame_is_not_modified(self):
        df = _two_skus_three_stores()
        snapshot = df.copy()
        rollup_stores(df, date_col="date", sku_col="sku",
                      store_cols=["store"], sum_cols=["qty"])
        pd.testing.assert_frame_equal(df, snapshot)

    def test_store_date_cells_that_do_not_exist_count_as_no_sale(self):
        # Store S only reports on day 1; a sales file lists sales, so the other
        # days are the two remaining stores' total.
        df = _frame([
            ("A", "N", "2026-01-01", 10.0), ("A", "S", "2026-01-01", 1.0),
            ("A", "N", "2026-01-02", 10.0),
            ("A", "N", "2026-01-03", 10.0),
        ])
        out, info = rollup_stores(df, date_col="date", sku_col="sku",
                                  store_cols=["store"], sum_cols=["qty"])
        assert out.set_index("date")["qty"].to_dict() == {
            "2026-01-01": 11.0, "2026-01-02": 10.0, "2026-01-03": 10.0}
        assert info.n_missing_target_cells == 0

    def test_empty_cells_are_counted_and_an_all_empty_day_stays_empty(self):
        df = _frame([
            ("A", "N", "2026-01-01", 10.0), ("A", "S", "2026-01-01", np.nan),
            ("A", "N", "2026-01-02", np.nan), ("A", "S", "2026-01-02", np.nan),
        ])
        out, info = rollup_stores(df, date_col="date", sku_col="sku",
                                  store_cols=["store"], sum_cols=["qty"])
        by_day = out.set_index("date")["qty"]
        assert by_day["2026-01-01"] == 10.0
        # Unknown is not zero: the day nobody reported stays missing, so the
        # engine's missing-data handling sees it instead of a fake 0.
        assert np.isnan(by_day["2026-01-02"])
        assert info.n_missing_target_cells == 1


class TestNothingToSum:
    def test_one_store_per_sku_returns_the_same_frame(self):
        df = _frame([
            ("A", "N", "2026-01-01", 3.0), ("A", "N", "2026-01-02", 4.0),
            ("B", "S", "2026-01-01", 5.0), ("B", "S", "2026-01-02", 6.0),
        ])
        out, info = rollup_stores(df, date_col="date", sku_col="sku",
                                  store_cols=["store"], sum_cols=["qty"])
        assert out is df
        assert not info.applied
        assert info.rows_in == info.rows_out == 4

    def test_no_store_column_returns_the_same_frame(self):
        df = _two_skus_three_stores().drop(columns=["store"])
        out, info = rollup_stores(df, date_col="date", sku_col="sku",
                                  store_cols=["store"], sum_cols=["qty"])
        assert out is df and not info.applied

    def test_empty_store_labels_do_not_count_as_a_second_store(self):
        df = _frame([
            ("A", "N", "2026-01-01", 3.0), ("A", None, "2026-01-02", 4.0),
        ])
        assert skus_with_several_stores(df, "sku", "store") == 0
        out, info = rollup_stores(df, date_col="date", sku_col="sku",
                                  store_cols=["store"], sum_cols=["qty"])
        assert out is df and not info.applied


class TestOtherColumns:
    def _df(self):
        return pd.DataFrame({
            "sku":   ["A", "A", "A", "A"],
            "store": ["N", "S", "N", "S"],
            "date":  pd.to_datetime(["2026-01-01", "2026-01-01",
                                     "2026-01-02", "2026-01-02"]),
            "qty":   [9, 1, 0, 0],                    # ints in, sum out
            "price": [10.0, 20.0, 10.0, 30.0],
            "stock": [50.0, 5.0, np.nan, np.nan],
            "promo": [False, True, False, False],
            "promo01": [0, 1, 0, 0],
            "lead":  [7, 20, 7, 20],
            "region": ["R1", "R1", "R1", "R1"],
            "other_num": [1.0, 3.0, 2.0, 2.0],
        })

    def _roll(self, df):
        return rollup_stores(
            df, date_col="date", sku_col="sku", store_cols=["store"],
            sum_cols=["qty", "stock"], weight_col="qty",
            weighted_mean_cols=["price"], flag_cols=["promo", "promo01"],
            max_cols=["lead"],
        )

    def test_price_is_demand_weighted_and_falls_back_to_the_plain_mean(self):
        out, _ = self._roll(self._df())
        day = out.set_index(out["date"].dt.strftime("%Y-%m-%d"))
        # 9 units at 10 and 1 unit at 20 -> 11, not the plain 15.
        assert day.loc["2026-01-01", "price"] == pytest.approx(11.0)
        # Nobody sold: the weights are all zero, so the plain mean.
        assert day.loc["2026-01-02", "price"] == pytest.approx(20.0)

    def test_stock_sums_flags_or_lead_time_max(self):
        out, _ = self._roll(self._df())
        day = out.set_index(out["date"].dt.strftime("%Y-%m-%d"))
        assert day.loc["2026-01-01", "stock"] == 55.0
        assert np.isnan(day.loc["2026-01-02", "stock"])
        assert bool(day.loc["2026-01-01", "promo"]) is True
        assert bool(day.loc["2026-01-02", "promo"]) is False
        assert day.loc["2026-01-01", "promo01"] == 1
        assert day.loc["2026-01-01", "lead"] == 20
        assert day.loc["2026-01-01", "region"] == "R1"
        assert day.loc["2026-01-01", "other_num"] == pytest.approx(2.0)

    def test_dtypes(self):
        out, _ = self._roll(self._df())
        assert pd.api.types.is_datetime64_any_dtype(out["date"])
        assert pd.api.types.is_numeric_dtype(out["qty"])
        assert list(out["qty"]) == [10, 0]
        assert pd.api.types.is_bool_dtype(out["promo"])
        assert list(out.columns) == [c for c in self._df().columns if c != "store"]


class TestCanonical:
    def test_mapped_column_and_its_alias_both_get_the_field_rule(self):
        # What the runner holds after apply_canonical_defaults: the user's
        # columns AND a canonical copy of each.
        df = pd.DataFrame({
            "producto": ["A"] * 4, "tienda": ["N", "S", "N", "S"],
            "fecha": ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
            "ventas": [4.0, 6.0, 1.0, 1.0], "precio": [1.0, 2.0, 1.0, 1.0],
        })
        df["sku"], df["store"], df["date"] = df["producto"], df["tienda"], df["fecha"]
        df["demand"], df["price"] = df["ventas"], df["precio"]
        df["inventory"], df["promo"], df["lead_time"] = 0, False, 15
        mapping = {"sku": "producto", "store": "tienda", "date": "fecha",
                   "demand": "ventas", "price": "precio"}
        out, info = rollup_stores_canonical(
            df, date_col="fecha", target_col="ventas", sku_col="producto",
            store_col="tienda", canonical_mapping=mapping)
        assert info.applied
        assert "tienda" not in out.columns and "store" not in out.columns
        assert list(out["ventas"]) == [10.0, 2.0]
        assert list(out["demand"]) == [10.0, 2.0]          # the alias agrees
        assert list(out["price"]) == pytest.approx([1.6, 1.0])
        assert list(out["precio"]) == pytest.approx([1.6, 1.0])
        assert info.store_columns == ["tienda", "store"]

    def test_the_finding_code_is_stable(self):
        assert STORE_ROLLUP_CODE == "PREP_STORES_SUMMED"


class TestEngineForecastsTheTotal:
    """End to end through the real engine: the defect was a forecast of ONE
    store's demand standing in for the SKU's."""

    def _data(self):
        rng = np.random.default_rng(0)
        rows = []
        for sku in ("A", "B"):
            for store, lvl in (("N", 10.0), ("C", 5.0), ("S", 1.0)):
                for d in pd.date_range("2026-01-01", periods=120, freq="D"):
                    rows.append((sku, store, d, float(max(0.0, rng.normal(lvl, lvl * 0.1)))))
        return _frame(rows)

    def test_lightgbm_forecasts_about_the_sku_total(self):
        from forecasting_core.engine import ForecastEngine

        df, info = rollup_stores(self._data(), date_col="date", sku_col="sku",
                                 store_cols=["store"], sum_cols=["qty"])
        assert info.applied
        engine = ForecastEngine.from_dict({
            "name": "stores", "data": {"path": "", "date_freq": None},
            "columns": {"target": "qty", "date": "date", "group_keys": ["sku"],
                        "exogenous": []},
            "features": {"lags": [1, 7], "rolling": [7], "diffs": [], "calendar": True},
            "models": {"lightgbm": {"n_estimators": 50}},
            "training": {"train_ratio": 0.8, "walk_forward": False, "wfv_splits": 2,
                         "min_history": 20, "seasonal_period": 7, "max_workers": 1},
            "forecast": {"horizon": 7},
        })
        engine._df = df
        engine.train()
        rows = [r for r in engine.get_forecast()["rows"] if r["model"] == "lightgbm"]
        assert {str(r["sku"]) for r in rows} == {"A", "B"}
        for sku in ("A", "B"):
            daily = np.mean([float(r["forecast"]) for r in rows if str(r["sku"]) == sku])
            # Truth is 16/day. Before the rollup the engine said ~1/day.
            assert daily == pytest.approx(16.0, rel=0.15), (sku, daily)
