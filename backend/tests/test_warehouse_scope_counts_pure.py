"""Pure checks (no database) of the scope pieces added for stock counts, the
barcode lookup and approval settings. The HTTP + database behaviour lives in
`test_warehouse_scope_counts_approvals.py`."""

from __future__ import annotations

import pytest

from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser
from backend.errors import AppError
from backend.inventory import stock_count_service as count_svc


def _user(names):
    u = CurrentUser("u1", "t1", "analyst")
    u.scope_cache = None if names is None else frozenset(names)
    return u


class TestCountGuard:
    def test_unrestricted_never_reads_the_count(self, monkeypatch):
        monkeypatch.setattr(wscope, "query_one",
                            lambda *a, **k: pytest.fail("must not query"))
        wscope.require_count_in_scope(_user(None), "c1")

    def test_another_warehouse_count_is_not_found(self, monkeypatch):
        monkeypatch.setattr(wscope, "query_one", lambda *a, **k: {"warehouse": "principal"})
        with pytest.raises(AppError) as e:
            wscope.require_count_in_scope(_user({"Norte"}), "c1")
        assert e.value.code == "count_not_found" and e.value.status_code == 404

    def test_their_own_count_passes_case_folded(self, monkeypatch):
        monkeypatch.setattr(wscope, "query_one", lambda *a, **k: {"warehouse": "NORTE"})
        wscope.require_count_in_scope(_user({"Norte"}), "c1")

    def test_a_missing_count_is_left_to_the_service(self, monkeypatch):
        monkeypatch.setattr(wscope, "query_one", lambda *a, **k: None)
        wscope.require_count_in_scope(_user({"Norte"}), "nope")

    def test_an_empty_scope_sees_no_count(self, monkeypatch):
        monkeypatch.setattr(wscope, "query_one", lambda *a, **k: {"warehouse": "Norte"})
        with pytest.raises(AppError):
            wscope.require_count_in_scope(_user(set()), "c1")


class TestCompanySetting:
    def test_scoped_refused_unrestricted_passes(self):
        with pytest.raises(AppError) as e:
            wscope.require_company_setting(_user({"Norte"}))
        assert e.value.code == "warehouse_scope_company_setting"
        assert e.value.status_code == 403
        wscope.require_company_setting(_user(None))


class TestLookupVisibility:
    ROWS = [
        {"sku": "A", "warehouse": "Norte", "display_name": "A", "barcode": "111",
         "unit_of_measure": "u", "unit_cost": 2.0, "current_stock": 5, "category": None},
        {"sku": "A", "warehouse": "principal", "display_name": "A", "barcode": "111",
         "unit_of_measure": "u", "unit_cost": 2.0, "current_stock": 600, "category": None},
        {"sku": "C", "warehouse": "principal", "display_name": "C", "barcode": "333",
         "unit_of_measure": "u", "unit_cost": 1.0, "current_stock": 70, "category": None},
    ]

    @pytest.fixture(autouse=True)
    def _db(self, monkeypatch):
        def fake_query(sql, params, **k):
            if "sku = %s ORDER BY warehouse" in sql:
                return [r for r in self.ROWS if r["sku"] == params[1]]
            code = params[1]
            return [r for r in self.ROWS if code in (r["sku"], r["barcode"])]
        monkeypatch.setattr(count_svc, "query", fake_query)

    def test_unrestricted_sums_every_warehouse(self):
        assert count_svc.lookup("t1", "111")["system_qty"] == 605

    def test_visible_sums_only_those_warehouses(self):
        got = count_svc.lookup("t1", "111", visible=lambda w: w == "Norte")
        assert got["sku"] == "A" and got["system_qty"] == 5

    def test_a_code_held_only_elsewhere_is_not_found(self):
        with pytest.raises(AppError) as e:
            count_svc.lookup("t1", "333", visible=lambda w: w == "Norte")
        assert e.value.code == "lookup_code_not_found" and e.value.status_code == 404
