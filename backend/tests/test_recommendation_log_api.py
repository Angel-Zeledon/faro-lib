"""
HTTP-level tests for the recommendation-log reads:

  GET /inventory/recommendation-log/cost-of-ignoring
  GET /inventory/recommendation-log/{sku}/why-changed

Pins:
  - cost-of-ignoring: "ordered" / "likely_stockout" / "no_po_no_stockout_observed"
    outcomes; a rejected PO line still counts as ignored; missing price or
    missing demand rate degrades to a null value with a reason, never a
    fabricated zero; an empty window returns a well-shaped, empty report.
  - why-changed: no-history / single-history / two-history behaviour, and
    that `session_changed` correctly separates "the tenant's own data moved"
    from "the model was retrained".
  - permissions: both endpoints are readable by a viewer (get_current_user).
  - tenant isolation: tenant A's recorded rows are invisible to tenant B.
"""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from backend.db.connection import execute, query_one
from backend.inventory import recommendation_log


def _item(sku, signal="PEDIR_YA", recommended_qty=10.0, current_stock=5.0,
          reorder_point=12.0, safety_stock=3.0, daily_demand=2.5, lead_time_days=7):
    return {
        "sku": sku,
        "signal": signal,
        "recommended_qty": recommended_qty,
        "current_stock": current_stock,
        "reorder_point": reorder_point,
        "daily_demand": daily_demand,
        "lead_time_days": lead_time_days,
        "calc_explanation": {"safety_stock": safety_stock},
    }


def _seed_stock(tenant_id, sku, sale_price=None):
    execute(
        """INSERT INTO inventory_stock (tenant_id, sku, current_stock, sale_price)
           VALUES (%s, %s, 0, %s)
           ON CONFLICT (tenant_id, sku, warehouse) DO UPDATE
           SET sale_price = EXCLUDED.sale_price""",
        (tenant_id, sku, sale_price),
    )


def _seed_snapshot(tenant_id, sku, current_stock, when):
    execute(
        "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, recorded_at) "
        "VALUES (%s, %s, %s, %s)",
        (tenant_id, sku, current_stock, when),
    )


def _seed_po(tenant_id, sku, session_id, generated_at, status="approved"):
    po = query_one(
        "INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units) "
        "VALUES (%s, %s, 1, 10) RETURNING id",
        (tenant_id, session_id),
    )
    execute(
        "INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, recommended_qty, final_qty, status) "
        "VALUES (%s, %s, %s, 10, 10, %s)",
        (po["id"], tenant_id, sku, status),
    )
    execute("UPDATE inventory_po_log SET generated_at=%s WHERE id=%s", (generated_at, po["id"]))
    return po["id"]


# ── cost-of-ignoring ─────────────────────────────────────────────────────────

