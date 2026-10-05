"""Manual per-warehouse demand split (feature 5.4, spec §1b)."""

import pytest

from backend.db.connection import execute, query_one
from backend.inventory import warehouse_service as wh_svc


def _insert_unflagged(tenant_id: str, name: str) -> None:
    """A warehouse row as it exists for tenants created before `is_default` was
    anchored at creation: no flag anywhere. Deliberately bypasses
    create_warehouse, which would set it."""
    execute(
        "INSERT INTO warehouses (tenant_id, name, is_default) VALUES (%s, %s, false)",
        (tenant_id, name),
    )


@pytest.fixture()
def three_warehouses(client, test_tenant):
    tid = test_tenant["id"]
    wh_svc.create_warehouse(tid, "principal", is_default=True)
    wh_svc.create_warehouse(tid, "Norte")
    wh_svc.create_warehouse(tid, "Sur")
    return tid


class TestShares:
    def test_all_null_defaults_to_default_warehouse(self, three_warehouses):
        shares = wh_svc.get_demand_shares(three_warehouses)
        assert shares == {"principal": 1.0}

    def test_normalizes_set_shares(self, three_warehouses):
        tid = three_warehouses
        wh_svc.set_demand_share(tid, "Norte", 30)
        wh_svc.set_demand_share(tid, "Sur", 10)
        shares = wh_svc.get_demand_shares(tid)
        assert shares == {"Norte": 0.75, "Sur": 0.25}
        # Persisted raw value, not the normalized one
        row = query_one(
            "SELECT demand_share FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, "Norte"))
        assert row["demand_share"] == 30

    def test_share_out_of_range_rejected(self, three_warehouses):
        with pytest.raises(ValueError):
            wh_svc.set_demand_share(three_warehouses, "Norte", 101)
        with pytest.raises(ValueError):
            wh_svc.set_demand_share(three_warehouses, "Norte", -1)

    def test_unknown_warehouse_rejected(self, three_warehouses):
        with pytest.raises(ValueError):
            wh_svc.set_demand_share(three_warehouses, "Ghost", 50)

    def test_clearing_share_returns_to_default(self, three_warehouses):
        tid = three_warehouses
        wh_svc.set_demand_share(tid, "Norte", 40)
        wh_svc.set_demand_share(tid, "Norte", None)
        assert wh_svc.get_demand_shares(tid) == {"principal": 1.0}

    def test_the_first_warehouse_created_owns_the_demand(self, client, test_tenant):
        """Creation order beats the alphabet.

        Both create paths now anchor `is_default` on the tenant's first
        warehouse, so a company whose only location is 'Zona Sur' gets its
        demand — it must not move to whatever name happens to sort first. The
        old answer changed when a warehouse was RENAMED, silently relocating
        100% of the forecast to a different building.
        """
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "Zona Sur")
        wh_svc.create_warehouse(tid, "bodega central")
        assert wh_svc.get_demand_shares(tid) == {"Zona Sur": 1.0}

    def test_legacy_rows_with_no_flag_prefer_principal_over_ascii_sort(
        self, client, test_tenant
    ):
        """Walkthrough finding (2026-07-22), now the LEGACY path only.

        Tenants created before the flag was anchored have is_default = false on
        every row, and 'which one is the default' still falls through to
        name_precedence_key. A capitalized name ('Tienda Norte' < 'principal' in
        ASCII) must not steal the fallback from 'principal' — the UI promises
        the default warehouse gets 100% until shares are configured.

        Inserted directly: going through create_warehouse would set the flag and
        this branch would never run, which is exactly how this test stopped
        testing what it is named after.
        """
        tid = test_tenant["id"]
        _insert_unflagged(tid, "principal")
        _insert_unflagged(tid, "Tienda Norte")
        assert not any(w["is_default"] for w in wh_svc.list_warehouses(tid))
        assert wh_svc.get_demand_shares(tid) == {"principal": 1.0}

    def test_legacy_rows_without_principal_use_casefold_sort(self, client, test_tenant):
        tid = test_tenant["id"]
        _insert_unflagged(tid, "Zona Sur")
        _insert_unflagged(tid, "bodega central")
        # Casefolded: 'bodega central' < 'Zona Sur' regardless of capitalization
        assert wh_svc.get_demand_shares(tid) == {"bodega central": 1.0}


