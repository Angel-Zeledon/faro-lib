"""Multi-store files against the database: the stock written, the grading read.

Every assertion reads `inventory_stock` or the grading service's own output
over a real uploaded file, not a response echo.
"""
import csv
import io

import pandas as pd
import pytest

from backend.db import session_store
from backend.db.connection import query_one


def _stock_of(tenant_id, sku):
    row = query_one(
        "SELECT current_stock FROM inventory_stock "
        "WHERE tenant_id=%s AND sku=%s AND warehouse='principal'", (tenant_id, sku))
    return None if row is None else float(row["current_stock"])


def _three_store_frame():
    # A: Norte's latest reading 40 (an older 50), Sur's 7, Centro never counts.
    return pd.DataFrame({
        "producto": ["A"] * 5 + ["B"] * 2,
        "tienda":   ["Norte", "Sur", "Norte", "Centro", "Sur", "Norte", "Norte"],
        "fecha":    ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-03",
                     "2026-01-03", "2026-01-01", "2026-01-02"],
        "ventas":   [5, 1, 5, 2, 1, 3, 3],
        "existencias": [50.0, 7.0, 40.0, None, None, 9.0, 8.0],
    })


@pytest.mark.integration
class TestStockIsTheSumOfTheStores:
    def test_the_sku_stock_is_each_stores_latest_reading_summed(self, test_tenant):
        from forecasting_core.data.canonical import apply_canonical_defaults
        from backend.inventory.service import sync_stock_from_dataset

        tid = test_tenant["id"]
        mapping = {"sku": "producto", "date": "fecha", "demand": "ventas",
                   "store": "tienda", "inventory": "existencias"}
        df = apply_canonical_defaults(_three_store_frame(), mapping)
        report: dict = {}
        n = sync_stock_from_dataset(tid, df, group_col="producto", date_col="fecha",
                                    canonical_mapping=mapping, store_col="tienda",
                                    report=report)
        assert n == 2
        assert _stock_of(tid, "A") == 47.0           # 40 + 7, not one store's row
        assert _stock_of(tid, "B") == 8.0            # one store: its latest reading
        assert report["n_skus"] == 1 and report["missing_pairs"] == 1

    def test_without_a_store_column_the_old_reading_stands(self, test_tenant):
        from forecasting_core.data.canonical import apply_canonical_defaults
        from backend.inventory.service import sync_stock_from_dataset

        tid = test_tenant["id"]
        frame = _three_store_frame()[lambda d: d["producto"] == "B"]
        mapping = {"sku": "producto", "date": "fecha", "demand": "ventas",
                   "inventory": "existencias"}
        df = apply_canonical_defaults(frame, mapping)
        report: dict = {}
        sync_stock_from_dataset(tid, df, group_col="producto", date_col="fecha",
                                canonical_mapping=mapping, store_col=None, report=report)
        assert _stock_of(tid, "B") == 8.0
        assert report == {}


def _later_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fecha", "producto", "tienda", "ventas"])
    for d in ("2026-05-01", "2026-05-02"):
        w.writerow([d, "A", "Norte", 100])
        w.writerow([d, "A", "Sur", 10])
    return buf.getvalue().encode()


def _graded_session(client, auth_headers, tid, with_rollup: bool):
    from backend.sessions.service import get_session

    r = client.post("/api/v1/datasets",
                    files={"file": ("later.csv", _later_csv(), "text/csv")},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    ds_id = r.json()["data"]["id"]
    sid = client.post("/api/v1/sessions", json={"name": "graded"},
                      headers=auth_headers).json()["data"]["id"]
    session_store.set_field(tid, sid, "columns_cfg", {
        "schema_version": "canonical_v1",
        "canonical_mapping": {"sku": "producto", "date": "fecha",
                              "demand": "ventas", "store": "tienda"}})
    result = {"metrics": {"rows": [{"model": "lightgbm", "sku": "A", "wape": 0.1,
                                    "mae": 1.0, "cost_horizon": 1.0}]}}
    if with_rollup:
        result["store_rollup"] = {"applied": True, "n_stores": 2, "n_skus": 1}
    session_store.set_training_result(tid, sid, result)
    session_store.set_forecasts(tid, sid, {"A": {"lightgbm": {
        "historical": [],
        "forecast": [{"date": "2026-05-01", "value": 110.0},
                     {"date": "2026-05-02", "value": 110.0}]}}})
    return get_session(tid, sid), ds_id


@pytest.mark.integration
class TestGradingOnTheSkuTotal:
    def test_a_summed_run_is_graded_against_the_stores_total(
        self, client, auth_headers, registered_user,
    ):
        from backend.forecast_check.service import forecast_vs_actual

        tid = registered_user["tenant"]["id"]
        session, ds_id = _graded_session(client, auth_headers, tid, with_rollup=True)
        out = forecast_vs_actual(tid, session, ds_id)
        assert out["status"] == "ok", out["status"]
        agg = out["result"]["aggregate"]
        assert agg["n_points"] == 2
        assert agg["wape"] == pytest.approx(0.0)

    def test_without_the_rollup_record_the_keys_stay_per_store(
        self, client, auth_headers, registered_user,
    ):
        from backend.forecast_check.service import forecast_vs_actual

        tid = registered_user["tenant"]["id"]
        session, ds_id = _graded_session(client, auth_headers, tid, with_rollup=False)
        out = forecast_vs_actual(tid, session, ds_id)
        # Unchanged behaviour for a session that never summed its stores.
        assert out["status"] == "no_matching_series"
