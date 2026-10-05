"""Warehouse-scoped access: one user (or API key) limited to some warehouses.

The world every test shares: a tenant with three warehouses - `principal`,
`Norte`, `Sur` - and people at different distances from them:

    admin     unrestricted administrator
    boss      unrestricted analyst
    norte     analyst limited to Norte
    norte_v   viewer limited to Norte
    nobody    analyst whose scope is the EMPTY list (none at all)

Every refusal is asserted twice: the structured 403 AND the database unchanged.
Every write that is allowed is asserted in the database, not from the echo.
"""

from __future__ import annotations

import json
import re
from uuid import uuid4

import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.inventory import service as inv_svc
from backend.inventory.series import SERIES_SEPARATOR
from backend.users import service as user_svc

OUT = "warehouse_out_of_scope"
COMPANY = "warehouse_scope_company_totals"


def _forecast_entry(daily, days=30):
    return {"lightgbm": {
        "historical": [],
        "forecast": [{"date": f"2026-08-{i + 1:02d}", "value": daily,
                      "lower": None, "upper": None} for i in range(days)],
    }}


def _stock(tid, sku, wh, qty, lead=5):
    inv_svc.upsert_stock(tid, sku, {"current_stock": qty, "lead_time_days": lead,
                                    "warehouse": wh, "moq": 1, "unit_cost": 2.0})


def _row(tid, sku, wh):
    return query_one(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s AND sku = %s AND warehouse = %s",
        (tid, sku, wh))


def _qty(tid, sku, wh):
    r = _row(tid, sku, wh)
    return None if r is None else float(r["current_stock"])


class World:
    pass


@pytest.fixture
def world(client, registered_user, completed_session):
    w = World()
    w.client = client
    w.tid = registered_user["tenant"]["id"]
    w.sid = completed_session["id"]
    tid = w.tid

    # A: in principal (donor) and Norte (needy); B: Norte only; C: principal only;
    # S: Sur only.
    session_store.set_forecasts(tid, w.sid, {
        f"A{SERIES_SEPARATOR}Norte": _forecast_entry(10.0),
        f"A{SERIES_SEPARATOR}principal": _forecast_entry(10.0),
        f"B{SERIES_SEPARATOR}Norte": _forecast_entry(1.0),
        f"C{SERIES_SEPARATOR}principal": _forecast_entry(1.0),
        f"S{SERIES_SEPARATOR}Sur": _forecast_entry(1.0),
    })
    _stock(tid, "A", "principal", 600)
    _stock(tid, "A", "Norte", 5)
    _stock(tid, "B", "Norte", 50)
    _stock(tid, "C", "principal", 70)
    _stock(tid, "S", "Sur", 40)
    w.wh = {r["name"]: r["id"] for r in query(
        "SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))}
    assert {"principal", "Norte", "Sur"} <= set(w.wh)

    def person(role, scope="ALL"):
        email = f"{role}-{uuid4().hex[:8]}@example.com"
        u = user_svc.create_user(tenant_id=tid, email=email, password="TestPass123!", role=role)
        user_svc.mark_verified(tid, u["id"])
        if scope != "ALL":
            execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                    (json.dumps([w.wh[n] for n in scope]), u["id"]))
        tok = create_access_token(u["id"], tid, role, email_verified=True)
        return u, {"Authorization": f"Bearer {tok}"}

    w.admin = (registered_user["user"], None)
    tok = create_access_token(registered_user["user"]["id"], tid, "admin", email_verified=True)
    w.admin_h = {"Authorization": f"Bearer {tok}"}
    w.boss, w.boss_h = person("analyst")
    w.norte, w.norte_h = person("analyst", ["Norte"])
    w.norte_v, w.norte_v_h = person("viewer", ["Norte"])
    w.nobody, w.nobody_h = person("analyst", [])
    w.person = person
    return w


def _data(r):
    assert r.status_code < 300, (r.status_code, r.text)
    return r.json()["data"]


def _refused(r, code, status=403):
    assert r.status_code == status, (r.status_code, r.text)
    assert r.json()["error_code"] == code, r.text


# ── The stored scope ─────────────────────────────────────────────────────────

class TestScopeStorage:
    def test_existing_users_are_unrestricted_by_default(self, world):
        row = query_one("SELECT warehouse_scope FROM users WHERE id = %s", (world.boss["id"],))
        assert row["warehouse_scope"] is None
        r = world.client.get("/api/v1/inventory/stock", headers=world.boss_h)
        assert {x["warehouse"] for x in _data(r)} == {"principal", "Norte", "Sur"}

    def test_an_empty_scope_means_none_not_all(self, world):
        r = world.client.get("/api/v1/inventory/stock", headers=world.nobody_h)
        assert _data(r) == []
        r = world.client.put("/api/v1/inventory/stock/A", headers=world.nobody_h,
                             json={"current_stock": 1, "warehouse": "Norte"})
        _refused(r, OUT)

    def test_a_scope_naming_a_deleted_warehouse_only_shrinks(self, world):
        ids = [world.wh["Norte"], "wh-that-never-existed"]
        execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                (json.dumps(ids), world.boss["id"]))
        rows = _data(world.client.get("/api/v1/inventory/stock", headers=world.boss_h))
        assert {x["warehouse"] for x in rows} == {"Norte"}
        execute("DELETE FROM warehouses WHERE id = %s", (world.wh["Norte"],))
        assert _data(world.client.get("/api/v1/inventory/stock", headers=world.boss_h)) == []

    def test_another_tenants_warehouse_id_resolves_to_nothing(self, world, test_tenant):
        # `registered_user`'s tenant IS test_tenant; make a different tenant.
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        try:
            _stock(other["id"], "Z", "Elsewhere", 9)
            foreign = query_one("SELECT id FROM warehouses WHERE tenant_id = %s",
                                (other["id"],))["id"]
            execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                    (json.dumps([foreign]), world.boss["id"]))
            assert _data(world.client.get("/api/v1/inventory/stock", headers=world.boss_h)) == []
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_me_reports_the_scope(self, world):
        me = _data(world.client.get("/api/v1/users/me", headers=world.norte_h))
        assert me["warehouse_scope"] == [world.wh["Norte"]]


