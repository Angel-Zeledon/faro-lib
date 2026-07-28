import pandas as pd
import pytest
from forecasting_core.data.canonical import (
    apply_canonical_defaults, series_key, parse_series_key,
    REQUIRED_FIELDS, FIELD_DEFAULTS, DEFAULT_LEAD_TIME_DAYS,
)

def _base_df():
    return pd.DataFrame({
        "fecha": ["2024-01-01", "2024-01-02"],
        "prod":  ["SKU-001", "SKU-001"],
        "ventas": [10.0, 12.0],
    })

def test_required_fields_are_sku_date_demand():
    assert REQUIRED_FIELDS == frozenset({"sku", "date", "demand"})

def test_apply_defaults_adds_store_column_when_not_mapped():
    df = _base_df()
    mapping = {"sku": "prod", "date": "fecha", "demand": "ventas"}
    result = apply_canonical_defaults(df, mapping)
    assert "store" in result.columns
    assert (result["store"] == "Tienda única").all()

def test_apply_defaults_adds_the_one_lead_time_default_when_not_mapped():
    """Pinned to the constant, not to a literal.

    The product used to carry three different lead-time defaults — 7 here and
    in the training runner, 15 in the DB schema and the wizard — so a number
    nobody chose was deciding how much to spend, and which number depended on
    which code path you came through. Asserting the constant is what keeps this
    test honest if the business value is revisited.
    """
    df = _base_df()
    mapping = {"sku": "prod", "date": "fecha", "demand": "ventas"}
    result = apply_canonical_defaults(df, mapping)
    assert (result["lead_time"] == DEFAULT_LEAD_TIME_DAYS).all()

def test_apply_defaults_sets_price_to_none_when_not_mapped():
    df = _base_df()
    mapping = {"sku": "prod", "date": "fecha", "demand": "ventas"}
    result = apply_canonical_defaults(df, mapping)
    assert result["price"].isna().all()

def test_apply_defaults_uses_source_column_when_mapped():
    df = _base_df()
    df["shop"] = ["Bogota", "Bogota"]
    mapping = {"sku": "prod", "date": "fecha", "demand": "ventas", "store": "shop"}
    result = apply_canonical_defaults(df, mapping)
    assert (result["store"] == "Bogota").all()

def test_required_field_missing_raises():
    df = _base_df()
    mapping = {"sku": "prod", "date": "fecha"}  # demand missing
    with pytest.raises(ValueError, match="demand"):
        apply_canonical_defaults(df, mapping)

def test_series_key_uses_pipe_separator():
    assert series_key("SKU-001", "Bogota") == "SKU-001│Bogota"

def test_parse_series_key_round_trips():
    key = series_key("SKU-001", "Bogota")
    assert parse_series_key(key) == ("SKU-001", "Bogota")
