"""
A number arriving from an ERP must be read by the same rules as one in a file.

`ProviderSaleLine.quantity` is annotated `float`, but a dataclass does not
enforce an annotation. An ERP that reports `"quantity": "10.5"` as a JSON string
put a `str` into the field, and the first arithmetic on it — the sum inside
`_build_sales_csv` — raised:

    TypeError: unsupported operand type(s) for +: 'float' and 'str'

Measured: ONE such line anywhere in the payload aborted the whole tenant's sync.
`run_daily_integration_syncs` catches per connection, so nothing crashed and
nobody was told — the tenant's forecast simply stopped being refreshed, which on
screen is indistinguishable from a forecast that is merely a few days old.

Two things are being pinned here, and the second matters more than the first:

  1. The value is read, not rejected. `10,5` is ten and a half in most of Latin
     America and the upload path already accepts it.
  2. It is read by the SAME rules as `runner.py::_coerce_decimal_comma`. Two
     paths reading one number differently is how a distributor gets one answer
     from their CSV and another from their ERP for the same week's sales.
"""

from datetime import date

import pytest

from backend.integrations.base import (
    ProviderProduct, ProviderSaleLine, ProviderStock, parse_provider_number,
)
from backend.integrations.sync_service import _build_sales_csv, _merge_products_and_stock


def _line(qty, sku="A", day=5):
    return ProviderSaleLine(date=date(2026, 1, day), sku=sku, quantity=qty,
                            unit_price=None)


def _rows(csv_bytes):
    body = csv_bytes.decode().strip().splitlines()
    return [r.split(",") for r in body[1:]]


class TestReadingTheNumber:

    @pytest.mark.parametrize("value,expected", [
        (10.5, 10.5),
        (10, 10.0),
        ("10.5", 10.5),
        ("10,5", 10.5),          # comma decimal — how most of LatAm writes it
        ("1.234,56", 1234.56),   # dot thousands + comma decimal
        ("-3,5", -3.5),
        ("0", 0.0),
        (" 7 ", 7.0),
    ])
    def test_readable_values(self, value, expected):
        assert parse_provider_number(value) == expected

    @pytest.mark.parametrize("value", [
        None, "", "   ", "diez", "n/a", "abc", [], {}, object(),
        float("nan"), float("inf"), float("-inf"),
    ])
    def test_unreadable_values_are_none_never_zero(self, value):
        """None and 0.0 are different facts. Collapsing them writes a sale of
        zero units on a day the ERP never reported one."""
        assert parse_provider_number(value) is None

    def test_a_bool_is_not_a_quantity(self):
        """`True + True == 2` in Python; a JSON `true` must not become 1 unit."""
        assert parse_provider_number(True) is None
        assert parse_provider_number(False) is None

    def test_the_ambiguous_thousands_form_is_refused_not_guessed(self):
        """
        `1,234` is 1234 in the US and 1.234 in Colombia. The upload path can ask
        the user which convention their file uses; a 3 a.m. daily sync cannot,
        and a silent 1000x on a purchase quantity is the worst outcome available.
        """
        assert parse_provider_number("1,234") is None

    def test_it_agrees_with_the_upload_path(self):
        """The two shapes `_coerce_decimal_comma` converts, converted the same."""
        assert parse_provider_number("10,5") == 10.5
        assert parse_provider_number("1.234,56") == 1234.56
        assert parse_provider_number("1,234") is None   # refused on both paths


class TestTheSyncNoLongerDies:

    def test_a_string_quantity_no_longer_raises(self):
        """The measured defect: this call used to raise TypeError."""
        csv_bytes = _build_sales_csv([_line("10.5")])
        assert _rows(csv_bytes) == [["2026-01-05", "A", "10.5"]]

    def test_one_bad_line_does_not_discard_the_good_ones(self):
        skipped: list[dict] = []
        csv_bytes = _build_sales_csv(
            [_line("10,5"), _line(2), _line("1,234", sku="B", day=6),
             _line("3", sku="B", day=6)],
            unreadable=skipped,
        )
        rows = _rows(csv_bytes)
        assert ["2026-01-05", "A", "12.5"] in rows, "10,5 + 2 must aggregate"
        assert ["2026-01-06", "B", "3.0"] in rows, "B's readable line survived"
        assert len(skipped) == 1 and skipped[0]["sku"] == "B"

    def test_a_skipped_line_is_not_written_as_zero(self):
        """A zero here teaches the model a stockout that never happened."""
        skipped: list[dict] = []
        csv_bytes = _build_sales_csv([_line("no idea")], unreadable=skipped)
        assert _rows(csv_bytes) == [], "an unreadable line must produce no row"
        assert len(skipped) == 1

    def test_the_caller_is_told_which_line_was_skipped(self):
        """Silent skipping is the same defect wearing a different hat."""
        skipped: list[dict] = []
        _build_sales_csv([_line("1,234", sku="SKU-9", day=7)], unreadable=skipped)
        assert skipped == [{"sku": "SKU-9", "date": "2026-01-07", "value": "1,234"}]

    def test_a_fully_numeric_payload_is_byte_identical(self):
        """The fix must not move a single number on a well-behaved provider."""
        lines = [_line(10.5), _line(2.0), _line(3, sku="B", day=6)]
        assert _build_sales_csv(lines) == _build_sales_csv(lines, unreadable=[])
        assert _rows(_build_sales_csv(lines)) == [
            ["2026-01-05", "A", "12.5"], ["2026-01-06", "B", "3.0"],
        ]


class TestStockAndCost:

    def test_a_string_stock_quantity_is_read(self):
        merged = _merge_products_and_stock(
            [], [ProviderStock(sku="A", quantity="7,5", warehouse="principal")])
        assert merged["A"]["current_stock"] == 7.5

    def test_a_string_unit_cost_is_read(self):
        merged = _merge_products_and_stock(
            [ProviderProduct(sku="A", name="A", unit_cost="1.234,56")], [])
        assert merged["A"]["unit_cost"] == 1234.56

    def test_an_unreadable_cost_is_left_unset_not_zeroed(self):
        """
        `upsert_stock` keeps whatever the tenant already had for a field it is
        not given. Writing a 0 instead would report every margin on this SKU as
        pure profit — a number the buyer would act on.
        """
        merged = _merge_products_and_stock(
            [ProviderProduct(sku="A", name="A", unit_cost="n/a")], [])
        assert "unit_cost" not in merged["A"]

    def test_an_unreadable_stock_is_left_unset_not_zeroed(self):
        """A 0 here reads as "out of stock" and triggers a PEDIR_YA."""
        merged = _merge_products_and_stock(
            [], [ProviderStock(sku="A", quantity="???", warehouse="principal")])
        assert "current_stock" not in merged["A"]
        assert merged["A"]["warehouse"] == "principal", "the row still exists"
