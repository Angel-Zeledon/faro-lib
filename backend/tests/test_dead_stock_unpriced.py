"""GET /inventory/dead-stock must never price an unpriced SKU at 0.

`capital = round(current_stock * (unit_cost or 0), 2)` used to make a SKU
whose cost was never entered read as "0 capital trapped" and sink to the
bottom of a list sorted by that same number — reading as "no money at risk
here" for the exact opposite reason. stability.md #20 names this alongside
`dead_capital.py`'s `value`/`value_unknown_reason` pair, which this fix now
matches: unknown cost is `null`, counted (`capital_trapped_unknown_reason`,
`unpriced_sku_count`), and sorted after priced items rather than folded into
the total as 0.

This does not touch the pinned tests in `test_holding_rate_is_one_number.py`
or `test_entitlements.py` — both of those build their dead-stock SKUs with an
explicit `unit_cost`, so the branch this file exercises (`unit_cost is None`)
never runs in either of them.
"""

import pytest

from backend.db.connection import execute
from backend.inventory import service as inv_svc
from backend.inventory.dead_capital import REASON_NO_UNIT_COST


def _make_dead_stock_sku(tenant_id: str, session_id: str, index: int = 0, **stock_fields) -> str:
    """A SKU the /dead-stock classifier will flag: real forecast demand, and
    two snapshots at the same stock level (0% depletion, under any 20%
    threshold). Mirrors `test_holding_rate_is_one_number.py`'s helper.

    `index` picks WHICH forecast sku to use (the fixture seeds several) so a
    single test can build two independent dead-stock rows to compare."""
    items = inv_svc.get_inventory_status(tenant_id, session_id)
    assert len(items) > index, "session fixture should expose enough forecast SKUs"
    sku = items[index]["sku"]

    inv_svc.upsert_stock(tenant_id, sku, {"current_stock": 500, **stock_fields})
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


class TestUnpricedNeverReadsAsZero:
    def test_missing_unit_cost_is_null_with_a_reason(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        tid, sid = test_tenant["id"], completed_session["id"]
        sku = _make_dead_stock_sku(tid, sid, 0)  # no unit_cost passed

        data = _dead_stock(client, auth_headers, sid)
        item = {i["sku"]: i for i in data["items"]}[sku]

        assert item["capital_trapped"] is None
        assert item["capital_trapped_unknown_reason"] == REASON_NO_UNIT_COST
        assert item["holding_cost_monthly"] is None
        assert data["unpriced_sku_count"] >= 1

    def test_total_capital_trapped_is_exactly_the_priced_items_sum(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """One priced dead-stock row and one unpriced one: the total must
        equal the priced row's own capital exactly — proving the unpriced row
        is excluded by construction, not merely summed in as a 0 that happens
        to add nothing."""
        tid, sid = test_tenant["id"], completed_session["id"]
        priced_sku = _make_dead_stock_sku(tid, sid, 0, unit_cost=50.0)
        unpriced_sku = _make_dead_stock_sku(tid, sid, 1)  # unit_cost omitted

        data = _dead_stock(client, auth_headers, sid)
        by_sku = {i["sku"]: i for i in data["items"]}
        assert by_sku[priced_sku]["capital_trapped"] == pytest.approx(500 * 50.0)
        assert by_sku[unpriced_sku]["capital_trapped"] is None

        assert data["total_capital_trapped"] == pytest.approx(500 * 50.0)
        assert data["unpriced_sku_count"] >= 1

    def test_unpriced_item_is_never_dropped(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        tid, sid = test_tenant["id"], completed_session["id"]
        sku = _make_dead_stock_sku(tid, sid, 0)

        data = _dead_stock(client, auth_headers, sid)
        assert sku in {i["sku"] for i in data["items"]}, (
            "unpriced dead stock is still dead stock — it must appear in the "
            "list, just without a fabricated money figure"
        )

    def test_priced_items_still_sort_ahead_of_unpriced_ones(
        self, client, auth_headers, test_tenant, completed_session,
    ):
        """A tiny priced value must still outrank an unpriced item — proving
        the sort key treats 'unknown' as worse than 'small', never as 0."""
        tid, sid = test_tenant["id"], completed_session["id"]
        priced_sku = _make_dead_stock_sku(tid, sid, 0, unit_cost=0.01)
        unpriced_sku = _make_dead_stock_sku(tid, sid, 1)

        data = _dead_stock(client, auth_headers, sid)
        skus = [i["sku"] for i in data["items"]]
        assert unpriced_sku in skus, "the comparison needs both rows present"
        assert skus.index(priced_sku) < skus.index(unpriced_sku), (
            "an unpriced item ranked ahead of a priced (if tiny) one"
        )