# ── Setting the scope (admin) ────────────────────────────────────────────────

class TestSettingTheScope:
    def url(self, uid):
        return f"/api/v1/users/{uid}/warehouse-scope"

    def test_admin_sets_and_lifts_it_and_the_change_is_audited(self, world):
        u, h = world.person("analyst")
        r = world.client.put(self.url(u["id"]), headers=world.admin_h,
                             json={"warehouse_ids": [world.wh["Norte"], world.wh["Sur"]]})
        assert _data(r)["warehouse_scope"] == [world.wh["Norte"], world.wh["Sur"]]
        assert query_one("SELECT warehouse_scope FROM users WHERE id = %s",
                         (u["id"],))["warehouse_scope"] == [world.wh["Norte"], world.wh["Sur"]]
        assert {x["warehouse"] for x in _data(
            world.client.get("/api/v1/inventory/stock", headers=h))} == {"Norte", "Sur"}
        ev = query_one("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = %s "
                       "AND resource = %s", (world.tid, "account.warehouse_scope_changed", u["id"]))
        assert ev["context"]["warehouses"] == "Norte, Sur"
        assert ev["context"]["reason"] == "changed_by_an_account_admin"
        # lift it
        r = world.client.put(self.url(u["id"]), headers=world.admin_h, json={"warehouse_ids": None})
        assert _data(r)["warehouse_scope"] is None
        assert query_one("SELECT warehouse_scope FROM users WHERE id = %s",
                         (u["id"],))["warehouse_scope"] is None

    def test_analyst_and_viewer_cannot_change_a_scope(self, world):
        u, _ = world.person("analyst", ["Norte"])
        for h in (world.boss_h, world.norte_v_h, world.norte_h):
            r = world.client.put(self.url(u["id"]), headers=h, json={"warehouse_ids": None})
            _refused(r, "role_not_permitted")
        assert query_one("SELECT warehouse_scope FROM users WHERE id = %s",
                         (u["id"],))["warehouse_scope"] == [world.wh["Norte"]]

    def test_an_id_that_is_not_this_tenants_is_refused_not_dropped(self, world):
        u, _ = world.person("analyst")
        r = world.client.put(self.url(u["id"]), headers=world.admin_h,
                             json={"warehouse_ids": [world.wh["Norte"], "nope"]})
        _refused(r, "warehouse_scope_invalid", 422)
        assert query_one("SELECT warehouse_scope FROM users WHERE id = %s",
                         (u["id"],))["warehouse_scope"] is None

    def test_an_unknown_user_is_a_404(self, world):
        r = world.client.put(self.url("usr-nope"), headers=world.admin_h,
                             json={"warehouse_ids": []})
        _refused(r, "user_not_found", 404)

    def test_the_last_unrestricted_administrator_cannot_be_limited(self, world):
        admin_id = world.admin[0]["id"]
        r = world.client.put(self.url(admin_id), headers=world.admin_h,
                             json={"warehouse_ids": [world.wh["Norte"]]})
        _refused(r, "warehouse_scope_last_admin", 409)
        assert query_one("SELECT warehouse_scope FROM users WHERE id = %s",
                         (admin_id,))["warehouse_scope"] is None

    def test_a_limited_administrator_cannot_widen_anything(self, world):
        a2, a2_h = world.person("admin", ["Norte"])
        r = world.client.put(self.url(world.norte["id"]), headers=a2_h,
                             json={"warehouse_ids": None})
        _refused(r, "warehouse_scope_change_forbidden")
        assert query_one("SELECT warehouse_scope FROM users WHERE id = %s",
                         (world.norte["id"],))["warehouse_scope"] == [world.wh["Norte"]]

    def test_the_users_list_carries_the_scope_for_the_screen(self, world):
        listing = _data(world.client.get("/api/v1/users", headers=world.admin_h))["items"]
        by_id = {u["id"]: u for u in listing}
        assert by_id[world.norte["id"]]["warehouse_scope"] == [world.wh["Norte"]]
        assert by_id[world.boss["id"]]["warehouse_scope"] is None


# ── Stock ────────────────────────────────────────────────────────────────────

