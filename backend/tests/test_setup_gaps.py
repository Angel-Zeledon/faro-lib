"""
GET /inventory/setup-gaps — the Pareto view of the stock-configuration wall
(onboarding-friction plan #1, phase 3).

The test that matters is the money one: a cheap high-volume SKU must NOT
outrank an expensive one that dominates spend. Ordering by row count or by
units is exactly the failure this endpoint exists to remove, so every ranking
assertion below is written so that a unit-based implementation fails it.
"""

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


def _flat_forecast(daily: float, days: int = 30) -> dict:
    pts = [{"date": f"2026-01-{i + 1:02d}", "value": daily} for i in range(days)]
    return {"lightgbm": {"forecast": pts}}


def _session_with_forecasts(tenant_id: str, forecasts: dict) -> str:
    from backend.db import session_store
    from backend.sessions.service import create_session

    session = create_session(tenant_id, "usr_test", "setup-gaps-test")
    session_store.set_forecasts(tenant_id, session["id"], forecasts)
    return session["id"]


class TestSetupGapsOrdering:

    def test_orders_by_money_not_by_volume(self, client, auth_headers, test_tenant):
        """
        THE test for this endpoint. `cheap` sells 100x more units than
        `expensive` but is worth a fraction of the purchase; if the ranking
        were by units (or by row count) `cheap` would come first and the user
        would spend their one sitting configuring the SKU that matters least.

        Both SKUs are gaps (no unit_cost), and both carry a sale_price — the
        state a tenant lands in when the wizard mapped a price column but no
        cost, which is the common case this screen was built for.
        """
        tid = test_tenant["id"]
        cheap, expensive = _sku(), _sku()

        # sale_price only: unit_cost stays NULL, so both are gaps.
        for sku, price in ((cheap, 0.50), (expensive, 200.0)):
            r = client.put(f"/api/v1/inventory/stock/{sku}",
                           json={"current_stock": 0, "sale_price": price},
                           headers=auth_headers)
            assert r.status_code == 200, r.text

        session_id = _session_with_forecasts(tid, {
            cheap:     _flat_forecast(100.0),   # 3.000 units x 0.50 =  1.500
            expensive: _flat_forecast(1.0),     #     30 units x 200  =  6.000
        })

        data = _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}&horizon_days=30",
            headers=auth_headers))

        assert data["basis"] == "money", "ranking must be money-weighted when prices exist"
        ranked = [i["sku"] for i in data["items"]]
        assert ranked[0] == expensive, (
            f"expected the expensive SKU first (spend-ordered), got {ranked}")
        assert ranked.index(expensive) < ranked.index(cheap)

        by_sku = {i["sku"]: i for i in data["items"]}
        # Money figures, not unit counts: 30 x 200 = 6000 and 3000 x 0.5 = 1500.
        assert by_sku[expensive]["projected_spend"] == pytest.approx(6000.0, rel=0.02)
        assert by_sku[cheap]["projected_spend"] == pytest.approx(1500.0, rel=0.02)
        # …and the cheap one moves 100x more UNITS, which is what makes this
        # assertion prove the ordering key is money.
        assert by_sku[cheap]["projected_demand"] > by_sku[expensive]["projected_demand"] * 50

    def test_cumulative_share_is_money_weighted(self, client, auth_headers, test_tenant):
        """
        The progress bar's numbers. Three gaps worth 6.000 / 1.500 / 30: the
        first alone is ~79.7% of the projected spend and the first two are
        ~99.6% — percentages a row-count bar (33% / 66%) could never produce.
        """
        tid = test_tenant["id"]
        big, mid, tiny = _sku(), _sku(), _sku()
        for sku, price in ((big, 200.0), (mid, 0.50), (tiny, 1.0)):
            client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 0, "sale_price": price},
                       headers=auth_headers)

        session_id = _session_with_forecasts(tid, {
            big:  _flat_forecast(1.0),      # 6.000
            mid:  _flat_forecast(100.0),    # 1.500
            tiny: _flat_forecast(1.0),      #    30
        })

        data = _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}", headers=auth_headers))

        items = data["items"]
        assert [i["sku"] for i in items] == [big, mid, tiny]
        assert items[0]["cumulative_pct"] == pytest.approx(79.68, abs=0.5)
        assert items[1]["cumulative_pct"] == pytest.approx(99.60, abs=0.5)
        assert items[2]["cumulative_pct"] == pytest.approx(100.0, abs=0.5)
        # A row-count bar would report 33.3% after the first row.
        assert items[0]["cumulative_pct"] > 60, "cumulative share must be by money, not by row"
        assert data["gap_skus"] == 3
        assert data["total_skus"] == 3

        # "Configure these N and you cover X% of your purchase." The default
        # target is 80%, which the first SKU (79.68%) just misses — so it takes
        # two. Ask for 75% and one is enough. Both numbers are shares of MONEY:
        # by row count, 2 of 3 rows would be 66%, never 99.6%.
        assert data["recommended_count"] == 2
        assert data["recommended_pct"] == pytest.approx(99.60, abs=0.5)

        lower = _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}&target_pct=75",
            headers=auth_headers))
        assert lower["recommended_count"] == 1
        assert lower["recommended_pct"] == pytest.approx(79.68, abs=0.5)

    def test_configured_skus_are_excluded_and_count_as_covered(
            self, client, auth_headers, test_tenant):
        """A SKU with cost configured is not a gap, and its money already
        counts toward the covered share — the bar starts where the user is."""
        tid = test_tenant["id"]
        done, pending = _sku(), _sku()
        client.put(f"/api/v1/inventory/stock/{done}",
                   json={"current_stock": 10, "unit_cost": 100.0, "sale_price": 150.0,
                         "supplier": "Acme"},
                   headers=auth_headers)
        client.put(f"/api/v1/inventory/stock/{pending}",
                   json={"current_stock": 0, "sale_price": 100.0},
                   headers=auth_headers)

        session_id = _session_with_forecasts(tid, {
            done:    _flat_forecast(1.0),   # 30 units x 100 = 3.000 covered
            pending: _flat_forecast(1.0),   # 30 units x 100 = 3.000 pending
        })

        data = _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}", headers=auth_headers))

        assert [i["sku"] for i in data["items"]] == [pending]
        assert data["gap_skus"] == 1
        assert data["configured_skus"] == 1
        assert data["covered_pct"] == pytest.approx(50.0, abs=1.0)
        assert "cost" in data["items"][0]["missing"]

    def test_no_money_anywhere_degrades_to_units_and_says_so(
            self, client, auth_headers, test_tenant):
        """
        With no cost and no price on file we cannot weigh by money. The
        endpoint must rank by volume AND report basis='units' — drawing a
        money bar over unit counts would be the dishonest option.
        """
        tid = test_tenant["id"]
        small, large = _sku(), _sku()
        session_id = _session_with_forecasts(tid, {
            small: _flat_forecast(1.0),
            large: _flat_forecast(50.0),
        })

        data = _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}", headers=auth_headers))

        assert data["basis"] == "units"
        assert [i["sku"] for i in data["items"]] == [large, small]
        assert all(i["price_source"] == "none" for i in data["items"])
        # No stock row at all -> everything is missing, including the stock.
        assert "stock" in data["items"][0]["missing"]

    def test_horizon_scales_the_projected_spend(self, client, auth_headers, test_tenant):
        """60 days of the same daily demand is twice the money of 30."""
        tid = test_tenant["id"]
        sku = _sku()
        client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"current_stock": 0, "sale_price": 10.0}, headers=auth_headers)
        session_id = _session_with_forecasts(tid, {sku: _flat_forecast(2.0, days=60)})

        d30 = _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}&horizon_days=30",
            headers=auth_headers))
        d60 = _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}&horizon_days=60",
            headers=auth_headers))

        assert d30["items"][0]["projected_spend"] == pytest.approx(600.0, rel=0.02)
        assert d60["items"][0]["projected_spend"] == pytest.approx(1200.0, rel=0.02)


