"""
Math audit 2026-10-01 — findings inside the semáforo threshold logic.

Written as failing tests while another workstream owned `_calc_signal`; fixed
2026-10-02 at the call sites in backend/inventory/service.py (an empty shelf
with no demand is OK, not SOBRESTOCK). Kept as the regression guard.
"""

from backend.db import session_store
from backend.inventory import service as inv_svc
from backend.sessions.service import create_session


class TestAnEmptyShelfIsNotOverstock:

    def test_zero_stock_and_zero_demand_is_not_sobrestock(self, client, auth_headers, test_tenant):
        """0 units on hand and a forecast of 0: `coverage_days = 0 / 0` falls to
        the 9999 sentinel and the row is painted SOBRESTOCK — "overstock" on an
        empty shelf. `_calc_signal`'s own docstring says the sentinel means "a
        dead SKU with ANY stock at all is overstock"; the caller never checks
        that there is any stock."""
        tid = test_tenant["id"]
        sid = create_session(tid, "usr_test", "ma-empty-shelf")["id"]
        r = client.put("/api/v1/inventory/stock/MA-EMPTY",
                       json={"current_stock": 0, "lead_time_days": 10, "moq": 1},
                       headers=auth_headers)
        assert r.status_code == 200, r.text
        session_store.set_forecasts(tid, sid, {"MA-EMPTY": {"lightgbm": {"forecast": [
            {"date": f"2026-01-{i + 1:02d}", "value": 0.0, "lower": 0.0, "upper": 0.0}
            for i in range(14)]}}})
        item = next(i for i in inv_svc.get_inventory_status(tid, sid) if i["sku"] == "MA-EMPTY")
        assert item["signal"] != "SOBRESTOCK", item