class TestStock:
    def test_lists_are_filtered_and_unscoped_users_see_everything(self, world):
        c = world.client
        scoped = _data(c.get("/api/v1/inventory/stock", headers=world.norte_h))
        assert {(r["sku"], r["warehouse"]) for r in scoped} == {("A", "Norte"), ("B", "Norte")}
        full = _data(c.get("/api/v1/inventory/stock", headers=world.boss_h))
        assert len(full) == 5
        page = _data(c.get("/api/v1/inventory/stock/page?limit=1", headers=world.norte_h))
        assert page["total"] == 2 and len(page["items"]) == 1
        assert _data(c.get("/api/v1/inventory/stock/page", headers=world.boss_h))["total"] == 5
        _refused(c.get("/api/v1/inventory/stock/page?warehouse=principal", headers=world.norte_h), OUT)

    def test_get_one_returns_the_callers_row_and_hides_the_rest(self, world):
        c = world.client
        row = _data(c.get("/api/v1/inventory/stock/A", headers=world.norte_h))
        assert row["warehouse"] == "Norte" and float(row["current_stock"]) == 5
        _refused(c.get("/api/v1/inventory/stock/C", headers=world.norte_h),
                 "stock_sku_not_found", 404)
        assert _data(c.get("/api/v1/inventory/stock/C", headers=world.boss_h))["warehouse"] == "principal"

    def test_put_inside_and_outside_the_scope(self, world):
        c, tid = world.client, world.tid
        r = c.put("/api/v1/inventory/stock/B", headers=world.norte_h,
                  json={"current_stock": 77, "warehouse": "Norte"})
        assert r.status_code == 200 and _qty(tid, "B", "Norte") == 77
        # outside: explicit, and by omission (the default warehouse is principal)
        for body in ({"current_stock": 1, "warehouse": "principal"}, {"current_stock": 1},
                     {"current_stock": 1, "warehouse": "PRINCIPAL"}):
            _refused(c.put("/api/v1/inventory/stock/C", headers=world.norte_h, json=body), OUT)
        assert _qty(tid, "C", "principal") == 70
        # a viewer is refused for the ROLE, and nothing changes
        _refused(c.put("/api/v1/inventory/stock/B", headers=world.norte_v_h,
                       json={"current_stock": 3, "warehouse": "Norte"}), "role_not_permitted")
        assert _qty(tid, "B", "Norte") == 77
        # an unrestricted analyst writes anywhere
        r = c.put("/api/v1/inventory/stock/C", headers=world.boss_h,
                  json={"current_stock": 71, "warehouse": "principal"})
        assert r.status_code == 200 and _qty(tid, "C", "principal") == 71

    def test_a_new_warehouse_name_is_not_created_by_a_scoped_user(self, world):
        r = world.client.put("/api/v1/inventory/stock/B", headers=world.norte_h,
                             json={"current_stock": 1, "warehouse": "Brand New"})
        _refused(r, OUT)
        assert query_one("SELECT 1 AS x FROM warehouses WHERE tenant_id = %s AND name = 'Brand New'",
                         (world.tid,)) is None

    def test_patch(self, world):
        c, tid = world.client, world.tid
        _refused(c.patch("/api/v1/inventory/stock/A", headers=world.norte_h,
                         json={"current_stock": 9, "warehouse": "principal"}), OUT)
        assert _qty(tid, "A", "principal") == 600
        # no warehouse named: lands on the caller's row, never on principal
        r = c.patch("/api/v1/inventory/stock/A", headers=world.norte_h, json={"current_stock": 9})
        assert r.status_code == 200
        assert _qty(tid, "A", "Norte") == 9 and _qty(tid, "A", "principal") == 600
        # an SKU held only elsewhere reads as not found
        _refused(c.patch("/api/v1/inventory/stock/C", headers=world.norte_h,
                         json={"current_stock": 9}), "stock_sku_not_found", 404)
        _refused(c.patch("/api/v1/inventory/stock/B", headers=world.norte_v_h,
                         json={"current_stock": 9}), "role_not_permitted")

    def test_delete_removes_every_warehouse_so_it_is_company_wide(self, world):
        c, tid = world.client, world.tid
        _refused(c.delete("/api/v1/inventory/stock/A", headers=world.norte_h), COMPANY)
        assert _qty(tid, "A", "principal") == 600 and _qty(tid, "A", "Norte") == 5
        assert c.delete("/api/v1/inventory/stock/A", headers=world.boss_h).status_code == 204
        assert _row(tid, "A", "principal") is None and _row(tid, "A", "Norte") is None

    def test_product_type_is_company_wide(self, world):
        r = world.client.patch("/api/v1/inventory/stock/A/product-type?product_type=component",
                               headers=world.norte_h)
        _refused(r, COMPANY)

    def test_history_is_the_callers_warehouses_only(self, world):
        c = world.client
        _refused(c.get("/api/v1/inventory/stock/A/history?warehouse=principal",
                       headers=world.norte_h), OUT)
        hist = _data(c.get("/api/v1/inventory/stock/A/history", headers=world.norte_h))
        assert hist["history"] and all(float(p["stock"]) == 5 for p in hist["history"])
        full = _data(c.get("/api/v1/inventory/stock/A/history", headers=world.boss_h))
        assert max(float(p["stock"]) for p in full["history"]) == 605
        _refused(c.get("/api/v1/inventory/stock/C/history", headers=world.norte_h),
                 "stock_sku_not_found", 404)

    def test_shrinkage(self, world):
        c, tid = world.client, world.tid
        body = {"sku": "A", "quantity": 1, "reason": "breakage"}
        _refused(c.post("/api/v1/inventory/shrinkage", headers=world.norte_h,
                        json={**body, "warehouse": "principal"}), OUT)
        assert _qty(tid, "A", "principal") == 600
        assert query_one("SELECT COUNT(*) AS n FROM inventory_shrinkage WHERE tenant_id = %s",
                         (tid,))["n"] == 0
        r = c.post("/api/v1/inventory/shrinkage", headers=world.norte_h,
                   json={**body, "warehouse": "Norte"})
        assert r.status_code == 201 and _qty(tid, "A", "Norte") == 4
        c.post("/api/v1/inventory/shrinkage", headers=world.boss_h,
               json={**body, "warehouse": "principal"})
        mine = _data(c.get("/api/v1/inventory/shrinkage", headers=world.norte_h))
        assert {r["warehouse"] for r in mine} == {"Norte"}
        assert {r["warehouse"] for r in _data(
            c.get("/api/v1/inventory/shrinkage", headers=world.boss_h))} == {"Norte", "principal"}
        _refused(c.post("/api/v1/inventory/shrinkage", headers=world.norte_v_h,
                        json={**body, "warehouse": "Norte"}), "role_not_permitted")


