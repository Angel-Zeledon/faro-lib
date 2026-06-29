"""
tests/test_canonical_columns_request.py

Unit tests for CanonicalColumnsRequest Pydantic model.

These tests are pure-Python and require no DB connection.
Marked @pytest.mark.offline so conftest skips the DB-unreachable guard.
"""
from __future__ import annotations

import sys
from unittest import mock as _mock

# ── Canonical mock setup ──────────────────────────────────────────────────────
# conftest._inject_forecasting_core_mocks() replaces 'forecasting_core' with a
# flat MagicMock (to make the package optional).  That breaks any
# `from forecasting_core.data.canonical import REQUIRED_FIELDS` call because
# Python won't resolve sub-module dotted imports against a non-package mock.
# Fix: pre-populate sys.modules with a proper stub before validate_required()
# is ever called.  We only do this when the mock is already installed.
_fc = sys.modules.get("forecasting_core")
if isinstance(_fc, _mock.MagicMock):
    _canonical_stub = _mock.MagicMock()
    _canonical_stub.REQUIRED_FIELDS = frozenset({"sku", "date", "demand"})
    sys.modules.setdefault("forecasting_core.data", _mock.MagicMock())
    sys.modules["forecasting_core.data.canonical"] = _canonical_stub

import pytest

from backend.schemas.configuration import CanonicalColumnsRequest, ColumnsConfigRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_COLS = [
    "fecha", "sku_id", "ventas", "tienda", "region",
    "inventario", "lead", "precio", "costo",
    "precio_regular", "precio_promo", "promo_flag", "tipo_promo", "descuento",
]

_REQUIRED_MAPPING = {
    "sku":    "sku_id",
    "date":   "fecha",
    "demand": "ventas",
}

_FULL_MAPPING = {
    **_REQUIRED_MAPPING,
    "store":         "tienda",
    "region":        "region",
    "inventory":     "inventario",
    "lead_time":     "lead",
    "price":         "precio",
    "cost":          "costo",
    "regular_price": "precio_regular",
    "promo_price":   "precio_promo",
    "promo":         "promo_flag",
    "promo_type":    "tipo_promo",
    "discount":      "descuento",
}


# ---------------------------------------------------------------------------
# Instantiation tests
# ---------------------------------------------------------------------------

@pytest.mark.offline
def test_canonical_columns_request_empty_mapping():
    """Instantiates with no arguments (all defaults)."""
    req = CanonicalColumnsRequest()
    assert req.canonical_mapping == {}
    assert req.defaults_override == {}


@pytest.mark.offline
def test_canonical_columns_request_required_only():
    """Instantiates with only the 3 required fields mapped."""
    req = CanonicalColumnsRequest(canonical_mapping=_REQUIRED_MAPPING)
    assert req.canonical_mapping["sku"] == "sku_id"
    assert req.canonical_mapping["date"] == "fecha"
    assert req.canonical_mapping["demand"] == "ventas"


@pytest.mark.offline
def test_canonical_columns_request_full_mapping():
    """Accepts all 14 canonical fields."""
    req = CanonicalColumnsRequest(canonical_mapping=_FULL_MAPPING)
    assert len(req.canonical_mapping) == 14


@pytest.mark.offline
def test_canonical_columns_request_defaults_override():
    """defaults_override accepts arbitrary key/value pairs."""
    req = CanonicalColumnsRequest(
        canonical_mapping=_REQUIRED_MAPPING,
        defaults_override={"lead_time": 14, "store": "Matriz"},
    )
    assert req.defaults_override["lead_time"] == 14
    assert req.defaults_override["store"] == "Matriz"


@pytest.mark.offline
def test_canonical_columns_request_optional_fields_can_be_none():
    """Optional canonical fields may be mapped to None."""
    mapping = {**_REQUIRED_MAPPING, "store": None, "price": None}
    req = CanonicalColumnsRequest(canonical_mapping=mapping)
    assert req.canonical_mapping["store"] is None


@pytest.mark.offline
def test_canonical_columns_request_model_dump():
    """model_dump() (Pydantic v2) returns a plain dict."""
    req = CanonicalColumnsRequest(canonical_mapping=_REQUIRED_MAPPING)
    d = req.model_dump()
    assert isinstance(d, dict)
    assert "canonical_mapping" in d
    assert "defaults_override" in d


# ---------------------------------------------------------------------------
# validate_required — happy path
# ---------------------------------------------------------------------------

