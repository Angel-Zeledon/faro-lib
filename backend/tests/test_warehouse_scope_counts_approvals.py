"""Warehouse scope on the barcode lookup, physical stock counts and purchase
approvals - the three routers `test_warehouse_scope_coverage.py` found unguarded.

Same world as `test_warehouse_scope.py` (principal / Norte / Sur; `norte` is an
analyst limited to Norte, `boss` an unrestricted analyst). Every refusal is
asserted with its code AND with the database unchanged; every allowed write is
read back from the database.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.tests.test_warehouse_scope import (  # noqa: F401 - fixtures
    OUT, _data, _qty, _refused, pos, world,
)

COUNTS = "/api/v1/inventory/stock-counts"
COMPANY_SETTING = "warehouse_scope_company_setting"


def _count_row(count_id):
    return query_one("SELECT * FROM stock_counts WHERE id = %s", (count_id,))


def _n_counts(tid):
    return query_one("SELECT COUNT(*) AS n FROM stock_counts WHERE tenant_id = %s",
                     (tid,))["n"]


def _closed_count(world, warehouse, sku, counted, headers=None):
    """A count of one SKU, closed and ready to apply, made by `headers`
    (the unrestricted analyst by default)."""
    c, h = world.client, headers or world.boss_h
    cid = _data(c.post(COUNTS, headers=h, json={"warehouse": warehouse}))["id"]
    _data(c.put(f"{COUNTS}/{cid}/lines", headers=h,
                json={"sku": sku, "quantity": counted, "mode": "set"}))
    _data(c.post(f"{COUNTS}/{cid}/close", headers=h))
    return cid


# ── Barcode / SKU lookup ─────────────────────────────────────────────────────

class TestLookup:
    def test_without_a_warehouse_the_quantity_is_over_their_warehouses_only(self, world):
        c = world.client
        # A: principal 600 + Norte 5.
        mine = _data(c.get("/api/v1/inventory/stock/lookup", headers=world.norte_h,
                           params={"code": "A"}))
        assert mine["system_qty"] == 5
        full = _data(c.get("/api/v1/inventory/stock/lookup", headers=world.boss_h,
                           params={"code": "A"}))
        assert full["system_qty"] == 605

    def test_a_code_held_only_elsewhere_is_not_found(self, world):
        r = world.client.get("/api/v1/inventory/stock/lookup", headers=world.norte_h,
                             params={"code": "C"})          # principal only
        _refused(r, "lookup_code_not_found", 404)
        assert _data(world.client.get("/api/v1/inventory/stock/lookup",
                                      headers=world.boss_h, params={"code": "C"}))["sku"] == "C"
        r = world.client.get("/api/v1/inventory/stock/lookup", headers=world.nobody_h,
                             params={"code": "A"})
        _refused(r, "lookup_code_not_found", 404)

    def test_naming_a_warehouse_outside_the_scope_is_refused(self, world):
        r = world.client.get("/api/v1/inventory/stock/lookup", headers=world.norte_h,
                             params={"code": "A", "warehouse": "principal"})
        _refused(r, OUT)
        # Case-folded to the existing warehouse, still refused.
        r = world.client.get("/api/v1/inventory/stock/lookup", headers=world.norte_h,
                             params={"code": "A", "warehouse": "PRINCIPAL"})
        _refused(r, OUT)

    def test_their_own_warehouse_returns_only_its_quantity(self, world):
        got = _data(world.client.get("/api/v1/inventory/stock/lookup", headers=world.norte_h,
                                     params={"code": "A", "warehouse": "Norte"}))
        assert got["system_qty"] == 5 and got["warehouse"] == "Norte"
        # An item never stocked in Norte is still identified (that is how it
        # gets counted in), with Norte's quantity - zero - and nothing else.
        s = _data(world.client.get("/api/v1/inventory/stock/lookup", headers=world.norte_h,
                                   params={"code": "S", "warehouse": "Norte"}))
        assert s["sku"] == "S" and s["system_qty"] == 0 and s["in_warehouse"] is False


# ── Physical stock counts ────────────────────────────────────────────────────

class TestCreateCount:
    def test_a_count_outside_the_scope_is_refused_and_nothing_is_created(self, world):
        c = world.client
        before = _n_counts(world.tid)
        _refused(c.post(COUNTS, headers=world.norte_h, json={"warehouse": "principal"}), OUT)
        _refused(c.post(COUNTS, headers=world.norte_h, json={"warehouse": "Sur"}), OUT)
        # No warehouse = the default (principal), which is not theirs.
        _refused(c.post(COUNTS, headers=world.norte_h, json={}), OUT)
        _refused(c.post(COUNTS, headers=world.nobody_h, json={"warehouse": "Norte"}), OUT)
        assert _n_counts(world.tid) == before

    def test_viewer_denied_and_scoped_analyst_creates_in_their_warehouse(self, world):
        c = world.client
        before = _n_counts(world.tid)
        r = c.post(COUNTS, headers=world.norte_v_h, json={"warehouse": "Norte"})
        assert r.status_code == 403, r.text
        assert _n_counts(world.tid) == before

        cid = _data(c.post(COUNTS, headers=world.norte_h, json={"warehouse": "norte"}))["id"]
        row = _count_row(cid)
        assert row["warehouse"] == "Norte" and row["status"] == "open"
        assert row["created_by"] == world.norte["id"]

    def test_unrestricted_analyst_creates_anywhere(self, world):
        cid = _data(world.client.post(COUNTS, headers=world.boss_h,
                                      json={"warehouse": "Sur"}))["id"]
        assert _count_row(cid)["warehouse"] == "Sur"


class TestCountsOfAnotherWarehouse:
    def test_reads_of_another_warehouse_count_answer_not_found(self, world):
        c = world.client
        cid = _closed_count(world, "principal", "C", 10)
        for path in (f"{COUNTS}/{cid}", f"{COUNTS}/{cid}/preview"):
            _refused(c.get(path, headers=world.norte_h), "count_not_found", 404)
            _refused(c.get(path, headers=world.norte_v_h), "count_not_found", 404)
        assert _data(c.get(f"{COUNTS}/{cid}", headers=world.boss_h))["warehouse"] == "principal"

    def test_applying_another_warehouse_count_changes_no_stock(self, world):
        c = world.client
        cid = _closed_count(world, "principal", "C", 10)        # system says 70
        _refused(c.post(f"{COUNTS}/{cid}/apply", headers=world.norte_h),
                 "count_not_found", 404)
        _refused(c.post(f"{COUNTS}/{cid}/apply", headers=world.norte_h, json={"skus": ["C"]}),
                 "count_not_found", 404)
        assert _qty(world.tid, "C", "principal") == 70
        assert _count_row(cid)["status"] == "closed"
        assert query("SELECT 1 FROM stock_adjustments WHERE ref_id = %s", (cid,)) == []

        # The unrestricted analyst applies it: the difference lands.
        _data(c.post(f"{COUNTS}/{cid}/apply", headers=world.boss_h))
        assert _qty(world.tid, "C", "principal") == 10
        assert _count_row(cid)["status"] == "applied"

    def test_lines_close_and_cancel_of_another_warehouse_count_are_refused(self, world):
        c = world.client
        cid = _data(c.post(COUNTS, headers=world.boss_h, json={"warehouse": "principal"}))["id"]
        _data(c.put(f"{COUNTS}/{cid}/lines", headers=world.boss_h,
                    json={"sku": "C", "quantity": 3, "mode": "set"}))

        _refused(c.put(f"{COUNTS}/{cid}/lines", headers=world.norte_h,
                       json={"sku": "C", "quantity": 99, "mode": "set"}), "count_not_found", 404)
        _refused(c.delete(f"{COUNTS}/{cid}/lines/C", headers=world.norte_h),
                 "count_not_found", 404)
        _refused(c.post(f"{COUNTS}/{cid}/close", headers=world.norte_h), "count_not_found", 404)
        _refused(c.post(f"{COUNTS}/{cid}/cancel", headers=world.norte_h), "count_not_found", 404)

        line = query_one("SELECT counted_qty FROM stock_count_lines WHERE count_id = %s "
                         "AND sku = 'C'", (cid,))
        assert float(line["counted_qty"]) == 3
        assert _count_row(cid)["status"] == "open"

    def test_a_count_that_does_not_exist_still_reads_as_not_found(self, world):
        _refused(world.client.get(f"{COUNTS}/no-such-count", headers=world.norte_h),
                 "count_not_found", 404)


class TestCountsOfTheirWarehouse:
    def test_viewer_denied_and_scoped_analyst_applies_their_own_count(self, world):
        c = world.client
        cid = _closed_count(world, "Norte", "B", 44, headers=world.norte_h)   # system 50

        r = c.post(f"{COUNTS}/{cid}/apply", headers=world.norte_v_h)
        assert r.status_code == 403, r.text
        assert _qty(world.tid, "B", "Norte") == 50
        assert _count_row(cid)["status"] == "closed"

        res = _data(c.post(f"{COUNTS}/{cid}/apply", headers=world.norte_h))
        assert res["warehouse"] == "Norte"
        assert _qty(world.tid, "B", "Norte") == 44
        assert _count_row(cid)["status"] == "applied"
        adj = query_one("SELECT warehouse, delta FROM stock_adjustments WHERE ref_id = %s",
                        (cid,))
        assert adj["warehouse"] == "Norte" and float(adj["delta"]) == -6

    def test_the_list_holds_only_their_warehouses_counts(self, world):
        c = world.client
        main = _data(c.post(COUNTS, headers=world.boss_h, json={"warehouse": "principal"}))["id"]
        norte = _data(c.post(COUNTS, headers=world.boss_h, json={"warehouse": "Norte"}))["id"]
        sur = _data(c.post(COUNTS, headers=world.boss_h, json={"warehouse": "Sur"}))["id"]

        mine = {r["id"] for r in _data(c.get(COUNTS, headers=world.norte_h))}
        assert norte in mine and not mine & {main, sur}
        assert {r["warehouse"] for r in _data(c.get(COUNTS, headers=world.norte_v_h))} == {"Norte"}
        everyone = {r["id"] for r in _data(c.get(COUNTS, headers=world.boss_h))}
        assert {main, norte, sur} <= everyone
        assert _data(c.get(COUNTS, headers=world.nobody_h)) == []

    def test_the_list_limit_counts_only_visible_rows(self, world):
        c = world.client
        norte = _data(c.post(COUNTS, headers=world.boss_h, json={"warehouse": "Norte"}))["id"]
        for _ in range(3):           # newer, and none of them theirs
            _data(c.post(COUNTS, headers=world.boss_h, json={"warehouse": "principal"}))
        got = _data(c.get(COUNTS, headers=world.norte_h, params={"limit": 1}))
        assert [r["id"] for r in got] == [norte]


# ── Purchase approvals ───────────────────────────────────────────────────────

@pytest.fixture
def approvals(pos):
    """The two orders of `pos` (Norte and principal, worth 20 each) under a rule
    that every order needs approval; `norte` (scoped) and the admin approve."""
    w = pos
    execute("UPDATE users SET can_approve_po = TRUE WHERE id = ANY(%s)",
            ([w.norte["id"], w.admin[0]["id"]],))
    w.rule_any = query_one(
        "INSERT INTO po_approval_rules (tenant_id, threshold, created_by) "
        "VALUES (%s, 1, 'test') RETURNING id", (w.tid,))["id"]
    return w


def _approval_rows(po_id):
    return query("SELECT status FROM po_approvals WHERE po_log_id = %s", (po_id,))


def _po_state(po_id):
    return query_one("SELECT approval_status, approved_amount FROM inventory_po_log "
                     "WHERE id = %s", (po_id,))


class TestApprovalPerOrder:
    def test_reading_the_approval_of_another_warehouse_order_is_refused(self, approvals):
        c = approvals.client
        _refused(c.get(f"/api/v1/inventory/po/{approvals.po_main}/approval",
                       headers=approvals.norte_h), OUT)
        _refused(c.get(f"/api/v1/inventory/po/{approvals.po_main}/approval",
                       headers=approvals.norte_v_h), OUT)
        own = _data(c.get(f"/api/v1/inventory/po/{approvals.po_norte}/approval",
                          headers=approvals.norte_h))
        assert own["po_log_id"] == approvals.po_norte and own["required"] is True

    def test_requesting_approval_outside_the_scope_writes_nothing(self, approvals):
        c = approvals.client
        before = _po_state(approvals.po_main)
        _refused(c.post(f"/api/v1/inventory/po/{approvals.po_main}/approval/request",
                        headers=approvals.norte_h), OUT)
        assert _approval_rows(approvals.po_main) == []
        assert _po_state(approvals.po_main) == before

        # Viewer denied on their own warehouse's order; the analyst succeeds.
        r = c.post(f"/api/v1/inventory/po/{approvals.po_norte}/approval/request",
                   headers=approvals.norte_v_h)
        assert r.status_code == 403, r.text
        assert _approval_rows(approvals.po_norte) == []
        _data(c.post(f"/api/v1/inventory/po/{approvals.po_norte}/approval/request",
                     headers=approvals.boss_h))
        assert [r["status"] for r in _approval_rows(approvals.po_norte)] == ["requested"]
        assert _po_state(approvals.po_norte)["approval_status"] == "pending_approval"

    def test_a_scoped_approver_cannot_decide_another_warehouse_order(self, approvals):
        c = approvals.client
        _data(c.post(f"/api/v1/inventory/po/{approvals.po_main}/approval/request",
                     headers=approvals.boss_h))
        before = _po_state(approvals.po_main)

        _refused(c.post(f"/api/v1/inventory/po/{approvals.po_main}/approval/approve",
                        headers=approvals.norte_h), OUT)
        _refused(c.post(f"/api/v1/inventory/po/{approvals.po_main}/approval/reject",
                        headers=approvals.norte_h,
                        json={"comment": "Not needed this month, too much stock"}), OUT)
        assert [r["status"] for r in _approval_rows(approvals.po_main)] == ["requested"]
        assert _po_state(approvals.po_main) == before

        # The unrestricted approver decides it.
        _data(c.post(f"/api/v1/inventory/po/{approvals.po_main}/approval/approve",
                     headers=approvals.admin_h))
        assert [r["status"] for r in _approval_rows(approvals.po_main)] == ["approved"]
        assert _po_state(approvals.po_main)["approval_status"] == "approved"

    def test_a_scoped_approver_decides_their_own_warehouse_order(self, approvals):
        c = approvals.client
        _data(c.post(f"/api/v1/inventory/po/{approvals.po_norte}/approval/request",
                     headers=approvals.boss_h))
        _data(c.post(f"/api/v1/inventory/po/{approvals.po_norte}/approval/reject",
                     headers=approvals.norte_h,
                     json={"comment": "Norte is full until the end of the month"}))
        assert [r["status"] for r in _approval_rows(approvals.po_norte)] == ["rejected"]
        assert _po_state(approvals.po_norte)["approval_status"] == "rejected"


class TestApprovalInbox:
    def test_the_inbox_holds_only_orders_of_their_warehouses(self, approvals):
        c = approvals.client
        for po in (approvals.po_norte, approvals.po_main):
            _data(c.post(f"/api/v1/inventory/po/{po}/approval/request",
                         headers=approvals.boss_h))
        mine = _data(c.get("/api/v1/inventory/po-approval/pending", headers=approvals.norte_h))
        assert mine["is_approver"] is True
        assert [i["po_log_id"] for i in mine["items"]] == [approvals.po_norte]
        full = _data(c.get("/api/v1/inventory/po-approval/pending", headers=approvals.admin_h))
        assert {i["po_log_id"] for i in full["items"]} == {approvals.po_norte, approvals.po_main}


class TestApprovalRules:
    @pytest.fixture
    def scoped_admin(self, approvals):
        u, h = approvals.person("admin", ["Norte"])
        approvals.rule_main = query_one(
            "INSERT INTO po_approval_rules (tenant_id, threshold, warehouse, created_by) "
            "VALUES (%s, 500, 'principal', 'test') RETURNING id", (approvals.tid,))["id"]
        approvals.rule_norte = query_one(
            "INSERT INTO po_approval_rules (tenant_id, threshold, warehouse, created_by) "
            "VALUES (%s, 700, 'Norte', 'test') RETURNING id", (approvals.tid,))["id"]
        return u, h

    def _rules(self, tid):
        return query("SELECT id, threshold, warehouse, active FROM po_approval_rules "
                     "WHERE tenant_id = %s ORDER BY id", (tid,))

    def test_a_scoped_admin_cannot_change_rules_or_approvers(self, approvals, scoped_admin):
        c, (_, h) = approvals.client, scoped_admin
        rules_before = self._rules(approvals.tid)
        boss_flag = query_one("SELECT can_approve_po FROM users WHERE id = %s",
                              (approvals.boss["id"],))["can_approve_po"]

        _refused(c.post("/api/v1/inventory/po-approval/rules", headers=h,
                        json={"threshold": 10, "warehouse": "Norte"}), COMPANY_SETTING)
        _refused(c.patch(f"/api/v1/inventory/po-approval/rules/{approvals.rule_main}",
                         headers=h, json={"active": False}), COMPANY_SETTING)
        _refused(c.patch(f"/api/v1/inventory/po-approval/rules/{approvals.rule_norte}",
                         headers=h, json={"threshold": 1}), COMPANY_SETTING)
        _refused(c.delete(f"/api/v1/inventory/po-approval/rules/{approvals.rule_main}",
                          headers=h), COMPANY_SETTING)
        _refused(c.put(f"/api/v1/inventory/po-approval/approvers/{approvals.boss['id']}",
                       headers=h, json={"can_approve": True}), COMPANY_SETTING)

        assert self._rules(approvals.tid) == rules_before
        assert query_one("SELECT can_approve_po FROM users WHERE id = %s",
                         (approvals.boss["id"],))["can_approve_po"] == boss_flag

    def test_the_unrestricted_admin_still_changes_them(self, approvals, scoped_admin):
        c = approvals.client
        _data(c.patch(f"/api/v1/inventory/po-approval/rules/{approvals.rule_main}",
                      headers=approvals.admin_h, json={"active": False}))
        assert query_one("SELECT active FROM po_approval_rules WHERE id = %s",
                         (approvals.rule_main,))["active"] is False
        _data(c.put(f"/api/v1/inventory/po-approval/approvers/{approvals.boss['id']}",
                    headers=approvals.admin_h, json={"can_approve": True}))
        assert query_one("SELECT can_approve_po FROM users WHERE id = %s",
                         (approvals.boss["id"],))["can_approve_po"] is True

    def test_settings_hide_rules_naming_another_warehouse(self, approvals, scoped_admin):
        c = approvals.client
        mine = _data(c.get("/api/v1/inventory/po-approval/settings", headers=approvals.norte_h))
        ids = {r["id"] for r in mine["rules"]}
        assert approvals.rule_any in ids and approvals.rule_norte in ids
        assert approvals.rule_main not in ids
        assert "principal" not in {r["warehouse"] for r in mine["rules"]}
        assert mine["enabled"] is True
        full = _data(c.get("/api/v1/inventory/po-approval/settings", headers=approvals.admin_h))
        assert {approvals.rule_any, approvals.rule_norte, approvals.rule_main} <= {
            r["id"] for r in full["rules"]}
