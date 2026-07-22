"""Manual per-warehouse demand split (feature 5.4, spec §1b)."""

import pytest

from backend.db.connection import query_one
from backend.inventory import warehouse_service as wh_svc


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
