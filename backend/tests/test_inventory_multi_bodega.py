"""get_inventory_status must sum stock across warehouses per SKU, not pick one."""
from uuid import uuid4


def _sku():
    return f"MB_{uuid4().hex[:8]}"


class TestInventoryStatusMultiWarehouse:
    def test_current_stock_is_summed_across_warehouses(self, client, auth_headers, test_tenant):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        session = create_session(tid, "usr_test", "multi-warehouse-test")
        sid = session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"current_stock": 100, "lead_time_days": 10, "warehouse": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"current_stock": 40, "lead_time_days": 10, "warehouse": "Sur"})

        session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [{"value": 1.0}] * 14}}})

        items = inv_svc.get_inventory_status(tid, sid)
        item = next(i for i in items if i["sku"] == sku)
        assert item["current_stock"] == 140.0  # 100 + 40, not just one warehouse's value

    def test_representative_attributes_come_from_the_anchored_default(self, client, auth_headers, test_tenant):
        """The representative row for per-SKU catalog attributes is the
        warehouse the tenant has ANCHORED as its default (`is_default`), which
        is also where get_demand_shares puts the demand. One question, one
        answer: this test used to assert the name-based order instead, and the
        two resolvers disagreeing is what made a single row describe two
        different buildings."""
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc
        from backend.db.connection import execute
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "default-warehouse-precedence")["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 50, "lead_time_days": 20,
            "supplier": "north-supplier", "warehouse": "Tienda Norte",
        })
        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 10, "lead_time_days": 7,
            "supplier": "main-supplier", "warehouse": "principal",
        })
        # 'Tienda Norte' was created first, so it holds the flag. Move it, the
        # way a tenant does from /bodegas, and the attributes must follow.
        execute("UPDATE warehouses SET is_default = (name = %s) WHERE tenant_id = %s",
                ("principal", tid))
        assert wh_svc.get_default_warehouse_name(tid) == "principal"

        session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [{"value": 1.0}] * 14}}})

        item = next(i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == sku)
        assert item["current_stock"] == 60.0  # still summed across warehouses
        # Attributes from the anchored 'principal', NOT from 'Tienda Norte'.
        assert item["lead_time_configured"] == 7
        assert item["supplier"] == "main-supplier"

    def test_representative_fallback_is_casefolded_alphabetical(self, client, auth_headers, test_tenant):
        """With NO warehouse anchored as default, the fallback representative is
        the casefolded-alphabetically-first one: 'almacen' beats 'Tienda Norte'
        ('T' < 'a' bytewise would pick the wrong one). The flag comes first when
        it exists — that is the test above."""
        from backend.inventory import service as inv_svc
        from backend.db.connection import execute
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "casefold-fallback")["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 50, "lead_time_days": 20,
            "supplier": "north-supplier", "warehouse": "Tienda Norte",
        })
        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 10, "lead_time_days": 7,
            "supplier": "warehouse-supplier", "warehouse": "almacen",
        })
        # No anchor at all: this is the branch the name key exists for.
        execute("UPDATE warehouses SET is_default = FALSE WHERE tenant_id = %s", (tid,))

        session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [{"value": 1.0}] * 14}}})

        item = next(i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == sku)
        assert item["lead_time_configured"] == 7
        assert item["supplier"] == "warehouse-supplier"
