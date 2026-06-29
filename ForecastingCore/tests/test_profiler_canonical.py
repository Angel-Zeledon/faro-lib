"""
Tests for DataProfiler.get_canonical_mapping — 14-field auto-detection.

All fixtures use explicit pd.DataFrame(...) constructs; no mock data.
"""

import pandas as pd
from forecasting_core.data.profiler import DataProfiler


def _df_with_spanish_headers():
    return pd.DataFrame({
        "fecha":    ["2024-01-01", "2024-01-02", "2024-01-03"],
        "producto": ["SKU-A", "SKU-B", "SKU-A"],
        "ventas":   [10.0, 5.0, 8.0],
        "tienda":   ["Norte", "Sur", "Norte"],
        "stock":    [100, 50, 90],
        "precio":   [25.0, 30.0, 25.0],
    })


def _df_with_english_headers():
    return pd.DataFrame({
        "date":      ["2024-01-01", "2024-01-02", "2024-01-03"],
        "product":   ["A", "B", "A"],
        "sales":     [10.0, 5.0, 8.0],
        "store":     ["N", "S", "N"],
        "inventory": [100, 50, 90],
        "price":     [25.0, 30.0, 25.0],
    })


def test_date_field_detected_spanish():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["date"]["top"] == "fecha"
    assert result["date"]["confidence"] >= 0.7


def test_sku_field_detected_spanish():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["sku"]["top"] == "producto"
    assert result["sku"]["confidence"] >= 0.7


def test_demand_field_detected_spanish():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["demand"]["top"] == "ventas"


def test_store_field_detected_spanish():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["store"]["top"] == "tienda"


def test_price_detected_english():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_english_headers())
    assert result["price"]["top"] == "price"


def test_required_fields_cannot_use_default():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["sku"]["can_use_default"] is False
    assert result["date"]["can_use_default"] is False
    assert result["demand"]["can_use_default"] is False


def test_optional_fields_can_use_default():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["lead_time"]["can_use_default"] is True
    assert result["cost"]["can_use_default"] is True
    assert result["promo_type"]["can_use_default"] is True


def test_undetected_field_has_none_top_and_low_confidence():
    profiler = DataProfiler()
    df = pd.DataFrame({
        "fecha":    ["2024-01-01"],
        "producto": ["A"],
        "ventas":   [10.0],
    })
    result = profiler.get_canonical_mapping(df)
    # 'store' not in df at all → top should be None, confidence 0
    assert result["store"]["top"] is None
    assert result["store"]["confidence"] == 0.0


def test_all_14_canonical_fields_present_in_result():
    """get_canonical_mapping must return an entry for every canonical field."""
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    expected_fields = {
        "sku", "date", "demand", "store", "region", "inventory",
        "lead_time", "price", "cost", "regular_price", "promo_price",
        "promo", "promo_type", "discount",
    }
    assert set(result.keys()) == expected_fields


def test_result_entry_has_required_keys():
    """Each entry must expose top, candidates, confidence, can_use_default."""
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    for field, entry in result.items():
        assert "top" in entry, f"'top' missing for {field}"
        assert "candidates" in entry, f"'candidates' missing for {field}"
        assert "confidence" in entry, f"'confidence' missing for {field}"
        assert "can_use_default" in entry, f"'can_use_default' missing for {field}"