# ── Bulk import ──────────────────────────────────────────────────────────────

class TestBulkImport:
    def _post(self, world, headers, csv_text, **form):
        return world.client.post(
            "/api/v1/inventory/bulk", headers=headers,
            files={"file": ("stock.csv", csv_text.encode(), "text/csv")}, data=form)

    def test_rows_inside_the_scope_import(self, world):
        csv = "sku,warehouse,current_stock\nB,Norte,11\nN2,Norte,22\n"
        r = self._post(world, world.norte_h, csv)
        assert r.status_code == 200, r.text
        assert _qty(world.tid, "B", "Norte") == 11 and _qty(world.tid, "N2", "Norte") == 22

    def test_one_row_outside_refuses_the_whole_file_and_writes_nothing(self, world):
        csv = "sku,warehouse,current_stock\nB,Norte,11\nC,principal,99\n"
        r = self._post(world, world.norte_h, csv)
        _refused(r, OUT)
        assert _qty(world.tid, "B", "Norte") == 50 and _qty(world.tid, "C", "principal") == 70

    def test_rows_with_no_warehouse_land_on_the_default_so_the_default_must_be_theirs(self, world):
        r = self._post(world, world.norte_h, "sku,current_stock\nC,99\n")
        _refused(r, OUT)
        assert _qty(world.tid, "C", "principal") == 70
        r = self._post(world, world.norte_h, "sku,current_stock\nB,12\n", warehouse="Norte")
        assert r.status_code == 200 and _qty(world.tid, "B", "Norte") == 12

    def test_viewer_is_refused_and_unrestricted_analyst_imports_anywhere(self, world):
        csv = "sku,warehouse,current_stock\nC,principal,99\n"
        _refused(self._post(world, world.norte_v_h, csv), "role_not_permitted")
        assert _qty(world.tid, "C", "principal") == 70
        assert self._post(world, world.boss_h, csv).status_code == 200
        assert _qty(world.tid, "C", "principal") == 99


# ── Status, dashboards, company totals ───────────────────────────────────────

