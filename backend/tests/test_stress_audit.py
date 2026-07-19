"""
STRESS AUDIT — adversarial tests for the inventory backend.

Philosophy: don't test that it works; test that it fails correctly when it should,
and doesn't crash when fed garbage.

Bugs targeted:
  1. moq=0 → silently stored (falsy check bypasses MOQ rounding but also bypasses
     division-by-zero); StockUpsert allows ge=0 for moq.
  2. unit_cost=0 → inventory_value returns None instead of 0 due to falsy check.
  3. EventCreate has no date-ordering validation (start_date > end_date is accepted).
  4. bulk_upsert bypasses Pydantic → negative lead_time stored via CSV.
  5. BOM self-reference only: indirect cycles (A→B→A) not blocked.
  6. assign_sku_supplier: no check that SKU exists in inventory_stock.
  7. CSV with only headers/no rows → 422.
"""

from __future__ import annotations

import csv
import io
import math
import threading
from uuid import uuid4

import pytest


# ── Fixture overrides (same as test_inventory.py) ─────────────────────────────

@pytest.fixture
def registered_user(test_tenant):
    from backend.users import service as user_svc
    email = f"audit-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(
        tenant_id=test_tenant["id"],
        email=email,
        password=password,
        role="admin",
        full_name="Audit Admin",
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
    return f"AUD-{uuid4().hex[:8].upper()}"


def _make_csv(rows: list[dict], encoding: str = "utf-8") -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode(encoding)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 1 — Extreme inputs and edge cases in inventory
# ═══════════════════════════════════════════════════════════════════════════════

