"""Bulk import of suppliers and of purchase orders (CSV / Excel).

Every assertion that matters reads the database: a 200 with a "created: 2" body
proves nothing about rows. Hostile inputs (NUL, blank rows, wrong columns,
oversized files, garbage bytes) are part of the contract, not an afterthought.
"""

import io
import threading

import pytest

from backend.db.connection import query, query_one
from backend.inventory import service as stock_svc
from backend.inventory import supplier_service as sup_svc

BASE = "/api/v1/inventory"


def _csv(text: str, name: str = "file.csv"):
    return {"file": (name, text.encode("utf-8"), "text/csv")}


def _xlsx(columns, rows, name="file.xlsx"):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(columns)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return {"file": (name, buf.getvalue(),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def _suppliers(tid):
    return {r["name"]: r for r in query(
        "SELECT * FROM suppliers WHERE tenant_id = %s", (tid,))}


def _orders(tid):
    return query(
        "SELECT * FROM inventory_po_log WHERE tenant_id = %s ORDER BY po_number", (tid,))


def _items(po_id):
    return query(
        "SELECT * FROM inventory_po_items WHERE po_log_id = %s ORDER BY sku", (po_id,))


# ═════════════════════════════════ Suppliers ═════════════════════════════════

class TestSupplierTemplates:
    def test_csv_template_has_bom_and_every_field(self, client, analyst_headers):
        r = client.get(f"{BASE}/suppliers/import/template", headers=analyst_headers)
        assert r.status_code == 200
        assert r.content.startswith("﻿name,".encode("utf-8"))
        assert "payment_terms_days" in r.text

    def test_template_round_trips_through_the_importer_csv_and_xlsx(
        self, client, analyst_headers, test_tenant
    ):
        pytest.importorskip("openpyxl")
        tid = test_tenant["id"]
        csv_t = client.get(f"{BASE}/suppliers/import/template", headers=analyst_headers)
        r = client.post(f"{BASE}/suppliers/import",
                        files={"file": ("t.csv", csv_t.content, "text/csv")},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["created"] == 2
        rows = _suppliers(tid)
        assert set(rows) == {"Distribuidora Sur", "Importadora Andina"}
        assert rows["Distribuidora Sur"]["lead_time_days"] == 7
        assert rows["Importadora Andina"]["lead_time_days"] == 21

        # The XLSX template reads back the same two suppliers: all skipped as
        # existing, none rejected — proof it parses, with nothing duplicated.
        x_t = client.get(f"{BASE}/suppliers/import/template?format=xlsx",
                         headers=analyst_headers)
        assert x_t.content[:2] == b"PK"
        r2 = client.post(f"{BASE}/suppliers/import",
                         files={"file": ("t.xlsx", x_t.content, "application/octet-stream")},
                         headers=analyst_headers)
        assert r2.status_code == 200, r2.text
        d = r2.json()["data"]
        assert (d["created"], d["skipped_existing"], d["error_count"]) == (0, 2, 0)
        assert len(_suppliers(tid)) == 2


class TestSupplierImport:
    def test_semicolon_spanish_export_maps_and_derives_credit_days(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        text = ("Proveedor;Correo electronico;Dias de entrega;Condiciones de pago\n"
                "Lacteos Norte;compras@lacteos.example;5;30 dias\n"
                "Cafe Sur;;12;contado\n")
        r = client.post(f"{BASE}/suppliers/import", files=_csv(text), headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["mapping"]["name"] == "Proveedor"
        rows = _suppliers(tid)
        assert rows["Lacteos Norte"]["email"] == "compras@lacteos.example"
        assert rows["Lacteos Norte"]["lead_time_days"] == 5
        assert rows["Lacteos Norte"]["payment_terms_days"] == 30
        # A lead time given in the file is stamped as the user's own.
        assert rows["Lacteos Norte"]["lead_time_set_by"] == "user"
        assert rows["Cafe Sur"]["email"] is None

    def test_xlsx_with_integer_cells(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        r = client.post(
            f"{BASE}/suppliers/import",
            files=_xlsx(["name", "lead_time_days", "phone"],
                        [["Acme X", 9, 5551234], ["Beta X", None, None]]),
            headers=analyst_headers)
        assert r.status_code == 200, r.text
        rows = _suppliers(tid)
        assert rows["Acme X"]["lead_time_days"] == 9
        # 5551234 arrives as an Excel number: no ".0" tail on a phone number.
        assert rows["Acme X"]["phone"] == "5551234"

    def test_preview_writes_nothing_and_classifies_rows(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sup_svc.create_supplier(tid, {"name": "Existente"})
        gone = sup_svc.create_supplier(tid, {"name": "Dormido"})
        sup_svc.delete_supplier(tid, gone["id"])
        text = ("name,email\nNuevo,a@b.com\nexistente,\nDormido,\nMalo,not-an-email\n"
                "nuevo,\n,x@y.com\n")
        r = client.post(f"{BASE}/suppliers/import/preview", files=_csv(text),
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["new_suppliers"] == 1
        assert d["existing_suppliers"] == 1
        assert d["deactivated_suppliers"] == 1
        assert d["duplicate_rows"] == 1                  # "Nuevo" / "nuevo"
        codes = {e["code"] for e in d["errors"]}
        assert codes == {"supplier_import_row_bad_email", "supplier_import_row_missing_name"}
        assert {e["row"] for e in d["errors"]} == {5, 7}
        assert set(_suppliers(tid)) == {"Existente", "Dormido"}   # nothing written

    def test_existing_is_skipped_by_default_and_updated_on_request(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sup_svc.create_supplier(tid, {"name": "Acme", "email": "old@acme.com",
                                      "lead_time_days": 10})
        text = "name,email,lead_time_days\nacme,new@acme.com,\n"
        r = client.post(f"{BASE}/suppliers/import", files=_csv(text), headers=analyst_headers)
        assert r.json()["data"]["skipped_existing"] == 1
        assert _suppliers(tid)["Acme"]["email"] == "old@acme.com"

        r = client.post(f"{BASE}/suppliers/import", files=_csv(text),
                        data={"on_existing": "update"}, headers=analyst_headers)
        assert r.json()["data"]["updated"] == 1
        row = _suppliers(tid)["Acme"]
        assert row["email"] == "new@acme.com"
        assert row["lead_time_days"] == 10               # blank cell never erases
        assert len(_suppliers(tid)) == 1

    def test_deactivated_supplier_is_reported_never_reactivated(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        gone = sup_svc.create_supplier(tid, {"name": "Dormido"})
        sup_svc.delete_supplier(tid, gone["id"])
        r = client.post(f"{BASE}/suppliers/import",
                        files=_csv("name\nDormido\nVivo\n"), headers=analyst_headers)
        d = r.json()["data"]
        assert d["created"] == 1
        assert [e["code"] for e in d["errors"]] == ["supplier_import_row_deactivated"]
        assert _suppliers(tid)["Dormido"]["active"] is False

    def test_file_that_repeats_a_supplier_reports_the_duplicate(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        text = "name,email,phone\nAcme,,+50688887777\nACME,a@acme.com,\nAcme ,,\n"
        r = client.post(f"{BASE}/suppliers/import", files=_csv(text), headers=analyst_headers)
        d = r.json()["data"]
        assert d["created"] == 1 and d["duplicate_rows"] == 2
        row = _suppliers(tid)["Acme"]
        assert (row["email"], row["phone"]) == ("a@acme.com", "+50688887777")   # field-wise merge
        event = query_one(
            "SELECT action FROM activity_logs WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 1",
            (tid,))
        assert event["action"] == "data.suppliers_import_partial"

    def test_concurrent_imports_of_the_same_file_create_each_supplier_once(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        text = "name\n" + "\n".join(f"Sup{i}" for i in range(20)) + "\n"
        results = []

        def go():
            r = client.post(f"{BASE}/suppliers/import", files=_csv(text),
                            headers=analyst_headers)
            results.append(r)

        threads = [threading.Thread(target=go) for _ in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        assert sum(r.json()["data"]["created"] for r in results) == 20
        assert len(_suppliers(tid)) == 20


class TestSupplierImportHostileInput:
    def test_nul_row_is_rejected_by_name_and_the_rest_imports(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        r = client.post(f"{BASE}/suppliers/import",
                        files=_csv("name,notes\nGood,ok\nBa\x00d,x\nAlso Good,\n"),
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert [e["code"] for e in d["errors"]] == ["bulk_import_row_has_nul"]
        assert d["errors"][0]["row"] == 3
        assert set(_suppliers(tid)) == {"Good", "Also Good"}
        assert not any("\x00" in n for n in _suppliers(tid))

    def test_blank_rows_are_skipped_and_counted_not_errors(
        self, client, analyst_headers, test_tenant
    ):
        r = client.post(f"{BASE}/suppliers/import",
                        files=_csv("name,email\nA,\n,\n  ,  \nB,\n"),
                        headers=analyst_headers)
        d = r.json()["data"]
        assert d["created"] == 2 and d["blank_rows"] == 2 and d["error_count"] == 0

    def test_wrong_columns_name_the_missing_field(self, client, analyst_headers, test_tenant):
        r = client.post(f"{BASE}/suppliers/import",
                        files=_csv("foo,bar\n1,2\n"), headers=analyst_headers)
        assert r.status_code == 422
        assert r.json()["error_code"] == "bulk_import_missing_columns"
        assert query("SELECT 1 FROM suppliers WHERE tenant_id=%s", (test_tenant["id"],)) == []

    def test_all_rows_invalid_is_a_422_with_the_row_errors(
        self, client, analyst_headers, test_tenant
    ):
        r = client.post(f"{BASE}/suppliers/import",
                        files=_csv("name,email\nA,nope\nB,also nope\n"),
                        headers=analyst_headers)
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "bulk_import_no_valid_rows"
        assert body["error_params"]["rejected"] == 2

    def test_out_of_range_and_non_numeric_cells_are_row_errors(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        text = ("name,lead_time_days\nOk,5\nHuge,99999\nText,soon\nNeg,-3\nFrac,2.5\n")
        r = client.post(f"{BASE}/suppliers/import", files=_csv(text), headers=analyst_headers)
        d = r.json()["data"]
        assert set(_suppliers(tid)) == {"Ok"}
        assert {e["row"]: e["code"] for e in d["errors"]} == {
            3: "bulk_import_row_out_of_range", 4: "bulk_import_row_not_a_number",
            5: "bulk_import_row_out_of_range", 6: "bulk_import_row_not_a_number",
        }

    def test_empty_file_garbage_and_wrong_type(self, client, analyst_headers, test_tenant):
        r = client.post(f"{BASE}/suppliers/import", files=_csv(""), headers=analyst_headers)
        assert (r.status_code, r.json()["error_code"]) == (422, "import_empty_file")
        r = client.post(f"{BASE}/suppliers/import",
                        files={"file": ("x.pdf", b"%PDF-1.4 garbage", "application/pdf")},
                        headers=analyst_headers)
        assert (r.status_code, r.json()["error_code"]) == (422, "import_unsupported_file_type")
        r = client.post(f"{BASE}/suppliers/import",
                        files={"file": ("x.xlsx", b"this is not a workbook", "application/zip")},
                        headers=analyst_headers)
        assert (r.status_code, r.json()["error_code"]) == (422, "inventory_import_unreadable_file")
        assert query("SELECT 1 FROM suppliers WHERE tenant_id=%s", (test_tenant["id"],)) == []

    def test_oversized_file_is_refused_before_parsing(
        self, client, analyst_headers, test_tenant, monkeypatch
    ):
        from backend.config import settings
        monkeypatch.setattr(settings, "testing_mode", False)
        monkeypatch.setattr(settings, "max_upload_size_mb", 1)
        # Over the infrastructure ceiling AND the plan's: either refusal is a
        # clean 4xx before a single row is parsed.
        big = b"name\n" + (b"Supplier padded name............................\n" * 40_000)
        assert len(big) > 1024 * 1024
        r = client.post(f"{BASE}/suppliers/import", files={"file": ("big.csv", big, "text/csv")},
                        headers=analyst_headers)
        assert r.status_code == 413, r.text
        assert r.json()["error_code"] == "import_file_too_large"
        assert query("SELECT 1 FROM suppliers WHERE tenant_id=%s", (test_tenant["id"],)) == []

    def test_free_plan_upload_ceiling_uses_the_plan_limit_error(
        self, client, analyst_headers, test_tenant, monkeypatch
    ):
        from backend.config import settings
        monkeypatch.setattr(settings, "testing_mode", False)
        big = b"name\n" + (b"x" * 59 + b"\n") * 450_000          # > 25 MB
        r = client.post(f"{BASE}/suppliers/import", files={"file": ("big.csv", big, "text/csv")},
                        headers=analyst_headers)
        assert r.status_code == 403, r.text
        assert "limit" in r.text.lower()

    def test_row_cap_stops_a_file_of_many_tiny_rows(
        self, client, analyst_headers, test_tenant, monkeypatch
    ):
        from backend.inventory import bulk_import
        monkeypatch.setattr(bulk_import, "MAX_SUPPLIER_ROWS", 5)
        text = "name\n" + "\n".join(f"S{i}" for i in range(6)) + "\n"
        r = client.post(f"{BASE}/suppliers/import", files=_csv(text), headers=analyst_headers)
        assert (r.status_code, r.json()["error_code"]) == (422, "import_too_many_rows")
        assert query("SELECT 1 FROM suppliers WHERE tenant_id=%s", (test_tenant["id"],)) == []

    def test_formula_and_long_text_cells_are_data_not_code(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        r = client.post(
            f"{BASE}/suppliers/import",
            files=_csv(f"name,notes\n=HYPERLINK(\"http://x\"),ok\nLong,{'n' * 3000}\n"),
            headers=analyst_headers)
        d = r.json()["data"]
        assert d["created"] == 1
        assert [e["code"] for e in d["errors"]] == ["bulk_import_row_text_too_long"]
        # Stored verbatim; the exports are the ones that neutralise formulas.
        assert '=HYPERLINK("http://x")' in _suppliers(tid)


class TestSupplierImportPermissions:
    def test_viewer_is_denied_and_nothing_is_written(
        self, client, viewer_headers, test_tenant
    ):
        for path in ("/suppliers/import", "/suppliers/import/preview"):
            r = client.post(f"{BASE}{path}", files=_csv("name\nNope\n"), headers=viewer_headers)
            assert r.status_code == 403, (path, r.text)
        assert query("SELECT 1 FROM suppliers WHERE tenant_id=%s", (test_tenant["id"],)) == []

    def test_analyst_succeeds(self, client, analyst_headers, test_tenant):
        r = client.post(f"{BASE}/suppliers/import", files=_csv("name\nYes\n"),
                        headers=analyst_headers)
        assert r.status_code == 200
        assert "Yes" in _suppliers(test_tenant["id"])

    def test_viewer_can_download_the_template(self, client, viewer_headers):
        assert client.get(f"{BASE}/suppliers/import/template",
                          headers=viewer_headers).status_code == 200


# ═══════════════════════════════ Purchase orders ═════════════════════════════

@pytest.fixture
def catalogue(test_tenant):
    tid = test_tenant["id"]
    sup_svc.create_supplier(tid, {"name": "Distribuidora Sur"})
    sup_svc.create_supplier(tid, {"name": "Importadora Andina"})
    stock_svc.upsert_stock(tid, "SKU001", {"display_name": "Agua 600ml", "unit_cost": 2.0})
    stock_svc.upsert_stock(tid, "SKU002", {"display_name": "Jugo Naranja", "unit_cost": 4.0})
    stock_svc.upsert_stock(tid, "SKU003", {"display_name": "Cafe Molido", "unit_cost": 9.5})
    stock_svc.upsert_stock(tid, "SKU004", {"display_name": "Jugo Naranja"})   # same name
    return tid


class TestPOTemplates:
    def test_template_csv_and_xlsx_download(self, client, analyst_headers):
        r = client.get(f"{BASE}/po/import/template", headers=analyst_headers)
        assert r.content.startswith("﻿order_ref,supplier,sku,qty".encode("utf-8"))
        x = client.get(f"{BASE}/po/import/template?format=xlsx", headers=analyst_headers)
        assert x.content[:2] == b"PK"

    def test_template_round_trip_creates_two_orders_with_consecutive_numbers(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        tpl = client.get(f"{BASE}/po/import/template", headers=analyst_headers)
        r = client.post(f"{BASE}/po/import",
                        files={"file": ("t.csv", tpl.content, "text/csv")},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["created_orders"] == 2 and d["error_count"] == 0
        orders = _orders(tid)
        assert [o["po_number"] for o in orders] == [1, 2]
        assert [d_["po_number"] for d_ in d["orders"]] == ["OC-000001", "OC-000002"]
        first, second = orders
        assert first["source"] == "manual" and first["session_id"] is None
        assert first["sku_count"] == 2 and float(first["total_units"]) == 168.0
        assert float(first["total_value"]) == 120 * 3.5 + 48 * 7.25
        items = _items(first["id"])
        assert [(i["sku"], i["supplier"], float(i["final_qty"])) for i in items] == [
            ("SKU001", "Distribuidora Sur", 120.0), ("SKU002", "Distribuidora Sur", 48.0)]
        # PO-1002's cost cell is blank -> the stock card's cost is used.
        assert float(_items(second["id"])[0]["unit_cost"]) == 9.5

    def test_numbering_continues_after_an_existing_order(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        sup = sup_svc.get_supplier_by_name(tid, "Distribuidora Sur")
        client.post(f"{BASE}/po", json={"supplier_id": sup["id"],
                                        "lines": [{"sku": "SKU001", "qty": 1}]},
                    headers=analyst_headers)
        r = client.post(f"{BASE}/po/import",
                        files=_csv("supplier,sku,qty\nDistribuidora Sur,SKU002,3\n"),
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert [o["po_number"] for o in _orders(tid)] == [1, 2]


class TestPOImport:
    def test_unresolved_references_are_reported_per_row_and_the_rest_imports(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        text = (
            "order_ref,supplier,sku,qty,unit_cost\n"
            "A,Distribuidora Sur,SKU001,10,2.5\n"
            "A,Proveedor Fantasma,SKU001,10,2.5\n"      # row 3 unknown supplier
            "A,Distribuidora Sur,NOPE,5,1\n"            # row 4 unknown sku
            "A,Distribuidora Sur,Jugo Naranja,5,1\n"    # row 5 ambiguous product name
            "A,Distribuidora Sur,Cafe Molido,2,\n"      # product by name (unique)
        )
        r = client.post(f"{BASE}/po/import", files=_csv(text), headers=analyst_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["created_orders"] == 1 and d["error_count"] == 3
        assert {e["row"]: e["code"] for e in d["errors"]} == {
            3: "po_import_row_unknown_supplier", 4: "po_import_row_unknown_sku",
            5: "po_import_row_ambiguous_sku"}
        items = _items(_orders(tid)[0]["id"])
        assert [i["sku"] for i in items] == ["SKU001", "SKU003"]
        assert float(items[1]["unit_cost"]) == 9.5       # blank cost -> stock card

    def test_grouping_by_reference_and_supplier_and_folding_duplicate_lines(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        text = (
            "order_ref,supplier,sku,qty\n"
            "X1,Distribuidora Sur,SKU001,10\n"
            "X1,Distribuidora Sur,SKU001,5\n"            # folded: qty 15
            "X1,Importadora Andina,SKU002,7\n"           # same ref, other supplier
            "X2,Distribuidora Sur,SKU003,1\n"
        )
        r = client.post(f"{BASE}/po/import", files=_csv(text), headers=analyst_headers)
        d = r.json()["data"]
        assert d["created_orders"] == 3 and d["folded_rows"] == 1
        by_supplier = {}
        for o in _orders(tid):
            by_supplier.setdefault(_items(o["id"])[0]["supplier"], []).append(o)
        assert len(by_supplier["Distribuidora Sur"]) == 2
        sur_first = by_supplier["Distribuidora Sur"][0]
        assert sur_first["sku_count"] == 1
        assert float(_items(sur_first["id"])[0]["final_qty"]) == 15.0

    def test_rows_without_a_reference_make_one_order_per_supplier(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        text = ("supplier,sku,qty\nDistribuidora Sur,SKU001,1\nDistribuidora Sur,SKU002,2\n"
                "Importadora Andina,SKU003,3\n")
        r = client.post(f"{BASE}/po/import", files=_csv(text), headers=analyst_headers)
        assert r.json()["data"]["created_orders"] == 2
        assert [o["sku_count"] for o in _orders(tid)] == [2, 1]

    def test_same_file_twice_returns_the_first_orders_and_writes_nothing(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        text = "order_ref,supplier,sku,qty\nR1,Distribuidora Sur,SKU001,4\n"
        first = client.post(f"{BASE}/po/import", files=_csv(text), headers=analyst_headers)
        assert first.json()["data"]["created_orders"] == 1
        pv = client.post(f"{BASE}/po/import/preview", files=_csv(text), headers=analyst_headers)
        assert pv.json()["data"]["already_imported_orders"] == 1
        again = client.post(f"{BASE}/po/import", files=_csv(text), headers=analyst_headers)
        d = again.json()["data"]
        assert (d["created_orders"], d["already_imported_orders"]) == (0, 1)
        assert len(_orders(tid)) == 1
        assert len(query("SELECT 1 FROM inventory_po_items WHERE tenant_id=%s", (tid,))) == 1

    def test_destination_warehouse_must_exist_and_is_stored_canonical(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        stock_svc.upsert_stock(tid, "SKU001", {"warehouse": "Norte", "current_stock": 1})
        text = ("supplier,sku,qty,warehouse\n"
                "Distribuidora Sur,SKU001,4,norte\n"
                "Distribuidora Sur,SKU002,4,Atlantida\n")
        r = client.post(f"{BASE}/po/import", files=_csv(text), headers=analyst_headers)
        d = r.json()["data"]
        assert [e["code"] for e in d["errors"]] == ["po_import_row_unknown_warehouse"]
        orders = _orders(tid)
        assert len(orders) == 1 and orders[0]["destination_warehouse"] == "Norte"
        # The typo did not mint a warehouse.
        assert query("SELECT 1 FROM warehouses WHERE tenant_id=%s AND name='Atlantida'",
                     (tid,)) == []

    def test_xlsx_upload_with_numeric_codes(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        sup_svc.create_supplier(tid, {"name": "Num Co"})
        stock_svc.upsert_stock(tid, "12345", {"display_name": "Tuerca"})
        r = client.post(
            f"{BASE}/po/import",
            files=_xlsx(["Orden", "Proveedor", "Codigo", "Cantidad", "Costo"],
                        [["OC-9", "Num Co", 12345, 10, 1.5]]),
            headers=analyst_headers)
        assert r.status_code == 200, r.text
        item = _items(_orders(tid)[0]["id"])[0]
        assert (item["sku"], float(item["final_qty"]), float(item["unit_cost"])) == (
            "12345", 10.0, 1.5)

    def test_preview_writes_nothing_and_lists_orders(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        text = ("order_ref,supplier,sku,qty,unit_cost\n"
                "P1,Distribuidora Sur,SKU001,10,2\nP1,Distribuidora Sur,SKU002,5,\n"
                "P2,Ghost,SKU001,1,1\n")
        r = client.post(f"{BASE}/po/import/preview", files=_csv(text), headers=analyst_headers)
        d = r.json()["data"]
        assert d["order_count"] == 1 and d["rejected_rows"] == 1
        assert d["orders"][0]["line_count"] == 2
        assert d["orders"][0]["total_value"] == 10 * 2 + 5 * 4.0
        assert d["issues"][0]["code"] == "po_import_row_unknown_supplier"
        assert _orders(tid) == []


class TestPOImportHostileInput:
    def test_nul_blank_and_bad_quantities_are_row_errors(
        self, client, analyst_headers, catalogue
    ):
        tid = catalogue
        text = (
            "supplier,sku,qty,unit_cost\n"
            "Distribuidora Sur,SKU001,5,1\n"
            "Distribuidora Sur,SKU0\x0002,5,1\n"        # row 3 NUL
            ",,,\n"                                     # blank
            "Distribuidora Sur,SKU002,0,1\n"            # row 5 zero qty
            "Distribuidora Sur,SKU002,-4,1\n"           # row 6 negative
            "Distribuidora Sur,SKU002,many,1\n"         # row 7 not a number
            "Distribuidora Sur,SKU002,1e30,1\n"         # row 8 exponent garbage
            "Distribuidora Sur,SKU002,5,cheap\n"        # row 9 bad cost
            "Distribuidora Sur,,5,1\n"                  # row 10 no sku
            ",SKU002,5,1\n"                             # row 11 no supplier
        )
        r = client.post(f"{BASE}/po/import", files=_csv(text), headers=analyst_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["blank_rows"] == 1 and d["created_orders"] == 1
        errs = {e["row"]: e["code"] for e in d["errors"]}
        assert errs[3] == "bulk_import_row_has_nul"
        assert errs[5] == errs[6] == "bulk_import_row_out_of_range"
        assert errs[7] == errs[8] == errs[9] == "bulk_import_row_not_a_number"
        assert (errs[10], errs[11]) == ("po_import_row_missing_sku",
                                        "po_import_row_missing_supplier")
        items = query("SELECT sku FROM inventory_po_items WHERE tenant_id=%s", (tid,))
        assert [i["sku"] for i in items] == ["SKU001"]

    def test_wrong_columns_blank_file_and_all_rows_unresolvable(
        self, client, analyst_headers, catalogue
    ):
        r = client.post(f"{BASE}/po/import", files=_csv("foo,bar\n1,2\n"),
                        headers=analyst_headers)
        assert (r.status_code, r.json()["error_code"]) == (422, "bulk_import_missing_columns")
        assert set(r.json()["error_params"]["fields"]) == {"supplier", "sku", "qty"}
        r = client.post(f"{BASE}/po/import", files=_csv("   \n"), headers=analyst_headers)
        assert r.json()["error_code"] == "import_empty_file"
        r = client.post(f"{BASE}/po/import",
                        files=_csv("supplier,sku,qty\nGhost,NOPE,1\n"), headers=analyst_headers)
        assert (r.status_code, r.json()["error_code"]) == (422, "bulk_import_no_valid_rows")
        assert r.json()["error_params"]["errors"][0]["code"] == "po_import_row_unknown_supplier"
        assert _orders(catalogue) == []

    def test_oversized_file_and_row_cap(
        self, client, analyst_headers, catalogue, monkeypatch
    ):
        from backend.config import settings
        from backend.inventory import bulk_import
        monkeypatch.setattr(bulk_import, "MAX_PO_ROWS", 3)
        text = "supplier,sku,qty\n" + "Distribuidora Sur,SKU001,1\n" * 4
        r = client.post(f"{BASE}/po/import", files=_csv(text), headers=analyst_headers)
        assert (r.status_code, r.json()["error_code"]) == (422, "import_too_many_rows")
        monkeypatch.setattr(settings, "testing_mode", False)
        monkeypatch.setattr(settings, "max_upload_size_mb", 1)
        big = b"supplier,sku,qty\n" + b"Distribuidora Sur,SKU001,1,padding.....\n" * 40_000
        r = client.post(f"{BASE}/po/import", files={"file": ("b.csv", big, "text/csv")},
                        headers=analyst_headers)
        assert (r.status_code, r.json()["error_code"]) == (413, "import_file_too_large")
        assert _orders(catalogue) == []

    def test_a_workbook_named_csv_is_still_read_as_a_workbook(
        self, client, analyst_headers, catalogue
    ):
        pytest.importorskip("openpyxl")
        f = _xlsx(["supplier", "sku", "qty"], [["Distribuidora Sur", "SKU001", 2]])
        name, content, ctype = f["file"]
        r = client.post(f"{BASE}/po/import", files={"file": ("renamed.csv", content, ctype)},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert len(_orders(catalogue)) == 1


class TestPOImportPermissions:
    def test_viewer_is_denied_and_no_order_exists(
        self, client, viewer_headers, catalogue
    ):
        text = "supplier,sku,qty\nDistribuidora Sur,SKU001,2\n"
        for path in ("/po/import", "/po/import/preview"):
            r = client.post(f"{BASE}{path}", files=_csv(text), headers=viewer_headers)
            assert r.status_code == 403, (path, r.text)
        assert _orders(catalogue) == []

    def test_analyst_succeeds(self, client, analyst_headers, catalogue):
        r = client.post(f"{BASE}/po/import",
                        files=_csv("supplier,sku,qty\nDistribuidora Sur,SKU001,2\n"),
                        headers=analyst_headers)
        assert r.status_code == 200
        assert len(_orders(catalogue)) == 1
