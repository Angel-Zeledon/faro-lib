"""GET /inventory/margin-erosion — "te subieron el costo y no moviste el
precio": cost history (same observations as supplier-cost-inflation) crossed
with the SKU's current sale_price.

Honesty constraint under test throughout this file: StockAI stores only the
CURRENT sale_price, never a history of it. So `margin_pct_then` is NOT a
historical margin — it is today's price against a past cost — and the
response says so once (`price_history_available: False`) rather than
implying a fact the data cannot support. See
`backend/inventory/cost_alerts.py`'s module docstring.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute
from backend.inventory import service as inv_svc


def _seed_reception(
    tenant_id: str, sku: str, supplier: str, unit_cost: float, qty: float, days_ago: float,
) -> str:
    po_log_id = str(uuid.uuid4())
    received_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    execute(
        """INSERT INTO inventory_po_log
               (id, tenant_id, session_id, generated_at, reception_status, received_at)
           VALUES (%s, %s, %s, %s, 'received', %s)""",
        (po_log_id, tenant_id, f"sess_{uuid.uuid4().hex[:6]}",
         received_at - timedelta(days=1), received_at),
    )
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, received_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, %s, 'approved', %s, %s, %s, %s, 'principal')""",
        (po_log_id, tenant_id, sku, sku, supplier, qty, qty, qty, unit_cost),
    )
    return po_log_id


def _get(client, headers, **params):
    r = client.get("/api/v1/inventory/margin-erosion", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _by_sku(data: dict) -> dict:
    return {i["sku"]: i for i in data["items"]}


class TestErosionDetection:
    def test_cost_up_price_flat_is_flagged_and_attributed_to_cost(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sku = f"MRG-{uuid.uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "sale_price": 100.0})
        _seed_reception(tid, sku, "Proveedor X", 60.0, 20, days_ago=300)
        _seed_reception(tid, sku, "Proveedor X", 80.0, 20, days_ago=50)

        data = _get(client, auth_headers)
        assert data["price_history_available"] is False
        item = _by_sku(data)[sku]
        assert item["margin_pct_then"] == pytest.approx(40.0)   # (100-60)/100
        assert item["margin_pct_now"] == pytest.approx(20.0)    # (100-80)/100
        assert item["erosion_pts"] == pytest.approx(20.0)
        assert item["unit_margin_then"] == pytest.approx(40.0)
        assert item["unit_margin_now"] == pytest.approx(20.0)
        assert item["cost_change_pct"] == pytest.approx((80 - 60) / 60 * 100, abs=0.1)

    def test_negative_margin_is_reported_not_clamped(self, client, auth_headers, test_tenant):
        """calc_unit_margin's discipline: a loss-making margin must be
        reported as-is, never clamped to 0."""
        tid = test_tenant["id"]
        sku = f"MRG-{uuid.uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "sale_price": 50.0})
        _seed_reception(tid, sku, "Proveedor Y", 20.0, 10, days_ago=300)
        _seed_reception(tid, sku, "Proveedor Y", 60.0, 10, days_ago=50)  # cost now exceeds price

        data = _get(client, auth_headers)
        item = _by_sku(data)[sku]
        assert item["unit_margin_now"] == pytest.approx(-10.0)
        assert item["margin_pct_now"] == pytest.approx(-20.0)

    def test_margin_improvement_is_not_flagged(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MRG-{uuid.uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "sale_price": 100.0})
        _seed_reception(tid, sku, "Proveedor Z", 80.0, 10, days_ago=300)
        _seed_reception(tid, sku, "Proveedor Z", 60.0, 10, days_ago=50)  # cost FELL

        data = _get(client, auth_headers)
        assert sku not in _by_sku(data)


class TestExclusions:
    def test_no_sale_price_is_excluded_and_counted_never_priced_at_zero(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sku = f"MRG-{uuid.uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10})  # no sale_price
        _seed_reception(tid, sku, "Proveedor W", 10.0, 10, days_ago=300)
        _seed_reception(tid, sku, "Proveedor W", 20.0, 10, days_ago=50)

        data = _get(client, auth_headers)
        assert sku not in _by_sku(data)
        assert data["excluded_no_sale_price"] >= 1

    def test_single_cost_observation_is_excluded_and_counted(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MRG-{uuid.uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "sale_price": 50.0})
        _seed_reception(tid, sku, "Proveedor V", 10.0, 10, days_ago=100)

        data = _get(client, auth_headers)
        assert sku not in _by_sku(data)
        assert data["excluded_no_cost_history"] >= 1

    def test_zero_sale_price_is_excluded_not_divided_by(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MRG-{uuid.uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "sale_price": 0})
        _seed_reception(tid, sku, "Proveedor U", 10.0, 10, days_ago=300)
        _seed_reception(tid, sku, "Proveedor U", 20.0, 10, days_ago=50)

        data = _get(client, auth_headers)
        assert sku not in _by_sku(data)
        assert data["excluded_invalid_price"] >= 1

    def test_small_erosion_is_filtered_by_min_erosion_pts(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"MRG-{uuid.uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "sale_price": 100.0})
        _seed_reception(tid, sku, "Proveedor T", 60.0, 10, days_ago=300)
        _seed_reception(tid, sku, "Proveedor T", 60.2, 10, days_ago=50)  # ~0.2pt erosion

        default_window = _get(client, auth_headers)  # default min_erosion_pts = 0.5
        assert sku not in _by_sku(default_window)

        lenient = _get(client, auth_headers, min_erosion_pts=0.1)
        assert sku in _by_sku(lenient)


class TestAccessControl:
    def test_viewer_can_read_it(self, client, viewer_headers, test_tenant):
        r = client.get("/api/v1/inventory/margin-erosion", headers=viewer_headers)
        assert r.status_code == 200, r.text

    def test_unauthenticated_is_rejected(self, client):
        r = client.get("/api/v1/inventory/margin-erosion")
        assert r.status_code in (401, 403)


class TestTenantIsolation:
    def test_another_tenant_sees_nothing_of_this_one(
        self, client, auth_headers, test_tenant, make_tenant_user_headers,
    ):
        tid = test_tenant["id"]
        sku = f"MRG-{uuid.uuid4().hex[:6]}"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "sale_price": 100.0})
        _seed_reception(tid, sku, "Proveedor Aislado", 60.0, 10, days_ago=300)
        _seed_reception(tid, sku, "Proveedor Aislado", 80.0, 10, days_ago=50)

        other_headers = make_tenant_user_headers(role="admin")

        mine = _get(client, auth_headers)
        theirs = _get(client, other_headers)

        assert sku in _by_sku(mine)
        assert sku not in _by_sku(theirs)