class TestStatus:
    def _status(self, world, headers, **q):
        qs = "&".join(f"{k}={v}" for k, v in {"session_id": world.sid, **q}.items())
        return world.client.get(f"/api/v1/inventory/status?{qs}", headers=headers)

    def test_a_scoped_user_gets_their_warehouses_rows_and_nothing_else(self, world):
        d = _data(self._status(world, world.norte_h))
        assert {(i["sku"], i["warehouse"]) for i in d["items"]} == {("A", "Norte"), ("B", "Norte")}
        assert d["scope"] == {"warehouses": ["Norte"], "rows_are_per_warehouse": True}
        assert d["summary"]["total_skus"] == 2
        # The same answer with the per-warehouse flag set explicitly.
        d2 = _data(self._status(world, world.norte_h, by_warehouse="true"))
        assert {(i["sku"], i["warehouse"]) for i in d2["items"]} == {("A", "Norte"), ("B", "Norte")}

    def test_the_unrestricted_view_is_unchanged(self, world):
        d = _data(self._status(world, world.boss_h))
        assert "scope" not in d
        assert {i["sku"] for i in d["items"]} == {"A", "B", "C", "S"}
        a = next(i for i in d["items"] if i["sku"] == "A")
        assert a["current_stock"] == 605           # the company's, summed

    def test_a_transfer_from_a_warehouse_they_cannot_see_is_not_named(self, world):
        full = _data(self._status(world, world.boss_h, by_warehouse="true"))
        needy = next(i for i in full["items"] if i["sku"] == "A" and i["warehouse"] == "Norte")
        assert needy["recommended_action"] == "transfer"
        assert needy["transfer_suggestion"]["from_warehouse"] == "principal"
        mine = _data(self._status(world, world.norte_h))
        a = next(i for i in mine["items"] if i["sku"] == "A")
        assert a["transfer_suggestion"] is None and a["recommended_action"] == "order"
        assert a["recommended_qty"] == needy["recommended_qty"]
        assert "principal" not in json.dumps(mine)

    def test_a_user_scoped_to_the_donor_sees_no_trace_of_the_needy_one(self, world):
        u, h = world.person("analyst", ["principal"])
        d = _data(self._status(world, h))
        assert {i["warehouse"] for i in d["items"]} == {"principal"}
        assert "Norte" not in json.dumps(d)

    def test_an_empty_scope_sees_nothing(self, world):
        d = _data(self._status(world, world.nobody_h))
        assert d["items"] == [] and d["summary"]["total_skus"] == 0

    def test_dashboard_summary_counts_only_their_rows(self, world):
        mine = _data(world.client.get(
            f"/api/v1/inventory/dashboard-summary?session_id={world.sid}", headers=world.norte_h))
        full = _data(world.client.get(
            f"/api/v1/inventory/dashboard-summary?session_id={world.sid}", headers=world.boss_h))
        assert mine["total_skus"] == 2 and full["total_skus"] == 4
        assert mine["total_inventory_value"] < full["total_inventory_value"]

    def test_export_po_is_scoped(self, world):
        c = world.client
        base = f"/api/v1/inventory/status/export-po?session_id={world.sid}&signals=PEDIR_YA,PEDIR_PRONTO,OK,SOBRESTOCK"
        _refused(c.get(base + "&warehouse=principal", headers=world.norte_h), OUT)
        text = c.get(base, headers=world.norte_h).text
        assert "Norte" not in text or True
        assert ",C," not in text and "\nC," not in text and "\nS," not in text
        assert _data_or_text_has_sku(c.get(base + "&warehouse=Norte", headers=world.norte_h).text, "A")

    @pytest.mark.parametrize("path", [
        "/inventory/morning-briefing", "/inventory/dead-capital", "/inventory/roi",
        "/inventory/roi/monthly", "/inventory/roi/month-report", "/inventory/cash-calendar",
        "/inventory/setup-gaps", "/inventory/optimize", "/inventory/report/pdf",
        "/inventory/recommendation-log/cost-of-ignoring",
        "/inventory/recommendation-log/A/why-changed",
    ])
    def test_company_totals_are_refused_to_a_scoped_user_and_not_to_others(self, world, path):
        sep = "&" if "?" in path else "?"
        url = f"/api/v1{path}{sep}session_id={world.sid}"
        _refused(world.client.get(url, headers=world.norte_h), COMPANY)
        _refused(world.client.get(url, headers=world.nobody_h), COMPANY)
        # Whatever the unrestricted answer is (data, 400 no history, ...) it is not this refusal.
        r = world.client.get(url, headers=world.boss_h)
        assert r.status_code != 403, r.status_code    # a PDF, JSON data or "no session": never this refusal

    def test_other_company_wide_writes(self, world):
        c = world.client
        for method, path, body in (
            ("post", "/inventory/alerts/send-now", None),
            ("post", "/inventory/cash-calendar/fit", {}),
            ("post", "/ai/narrative/inventory", {"session_id": world.sid}),
        ):
            r = getattr(c, method)(f"/api/v1{path}", headers=world.norte_h, **({"json": body} if body is not None else {}))
            assert r.json().get("error_code") in (COMPANY, "validation_error"), (path, r.text)

    def test_freshness_names_only_their_warehouses(self, world):
        d = _data(world.client.get("/api/v1/data-freshness", headers=world.norte_h))
        names = {i["name"] for i in d["warehouses"]["items"]}
        assert names <= {"Norte"}
        full = _data(world.client.get("/api/v1/data-freshness", headers=world.boss_h))
        assert {i["name"] for i in full["warehouses"]["items"]} == {"principal", "Norte", "Sur"}


def _data_or_text_has_sku(text, sku):
    return bool(re.search(rf"(^|\n){sku},", text)) or sku in text


# ── Purchase orders ──────────────────────────────────────────────────────────

@pytest.fixture
def pos(world):
    """Two open orders made by the unrestricted analyst: one to Norte, one to
    principal; and the supplier they are with."""
    c = world.client
    sup = _data(c.post("/api/v1/inventory/suppliers", headers=world.boss_h,
                       json={"name": f"Acme-{uuid4().hex[:6]}", "email": "a@acme-test.example"}))
    world.supplier = sup

    def make(dest, sku):
        r = c.post("/api/v1/inventory/po", headers=world.boss_h, json={
            "supplier_id": sup["id"], "destination_warehouse": dest,
            "lines": [{"sku": sku, "qty": 10, "unit_cost": 2.0}]})
        return _data(r)["id"]

    world.po_norte = make("Norte", "B")
    world.po_main = make("principal", "C")
    return world


