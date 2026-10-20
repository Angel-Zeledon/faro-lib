"""Pure rules of the purchase-order ERP feed: cursor, identity, document split and
the import files. No database."""

import csv
import io

import pytest

from backend.po_erp import core, formats


def _doc(**over):
    base = {
        "document_id": "po1:sup1", "number": "OC-000007",
        "supplier": {"id": "sup1", "name": "Acme", "erp_code": "V100", "mapped": True},
        "currency": "USD", "order_date": "2026-10-19", "expected_date": "2026-11-02",
        "warehouse": "principal",
        "lines": [
            {"line_id": "l1", "sku": "SKU-1", "name": "Widget", "quantity": 12.0,
             "unit_cost": 2.5, "currency": "USD", "total": 30.0},
            {"line_id": "l2", "sku": "SKU-2", "name": "Gadget", "quantity": 3.0,
             "unit_cost": None, "currency": None, "total": None},
        ],
    }
    base.update(over)
    return base


class TestCursor:
    def test_round_trip(self):
        assert core.decode_cursor(core.encode_cursor(42)) == 42

    def test_no_cursor_starts_at_the_beginning(self):
        assert core.decode_cursor(None) == 0
        assert core.decode_cursor("") == 0

    @pytest.mark.parametrize("raw", ["42", "po_", "po_-1", "po_1.5", "x_5", "po_5; DROP"])
    def test_a_foreign_cursor_is_refused_not_guessed(self, raw):
        with pytest.raises(ValueError):
            core.decode_cursor(raw)

    def test_page_size_is_clamped(self):
        assert core.clamp_page_size(None) == core.DEFAULT_PAGE_SIZE
        assert core.clamp_page_size(0) == 1
        assert core.clamp_page_size(10_000) == core.MAX_PAGE_SIZE


class TestState:
    def test_cancelled_wins_over_reception(self):
        assert core.order_state("received", True) == core.STATE_CANCELLED

    @pytest.mark.parametrize("status,state", [
        ("pending", core.STATE_OPEN), ("not_received", core.STATE_OPEN),
        ("partial", core.STATE_PARTIAL), ("received", core.STATE_RECEIVED),
        (None, core.STATE_OPEN),
    ])
    def test_reception_maps_to_four_states(self, status, state):
        assert core.order_state(status, False) == state


class TestDocuments:
    def test_document_id_is_stable_and_names_the_supplier(self):
        assert core.document_id("po1", "sup1") == core.document_id("po1", "sup1") == "po1:sup1"
        assert core.document_id("po1", "") == "po1:-"

    def test_lines_split_one_document_per_supplier_unassigned_last(self):
        lines = [
            {"id": "a", "supplier": "Zeta", "supplier_id": "z"},
            {"id": "b", "supplier": None, "supplier_id": None},
            {"id": "c", "supplier": "Acme", "supplier_id": "a"},
            {"id": "d", "supplier": "Zeta", "supplier_id": "z"},
        ]
        docs = core.split_documents(lines)
        assert [d["supplier_key"] for d in docs] == ["a", "z", ""]
        assert [ln["id"] for ln in docs[1]["lines"]] == ["a", "d"]

    def test_mixed_currency_is_flagged_not_picked(self):
        assert core.common_currency(["USD", "USD", None]) == ("USD", False)
        assert core.common_currency(["USD", "EUR"]) == (None, True)
        assert core.common_currency([None]) == (None, False)

    def test_unknown_cost_is_not_zero(self):
        assert core.line_total(3, None) is None
        assert core.line_total(3, 0.0) == 0.0
        assert core.line_total(3, 2.5) == 7.5

    @pytest.mark.parametrize("raw", ["", "   ", None, "x" * (core.MAX_REFERENCE_LENGTH + 1)])
    def test_bad_references_are_refused(self, raw):
        with pytest.raises(ValueError):
            core.clean_reference(raw)

    def test_reference_is_trimmed(self):
        assert core.clean_reference("  4500001234 ") == "4500001234"


def _rows(blob: bytes, delimiter=","):
    text = blob.decode("utf-8").lstrip("﻿")
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


class TestFormats:
    def test_every_unverified_layout_says_so(self):
        assert formats.FORMATS["csv"].verified and formats.FORMATS["xlsx"].verified
        for key in ("sap", "dynamics_bc", "netsuite", "odoo", "oracle_ebs"):
            assert formats.FORMATS[key].verified is False, key

    def test_generic_csv_carries_stable_ids_and_blank_unknown_cost(self):
        rows = _rows(formats.generic_csv([_doc()]))
        header, first, second = rows
        col = {name: i for i, name in enumerate(header)}
        assert first[col["document_id"]] == "po1:sup1"
        assert first[col["line_id"]] == "l1" and second[col["line_id"]] == "l2"
        assert first[col["supplier_code"]] == "V100"
        assert first[col["line_total"]] == "30"
        assert second[col["unit_cost"]] == "" and second[col["line_total"]] == ""

    def test_unmapped_supplier_falls_back_to_the_name_where_a_vendor_is_required(self):
        doc = _doc(supplier={"id": "s", "name": "Acme", "erp_code": None, "mapped": False})
        assert _rows(formats.netsuite([doc]))[1][1] == "Acme"
        assert _rows(formats.generic_csv([doc]))[1][3] == ""

    def test_sap_has_one_header_and_an_item_and_schedule_per_line(self):
        rows = _rows(formats.sap_bapi([_doc()]), delimiter="\t")
        assert [r[0] for r in rows[1:]] == ["H", "I", "S", "I", "S"]
        assert rows[1][4] == "V100"
        assert rows[2][9] == "00010" and rows[4][9] == "00020"
        assert rows[3][15] == "20261102"

    def test_netsuite_external_id_makes_a_reimport_an_update(self):
        rows = _rows(formats.netsuite([_doc()]))
        assert {r[0] for r in rows[1:]} == {"po1:sup1"}

    def test_odoo_repeats_header_columns_only_on_the_first_line(self):
        rows = _rows(formats.odoo([_doc()]))
        assert rows[1][0] == "po1:sup1" and rows[2][0] == ""
        assert rows[2][5] == "SKU-2"

    def test_text_cells_are_neutralised_against_formula_injection(self):
        doc = _doc()
        doc["lines"][0]["name"] = "=HYPERLINK(\"http://x\")"
        for key in ("csv", "netsuite", "dynamics_bc", "odoo", "oracle_ebs", "sap"):
            blob = formats.FORMATS[key].render([doc])
            assert b'\n=HYPERLINK' not in blob and b',=HYPERLINK' not in blob, key
            assert b'\t=HYPERLINK' not in blob, key

    def test_xlsx_is_the_same_data_as_the_csv(self):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(formats.generic_xlsx([_doc()])))
        got = [[("" if c is None else str(c)) for c in row] for row in wb.active.iter_rows(values_only=True)]
        assert got == _rows(formats.generic_csv([_doc()]))
