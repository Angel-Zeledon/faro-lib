"""One tenant, one cost of holding stock.

`/inventory/dead-stock` priced dead stock at a hardcoded 25% a year while
`/inventory/price-breaks/evaluate` and the MILP optimizer priced the SAME
warehouse at the tenant's `business_cfg.holding_cost_pct` (0.20 by default).
A buyer read "this costs you X a month to keep" on /inventario and then got
purchase advice built on a different cost of money on /compras — two answers to
one question, and nothing on either screen said which.

These tests pin the rate to ONE source. The dead-stock endpoint also returns it,
because the footer narrating it must name the number actually used.
"""

import pytest

from backend.db import session_store
from backend.db.connection import execute
from backend.inventory import price_break_service as pb_svc
from backend.inventory import service as inv_svc


def _make_dead_stock_sku(tenant_id: str, session_id: str) -> str:
    """A SKU the endpoint will classify as dead, with real money on it.

    The classifier wants stock on hand, a forecast demand above zero, and at
    least two snapshots showing the stock barely moved. Two snapshots at the
    SAME level give a depletion of 0, which is under any 20% threshold.
    """
    items = inv_svc.get_inventory_status(tenant_id, session_id)
    assert items, "session fixture should expose forecast SKUs"
    sku = items[0]["sku"]

    # Stock and cost first: without a stock row the SKU is SIN_DATOS and its
    # `daily_demand` reads None, so the classifier's `expected` would be 0 and
    # nothing would ever be dead.
    inv_svc.upsert_stock(tenant_id, sku, {"current_stock": 500, "unit_cost": 20})
    priced = {i["sku"]: i for i in inv_svc.get_inventory_status(tenant_id, session_id)}[sku]
    assert (priced.get("daily_demand") or 0) > 0, "forecast demand is what makes it 'dead'"

    for _ in range(2):
        execute(
            "INSERT INTO inventory_snapshots (tenant_id, sku, current_stock)"
            " VALUES (%s, %s, %s)",
            (tenant_id, sku, 500),
        )
    return sku


def _dead_stock(client, headers, session_id: str) -> dict:
    r = client.get(
        "/api/v1/inventory/dead-stock",
        params={"session_id": session_id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


class TestTheRateComesFromTheTenant:
    def test_default_matches_the_price_break_default_not_25_percent(
        self, client, auth_headers, test_tenant, completed_session
    ):
        """With nothing configured, dead stock must agree with the other
        surfaces' fallback — the literal 0.25 is what this is guarding against."""
        data = _dead_stock(client, auth_headers, completed_session["id"])
        assert data["holding_cost_pct"] == pytest.approx(pb_svc.DEFAULT_HOLDING_COST_PCT)
        assert data["holding_cost_pct"] != pytest.approx(0.25), (
            "dead stock must not carry a holding rate of its own"
        )

    def test_a_configured_rate_reaches_the_endpoint(
        self, client, auth_headers, test_tenant, completed_session
    ):
        sid = completed_session["id"]
        cfg = session_store.get_field(test_tenant["id"], sid, "business_cfg") or {}
        session_store.set_field(
            test_tenant["id"], sid, "business_cfg", {**cfg, "holding_cost_pct": 0.4},
        )
        data = _dead_stock(client, auth_headers, sid)
        assert data["holding_cost_pct"] == pytest.approx(0.4)

    def test_the_money_moves_with_the_rate(
        self, client, auth_headers, test_tenant, completed_session
    ):
        """The rate is not decoration: doubling it doubles the monthly cost.

        Asserting only the echoed percentage would pass while the arithmetic
        still used the literal, so this builds a SKU that actually qualifies as
        dead stock and prices it twice. It must never skip — a test that opts
        out when the fixture is thin is a test that cannot fail.
        """
        tid, sid = test_tenant["id"], completed_session["id"]
        sku = _make_dead_stock_sku(tid, sid)
        cfg = session_store.get_field(tid, sid, "business_cfg") or {}

        session_store.set_field(tid, sid, "business_cfg", {**cfg, "holding_cost_pct": 0.2})
        low = _dead_stock(client, auth_headers, sid)
        session_store.set_field(tid, sid, "business_cfg", {**cfg, "holding_cost_pct": 0.4})
        high = _dead_stock(client, auth_headers, sid)

        assert sku in {i["sku"] for i in low["items"]}, (
            "the SKU built for this test must be classified as dead stock"
        )
        assert low["total_holding_cost_monthly"] > 0, "nothing priced, nothing proven"

        assert high["total_holding_cost_monthly"] == pytest.approx(
            low["total_holding_cost_monthly"] * 2, rel=0.02,
        ), "the monthly holding cost must scale with the configured rate"
        assert high["total_capital_trapped"] == pytest.approx(
            low["total_capital_trapped"],
        ), "the capital trapped is stock x cost — the rate must not touch it"
