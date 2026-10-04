"""GET /inventory/supplier-cost-inflation — the price history nobody realised
`inventory_po_items.unit_cost` already was.

Every test seeds `inventory_po_log`/`inventory_po_items` directly (like
`test_dead_capital.py` seeds `inventory_snapshots` directly) so the dated
cost observations are ground truth the test itself controls, not the
endpoint's own arithmetic. See `backend/inventory/cost_alerts.py`'s module
docstring for why an observation requires a RECEIVED order.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute


def _seed_reception(
    tenant_id: str, sku: str, supplier: str, unit_cost: float, qty: float,
    days_ago: float, *, reception_status: str = "received",
    line_status: str = "approved", warehouse: str = "principal",
    display_name: str | None = None,
) -> str:
    """Back-dates one PO (header + one line) the way a real reception would
    have looked `days_ago` days ago. Returns the po_log id."""
    po_log_id = str(uuid.uuid4())
    received_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    generated_at = received_at - timedelta(days=1)
    execute(
        """INSERT INTO inventory_po_log
               (id, tenant_id, session_id, generated_at, reception_status, received_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (po_log_id, tenant_id, f"sess_{uuid.uuid4().hex[:6]}", generated_at,
         reception_status, received_at if reception_status != "pending" else None),
    )
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, received_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (po_log_id, tenant_id, sku, display_name or sku, supplier, line_status,
         qty, qty, qty if reception_status != "pending" else None, unit_cost, warehouse),
    )
    return po_log_id


