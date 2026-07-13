"""get_inventory_status must sum stock across bodegas per SKU, not pick one."""
from uuid import uuid4


def _sku():
    return f"MB_{uuid4().hex[:8]}"


class TestInventoryStatusMultiBodega:
    def test_stock_actual_is_summed_across_bodegas(self, client, auth_headers, test_tenant):
        from backend.inventory import service as inv_svc
        from backend.db import session_store
        from backend.sessions.service import create_session

        tid = test_tenant["id"]
        session = create_session(tid, "usr_test", "multi-bodega-test")
        sid = session["id"]
        sku = _sku()

        inv_svc.upsert_stock(tid, sku, {"stock_actual": 100, "lead_time_dias": 10, "bodega": "Norte"})
        inv_svc.upsert_stock(tid, sku, {"stock_actual": 40, "lead_time_dias": 10, "bodega": "Sur"})

        session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [{"value": 1.0}] * 14}}})

        items = inv_svc.get_inventory_status(tid, sid)
        item = next(i for i in items if i["sku"] == sku)
        assert item["stock_actual"] == 140.0  # 100 + 40, not just one bodega's value