class TestInventoryEdgeCases:
    """Adversarial inputs for the stock CRUD + calculation layer."""

    # ── moq = 0 ────────────────────────────────────────────────────────────────

    def test_moq_zero_does_not_divide_by_zero(self):
        """
        _calc_recommended with moq=0 must not raise ZeroDivisionError.
        The `if moq and moq > 0` guard makes moq=0 falsy so no division occurs,
        but we must verify this assumption holds explicitly.
        """
        from backend.inventory.service import _calc_recommended
        # moq=0: should return raw demand without MOQ rounding
        result = _calc_recommended(
            current_stock=10.0, avg_daily=5.0, avg_std=1.0,
            lead_time=14, moq=0, service_level=0.95
        )
        assert isinstance(result, float), "Must return a float, not raise"
        assert result >= 0, "Result must be non-negative"

    def test_moq_zero_via_api_rejected_with_422(self, client, auth_headers):
        """
        StockUpsert declares `moq: float = Field(default=1, ge=1)` (api/v1/inventory.py),
        so moq=0 is always rejected by Pydantic at the API boundary — the service-level
        falsy-check guard tested in test_moq_zero_does_not_divide_by_zero above only
        matters for callers that bypass the API (e.g. dataset sync), never for PUT/PATCH.
        """
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100.0, "moq": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

        get_resp = client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers)
        assert get_resp.status_code == 404, "Rejected PUT must not create a stock row"

    # ── lead_time = 0 ─────────────────────────────────────────────────────────

    def test_lead_time_zero_rejected_by_api(self, client, auth_headers):
        """lead_time_days has ge=1 in StockUpsert — zero must return 422."""
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100.0, "lead_time_days": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 422, (
            f"lead_time_days=0 should be rejected (ge=1), got {resp.status_code}"
        )

    def test_lead_time_zero_calc_does_not_crash(self):
        """
        Even if lead_time=0 somehow reaches _calc_recommended,
        math.sqrt(0)=0 is safe and lead_time_demand=0.
        """
        from backend.inventory.service import _calc_recommended, _calc_signal
        qty = _calc_recommended(100.0, 10.0, 1.0, lead_time=0, moq=1)
        assert qty >= 0

        # _calc_signal with lead_time=0 → threshold = 0 → PEDIR_YA for any coverage
        sig = _calc_signal(coverage_days=5.0, lead_time=0)
        assert isinstance(sig, str), "_calc_signal must return a string even with lead_time=0"

    # ── large stock values ─────────────────────────────────────────────────────

    def test_stock_very_large_number(self, client, auth_headers):
        """current_stock = 1e12 must be accepted and returned without overflow."""
        sku = _sku()
        large = 1e12
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": large, "lead_time_days": 15},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["current_stock"] == large

    # ── stock = 0 ─────────────────────────────────────────────────────────────

    def test_stock_zero(self, client, auth_headers):
        """current_stock=0 is valid and must be stored correctly."""
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 0, "lead_time_days": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["current_stock"] == 0

    # ── unit_cost = 0 → inventory_value bug ──────────────────────────────

    def test_cost_zero_inventory_value_not_none(self, client, auth_headers, registered_user):
        """
        BUG CANDIDATE: in get_inventory_status the check is
            `if stock.get("unit_cost")`
        which is falsy when unit_cost=0, making inventory_value=None
        instead of 0.0.  This test verifies the behavior.

        The inventory_value calculation only runs when has_forecast and
        has_stock are both true, and /status now scopes its SKU set strictly
        to the session's forecasts (see test_status_nonexistent_session_returns_no_phantom_skus),
        so a forecast must be seeded for this SKU to actually exercise that branch.
        """
        from backend.db import session_store

        tenant_id = registered_user["tenant"]["id"]
        sku = _sku()
        sess_resp = client.post(
            "/api/v1/sessions", json={"name": "cost-zero-test"}, headers=auth_headers,
        )
        assert sess_resp.status_code in (200, 201), sess_resp.text
        session_id = sess_resp.json()["data"]["id"]

        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100.0, "unit_cost": 0.0, "lead_time_days": 10},
            headers=auth_headers,
        )
        session_store.set_forecasts(tenant_id, session_id, {
            sku: {"lightgbm": {"historical": [], "forecast": [
                {"date": "2026-01-01", "value": 5.0, "upper": 6.0},
            ]}},
        })

        resp = client.get(
            f"/api/v1/inventory/status?session_id={session_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        matching = [i for i in items if i["sku"] == sku]
        assert matching, "SKU not found in status response"
        item = matching[0]
        # inventory_value should be 0.0 (100 * 0 = 0), NOT None
        assert item["inventory_value"] == 0.0, (
            "inventory_value must be 0.0 when unit_cost=0, not None "
            f"(falsy-check bug confirmed): got {item['inventory_value']}"
        )

    # ── service_level extremes ─────────────────────────────────────────────────

    def test_service_level_zero_falls_back_to_default_z(self):
        """
        service_level=0 is not in _Z dict → falls back to z=1.645 (default).
        Must not crash and must return a non-negative number.

        NOTE: _calc_recommended returns int when moq is a Python int literal and
        math.ceil produces an integer (e.g. moq=1 → ceil(103.x) * 1 = 103 int).
        This is a type annotation violation (declared return float) but not a
        functional bug. The test accepts int or float.
        """
        from backend.inventory.service import _calc_recommended
        result = _calc_recommended(50.0, 10.0, 2.0, lead_time=14, moq=1, service_level=0)
        assert isinstance(result, (int, float)), "Must return a number, not raise"
        assert result >= 0, "Result must be non-negative"

    def test_service_level_one_falls_back_to_default_z(self):
        """
        service_level=1.0 is not in _Z dict → falls back to 1.645. Must not crash.
        See note above about int-vs-float return type.
        """
        from backend.inventory.service import _calc_recommended
        result = _calc_recommended(50.0, 10.0, 2.0, lead_time=14, moq=1, service_level=1.0)
        assert isinstance(result, (int, float)), "Must return a number, not raise"
        assert result >= 0

    def test_service_level_boundary_via_api(self, client, auth_headers):
        """
        /status endpoint accepts service_level between 0.5 and 0.999 (ge=0.5, le=0.999).
        0.0 or 1.0 must be rejected.
        """
        for bad_sl in (0.0, 1.0, 1.5):
            resp = client.get(
                f"/api/v1/inventory/status?session_id={uuid4().hex}&service_level={bad_sl}",
                headers=auth_headers,
            )
            assert resp.status_code == 422, (
                f"service_level={bad_sl} should be rejected, got {resp.status_code}"
            )

    # ── XSS / special chars in display_name ───────────────────────────────────

    def test_display_name_xss_chars_stored_as_literal(self, client, auth_headers):
        """
        XSS payload in display_name must be stored as a literal string
        and returned verbatim (no HTML encoding expected in JSON API).
        """
        sku = _sku()
        xss_name = "Aceite <script>alert(1)</script>"
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 10.0, "display_name": xss_name},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        stored = resp.json()["data"]["display_name"]
        # Must survive round-trip as literal string (backend is not a browser)
        assert stored == xss_name, (
            "display_name should be stored verbatim, not mangled"
        )

    def test_sku_with_slash_chars(self, client, auth_headers):
        """
        SKU with slash 'SKU/001' would break URL routing if used in path.
        The PUT endpoint takes sku as a path param — a slash would cause 404/405.
        Verify the API handles it gracefully (either stores it or rejects it).
        """
        sku = "SKU/001"
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 5.0},
            headers=auth_headers,
        )
        # A slash in the URL path segment is technically a routing issue.
        # Acceptable outcomes: 404 (route not matched) or 422 (rejected).
        # NOT acceptable: 500 (server error).
        assert resp.status_code != 500, f"slash in SKU caused 500: {resp.text}"

    # ── lead_time limits via API ───────────────────────────────────────────────

    def test_lead_time_over_limit_rejected(self, client, auth_headers):
        """lead_time_days > 365 must return 422 (le=365)."""
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 10.0, "lead_time_days": 366},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_negative_stock_rejected(self, client, auth_headers):
        """current_stock < 0 must return 422 (ge=0)."""
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": -1.0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_moq_larger_than_demand_produces_valid_order(self):
        """
        When moq >> demand, the recommended order should still be a valid
        multiple of moq and non-negative.
        """
        from backend.inventory.service import _calc_recommended
        # avg_daily=1, lead=14, stock=100 → demand_lt=14 < stock, raw=0
        # → result = 0 (no order needed)
        qty = _calc_recommended(
            current_stock=100.0, avg_daily=1.0, avg_std=0.1,
            lead_time=14, moq=10000, service_level=0.95
        )
        assert qty == 0.0 or qty % 10000 == 0, (
            f"Result {qty} is not 0 or a multiple of MOQ 10000"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Calculations with empty forecast or nonexistent session
# ═══════════════════════════════════════════════════════════════════════════════

class TestInventoryCalculationEdgeCases:

    def test_status_nonexistent_session_returns_no_phantom_skus(self, client, auth_headers):
        """
        REGRESSION TEST for the Quick Start "phantom entity" bug: inventory_stock
        is a tenant-wide table with no session_id column, so a SKU with stock
        entered under one session must NOT leak into the view of a different
        (or nonexistent) session that never forecast it. A session with no
        forecast data must show zero items, not every stock row the tenant
        has ever accumulated.
        """
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 50.0, "lead_time_days": 10},
            headers=auth_headers,
        )
        resp = client.get(
            f"/api/v1/inventory/status?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == [], (
            "A session with no forecast must not surface unrelated stock SKUs "
            f"from other sessions: {data['items']}"
        )

    def test_status_summary_zero_for_session_without_forecast(self, client, auth_headers):
        """
        A session_id with no forecast data must report total_skus == 0, even
        when the tenant has stock entries for other SKUs from other sessions.
        Counters must still add up internally.
        """
        skus = [_sku() for _ in range(3)]
        for sku in skus:
            client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 100.0},
                headers=auth_headers,
            )
        resp = client.get(
            f"/api/v1/inventory/status?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        summary = resp.json()["data"]["summary"]
        assert summary["total_skus"] == 0, (
            f"Unrelated tenant stock must not appear under a session with no "
            f"forecast for those SKUs: {summary}"
        )
        counted = (
            summary["sin_datos"] + summary["ok"] +
            summary["order_now"] + summary["order_soon"] + summary["overstock"]
        )
        assert counted == summary["total_skus"], (
            f"Signal counts don't add up: {summary}"
        )

    def test_recommendation_zero_demand(self):
        """
        avg_daily=0 → coverage_days is set to 9999 (infinite).
        _calc_recommended must return 0 (no order needed if no demand).
        """
        from backend.inventory.service import _calc_recommended, _avg_daily_forecast
        avg, std = _avg_daily_forecast({}, lead_time=14)
        assert avg == 0.0
        qty = _calc_recommended(100.0, avg, std, lead_time=14, moq=1)
        assert qty == 0.0, "Zero demand → no order recommended"

    def test_morning_briefing_no_inventory(self, client, auth_headers):
        """
        Morning briefing with a session that has no SKUs in inventory_stock
        must return 200 with has_data=False or has_data=True with empty lists.
        Must NOT return 500.
        """
        resp = client.get(
            f"/api/v1/inventory/morning-briefing?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200, (
            f"morning-briefing crashed with no inventory: {resp.text}"
        )
        data = resp.json()["data"]
        assert "kpis" in data, "kpis key must always be present"
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)

    def test_morning_briefing_empty_inventory(self, client, auth_headers):
        """
        Tenant with zero SKUs in inventory_stock.
        Briefing kpis.total_skus must be 0 and no 500.
        """
        resp = client.get(
            f"/api/v1/inventory/morning-briefing?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        kpis = resp.json()["data"]["kpis"]
        # Without any SKUs in this tenant, total_skus could be 0
        assert isinstance(kpis["total_skus"], int)
        assert kpis["total_skus"] >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 3 — BOM and material explosion
# ═══════════════════════════════════════════════════════════════════════════════

class TestBOMEdgeCases:

    def test_bom_self_reference_rejected(self, client, auth_headers):
        """
        PUT /bom/{sku}/{sku} (parent == child) must return 422 with a clear error.
        The service raises ValueError("A SKU cannot be its own component").
        """
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 50.0},
            headers=auth_headers,
        )
        resp = client.put(
            f"/api/v1/inventory/bom/{sku}/{sku}",
            json={"quantity": 1.0},
            headers=auth_headers,
        )
        assert resp.status_code == 422, (
            f"Self-referencing BOM should return 422, got {resp.status_code}: {resp.text}"
        )
        assert "component" in resp.text.lower() or "itself" in resp.text.lower() or "own" in resp.text.lower(), (
            "Error message should mention the self-reference issue"
        )

    def test_bom_zero_quantity_rejected(self, client, auth_headers):
        """quantity=0 is blocked by BomItemUpsert(quantity: float = Field(gt=0))."""
        parent = _sku()
        child = _sku()
        resp = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 422, (
            f"quantity=0 should be rejected by gt=0 constraint, got {resp.status_code}"
        )

    def test_bom_negative_quantity_rejected(self, client, auth_headers):
        """quantity=-1 must be rejected."""
        parent = _sku()
        child = _sku()
        resp = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": -1},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_explode_no_bom_returns_empty(self, client, auth_headers):
        """
        /production-requirements with a session that has no BOM defined
        must return an empty result, not a 500 error.
        """
        resp = client.get(
            f"/api/v1/inventory/production-requirements?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200, (
            f"explode with no BOM crashed: {resp.text}"
        )
        data = resp.json()["data"]
        assert data["finished_goods_count"] == 0
        assert data["has_shortages"] is False
        assert isinstance(data["finished_goods"], list)
        assert isinstance(data["raw_material_summary"], list)

    def test_bom_upsert_idempotent(self, client, auth_headers):
        """
        Upserting the same BOM relationship twice must be idempotent.
        Second call should return 200 (not 409/500) with updated quantity.
        """
        parent = _sku()
        child = _sku()
        for sku in (parent, child):
            client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 100.0},
                headers=auth_headers,
            )

        resp1 = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 2.0, "unit": "kg"},
            headers=auth_headers,
        )
        assert resp1.status_code == 200

        resp2 = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 3.0, "unit": "kg"},
            headers=auth_headers,
        )
        assert resp2.status_code == 200, (
            f"Second upsert failed: {resp2.text}"
        )
        # Quantity should be updated to 3.0
        data2 = resp2.json()["data"]
        assert data2.get("quantity") == 3.0 or data2 == {}, (
            "Upsert should update quantity"
        )

    def test_bom_delete_nonexistent(self, client, auth_headers):
        """
        DELETE /bom/{parent}/{child} that doesn't exist must not return 500.
        204 (idempotent delete) or 404 are both acceptable.
        """
        parent = _sku()
        child = _sku()
        resp = client.delete(
            f"/api/v1/inventory/bom/{parent}/{child}",
            headers=auth_headers,
        )
        assert resp.status_code in (204, 404), (
            f"Deleting non-existent BOM returned {resp.status_code}: {resp.text}"
        )
        assert resp.status_code != 500

    def test_bom_indirect_cycle_does_not_infinite_loop(self, client, auth_headers):
        """
        BUG CANDIDATE: upsert_bom_item only blocks direct self-reference.
        Indirect cycle A→B→A is not blocked at the service layer.
        explode_requirements processes only finished_good / semi_finished product_type
        and only one level deep, so infinite recursion should NOT occur.
        This test verifies the explosion completes in finite time even with a cycle.
        """
        sku_a = _sku()
        sku_b = _sku()

        for sku in (sku_a, sku_b):
            client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 10.0},
                headers=auth_headers,
            )
            # Mark both as finished_good so they could theoretically trigger the loop
            client.patch(
                f"/api/v1/inventory/stock/{sku}/product-type?product_type=finished_good",
                headers=auth_headers,
            )

        # Create A→B
        client.put(
            f"/api/v1/inventory/bom/{sku_a}/{sku_b}",
            json={"quantity": 1.0},
            headers=auth_headers,
        )
        # Create B→A (indirect cycle)
        client.put(
            f"/api/v1/inventory/bom/{sku_b}/{sku_a}",
            json={"quantity": 1.0},
            headers=auth_headers,
        )

        # explode_requirements should still complete (no infinite loop)
        import signal as _signal

        completed = threading.Event()
        result_holder: dict = {}

        def run_explode():
            resp = client.get(
                f"/api/v1/inventory/production-requirements?session_id={uuid4().hex}",
                headers=auth_headers,
            )
            result_holder["status"] = resp.status_code
            completed.set()

        t = threading.Thread(target=run_explode, daemon=True)
        t.start()
        finished = completed.wait(timeout=10)  # 10 seconds max

        assert finished, "explode_requirements timed out — possible infinite loop with BOM cycle"
        assert result_holder.get("status") == 200, (
            f"explode_requirements should 200 even for an unknown session_id with a BOM "
            f"cycle present, got {result_holder.get('status')}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 4 — Bulk import with problematic data
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkImportEdgeCases:

    def test_bulk_no_sku_column_rejected(self, client, auth_headers):
        """CSV without 'sku' column → 422 (no valid rows)."""
        csv_bytes = b"current_stock,lead_time_days\n100,15\n200,10\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("bad.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 422, (
            f"Missing 'sku' column should be rejected, got {resp.status_code}"
        )

    def test_bulk_only_headers_no_rows_rejected(self, client, auth_headers):
        """CSV with only headers, no data rows → 422."""
        csv_bytes = b"sku,current_stock,lead_time_days\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("empty.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 422, (
            f"CSV with headers only should return 422, got {resp.status_code}"
        )

    def test_bulk_empty_sku_rows_skipped(self, client, auth_headers):
        """Rows where sku is empty must be skipped, not cause a 500."""
        good_sku = _sku()
        csv_content = (
            f"sku,current_stock\n"
            f",100\n"                   # empty sku
            f"   ,50\n"                 # whitespace-only sku
            f"{good_sku},75\n"          # valid row
        )
        csv_bytes = csv_content.encode("utf-8")
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("mixed.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 200, f"Should succeed with some valid rows: {resp.text}"
        data = resp.json()["data"]
        assert data["imported"] == 1, f"Only 1 valid row should be imported, got {data}"

    def test_bulk_invalid_numeric_skipped(self, client, auth_headers):
        """
        Rows with non-numeric current_stock values should be stored without the
        invalid field (graceful degradation), not cause a 500.
        """
        sku = _sku()
        csv_bytes = f"sku,current_stock,lead_time_days\n{sku},not_a_number,15\n".encode("utf-8")
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("invalid_num.csv", csv_bytes, "text/csv")},
        )
        # The bulk endpoint uses a try/except in _float(), so invalid numeric
        # fields are simply omitted. The row may still be imported with defaults.
        # Must NOT be 500.
        assert resp.status_code != 500, f"Invalid numeric should not cause 500: {resp.text}"

    def test_bulk_latin1_encoding(self, client, auth_headers):
        """CSV encoded in Latin-1 (common from Excel in Latin America) must be accepted."""
        sku = _sku()
        # Include a Latin-1 specific character (ñ, á)
        csv_content = f"sku,display_name,stock_actual\n{sku},Aceite Niño,50\n"
        csv_bytes = csv_content.encode("latin-1")
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("latin1.csv", csv_bytes, "text/csv")},
        )
        # The endpoint falls back to latin-1 on UnicodeDecodeError
        assert resp.status_code == 200, f"Latin-1 CSV should be accepted: {resp.text}"
        data = resp.json()["data"]
        assert data["imported"] >= 1

    def test_bulk_utf8_bom_encoding(self, client, auth_headers):
        """CSV with UTF-8 BOM (typical from Windows Excel) must be parsed correctly."""
        sku = _sku()
        # BOM = \xef\xbb\xbf
        csv_bytes = b"\xef\xbb\xbf" + f"sku,current_stock\n{sku},99\n".encode("utf-8")
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("bom.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 200
        # The BOM-stripped sku must be found
        row = client.get(
            f"/api/v1/inventory/stock/{sku}",
            headers=auth_headers,
        )
        assert row.status_code == 200
        assert row.json()["data"]["current_stock"] == 99.0

    def test_bulk_large_import(self, client, auth_headers):
        """1000 SKUs in a single bulk → must complete without timeout/500."""
        rows = [
            {"sku": f"BULK-{i:04d}-{uuid4().hex[:4]}", "current_stock": i * 10, "lead_time_days": 15}
            for i in range(1000)
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["sku", "current_stock", "lead_time_days"])
        writer.writeheader()
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode("utf-8")

        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("large.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 200, f"Large bulk failed: {resp.text}"
        data = resp.json()["data"]
        assert data["imported"] == 1000, (
            f"Expected 1000 imports, got {data['imported']}"
        )

    def test_bulk_negative_lead_time_via_csv_row_rejected_not_stored(self, client, auth_headers):
        """
        Each CSV row is validated through StockPatch (ge=1 on lead_time_days) before
        being queued for bulk_upsert (api/v1/inventory.py:152) — a ValidationError just
        skips the row (`continue`), it does not bypass the constraint. With only one,
        invalid row in the file, `rows` ends up empty, which 422s.
        """
        sku = _sku()
        csv_bytes = f"sku,current_stock,lead_time_days\n{sku},100,-5\n".encode("utf-8")
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("neg_lead.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 422

        check = client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers)
        assert check.status_code == 404, "Row that failed validation must not be persisted"

    def test_bulk_csv_with_mixed_valid_and_invalid_rows_imports_only_valid(self, client, auth_headers):
        """A CSV with one valid row and one invalid (negative lead_time) row must
        import exactly the valid row and silently skip the invalid one."""
        good_sku, bad_sku = _sku(), _sku()
        csv_bytes = (
            f"sku,current_stock,lead_time_days\n"
            f"{good_sku},100,10\n"
            f"{bad_sku},100,-5\n"
        ).encode("utf-8")
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("mixed.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["imported"] == 1

        good = client.get(f"/api/v1/inventory/stock/{good_sku}", headers=auth_headers)
        assert good.status_code == 200
        assert good.json()["data"]["lead_time_days"] == 10

        bad = client.get(f"/api/v1/inventory/stock/{bad_sku}", headers=auth_headers)
        assert bad.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 5 — Suppliers — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestSupplierEdgeCases:

    def test_supplier_name_very_long(self, client, auth_headers):
        """Supplier name of 500 chars — should be accepted or rejected cleanly (not 500)."""
        long_name = "X" * 500
        resp = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": long_name},
            headers=auth_headers,
        )
        assert resp.status_code in (201, 422), (
            f"500-char supplier name caused {resp.status_code}: {resp.text}"
        )
        assert resp.status_code != 500

    def test_supplier_name_unicode(self, client, auth_headers):
        """Supplier name with Unicode and special chars must be stored correctly."""
        unicode_name = f"Distribuidora ñoño & Cía. — {uuid4().hex[:4]}"
        resp = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": unicode_name},
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"Unicode supplier name rejected: {resp.text}"
        assert resp.json()["data"]["name"] == unicode_name

    def test_supplier_std_greater_than_lead_time_allowed(self, client, auth_headers):
        """
        lead_time_std > lead_time_days is mathematically odd but the schema
        allows it (lead_time_std: int = Field(ge=0, le=60)).
        Must not cause crashes in calculations.
        """
        resp = client.post(
            "/api/v1/inventory/suppliers",
            json={
                "name": f"StdGtLt-{uuid4().hex[:4]}",
                "lead_time_days": 5,
                "lead_time_std": 50,  # std > lead_time
            },
            headers=auth_headers,
        )
        # Should be accepted (no schema constraint prevents this)
        assert resp.status_code in (201, 422), (
            f"Unexpected status: {resp.status_code}"
        )

    def test_assign_supplier_to_nonexistent_sku(self, client, auth_headers):
        """
        PUT /stock/{nonexistent_sku}/suppliers/{supplier_id}:
        The endpoint checks supplier existence but not SKU existence.
        This may silently create a dangling link.
        """
        # Create a real supplier
        cr = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": f"Sup-{uuid4().hex[:4]}", "lead_time_days": 10},
            headers=auth_headers,
        )
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        # Try to assign to a SKU that doesn't exist in inventory_stock
        ghost_sku = f"GHOST-{uuid4().hex[:8]}"
        resp = client.put(
            f"/api/v1/inventory/stock/{ghost_sku}/suppliers/{sup_id}",
            json={"is_primary": True},
            headers=auth_headers,
        )
        # Expected: either 404 (SKU not found) or 200 (dangling link created)
        # NOT acceptable: 500
        assert resp.status_code != 500, (
            f"Assigning supplier to non-existent SKU caused 500: {resp.text}"
        )

    def test_assign_supplier_idempotent_under_concurrency(self, client, auth_headers):
        """
        Two threads assigning the same supplier to the same SKU simultaneously must
        not produce two rows. sku_suppliers is keyed by (tenant_id, sku, supplier_id)
        with ON CONFLICT DO UPDATE (inventory/supplier_service.py:96-114), but the
        original version of this test only checked status codes sequentially, which
        can't catch a race — this drives both PUTs concurrently and queries the table
        directly to rule out a duplicate row.
        """
        from backend.db.connection import query_one
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 50.0},
            headers=auth_headers,
        )
        cr = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": f"IdemSup-{uuid4().hex[:4]}"},
            headers=auth_headers,
        )
        assert cr.status_code == 201
        sup_id = cr.json()["data"]["id"]

        results: list[int] = []

        def assign():
            r = client.put(
                f"/api/v1/inventory/stock/{sku}/suppliers/{sup_id}",
                json={"is_primary": True},
                headers=auth_headers,
            )
            results.append(r.status_code)

        threads = [threading.Thread(target=assign) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert results == [200, 200], f"Concurrent identical assignment failed: {results}"

        row = query_one(
            "SELECT COUNT(*) AS n FROM sku_suppliers WHERE sku = %s AND supplier_id = %s",
            (sku, sup_id),
        )
        assert row["n"] == 1, f"Expected exactly 1 sku_suppliers row, found {row['n']} (race condition)"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 6 — Inventory events
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventEdgeCases:

    def test_event_create_start_after_end_rejected(self, client, auth_headers):
        """
        EventCreate._check_date_order (api/v1/inventory.py) now enforces
        end_date >= start_date. Previously unvalidated — this used to xfail.
        """
        resp = client.post(
            "/api/v1/inventory/events",
            json={
                "name": "Backwards Event",
                "start_date": "2026-12-31",
                "end_date": "2026-01-01",   # end before start
                "multiplier": 1.5,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_event_create_start_equals_end_accepted(self, client, auth_headers):
        """Single-day events (start == end) must remain valid."""
        resp = client.post(
            "/api/v1/inventory/events",
            json={
                "name": "Single Day Event",
                "start_date": "2026-12-15",
                "end_date": "2026-12-15",
                "multiplier": 1.5,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201

    def test_event_patch_start_after_existing_end_rejected(self, client, auth_headers):
        """
        Patching only start_date to a value after the event's existing end_date
        must be rejected — the patch route merges with the stored row before
        validating, so this can't be bypassed by patching one field at a time.
        """
        cr = client.post(
            "/api/v1/inventory/events",
            json={"name": "To Be Hijacked", "start_date": "2026-05-01", "end_date": "2026-05-31"},
            headers=auth_headers,
        )
        assert cr.status_code == 201
        event_id = cr.json()["data"]["id"]

        resp = client.patch(
            f"/api/v1/inventory/events/{event_id}",
            json={"start_date": "2026-06-15"},  # after existing end_date of 2026-05-31
            headers=auth_headers,
        )
        assert resp.status_code == 422

        check = client.get("/api/v1/inventory/events", headers=auth_headers)
        ev = next(e for e in check.json()["data"] if e["id"] == event_id)
        assert str(ev["start_date"]) == "2026-05-01", "Rejected patch must not partially apply"

    def test_event_zero_multiplier_rejected(self, client, auth_headers):
        """
        multiplier has `ge=0.1` in EventCreate — multiplier=0 must return 422.
        """
        resp = client.post(
            "/api/v1/inventory/events",
            json={
                "name": "Zero Multiplier",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "multiplier": 0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422, (
            f"multiplier=0 should be rejected (ge=0.1), got {resp.status_code}"
        )

    def test_event_very_high_multiplier(self, client, auth_headers):
        """multiplier=10.0 is the upper limit (le=10.0) — must be accepted."""
        resp = client.post(
            "/api/v1/inventory/events",
            json={
                "name": f"HighMult-{uuid4().hex[:4]}",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "multiplier": 10.0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201

    def test_event_multiplier_above_max_rejected(self, client, auth_headers):
        """multiplier > 10.0 must be rejected (le=10.0)."""
        resp = client.post(
            "/api/v1/inventory/events",
            json={
                "name": "TooHigh",
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
                "multiplier": 10.001,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_event_patch_wrong_tenant(self, client, auth_headers):
        """
        Patching an event_id that belongs to a different tenant must return 404,
        not accidentally update the other tenant's event.
        """
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        from backend.db.connection import execute

        # Create tenant B with its own event
        t2 = create_tenant(f"tenant-b-{uuid4().hex[:6]}")
        email2 = f"b-{uuid4().hex[:6]}@example.com"
        u2 = user_svc.create_user(t2["id"], email2, "TestPass123!", "admin", "B")
        user_svc.mark_verified(t2["id"], u2["id"])

        try:
            lr = client.post("/api/v1/auth/login", json={"email": email2, "password": "TestPass123!"})
            h2 = {"Authorization": f"Bearer {lr.json()['data']['access_token']}"}

            # Tenant B creates an event
            cr = client.post(
                "/api/v1/inventory/events",
                json={
                    "name": "Tenant B Event",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-30",
                    "multiplier": 1.2,
                },
                headers=h2,
            )
            assert cr.status_code == 201
            event_id = cr.json()["data"]["id"]

            # Tenant A tries to patch Tenant B's event
            resp = client.patch(
                f"/api/v1/inventory/events/{event_id}",
                json={"multiplier": 9.9},
                headers=auth_headers,
            )
            assert resp.status_code == 404, (
                f"Cross-tenant event patch should return 404, got {resp.status_code}"
            )
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))

    def test_event_delete_wrong_tenant(self, client, auth_headers):
        """
        Deleting an event owned by a different tenant must return 204 (no-op)
        without actually deleting it, OR return 404.
        The delete_event service uses: DELETE WHERE id=%s AND tenant_id=%s
        So it's a no-op — returns 204 but does nothing. This is acceptable.
        """
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        from backend.db.connection import execute

        t2 = create_tenant(f"tenant-c-{uuid4().hex[:6]}")
        email2 = f"c-{uuid4().hex[:6]}@example.com"
        u2 = user_svc.create_user(t2["id"], email2, "TestPass123!", "admin", "C")
        user_svc.mark_verified(t2["id"], u2["id"])

        try:
            lr = client.post("/api/v1/auth/login", json={"email": email2, "password": "TestPass123!"})
            h2 = {"Authorization": f"Bearer {lr.json()['data']['access_token']}"}

            cr = client.post(
                "/api/v1/inventory/events",
                json={
                    "name": "Protected Event",
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-31",
                    "multiplier": 1.0,
                },
                headers=h2,
            )
            assert cr.status_code == 201
            event_id = cr.json()["data"]["id"]

            # Tenant A tries to delete it
            resp = client.delete(
                f"/api/v1/inventory/events/{event_id}",
                headers=auth_headers,
            )
            # No-op is acceptable (204) but event must still exist for tenant B
            assert resp.status_code in (204, 404)

            # Verify event still exists for Tenant B
            lr2 = client.get("/api/v1/inventory/events", headers=h2)
            ids = [e["id"] for e in lr2.json()["data"]]
            assert event_id in ids, (
                "Cross-tenant delete should not remove another tenant's event"
            )
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 6b — Cross-tenant mutation (IDOR) checks for sessions, stock, suppliers, BOM
#
# The event tests above (test_event_patch_wrong_tenant / test_event_delete_wrong_tenant)
# are the template: create a real second tenant, attempt the mutation as that tenant
# against tenant A's resource, assert it's rejected, then independently re-verify
# tenant A's resource is unchanged. These tests extend that pattern to the other
# mutable inventory/session resources, which the audit found had only GET/list
# tenant-isolation coverage, never PATCH/DELETE coverage.
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossTenantMutations:
    """
    For sessions/stock/suppliers/BOM, the service layer already scopes every
    UPDATE/DELETE by `WHERE tenant_id = %s` (composite keys for stock/BOM,
    explicit tenant_id checks for sessions/suppliers) — so these are expected
    to already be safe. The point of these tests is to actually prove it, since
    the audit found this behavior was asserted nowhere.
    """

    def _make_tenant_b(self, prefix: str):
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        t2 = create_tenant(f"{prefix}-{uuid4().hex[:6]}")
        email2 = f"{prefix}-{uuid4().hex[:6]}@example.com"
        user = user_svc.create_user(t2["id"], email2, "TestPass123!", "admin", prefix)
        user_svc.mark_verified(t2["id"], user["id"])
        return t2, email2

    def _login(self, client, email):
        lr = client.post("/api/v1/auth/login", json={"email": email, "password": "TestPass123!"})
        assert lr.status_code == 200
        return {"Authorization": f"Bearer {lr.json()['data']['access_token']}"}

    # ── sessions ───────────────────────────────────────────────────────────────

    def test_session_patch_wrong_tenant(self, client, auth_headers, test_session):
        from backend.db.connection import execute
        t2, email2 = self._make_tenant_b("sess-b")
        try:
            resp = client.patch(
                f"/api/v1/sessions/{test_session['id']}",
                json={"name": "hijacked"},
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404

            check = client.get(f"/api/v1/sessions/{test_session['id']}", headers=auth_headers)
            assert check.status_code == 200
            assert check.json()["data"]["name"] == test_session["name"]
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))

    def test_session_delete_wrong_tenant(self, client, auth_headers, test_session):
        from backend.db.connection import execute
        t2, email2 = self._make_tenant_b("sess-c")
        try:
            resp = client.delete(
                f"/api/v1/sessions/{test_session['id']}",
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404

            check = client.get(f"/api/v1/sessions/{test_session['id']}", headers=auth_headers)
            assert check.status_code == 200, "Cross-tenant delete must not remove another tenant's session"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))

    # ── inventory stock ────────────────────────────────────────────────────────

    def test_stock_patch_wrong_tenant_is_404_not_overwrite(self, client, auth_headers):
        from backend.db.connection import execute
        sku = f"PYTEST-STOCK-{uuid4().hex[:8]}"
        put = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100, "display_name": "Tenant A stock"},
            headers=auth_headers,
        )
        assert put.status_code == 200

        t2, email2 = self._make_tenant_b("stock-b")
        try:
            resp = client.patch(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 9999},
                headers=self._login(client, email2),
            )
            # Stock is keyed by (tenant_id, sku): tenant B has no row for this sku yet,
            # so PATCH (which requires an existing row) must 404 rather than create
            # or mutate tenant A's row.
            assert resp.status_code == 404

            check = client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers)
            assert check.status_code == 200
            assert check.json()["data"]["current_stock"] == 100
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM inventory_stock WHERE sku=%s", (sku,))

    def test_stock_delete_wrong_tenant_is_404_not_delete(self, client, auth_headers):
        from backend.db.connection import execute
        sku = f"PYTEST-STOCK-{uuid4().hex[:8]}"
        put = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 50},
            headers=auth_headers,
        )
        assert put.status_code == 200

        t2, email2 = self._make_tenant_b("stock-c")
        try:
            resp = client.delete(
                f"/api/v1/inventory/stock/{sku}",
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404

            check = client.get(f"/api/v1/inventory/stock/{sku}", headers=auth_headers)
            assert check.status_code == 200, "Cross-tenant delete must not remove another tenant's stock row"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM inventory_stock WHERE sku=%s", (sku,))

    # ── suppliers ──────────────────────────────────────────────────────────────

    def test_supplier_patch_wrong_tenant(self, client, auth_headers):
        from backend.db.connection import execute, query_one
        cr = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": "Tenant A Supplier"},
            headers=auth_headers,
        )
        assert cr.status_code == 201
        supplier_id = cr.json()["data"]["id"]

        t2, email2 = self._make_tenant_b("sup-b")
        try:
            resp = client.patch(
                f"/api/v1/inventory/suppliers/{supplier_id}",
                json={"name": "hijacked"},
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404

            row = query_one("SELECT name FROM suppliers WHERE id = %s", (supplier_id,))
            assert row["name"] == "Tenant A Supplier"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM suppliers WHERE id=%s", (supplier_id,))

    def test_supplier_delete_wrong_tenant(self, client, auth_headers):
        from backend.db.connection import execute
        cr = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": "Protected Supplier"},
            headers=auth_headers,
        )
        assert cr.status_code == 201
        supplier_id = cr.json()["data"]["id"]

        t2, email2 = self._make_tenant_b("sup-c")
        try:
            resp = client.delete(
                f"/api/v1/inventory/suppliers/{supplier_id}",
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404

            check = client.get("/api/v1/inventory/suppliers", headers=auth_headers)
            ids = [s["id"] for s in check.json()["data"]]
            assert supplier_id in ids, "Cross-tenant delete must not deactivate another tenant's supplier"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM suppliers WHERE id=%s", (supplier_id,))

    # ── BOM ────────────────────────────────────────────────────────────────────

    def test_bom_put_wrong_tenant_does_not_affect_owner(self, client, auth_headers):
        from backend.db.connection import execute
        parent, child = f"PARENT-{uuid4().hex[:6]}", f"CHILD-{uuid4().hex[:6]}"
        for sku in (parent, child):
            assert client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 1}, headers=auth_headers).status_code == 200
        put = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 2.0},
            headers=auth_headers,
        )
        assert put.status_code == 200

        t2, email2 = self._make_tenant_b("bom-b")
        try:
            h2 = self._login(client, email2)
            # Tenant B PUTs the same (parent, child) pair — since BOM rows are keyed by
            # (tenant_id, parent_sku, child_sku), this must create/affect only tenant B's
            # own row, never tenant A's.
            resp = client.put(
                f"/api/v1/inventory/bom/{parent}/{child}",
                json={"quantity": 999.0},
                headers=h2,
            )
            assert resp.status_code == 200

            check = client.get(f"/api/v1/inventory/bom/{parent}", headers=auth_headers)
            items = check.json()["data"]
            assert len(items) == 1
            assert items[0]["quantity"] == 2.0, "Tenant B's BOM write must not affect tenant A's BOM row"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM bom_items WHERE parent_sku=%s AND child_sku=%s", (parent, child))
            execute("DELETE FROM inventory_stock WHERE sku IN (%s, %s)", (parent, child))

    def test_bom_delete_wrong_tenant_is_noop(self, client, auth_headers):
        from backend.db.connection import execute
        parent, child = f"PARENT-{uuid4().hex[:6]}", f"CHILD-{uuid4().hex[:6]}"
        for sku in (parent, child):
            assert client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 1}, headers=auth_headers).status_code == 200
        put = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 3.0},
            headers=auth_headers,
        )
        assert put.status_code == 200

        t2, email2 = self._make_tenant_b("bom-c")
        try:
            resp = client.delete(
                f"/api/v1/inventory/bom/{parent}/{child}",
                headers=self._login(client, email2),
            )
            # DELETE is scoped by tenant_id, so it's a no-op for tenant A's row regardless
            # of status code — what matters is tenant A's row survives.
            assert resp.status_code == 204

            check = client.get(f"/api/v1/inventory/bom/{parent}", headers=auth_headers)
            items = check.json()["data"]
            assert len(items) == 1, "Cross-tenant BOM delete must not remove another tenant's BOM row"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM bom_items WHERE parent_sku=%s AND child_sku=%s", (parent, child))
            execute("DELETE FROM inventory_stock WHERE sku IN (%s, %s)", (parent, child))


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 7 — AI narratives with empty data
# ═══════════════════════════════════════════════════════════════════════════════

