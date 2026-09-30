"""GET /inventory/forecast-money — the forecast in money.

stability.md #20, item 1: the engine predicts units, the product already
knows `inventory_stock.sale_price` and `inventory_stock.unit_cost` per SKU,
and nobody had multiplied them. This reads the same per-SKU champion-model
forecast `/inventory/status` already serves and projects it into revenue,
cost and gross margin over the session's forecast horizon.

The `completed_session` fixture seeds 3 SKUs (SKU_001..003) with a
randomized 14-day forecast under two models (lightgbm, prophet). Tests that
need an exact units total overwrite one SKU's forecast with a single,
deterministic model series — `_pick_model` returns it unconditionally when
it is the only model present, so the champion-selection logic is exercised
without reimplementing it in the test.
"""

from datetime import date, timedelta

import pytest

from backend.db import session_store
from backend.inventory import service as inv_svc
from backend.inventory.forecast_money import REASON_NO_SALE_PRICE, REASON_NO_UNIT_COST


def _get(client, headers, session_id=None):
    params = {"session_id": session_id} if session_id else {}
    r = client.get("/api/v1/inventory/forecast-money", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _by_sku(data: dict) -> dict:
    return {i["sku"]: i for i in data["items"]}


def _set_deterministic_forecast(tenant_id: str, session_id: str, sku: str, values: list[float]):
    """Overwrites this ONE SKU's forecast with a single-model, exact series —
    the fixture's other SKUs keep their randomized two-model forecast."""
    forecasts = session_store.get_forecasts(tenant_id, session_id) or {}
    start = date(2023, 4, 1)
    forecasts[sku] = {
        "lightgbm": {
            "historical": [],
            "forecast": [
                {"date": (start + timedelta(days=i)).isoformat(), "value": v}
                for i, v in enumerate(values)
            ],
        }
    }
    session_store.set_forecasts(tenant_id, session_id, forecasts)


class TestProjectionArithmetic:
    def test_units_revenue_cost_margin_match_the_known_forecast(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        tid, sid = test_tenant["id"], completed_session["id"]
        sku = "SKU_001"
        _set_deterministic_forecast(tid, sid, sku, [10.0, 20.0, 30.0])
        inv_svc.upsert_stock(tid, sku, {"current_stock": 5, "sale_price": 5.0, "unit_cost": 3.0})

        data = _get(client, auth_headers, sid)
        item = _by_sku(data)[sku]

        assert item["units_forecast"] == pytest.approx(60.0)
        assert item["revenue"] == pytest.approx(300.0)
        assert item["cost"] == pytest.approx(180.0)
        assert item["margin"] == pytest.approx(120.0)
        assert item["margin_pct"] == pytest.approx(40.0)
        assert item["revenue_unknown_reason"] is None
        assert item["cost_unknown_reason"] is None

    def test_negative_margin_is_reported_not_clamped(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """Selling below cost is the most valuable row on the screen — it must
        never be floored at 0."""
        tid, sid = test_tenant["id"], completed_session["id"]
        sku = "SKU_001"
        _set_deterministic_forecast(tid, sid, sku, [10.0])
        inv_svc.upsert_stock(tid, sku, {"current_stock": 5, "sale_price": 5.0, "unit_cost": 8.0})

        data = _get(client, auth_headers, sid)
        item = _by_sku(data)[sku]

        assert item["margin"] == pytest.approx(-30.0)
        assert item["margin_pct"] == pytest.approx(-60.0)

    def test_price_history_available_is_always_false(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """StockAI stores no price history — the projection is built entirely on
        today's sale_price. Same flag `margin_erosion` uses for the identical
        caveat."""
        data = _get(client, auth_headers, completed_session["id"])
        assert data["price_history_available"] is False


class TestHonestExclusion:
    def test_no_sale_price_excludes_revenue_and_margin_never_zero(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        tid, sid = test_tenant["id"], completed_session["id"]
        sku = "SKU_002"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "unit_cost": 4.0})  # no sale_price

        data = _get(client, auth_headers, sid)
        item = _by_sku(data)[sku]

        assert item["revenue"] is None
        assert item["revenue_unknown_reason"] == REASON_NO_SALE_PRICE
        # Cost is projected from unit_cost alone — it does not need a price to
        # be a real (and useful) number.
        assert item["cost"] is not None
        assert item["cost_unknown_reason"] is None
        # Margin needs BOTH sides, so it stays unknown even though cost is not.
        assert item["margin"] is None
        assert data["excluded_no_price_count"] >= 1

    def test_no_unit_cost_still_prices_revenue_but_not_margin(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        tid, sid = test_tenant["id"], completed_session["id"]
        sku = "SKU_003"
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "sale_price": 9.0})  # no unit_cost

        data = _get(client, auth_headers, sid)
        item = _by_sku(data)[sku]

        assert item["revenue"] is not None, "price is known — revenue must not disappear too"
        assert item["cost"] is None
        assert item["cost_unknown_reason"] == REASON_NO_UNIT_COST
        assert item["margin"] is None
        assert data["excluded_no_cost_count"] >= 1

    def test_totals_never_fold_an_unknown_in_as_zero(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """A mix of priced-and-costed, priced-only, and fully-unknown SKUs:
        each total must be the sum of exactly the items that have the data it
        needs, never the whole catalogue with a silent 0 standing in."""
        tid, sid = test_tenant["id"], completed_session["id"]
        _set_deterministic_forecast(tid, sid, "SKU_001", [10.0])   # priced + costed
        _set_deterministic_forecast(tid, sid, "SKU_002", [10.0])   # priced only
        _set_deterministic_forecast(tid, sid, "SKU_003", [10.0])   # neither
        inv_svc.upsert_stock(tid, "SKU_001", {"current_stock": 1, "sale_price": 10.0, "unit_cost": 6.0})
        inv_svc.upsert_stock(tid, "SKU_002", {"current_stock": 1, "sale_price": 20.0})
        inv_svc.upsert_stock(tid, "SKU_003", {"current_stock": 1})

        data = _get(client, auth_headers, sid)

        assert data["total_revenue"] == pytest.approx(10 * 10.0 + 10 * 20.0)
        assert data["total_cost"] == pytest.approx(10 * 6.0)
        assert data["total_margin"] == pytest.approx(10 * (10.0 - 6.0))
        assert data["sku_count"] == 3
        assert data["priced_sku_count"] == 2
        assert data["costed_sku_count"] == 1
        assert data["excluded_no_price_count"] == 1
        assert data["excluded_no_cost_count"] == 1


class TestRankedContribution:
    def test_ranked_by_margin_worst_last_and_contribution_sums_to_the_total(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        tid, sid = test_tenant["id"], completed_session["id"]
        small, big = "SKU_002", "SKU_001"
        _set_deterministic_forecast(tid, sid, big, [10.0])
        _set_deterministic_forecast(tid, sid, small, [10.0])
        inv_svc.upsert_stock(tid, big, {"current_stock": 1, "sale_price": 100.0, "unit_cost": 10.0})
        inv_svc.upsert_stock(tid, small, {"current_stock": 1, "sale_price": 12.0, "unit_cost": 10.0})

        data = _get(client, auth_headers, sid)
        ranked = [i["sku"] for i in data["items"] if i["sku"] in (big, small)]
        assert ranked == [big, small], "the bigger margin must rank first"

        by_sku = _by_sku(data)
        assert by_sku[big]["contribution_pct"] + by_sku[small]["contribution_pct"] == pytest.approx(
            100.0, abs=0.2,
        )

    def test_top10_share_reflects_what_it_claims(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """With 3 SKUs total (fewer than TOP_CONTRIBUTORS=10), every costed SKU
        is a 'top contributor' — the share must read 100%, not something
        smaller that silently excludes SKUs the top-10 window never reached."""
        tid, sid = test_tenant["id"], completed_session["id"]
        for sku in ("SKU_001", "SKU_002", "SKU_003"):
            _set_deterministic_forecast(tid, sid, sku, [10.0])
            inv_svc.upsert_stock(tid, sku, {"current_stock": 1, "sale_price": 20.0, "unit_cost": 5.0})

        data = _get(client, auth_headers, sid)
        assert data["top10_margin_share_pct"] == pytest.approx(100.0)


class TestHorizon:
    def test_horizon_matches_the_stored_forecast_dates(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """The fixture seeds a 14-day forecast starting 2023-04-01 — the
        reported horizon must be read from the stored forecast, not a fixed
        screen default."""
        data = _get(client, auth_headers, completed_session["id"])
        assert data["horizon_days"] == 14
        assert data["horizon_start"] == "2023-04-01"
        assert data["horizon_end"] == "2023-04-14"


class TestAccessControl:
    def test_viewer_can_read_it(self, client, viewer_headers, completed_session):
        r = client.get(
            "/api/v1/inventory/forecast-money",
            params={"session_id": completed_session["id"]},
            headers=viewer_headers,
        )
        assert r.status_code == 200, r.text

    def test_unauthenticated_is_rejected(self, client):
        r = client.get("/api/v1/inventory/forecast-money")
        assert r.status_code in (401, 403)

    def test_no_completed_session_is_a_clear_error_not_empty_data(
        self, client, auth_headers, test_tenant,
    ):
        r = client.get("/api/v1/inventory/forecast-money", headers=auth_headers)
        assert r.status_code == 400, r.text
        assert r.json()["error_code"] == "no_completed_session"


class TestTenantIsolation:
    def test_another_tenant_has_no_session_of_its_own(
        self, client, auth_headers, test_tenant, completed_session, make_tenant_user_headers,
    ):
        """The active-session resolution is per tenant: a second tenant with no
        session of its own must not see (or inherit) this tenant's forecast."""
        other_headers = make_tenant_user_headers(role="admin")
        r = client.get("/api/v1/inventory/forecast-money", headers=other_headers)
        assert r.status_code == 400, r.text
        assert r.json()["error_code"] == "no_completed_session"
