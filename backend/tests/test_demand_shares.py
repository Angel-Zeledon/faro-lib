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
