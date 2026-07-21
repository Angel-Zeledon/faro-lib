"""
Inventory module integration tests.
Covers: stock CRUD, bulk import, status calculation, PO export.
All tests use isolated test_tenant + auth_headers fixtures.
"""

import csv
import io
import math
import pytest
from uuid import uuid4


# ── Fixture override: @pytest.local fails email-validator — use @example.com ──

@pytest.fixture
def registered_user(test_tenant):
    """Admin user with a valid domain (overrides conftest to avoid .local rejection)."""
    from backend.users import service as user_svc
    email = f"admin-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(
        tenant_id=test_tenant["id"],
        email=email,
        password=password,
        role="admin",
        full_name="Test Admin",
    )
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def auth_headers(client, registered_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": registered_user["email"],
        "password": registered_user["password"],
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ok(resp, code=200):
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"
    return resp.json()["data"]


def _sku():
    return f"SKU-{uuid4().hex[:8].upper()}"


# ── Stock CRUD ─────────────────────────────────────────────────────────────────

class TestStockCRUD:

    def test_list_stock_empty(self, client, auth_headers):
        resp = client.get("/api/v1/inventory/stock", headers=auth_headers)
        data = _ok(resp)
        assert isinstance(data, list)

    def test_upsert_creates_sku(self, client, auth_headers):
        from backend.db.connection import query_one
        sku = _sku()
        body = {
            "current_stock": 100,
            "lead_time_days": 14,
            "unit_cost": 2500.0,
            "moq": 10,
            "supplier": "Proveedor Test S.A.",
            "display_name": "Producto de prueba",
        }
        data = _ok(client.put(f"/api/v1/inventory/stock/{sku}", json=body, headers=auth_headers))
        assert data["sku"] == sku
        assert data["current_stock"] == 100
        assert data["lead_time_days"] == 14
        assert data["supplier"] == "Proveedor Test S.A."

        row = query_one(
            "SELECT current_stock, lead_time_days, unit_cost, supplier FROM inventory_stock WHERE sku = %s",
            (sku,),
        )
        assert row is not None, "Stock row was not persisted to the DB"
        assert float(row["current_stock"]) == 100
        assert row["lead_time_days"] == 14
        assert float(row["unit_cost"]) == 2500.0
        assert row["supplier"] == "Proveedor Test S.A."

    def test_upsert_updates_existing(self, client, auth_headers):
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 50}, headers=auth_headers)
        data = _ok(client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 200, "lead_time_days": 7}, headers=auth_headers))
        assert data["current_stock"] == 200
        assert data["lead_time_days"] == 7

    def test_get_existing_sku(self, client, auth_headers):
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 75}, headers=auth_headers)
        data = _ok(client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers))
        assert data["sku"] == sku
        assert data["current_stock"] == 75

    def test_get_missing_sku_returns_404(self, client, auth_headers):
        resp = client.get(f"/api/v1/inventory/stock/NOSUCHSKU-{uuid4().hex}", headers=auth_headers)
        assert resp.status_code == 404

    def test_patch_partial_update(self, client, auth_headers):
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 100, "lead_time_days": 20}, headers=auth_headers)
        data = _ok(client.patch(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 55}, headers=auth_headers))
        assert data["current_stock"] == 55
        assert data["lead_time_days"] == 20  # unchanged

    def test_patch_persists_catalog_fields(self, client, auth_headers, viewer_headers):
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"current_stock": 10, "lead_time_days": 7},
                   headers=auth_headers)
        payload = {"sale_price": 19.99, "category": "Bebidas",
                   "brand": "AguaPura", "unit_of_measure": "caja",
                   "barcode": "7501234567890"}
        # viewer denied + state unchanged
        vr = client.patch(f"/api/v1/inventory/stock/{sku}", json=payload, headers=viewer_headers)
        assert vr.status_code == 403
        from backend.db.connection import query_one
        row0 = query_one("SELECT sale_price FROM inventory_stock WHERE sku = %s", (sku,))
        assert row0["sale_price"] is None
        # analyst succeeds + DB reflects every field
        r = client.patch(f"/api/v1/inventory/stock/{sku}", json=payload, headers=auth_headers)
        assert r.status_code == 200
        row = query_one(
            "SELECT sale_price, category, brand, unit_of_measure, barcode "
            "FROM inventory_stock WHERE sku = %s", (sku,))
        assert float(row["sale_price"]) == 19.99
        assert row["category"] == "Bebidas"
        assert row["brand"] == "AguaPura"
        assert row["unit_of_measure"] == "caja"
        assert row["barcode"] == "7501234567890"

    def test_delete_sku(self, client, auth_headers):
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 10}, headers=auth_headers)
        resp = client.delete(f"/api/v1/inventory/stock/{sku}", headers=auth_headers)
        assert resp.status_code == 204
        assert client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers).status_code == 404

    def test_upsert_negative_stock_rejected(self, client, auth_headers):
        sku = _sku()
        resp = client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": -10}, headers=auth_headers)
        assert resp.status_code == 422

    def test_upsert_lead_time_out_of_range_rejected(self, client, auth_headers):
        sku = _sku()
        resp = client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 10, "lead_time_days": 400}, headers=auth_headers)
        assert resp.status_code == 422

    def test_tenant_isolation(self, client, auth_headers, registered_user):
        """SKU created by tenant A must not be visible to tenant B."""
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 99}, headers=auth_headers)

        from backend.db.connection import execute
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc

        # Create a second tenant + user
        tenant_b = create_tenant(f"tenant-b-{uuid4().hex[:6]}")
        email_b  = f"b-{uuid4().hex[:8]}@example.com"
        user_b   = user_svc.create_user(tenant_b["id"], email_b, "TestPass123!", "admin", "User B")
        user_svc.mark_verified(tenant_b["id"], user_b["id"])

        try:
            resp_b = client.post("/api/v1/auth/login", json={"email": email_b, "password": "TestPass123!"})
            assert resp_b.status_code == 200
            token_b = resp_b.json()["data"]["access_token"]
            headers_b = {"Authorization": f"Bearer {token_b}"}

            resp = client.get(f"/api/v1/inventory/stock/{sku}", headers=headers_b)
            assert resp.status_code == 404, "Tenant B should not see Tenant A's SKU"
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (tenant_b["id"],))


