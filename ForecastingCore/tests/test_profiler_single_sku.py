"""
Regression test for the single-SKU "__all__" bug.

A 1-product file (the SKU column has a single distinct value) must still expose
that column as a group candidate. Otherwise the quick-start wizard sends
sku_column=None, training collapses to the "__all__" single-series mode, and the
product identity is lost — the forecast/inventory can never be matched back to
the real SKU (any stock entered for it stays SIN_DATOS forever).
Multi-SKU files must be unaffected.
"""
import pandas as pd

from forecasting_core.data.profiler import DataProfiler


def _single_sku_df(n=40):
    return pd.DataFrame({
        "fecha":    pd.date_range("2024-01-01", periods=n).astype(str),
        "sku":      ["SKU-001"] * n,
        "nombre":   ["Aceite de Oliva"] * n,
        "cantidad": list(range(1, n + 1)),
    })


def _multi_sku_df(n_per=12):
    skus = ["SKU-001", "SKU-002", "SKU-003"]
    base = pd.Timestamp("2024-01-01")
    rows = [
        {"fecha": str((base + pd.Timedelta(days=i)).date()),
         "sku": s, "region": "Centro", "cantidad": i + 1}
        for i in range(n_per) for s in skus
    ]
    return pd.DataFrame(rows)


def test_single_sku_exposed_as_group_candidate():
    opts = DataProfiler().get_column_options(_single_sku_df())
    assert "sku" in opts["group_candidates"], (
        "single-SKU file must keep its product column as a group candidate "
        "(otherwise it trains as __all__ and loses the SKU)"
    )
    # The SKU-like column is preferred over a generic single-valued text column.
    assert opts["group_candidates"][0] == "sku"


def test_multi_sku_ignores_constant_columns():
    opts = DataProfiler().get_column_options(_multi_sku_df())
    # 'sku' is the real (multi-valued) grouping column; the constant 'region'
    # must NOT be offered, because a valid multi-valued candidate exists.
    assert opts["group_candidates"] == ["sku"]