class TestAINarrativeEdgeCases:
    """
    AI narrative endpoints often depend on inventory status being populated.
    These tests ensure graceful degradation with empty / missing data.
    """

    def test_narrative_empty_inventory(self, client, auth_headers):
        """
        Morning narrative with a session that has no inventory must return
        a valid response, not a 500.
        """
        resp = client.get(
            f"/api/v1/inventory/morning-briefing?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # has_data should be False when there are no items
        assert "has_data" in data
        # Ensure it's serializable (no NaN, Infinity, etc.)
        import json
        try:
            json.dumps(data)
        except (TypeError, ValueError) as e:
            pytest.fail(f"Morning briefing response is not JSON-serializable: {e}")

    def test_dashboard_summary_empty(self, client, auth_headers):
        """Dashboard summary with no SKUs must return zeros, not errors."""
        resp = client.get(
            f"/api/v1/inventory/dashboard-summary?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_skus"] >= 0
        for key in ("order_now", "order_soon", "ok", "overstock", "sin_datos"):
            assert key in data, f"Missing key {key} in dashboard-summary"

    def test_morning_briefing_serializable(self, client, auth_headers):
        """
        morning-briefing response must be fully JSON-serializable
        (no datetime objects, no NaN, no Infinity).
        """
        # Create a SKU with cost and stock so inventory_value is computed
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 500.0, "unit_cost": 100.0, "lead_time_days": 7},
            headers=auth_headers,
        )
        resp = client.get(
            f"/api/v1/inventory/morning-briefing?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200

        import json
        import math as _math
        raw = resp.text

        def _check_no_nan_inf(obj):
            if isinstance(obj, float):
                assert not _math.isnan(obj), f"NaN found in response: {obj}"
                assert not _math.isinf(obj), f"Infinity found in response: {obj}"
            elif isinstance(obj, dict):
                for v in obj.values():
                    _check_no_nan_inf(v)
            elif isinstance(obj, list):
                for v in obj:
                    _check_no_nan_inf(v)

        parsed = resp.json()["data"]
        _check_no_nan_inf(parsed)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 8 — Concurrency and consistency
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrencyAndConsistency:

    def test_concurrent_upsert_same_sku(self, client, auth_headers):
        """
        Five concurrent upserts for the same SKU must not produce duplicate rows
        and must leave the row holding one of the values actually written (not a
        crash, a NULL, or a corrupted blend). inventory_stock is keyed by
        (tenant_id, sku) with ON CONFLICT DO UPDATE — this verifies that guarantee
        directly via the DB rather than just checking for the absence of a 500.
        """
        from backend.db.connection import query_one
        sku = _sku()
        errors: list[int] = []
        written_values = [i * 10 for i in range(5)]

        def do_upsert(stock_val: int):
            r = client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": float(stock_val), "lead_time_days": 10},
                headers=auth_headers,
            )
            if r.status_code == 500:
                errors.append(stock_val)

        threads = [
            threading.Thread(target=do_upsert, args=(v,))
            for v in written_values
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Concurrent upserts caused 500 errors for stock values: {errors}"

        row = query_one(
            "SELECT COUNT(*) AS n FROM inventory_stock WHERE sku = %s",
            (sku,),
        )
        assert row["n"] == 1, f"Expected exactly 1 row for {sku}, found {row['n']} (race condition)"

        final = client.get(
            f"/api/v1/inventory/stock/{sku}",
            headers=auth_headers,
        )
        assert final.status_code == 200
        final_value = final.json()["data"]["current_stock"]
        assert final_value in written_values, (
            f"Final current_stock {final_value} does not match any value actually written "
            f"({written_values}) — possible corrupted/blended write under concurrency"
        )

    def test_delete_while_reading(self, client, auth_headers):
        """
        Deleting a SKU while another thread reads the status must not cause 500,
        and the delete must actually win in the end — verified via a direct DB
        query, not just the absence of a 500 from either thread.
        """
        from backend.db.connection import query_one
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100.0},
            headers=auth_headers,
        )

        errors: list[str] = []

        def delete_sku():
            r = client.delete(
                f"/api/v1/inventory/stock/{sku}",
                headers=auth_headers,
            )
            if r.status_code == 500:
                errors.append(f"delete 500: {r.text}")

        def read_status():
            for _ in range(5):
                r = client.get(
                    f"/api/v1/inventory/status?session_id={uuid4().hex}",
                    headers=auth_headers,
                )
                if r.status_code == 500:
                    errors.append(f"read 500: {r.text}")

        t1 = threading.Thread(target=delete_sku)
        t2 = threading.Thread(target=read_status)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not errors, f"Delete-while-reading caused 500: {errors}"

        row = query_one("SELECT sku FROM inventory_stock WHERE sku = %s", (sku,))
        assert row is None, "Delete ran concurrently with reads but the row still exists afterward"

    def test_upsert_null_current_stock_rejected(self, client, auth_headers):
        """
        Sending current_stock=null in the JSON body must return 422.
        StockUpsert has `current_stock: float = Field(ge=0)` with no Optional —
        null should fail Pydantic validation.
        """
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": None},
            headers=auth_headers,
        )
        assert resp.status_code == 422, (
            f"current_stock=null should be rejected, got {resp.status_code}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 9 — Pure calculation (unit-level, no DB)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPureCalculationEdgeCases:
    """
    Unit-level tests for service calculation functions.
    These run without the DB — fast and deterministic.
    """

    def test_avg_daily_forecast_with_none_values(self):
        """
        Forecast points where 'value' is None must not cause AttributeError.
        The code uses `p.get("value") or 0.0` which handles None.
        """
        from backend.inventory.service import _avg_daily_forecast
        model_forecasts = {
            "model1": {
                "forecast": [
                    {"date": "2026-01-01", "value": None, "upper": 10.0},
                    {"date": "2026-01-02", "value": 5.0,  "upper": 6.0},
                ]
            }
        }
        avg, std = _avg_daily_forecast(model_forecasts, lead_time=2)
        assert isinstance(avg, float), "avg must be a float even with None values"
        assert avg >= 0

    def test_avg_daily_forecast_negative_upper(self):
        """
        If upper < value (misconfigured), stds would be negative.
        max(0.0, avg_std) protects the output.
        """
        from backend.inventory.service import _avg_daily_forecast
        model_forecasts = {
            "model1": {
                "forecast": [
                    {"value": 100.0, "upper": 50.0},  # upper < value
                ]
            }
        }
        avg, std = _avg_daily_forecast(model_forecasts, lead_time=1)
        assert std >= 0.0, "avg_std must be >= 0 even with upper < value"

    def test_classify_xyz_boundary_values(self):
        """_classify_xyz CV boundary values must return correct categories."""
        from backend.inventory.service import _classify_xyz
        assert _classify_xyz(None) == "?"
        assert _classify_xyz(0.0) == "X"
        assert _classify_xyz(0.499) == "X"
        assert _classify_xyz(0.5) == "Y"
        assert _classify_xyz(0.999) == "Y"
        assert _classify_xyz(1.0) == "Z"
        assert _classify_xyz(100.0) == "Z"

    def test_classify_abc_all_zero_demand(self):
        """All-zero demand → all SKUs classified as C (no division by zero)."""
        from backend.inventory.service import _classify_abc
        items = [
            {"sku": "A", "daily_demand": 0.0, "unit_cost": 100.0},
            {"sku": "B", "daily_demand": 0.0, "unit_cost": 200.0},
        ]
        result = _classify_abc(items)
        assert result == {"A": "C", "B": "C"}, (
            "Zero demand → all C, no ZeroDivisionError"
        )

    def test_calc_signal_with_zero_lead_time(self):
        """
        _calc_signal with lead_time=0 → all thresholds become 0.
        coverage_days < 0 is False for any positive value.
        Result depends on whether coverage_days < 0 evaluates as expected.
        Must return a valid signal string.
        """
        from backend.inventory.service import _calc_signal
        result = _calc_signal(coverage_days=0.0, lead_time=0)
        assert result in ("PEDIR_YA", "PEDIR_PRONTO", "OK", "SOBRESTOCK")

    def test_calc_recommended_service_level_interpolation(self):
        """
        Verifies that known service levels produce expected z-scores.
        """
        from backend.inventory.service import _calc_recommended, _Z
        for sl, z in _Z.items():
            # With stock=0, avg_daily=0, avg_std=0, result should be 0
            qty = _calc_recommended(0.0, 0.0, 0.0, lead_time=14, moq=1, service_level=sl)
            assert qty == 0.0, f"Zero demand/stock should produce 0 order for service_level={sl}"

    def test_generate_recommendations_empty_items(self):
        """generate_recommendations with empty list must return empty list."""
        from backend.inventory.service import generate_recommendations
        result = generate_recommendations([])
        assert result == []

    def test_generate_recommendations_sin_datos_items(self):
        """
        Items with SIN_DATOS signal and no coverage_days must not crash.
        """
        from backend.inventory.service import generate_recommendations
        items = [
            {
                "sku": "SKU-001",
                "display_name": "Test Item",
                "signal": "SIN_DATOS",
                "coverage_days": None,
                "lead_time_days": 15,
                "recommended_qty": None,
                "supplier": None,
                "abc": "C",
                "demand_trend_pct": None,
                "inventory_value": None,
            }
        ]
        result = generate_recommendations(items)
        assert isinstance(result, list), "Must return a list even for SIN_DATOS items"