def _get(client, headers, **params):
    r = client.get("/api/v1/inventory/supplier-cost-inflation", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _by_supplier(data: dict) -> dict:
    return {s["supplier"]: s for s in data["suppliers"]}


class TestInflationDetection:
    def test_two_increases_are_flagged_with_cumulative_pct(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku, supplier = f"INF-{uuid.uuid4().hex[:6]}", "Proveedor Alza"
        _seed_reception(tid, sku, supplier, 10.0, 100, days_ago=300)
        _seed_reception(tid, sku, supplier, 12.0, 100, days_ago=200)
        _seed_reception(tid, sku, supplier, 15.0, 100, days_ago=100)

        data = _get(client, auth_headers)
        sp = _by_supplier(data)[supplier]
        assert sp["increases_count"] == 2
        assert sp["cumulative_pct"] == pytest.approx(50.0)  # (15-10)/10 * 100
        product = next(p for p in sp["worst_products"] if p["sku"] == sku)
        assert product["first_cost"] == pytest.approx(10.0)
        assert product["last_cost"] == pytest.approx(15.0)
        assert product["increases_count"] == 2

    def test_only_decreases_are_never_flagged(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku, supplier = f"INF-{uuid.uuid4().hex[:6]}", "Proveedor Baja"
        _seed_reception(tid, sku, supplier, 10.0, 50, days_ago=200)
        _seed_reception(tid, sku, supplier, 8.0, 50, days_ago=50)

        data = _get(client, auth_headers)
        assert supplier not in _by_supplier(data)

    def test_single_observation_excluded_and_counted(self, client, auth_headers, test_tenant):
        """One reception proves nothing about a trend — must not be silently
        read as flat, or as anything at all."""
        tid = test_tenant["id"]
        sku, supplier = f"INF-{uuid.uuid4().hex[:6]}", "Proveedor Unico"
        _seed_reception(tid, sku, supplier, 10.0, 50, days_ago=30)

        data = _get(client, auth_headers)
        assert supplier not in _by_supplier(data)
        assert data["skus_single_observation"] >= 1

    def test_unreceived_po_is_not_a_price_observation(self, client, auth_headers, test_tenant):
        """A quoted order that never arrived proves nothing was actually
        paid — it must not count toward the trend."""
        tid = test_tenant["id"]
        sku, supplier = f"INF-{uuid.uuid4().hex[:6]}", "Proveedor Pendiente"
        _seed_reception(tid, sku, supplier, 10.0, 50, days_ago=300)
        _seed_reception(tid, sku, supplier, 20.0, 50, days_ago=1, reception_status="pending")

        data = _get(client, auth_headers)
        # Only one real (received) observation exists, so this pair is a
        # single-observation series, never a flagged two-point rise to 20.
        assert supplier not in _by_supplier(data)

    def test_rejected_line_is_not_a_price_observation(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku, supplier = f"INF-{uuid.uuid4().hex[:6]}", "Proveedor Rechazo"
        _seed_reception(tid, sku, supplier, 10.0, 50, days_ago=300)
        _seed_reception(tid, sku, supplier, 99.0, 50, days_ago=100, line_status="rejected")

        data = _get(client, auth_headers)
        assert supplier not in _by_supplier(data)

    def test_window_days_filters_old_observations(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku, supplier = f"INF-{uuid.uuid4().hex[:6]}", "Proveedor Viejo"
        _seed_reception(tid, sku, supplier, 10.0, 50, days_ago=500)
        _seed_reception(tid, sku, supplier, 20.0, 50, days_ago=490)

        narrow = _get(client, auth_headers, window_days=365)
        wide = _get(client, auth_headers, window_days=600)
        assert supplier not in _by_supplier(narrow)
        assert supplier in _by_supplier(wide)

    def test_no_supplier_name_is_excluded_and_counted(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = f"INF-{uuid.uuid4().hex[:6]}"
        _seed_reception(tid, sku, "", 10.0, 50, days_ago=300)
        _seed_reception(tid, sku, "", 20.0, 50, days_ago=100)

        data = _get(client, auth_headers)
        assert data["lines_excluded_no_supplier"] >= 2
        assert data["suppliers"] == []


class TestAggregation:
    def test_same_order_multi_warehouse_lines_are_one_qty_weighted_observation(
        self, client, auth_headers, test_tenant,
    ):
        """Two lines of the SAME po_log/sku/supplier (a multi-warehouse order)
        must collapse into ONE observation, qty-weighted — not be read as two
        separate price points close in time."""
        tid = test_tenant["id"]
        sku, supplier = f"INF-{uuid.uuid4().hex[:6]}", "Proveedor Multi"

        po_id = str(uuid.uuid4())
        received_at = datetime.now(timezone.utc) - timedelta(days=200)
        execute(
            """INSERT INTO inventory_po_log
                   (id, tenant_id, session_id, generated_at, reception_status, received_at)
               VALUES (%s, %s, %s, %s, 'received', %s)""",
            (po_id, tid, "sess_x", received_at - timedelta(days=1), received_at),
        )
        # Warehouse A: cost 10, qty 10. Warehouse B: cost 12, qty 10.
        # Weighted average = 11.0 — never 10 or 12 alone.
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, display_name, supplier, status,
                    recommended_qty, final_qty, received_qty, unit_cost, warehouse)
               VALUES (%s, %s, %s, %s, %s, 'approved', 10, 10, 10, 10.0, 'A'),
                      (%s, %s, %s, %s, %s, 'approved', 10, 10, 10, 12.0, 'B')""",
            (po_id, tid, sku, sku, supplier, po_id, tid, sku, sku, supplier),
        )
        # A second, later, single-line order that is a real increase over the
        # weighted 11.0 baseline.
        _seed_reception(tid, sku, supplier, 13.0, 20, days_ago=50)

        data = _get(client, auth_headers)
        sp = _by_supplier(data)[supplier]
        product = next(p for p in sp["worst_products"] if p["sku"] == sku)
        assert product["first_cost"] == pytest.approx(11.0)
        assert product["last_cost"] == pytest.approx(13.0)
        assert product["increases_count"] == 1

    def test_supplier_cumulative_pct_is_qty_weighted_not_a_flat_average(
        self, client, auth_headers, test_tenant,
    ):
        """A supplier's headline % leans toward the SKU actually bought in
        volume, not a flat average across SKUs of very different importance
        — see the aggregation comment in cost_alerts.get_supplier_cost_inflation."""
        tid = test_tenant["id"]
        supplier = "Proveedor Ponderado"
        big_sku, small_sku = f"INF-BIG-{uuid.uuid4().hex[:6]}", f"INF-SMALL-{uuid.uuid4().hex[:6]}"

        # Big-volume SKU: +10% (1000 units observed)
        _seed_reception(tid, big_sku, supplier, 100.0, 1000, days_ago=300)
        _seed_reception(tid, big_sku, supplier, 110.0, 1000, days_ago=100)
        # Small-volume SKU: +100% (1 unit observed)
        _seed_reception(tid, small_sku, supplier, 1.0, 1, days_ago=300)
        _seed_reception(tid, small_sku, supplier, 2.0, 1, days_ago=100)

        data = _get(client, auth_headers)
        sp = _by_supplier(data)[supplier]
        # A flat average of 10% and 100% would be 55%. Weighted by qty
        # (2000 units at ~10% vs 2 units at 100%) it must land close to 10%.
        assert sp["cumulative_pct"] < 20.0


class TestAccessControl:
    def test_viewer_can_read_it(self, client, viewer_headers, test_tenant):
        r = client.get("/api/v1/inventory/supplier-cost-inflation", headers=viewer_headers)
        assert r.status_code == 200, r.text

    def test_unauthenticated_is_rejected(self, client):
        r = client.get("/api/v1/inventory/supplier-cost-inflation")
        assert r.status_code in (401, 403)


class TestTenantIsolation:
    def test_another_tenant_sees_nothing_of_this_one(
        self, client, auth_headers, test_tenant, make_tenant_user_headers,
    ):
        tid = test_tenant["id"]
        sku, supplier = f"INF-{uuid.uuid4().hex[:6]}", "Proveedor Aislado"
        _seed_reception(tid, sku, supplier, 10.0, 50, days_ago=200)
        _seed_reception(tid, sku, supplier, 20.0, 50, days_ago=50)

        other_headers = make_tenant_user_headers(role="admin")

        mine = _get(client, auth_headers)
        theirs = _get(client, other_headers)

        assert supplier in _by_supplier(mine)
        assert supplier not in _by_supplier(theirs)