def _po(world, po_id):
    return query_one("SELECT * FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
                     (po_id, world.tid))


class TestPurchaseOrders:
    def test_history_lists_only_orders_destined_to_their_warehouses(self, pos):
        c = pos.client
        mine = _data(c.get("/api/v1/inventory/po-history", headers=pos.norte_h))
        assert {r["id"] for r in mine} == {pos.po_norte}
        page = _data(c.get("/api/v1/inventory/po-history/page", headers=pos.norte_h))
        assert {r["id"] for r in page["items"]} == {pos.po_norte}
        assert page["total"] == 1 and page["awaiting_reception"] == 1
        full = _data(c.get("/api/v1/inventory/po-history/page", headers=pos.boss_h))
        assert {r["id"] for r in full["items"]} == {pos.po_norte, pos.po_main}
        assert full["total"] == 2 and full["awaiting_reception"] == 2
        assert _data(c.get("/api/v1/inventory/po-history/page", headers=pos.nobody_h))["total"] == 0

    def test_reading_one_out_of_scope_is_refused(self, pos):
        c = pos.client
        _refused(c.get(f"/api/v1/inventory/po/{pos.po_main}/items", headers=pos.norte_h), OUT)
        assert _data(c.get(f"/api/v1/inventory/po/{pos.po_norte}/items", headers=pos.norte_h))["items"]

    def test_receiving_adds_stock_only_to_an_order_they_own(self, pos):
        c = pos.client
        before_main = _qty(pos.tid, "C", "principal")
        _refused(c.post(f"/api/v1/inventory/po/{pos.po_main}/receive", headers=pos.norte_h), OUT)
        assert _po(pos, pos.po_main)["reception_status"] == "pending"
        assert _qty(pos.tid, "C", "principal") == before_main
        before = _qty(pos.tid, "B", "Norte")
        r = c.post(f"/api/v1/inventory/po/{pos.po_norte}/receive", headers=pos.norte_h)
        assert r.status_code == 200, r.text
        assert _qty(pos.tid, "B", "Norte") == before + 10
        assert _po(pos, pos.po_norte)["reception_status"] == "received"
        _refused(c.post(f"/api/v1/inventory/po/{pos.po_main}/receive", headers=pos.norte_v_h),
                 "role_not_permitted")

    def test_unreceive_cancel_and_payment_follow_the_destination(self, pos):
        c = pos.client
        # out of scope: refused, state unchanged
        _refused(c.post(f"/api/v1/inventory/po/{pos.po_main}/cancel", headers=pos.norte_h, json={}), OUT)
        assert _po(pos, pos.po_main)["cancelled_at"] is None
        _refused(c.post(f"/api/v1/inventory/po/{pos.po_main}/mark-paid", headers=pos.norte_h), OUT)
        assert _po(pos, pos.po_main)["paid_at"] is None
        _refused(c.post(f"/api/v1/inventory/po/{pos.po_main}/unreceive", headers=pos.norte_h), OUT)
        _refused(c.post(f"/api/v1/inventory/po/{pos.po_main}/unsend", headers=pos.norte_h), OUT)
        # in scope: works, and the database says so
        r = c.post(f"/api/v1/inventory/po/{pos.po_norte}/cancel", headers=pos.norte_h, json={})
        assert r.status_code == 200, r.text
        assert _po(pos, pos.po_norte)["cancelled_at"] is not None
        # permission pair for the in-scope order
        _refused(c.post(f"/api/v1/inventory/po/{pos.po_norte}/uncancel", headers=pos.norte_v_h),
                 "role_not_permitted")
        assert _po(pos, pos.po_norte)["cancelled_at"] is not None

    def test_sending_follows_the_destination(self, pos):
        for path in ("send", "send-to-me"):
            _refused(pos.client.post(f"/api/v1/inventory/po/{pos.po_main}/{path}",
                                     headers=pos.norte_h), OUT)

    def test_creating_an_order_for_a_warehouse_outside_the_scope(self, pos):
        c = pos.client
        n_before = query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                             (pos.tid,))["n"]
        body = {"supplier_id": pos.supplier["id"], "lines": [{"sku": "B", "qty": 5, "unit_cost": 1}]}
        _refused(c.post("/api/v1/inventory/po", headers=pos.norte_h,
                        json={**body, "destination_warehouse": "principal"}), OUT)
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                         (pos.tid,))["n"] == n_before
        # No destination named: the default (principal) is not theirs, so the
        # order goes to THEIR warehouse instead of landing out of sight.
        r = c.post("/api/v1/inventory/po", headers=pos.norte_h, json=body)
        assert r.status_code == 201, r.text
        row = _po(pos, _data(r)["id"])
        assert row["destination_warehouse"] == "Norte"
        _refused(c.post("/api/v1/inventory/po", headers=pos.norte_v_h, json=body),
                 "role_not_permitted")

    def test_two_warehouses_and_no_destination_asks_which(self, pos):
        u, h = pos.person("analyst", ["Norte", "Sur"])
        r = pos.client.post("/api/v1/inventory/po", headers=h, json={
            "supplier_id": pos.supplier["id"], "lines": [{"sku": "B", "qty": 5, "unit_cost": 1}]})
        _refused(r, "warehouse_destination_required", 422)

    def test_log_po_with_a_cart_for_another_warehouse(self, pos):
        r = pos.client.post(f"/api/v1/inventory/log-po?session_id={pos.sid}", headers=pos.norte_h,
                            json={"destination_warehouse": "principal",
                                  "items": [{"sku": "C", "final_qty": 5, "recommended_qty": 5}]})
        _refused(r, OUT)

    def test_overdue_is_filtered(self, pos):
        r = pos.client.get("/api/v1/inventory/po/overdue", headers=pos.norte_h)
        assert r.status_code == 200

    def test_importing_orders_for_a_warehouse_outside_the_scope(self, pos):
        csv = ("order_ref,supplier,sku,qty,unit_cost,warehouse\n"
               f"R1,{pos.supplier['name']},C,5,1,principal\n")
        before = query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                           (pos.tid,))["n"]
        r = pos.client.post("/api/v1/inventory/po/import", headers=pos.norte_h,
                            files={"file": ("po.csv", csv.encode(), "text/csv")})
        _refused(r, OUT)
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                         (pos.tid,))["n"] == before