class TestOneAnswerToWhichWarehouseIsTheDefault:
    """
    Two resolvers, two answers. `get_demand_shares` checks the anchored
    `is_default` flag first; `service._aggregate_stock_rows_by_sku` — which
    picks the row that REPRESENTS a SKU on the aggregated screen — only ever
    consulted `name_precedence_key`, which puts DEFAULT_WAREHOUSE ("principal")
    first whatever the flag says.

    So a tenant whose first warehouse was "Zona Sur" had 100% of a SKU's demand
    attributed there, while the row on screen took that SKU's cost, lead time,
    MOQ and supplier — and with them the headline warehouse value — from
    "principal". One row, describing two different buildings, and nothing on
    screen to tell which.
    """

    def test_the_aggregated_row_represents_the_same_warehouse_that_owns_the_demand(
        self, client, test_tenant,
    ):
        from backend.inventory.service import _aggregate_stock_rows_by_sku

        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "Zona Sur")     # first -> is_default
        wh_svc.create_warehouse(tid, "principal")    # sorts first by NAME

        owner = next(iter(wh_svc.get_demand_shares(tid)))
        assert owner == "Zona Sur", "the premise: the flag beats the alphabet"

        rows = [
            {"sku": "A", "warehouse": "Zona Sur",  "current_stock": 10,
             "unit_cost": 100.0, "lead_time_days": 45},
            {"sku": "A", "warehouse": "principal", "current_stock": 5,
             "unit_cost": 1.0,  "lead_time_days": 2},
        ]
        agg = _aggregate_stock_rows_by_sku(
            rows, wh_svc.get_default_warehouse_name(tid))

        assert agg["A"]["warehouse"] == owner
        assert agg["A"]["unit_cost"] == 100.0
        assert agg["A"]["lead_time_days"] == 45
        # The quantity is still the tenant's real total across warehouses.
        assert agg["A"]["current_stock"] == 15.0

    def test_without_the_flag_it_still_falls_back_to_the_name_order(
        self, client, test_tenant,
    ):
        """Legacy tenants carry no flag anywhere, so the name key is all there
        is — and passing None must not change that answer."""
        from backend.inventory.service import _aggregate_stock_rows_by_sku

        tid = test_tenant["id"]
        _insert_unflagged(tid, "Tienda Norte")
        _insert_unflagged(tid, "principal")

        rows = [
            {"sku": "A", "warehouse": "Tienda Norte", "current_stock": 1, "unit_cost": 9.0},
            {"sku": "A", "warehouse": "principal",    "current_stock": 1, "unit_cost": 3.0},
        ]
        assert _aggregate_stock_rows_by_sku(rows, None)["A"]["warehouse"] == "principal"
        assert (_aggregate_stock_rows_by_sku(
            rows, wh_svc.get_default_warehouse_name(tid))["A"]["warehouse"] == "principal")


class TestSharesApi:
    def test_viewer_denied_and_unchanged(self, client, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        r = client.patch("/api/v1/inventory/warehouses/principal",
                         json={"demand_share": 40}, headers=viewer_headers)
        assert r.status_code == 403
        row = query_one(
            "SELECT demand_share FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, "principal"))
        assert row["demand_share"] is None

    def test_analyst_sets_share(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        r = client.patch("/api/v1/inventory/warehouses/principal",
                         json={"demand_share": 40}, headers=analyst_headers)
        assert r.status_code == 200
        row = query_one(
            "SELECT demand_share FROM warehouses WHERE tenant_id=%s AND name=%s",
            (tid, "principal"))
        assert row["demand_share"] == 40