def test_cost_of_ignoring_empty_window_is_well_shaped(client, analyst_headers):
    r = client.get(
        "/api/v1/inventory/recommendation-log/cost-of-ignoring",
        headers=analyst_headers,
        params={"from_date": "2026-01-01", "to_date": "2026-01-31"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["skus"] == []
    assert data["summary"]["skus_flagged"] == 0
    assert data["summary"]["total_estimated_lost_units"] is None
    assert data["summary"]["total_estimated_lost_value"] is None


def test_cost_of_ignoring_po_followed_reports_no_loss(client, analyst_headers, test_tenant):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    recommendation_log.record_recommendations(
        tenant_id, "sess-1", [_item(sku, signal="PEDIR_YA")], as_of=date(2026, 2, 1))
    _seed_po(tenant_id, sku, "sess-1", datetime(2026, 2, 3, tzinfo=timezone.utc))

    r = client.get(
        "/api/v1/inventory/recommendation-log/cost-of-ignoring",
        headers=analyst_headers,
        params={"from_date": "2026-02-01", "to_date": "2026-02-28"},
    )
    data = r.json()["data"]
    assert len(data["skus"]) == 1
    entry = data["skus"][0]
    assert entry["sku"] == sku
    assert entry["outcome"] == "ordered"
    assert entry["lost_units"] is None
    assert entry["lost_value"] is None


def test_cost_of_ignoring_stockout_with_known_price(client, analyst_headers, test_tenant):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    _seed_stock(tenant_id, sku, sale_price=20.0)
    recommendation_log.record_recommendations(
        tenant_id, "sess-1",
        [_item(sku, signal="PEDIR_YA", daily_demand=4.0)],
        as_of=date(2026, 3, 1))
    # No PO follows. Stock is observed at zero on the 3rd, recovered on the
    # 6th: three days out of stock.
    _seed_snapshot(tenant_id, sku, 0, datetime(2026, 3, 3, tzinfo=timezone.utc))
    _seed_snapshot(tenant_id, sku, 15, datetime(2026, 3, 6, tzinfo=timezone.utc))

    r = client.get(
        "/api/v1/inventory/recommendation-log/cost-of-ignoring",
        headers=analyst_headers,
        params={"from_date": "2026-03-01", "to_date": "2026-03-10"},
    )
    entry = r.json()["data"]["skus"][0]
    assert entry["outcome"] == "likely_stockout"
    assert entry["days_out_of_stock"] == 3
    assert entry["lost_units"] == pytest.approx(12.0)
    assert entry["lost_value"] == pytest.approx(240.0)
    assert entry["partial_window"] is False


def test_cost_of_ignoring_stockout_unknown_price_reports_units_only(client, analyst_headers, test_tenant):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    # No inventory_stock row at all: sale price is unknown.
    recommendation_log.record_recommendations(
        tenant_id, "sess-1",
        [_item(sku, signal="PEDIR_YA", daily_demand=2.0)],
        as_of=date(2026, 4, 1))
    _seed_snapshot(tenant_id, sku, 0, datetime(2026, 4, 2, tzinfo=timezone.utc))
    _seed_snapshot(tenant_id, sku, 10, datetime(2026, 4, 4, tzinfo=timezone.utc))

    r = client.get(
        "/api/v1/inventory/recommendation-log/cost-of-ignoring",
        headers=analyst_headers,
        params={"from_date": "2026-04-01", "to_date": "2026-04-10"},
    )
    entry = r.json()["data"]["skus"][0]
    assert entry["outcome"] == "likely_stockout"
    assert entry["lost_units"] is not None
    assert entry["lost_value"] is None
    assert entry["lost_value_reason"] == "sale_price_unknown"


def test_cost_of_ignoring_no_po_no_stockout_claims_nothing(client, analyst_headers, test_tenant):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    recommendation_log.record_recommendations(
        tenant_id, "sess-1", [_item(sku, signal="PEDIR_PRONTO")],
        as_of=date(2026, 5, 1))
    # No PO, and stock is observed but never at/below zero.
    _seed_snapshot(tenant_id, sku, 8, datetime(2026, 5, 2, tzinfo=timezone.utc))

    r = client.get(
        "/api/v1/inventory/recommendation-log/cost-of-ignoring",
        headers=analyst_headers,
        params={"from_date": "2026-05-01", "to_date": "2026-05-10"},
    )
    entry = r.json()["data"]["skus"][0]
    assert entry["outcome"] == "no_po_no_stockout_observed"
    assert entry["lost_units"] is None
    assert entry["lost_value"] is None


def test_cost_of_ignoring_rejected_po_line_does_not_count_as_ordered(client, analyst_headers, test_tenant):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    recommendation_log.record_recommendations(
        tenant_id, "sess-1", [_item(sku, signal="PEDIR_YA")], as_of=date(2026, 6, 1))
    _seed_po(tenant_id, sku, "sess-1", datetime(2026, 6, 2, tzinfo=timezone.utc), status="rejected")

    r = client.get(
        "/api/v1/inventory/recommendation-log/cost-of-ignoring",
        headers=analyst_headers,
        params={"from_date": "2026-06-01", "to_date": "2026-06-10"},
    )
    entry = r.json()["data"]["skus"][0]
    assert entry["outcome"] != "ordered", "a rejected PO line is still an ignored recommendation"


# ── why-changed ──────────────────────────────────────────────────────────────

def test_why_changed_no_history(client, analyst_headers):
    sku = f"NEVER-SEEN-{uuid4().hex[:6]}"
    r = client.get(
        f"/api/v1/inventory/recommendation-log/{sku}/why-changed",
        headers=analyst_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is False
    assert data["reason"] == "no_recorded_recommendations"


def test_why_changed_single_history(client, analyst_headers, test_tenant):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    recommendation_log.record_recommendations(
        tenant_id, "sess-1", [_item(sku)], as_of=date(2026, 7, 1))

    r = client.get(
        f"/api/v1/inventory/recommendation-log/{sku}/why-changed",
        headers=analyst_headers,
    )
    data = r.json()["data"]
    assert data["available"] is False
    assert data["reason"] == "no_previous_recommendation"


def test_why_changed_same_session_flags_operational_change(client, analyst_headers, test_tenant):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    recommendation_log.record_recommendations(
        tenant_id, "sess-1",
        [_item(sku, current_stock=10.0, daily_demand=2.0)],
        as_of=date(2026, 8, 1))
    recommendation_log.record_recommendations(
        tenant_id, "sess-1",
        [_item(sku, current_stock=4.0, daily_demand=2.0)],
        as_of=date(2026, 8, 2))

    r = client.get(
        f"/api/v1/inventory/recommendation-log/{sku}/why-changed",
        headers=analyst_headers,
    )
    data = r.json()["data"]
    assert data["available"] is True
    assert data["session_changed"] is False
    assert data["fields"]["current_stock"]["delta"] == pytest.approx(-6.0)
    assert data["fields"]["avg_daily_demand"]["delta"] == pytest.approx(0.0)
    assert data["fields"]["current_stock"]["origin"] == "operational"
    assert data["fields"]["avg_daily_demand"]["origin"] == "session"


def test_why_changed_new_session_flags_it(client, analyst_headers, test_tenant):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    recommendation_log.record_recommendations(
        tenant_id, "sess-1", [_item(sku, daily_demand=2.0)], as_of=date(2026, 9, 10))
    recommendation_log.record_recommendations(
        tenant_id, "sess-2", [_item(sku, daily_demand=5.0)], as_of=date(2026, 9, 11))

    r = client.get(
        f"/api/v1/inventory/recommendation-log/{sku}/why-changed",
        headers=analyst_headers,
    )
    data = r.json()["data"]
    assert data["session_changed"] is True
    assert data["fields"]["avg_daily_demand"]["delta"] == pytest.approx(3.0)


# ── permissions ──────────────────────────────────────────────────────────────

def test_viewer_can_read_cost_of_ignoring(client, viewer_headers):
    r = client.get(
        "/api/v1/inventory/recommendation-log/cost-of-ignoring",
        headers=viewer_headers,
    )
    assert r.status_code == 200


def test_viewer_can_read_why_changed(client, viewer_headers):
    r = client.get(
        "/api/v1/inventory/recommendation-log/SKU-X/why-changed",
        headers=viewer_headers,
    )
    assert r.status_code == 200


def test_unauthenticated_is_rejected(client):
    r = client.get("/api/v1/inventory/recommendation-log/cost-of-ignoring")
    assert r.status_code in (401, 403)


# ── tenant isolation ─────────────────────────────────────────────────────────

def test_tenant_cannot_read_another_tenants_recommendation_rows(
    client, test_tenant, make_tenant_user_headers,
):
    tenant_id = test_tenant["id"]
    sku = f"SKU-{uuid4().hex[:6]}"
    recommendation_log.record_recommendations(
        tenant_id, "sess-1", [_item(sku, signal="PEDIR_YA")], as_of=date(2026, 10, 1))

    other_headers = make_tenant_user_headers(role="analyst")

    r = client.get(
        f"/api/v1/inventory/recommendation-log/{sku}/why-changed",
        headers=other_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is False, "another tenant could read this tenant's recorded row"
    assert data["reason"] == "no_recorded_recommendations"

    r2 = client.get(
        "/api/v1/inventory/recommendation-log/cost-of-ignoring",
        headers=other_headers,
        params={"from_date": "2026-10-01", "to_date": "2026-10-10"},
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["skus"] == []
