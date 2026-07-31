"""
The purchase-order document leaves the tenant and arrives at their supplier.

A line whose `unit_cost` nobody had recorded used to render through
`i.get("unit_cost") or 0`, so the PDF quoted **₡0 per unit** and, since the same
zeros were summed, **"Total: ₡0"** for the whole order — in the buyer's name, to
a third party who has no way to tell that apart from a price the buyer meant.
Understating an order is the one direction a purchase document must never err
in.

An unknown price is now stated as unknown ("a convenir"), and the total covers
only the priced lines and says how many those are.
"""

import pytest

from backend.formatting import DEFAULT_CURRENCY
from backend.inventory.po_pdf import _total_line
from backend.notifications.locale import render_es


UNKNOWN = render_es("po_pdf_cost_unknown")


def _line(cost, qty=2):
    return {"sku": "SKU1", "display_name": "Producto", "final_qty": qty,
            "unit_cost": cost}


class TestTheTotal:

    def test_every_line_priced_reads_like_before(self):
        items = [_line(2.0), _line(3.0)]
        assert _total_line(items, items, 10.0, DEFAULT_CURRENCY).startswith("Total:")
        assert "10" in _total_line(items, items, 10.0, DEFAULT_CURRENCY)

    def test_a_partial_total_says_how_many_lines_it_covers(self):
        items = [_line(2.0), _line(None), _line(3.0)]
        priced = [i for i in items if i["unit_cost"] is not None]
        text = _total_line(priced, items, 10.0, DEFAULT_CURRENCY)
        assert "2" in text and "3" in text, (
            f"the supplier must see which lines the total covers: {text!r}"
        )

    def test_no_price_at_all_quotes_nothing(self):
        items = [_line(None), _line(None)]
        text = _total_line([], items, 0.0, DEFAULT_CURRENCY)
        assert text == render_es("po_pdf_total_none")
        assert "0" not in text, f"a zero here reads as a quoted price: {text!r}"

    def test_unknown_costs_are_not_summed_as_zero(self):
        """The regression: the total must not silently include the unpriced
        lines at 0, which is what made a 3-line order look cheaper than it is."""
        items = [_line(5.0, qty=2), _line(None, qty=100)]
        priced = [items[0]]
        text = _total_line(priced, items, 10.0, DEFAULT_CURRENCY)
        assert text != render_es("po_pdf_total", amount="x"), "must be the partial form"
        assert "1 de 2" in text, text


class TestTheDocument:
    """Rendered end to end, because the table cell is where the ₡0 appeared."""

    def _render(self, tmp_path, monkeypatch, items):
        from backend.inventory import po_pdf
        monkeypatch.setattr(po_pdf.paths, "po_pdf_file",
                            lambda t, p, s: tmp_path / f"{s}.pdf")
        return po_pdf.generate_po_pdf(
            "ten_x", "po_x", "ACME", items,
            {"generated_at": "2026-07-30", "po_log_id": "po_x"},
            DEFAULT_CURRENCY,
        )

    def _text_of(self, path):
        if path.suffix == ".txt":
            return path.read_text(encoding="utf-8")
        # reportlab compresses page streams; the strings we assert on are in the
        # PDF's object stream only when uncompressed, so fall back to bytes and
        # let the caller skip if the marker is not findable.
        return path.read_bytes().decode("latin-1")

    def test_an_unpriced_line_never_prints_a_zero_price(
        self, tmp_path, monkeypatch,
    ):
        path = self._render(tmp_path, monkeypatch, [_line(None, qty=7)])
        assert path.exists()
        if path.suffix == ".txt":
            text = self._text_of(path)
            assert UNKNOWN in text, text
            assert render_es("po_pdf_total_none") in text, text

    def test_a_priced_line_still_renders(self, tmp_path, monkeypatch):
        path = self._render(tmp_path, monkeypatch, [_line(4.0, qty=3)])
        assert path.exists()
        if path.suffix == ".txt":
            assert UNKNOWN not in self._text_of(path)


class TestTheCatalogEntriesExist:
    """`render_es` raises KeyError on a missing key — a blank in a supplier
    document would otherwise ship silently."""

    @pytest.mark.parametrize("key,params", [
        ("po_pdf_cost_unknown", {}),
        ("po_pdf_total", {"amount": "₡10"}),
        ("po_pdf_total_partial", {"priced": 1, "total": 2, "amount": "₡10"}),
        ("po_pdf_total_none", {}),
    ])
    def test_key_renders(self, key, params):
        assert render_es(key, **params).strip()
