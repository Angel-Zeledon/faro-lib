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

    def test_representative_attributes_come_from_default_warehouse(self, client, auth_headers, test_tenant):
        """Regression (shared name_precedence_key): the representative row for
        per-SKU catalog attributes must be the default warehouse when present,
        even next to a Capitalized name that sorts before 'principal' in a
        case-sensitive ASCII sort ('Tienda Norte' < 'principal')."""
        from backend.inventory import service as inv_svc
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
        session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [{"value": 1.0}] * 14}}})

        item = next(i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == sku)
        assert item["current_stock"] == 60.0  # still summed across warehouses
        # Attributes from 'principal', NOT from 'Tienda Norte'.
        assert item["lead_time_configured"] == 7
        assert item["supplier"] == "main-supplier"

    def test_representative_fallback_is_casefolded_alphabetical(self, client, auth_headers, test_tenant):
        """Without a 'principal' row, the fallback representative must be the
        casefolded-alphabetically-first warehouse: 'almacen' beats 'Tienda
        Norte' ('T' < 'a' bytewise would pick the wrong one)."""
        from backend.inventory import service as inv_svc
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
        session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [{"value": 1.0}] * 14}}})

        item = next(i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == sku)
        assert item["lead_time_configured"] == 7
        assert item["supplier"] == "warehouse-supplier"
