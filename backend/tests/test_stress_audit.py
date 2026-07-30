"""
STRESS AUDIT — adversarial tests for the inventory backend.

Philosophy: don't test that it works; test that it fails correctly when it
should, and that nothing is written when it fails.

Rewritten against tests/README.md. Three anti-patterns were removed everywhere
they occurred in this file:

  * `assert resp.status_code != 500` — passes for 200, 404, 422 and every other
    status, so it says only "the process is still alive". Every one of those is
    now pinned to the single status the endpoint actually returns, plus the row
    that must or must not exist.
  * `assert x in (204, 404)` / `assert qty == 0 or qty % moq == 0` — an
    either/or assert distinguishes nothing. Replaced with the one true value.
  * `assert isinstance(result, float)` on a pure calculation — a hand-computed
    expected number is available for every one of these, so the type check was
    hiding a real assertion.

Reads of `inventory_stock` / `suppliers` / `bom_items` / `inventory_events` are
scoped by tenant_id: the suite runs against a shared database, and an unscoped
COUNT(*) would be flaky and, worse, could pass on another tenant's row.
"""

from __future__ import annotations

import csv
import io
import threading
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one


# ── Fixture overrides (same as test_inventory.py) ─────────────────────────────

@pytest.fixture
def registered_user(test_tenant):
    from backend.users import service as user_svc
    email = f"audit-{uuid4().hex[:8]}@faro-e2e.io"
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


@pytest.fixture
def tid(test_tenant):
    """The tenant every DB assertion in this file is scoped to."""
    return test_tenant["id"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sku():
    return f"AUD-{uuid4().hex[:8].upper()}"


def _stock_row(tenant_id: str, sku: str):
    return query_one(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )


def _stock_count(tenant_id: str) -> int:
    return query_one(
        "SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id = %s", (tenant_id,),
    )["n"]


def _make_session(client, headers, name="audit-session") -> str:
    resp = client.post("/api/v1/sessions", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _seed_forecast(tenant_id: str, session_id: str, skus, daily=5.0, days=30):
    """A session's forecast is what makes a SKU visible to /status, /briefing and
    the BOM explosion — none of them read inventory_stock on its own."""
    from backend.db import session_store
    from datetime import date, timedelta
    start = date(2026, 1, 1)
    session_store.set_forecasts(tenant_id, session_id, {
        sku: {"lightgbm": {"historical": [], "forecast": [
            {"date": str(start + timedelta(days=i)), "value": daily, "upper": daily + 1.0}
            for i in range(days)
        ]}}
        for sku in skus
    })


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 1 — Extreme inputs and edge cases in inventory
# ═══════════════════════════════════════════════════════════════════════════════

class TestInventoryEdgeCases:
    """Adversarial inputs for the stock CRUD + calculation layer."""

    # ── moq = 0 ────────────────────────────────────────────────────────────────

    def test_moq_zero_skips_rounding_instead_of_dividing_by_zero(self):
        """
        `if moq and moq > 0` makes moq=0 falsy, so no `raw / moq` happens. The
        old test asserted `isinstance(result, float) and result >= 0`, which a
        function returning 0.0 for everything would also pass.

        Hand-derived from the documented formula, z(0.95) = 1.645:
            lead_time_demand = 5 * 14                       = 70
            safety_stock     = 1.645 * 1 * sqrt(14)         =  6.15502…
            raw              = 70 + 6.15502 - 10            = 66.15502…
        With moq=0 that is returned rounded to 2dp; with moq=1 it is rounded UP
        to the next whole unit. Asserting both is what proves moq=0 skipped the
        rounding rather than merely failing to crash.
        """
        from backend.inventory.service import _calc_recommended
        unrounded = _calc_recommended(
            current_stock=10.0, avg_daily=5.0, avg_std=1.0,
            lead_time=14, moq=0, service_level=0.95,
        )
        assert unrounded == 66.16, f"moq=0 changed the quantity: {unrounded}"

        rounded = _calc_recommended(
            current_stock=10.0, avg_daily=5.0, avg_std=1.0,
            lead_time=14, moq=1, service_level=0.95,
        )
        assert rounded == 67.0, f"moq=1 did not round up: {rounded}"

    def test_moq_zero_via_api_rejected_with_422(self, client, auth_headers, tid):
        """
        StockUpsert declares `moq: float = Field(default=1, ge=1)`, so moq=0 is
        always rejected at the API boundary — the service-level falsy guard above
        only matters for callers that bypass the API (e.g. dataset sync).
        """
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100.0, "moq": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert _stock_row(tid, sku) is None, "a rejected PUT created a stock row"

    # ── boundary values rejected at the API, with nothing written ─────────────

    @pytest.mark.parametrize("body,label", [
        ({"current_stock": 100.0, "lead_time_days": 0}, "lead_time_days below ge=1"),
        ({"current_stock": 10.0, "lead_time_days": 366}, "lead_time_days above le=365"),
        ({"current_stock": -1.0}, "current_stock below ge=0"),
        ({"current_stock": 1e15}, "current_stock above le=1e9"),
        ({"current_stock": None}, "current_stock null on a non-Optional field"),
        ({"current_stock": 10.0, "moq": 1e9}, "moq above le=1e6"),
        ({"current_stock": 10.0, "unit_cost": -5.0}, "unit_cost below ge=0"),
    ])
    def test_out_of_range_stock_is_rejected_and_never_stored(
        self, body, label, client, auth_headers, tid,
    ):
        """One parametrized table replaces five separate `assert 422` tests. The
        assertion that matters is the second one: a validator that answers 422
        after the write would pass every one of the originals."""
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}", json=body, headers=auth_headers,
        )
        assert resp.status_code == 422, f"{label} was accepted: {resp.text}"
        assert _stock_row(tid, sku) is None, f"{label} was rejected and stored anyway"

    def test_stock_at_the_upper_bound_is_stored_exactly(self, client, auth_headers, tid):
        """Guards the table above: the bound must be inclusive, or "rejected"
        would just mean "rejects everything"."""
        sku = _sku()
        large = 1_000_000_000  # 1e9 — the sane maximum
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": large, "lead_time_days": 15},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        row = _stock_row(tid, sku)
        assert row["current_stock"] == large, (
            f"1e9 was accepted but stored as {row['current_stock']} (overflow)"
        )
        assert row["lead_time_days"] == 15

    def test_stock_zero_is_stored_as_zero_not_dropped(self, client, auth_headers, tid):
        """current_stock=0 is valid and falsy — the classic value to lose to an
        `if value:` filter on the way to the database."""
        sku = _sku()
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 0, "lead_time_days": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        row = _stock_row(tid, sku)
        assert row is not None, "200 returned and no row was written"
        assert row["current_stock"] == 0.0, f"0 was stored as {row['current_stock']!r}"

    # ── unit_cost = 0 → inventory_value ──────────────────────────────────

    def test_cost_zero_inventory_value_is_zero_not_none(self, client, auth_headers, registered_user):
        """
        In get_inventory_status the check was `if stock.get("unit_cost")`, which
        is falsy at unit_cost=0 and made inventory_value None instead of 0.0.

        The calculation only runs when has_forecast and has_stock are both true,
        and /status scopes its SKU set strictly to the session's forecasts (see
        test_status_nonexistent_session_returns_no_phantom_skus), so a forecast
        must be seeded for this SKU to reach that branch at all.
        """
        tenant_id = registered_user["tenant"]["id"]
        sku = _sku()
        session_id = _make_session(client, auth_headers, "cost-zero-test")

        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100.0, "unit_cost": 0.0, "lead_time_days": 10},
            headers=auth_headers,
        )
        _seed_forecast(tenant_id, session_id, [sku])

        resp = client.get(
            f"/api/v1/inventory/status?session_id={session_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        matching = [i for i in resp.json()["data"]["items"] if i["sku"] == sku]
        assert matching, "SKU not found in status response"
        assert matching[0]["inventory_value"] == 0.0, (
            "inventory_value must be 0.0 when unit_cost=0, not None "
            f"(falsy-check bug): got {matching[0]['inventory_value']}"
        )

    # ── service_level ─────────────────────────────────────────────────────────

    @pytest.mark.parametrize("service_level", [0, 1.0, 0.5, 0.42])
    def test_unknown_service_level_falls_back_to_z_of_0_95(self, service_level):
        """
        A service level absent from `_Z` falls back to z=1.645, i.e. it must
        produce exactly the 0.95 answer — and that answer must differ from a
        service level that IS in the table, otherwise "falls back" would be
        indistinguishable from "ignores the parameter".

        Hand-derived (stock=50, avg=10, std=2, lead=14):
            z=1.645 → 140 + 1.645*2*sqrt(14) - 50 = 102.31005… → ceil → 103
            z=2.326 → 140 + 2.326*2*sqrt(14) - 50 = 107.40739… → ceil → 108
        """
        from backend.inventory.service import _calc_recommended
        args = dict(current_stock=50.0, avg_daily=10.0, avg_std=2.0, lead_time=14, moq=1)
        assert _calc_recommended(**args, service_level=service_level) == 103.0
        assert _calc_recommended(**args, service_level=0.95) == 103.0
        assert _calc_recommended(**args, service_level=0.99) == 108.0

    def test_service_level_boundary_via_api(self, client, auth_headers):
        """/status accepts service_level in [0.5, 0.999]; outside that, 422."""
        for bad_sl in (0.0, 1.0, 1.5, 0.4999):
            resp = client.get(
                f"/api/v1/inventory/status?session_id={uuid4().hex}&service_level={bad_sl}",
                headers=auth_headers,
            )
            assert resp.status_code == 422, (
                f"service_level={bad_sl} should be rejected, got {resp.status_code}"
            )
        # The inclusive ends must still work, or the check above is vacuous.
        for ok_sl in (0.5, 0.999):
            assert client.get(
                f"/api/v1/inventory/status?session_id={uuid4().hex}&service_level={ok_sl}",
                headers=auth_headers,
            ).status_code == 200

    # ── special characters ────────────────────────────────────────────────────

    def test_display_name_xss_chars_are_stored_verbatim(self, client, auth_headers, tid):
        """A JSON API is not a browser: escaping here would corrupt the product
        name. What matters is that the string reaches the DB unaltered — checked
        against the row, not against the response echo of what we just sent."""
        sku = _sku()
        xss_name = "Aceite <script>alert(1)</script> & \"quoted\" 'single'"
        resp = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 10.0, "display_name": xss_name},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert _stock_row(tid, sku)["display_name"] == xss_name, (
            "display_name was mangled between the request and the row"
        )

    def test_sku_containing_a_slash_is_a_404_and_writes_nothing(self, client, auth_headers, tid):
        """
        `PUT /stock/{sku}` takes the SKU as one path segment, so "SKU/001"
        matches no route and Starlette answers its own 404 — not the endpoint's.
        The old assertion was `!= 500`, which also passed for a 200 that silently
        created a row named "001". Pin both halves.
        """
        before = _stock_count(tid)
        resp = client.put(
            "/api/v1/inventory/stock/SKU/001",
            json={"current_stock": 5.0},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}, (
            "a slashed SKU reached an endpoint instead of falling off the router"
        )
        assert _stock_count(tid) == before, "an unrouted PUT still wrote a row"
        assert query_one(
            "SELECT sku FROM inventory_stock WHERE tenant_id = %s AND sku IN ('SKU', '001', 'SKU/001')",
            (tid,),
        ) is None

    # ── MOQ rounding ──────────────────────────────────────────────────────────

    def test_moq_rounds_up_and_never_invents_an_order(self):
        """
        `qty == 0.0 or qty % 10000 == 0` was true for every possible return
        value of a function that rounds to multiples of 10000, so it asserted
        nothing. Both branches are now separate, exact cases.
        """
        from backend.inventory.service import _calc_recommended
        # Stock (100) already covers lead-time demand (14 + 0.6155 safety) —
        # a huge MOQ must not turn "no order" into "order 10 000".
        assert _calc_recommended(
            current_stock=100.0, avg_daily=1.0, avg_std=0.1,
            lead_time=14, moq=10000, service_level=0.95,
        ) == 0.0
        # With no stock, 14 units of demand round up to exactly one MOQ lot.
        assert _calc_recommended(
            current_stock=0.0, avg_daily=1.0, avg_std=0.0,
            lead_time=14, moq=10000, service_level=0.95,
        ) == 10000.0


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Calculations with empty forecast or nonexistent session
# ═══════════════════════════════════════════════════════════════════════════════