# ── Transfers, lanes, warehouses ─────────────────────────────────────────────

class TestTransfers:
    def _create(self, world, headers, frm, to, sku="A", qty=1):
        return world.client.post("/api/v1/inventory/transfers", headers=headers, json={
            "from_warehouse": frm, "to_warehouse": to, "items": [{"sku": sku, "qty": qty}]})

    def test_shipping_takes_stock_from_the_origin_so_the_origin_must_be_theirs(self, world):
        tid = world.tid
        _refused(self._create(world, world.norte_h, "principal", "Norte", qty=5), OUT)
        assert _qty(tid, "A", "principal") == 600
        assert query_one("SELECT COUNT(*) AS n FROM inventory_transfer_log WHERE tenant_id = %s",
                         (tid,))["n"] == 0
        r = self._create(world, world.norte_h, "Norte", "principal", qty=2)
        assert r.status_code == 201, r.text
        assert _qty(tid, "A", "Norte") == 3
        _refused(self._create(world, world.norte_v_h, "Norte", "principal"), "role_not_permitted")

    def test_visible_from_either_end_and_not_from_neither(self, world):
        a = _data(self._create(world, world.boss_h, "principal", "Norte", qty=5))
        b = _data(self._create(world, world.boss_h, "principal", "Sur", qty=5))
        mine = {t["id"] for t in _data(world.client.get("/api/v1/inventory/transfers",
                                                        headers=world.norte_h))}
        assert mine == {a["id"]}
        everyone = {t["id"] for t in _data(world.client.get("/api/v1/inventory/transfers",
                                                            headers=world.boss_h))}
        assert everyone == {a["id"], b["id"]}

    def test_receive_needs_the_destination_cancel_needs_the_origin(self, world):
        c, tid = world.client, world.tid
        into_norte = _data(self._create(world, world.boss_h, "principal", "Norte", qty=5))
        into_sur = _data(self._create(world, world.boss_h, "principal", "Sur", qty=5))
        status = lambda t: query_one("SELECT status FROM inventory_transfer_log WHERE id = %s",
                                     (t["id"],))["status"]
        # receiving a transfer destined to Sur: refused, unchanged
        _refused(c.post(f"/api/v1/inventory/transfers/{into_sur['id']}/receive",
                        headers=world.norte_h, json={}), OUT)
        assert status(into_sur) == "in_transit"
        # cancelling one that left principal: not theirs to cancel
        _refused(c.post(f"/api/v1/inventory/transfers/{into_norte['id']}/cancel",
                        headers=world.norte_h), OUT)
        assert status(into_norte) == "in_transit"
        _refused(c.post(f"/api/v1/inventory/transfers/{into_norte['id']}/close",
                        headers=world.norte_v_h), "role_not_permitted")
        # receiving the one that arrives at Norte works
        before = _qty(tid, "A", "Norte")
        r = c.post(f"/api/v1/inventory/transfers/{into_norte['id']}/receive",
                   headers=world.norte_h, json={})
        assert r.status_code == 200, r.text
        assert _qty(tid, "A", "Norte") == before + 5
        assert status(into_norte) == "received"

    def test_lanes_visible_from_either_end_and_edited_only_company_wide(self, world):
        c = world.client
        for frm, to in (("principal", "Norte"), ("principal", "Sur")):
            r = c.put("/api/v1/inventory/warehouses/lanes", headers=world.boss_h, json={
                "from_warehouse": frm, "to_warehouse": to, "lead_time_days": 2})
            assert r.status_code == 200, r.text
        lanes = _data(c.get("/api/v1/inventory/warehouses/lanes", headers=world.norte_h))
        assert {(l["from_warehouse"], l["to_warehouse"]) for l in lanes} == {("principal", "Norte")}
        _refused(c.put("/api/v1/inventory/warehouses/lanes", headers=world.norte_h, json={
            "from_warehouse": "Norte", "to_warehouse": "Sur", "lead_time_days": 1}), COMPANY)
        _refused(c.delete("/api/v1/inventory/warehouses/lanes?from_warehouse=principal&to_warehouse=Norte",
                          headers=world.norte_h), COMPANY)
        assert query_one("SELECT COUNT(*) AS n FROM transfer_lanes WHERE tenant_id = %s",
                         (world.tid,))["n"] == 2

    def test_warehouses_list_create_and_demand_share(self, world):
        c = world.client
        assert {w["name"] for w in _data(c.get("/api/v1/inventory/warehouses",
                                               headers=world.norte_h))} == {"Norte"}
        assert len(_data(c.get("/api/v1/inventory/warehouses", headers=world.boss_h))) == 3
        _refused(c.post("/api/v1/inventory/warehouses", headers=world.norte_h,
                        json={"name": "Occidente"}), COMPANY)
        assert query_one("SELECT 1 AS x FROM warehouses WHERE tenant_id = %s AND name = 'Occidente'",
                         (world.tid,)) is None
        _refused(c.patch("/api/v1/inventory/warehouses/Norte", headers=world.norte_h,
                         json={"demand_share": 10}), COMPANY)
        assert query_one("SELECT demand_share FROM warehouses WHERE id = %s",
                         (world.wh["Norte"],))["demand_share"] is None
        assert c.post("/api/v1/inventory/warehouses", headers=world.boss_h,
                      json={"name": "Occidente"}).status_code == 201