class TestSetupGapsAccess:

    def test_viewer_can_read(self, client, viewer_headers, auth_headers, test_tenant):
        """Read-only endpoint: a viewer sees the gaps (they are not mutating)."""
        tid = test_tenant["id"]
        sku = _sku()
        session_id = _session_with_forecasts(tid, {sku: _flat_forecast(5.0)})
        resp = client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}", headers=viewer_headers)
        data = _ok(resp)
        assert [i["sku"] for i in data["items"]] == [sku]

    def test_requires_auth(self, client, test_tenant):
        resp = client.get(f"/api/v1/inventory/setup-gaps?session_id={uuid4().hex}")
        assert resp.status_code in (401, 403)

    def test_no_session_returns_structured_error(self, client, auth_headers, monkeypatch):
        import backend.api.v1.inventory as inv_api
        monkeypatch.setattr(inv_api.planning_service, "resolve_active_session", lambda t: None)
        resp = client.get("/api/v1/inventory/setup-gaps", headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json().get("error_code") == "no_completed_session"

    def test_is_read_only(self, client, auth_headers, test_tenant):
        """Calling it must never create inventory rows for the missing SKUs."""
        tid = test_tenant["id"]
        sku = _sku()
        session_id = _session_with_forecasts(tid, {sku: _flat_forecast(5.0)})
        _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={session_id}", headers=auth_headers))
        assert query_one(
            "SELECT 1 FROM inventory_stock WHERE tenant_id = %s AND sku = %s", (tid, sku),
        ) is None, "the gaps endpoint must not materialize rows for unconfigured SKUs"

    def test_other_tenant_session_is_empty(self, client, auth_headers, test_tenant):
        """Forecasts are read tenant-scoped: another tenant's session is blank."""
        from backend.tenants.service import create_tenant

        other = create_tenant(name=f"other-{uuid4().hex[:6]}")
        other_session = _session_with_forecasts(other["id"], {_sku(): _flat_forecast(9.0)})
        data = _ok(client.get(
            f"/api/v1/inventory/setup-gaps?session_id={other_session}", headers=auth_headers))
        assert data["items"] == []
        assert data["total_skus"] == 0


class TestProjectedDemandHelper:
    """Unit-level guards on the arithmetic the ranking depends on."""

    def test_averages_across_models_never_sums_them(self):
        from backend.inventory.setup_gaps_service import projected_demand

        pts = [{"date": "2026-01-01", "value": 10.0}] * 3
        two_models = {"a": {"forecast": pts}, "b": {"forecast": pts}}
        # 30 units, not 60: two models are two opinions of the same demand.
        assert projected_demand(two_models, 3) == 30.0

    def test_truncates_to_the_horizon(self):
        from backend.inventory.setup_gaps_service import projected_demand

        pts = [{"date": f"2026-01-{i + 1:02d}", "value": 5.0} for i in range(30)]
        assert projected_demand({"m": {"forecast": pts}}, 7) == 35.0

    def test_empty_forecast_is_zero(self):
        from backend.inventory.setup_gaps_service import projected_demand

        assert projected_demand({}, 30) == 0.0
        assert projected_demand({"m": {"forecast": []}}, 30) == 0.0
