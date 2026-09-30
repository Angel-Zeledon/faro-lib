"""GET /inventory/dead-capital — "capital parado": money that is not moving.

The one surface for money that is not moving since `/inventory/dead-stock`
(depletion against a forecast, which needed a completed session) was retired
on 2026-09-30, stability.md 19.2. This endpoint needs no session: it reads `inventory_snapshots` — real recorded stock levels — to ask
how long a SKU's stock has gone without falling, and prices what is sitting
in it. Every test here builds the snapshot history directly with explicit
`recorded_at` timestamps so the "days still" math is pinned against ground
truth the test itself controls, not against the endpoint's own arithmetic.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute
from backend.inventory import service as inv_svc
from backend.inventory.dead_capital import REASON_NO_UNIT_COST


def _snapshot(tenant_id: str, sku: str, level: float, days_ago: int, warehouse: str = "principal"):
    """Back-dates a snapshot row the way a real one would have looked had it
    been recorded `days_ago` days ago."""
    recorded_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    execute(
        "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, sku, level, warehouse, recorded_at),
    )


def _get(client, headers, **params):
    r = client.get("/api/v1/inventory/dead-capital", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _by_sku(data: dict) -> dict:
    return {i["sku"]: i for i in data["items"]}


class TestStillnessDefinition:
    def test_flat_for_the_whole_window_qualifies_unconfirmed(self, client, auth_headers, test_tenant):
        """No decrease anywhere in 120 days of history: legitimate evidence of
        stillness, but no exact 'this is the day it stopped selling' — days_still
        is the span of the data itself, and `days_still_exact` says so."""
        tid = test_tenant["id"]
        sku = "STILL-FLAT"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 100, "unit_cost": 50})
        _snapshot(tid, sku, 100, days_ago=120)

        data = _get(client, auth_headers)
        item = _by_sku(data)[sku]
        assert item["days_still"] >= 119
        assert item["days_still_exact"] is False
        assert item["value"] == pytest.approx(5000.0)
        assert item["value_unknown_reason"] is None

    def test_exact_fall_is_found_and_dated(self, client, auth_headers, test_tenant):
        """A real decrease 95 days ago, flat since: days_still lands exactly on
        that day and is reported as exact, not estimated."""
        tid = test_tenant["id"]
        sku = "STILL-EXACT-FALL"
        _snapshot(tid, sku, 300, days_ago=200)
        _snapshot(tid, sku, 50, days_ago=95)
        inv_svc.upsert_stock(tid, sku, {"current_stock": 50, "unit_cost": 10})

        data = _get(client, auth_headers)
        item = _by_sku(data)[sku]
        assert 94 <= item["days_still"] <= 96
        assert item["days_still_exact"] is True
        assert item["value"] == pytest.approx(500.0)

    def test_two_days_of_history_never_claims_ninety(self, client, auth_headers, test_tenant):
        """The exact defect named in the brief: thin history must not be read as
        long stillness. Two days of flat data stays a 1-2 day answer and is
        excluded from a 90-day list, not promoted to it."""
        tid = test_tenant["id"]
        sku = "STILL-THIN-HISTORY"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 20, "unit_cost": 5})
        _snapshot(tid, sku, 20, days_ago=2)

        data = _get(client, auth_headers, window_days=7)
        assert sku not in _by_sku(data)
        assert data["excluded_too_recent"] >= 1

    def test_recent_movement_excludes_from_the_default_window(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = "STILL-RECENT-MOVE"
        _snapshot(tid, sku, 100, days_ago=30)
        _snapshot(tid, sku, 10, days_ago=10)  # fell 10 days ago
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "unit_cost": 5})

        data = _get(client, auth_headers)  # default window = 90
        assert sku not in _by_sku(data)

    def test_no_snapshot_history_is_excluded_not_guessed(self, client, auth_headers, test_tenant):
        """A SKU whose stock was set but never got a recorded snapshot (a
        defensive case — `_record_snapshot` swallows its own failures) must not
        be silently claimed as either moving or still."""
        tid = test_tenant["id"]
        sku = "STILL-NO-HISTORY"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 40, "unit_cost": 5})
        execute("DELETE FROM inventory_snapshots WHERE tenant_id = %s AND sku = %s", (tid, sku))

        data = _get(client, auth_headers, window_days=7)
        assert sku not in _by_sku(data)
        assert data["excluded_no_history"] >= 1

    def test_zero_stock_is_not_capital(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = "STILL-ZERO-STOCK"
        _snapshot(tid, sku, 50, days_ago=150)
        inv_svc.upsert_stock(tid, sku, {"current_stock": 0, "unit_cost": 5})

        data = _get(client, auth_headers, window_days=7)
        assert sku not in _by_sku(data)

    def test_window_days_is_a_screen_filter(self, client, auth_headers, test_tenant):
        """95 days still: visible under a 90-day window, invisible under a
        100-day one. The same underlying data, just a different cut of it — this
        is what makes the window a filter, not a stored setting."""
        tid = test_tenant["id"]
        sku = "STILL-WINDOW-EDGE"
        _snapshot(tid, sku, 300, days_ago=200)
        _snapshot(tid, sku, 20, days_ago=95)
        inv_svc.upsert_stock(tid, sku, {"current_stock": 20, "unit_cost": 3})

        wide = _get(client, auth_headers, window_days=90)
        narrow = _get(client, auth_headers, window_days=100)
        assert sku in _by_sku(wide)
        assert sku not in _by_sku(narrow)


class TestValuation:
    def test_unknown_cost_is_null_never_zero(self, client, auth_headers, test_tenant):
        """The defect this brief calls out by name: an unpriced SKU must read as
        'we don't know', never as 'this is worth nothing'."""
        tid = test_tenant["id"]
        sku = "STILL-NO-COST"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 75})  # no unit_cost
        _snapshot(tid, sku, 75, days_ago=100)

        data = _get(client, auth_headers)
        item = _by_sku(data)[sku]
        assert item["value"] is None
        assert item["value_unknown_reason"] == REASON_NO_UNIT_COST
        assert item["unit_cost"] is None
        # Present in the list (not dropped) and counted separately from the
        # priced total, which must not silently treat it as 0.
        assert data["unpriced_sku_count"] >= 1

    def test_total_value_excludes_unpriced_items(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        priced_sku, unpriced_sku = "STILL-PRICED", "STILL-UNPRICED"
        inv_svc.upsert_stock(tid, priced_sku, {"current_stock": 10, "unit_cost": 100})
        _snapshot(tid, priced_sku, 10, days_ago=100)
        inv_svc.upsert_stock(tid, unpriced_sku, {"current_stock": 999})
        _snapshot(tid, unpriced_sku, 999, days_ago=100)

        data = _get(client, auth_headers)
        assert data["total_value"] == pytest.approx(1000.0)

    def test_ranked_worst_first_by_money(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        cheap, expensive = "STILL-CHEAP", "STILL-EXPENSIVE"
        inv_svc.upsert_stock(tid, cheap, {"current_stock": 1, "unit_cost": 1})
        _snapshot(tid, cheap, 1, days_ago=100)
        inv_svc.upsert_stock(tid, expensive, {"current_stock": 1000, "unit_cost": 500})
        _snapshot(tid, expensive, 1000, days_ago=100)

        data = _get(client, auth_headers)
        skus_in_order = [i["sku"] for i in data["items"] if i["sku"] in {cheap, expensive}]
        assert skus_in_order == [expensive, cheap]


class TestSignalAgreement:
    def test_works_with_no_session_at_all(self, client, auth_headers, test_tenant):
        """No completed session for this tenant — the semáforo has nothing to
        say, but capital parado does not need it to compute money and stillness."""
        tid = test_tenant["id"]
        sku = "STILL-NO-SESSION"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 5, "unit_cost": 20})
        _snapshot(tid, sku, 5, days_ago=100)

        data = _get(client, auth_headers)
        item = _by_sku(data)[sku]
        assert item["signal"] is None


class TestAccessControl:
    def test_viewer_can_read_it(self, client, viewer_headers, test_tenant):
        """A read needs only get_current_user — no mutation happens here, so
        there is no viewer/analyst pair to prove, only that a read is not
        accidentally gated to a higher role."""
        r = client.get("/api/v1/inventory/dead-capital", headers=viewer_headers)
        assert r.status_code == 200, r.text

    def test_unauthenticated_is_rejected(self, client):
        r = client.get("/api/v1/inventory/dead-capital")
        assert r.status_code in (401, 403)


class TestTenantIsolation:
    def test_another_tenant_sees_nothing_of_this_one(
        self, client, auth_headers, test_tenant, make_tenant_user_headers,
    ):
        tid = test_tenant["id"]
        sku = "STILL-TENANT-A-ONLY"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 42, "unit_cost": 42})
        _snapshot(tid, sku, 42, days_ago=100)

        other_headers = make_tenant_user_headers(role="admin")

        mine = _get(client, auth_headers)
        theirs = _get(client, other_headers)

        assert sku in _by_sku(mine)
        assert sku not in _by_sku(theirs)
        assert theirs["sku_count"] == 0
        assert theirs["total_value"] == 0
