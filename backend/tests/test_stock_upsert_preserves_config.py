"""
Counting stock must not rewrite the purchasing rules.

`PUT /inventory/stock/{sku}` is what the daily "update stock" screen posts, and
it posts three fields: {current_stock, lead_time_days, supplier}. `StockUpsert`
declares `min_stock`, `lead_time_days` and `moq` as NON-optional with defaults,
so Pydantic materialised 0 / 15 / 1 for anything the body left out and the
upsert wrote them over the tenant's configuration.

Measured before the fix, on a supplier whose minimum order is 100 units:

    before the stock update   moq=100   ->  Faro recommends 100 units
    after  the stock update   moq=1     ->  Faro recommends  81 units

81 is below a minimum the supplier will not ship, and nothing warned: the screen
said "saved". The stock count and the supplier's minimum have nothing to do with
each other.

The rule these tests pin: on an EXISTING row only the fields actually present in
the request body change. A row that does not exist yet still gets the documented
defaults, because a new row has to start somewhere.
"""
from uuid import uuid4

import pytest


def _sku():
    return f"UPS_{uuid4().hex[:8]}"


def _row(tenant_id: str, sku: str, warehouse: str = "principal"):
    """Read the row straight from the DB — the endpoint's echo is not evidence."""
    from backend.db.connection import query_one
    return query_one(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s AND sku = %s AND warehouse = %s",
        (tenant_id, sku, warehouse),
    )


def _seed(client, headers, sku: str, **over):
    body = {
        "current_stock": 40, "min_stock": 20, "lead_time_days": 7,
        "moq": 100, "unit_cost": 8.5, "supplier": "Distribuidora Andina",
    }
    body.update(over)
    r = client.put(f"/api/v1/inventory/stock/{sku}", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return body


class TestPartialUpsertKeepsWhatItWasNotAsked_ToChange:
    def test_counting_stock_leaves_moq_min_stock_and_lead_time_alone(
        self, client, auth_headers, test_tenant
    ):
        tid, sku = test_tenant["id"], _sku()
        _seed(client, auth_headers, sku)

        # Exactly what the "update stock" screen posts.
        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 35, "lead_time_days": 7,
                             "supplier": "Distribuidora Andina"},
                       headers=auth_headers)
        assert r.status_code == 200, r.text

        row = _row(tid, sku)
        assert float(row["current_stock"]) == 35.0, "the field that WAS sent must change"
        assert float(row["moq"]) == 100.0, "supplier minimum was silently reset"
        assert float(row["min_stock"]) == 20.0, "minimum stock was silently reset"
        assert int(row["lead_time_days"]) == 7

    def test_a_body_with_only_the_count_changes_only_the_count(
        self, client, auth_headers, test_tenant
    ):
        """The narrowest possible body — an integration posting a stock take."""
        tid, sku = test_tenant["id"], _sku()
        _seed(client, auth_headers, sku)

        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 12}, headers=auth_headers)
        assert r.status_code == 200, r.text

        row = _row(tid, sku)
        assert float(row["current_stock"]) == 12.0
        assert float(row["moq"]) == 100.0
        assert float(row["min_stock"]) == 20.0
        assert int(row["lead_time_days"]) == 7
        assert float(row["unit_cost"]) == 8.5
        assert row["supplier"] == "Distribuidora Andina"

    def test_a_field_that_IS_sent_still_changes(self, client, auth_headers, test_tenant):
        """Break to check: this is what stops the fix from becoming 'ignore
        everything'. Without it, a service that never writes moq would pass the
        two tests above."""
        tid, sku = test_tenant["id"], _sku()
        _seed(client, auth_headers, sku)

        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 40, "moq": 25, "min_stock": 5},
                       headers=auth_headers)
        assert r.status_code == 200, r.text

        row = _row(tid, sku)
        assert float(row["moq"]) == 25.0
        assert float(row["min_stock"]) == 5.0

    def test_a_brand_new_row_still_gets_the_documented_defaults(
        self, client, auth_headers, test_tenant
    ):
        """Preserving is only meaningful when there is something to preserve."""
        from backend.inventory.defaults import DEFAULT_LEAD_TIME_DAYS, DEFAULT_MOQ

        tid, sku = test_tenant["id"], _sku()
        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 10}, headers=auth_headers)
        assert r.status_code == 200, r.text

        row = _row(tid, sku)
        assert int(row["lead_time_days"]) == DEFAULT_LEAD_TIME_DAYS
        assert float(row["moq"]) == DEFAULT_MOQ
        assert float(row["min_stock"]) == 0.0

    def test_each_warehouse_keeps_its_own_configuration(
        self, client, auth_headers, test_tenant
    ):
        """Multi-store makes this worse, not different: one row per warehouse
        means the same daily update erodes the configuration once per store."""
        tid, sku = test_tenant["id"], _sku()
        _seed(client, auth_headers, sku, warehouse="principal")
        _seed(client, auth_headers, sku, moq=50, lead_time_days=3,
              warehouse="Sucursal Cartago")

        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 7, "warehouse": "Sucursal Cartago"},
                       headers=auth_headers)
        assert r.status_code == 200, r.text

        branch = _row(tid, sku, "Sucursal Cartago")
        main = _row(tid, sku, "principal")
        assert float(branch["current_stock"]) == 7.0
        assert float(branch["moq"]) == 50.0, "the branch's own minimum was reset"
        assert int(branch["lead_time_days"]) == 3
        assert float(main["moq"]) == 100.0, "the other warehouse must not be touched"
        assert float(main["current_stock"]) == 40.0