class TestInventoryCalculationEdgeCases:

    def test_status_nonexistent_session_returns_no_phantom_skus(self, client, auth_headers, tid):
        """
        REGRESSION TEST for the Quick Start "phantom entity" bug: inventory_stock
        is a tenant-wide table with no session_id column, so a SKU with stock
        entered under one session must NOT leak into the view of a different
        (or nonexistent) session that never forecast it.

        The DB read is not decoration: without it, an empty `items` list would
        also pass if the stock write had silently failed, which is the opposite
        bug and would make this test meaningless.
        """
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 50.0, "lead_time_days": 10},
            headers=auth_headers,
        )
        assert _stock_row(tid, sku) is not None, "the fixture stock row was never written"

        resp = client.get(
            f"/api/v1/inventory/status?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == [], (
            "a session with no forecast surfaced unrelated stock SKUs: "
            f"{data['items']}"
        )
        assert data["summary"]["total_skus"] == 0

    def test_status_shows_exactly_the_skus_the_session_forecast(
        self, client, auth_headers, registered_user,
    ):
        """The other half of the phantom-SKU rule, and the one that catches an
        over-eager fix: a session that DID forecast a SKU must show it, and only
        it, while the tenant holds stock for two more."""
        tenant_id = registered_user["tenant"]["id"]
        forecast_sku, other_a, other_b = _sku(), _sku(), _sku()
        for sku in (forecast_sku, other_a, other_b):
            assert client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 100.0, "lead_time_days": 10},
                headers=auth_headers,
            ).status_code == 200

        session_id = _make_session(client, auth_headers, "one-sku-session")
        _seed_forecast(tenant_id, session_id, [forecast_sku])

        resp = client.get(
            f"/api/v1/inventory/status?session_id={session_id}", headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [i["sku"] for i in data["items"]] == [forecast_sku]
        summary = data["summary"]
        assert summary["total_skus"] == 1
        counted = (
            summary["sin_datos"] + summary["ok"] + summary["order_now"]
            + summary["order_soon"] + summary["overstock"]
        )
        assert counted == 1, f"signal counts do not add up to total_skus: {summary}"

    def test_recommendation_zero_demand(self):
        """avg_daily=0 → no order, whatever the stock level."""
        from backend.inventory.service import _calc_recommended, _avg_daily_forecast
        avg, std = _avg_daily_forecast({}, lead_time=14)
        assert (avg, std) == (0.0, 0.0)
        assert _calc_recommended(100.0, avg, std, lead_time=14, moq=1) == 0.0

    def test_briefing_and_dashboard_are_all_zeros_for_a_session_with_no_forecast(
        self, client, auth_headers,
    ):
        """
        Replaces three tests that asserted `"kpis" in data`,
        `isinstance(kpis["total_skus"], int)` and `kpis["total_skus"] >= 0` —
        all three of which pass for any integer, including a wrong one.

        Every counter is pinned to 0 and `has_data` to False, so a briefing that
        starts counting the tenant's whole catalogue under an unrelated session
        (the phantom-SKU bug in its other guise) fails here.
        """
        session_id = uuid4().hex
        briefing = client.get(
            f"/api/v1/inventory/morning-briefing?session_id={session_id}",
            headers=auth_headers,
        )
        assert briefing.status_code == 200, briefing.text
        data = briefing.json()["data"]
        assert data["has_data"] is False
        assert data["recommendations"] == []
        assert data["risks"] == []
        assert data["overstocked"] == []
        kpis = data["kpis"]
        for key in ("total_skus", "order_now", "order_soon", "ok", "overstock",
                    "sin_datos", "total_inventory_value", "capital_in_overstock",
                    "demand_alerts", "demand_spikes"):
            assert kpis[key] == 0, f"kpis[{key}] == {kpis[key]!r}, expected 0"
        assert kpis["avg_accuracy"] is None, (
            "an untrained session reported an accuracy figure"
        )

        dash = client.get(
            f"/api/v1/inventory/dashboard-summary?session_id={session_id}",
            headers=auth_headers,
        )
        assert dash.status_code == 200
        summary = dash.json()["data"]
        for key in ("total_skus", "order_now", "order_soon", "ok", "overstock",
                    "sin_datos", "total_inventory_value"):
            assert summary[key] == 0, f"dashboard-summary[{key}] == {summary[key]!r}"
        assert summary["top_critical"] == []

    def test_briefing_counts_a_real_session(self, client, auth_headers, registered_user):
        """Guards the all-zeros test above: zeros must mean "nothing to report",
        not "this endpoint always returns zeros"."""
        tenant_id = registered_user["tenant"]["id"]
        skus = [_sku() for _ in range(2)]
        for sku in skus:
            client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 20.0, "unit_cost": 10.0, "lead_time_days": 7},
                headers=auth_headers,
            )
        session_id = _make_session(client, auth_headers, "briefing-real")
        _seed_forecast(tenant_id, session_id, skus, daily=5.0)

        resp = client.get(
            f"/api/v1/inventory/morning-briefing?session_id={session_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["has_data"] is True
        assert data["kpis"]["total_skus"] == 2
        # 20 units at 10 each, twice.
        assert data["kpis"]["total_inventory_value"] == 400

    def test_morning_briefing_is_json_safe(self, client, auth_headers, registered_user):
        """NaN and Infinity are legal in Python's json but not in JSON, so they
        reach the browser as a parse error rather than a 500 here."""
        import json
        import math

        tenant_id = registered_user["tenant"]["id"]
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 500.0, "unit_cost": 100.0, "lead_time_days": 7},
            headers=auth_headers,
        )
        session_id = _make_session(client, auth_headers, "json-safe")
        _seed_forecast(tenant_id, session_id, [sku])

        resp = client.get(
            f"/api/v1/inventory/morning-briefing?session_id={session_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # The raw body, not the parsed one: json.loads accepts the NaN literal,
        # so parsing first would hide exactly what we are looking for.
        for token in ("NaN", "Infinity", "-Infinity"):
            assert token not in resp.text, f"the response body contains a bare {token}"
        json.loads(resp.text, parse_constant=lambda c: (_ for _ in ()).throw(
            ValueError(f"non-JSON constant {c} in body")))

        def _finite(obj):
            if isinstance(obj, float):
                assert math.isfinite(obj), f"non-finite float in response: {obj}"
            elif isinstance(obj, dict):
                for v in obj.values():
                    _finite(v)
            elif isinstance(obj, list):
                for v in obj:
                    _finite(v)

        _finite(resp.json()["data"])


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 3 — BOM and material explosion
# ═══════════════════════════════════════════════════════════════════════════════

class TestBOMEdgeCases:

    def test_bom_self_reference_rejected(self, client, auth_headers, tid):
        """A SKU cannot be its own component; the service raises ValueError and
        the router turns that into a 422 carrying a stable code."""
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
        assert resp.status_code == 422, resp.text
        assert query_one(
            "SELECT id FROM bom_items WHERE tenant_id = %s AND parent_sku = %s",
            (tid, sku),
        ) is None, "the self-reference was refused with 422 and stored anyway"

    @pytest.mark.parametrize("quantity,label", [
        (0, "quantity=0 against gt=0"),
        (-1, "negative quantity"),
    ])
    def test_bom_bad_quantity_rejected_and_not_stored(
        self, quantity, label, client, auth_headers, tid,
    ):
        parent, child = _sku(), _sku()
        resp = client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": quantity},
            headers=auth_headers,
        )
        assert resp.status_code == 422, f"{label} was accepted"
        assert query_one(
            "SELECT id FROM bom_items WHERE tenant_id = %s AND parent_sku = %s AND child_sku = %s",
            (tid, parent, child),
        ) is None, f"{label} was rejected and stored anyway"

    def test_explode_no_bom_returns_empty(self, client, auth_headers):
        """A session with no BOM must produce an empty explosion, not a 500."""
        resp = client.get(
            f"/api/v1/inventory/production-requirements?session_id={uuid4().hex}",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["finished_goods_count"] == 0
        assert data["has_shortages"] is False
        assert data["finished_goods"] == []
        assert data["raw_material_summary"] == []

    def test_bom_upsert_is_idempotent_and_updates_the_quantity(self, client, auth_headers, tid):
        """
        `data2.get("quantity") == 3.0 or data2 == {}` accepted the endpoint doing
        nothing at all. What must hold is one row, holding the newest quantity.
        """
        parent, child = _sku(), _sku()
        for sku in (parent, child):
            client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 100.0},
                headers=auth_headers,
            )

        assert client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 2.0, "unit": "kg"},
            headers=auth_headers,
        ).status_code == 200
        assert client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 3.0, "unit": "kg"},
            headers=auth_headers,
        ).status_code == 200

        rows = query(
            "SELECT quantity, unit FROM bom_items "
            "WHERE tenant_id = %s AND parent_sku = %s AND child_sku = %s",
            (tid, parent, child),
        )
        assert len(rows) == 1, f"the second upsert duplicated the row: {rows}"
        assert rows[0]["quantity"] == 3.0, "the second upsert did not update the quantity"
        assert rows[0]["unit"] == "kg"

    def test_bom_delete_of_a_missing_row_is_an_idempotent_204(self, client, auth_headers, tid):
        """`in (204, 404)` passed either way. The service issues a plain
        tenant-scoped DELETE, so the answer is 204 and the table is untouched."""
        parent, child = _sku(), _sku()
        before = query_one(
            "SELECT COUNT(*) AS n FROM bom_items WHERE tenant_id = %s", (tid,),
        )["n"]
        resp = client.delete(
            f"/api/v1/inventory/bom/{parent}/{child}", headers=auth_headers,
        )
        assert resp.status_code == 204, resp.text
        assert query_one(
            "SELECT COUNT(*) AS n FROM bom_items WHERE tenant_id = %s", (tid,),
        )["n"] == before

    def test_bom_indirect_cycle_terminates_and_reports_both_parents(
        self, client, auth_headers, registered_user,
    ):
        """
        upsert_bom_item only blocks a DIRECT self-reference, so A→B→A is
        accepted. explode_requirements walks one level per finished good, so it
        must terminate — but the old version of this test passed a random
        session_id, which makes `get_inventory_status` return [] and the
        explosion exit on its first line, never touching the cycle at all. It
        would have kept passing if the explosion recursed forever.

        This seeds a real session with forecasts for both SKUs so the walk
        actually runs over the cycle, and asserts the shape of what comes back.
        """
        tenant_id = registered_user["tenant"]["id"]
        sku_a, sku_b = _sku(), _sku()

        for sku in (sku_a, sku_b):
            assert client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 10.0, "lead_time_days": 7},
                headers=auth_headers,
            ).status_code == 200
            assert client.patch(
                f"/api/v1/inventory/stock/{sku}/product-type?product_type=finished_good",
                headers=auth_headers,
            ).status_code == 200

        assert client.put(
            f"/api/v1/inventory/bom/{sku_a}/{sku_b}", json={"quantity": 1.0},
            headers=auth_headers,
        ).status_code == 200
        assert client.put(
            f"/api/v1/inventory/bom/{sku_b}/{sku_a}", json={"quantity": 1.0},
            headers=auth_headers,
        ).status_code == 200

        session_id = _make_session(client, auth_headers, "bom-cycle")
        _seed_forecast(tenant_id, session_id, [sku_a, sku_b], daily=5.0)

        completed = threading.Event()
        holder: dict = {}

        def run_explode():
            try:
                r = client.get(
                    f"/api/v1/inventory/production-requirements?session_id={session_id}",
                    headers=auth_headers,
                )
                holder["status"] = r.status_code
                holder["body"] = r.json()
            except Exception as exc:  # noqa: BLE001 — reported by the assert below
                holder["error"] = repr(exc)
            completed.set()

        t = threading.Thread(target=run_explode, daemon=True)
        t.start()
        assert completed.wait(timeout=20), (
            "explode_requirements did not finish — the A→B→A cycle recursed"
        )
        assert "error" not in holder, holder.get("error")
        assert holder["status"] == 200, holder
        data = holder["body"]["data"]
        # Both SKUs are finished goods with a BOM, so both are exploded once —
        # exactly once, which is the property a cycle would break.
        assert data["finished_goods_count"] == 2, data
        parents = sorted(fg["sku"] for fg in data["finished_goods"])
        assert parents == sorted([sku_a, sku_b])
        for fg in data["finished_goods"]:
            assert len(fg["requirements"]) == 1, (
                f"{fg['sku']} was exploded {len(fg['requirements'])} times — the "
                "cycle was followed instead of stopping at level 1"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 4 — Bulk import with problematic data
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkImportEdgeCases:

    def test_bulk_no_sku_column_rejected(self, client, auth_headers, tid):
        """Without a product-code column there is nothing to key on. The error
        has to name the missing field, because that message is the only thing
        that tells the user how to fix their file."""
        before = _stock_count(tid)
        csv_bytes = b"current_stock,lead_time_days\n100,15\n200,10\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("bad.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "inventory_import_no_valid_rows"
        assert resp.json()["error_params"]["missing_required"] == ["sku"]
        assert _stock_count(tid) == before, "a rejected import still wrote rows"

    def test_bulk_only_headers_no_rows_rejected(self, client, auth_headers, tid):
        before = _stock_count(tid)
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("empty.csv", b"sku,current_stock,lead_time_days\n", "text/csv")},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "inventory_import_no_valid_rows"
        assert _stock_count(tid) == before

    def test_bulk_empty_sku_rows_are_skipped_and_counted(self, client, auth_headers, tid):
        """Rows with no SKU are skipped, and the count of skipped rows is
        reported — silently dropping three of five rows is the failure mode."""
        good_sku = _sku()
        csv_content = (
            "sku,current_stock\n"
            ",100\n"                    # empty sku
            "   ,50\n"                  # whitespace-only sku
            f"{good_sku},75\n"          # valid row
        )
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("mixed.csv", csv_content.encode("utf-8"), "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["imported"] == 1
        assert data["skipped_no_sku"] == 2, f"skipped rows not reported: {data}"
        assert _stock_row(tid, good_sku)["current_stock"] == 75.0
        assert _stock_count(tid) == 1, "an empty SKU produced a row of its own"

    def test_bulk_row_with_a_non_numeric_cell_is_reported_not_coerced(
        self, client, auth_headers, tid,
    ):
        """
        `assert resp.status_code != 500` was the whole original test. The real
        behaviour is specific and worth pinning: a garbage numeric cell rejects
        that ROW with a per-row code (it is not coerced to 0 and imported), and
        with no other row in the file the whole import 422s.
        """
        sku = _sku()
        csv_bytes = f"sku,current_stock,lead_time_days\n{sku},not_a_number,15\n".encode()
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("invalid_num.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 422, resp.text
        params = resp.json()["error_params"]
        assert params["rejected"] == 1
        row_error = params["errors"][0]
        assert row_error["row"] == 2, "the reported line number is not the spreadsheet's"
        assert row_error["sku"] == sku
        assert row_error["code"] == "inventory_import_row_not_a_number"
        assert row_error["params"] == {"column": "current_stock", "value": "not_a_number"}
        assert _stock_row(tid, sku) is None, (
            "a row with a garbage number was coerced and imported anyway"
        )

    def test_bulk_latin1_encoding_preserves_the_accents(self, client, auth_headers, tid):
        """
        Latin-1 is what Excel in Latin America produces. `imported >= 1` proved
        the file was accepted; it did not prove the text survived, which is the
        entire point — a wrong fallback yields "Aceite NiÃ±o" and still imports.
        """
        sku = _sku()
        csv_content = f"sku,display_name,stock_actual\n{sku},Aceite Niño,50\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("latin1.csv", csv_content.encode("latin-1"), "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["imported"] == 1
        row = _stock_row(tid, sku)
        assert row["display_name"] == "Aceite Niño", (
            f"latin-1 text was mojibaked on import: {row['display_name']!r}"
        )
        # 'stock_actual' is a Spanish alias, so the mapping had to resolve too.
        assert row["current_stock"] == 50.0

    def test_bulk_utf8_bom_encoding(self, client, auth_headers, tid):
        """A UTF-8 BOM from Windows Excel glues itself to the first header, so
        'sku' becomes '\\ufeffsku' and the whole file loses its key column."""
        sku = _sku()
        csv_bytes = b"\xef\xbb\xbf" + f"sku,current_stock\n{sku},99\n".encode()
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("bom.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["imported"] == 1
        assert _stock_row(tid, sku)["current_stock"] == 99.0

    def test_bulk_large_import_writes_every_row(self, client, auth_headers, tid):
        rows = [
            {"sku": f"BULK-{i:04d}-{uuid4().hex[:4]}", "current_stock": i * 10,
             "lead_time_days": 15}
            for i in range(1000)
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["sku", "current_stock", "lead_time_days"])
        writer.writeheader()
        writer.writerows(rows)

        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("large.csv", buf.getvalue().encode(), "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["imported"] == 1000
        # The reported count is the endpoint's own arithmetic; the row count is not.
        assert _stock_count(tid) == 1000
        spot = _stock_row(tid, rows[500]["sku"])
        assert spot["current_stock"] == 5000.0 and spot["lead_time_days"] == 15

    def test_bulk_negative_lead_time_via_csv_row_rejected_not_stored(
        self, client, auth_headers, tid,
    ):
        """
        Each CSV row is validated through StockPatch (ge=1 on lead_time_days)
        before being queued for bulk_upsert — a ValidationError skips the row, it
        does not bypass the constraint. With one invalid row, `rows` is empty and
        the import 422s.
        """
        sku = _sku()
        csv_bytes = f"sku,current_stock,lead_time_days\n{sku},100,-5\n".encode()
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("neg_lead.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 422
        assert resp.json()["error_params"]["errors"][0]["code"] == "inventory_import_row_out_of_range"
        assert _stock_row(tid, sku) is None, "a row that failed validation was persisted"

    def test_bulk_mixed_rows_imports_only_the_valid_one(self, client, auth_headers, tid):
        good_sku, bad_sku = _sku(), _sku()
        csv_bytes = (
            "sku,current_stock,lead_time_days\n"
            f"{good_sku},100,10\n"
            f"{bad_sku},100,-5\n"
        ).encode()
        resp = client.post(
            "/api/v1/inventory/bulk",
            headers=auth_headers,
            files={"file": ("mixed.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["imported"] == 1
        assert data["error_count"] == 1
        assert data["errors"][0]["sku"] == bad_sku

        good = _stock_row(tid, good_sku)
        assert good is not None and good["lead_time_days"] == 10
        assert _stock_row(tid, bad_sku) is None
        assert _stock_count(tid) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 5 — Suppliers — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestSupplierEdgeCases:

    def test_supplier_name_of_500_chars_is_accepted_and_kept_whole(
        self, client, auth_headers, tid,
    ):
        """`in (201, 422)` accepted both outcomes, so it could not notice the
        name being silently truncated. `suppliers.name` is TEXT and the schema
        sets no max_length, so the answer is 201 and all 500 characters survive."""
        long_name = "X" * 500
        resp = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": long_name},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        row = query_one(
            "SELECT name FROM suppliers WHERE id = %s", (resp.json()["data"]["id"],),
        )
        assert len(row["name"]) == 500, f"the name was truncated to {len(row['name'])}"
        assert row["name"] == long_name
        assert row["name"] is not None

    def test_supplier_name_unicode(self, client, auth_headers, tid):
        unicode_name = f"Distribuidora ñoño & Cía. — {uuid4().hex[:4]}"
        resp = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": unicode_name},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert query_one(
            "SELECT name FROM suppliers WHERE id = %s", (resp.json()["data"]["id"],),
        )["name"] == unicode_name

    def test_supplier_std_greater_than_lead_time_is_allowed_and_stored(
        self, client, auth_headers,
    ):
        """
        lead_time_std > lead_time_days is mathematically odd but the schema
        allows it (`lead_time_std: int = Field(ge=0, le=60)`), and a real
        supplier with a 5-day nominal lead time and wild variance is a thing the
        buyer needs to be able to record. Pinned to 201 with both values read
        back, instead of the old `in (201, 422)`.
        """
        resp = client.post(
            "/api/v1/inventory/suppliers",
            json={
                "name": f"StdGtLt-{uuid4().hex[:4]}",
                "lead_time_days": 5,
                "lead_time_std": 50,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        row = query_one(
            "SELECT lead_time_days, lead_time_std FROM suppliers WHERE id = %s",
            (resp.json()["data"]["id"],),
        )
        assert (row["lead_time_days"], row["lead_time_std"]) == (5, 50)

    def test_assigning_a_supplier_to_a_sku_with_no_stock_row_is_allowed(
        self, client, auth_headers, tid,
    ):
        """
        `assign_sku_supplier` checks the supplier exists but not the SKU, so a
        link can precede the stock row. That is deliberate — suppliers are
        commonly imported before inventory — and the old `!= 500` assertion could
        not tell that apart from a crash or a silent 404.

        What must hold: 200, one tenant-scoped link row, and the supplier check
        still refusing an unknown supplier id.
        """
        supplier = client.post(
            "/api/v1/inventory/suppliers",
            json={"name": f"Sup-{uuid4().hex[:4]}", "lead_time_days": 10},
            headers=auth_headers,
        )
        assert supplier.status_code == 201
        sup_id = supplier.json()["data"]["id"]

        ghost_sku = f"GHOST-{uuid4().hex[:8]}"
        resp = client.put(
            f"/api/v1/inventory/stock/{ghost_sku}/suppliers/{sup_id}",
            json={"is_primary": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        link = query_one(
            "SELECT tenant_id, is_primary FROM sku_suppliers WHERE sku = %s AND supplier_id = %s",
            (ghost_sku, sup_id),
        )
        assert link is not None, "200 returned and no link row was written"
        assert link["tenant_id"] == tid
        assert link["is_primary"] is True
        assert _stock_row(tid, ghost_sku) is None, (
            "assigning a supplier invented a stock row for the SKU"
        )

        # The supplier half of the check is still enforced.
        unknown = client.put(
            f"/api/v1/inventory/stock/{ghost_sku}/suppliers/{uuid4()}",
            json={"is_primary": True},
            headers=auth_headers,
        )
        assert unknown.status_code == 404
        assert unknown.json()["error_code"] == "supplier_not_found"

    def test_assign_supplier_idempotent_under_concurrency(self, client, auth_headers, tid):
        """
        Two threads assigning the same supplier to the same SKU must not produce
        two rows. sku_suppliers is keyed by (tenant_id, sku, supplier_id) with
        ON CONFLICT DO UPDATE; the original version of this test only checked
        status codes sequentially, which cannot catch a race.
        """
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
        assert query_one(
            "SELECT COUNT(*) AS n FROM sku_suppliers "
            "WHERE tenant_id = %s AND sku = %s AND supplier_id = %s",
            (tid, sku, sup_id),
        )["n"] == 1, "the concurrent assignment produced a duplicate link"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 6 — Inventory events
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventEdgeCases:

    @pytest.mark.parametrize("body,label", [
        ({"name": "Backwards", "start_date": "2026-12-31", "end_date": "2026-01-01",
          "multiplier": 1.5}, "end_date before start_date"),
        ({"name": "Zero", "start_date": "2026-06-01", "end_date": "2026-06-30",
          "multiplier": 0}, "multiplier below ge=0.1"),
        ({"name": "TooHigh", "start_date": "2026-08-01", "end_date": "2026-08-31",
          "multiplier": 10.001}, "multiplier above le=10.0"),
    ])
    def test_invalid_event_is_rejected_and_never_stored(
        self, body, label, client, auth_headers, tid,
    ):
        resp = client.post("/api/v1/inventory/events", json=body, headers=auth_headers)
        assert resp.status_code == 422, f"{label} was accepted: {resp.text}"
        assert query_one(
            "SELECT COUNT(*) AS n FROM inventory_events WHERE tenant_id = %s", (tid,),
        )["n"] == 0, f"{label} was rejected with 422 and stored anyway"

    @pytest.mark.parametrize("start,end,multiplier,label", [
        ("2026-12-15", "2026-12-15", 1.5, "single-day event (start == end)"),
        ("2026-07-01", "2026-07-31", 10.0, "multiplier at the le=10.0 ceiling"),
    ])
    def test_valid_boundary_event_is_stored_with_its_values(
        self, start, end, multiplier, label, client, auth_headers,
    ):
        """Guards the rejection table above: the inclusive boundaries must still
        work, and the values must land in the row rather than being defaulted."""
        resp = client.post(
            "/api/v1/inventory/events",
            json={"name": f"Bound-{uuid4().hex[:4]}", "start_date": start,
                  "end_date": end, "multiplier": multiplier},
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"{label} was rejected: {resp.text}"
        row = query_one(
            "SELECT start_date, end_date, multiplier FROM inventory_events WHERE id = %s",
            (resp.json()["data"]["id"],),
        )
        assert str(row["start_date"]) == start
        assert str(row["end_date"]) == end
        assert row["multiplier"] == multiplier

    def test_event_patch_start_after_existing_end_rejected(self, client, auth_headers):
        """
        Patching only start_date to a value after the event's existing end_date
        must be rejected — the patch route merges with the stored row before
        validating, so the ordering rule cannot be bypassed one field at a time.
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
            json={"start_date": "2026-06-15"},  # after the existing end_date
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "event_end_before_start"
        row = query_one(
            "SELECT start_date, end_date FROM inventory_events WHERE id = %s", (event_id,),
        )
        assert str(row["start_date"]) == "2026-05-01", "the rejected patch partially applied"
        assert str(row["end_date"]) == "2026-05-31"

    def test_event_patch_wrong_tenant(self, client, auth_headers):
        """Patching another tenant's event must 404 and leave it untouched."""
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc

        t2 = create_tenant(f"tenant-b-{uuid4().hex[:6]}")
        email2 = f"b-{uuid4().hex[:6]}@faro-e2e.io"
        u2 = user_svc.create_user(t2["id"], email2, "TestPass123!", "admin", "B")
        user_svc.mark_verified(t2["id"], u2["id"])

        try:
            lr = client.post("/api/v1/auth/login", json={"email": email2, "password": "TestPass123!"})
            h2 = {"Authorization": f"Bearer {lr.json()['data']['access_token']}"}

            cr = client.post(
                "/api/v1/inventory/events",
                json={"name": "Tenant B Event", "start_date": "2026-09-01",
                      "end_date": "2026-09-30", "multiplier": 1.2},
                headers=h2,
            )
            assert cr.status_code == 201
            event_id = cr.json()["data"]["id"]

            resp = client.patch(
                f"/api/v1/inventory/events/{event_id}",
                json={"multiplier": 9.9},
                headers=auth_headers,
            )
            assert resp.status_code == 404
            assert query_one(
                "SELECT multiplier, tenant_id FROM inventory_events WHERE id = %s", (event_id,),
            ) == {"multiplier": 1.2, "tenant_id": t2["id"]}, (
                "the cross-tenant patch returned 404 and changed the row anyway"
            )
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))

    def test_event_delete_wrong_tenant_is_a_noop_204(self, client, auth_headers):
        """
        delete_event issues `DELETE ... WHERE id=%s AND tenant_id=%s`, so a
        cross-tenant delete matches nothing and still answers 204. `in (204, 404)`
        hid which of the two it was; the row surviving is the real assertion.
        """
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc

        t2 = create_tenant(f"tenant-c-{uuid4().hex[:6]}")
        email2 = f"c-{uuid4().hex[:6]}@faro-e2e.io"
        u2 = user_svc.create_user(t2["id"], email2, "TestPass123!", "admin", "C")
        user_svc.mark_verified(t2["id"], u2["id"])

        try:
            lr = client.post("/api/v1/auth/login", json={"email": email2, "password": "TestPass123!"})
            h2 = {"Authorization": f"Bearer {lr.json()['data']['access_token']}"}

            cr = client.post(
                "/api/v1/inventory/events",
                json={"name": "Protected Event", "start_date": "2026-10-01",
                      "end_date": "2026-10-31", "multiplier": 1.0},
                headers=h2,
            )
            assert cr.status_code == 201
            event_id = cr.json()["data"]["id"]

            assert client.delete(
                f"/api/v1/inventory/events/{event_id}", headers=auth_headers,
            ).status_code == 204
            assert query_one(
                "SELECT id FROM inventory_events WHERE id = %s", (event_id,),
            ) is not None, "a cross-tenant delete removed another tenant's event"
            assert event_id in [
                e["id"] for e in
                client.get("/api/v1/inventory/events", headers=h2).json()["data"]
            ]
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 6b — Cross-tenant mutation (IDOR) checks for sessions, stock, suppliers, BOM
#
# The event tests above are the template: create a real second tenant, attempt
# the mutation as that tenant against tenant A's resource, assert it is rejected,
# then independently re-verify tenant A's resource is unchanged. These extend the
# pattern to the other mutable inventory/session resources, which the audit found
# had only GET/list tenant-isolation coverage, never PATCH/DELETE coverage.
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
        email2 = f"{prefix}-{uuid4().hex[:6]}@faro-e2e.io"
        user = user_svc.create_user(t2["id"], email2, "TestPass123!", "admin", prefix)
        user_svc.mark_verified(t2["id"], user["id"])
        return t2, email2

    def _login(self, client, email):
        lr = client.post("/api/v1/auth/login", json={"email": email, "password": "TestPass123!"})
        assert lr.status_code == 200
        return {"Authorization": f"Bearer {lr.json()['data']['access_token']}"}

    # ── sessions ───────────────────────────────────────────────────────────────

    def test_session_patch_wrong_tenant(self, client, auth_headers, test_session):
        t2, email2 = self._make_tenant_b("sess-b")
        try:
            resp = client.patch(
                f"/api/v1/sessions/{test_session['id']}",
                json={"name": "hijacked"},
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404
            assert query_one(
                "SELECT name FROM sessions WHERE id = %s", (test_session["id"],),
            )["name"] == test_session["name"]
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))

    def test_session_delete_wrong_tenant(self, client, auth_headers, test_session):
        t2, email2 = self._make_tenant_b("sess-c")
        try:
            resp = client.delete(
                f"/api/v1/sessions/{test_session['id']}",
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404
            assert query_one(
                "SELECT id FROM sessions WHERE id = %s", (test_session["id"],),
            ) is not None, "a cross-tenant delete removed another tenant's session"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))

    # ── inventory stock ────────────────────────────────────────────────────────

    def test_stock_patch_wrong_tenant_is_404_not_overwrite(self, client, auth_headers, tid):
        sku = f"PYTEST-STOCK-{uuid4().hex[:8]}"
        assert client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100, "display_name": "Tenant A stock"},
            headers=auth_headers,
        ).status_code == 200

        t2, email2 = self._make_tenant_b("stock-b")
        try:
            # Stock is keyed by (tenant_id, sku): tenant B has no row for this
            # sku, so PATCH (which requires an existing row) must 404 rather than
            # create one or mutate tenant A's.
            resp = client.patch(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": 9999},
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404
            row = _stock_row(tid, sku)
            assert row["current_stock"] == 100
            assert row["display_name"] == "Tenant A stock"
            assert query_one(
                "SELECT COUNT(*) AS n FROM inventory_stock WHERE sku = %s AND tenant_id = %s",
                (sku, t2["id"]),
            )["n"] == 0, "the 404 PATCH created a row under tenant B"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM inventory_stock WHERE sku=%s", (sku,))

    def test_stock_delete_wrong_tenant_is_404_not_delete(self, client, auth_headers, tid):
        sku = f"PYTEST-STOCK-{uuid4().hex[:8]}"
        assert client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 50},
            headers=auth_headers,
        ).status_code == 200

        t2, email2 = self._make_tenant_b("stock-c")
        try:
            resp = client.delete(
                f"/api/v1/inventory/stock/{sku}",
                headers=self._login(client, email2),
            )
            assert resp.status_code == 404
            assert _stock_row(tid, sku) is not None, (
                "a cross-tenant delete removed another tenant's stock row"
            )
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM inventory_stock WHERE sku=%s", (sku,))

    # ── suppliers ──────────────────────────────────────────────────────────────

    def test_supplier_patch_wrong_tenant(self, client, auth_headers):
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
            assert query_one(
                "SELECT name FROM suppliers WHERE id = %s", (supplier_id,),
            )["name"] == "Tenant A Supplier"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM suppliers WHERE id=%s", (supplier_id,))

    def test_supplier_delete_wrong_tenant(self, client, auth_headers):
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
            assert supplier_id in [
                s["id"] for s in
                client.get("/api/v1/inventory/suppliers", headers=auth_headers).json()["data"]
            ], "a cross-tenant delete deactivated another tenant's supplier"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM suppliers WHERE id=%s", (supplier_id,))

    # ── BOM ────────────────────────────────────────────────────────────────────

    def test_bom_put_wrong_tenant_does_not_affect_owner(self, client, auth_headers, tid):
        parent, child = f"PARENT-{uuid4().hex[:6]}", f"CHILD-{uuid4().hex[:6]}"
        for sku in (parent, child):
            assert client.put(
                f"/api/v1/inventory/stock/{sku}", json={"current_stock": 1}, headers=auth_headers,
            ).status_code == 200
        assert client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 2.0},
            headers=auth_headers,
        ).status_code == 200

        t2, email2 = self._make_tenant_b("bom-b")
        try:
            # BOM rows are keyed by (tenant_id, parent_sku, child_sku), so tenant
            # B writing the same pair must create/affect only its own row.
            assert client.put(
                f"/api/v1/inventory/bom/{parent}/{child}",
                json={"quantity": 999.0},
                headers=self._login(client, email2),
            ).status_code == 200

            rows = query(
                "SELECT tenant_id, quantity FROM bom_items "
                "WHERE parent_sku = %s AND child_sku = %s ORDER BY tenant_id",
                (parent, child),
            )
            by_tenant = {r["tenant_id"]: r["quantity"] for r in rows}
            assert by_tenant[tid] == 2.0, "tenant B's BOM write changed tenant A's row"
            assert by_tenant[t2["id"]] == 999.0
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM bom_items WHERE parent_sku=%s AND child_sku=%s", (parent, child))
            execute("DELETE FROM inventory_stock WHERE sku IN (%s, %s)", (parent, child))

    def test_bom_delete_wrong_tenant_is_noop(self, client, auth_headers, tid):
        parent, child = f"PARENT-{uuid4().hex[:6]}", f"CHILD-{uuid4().hex[:6]}"
        for sku in (parent, child):
            assert client.put(
                f"/api/v1/inventory/stock/{sku}", json={"current_stock": 1}, headers=auth_headers,
            ).status_code == 200
        assert client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 3.0},
            headers=auth_headers,
        ).status_code == 200

        t2, email2 = self._make_tenant_b("bom-c")
        try:
            # DELETE is scoped by tenant_id, so it is a no-op for tenant A's row
            # regardless of the status code — what matters is the row surviving.
            assert client.delete(
                f"/api/v1/inventory/bom/{parent}/{child}",
                headers=self._login(client, email2),
            ).status_code == 204
            assert query_one(
                "SELECT quantity FROM bom_items "
                "WHERE tenant_id = %s AND parent_sku = %s AND child_sku = %s",
                (tid, parent, child),
            )["quantity"] == 3.0, "a cross-tenant BOM delete removed the owner's row"
        finally:
            execute("DELETE FROM tenants WHERE id=%s", (t2["id"],))
            execute("DELETE FROM bom_items WHERE parent_sku=%s AND child_sku=%s", (parent, child))
            execute("DELETE FROM inventory_stock WHERE sku IN (%s, %s)", (parent, child))


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 7 — Concurrency and consistency
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrencyAndConsistency:

    def test_concurrent_upsert_same_sku(self, client, auth_headers, tid):
        """
        Five concurrent upserts for the same SKU must not produce duplicate rows
        and must leave the row holding one of the values actually written (not a
        crash, a NULL, or a corrupted blend). inventory_stock is keyed by
        (tenant_id, sku) with ON CONFLICT DO UPDATE — verified against the DB
        rather than by the absence of a 500.
        """
        sku = _sku()
        errors: list[int] = []
        written_values = [i * 10 for i in range(5)]

        def do_upsert(stock_val: int):
            r = client.put(
                f"/api/v1/inventory/stock/{sku}",
                json={"current_stock": float(stock_val), "lead_time_days": 10},
                headers=auth_headers,
            )
            if r.status_code != 200:
                errors.append(r.status_code)

        threads = [threading.Thread(target=do_upsert, args=(v,)) for v in written_values]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"concurrent upserts returned {errors}"
        rows = query(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
        assert len(rows) == 1, f"expected exactly 1 row for {sku}, found {len(rows)}"
        assert rows[0]["current_stock"] in written_values, (
            f"final current_stock {rows[0]['current_stock']} matches none of the values "
            f"actually written ({written_values}) — corrupted/blended write"
        )

    def test_delete_while_reading(self, client, auth_headers, tid):
        """
        Deleting a SKU while another thread reads /status must not 500, and the
        delete must win in the end — verified with a direct query, not by the
        absence of a 500 from either thread.
        """
        sku = _sku()
        client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 100.0},
            headers=auth_headers,
        )
        assert _stock_row(tid, sku) is not None

        errors: list[str] = []

        def delete_sku():
            r = client.delete(f"/api/v1/inventory/stock/{sku}", headers=auth_headers)
            if r.status_code != 204:
                errors.append(f"delete {r.status_code}: {r.text[:120]}")

        def read_status():
            for _ in range(5):
                r = client.get(
                    f"/api/v1/inventory/status?session_id={uuid4().hex}",
                    headers=auth_headers,
                )
                if r.status_code != 200:
                    errors.append(f"read {r.status_code}: {r.text[:120]}")

        t1 = threading.Thread(target=delete_sku)
        t2 = threading.Thread(target=read_status)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not errors, f"delete-while-reading produced {errors}"
        assert _stock_row(tid, sku) is None, (
            "the delete ran concurrently with reads and the row still exists"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP 8 — Pure calculation (unit-level, no DB)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPureCalculationEdgeCases:
    """
    Unit-level tests for the service's calculation helpers. Every expected value
    here is hand-derived from the documented formula, not recomputed by calling
    the same code a second time.
    """

    def test_avg_daily_forecast_with_none_values(self):
        """
        A forecast point whose `value` is None counts as 0 demand (`p.get("value")
        or 0.0`), and its sigma still comes from the spread. Over
        [(None, upper 10), (5.0, upper 6)]:
            avg = (0 + 5) / 2       = 2.5
            std = ((10-0) + (6-5))/2 = 5.5
        The old test asserted `isinstance(avg, float) and avg >= 0`, which a
        function returning 0.0 would satisfy — and 0.0 demand is precisely the
        bug that drops a SKU off the semáforo.
        """
        from backend.inventory.service import _avg_daily_forecast
        avg, std = _avg_daily_forecast({
            "model1": {"forecast": [
                {"date": "2026-01-01", "value": None, "upper": 10.0},
                {"date": "2026-01-02", "value": 5.0, "upper": 6.0},
            ]}
        }, lead_time=2)
        assert avg == 2.5, f"None was not treated as zero demand: avg={avg}"
        assert std == 5.5, f"sigma from the spread is wrong: std={std}"

    def test_avg_daily_forecast_negative_upper(self):
        """upper < value would make sigma negative; it is clamped to 0, and the
        demand itself is untouched."""
        from backend.inventory.service import _avg_daily_forecast
        avg, std = _avg_daily_forecast({
            "model1": {"forecast": [{"value": 100.0, "upper": 50.0}]}
        }, lead_time=1)
        assert avg == 100.0
        assert std == 0.0, f"negative sigma leaked through: {std}"

    def test_classify_xyz_boundary_values(self):
        """CV boundaries: X below 0.5, Y below 1.0, Z from 1.0 up."""
        from backend.inventory.service import _classify_xyz
        assert _classify_xyz(None) == "?"
        assert _classify_xyz(0.0) == "X"
        assert _classify_xyz(0.499) == "X"
        assert _classify_xyz(0.5) == "Y"
        assert _classify_xyz(0.999) == "Y"
        assert _classify_xyz(1.0) == "Z"
        assert _classify_xyz(100.0) == "Z"

    def test_classify_abc_all_zero_demand(self):
        """All-zero demand → every SKU is C, with no division by zero."""
        from backend.inventory.service import _classify_abc
        assert _classify_abc([
            {"sku": "A", "daily_demand": 0.0, "unit_cost": 100.0},
            {"sku": "B", "daily_demand": 0.0, "unit_cost": 200.0},
        ]) == {"A": "C", "B": "C"}

    @pytest.mark.parametrize("coverage,lead,expected", [
        (4.0, 10, "PEDIR_YA"),       # < 0.5 * lead
        (4.99, 10, "PEDIR_YA"),
        (5.0, 10, "PEDIR_PRONTO"),   # < 1.2 * lead
        (11.99, 10, "PEDIR_PRONTO"),
        (12.0, 10, "OK"),            # < 3 * lead
        (29.99, 10, "OK"),
        (30.0, 10, "SOBRESTOCK"),
        (0.0, 10, "PEDIR_YA"),
    ])
    def test_calc_signal_thresholds(self, coverage, lead, expected):
        """The semáforo's actual boundaries, pinned. Every one of these was
        previously covered only by `assert result in (the four signals)`, which
        is true for any return value the function can produce."""
        from backend.inventory.service import _calc_signal
        assert _calc_signal(coverage_days=coverage, lead_time=lead) == expected

    def test_calc_signal_with_zero_lead_time_does_not_crash(self):
        """
        lead_time=0 collapses every threshold to 0, so nothing is ever "below"
        one and the answer is SOBRESTOCK even at zero coverage. Unreachable
        through the API (`lead_time_days` is ge=1) and via
        DEFAULT_LEAD_TIME_DAYS, so this pins "does not raise, returns a real
        signal" rather than endorsing the reading.
        """
        from backend.inventory.service import _calc_signal
        assert _calc_signal(coverage_days=0.0, lead_time=0) == "SOBRESTOCK"

    def test_recommended_quantity_rises_with_the_service_level(self):
        """
        The old test looped over every entry of `_Z` asserting the answer was 0
        for zero demand and zero stock — true whatever the z-score, so the
        z table could have been all ones. Higher service level must mean strictly
        more safety stock, and the exact values are hand-derived
        (stock=0, avg=10, std=5, lead=9, sqrt(9)=3, moq=1):
            0.90 → 90 + 1.282*5*3 = 109.23  → ceil → 110
            0.95 → 90 + 1.645*5*3 = 114.675 → ceil → 115
            0.97 → 90 + 1.881*5*3 = 118.215 → ceil → 119
            0.99 → 90 + 2.326*5*3 = 124.89  → ceil → 125
        """
        from backend.inventory.service import _calc_recommended, _Z
        args = dict(current_stock=0.0, avg_daily=10.0, avg_std=5.0, lead_time=9, moq=1)
        got = {sl: _calc_recommended(**args, service_level=sl) for sl in sorted(_Z)}
        assert got == {0.90: 110.0, 0.95: 115.0, 0.97: 119.0, 0.99: 125.0}, got
        values = [got[sl] for sl in sorted(got)]
        assert values == sorted(values) and len(set(values)) == len(values), (
            f"recommended quantity is not strictly increasing in service level: {got}"
        )

    def test_generate_recommendations_empty_items(self):
        from backend.inventory.service import generate_recommendations
        assert generate_recommendations([]) == []

    def test_generate_recommendations_ignores_items_with_no_data(self):
        """
        A SIN_DATOS item has no coverage figure, so there is nothing honest to
        recommend and it must produce no recommendation at all. `isinstance(
        result, list)` was true for any output, including a sentence built from
        `coverage_days=None`.
        """
        from backend.inventory.service import generate_recommendations
        assert generate_recommendations([{
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
        }]) == []

    def test_generate_recommendations_speaks_up_for_a_real_stockout_risk(self):
        """Guards the test above: an empty list must mean "nothing to say", not
        "this function always returns nothing"."""
        from backend.inventory.service import generate_recommendations
        recs = generate_recommendations([{
            "sku": "SKU-002",
            "display_name": "Aceite 1L",
            "signal": "PEDIR_YA",
            "coverage_days": 2.0,
            "lead_time_days": 15,
            "recommended_qty": 120.0,
            "supplier": "Distribuidora Sur",
            "abc": "A",
            "demand_trend_pct": None,
            "inventory_value": 5000.0,
        }])
        assert len(recs) == 1, recs
        rec = recs[0]
        assert rec["rec_type"] == "STOCKOUT_RISK"
        assert rec["priority"] == 1
        assert rec["sku"] == "SKU-002"
        assert "120" in rec["action"], f"the quantity is missing from the action: {rec['action']}"
        assert "Distribuidora Sur" in rec["action"]
