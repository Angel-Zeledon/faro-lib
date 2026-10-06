"""Purchase budgets: the ledger, usage, the plan, the order hook and warehouse
scope. Every refusal also asserts the tables unchanged; every write asserts the
rows with a direct query."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query, query_one
from backend.inventory import service as inv_svc
from backend.users import service as user_svc

BUDGETS = "/api/v1/inventory/budgets"
STATUS = "/api/v1/inventory/budget/status"
PLAN = "/api/v1/inventory/budget/plan"
CHECK = "/api/v1/inventory/budget/check"
LOG_PO = "/api/v1/inventory/log-po"


def _body(**over):
    b = {"period_type": "month", "period_start": date.today().isoformat(), "amount": 1000.0}
    b.update(over)
    return b


def _rows(tid):
    return query("SELECT * FROM purchase_budgets WHERE tenant_id = %s ORDER BY created_at", (tid,))


def _order(client, headers, *, qty, cost, supplier="Acme", reason=None, warehouse=None):
    body = {"items": [{"sku": f"B-{uuid4().hex[:4]}", "supplier": supplier, "signal": "PEDIR_YA",
                       "recommended_qty": qty, "final_qty": qty, "unit_cost": cost,
                       "status": "approved"}]}
    if reason:
        body["budget_override_reason"] = reason
    if warehouse:
        body["destination_warehouse"] = warehouse
    return client.post(LOG_PO, params={"session_id": f"sess_{uuid4().hex[:6]}"},
                       json=body, headers=headers)


def _events(tid, action):
    return query("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = %s", (tid, action))


class TestLedger:

    def test_no_budget_means_nothing_changes(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        st = client.get(STATUS, headers=analyst_headers)
        assert st.status_code == 200 and st.json()["data"]["budget"] is None
        r = _order(client, analyst_headers, qty=10, cost=5.0)
        assert r.status_code == 201
        assert "budget_warnings" not in r.json()["data"]
        assert _rows(tid) == []
        assert not _events(tid, "purchase_budget.exceeded")

    def test_viewer_cannot_create_analyst_can(self, client, test_tenant, viewer_headers,
                                              analyst_headers, analyst_user):
        tid = test_tenant["id"]
        denied = client.post(BUDGETS, json=_body(), headers=viewer_headers)
        assert denied.status_code == 403
        assert _rows(tid) == []
        ok = client.post(BUDGETS, json=_body(), headers=analyst_headers)
        assert ok.status_code == 201, ok.text
        rows = _rows(tid)
        assert len(rows) == 1
        r = rows[0]
        assert (r["revision"], r["amount"], r["scope_type"], r["scope_value"], r["active"],
                r["hard_cap"], r["superseded_by"]) == (1, 1000.0, "company", None, True, False, None)
        assert r["root_id"] == r["id"] and r["created_by"] == analyst_user["user"]["id"]
        assert r["period_start"].day == 1
        assert _events(tid, "purchase_budget.created")

    def test_viewer_can_read(self, client, analyst_headers, viewer_headers):
        client.post(BUDGETS, json=_body(), headers=analyst_headers)
        assert client.get(BUDGETS, headers=viewer_headers).status_code == 200
        assert client.get(STATUS, headers=viewer_headers).json()["data"]["budget"] is not None

    def test_revision_is_append_only(self, client, test_tenant, analyst_headers, viewer_headers):
        tid = test_tenant["id"]
        root = client.post(BUDGETS, json=_body(), headers=analyst_headers).json()["data"]["root_id"]
        denied = client.patch(f"{BUDGETS}/{root}", json={"expected_revision": 1, "amount": 5},
                              headers=viewer_headers)
        assert denied.status_code == 403 and len(_rows(tid)) == 1
        ok = client.patch(f"{BUDGETS}/{root}", json={"expected_revision": 1, "amount": 2500,
                                                     "hard_cap": True}, headers=analyst_headers)
        assert ok.status_code == 200, ok.text
        rows = _rows(tid)
        assert [(r["revision"], r["amount"]) for r in rows] == [(1, 1000.0), (2, 2500.0)]
        assert rows[0]["superseded_by"] == rows[1]["id"] and rows[0]["superseded_at"] is not None
        assert rows[1]["superseded_by"] is None and rows[1]["hard_cap"] is True
        assert rows[0]["amount"] == 1000.0  # the old revision is untouched
        hist = client.get(f"{BUDGETS}/{root}/history", headers=viewer_headers).json()["data"]["items"]
        assert [h["revision"] for h in hist] == [2, 1]
        assert _events(tid, "purchase_budget.revised")

    def test_stale_revision_conflicts_and_writes_nothing(self, client, test_tenant, analyst_headers):
        root = client.post(BUDGETS, json=_body(), headers=analyst_headers).json()["data"]["root_id"]
        client.patch(f"{BUDGETS}/{root}", json={"expected_revision": 1, "amount": 10}, headers=analyst_headers)
        stale = client.patch(f"{BUDGETS}/{root}", json={"expected_revision": 1, "amount": 99},
                             headers=analyst_headers)
        assert stale.status_code == 409 and stale.json()["error_code"] == "purchase_budget_stale"
        assert len(_rows(test_tenant["id"])) == 2

    def test_overlapping_active_budget_of_same_scope_refused(self, client, test_tenant, analyst_headers):
        client.post(BUDGETS, json=_body(), headers=analyst_headers)
        dup = client.post(BUDGETS, json=_body(amount=5), headers=analyst_headers)
        assert dup.status_code == 409 and dup.json()["error_code"] == "purchase_budget_overlap"
        assert len(_rows(test_tenant["id"])) == 1
        # an inactive duplicate is fine (a draft the owner keeps aside)
        assert client.post(BUDGETS, json=_body(active=False), headers=analyst_headers).status_code == 201

    @pytest.mark.parametrize("patch", [
        {"amount": -1}, {"period_type": "week"}, {"scope_type": "warehouse"},
        {"period_type": "custom"}, {"scope_type": "supplier", "scope_value": "nope"},
    ])
    def test_invalid_terms_422_and_nothing_written(self, client, test_tenant, analyst_headers, patch):
        r = client.post(BUDGETS, json=_body(**patch), headers=analyst_headers)
        assert r.status_code == 422
        assert _rows(test_tenant["id"]) == []

    def test_parent_cannot_form_a_cycle(self, client, test_tenant, analyst_headers):
        a = client.post(BUDGETS, json=_body(), headers=analyst_headers).json()["data"]["root_id"]
        sup_id = uuid4().hex
        execute("INSERT INTO suppliers (id, tenant_id, name) VALUES (%s, %s, 'Acme')",
                (sup_id, test_tenant["id"]))
        b = client.post(BUDGETS, json=_body(scope_type="supplier", scope_value=sup_id,
                                            parent_root_id=a), headers=analyst_headers)
        assert b.status_code == 201, b.text
        cyc = client.patch(f"{BUDGETS}/{a}", json={"expected_revision": 1,
                                                   "parent_root_id": b.json()["data"]["root_id"]},
                           headers=analyst_headers)
        assert cyc.status_code == 422 and cyc.json()["error_code"] == "purchase_budget_parent_invalid"
        assert len(_rows(test_tenant["id"])) == 2


class TestUsage:

    def _po(self, tid, *, qty, cost, status="pending", cancelled=False, supplier="Acme", when=None):
        po_id = uuid4().hex
        nxt = query_one("SELECT COALESCE(MAX(po_number), 0) + 1 AS n FROM inventory_po_log "
                        "WHERE tenant_id = %s", (tid,))["n"]
        execute("""INSERT INTO inventory_po_log
                       (id, tenant_id, session_id, generated_at, sku_count, total_units, total_value,
                        reception_status, cancelled_at, po_number)
                   VALUES (%s, %s, 's', %s, 1, %s, %s, %s, %s, %s)""",
                (po_id, tid, when or datetime.now(timezone.utc), qty, qty * cost, status,
                 datetime.now(timezone.utc) if cancelled else None, nxt))
        execute("""INSERT INTO inventory_po_items
                       (po_log_id, tenant_id, sku, supplier, signal, recommended_qty, final_qty,
                        unit_cost, status)
                   VALUES (%s, %s, 'X', %s, 'PEDIR_YA', %s, %s, %s, 'approved')""",
                (po_id, tid, supplier, qty, qty, cost))
        return po_id

    def test_spent_committed_remaining_and_cancelled_excluded(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        client.post(BUDGETS, json=_body(amount=1000), headers=analyst_headers)
        self._po(tid, qty=10, cost=10, status="received")     # 100 spent
        self._po(tid, qty=20, cost=10, status="pending")      # 200 committed
        self._po(tid, qty=50, cost=10, status="pending", cancelled=True)  # not counted
        old = datetime.now(timezone.utc) - timedelta(days=90)
        self._po(tid, qty=99, cost=10, status="received", when=old)       # outside the period
        data = client.get(STATUS, headers=analyst_headers).json()["data"]
        u = data["usage"]
        assert (u["spent"], u["committed"], u["remaining"], u["free"]) == (100.0, 200.0, 700.0, 700.0)
        assert 0 <= u["burn"]["elapsed_fraction"] <= 1 and u["burn"]["used_fraction"] == 0.3

    def test_lines_without_cost_are_counted_not_zeroed(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        client.post(BUDGETS, json=_body(amount=1000), headers=analyst_headers)
        po = self._po(tid, qty=5, cost=1, status="pending")
        execute("UPDATE inventory_po_items SET unit_cost = NULL WHERE po_log_id = %s", (po,))
        data = client.get(STATUS, headers=analyst_headers).json()["data"]
        assert data["usage"]["unknown_cost_lines"] == 1 and data["usage"]["committed"] == 0
        assert {"code": "ordered_lines_without_cost", "params": {"count": 1}} in data["warnings"]

    def test_supplier_scope_counts_only_that_supplier(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sid = uuid4().hex
        execute("INSERT INTO suppliers (id, tenant_id, name) VALUES (%s, %s, 'Acme')", (sid, tid))
        root = client.post(BUDGETS, json=_body(scope_type="supplier", scope_value=sid, amount=500),
                           headers=analyst_headers).json()["data"]["root_id"]
        self._po(tid, qty=10, cost=10, supplier="Acme")
        self._po(tid, qty=10, cost=10, supplier="Other")
        u = client.get(STATUS, params={"budget_id": root}, headers=analyst_headers).json()["data"]["usage"]
        assert u["committed"] == 100.0

    def test_child_is_limited_by_its_parent(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sid = uuid4().hex
        execute("INSERT INTO suppliers (id, tenant_id, name) VALUES (%s, %s, 'Acme')", (sid, tid))
        parent = client.post(BUDGETS, json=_body(amount=300), headers=analyst_headers).json()["data"]["root_id"]
        child = client.post(BUDGETS, json=_body(scope_type="supplier", scope_value=sid, amount=900,
                                                parent_root_id=parent),
                            headers=analyst_headers).json()["data"]["root_id"]
        self._po(tid, qty=10, cost=10, supplier="Other")   # 100 against the company only
        data = client.get(STATUS, params={"budget_id": child}, headers=analyst_headers).json()["data"]
        assert data["usage"]["remaining"] == 900.0
        assert data["usage"]["free"] == 200.0 and data["usage"]["limited_by"] == "parent"


class TestOrderHook:

    def test_soft_budget_warns_and_still_orders(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        client.post(BUDGETS, json=_body(amount=100), headers=analyst_headers)
        r = _order(client, analyst_headers, qty=20, cost=10.0)   # 200 > 100
        assert r.status_code == 201, r.text
        w = r.json()["data"]["budget_warnings"]
        assert len(w) == 1 and w[0]["over_by"] == 100.0 and w[0]["hard_cap"] is False
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s", (tid,))["n"] == 1
        ev = _events(tid, "purchase_budget.exceeded")
        assert len(ev) == 1

    def test_order_inside_the_budget_has_no_warning(self, client, test_tenant, analyst_headers):
        client.post(BUDGETS, json=_body(amount=1000), headers=analyst_headers)
        r = _order(client, analyst_headers, qty=20, cost=10.0)
        assert r.status_code == 201 and "budget_warnings" not in r.json()["data"]
        assert not _events(test_tenant["id"], "purchase_budget.exceeded")

    def test_hard_cap_blocks_an_analyst_even_with_a_reason(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        client.post(BUDGETS, json=_body(amount=100, hard_cap=True), headers=analyst_headers)
        blocked = _order(client, analyst_headers, qty=20, cost=10.0)
        assert blocked.status_code == 409 and blocked.json()["error_code"] == "purchase_budget_hard_cap"
        reasoned = _order(client, analyst_headers, qty=20, cost=10.0, reason="urgent")
        assert reasoned.status_code == 403
        assert reasoned.json()["error_code"] == "purchase_budget_override_requires_admin"
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s", (tid,))["n"] == 0

    def test_hard_cap_admin_needs_a_reason_and_it_is_audited(self, client, test_tenant, auth_headers,
                                                             analyst_headers):
        tid = test_tenant["id"]
        client.post(BUDGETS, json=_body(amount=100, hard_cap=True), headers=analyst_headers)
        no_reason = _order(client, auth_headers, qty=20, cost=10.0)
        assert no_reason.status_code == 409
        assert no_reason.json()["error_params"]["override_possible"] is True
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s", (tid,))["n"] == 0
        ok = _order(client, auth_headers, qty=20, cost=10.0, reason="customer contract deadline")
        assert ok.status_code == 201, ok.text
        ev = _events(tid, "purchase_budget.override")
        assert len(ev) == 1
        assert "customer contract deadline" in json.dumps(ev[0], default=str)

    def test_order_within_hard_cap_is_untouched(self, client, test_tenant, analyst_headers):
        client.post(BUDGETS, json=_body(amount=1000, hard_cap=True), headers=analyst_headers)
        assert _order(client, analyst_headers, qty=20, cost=10.0).status_code == 201

    def test_unknown_cost_order_never_trips_the_cap(self, client, test_tenant, analyst_headers):
        client.post(BUDGETS, json=_body(amount=1, hard_cap=True), headers=analyst_headers)
        body = {"items": [{"sku": "NC", "signal": "PEDIR_YA", "recommended_qty": 50,
                           "final_qty": 50, "status": "approved"}]}
        r = client.post(LOG_PO, params={"session_id": "s1"}, json=body, headers=analyst_headers)
        # nothing measurable to compare: the order is not blocked by a made-up zero
        assert r.status_code == 201

    def test_check_preview_matches_without_writing(self, client, test_tenant, analyst_headers,
                                                   viewer_headers):
        client.post(BUDGETS, json=_body(amount=100), headers=analyst_headers)
        payload = {"lines": [{"sku": "A", "qty": 20, "unit_cost": 10}]}
        r = client.post(CHECK, json=payload, headers=viewer_headers)
        assert r.status_code == 200 and r.json()["data"]["exceeded"][0]["over_by"] == 100.0
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                         (test_tenant["id"],))["n"] == 0

    def test_keyed_replay_is_not_checked_again(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        key = uuid4().hex
        client.post(BUDGETS, json=_body(amount=100, hard_cap=True), headers=analyst_headers)
        # First order fits.
        body = {"items": [{"sku": "K", "signal": "PEDIR_YA", "recommended_qty": 8, "final_qty": 8,
                           "unit_cost": 10, "status": "approved"}]}
        first = client.post(LOG_PO, params={"session_id": "s"}, json=body,
                            headers={**analyst_headers, "Idempotency-Key": key})
        assert first.status_code == 201
        # The replay now sees only 20 free but must answer with the existing order.
        again = client.post(LOG_PO, params={"session_id": "s"}, json=body,
                            headers={**analyst_headers, "Idempotency-Key": key})
        assert again.status_code == 200 and again.json()["data"]["replayed"] is True
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s", (tid,))["n"] == 1


class TestPlan:

    def test_no_budget_answers_with_no_lines(self, client, analyst_headers):
        r = client.post(PLAN, json={}, headers=analyst_headers)
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["budget"] is None and d["lines"] == []
        assert {"code": "no_budget", "params": {}} in d["warnings"]

    def test_plan_creates_no_order_and_changes_no_budget(self, client, test_tenant, analyst_headers,
                                                         viewer_headers, monkeypatch):
        """The plan is read-only: the funded/deferred split is computed over the
        status rows, nothing is written. Rows are stubbed so the split is hand-checkable."""
        from backend.inventory import purchase_budget_service as svc
        tid = test_tenant["id"]
        client.post(BUDGETS, json=_body(amount=150), headers=analyst_headers)
        rows = [
            {"sku": "A", "supplier": "S", "warehouse": None, "recommended_qty": 10, "unit_cost": 10.0,
             "signal": "PEDIR_YA", "abc": "A", "moq": 1, "money_at_risk": 500.0, "display_name": "A"},
            {"sku": "B", "supplier": "S", "warehouse": None, "recommended_qty": 10, "unit_cost": 10.0,
             "signal": "PEDIR_YA", "abc": "B", "moq": 1, "money_at_risk": 100.0, "display_name": "B"},
            {"sku": "C", "supplier": "S", "warehouse": None, "recommended_qty": 5, "unit_cost": None,
             "signal": "PEDIR_PRONTO", "abc": "C", "moq": 1, "money_at_risk": 50.0, "display_name": "C"},
        ]
        monkeypatch.setattr(svc, "candidate_rows", lambda user, session_id, row: (rows, "daily"))
        r = client.post(PLAN, json={"session_id": "s"}, headers=viewer_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        by = {ln["sku"]: ln for ln in d["lines"]}
        assert by["A"]["status"] == "funded" and by["A"]["funded_qty"] == 10
        assert by["B"]["status"] == "partial" and by["B"]["funded_qty"] == 5   # 50 left
        assert by["C"]["status"] == "cost_unknown"
        assert d["summary"]["funded_cost"] == 150.0 and d["summary"]["cost_unknown_lines"] == 1
        assert any(w["code"] == "cost_unknown_lines" for w in d["warnings"])
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s", (tid,))["n"] == 0
        assert len(_rows(tid)) == 1

    def test_plan_with_a_larger_budget_equals_no_cap(self, client, test_tenant, analyst_headers, monkeypatch):
        from backend.inventory import purchase_budget_service as svc
        client.post(BUDGETS, json=_body(amount=10_000_000), headers=analyst_headers)
        rows = [{"sku": f"S{i}", "supplier": "S", "warehouse": None, "recommended_qty": 10 + i,
                 "unit_cost": 3.0, "signal": "PEDIR_YA", "abc": "A", "moq": 1,
                 "money_at_risk": 10.0 * i} for i in range(1, 6)]
        monkeypatch.setattr(svc, "candidate_rows", lambda user, session_id, row: (rows, "daily"))
        d = client.post(PLAN, json={"session_id": "s"}, headers=analyst_headers).json()["data"]
        assert all(ln["status"] == "funded" and ln["funded_qty"] == ln["recommended_qty"]
                   for ln in d["lines"])


class TestWarehouseScope:

    @pytest.fixture
    def world(self, client, registered_user):
        class W: ...
        w = W()
        w.tid = tid = registered_user["tenant"]["id"]
        w.admin_h = None
        for name in ("Norte", "Sur"):  # a stock row creates the warehouse
            inv_svc.upsert_stock(tid, f"WB-{name}", {"current_stock": 10, "lead_time_days": 10,
                                                    "warehouse": name, "moq": 1})
        w.wh = {r["name"]: r["id"] for r in query(
            "SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))}

        def person(role, scope=None):
            email = f"{role}-{uuid4().hex[:8]}@example.com"
            u = user_svc.create_user(tenant_id=tid, email=email, password="TestPass123!", role=role)
            user_svc.mark_verified(tid, u["id"])
            if scope is not None:
                execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                        (json.dumps([w.wh[n] for n in scope]), u["id"]))
            tok = create_access_token(u["id"], tid, role, email_verified=True)
            return {"Authorization": f"Bearer {tok}"}

        w.boss = person("analyst")
        w.norte = person("analyst", ["Norte"])
        return w

    def test_scoped_user_sees_and_edits_only_their_warehouse_budgets(self, client, world):
        company = client.post(BUDGETS, json=_body(amount=900), headers=world.boss).json()["data"]
        sur = client.post(BUDGETS, json=_body(scope_type="warehouse", scope_value=world.wh["Sur"],
                                              amount=100), headers=world.boss).json()["data"]
        norte = client.post(BUDGETS, json=_body(scope_type="warehouse", scope_value=world.wh["Norte"],
                                                amount=200), headers=world.boss).json()["data"]
        seen = client.get(BUDGETS, headers=world.norte).json()
        assert [b["root_id"] for b in seen["data"]["items"]] == [norte["root_id"]]
        assert seen["data"]["scope"] == "warehouses"
        before = len(_rows(world.tid))
        for hidden in (company, sur):
            gone = client.patch(f"{BUDGETS}/{hidden['root_id']}",
                                json={"expected_revision": 1, "amount": 1}, headers=world.norte)
            assert gone.status_code == 404 and gone.json()["error_code"] == "purchase_budget_not_found"
            assert client.get(f"{BUDGETS}/{hidden['root_id']}/history", headers=world.norte).status_code == 404
        assert len(_rows(world.tid)) == before
        moved = client.patch(f"{BUDGETS}/{norte['root_id']}",
                             json={"expected_revision": 1, "scope_value": world.wh["Sur"]}, headers=world.norte)
        assert moved.status_code == 403 and moved.json()["error_code"] == "warehouse_out_of_scope"
        assert len(_rows(world.tid)) == before
        ok = client.patch(f"{BUDGETS}/{norte['root_id']}", json={"expected_revision": 1, "amount": 250},
                          headers=world.norte)
        assert ok.status_code == 200 and ok.json()["data"]["amount"] == 250.0

    def test_scoped_user_cannot_create_company_or_foreign_warehouse_budgets(self, client, world):
        before = len(_rows(world.tid))
        cases = [(_body(), "warehouse_scope_company_setting"),
                 (_body(scope_type="supplier", scope_value="x"), "warehouse_scope_company_setting"),
                 (_body(scope_type="warehouse", scope_value=world.wh["Sur"]), "warehouse_out_of_scope")]
        for body, code in cases:
            r = client.post(BUDGETS, json=body, headers=world.norte)
            assert r.status_code == 403, r.text
            assert r.json()["error_code"] == code
        assert len(_rows(world.tid)) == before
        mine = client.post(BUDGETS, json=_body(scope_type="warehouse", scope_value=world.wh["Norte"]),
                           headers=world.norte)
        assert mine.status_code == 201 and mine.json()["data"]["parent_root_id"] is None

    def test_status_for_a_scoped_user_never_picks_a_company_budget(self, client, world):
        client.post(BUDGETS, json=_body(amount=900), headers=world.boss)
        d = client.get(STATUS, headers=world.norte).json()["data"]
        assert d["budget"] is None and d["budgets"] == []
        missing = client.get(STATUS, params={"budget_id": "nope"}, headers=world.norte)
        assert missing.status_code == 404
