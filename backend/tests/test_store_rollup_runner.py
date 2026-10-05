"""The runner sums a multi-store file to one series per SKU before training.

The defect: a sales file with several stores per SKU and a mapped store column
trained on `group_keys=[sku, store]`. The engine keys everything by the bare
SKU, so the stored "SKU forecast" was ONE store's number — 9.7/week for a SKU
whose stores sell 100 + 10 = 110/week — with an empty history, and nothing
errored. These tests drive the runner's own helpers over the real engine; no
database (the end-to-end DB test is `test_store_rollup_training_db.py`).
"""
import numpy as np
import pandas as pd
import pytest

from backend.workers.runner import (
    _collect_run_warnings,
    _generate_forecast_series,
    _store_data_through,
    _sum_stores_before_training,
)


def _weekly_shape(days=140, seed=1):
    """2 SKUs x 2 stores, daily rows; store N sells 100/week, store S 10/week."""
    rng = np.random.default_rng(seed)
    rows = []
    for sku in ("A", "B"):
        for store, weekly in (("Norte", 100.0), ("Sur", 10.0)):
            lvl = weekly / 7.0
            for d in pd.date_range("2026-01-01", periods=days, freq="D"):
                rows.append({"producto": sku, "tienda": store, "fecha": d,
                             "ventas": float(max(0.0, rng.normal(lvl, lvl * 0.1)))})
    return pd.DataFrame(rows)


def _col_cfg():
    return {"target": "ventas", "date": "fecha",
            "group_keys": ["producto", "tienda"], "exogenous": [], "inventory": ""}


class TestTheHelper:
    def test_several_stores_are_summed_and_the_run_says_so(self):
        df = _weekly_shape(days=14)
        cfg = _col_cfg()
        notes: list = []
        out, record = _sum_stores_before_training(df, cfg, {"store": "tienda"}, notes)

        assert record is not None and record["applied"] is True
        assert record["n_stores"] == 2 and record["n_skus"] == 2
        assert cfg["group_keys"] == ["producto"]          # what was trained, recorded
        assert "tienda" not in out.columns
        assert len(out) == 2 * 14
        assert float(out["ventas"].sum()) == pytest.approx(float(df["ventas"].sum()))

        assert [n["error_id"] for n in notes] == ["PREP_STORES_SUMMED"]
        assert notes[0]["context"]["n_stores"] == 2
        assert notes[0]["context"]["n_skus"] == 2
        # It reaches the payload the results screen reads, not only the log.
        grouped = _collect_run_warnings(_NoEngineWarnings(), notes)["validation"]
        assert [g["code"] for g in grouped] == ["PREP_STORES_SUMMED"]

    def test_one_store_per_sku_changes_nothing(self):
        df = _weekly_shape(days=14)
        df = df[~((df["producto"] == "A") & (df["tienda"] == "Sur"))]
        df = df[~((df["producto"] == "B") & (df["tienda"] == "Norte"))]
        cfg = _col_cfg()
        notes: list = []
        out, record = _sum_stores_before_training(df, cfg, {"store": "tienda"}, notes)
        assert out is df and record is None and notes == []
        assert cfg["group_keys"] == ["producto", "tienda"]

    def test_a_single_key_session_is_never_touched(self):
        df = _weekly_shape(days=14)
        cfg = {"target": "ventas", "date": "fecha", "group_keys": ["producto"]}
        out, record = _sum_stores_before_training(df, cfg, {}, [])
        assert out is df and record is None

    def test_per_store_freshness_is_read_before_the_store_column_goes(self):
        df = _weekly_shape(days=14)
        cfg = _col_cfg()
        through = _store_data_through(df, cfg)
        _sum_stores_before_training(df, cfg, {"store": "tienda"}, [])
        assert through == {"Norte": "2026-01-14", "Sur": "2026-01-14"}


class _NoEngineWarnings:
    def get_run_warnings(self):
        return {"validation": [], "corrections": []}


class TestTheStoredForecastIsTheSkuTotal:
    """The original wrong number, reproduced on the real engine and fixed."""

    def _engine(self, df, group_keys):
        from forecasting_core.engine import ForecastEngine
        config = {
            "name": "stores", "data": {"path": "", "date_freq": None},
            "columns": {"target": "ventas", "date": "fecha", "group_keys": group_keys,
                        "exogenous": [], "inventory": ""},
            "features": {"lags": [1, 7], "rolling": [7], "diffs": [], "calendar": True},
            "models": {"lightgbm": {"n_estimators": 50}},
            "training": {"train_ratio": 0.8, "walk_forward": False, "wfv_splits": 2,
                         "min_history": 20, "seasonal_period": 7, "max_workers": 1},
            "forecast": {"horizon": 7},
        }
        engine = ForecastEngine.from_dict(config)
        engine._df = df
        return engine, config

    def test_weekly_forecast_is_about_110_not_one_stores_10(self):
        df = _weekly_shape()
        col_cfg = _col_cfg()
        engine, config = self._engine(df, list(col_cfg["group_keys"]))
        config["columns"] = col_cfg

        # Exactly what run_training_job does.
        engine._df, record = _sum_stores_before_training(
            engine._df, col_cfg, {"store": "tienda"}, [])
        assert record is not None
        engine._config.columns.group_keys = list(col_cfg["group_keys"])
        engine.train()

        forecasts = _generate_forecast_series(engine, config)
        assert set(forecasts) == {"A", "B"}
        for sku in ("A", "B"):
            series = forecasts[sku]["lightgbm"]
            weekly = sum(p["value"] for p in series["forecast"])
            # Before: ~9.7 (store Sur alone). The SKU sells 110/week.
            assert weekly == pytest.approx(110.0, rel=0.15), (sku, weekly)
            # Before: empty, because history was keyed sku│store.
            assert len(series["historical"]) == 140