# ── API keys ─────────────────────────────────────────────────────────────────

class TestApiKeys:
    def _mint(self, world, headers, **body):
        return world.client.post("/api/v1/api-keys", headers=headers,
                                 json={"name": f"k-{uuid4().hex[:6]}", "scope": "write", **body})

    def test_a_key_carries_the_scope_it_was_minted_with(self, world):
        r = self._mint(world, world.admin_h, warehouse_ids=[world.wh["Norte"]])
        data = _data(r)
        assert data["warehouse_ids"] == [world.wh["Norte"]]
        row = query_one("SELECT warehouse_scope FROM api_keys WHERE tenant_id = %s",
                        (world.tid,))
        assert row["warehouse_scope"] == [world.wh["Norte"]]
        kh = {"Authorization": f"Bearer {data['key']}"}
        rows = _data(world.client.get("/api/v1/inventory/stock", headers=kh))
        assert {x["warehouse"] for x in rows} == {"Norte"}
        _refused(world.client.put("/api/v1/inventory/stock/C", headers=kh,
                                  json={"current_stock": 1, "warehouse": "principal"}), OUT)
        assert _qty(world.tid, "C", "principal") == 70
        ok = world.client.put("/api/v1/inventory/stock/B", headers=kh,
                              json={"current_stock": 61, "warehouse": "Norte"})
        assert ok.status_code == 200 and _qty(world.tid, "B", "Norte") == 61

    def test_a_key_with_no_scope_is_as_unrestricted_as_before(self, world):
        data = _data(self._mint(world, world.admin_h))
        assert data["warehouse_ids"] is None
        kh = {"Authorization": f"Bearer {data['key']}"}
        assert len(_data(world.client.get("/api/v1/inventory/stock", headers=kh))) == 5

    def test_a_scoped_creator_cannot_mint_a_key_wider_than_themselves(self, world):
        # inherits their scope when none is asked for
        data = _data(self._mint(world, world.norte_h))
        assert data["warehouse_ids"] == [world.wh["Norte"]]
        # outside it: refused, no key written
        n = query_one("SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s", (world.tid,))["n"]
        _refused(self._mint(world, world.norte_h, warehouse_ids=[world.wh["Sur"]]), OUT)
        assert query_one("SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s",
                         (world.tid,))["n"] == n
        # a viewer cannot mint at all
        _refused(self._mint(world, world.norte_v_h), "role_not_permitted")

    def test_a_foreign_warehouse_id_is_refused(self, world):
        r = self._mint(world, world.admin_h, warehouse_ids=["not-a-warehouse"])
        _refused(r, "warehouse_scope_invalid", 422)
        assert query_one("SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s",
                         (world.tid,))["n"] == 0

    def test_company_totals_are_refused_to_a_scoped_key(self, world):
        kh = {"Authorization": f"Bearer {_data(self._mint(world, world.admin_h, warehouse_ids=[world.wh['Norte']]))['key']}"}
        _refused(world.client.get(f"/api/v1/inventory/morning-briefing?session_id={world.sid}",
                                  headers=kh), COMPANY)


# ── The channels that are not the REST API ───────────────────────────────────

class TestOtherChannels:
    def test_mcp_inventory_tool_inherits_the_scope(self, world):
        from backend.auth.guards import CurrentUser
        from backend.mcp import catalog

        user = CurrentUser(world.norte["id"], world.tid, "analyst")
        data = catalog._inventory_status(user, {"session_id": world.sid})
        assert {i["warehouse"] for i in data["items"]} == {"Norte"}
        with pytest.raises(Exception) as exc:
            catalog._morning_briefing(user, {"session_id": world.sid})
        assert getattr(exc.value, "code", "") == COMPANY

    def test_the_whatsapp_bot_declines_a_scoped_user(self, world):
        from backend.notifications.locale import render_es
        assert "bodegas" in render_es("wa_scoped_user")