@pytest.mark.offline
def test_validate_required_passes_with_valid_mapping():
    """No exception when all required fields are mapped to existing columns."""
    req = CanonicalColumnsRequest(canonical_mapping=_REQUIRED_MAPPING)
    req.validate_required(_ALL_COLS)  # must not raise


@pytest.mark.offline
def test_validate_required_passes_with_full_mapping():
    """No exception when all 14 fields are mapped to existing columns."""
    req = CanonicalColumnsRequest(canonical_mapping=_FULL_MAPPING)
    req.validate_required(_ALL_COLS)  # must not raise


# ---------------------------------------------------------------------------
# validate_required — missing required fields
# ---------------------------------------------------------------------------

@pytest.mark.offline
def test_validate_required_raises_when_sku_missing():
    """ValueError when 'sku' has no column mapping."""
    mapping = {"date": "fecha", "demand": "ventas"}
    req = CanonicalColumnsRequest(canonical_mapping=mapping)
    with pytest.raises(ValueError, match="sku"):
        req.validate_required(_ALL_COLS)


@pytest.mark.offline
def test_validate_required_raises_when_date_missing():
    """ValueError when 'date' has no column mapping."""
    mapping = {"sku": "sku_id", "demand": "ventas"}
    req = CanonicalColumnsRequest(canonical_mapping=mapping)
    with pytest.raises(ValueError, match="date"):
        req.validate_required(_ALL_COLS)


@pytest.mark.offline
def test_validate_required_raises_when_demand_missing():
    """ValueError when 'demand' has no column mapping."""
    mapping = {"sku": "sku_id", "date": "fecha"}
    req = CanonicalColumnsRequest(canonical_mapping=mapping)
    with pytest.raises(ValueError, match="demand"):
        req.validate_required(_ALL_COLS)


@pytest.mark.offline
def test_validate_required_raises_when_required_mapped_to_none():
    """ValueError when a required field is explicitly mapped to None."""
    mapping = {"sku": None, "date": "fecha", "demand": "ventas"}
    req = CanonicalColumnsRequest(canonical_mapping=mapping)
    with pytest.raises(ValueError, match="sku"):
        req.validate_required(_ALL_COLS)


@pytest.mark.offline
def test_validate_required_raises_all_three_missing():
    """ValueError lists all three required fields when mapping is empty."""
    req = CanonicalColumnsRequest(canonical_mapping={})
    with pytest.raises(ValueError) as exc_info:
        req.validate_required(_ALL_COLS)
    msg = str(exc_info.value)
    assert "sku" in msg
    assert "date" in msg
    assert "demand" in msg


# ---------------------------------------------------------------------------
# validate_required — column not in file
# ---------------------------------------------------------------------------

@pytest.mark.offline
def test_validate_required_raises_when_required_col_not_in_file():
    """ValueError when a required field maps to a column absent from the file."""
    mapping = {"sku": "NONEXISTENT_COL", "date": "fecha", "demand": "ventas"}
    req = CanonicalColumnsRequest(canonical_mapping=mapping)
    with pytest.raises(ValueError, match="NONEXISTENT_COL"):
        req.validate_required(_ALL_COLS)


@pytest.mark.offline
def test_validate_required_raises_when_optional_col_not_in_file():
    """ValueError when an optional field maps to a column absent from the file."""
    mapping = {**_REQUIRED_MAPPING, "store": "MISSING_STORE_COL"}
    req = CanonicalColumnsRequest(canonical_mapping=mapping)
    with pytest.raises(ValueError, match="MISSING_STORE_COL"):
        req.validate_required(_ALL_COLS)


@pytest.mark.offline
def test_validate_required_error_message_lists_available_columns():
    """Error message for required field includes the list of available columns."""
    mapping = {"sku": "bad_col", "date": "fecha", "demand": "ventas"}
    req = CanonicalColumnsRequest(canonical_mapping=mapping)
    cols = ["fecha", "ventas", "sku_id"]
    with pytest.raises(ValueError) as exc_info:
        req.validate_required(cols)
    msg = str(exc_info.value)
    # Should mention at least one of the available columns
    assert any(c in msg for c in cols)


# ---------------------------------------------------------------------------
# Backward compatibility — ColumnsConfigRequest must still exist
# ---------------------------------------------------------------------------

@pytest.mark.offline
def test_columns_config_request_still_exists():
    """ColumnsConfigRequest is not removed (backward compatibility)."""
    req = ColumnsConfigRequest(date_column="fecha", target_column="ventas")
    assert req.date_column == "fecha"
    assert req.target_column == "ventas"
    assert req.sku_column is None