# ── Bulk import ────────────────────────────────────────────────────────────────

class TestBulkImport:

    def _make_csv(self, rows: list[dict]) -> bytes:
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return buf.getvalue().encode("utf-8")

    def test_bulk_import_creates_skus(self, client, auth_headers):
        skus = [_sku() for _ in range(3)]
        csv_bytes = self._make_csv([
            {"sku": s, "current_stock": 100 * (i + 1), "lead_time_days": 10, "supplier": "Prov A"}
            for i, s in enumerate(skus)
        ])
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("stock.csv", csv_bytes, "text/csv")},
        )
        data = _ok(resp)
        assert data["imported"] == 3

        # Verify each was created
        for i, sku in enumerate(skus):
            row = client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers).json()["data"]
            assert row["current_stock"] == 100 * (i + 1)

    def test_bulk_import_persists_catalog_and_supplier(self, client, auth_headers):
        sku = _sku()
        csv_text = (
            "sku,current_stock,sale_price,category,brand,unit_of_measure,barcode,supplier,notes\n"
            f"{sku},50,12.5,Lacteos,Alpina,litro,7700000000001,Distribuidora Sur,fragil\n"
        )
        r = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", csv_text.encode("utf-8"), "text/csv")},
            headers=auth_headers,
        )
        assert r.status_code == 200
        from backend.db.connection import query_one
        row = query_one(
            "SELECT current_stock, sale_price, category, brand, unit_of_measure, "
            "barcode, supplier, notes FROM inventory_stock WHERE sku = %s", (sku,))
        assert float(row["current_stock"]) == 50
        assert float(row["sale_price"]) == 12.5
        assert row["category"] == "Lacteos"
        assert row["brand"] == "Alpina"
        assert row["unit_of_measure"] == "litro"
        assert row["barcode"] == "7700000000001"
        assert row["supplier"] == "Distribuidora Sur"   # regression: was silently dropped
        assert row["notes"] == "fragil"

    def test_bulk_import_viewer_denied(self, client, viewer_headers):
        """POST /bulk is mutating: a viewer is denied and no row is created."""
        sku = _sku()
        csv_bytes = self._make_csv([{"sku": sku, "current_stock": 99, "lead_time_days": 7}])
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=viewer_headers,
            files={"file": ("stock.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 403
        from backend.db.connection import query_one
        assert query_one("SELECT 1 FROM inventory_stock WHERE sku = %s", (sku,)) is None

    def test_bulk_import_handles_bom(self, client, auth_headers):
        """Excel CSVs exported with UTF-8 BOM should be parsed correctly."""
        sku = _sku()
        # UTF-8 BOM prefix
        csv_bytes = b"\xef\xbb\xbf" + f"sku,current_stock\n{sku},42\n".encode("utf-8")
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("stock.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 200
        row = client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers).json()["data"]
        assert row["current_stock"] == 42

    def test_bulk_import_empty_csv_returns_422(self, client, auth_headers):
        csv_bytes = b"current_stock,lead_time\n100,15\n"  # no sku column
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("bad.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 422

    def test_bulk_import_rejects_negative_quantities(self, client, auth_headers):
        """CSV import must enforce the same ge=0 constraints as the direct
        PUT/PATCH endpoints — a regression guard for the bug where bulk_import
        parsed rows manually and never validated them, letting negative
        current_stock/unit_cost/moq into the DB."""
        good_sku, bad_sku = _sku(), _sku()
        csv_bytes = self._make_csv([
            {"sku": good_sku, "current_stock": 50, "min_stock": 0,
             "unit_cost": 1.0, "moq": 1},
            {"sku": bad_sku, "current_stock": -50, "min_stock": -5,
             "unit_cost": -2.5, "moq": -1},
        ])
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("stock.csv", csv_bytes, "text/csv")},
        )
        data = _ok(resp)
        assert data["imported"] == 1

        good_row = client.get(f"/api/v1/inventory/stock/{good_sku}", headers=auth_headers).json()["data"]
        assert good_row["current_stock"] == 50

        bad_resp = client.get(f"/api/v1/inventory/stock/{bad_sku}", headers=auth_headers)
        assert bad_resp.status_code == 404

    def test_template_csv_has_canonical_header(self, client, auth_headers):
        r = client.get("/api/v1/inventory/template.csv", headers=auth_headers)
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "inventory_template.csv" in r.headers["content-disposition"]
        text = r.content.decode("utf-8-sig")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        header = lines[0].split(",")
        expected = ["sku", "warehouse", "display_name", "category", "brand", "unit_of_measure",
                    "barcode", "current_stock", "min_stock", "lead_time_days",
                    "unit_cost", "sale_price", "moq", "supplier", "notes"]
        assert header == expected
        assert len(lines) >= 2          # header + at least one example row
        assert len(lines[1].split(",")) == len(expected)   # example row is parseable


class TestDatasetSyncSanitization:
    """
    sync_stock_from_dataset (backend/inventory/service.py) is the Quick Start
    upload path that seeds inventory_stock straight from a training dataframe
    (backend/workers/runner.py). Unlike PUT /stock, PATCH /stock and POST /bulk
    (see test_bulk_import_rejects_negative_quantities above), it never went
    through StockUpsert/StockPatch's ge=0/ge=1 bounds — it parses whatever a
    sales-history column happens to contain and hands it straight to
    upsert_stock. A stray lead_time_days=0 collapses every _calc_signal
    threshold (lead_time*0.5/1.2/3 all become 0), permanently misreporting the
    SKU as SOBRESTOCK regardless of real coverage and silently hiding a
    stockout risk; a stray negative current_stock corrupts the reorder-point
    math the same way bulk_import's ge=0 guard exists to prevent.
    """

    def test_zero_lead_time_from_dataset_is_rejected(self, client, test_tenant):
        import pandas as pd
        from backend.inventory.service import sync_stock_from_dataset
        from backend.db.connection import query_one

        sku = _sku()
        df = pd.DataFrame({
            "sku":            [sku],
            "fecha":          ["2026-01-01"],
            "current_stock":  [40],
            "lead_time_days": [0],
        })
        n = sync_stock_from_dataset(test_tenant["id"], df, group_col="sku", date_col="fecha")
        assert n == 1

        row = query_one(
            "SELECT current_stock, lead_time_days FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s",
            (test_tenant["id"], sku),
        )
        assert row is not None
        assert float(row["current_stock"]) == 40   # the valid field must still be saved
        assert row["lead_time_days"] != 0, (
            "lead_time_days=0 from a dataset column was persisted — this collapses "
            "_calc_signal's thresholds to 0 and forces every coverage value into "
            "SOBRESTOCK, permanently hiding real stockout risk for this SKU"
        )

    def test_negative_current_stock_from_dataset_is_rejected(self, client, test_tenant):
        import pandas as pd
        from backend.inventory.service import sync_stock_from_dataset
        from backend.db.connection import query_one

        sku = _sku()
        df = pd.DataFrame({
            "sku":           [sku],
            "fecha":         ["2026-01-01"],
            "current_stock": [-25],
            "supplier":      ["Prov Dataset"],
        })
        n = sync_stock_from_dataset(test_tenant["id"], df, group_col="sku", date_col="fecha")
        assert n == 1

        row = query_one(
            "SELECT current_stock, supplier FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s",
            (test_tenant["id"], sku),
        )
        assert row is not None
        assert row["supplier"] == "Prov Dataset"   # other valid fields still saved
        assert float(row["current_stock"]) >= 0, (
            "negative current_stock from a dataset column was persisted, unlike "
            "every other write path (PUT/PATCH/bulk CSV) which enforces ge=0"
        )

    def test_negative_moq_from_dataset_is_rejected(self, client, test_tenant):
        import pandas as pd
        from backend.inventory.service import sync_stock_from_dataset
        from backend.db.connection import query_one

        sku = _sku()
        df = pd.DataFrame({
            "sku":           [sku],
            "fecha":         ["2026-01-01"],
            "current_stock": [10],
            "moq":           [-3],
        })
        n = sync_stock_from_dataset(test_tenant["id"], df, group_col="sku", date_col="fecha")
        assert n == 1

        row = query_one(
            "SELECT moq FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (test_tenant["id"], sku),
        )
        assert row is not None
        assert float(row["moq"]) >= 1, (
            "negative moq from a dataset column was persisted — downstream "
            "_calc_recommended treats moq<=0 as 'no rounding', but a negative "
            "moq stored on the SKU is nonsense data that should never land in the DB"
        )


class TestDirectUpsertSanitization:
    """
    upsert_stock/bulk_upsert (backend/inventory/service.py) called directly —
    not through PUT/PATCH /stock (which reject out-of-range values via
    Pydantic's ge=0/ge=1) — is the path receive_po and any other in-process
    caller uses. Before this fix it had no numeric floor at all, so a
    0/negative lead_time_days/current_stock/moq passed straight through
    could corrupt the reorder-point math exactly like the unvalidated
    dataset-sync column did (see TestDatasetSyncSanitization above). This
    mirrors the same _DATASET_STOCK_MIN floor now applied inside
    upsert_stock itself, so the guard holds regardless of caller.
    """

    def test_direct_upsert_zero_lead_time_dropped(self, test_tenant):
        from backend.inventory.service import upsert_stock
        from backend.db.connection import query_one

        sku = _sku()
        row = upsert_stock(test_tenant["id"], sku, {
            "current_stock": 40,
            "lead_time_days": 0,
        })
        assert float(row["current_stock"]) == 40  # valid field still saved

        db_row = query_one(
            "SELECT lead_time_days FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (test_tenant["id"], sku),
        )
        assert db_row["lead_time_days"] != 0, (
            "lead_time_days=0 passed directly to upsert_stock was persisted — "
            "this collapses _calc_signal's thresholds to 0"
        )

    def test_direct_upsert_negative_current_stock_dropped(self, test_tenant):
        from backend.inventory.service import upsert_stock
        from backend.db.connection import query_one

        sku = _sku()
        upsert_stock(test_tenant["id"], sku, {
            "current_stock": -25,
            "supplier": "Prov Directo",
        })

        db_row = query_one(
            "SELECT current_stock, supplier FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (test_tenant["id"], sku),
        )
        assert db_row["supplier"] == "Prov Directo"  # other valid fields still saved
        assert db_row["current_stock"] is None or float(db_row["current_stock"]) >= 0, (
            "negative current_stock passed directly to upsert_stock was persisted"
        )

    def test_direct_bulk_upsert_negative_moq_dropped(self, test_tenant):
        from backend.inventory.service import bulk_upsert
        from backend.db.connection import query_one

        sku = _sku()
        count = bulk_upsert(test_tenant["id"], [
            {"sku": sku, "current_stock": 10, "moq": -3},
        ])
        assert count == 1

        db_row = query_one(
            "SELECT moq FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (test_tenant["id"], sku),
        )
        assert db_row["moq"] is None or float(db_row["moq"]) >= 1, (
            "negative moq passed directly to bulk_upsert was persisted"
        )


# ── Signal calculation (unit-level, no DB) ────────────────────────────────────

class TestSignalCalculation:
    """Tests for the pure calculation logic in inventory.service."""

    def _make_forecast(self, daily_demand: float, days: int, uncertainty_pct: float = 0.1):
        """Creates a fake model_forecasts dict for service._avg_daily_forecast."""
        pts = [
            {
                "date": f"2026-01-{i+1:02d}",
                "value": daily_demand,
                "lower": daily_demand * (1 - uncertainty_pct),
                "upper": daily_demand * (1 + uncertainty_pct),
            }
            for i in range(days)
        ]
        return {"lightgbm": {"forecast": pts}, "prophet": {"forecast": pts}}

    def test_avg_daily_forecast_correct(self):
        from backend.inventory.service import _avg_daily_forecast
        mf = self._make_forecast(100.0, 14)
        avg, std = _avg_daily_forecast(mf, 14)
        assert abs(avg - 100.0) < 0.01
        assert std >= 0

    def test_avg_daily_forecast_empty(self):
        from backend.inventory.service import _avg_daily_forecast
        avg, std = _avg_daily_forecast({}, 14)
        assert avg == 0.0
        assert std == 0.0

    def test_signal_order_now(self):
        from backend.inventory.service import _calc_signal
        # coverage_days < lead_time * 0.5 → PEDIR_YA
        assert _calc_signal(coverage_days=3, lead_time=15) == "PEDIR_YA"

    def test_signal_order_soon(self):
        from backend.inventory.service import _calc_signal
        assert _calc_signal(coverage_days=14, lead_time=15) == "PEDIR_PRONTO"

    def test_signal_ok(self):
        from backend.inventory.service import _calc_signal
        assert _calc_signal(coverage_days=25, lead_time=15) == "OK"

    def test_signal_overstock(self):
        from backend.inventory.service import _calc_signal
        assert _calc_signal(coverage_days=60, lead_time=15) == "SOBRESTOCK"

    def test_recommended_order_respects_moq(self):
        from backend.inventory.service import _calc_recommended
        # With avg_daily=10, lead=14, stock=50 → demand_lt=140, safety~=23 → raw~=113
        qty = _calc_recommended(current_stock=50, avg_daily=10, avg_std=1.0, lead_time=14, moq=50)
        assert qty % 50 == 0  # must be a multiple of MOQ
        assert qty >= 0

    def test_recommended_order_zero_when_overstock(self):
        from backend.inventory.service import _calc_recommended
        qty = _calc_recommended(current_stock=10_000, avg_daily=1, avg_std=0.1, lead_time=14, moq=1)
        assert qty == 0

    def test_recommended_order_rounds_up_moq(self):
        from backend.inventory.service import _calc_recommended
        # With high demand and moq=100, result must be ceiling multiple of 100
        qty = _calc_recommended(current_stock=0, avg_daily=50, avg_std=5, lead_time=14, moq=100)
        assert qty > 0
        assert qty % 100 == 0

    def test_recommended_gated_to_ordering_signals(self):
        from backend.inventory.service import _gate_recommended_by_signal
        # Ordering signals keep the computed quantity
        assert _gate_recommended_by_signal("PEDIR_YA", 120.0) == 120.0
        assert _gate_recommended_by_signal("PEDIR_PRONTO", 45.0) == 45.0
        # "Enough stock" signals never suggest ordering, even if the raw math > 0
        assert _gate_recommended_by_signal("OK", 30.0) == 0.0
        assert _gate_recommended_by_signal("SOBRESTOCK", 5.0) == 0.0
        assert _gate_recommended_by_signal("SIN_DATOS", 10.0) == 0.0


# ── Status endpoint ───────────────────────────────────────────────────────────

class TestInventoryStatus:

    def test_status_missing_session_id_returns_422(self, client, auth_headers):
        resp = client.get("/api/v1/inventory/status", headers=auth_headers)
        assert resp.status_code == 422

    def test_status_unknown_session_returns_sin_datos(self, client, auth_headers):
        """If session has no forecast data, all SKUs with stock return SIN_DATOS."""
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"current_stock": 50, "lead_time_days": 10},
                   headers=auth_headers)

        resp = client.get(
            f"/api/v1/inventory/status?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        data = _ok(resp)
        # All items should be SIN_DATOS when there's no forecast
        if data["items"]:
            assert all(i["signal"] == "SIN_DATOS" for i in data["items"])

    def test_status_signal_filter(self, client, auth_headers):
        """signal= query param filters the response."""
        resp = client.get(
            f"/api/v1/inventory/status?session_id={uuid4().hex}&signal=OK",
            headers=auth_headers,
        )
        data = _ok(resp)
        assert all(i["signal"] == "OK" for i in data["items"])

    def test_status_summary_keys(self, client, auth_headers):
        resp = client.get(
            f"/api/v1/inventory/status?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        data = _ok(resp)
        for key in ("total_skus", "order_now", "order_soon", "ok", "overstock", "sin_datos"):
            assert key in data["summary"], f"Missing summary key: {key}"

    def test_status_gates_recommended_qty_by_signal(self, client, auth_headers, test_tenant):
        """
        Integration-level proof that the _gate_recommended_by_signal helper is
        actually wired into get_inventory_status. The 'plenty' SKU is chosen so
        the RAW math still wants an order (its safety stock, from high demand
        variability, exceeds the gap between stock and lead-time demand) yet its
        coverage lands it on an OK signal — so without the gating wiring it would
        surface recommended_qty > 0. The gate must force it to 0 with
        calc_explanation["suficiente"] is True. The 'short' SKU (critically low
        stock) must surface recommended_qty > 0 with no "suficiente" flag.
        A SOBRESTOCK/huge-pile SKU would NOT prove the wiring, because its raw
        recommendation is already 0.
        """
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        session = create_session(tid, "usr_test", "signal-gating-test")
        session_id = session["id"]

        sku_short, sku_plenty = _sku(), _sku()

        # sku_short: near-empty -> PEDIR_YA.
        client.put(f"/api/v1/inventory/stock/{sku_short}",
                   json={"current_stock": 1, "lead_time_days": 10, "moq": 1},
                   headers=auth_headers)
        # sku_plenty: 130 units, demand 10/day, lead 10 -> 13 days coverage -> OK
        # signal (lead*1.2=12 <= 13 < lead*3=30). With spread=6 the safety stock
        # (~31) makes the RAW recommendation ~2 (>0); only the gate zeroes it.
        client.put(f"/api/v1/inventory/stock/{sku_plenty}",
                   json={"current_stock": 130, "lead_time_days": 10, "moq": 1},
                   headers=auth_headers)

        def _forecast(daily_demand: float, spread: float) -> dict:
            pts = [
                {
                    "date": f"2026-01-{i+1:02d}",
                    "value": daily_demand,
                    "lower": max(0.0, daily_demand - spread),
                    "upper": daily_demand + spread,
                }
                for i in range(14)
            ]
            return {"lightgbm": {"forecast": pts}}

        session_store.set_forecasts(tid, session_id, {
            sku_short:  _forecast(100.0, 10.0),  # critically short -> PEDIR_YA
            sku_plenty: _forecast(10.0, 6.0),    # OK coverage, high variance
        })

        resp = client.get(f"/api/v1/inventory/status?session_id={session_id}", headers=auth_headers)
        data = _ok(resp)
        items = {i["sku"]: i for i in data["items"]}

        short_item = items[sku_short]
        plenty_item = items[sku_plenty]

        assert short_item["signal"] == "PEDIR_YA"
        assert short_item["recommended_qty"] > 0
        assert not (short_item["calc_explanation"] or {}).get("suficiente")

        # OK coverage but the raw math (with safety stock) wants an order;
        # the gate is the ONLY reason this is 0 — deleting the wiring makes it > 0.
        assert plenty_item["signal"] == "OK"
        assert plenty_item["recommended_qty"] == 0
        assert plenty_item["calc_explanation"]["suficiente"] is True


# ── PO Export ─────────────────────────────────────────────────────────────────

class TestPOExport:

    def test_export_returns_csv(self, client, auth_headers):
        resp = client.get(
            f"/api/v1/inventory/status/export-po?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "purchase_order" in resp.headers.get("content-disposition", "")

    def test_export_csv_has_header_row(self, client, auth_headers):
        resp = client.get(
            f"/api/v1/inventory/status/export-po?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        content = resp.content.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        header = next(reader, None)
        assert header is not None
        assert "SKU" in header
        assert "Cantidad recomendada" in header
        # The exported PO must be transparent about the lead time it used and
        # whether that lead time was learned from real receptions or configured
        # by hand (Faro plan open risk #6).
        assert "Lead time (días)" in header
        assert "Origen lead time" in header


# ── Log PO: 0-unit lines must never become ordered PO items ───────────────────

class TestLogPOZeroQtyGuard:

    def test_log_po_excludes_zero_qty_ordered_line(self, client, analyst_headers, viewer_headers):
        session_id = f"sess_test_{uuid4().hex[:6]}"
        body = {"items": [
            {"sku": "A", "signal": "PEDIR_YA",     "recommended_qty": 0, "final_qty": 0,  "status": "approved"},
            {"sku": "B", "signal": "PEDIR_PRONTO", "recommended_qty": 20, "final_qty": 20, "status": "approved"},
        ]}
        # viewer denied
        vr = client.post(f"/api/v1/inventory/log-po?session_id={session_id}",
                         json=body, headers=viewer_headers)
        assert vr.status_code == 403

        # analyst succeeds
        r = client.post(f"/api/v1/inventory/log-po?session_id={session_id}",
                        json=body, headers=analyst_headers)
        assert r.status_code == 201
        po_log_id = r.json()["data"]["id"]

        # Direct DB assertion: A is NOT an ordered line, B is
        from backend.db.connection import query
        rows = {row["sku"]: row for row in query(
            "SELECT sku, status, final_qty FROM inventory_po_items WHERE po_log_id = %s",
            (po_log_id,))}
        assert rows["A"]["status"] == "rejected"
        assert float(rows["A"]["final_qty"]) == 0
        assert rows["B"]["status"] == "approved"
        assert float(rows["B"]["final_qty"]) == 20