class TestTheRecommendationTheBuyerActsOn:
    def test_the_supplier_minimum_survives_a_stock_count(
        self, client, auth_headers, test_tenant
    ):
        """The half the user actually feels: a purchase order below a minimum
        the supplier will not ship."""
        from backend.db import session_store
        from backend.inventory import service as inv_svc
        from backend.sessions.service import create_session

        tid, sku = test_tenant["id"], _sku()
        sid = create_session(tid, "usr_test", "moq-after-count")["id"]
        _seed(client, auth_headers, sku, current_stock=40, moq=100, lead_time_days=7)
        session_store.set_forecasts(
            tid, sid, {sku: {"lightgbm": {"forecast": [{"value": 10.0}] * 14}}})

        before = next(i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == sku)
        assert before["recommended_qty"] >= 100, before["recommended_qty"]

        client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"current_stock": 35, "lead_time_days": 7},
                   headers=auth_headers)

        after = next(i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == sku)
        assert after["recommended_qty"] >= 100, (
            "counting stock dropped the recommendation below the supplier's "
            f"minimum: {after['recommended_qty']}")


class TestPermissions:
    def test_viewer_cannot_upsert_and_the_row_is_untouched(
        self, client, auth_headers, viewer_headers, test_tenant
    ):
        tid, sku = test_tenant["id"], _sku()
        _seed(client, auth_headers, sku)

        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 999}, headers=viewer_headers)
        assert r.status_code == 403, r.text
        assert float(_row(tid, sku)["current_stock"]) == 40.0

    def test_analyst_can_upsert(self, client, analyst_headers, test_tenant):
        tid, sku = test_tenant["id"], _sku()
        r = client.put(f"/api/v1/inventory/stock/{sku}",
                       json={"current_stock": 11, "moq": 4}, headers=analyst_headers)
        assert r.status_code == 200, r.text
        row = _row(tid, sku)
        assert float(row["current_stock"]) == 11.0
        assert float(row["moq"]) == 4.0


@pytest.mark.parametrize("field,value", [("moq", 3.0), ("min_stock", 9.0), ("lead_time_days", 21)])
def test_every_non_optional_field_is_individually_preservable(
    client, auth_headers, test_tenant, field, value
):
    """One case per field that carries a model default — those are exactly the
    ones `exclude_none` could not protect."""
    tid, sku = test_tenant["id"], _sku()
    _seed(client, auth_headers, sku, **{field: value})

    r = client.put(f"/api/v1/inventory/stock/{sku}",
                   json={"current_stock": 1}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert float(_row(tid, sku)[field]) == float(value)
