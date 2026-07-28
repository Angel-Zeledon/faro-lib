"""
POST /inventory/bulk and /inventory/bulk/preview — the tolerant stock importer
(onboarding-friction plan #1, phase 4).

The question every test here answers is the product one: can a LatAm ERP's
stock export be imported WITHOUT hand-editing its headers? So the fixtures are
deliberately not our template: 'Código;Existencia;Costo Unitario', decimal
commas, thousands dots, a UTF-8 BOM and an .xlsx.
"""

import io
from uuid import uuid4

import pytest

from backend.db.connection import query_one


# ── Fixture override: @pytest.local fails email-validator — use @example.com ──

@pytest.fixture
def registered_user(test_tenant):
    from backend.users import service as user_svc
    email = f"admin-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(
        tenant_id=test_tenant["id"], email=email, password=password,
        role="admin", full_name="Test Admin",
    )
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def auth_headers(client, registered_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": registered_user["email"], "password": registered_user["password"],
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _ok(resp, code=200):
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"
    return resp.json()["data"]


def _sku():
    return f"SKU-{uuid4().hex[:8].upper()}"


def _post(client, headers, content: bytes, filename="stock.csv", mapping=None):
    files = {"file": (filename, content, "application/octet-stream")}
    data = {"mapping": mapping} if mapping else None
    return client.post("/api/v1/inventory/bulk", headers=headers, files=files, data=data)


def _preview(client, headers, content: bytes, filename="stock.csv", mapping=None):
    files = {"file": (filename, content, "application/octet-stream")}
    data = {"mapping": mapping} if mapping else None
    return client.post("/api/v1/inventory/bulk/preview", headers=headers,
                       files=files, data=data)


def _row(sku: str):
    return query_one(
        "SELECT current_stock, min_stock, lead_time_days, unit_cost, sale_price, "
        "moq, supplier, display_name, category, brand, warehouse "
        "FROM inventory_stock WHERE sku = %s", (sku,))


# ── The headline case: a real ERP export ──────────────────────────────────────

class TestErpExportImportsUnedited:

    def test_spanish_headers_semicolons_and_decimal_commas(self, client, auth_headers):
        """
        The file this endpoint used to reject outright: Spanish headers, ';'
        separator, '1.250,75' money and a BOM. Nothing about it matches the
        template we ship, and it must still import — with the VALUES read
        correctly, which is the part a lenient parser gets wrong.
        """
        sku = _sku()
        csv_text = (
            "Código;Descripción;Existencia;Costo Unitario;Precio Venta;Proveedor;Días de entrega\r\n"
            f"{sku};Agua 600ml;1.250;3,75;5,90;Distribuidora Sur;12\r\n"
        )
        content = b"\xef\xbb\xbf" + csv_text.encode("utf-8")

        data = _ok(_post(client, auth_headers, content))
        assert data["imported"] == 1

        row = _row(sku)
        assert row is not None, "the ERP export did not import"
        assert float(row["current_stock"]) == 1250.0      # '1.250' is thousands
        assert float(row["unit_cost"]) == 3.75            # '3,75' is a decimal
        assert float(row["sale_price"]) == 5.90
        assert row["supplier"] == "Distribuidora Sur"
        assert row["display_name"] == "Agua 600ml"
        assert int(row["lead_time_days"]) == 12

    def test_latin1_encoded_export(self, client, auth_headers):
        """Legacy ERPs export latin-1, not UTF-8."""
        sku = _sku()
        content = (
            "Código;Existencia\r\n"
            f"{sku};45\r\n"
        ).encode("latin-1")
        data = _ok(_post(client, auth_headers, content))
        assert data["imported"] == 1
        assert float(_row(sku)["current_stock"]) == 45.0

    def test_currency_symbols_and_spaced_thousands(self, client, auth_headers):
        sku = _sku()
        csv_text = (
            "codigo,existencia,costo\n"
            f"{sku},\"1 250\",\"₡ 3.499,50\"\n"
        )
        data = _ok(_post(client, auth_headers, csv_text.encode("utf-8")))
        assert data["imported"] == 1
        row = _row(sku)
        assert float(row["current_stock"]) == 1250.0
        assert float(row["unit_cost"]) == 3499.50

    def test_xlsx_import(self, client, auth_headers):
        """The sales upload has always accepted Excel; this one refused it."""
        openpyxl = pytest.importorskip("openpyxl")
        sku = _sku()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Codigo", "Descripcion", "Existencia", "Costo Unitario", "Proveedor"])
        ws.append([sku, "Arroz 1kg", 320, 1.85, "Granos SA"])
        buf = io.BytesIO()
        wb.save(buf)

        data = _ok(_post(client, auth_headers, buf.getvalue(), filename="inventario.xlsx"))
        assert data["imported"] == 1
        assert data["format"] == "excel"

        row = _row(sku)
        assert float(row["current_stock"]) == 320.0
        assert float(row["unit_cost"]) == pytest.approx(1.85)
        assert row["supplier"] == "Granos SA"
        assert row["display_name"] == "Arroz 1kg"

    def test_xlsx_numeric_product_codes_keep_their_digits(self, client, auth_headers):
        """
        A numeric code column with one blank cell is read as float by the
        spreadsheet layer, and str(12345.0) is '12345.0' — which would import
        every product under a second, wrong code.
        """
        openpyxl = pytest.importorskip("openpyxl")
        code = int(uuid4().int % 10_000_000)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Codigo", "Existencia"])
        ws.append([code, 10])
        ws.append([None, 5])       # blank cell -> the column becomes float
        buf = io.BytesIO()
        wb.save(buf)

        data = _ok(_post(client, auth_headers, buf.getvalue(), filename="codes.xlsx"))
        assert data["imported"] == 1
        assert _row(str(code)) is not None, "numeric product code was mangled on import"
        assert _row(f"{code}.0") is None

    def test_unreadable_excel_returns_structured_error(self, client, auth_headers):
        resp = _post(client, auth_headers, b"not really a spreadsheet",
                     filename="broken.xlsx")
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "inventory_import_unreadable_file"

    def test_canonical_template_still_imports(self, client, auth_headers):
        """Regression: the tolerant path must not break the documented one."""
        sku = _sku()
        csv_text = f"sku,current_stock,lead_time_days,unit_cost\n{sku},80,7,2.5\n"
        data = _ok(_post(client, auth_headers, csv_text.encode("utf-8")))
        assert data["imported"] == 1
        row = _row(sku)
        assert float(row["current_stock"]) == 80.0
        assert int(row["lead_time_days"]) == 7
        assert float(row["unit_cost"]) == 2.5


# ── Explicit mapping from the wizard ──────────────────────────────────────────

class TestExplicitMapping:

    def test_mapping_overrides_detection(self, client, auth_headers):
        """
        Two plausible stock columns: the file's own 'Existencia' is the
        theoretical figure and 'Disponible' the real one. The user picks in
        the wizard and their pick must win over our guess.
        """
        sku = _sku()
        csv_text = (
            "codigo,existencia,disponible\n"
            f"{sku},999,42\n"
        )
        data = _ok(_post(client, auth_headers, csv_text.encode("utf-8"),
                         mapping='{"sku": "codigo", "current_stock": "disponible"}'))
        assert data["imported"] == 1
        assert float(_row(sku)["current_stock"]) == 42.0, (
            "the user's column pick was ignored in favour of the auto-detected one")
        assert data["mapping"]["current_stock"] == "disponible"

    def test_mapping_to_unknown_column_is_rejected(self, client, auth_headers):
        sku = _sku()
        resp = _post(client, auth_headers,
                     f"codigo,existencia\n{sku},5\n".encode("utf-8"),
                     mapping='{"current_stock": "no_such_column"}')
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "inventory_import_unknown_column"
        assert _row(sku) is None, "a rejected mapping must not write anything"

    def test_malformed_mapping_is_rejected(self, client, auth_headers):
        resp = _post(client, auth_headers, b"codigo,existencia\nX,5\n", mapping="{not json")
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "inventory_import_bad_mapping"

    def test_empty_mapping_value_skips_that_field(self, client, auth_headers):
        """Sending "" for a field means 'do not import this column'."""
        sku = _sku()
        csv_text = f"codigo,existencia,costo\n{sku},10,7.5\n"
        _ok(_post(client, auth_headers, csv_text.encode("utf-8"),
                  mapping='{"unit_cost": ""}'))
        row = _row(sku)
        assert float(row["current_stock"]) == 10.0
        assert row["unit_cost"] is None, "a de-selected column was imported anyway"


# ── Preview (the mapping wizard's data source) ────────────────────────────────

class TestPreview:

    def test_preview_reports_mapping_and_does_not_write(self, client, auth_headers):
        sku = _sku()
        csv_text = (
            "Código;Existencia;Costo Unitario;Columna Rara\r\n"
            f"{sku};10;2,50;xyz\r\n"
        )
        data = _ok(_preview(client, auth_headers, csv_text.encode("utf-8")))

        assert data["format"] == "csv"
        assert data["separator"] == ";"
        assert data["mapping"]["sku"] == "Código"
        assert data["mapping"]["current_stock"] == "Existencia"
        assert data["mapping"]["unit_cost"] == "Costo Unitario"
        assert "Columna Rara" in data["unmapped_columns"]
        assert data["missing_required"] == []
        assert data["importable_rows"] == 1
        assert data["sample_rows"][0]["unit_cost"] == 2.5

        assert _row(sku) is None, "preview must be a dry run — it wrote to the DB"

    def test_preview_flags_missing_sku_column(self, client, auth_headers):
        data = _ok(_preview(client, auth_headers, b"existencia,costo\n10,2\n"))
        assert data["missing_required"] == ["sku"]
        assert data["importable_rows"] == 0

    def test_preview_groups_row_issues(self, client, auth_headers):
        rows = "\n".join(f"{_sku()},N/D" for _ in range(4))
        data = _ok(_preview(client, auth_headers,
                            f"codigo,existencia\n{rows}\n".encode("utf-8")))
        assert data["rejected_rows"] == 4
        issue = next(i for i in data["issues"]
                     if i["code"] == "inventory_import_row_not_a_number")
        assert issue["count"] == 4
        assert issue["column"] == "current_stock"
        assert len(issue["samples"]) <= 5

    def test_preview_viewer_denied_and_writes_nothing(self, client, viewer_headers):
        sku = _sku()
        resp = _preview(client, viewer_headers,
                        f"codigo,existencia\n{sku},9\n".encode("utf-8"))
        assert resp.status_code == 403
        assert query_one("SELECT 1 FROM inventory_stock WHERE sku = %s", (sku,)) is None


# ── Per-row errors carry a code, not English prose ────────────────────────────

class TestRowErrorsAreStructured:

    def test_non_numeric_cell_reports_code_and_params(self, client, auth_headers):
        """
        The 200 payload used to carry English prose the UI printed verbatim
        ("column 'moq' is not a number: 'abc'"). It now carries a stable code
        plus params; the English string stays only as a fallback.
        """
        good, bad = _sku(), _sku()
        csv_text = (
            "codigo,existencia,moq\n"
            f"{good},10,2\n"
            f"{bad},10,abc\n"
        )
        data = _ok(_post(client, auth_headers, csv_text.encode("utf-8")))

        assert data["imported"] == 1
        assert data["error_count"] == 1
        err = data["errors"][0]
        assert err["sku"] == bad
        assert err["code"] == "inventory_import_row_not_a_number"
        assert err["params"] == {"column": "moq", "value": "abc"}
        assert err["row"] == 3          # header is line 1

        assert _row(bad) is None, "the garbage row was imported anyway"
        assert float(_row(good)["current_stock"]) == 10.0

    def test_out_of_range_value_reports_its_own_code(self, client, auth_headers):
        good, bad = _sku(), _sku()
        csv_text = (
            "codigo,existencia\n"
            f"{good},10\n"
            f"{bad},-50\n"
        )
        data = _ok(_post(client, auth_headers, csv_text.encode("utf-8")))
        assert data["imported"] == 1
        err = data["errors"][0]
        assert err["code"] == "inventory_import_row_out_of_range"
        assert err["params"]["column"] == "current_stock"
        assert _row(bad) is None

    def test_whole_file_rejection_carries_the_columns_we_saw(self, client, auth_headers):
        resp = _post(client, auth_headers, b"existencia;costo\r\n10;2\r\n")
        assert resp.status_code == 422
        body = resp.json()
        assert body["error_code"] == "inventory_import_no_valid_rows"
        # The params are what lets the UI say "we found these columns and none
        # of them looks like a product code" instead of a bare failure.
        assert body["error_params"]["missing_required"] == ["sku"]
        assert "existencia" in body["error_params"]["columns"]


# ── Permissions (mutating endpoint) ───────────────────────────────────────────

class TestPermissions:

    def test_viewer_denied_and_state_unchanged(self, client, viewer_headers):
        sku = _sku()
        resp = _post(client, viewer_headers, f"codigo;existencia\n{sku};99\n".encode("utf-8"))
        assert resp.status_code == 403
        assert query_one("SELECT 1 FROM inventory_stock WHERE sku = %s", (sku,)) is None

    def test_analyst_succeeds_and_row_is_persisted(self, client, analyst_headers):
        sku = _sku()
        data = _ok(_post(client, analyst_headers,
                         f"codigo;existencia;costo unitario\n{sku};55;1,25\n".encode("utf-8")))
        assert data["imported"] == 1
        row = _row(sku)
        assert float(row["current_stock"]) == 55.0
        assert float(row["unit_cost"]) == 1.25


# ── Pure-function guards on the parser ────────────────────────────────────────

class TestNumberParsing:

    @pytest.mark.parametrize("raw,expected", [
        ("120", 120.0),
        # A lone separator before exactly 3 digits is genuinely ambiguous with
        # no file to compare against, so each is read the way its own notation
        # says: '1.250' is 1.25 and '1,250' is 1250. The file-level verdict
        # (test below) is what resolves it in a real import.
        ("1.250", 1.25),
        ("1,250", 1250.0),
        ("3,75", 3.75),             # LatAm decimal
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("₡ 3.499,50", 3499.50),
        ("$1,234.56", 1234.56),
        ("1 250", 1250.0),
        ("12,50 USD", 12.5),
        ("(50)", -50.0),
        ("50-", -50.0),
        ("0", 0.0),
    ])
    def test_parses(self, raw, expected):
        from backend.utils.stock_import import parse_number
        assert parse_number(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["N/D", "", "   ", "abc", "12abc", "not-a-date", None])
    def test_rejects(self, raw):
        from backend.utils.stock_import import parse_number
        assert parse_number(raw) is None

    def test_file_level_comma_verdict_resolves_the_ambiguous_cell(self):
        """
        '1,250' alone is unreadable — 1250 or 1.25? The file decides: a
        neighbour cell of '3,75' proves this file writes decimals with commas,
        so 1,250 is 1.25. Guessing per cell is how an importer turns a 1,25
        cost into 125.
        """
        from backend.utils.stock_import import has_decimal_comma, parse_number

        assert has_decimal_comma(["3,75", "1,250"]) is True
        assert parse_number("1,250", decimal_comma=True) == 1.25
        assert has_decimal_comma(["1,250", "2,500"]) is False
        assert parse_number("1,250", decimal_comma=False) == 1250.0
        # …and the mirror case: in a comma-decimal file the dot is thousands,
        # so '1.250' is 1250 there and 1.25 in a file with no such evidence.
        assert parse_number("1.250", decimal_comma=True) == 1250.0
        assert parse_number("1.250", decimal_comma=None) == 1.25


class TestHeaderDetection:

    def test_codigo_wins_sku_and_producto_becomes_the_name(self):
        from backend.utils.stock_import import detect_mapping

        mapping = detect_mapping(["Código", "Producto", "Existencia"])
        assert mapping["sku"] == "Código"
        assert mapping["display_name"] == "Producto"
        assert mapping["current_stock"] == "Existencia"

    def test_producto_alone_is_the_sku(self):
        from backend.utils.stock_import import detect_mapping

        mapping = detect_mapping(["Producto", "Stock"])
        assert mapping["sku"] == "Producto"

    def test_accents_case_and_punctuation_are_ignored(self):
        from backend.utils.stock_import import detect_mapping

        mapping = detect_mapping(["SKU", "COSTO_UNITARIO ($)", "Días de Entrega"])
        assert mapping["unit_cost"] == "COSTO_UNITARIO ($)"
        assert mapping["lead_time_days"] == "Días de Entrega"

    def test_a_column_is_never_mapped_to_two_fields(self):
        from backend.utils.stock_import import detect_mapping

        mapping = detect_mapping(["codigo", "existencia", "costo", "precio"])
        assert len(set(mapping.values())) == len(mapping)

    def test_unknown_columns_stay_unmapped(self):
        from backend.utils.stock_import import detect_mapping

        mapping = detect_mapping(["codigo", "columna rara", "otra cosa"])
        assert set(mapping.values()) == {"codigo"}
