"""CSV formula-injection neutralization (QA finding #1)."""

import csv
import io

from backend.utils.csv_safe import csv_safe


class TestCsvSafe:
    def test_neutralizes_formula_prefixes(self):
        assert csv_safe("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
        assert csv_safe("+1+1") == "'+1+1"
        assert csv_safe("-2+3") == "'-2+3"
        assert csv_safe('@SUM(A1)') == "'@SUM(A1)"

    def test_leaves_plain_text_untouched(self):
        assert csv_safe("Acme Corp") == "Acme Corp"
        assert csv_safe("SKU-001") == "SKU-001"
        assert csv_safe(123) == "123"
        assert csv_safe(None) == ""

    def test_written_cell_is_not_a_formula(self):
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([csv_safe("=HYPERLINK(\"http://evil\")")])
        # The rendered cell starts with an apostrophe, so no spreadsheet
        # evaluates it as a formula.
        assert out.getvalue().lstrip('"').startswith("'=")


class TestExportPoNeutralized:
    def test_export_po_neutralizes_supplier_and_name(self, client, auth_headers,
                                                      test_tenant, completed_session):
        from backend.inventory import service as inv_svc
        tid, sid = test_tenant["id"], completed_session["id"]
        # A forecasted SKU with a malicious supplier/name.
        skus = list((inv_svc.get_inventory_status(tid, sid) or []))
        assert skus, "completed_session should have forecasted SKUs"
        sku = skus[0]["sku"]
        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 0, "warehouse": "principal",
            "supplier": "=cmd|' /C calc'!A0",
            "display_name": "=HYPERLINK(\"http://evil\")",
        })
        r = client.get(
            f"/api/v1/inventory/status/export-po?session_id={sid}"
            "&signals=PEDIR_YA,PEDIR_PRONTO,OK,SOBRESTOCK",
            headers=auth_headers)
        assert r.status_code == 200
        body = r.text
        # The raw executable forms must not appear at a cell boundary.
        assert ",=cmd" not in body and '"=cmd' not in body
        assert ",=HYPERLINK" not in body and '"=HYPERLINK' not in body
        # The neutralized form (apostrophe-prefixed) is what ships.
        assert "'=cmd" in body or "'=HYPERLINK" in body
