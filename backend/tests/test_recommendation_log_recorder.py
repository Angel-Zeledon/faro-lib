"""
Tests for the daily recommendation-log recorder
(backend/inventory/recommendation_log.py::record_recommendations).

Pins:
  - it persists exactly what it is handed (checked with a direct DB query,
    not the function's return value);
  - one row per (tenant, sku, day): a second write the same day UPDATES the
    existing row instead of creating a second one;
  - a different day creates a genuinely separate row;
  - a malformed item (missing sku/signal) is skipped, not fatal to the rest
    of the batch — this recorder rides along on a call whose real job
    (rendering the status page) must not fail over a history-logging defect.
"""
from datetime import date

from backend.db.connection import query
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


def test_records_exactly_what_it_is_handed(test_tenant):
    tenant_id = test_tenant["id"]
    session_id = "sess-" + tenant_id[:8]
    day = date(2026, 9, 1)
    items = [_item("SKU-A", signal="PEDIR_YA", recommended_qty=42.0,
                    current_stock=3.0, reorder_point=18.0,
                    safety_stock=6.0, daily_demand=4.5, lead_time_days=10)]

    written = recommendation_log.record_recommendations(
        tenant_id, session_id, items, as_of=day)

    assert written == 1
    rows = query(
        "SELECT * FROM inventory_recommendation_log WHERE tenant_id=%s AND sku=%s",
        (tenant_id, "SKU-A"),
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["recorded_on"] == day
    assert r["signal"] == "PEDIR_YA"
    assert r["recommended_qty"] == 42.0
    assert r["current_stock"] == 3.0
    assert r["reorder_point"] == 18.0
    assert r["safety_stock"] == 6.0
    assert r["avg_daily_demand"] == 4.5
    assert r["lead_time_days"] == 10
    assert r["session_id"] == session_id


def test_second_call_same_day_updates_not_duplicates(test_tenant):
    tenant_id = test_tenant["id"]
    day = date(2026, 9, 2)

    recommendation_log.record_recommendations(
        tenant_id, "sess-1", [_item("SKU-B", recommended_qty=10.0, signal="PEDIR_YA")],
        as_of=day)
    recommendation_log.record_recommendations(
        tenant_id, "sess-2", [_item("SKU-B", recommended_qty=99.0, signal="OK")],
        as_of=day)

    rows = query(
        "SELECT * FROM inventory_recommendation_log WHERE tenant_id=%s AND sku=%s",
        (tenant_id, "SKU-B"),
    )
    assert len(rows) == 1, "a second same-day write duplicated the row instead of updating it"
    assert rows[0]["recommended_qty"] == 99.0
    assert rows[0]["signal"] == "OK"
    assert rows[0]["session_id"] == "sess-2"


def test_different_day_creates_a_second_row(test_tenant):
    tenant_id = test_tenant["id"]
    day1 = date(2026, 9, 3)
    day2 = date(2026, 9, 4)

    recommendation_log.record_recommendations(
        tenant_id, "sess-x", [_item("SKU-C")], as_of=day1)
    recommendation_log.record_recommendations(
        tenant_id, "sess-x", [_item("SKU-C")], as_of=day2)

    rows = query(
        "SELECT recorded_on FROM inventory_recommendation_log "
        "WHERE tenant_id=%s AND sku=%s ORDER BY recorded_on",
        (tenant_id, "SKU-C"),
    )
    assert [r["recorded_on"] for r in rows] == [day1, day2]


def test_malformed_item_is_skipped_not_fatal(test_tenant):
    tenant_id = test_tenant["id"]
    day = date(2026, 9, 5)
    bad_item = {"current_stock": 5.0}  # no sku, no signal
    good_item = _item("SKU-D")

    written = recommendation_log.record_recommendations(
        tenant_id, "sess-y", [bad_item, good_item], as_of=day)

    assert written == 1
    rows = query(
        "SELECT sku FROM inventory_recommendation_log WHERE tenant_id=%s",
        (tenant_id,),
    )
    assert [r["sku"] for r in rows] == ["SKU-D"]


def test_empty_items_writes_nothing(test_tenant):
    tenant_id = test_tenant["id"]
    written = recommendation_log.record_recommendations(tenant_id, "sess-z", [])
    assert written == 0
    rows = query(
        "SELECT sku FROM inventory_recommendation_log WHERE tenant_id=%s",
        (tenant_id,),
    )
    assert rows == []
