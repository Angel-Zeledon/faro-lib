"""
Permission-pair + cross-tenant audit for the newer inventory endpoints:
MILP optimizer, warehouses, and the event simulator (create/patch/delete/
multipliers).

This file only ADDS coverage the audit found missing. Already covered
elsewhere (not duplicated here):
  - test_warehouses.py::TestWarehouseEndpoints — viewer-denied/analyst-success
    + DB assertions for POST/GET /inventory/warehouses (same-tenant only).
  - test_optimizer_endpoint.py — viewer can read, unauthenticated rejected,
    real order/transfer recommendations (same-tenant only).
  - test_edge_cases.py::TestInventoryMutationPermissions — viewer-denied/
    analyst-success + DB-unchanged assertions for POST/PATCH/DELETE
    /inventory/events (same-tenant only).
  - test_event_multipliers.py::TestMultiplierEndpoints — viewer-denied/
    analyst-success + DB assertions for PUT/DELETE
    /inventory/events/{id}/multipliers (same-tenant only).

What none of the above check: that tenant B cannot read, leak into, or
mutate tenant A's warehouses, optimizer inputs, events, or event
multipliers. That is the gap this file closes.
"""
from uuid import uuid4

from backend.db.connection import query, query_one


def _warehouse_name():
    return f"AUDIT_WH_{uuid4().hex[:8]}"


# ── Warehouses: cross-tenant isolation ───────────────────────────────────────

class TestWarehouseCrossTenant:
    def test_list_does_not_leak_other_tenants_warehouse(
        self, client, analyst_headers, test_tenant, make_tenant_user_headers,
    ):
        name = _warehouse_name()
        create = client.post(
            "/api/v1/inventory/warehouses",
            json={"name": name},
            headers=analyst_headers,
        )
        assert create.status_code == 201

        other_headers = make_tenant_user_headers(role="analyst")
        listed = client.get("/api/v1/inventory/warehouses", headers=other_headers)
        assert listed.status_code == 200
        names = [w["name"] for w in listed.json()["data"]]
        assert name not in names

    def test_create_in_one_tenant_does_not_create_row_for_another(
        self, client, analyst_headers, test_tenant, make_tenant_user_headers,
    ):
        """Same warehouse name created independently by two tenants must
        produce two distinct rows (scoped by tenant_id), not a shared one."""
        name = _warehouse_name()
        other_headers, other_tid = make_tenant_user_headers(role="analyst", return_tenant_id=True)

        r1 = client.post("/api/v1/inventory/warehouses", json={"name": name}, headers=analyst_headers)
        r2 = client.post("/api/v1/inventory/warehouses", json={"name": name}, headers=other_headers)
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["data"]["id"] != r2.json()["data"]["id"]

        rows = query("SELECT tenant_id FROM warehouses WHERE name = %s", (name,))
        tenant_ids = {r["tenant_id"] for r in rows}
        assert tenant_ids == {test_tenant["id"], other_tid}


# ── MILP optimizer: cross-tenant isolation ──────────────────────────────────

class TestOptimizerCrossTenant:
    def test_other_tenant_cannot_pull_data_via_foreign_session_id(
        self, client, auth_headers, test_tenant, test_session, make_tenant_user_headers,
    ):
        """
        Tenant B calling /inventory/optimize with tenant A's session_id must
        not see tenant A's forecasts/stock. build_optimization_input scopes
        both the forecasts (session_store, tenant+session) and the stock
        rows (list_stock, tenant) by the CALLING user's tenant_id, so a
        foreign session_id should simply yield no data rather than leaking it.
        """
        from backend.inventory import service as inv_svc
        from backend.db import session_store

        tid = test_tenant["id"]
        sid = test_session["id"]
        sku = f"OPTAUDIT_{uuid4().hex[:8]}"

        inv_svc.upsert_stock(tid, sku, {
            "current_stock": 0, "lead_time_days": 0, "unit_cost": 5.0, "warehouse": "principal",
        })
        session_store.set_forecasts(tid, sid, {
            sku: {"lightgbm": {"forecast": [{"date": "2026-01-01", "value": 10.0}] * 7}},
        })

        # Sanity check: the owning tenant DOES get a real recommendation.
        own_resp = client.get(
            "/api/v1/inventory/optimize",
            params={"session_id": sid, "horizon_days": 7},
            headers=auth_headers,
        )
        assert own_resp.status_code == 200
        own_orders = own_resp.json()["data"]["orders"]
        assert any(o["sku"] == sku for o in own_orders)

        other_headers = make_tenant_user_headers(role="analyst")
        foreign_resp = client.get(
            "/api/v1/inventory/optimize",
            params={"session_id": sid, "horizon_days": 7},
            headers=other_headers,
        )
        assert foreign_resp.status_code == 200
        data = foreign_resp.json()["data"]
        assert not any(o["sku"] == sku for o in data["orders"]), \
            "tenant B must not receive an order recommendation derived from tenant A's data"
        assert data["orders"] == []
        assert data["transfers"] == []


# ── Events: cross-tenant isolation ──────────────────────────────────────────

class TestEventCrossTenant:
    def _create_event(self, client, headers, name_suffix=""):
        r = client.post(
            "/api/v1/inventory/events",
            json={
                "name": f"audit-event-{name_suffix}{uuid4().hex[:6]}",
                "start_date": "2026-12-01",
                "end_date": "2026-12-10",
                "multiplier": 1.5,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        return r.json()["data"]

    def test_list_does_not_leak_other_tenants_event(
        self, client, analyst_headers, make_tenant_user_headers,
    ):
        ev = self._create_event(client, analyst_headers)
        other_headers = make_tenant_user_headers(role="viewer")
        listed = client.get("/api/v1/inventory/events", headers=other_headers)
        assert listed.status_code == 200
        ids = [e["id"] for e in listed.json()["data"]]
        assert ev["id"] not in ids

    def test_patch_other_tenants_event_404_and_unchanged(
        self, client, analyst_headers, make_tenant_user_headers,
    ):
        ev = self._create_event(client, analyst_headers)
        other_headers = make_tenant_user_headers(role="analyst")

        resp = client.patch(
            f"/api/v1/inventory/events/{ev['id']}",
            json={"multiplier": 9.0},
            headers=other_headers,
        )
        assert resp.status_code == 404

        row = query_one(
            "SELECT multiplier FROM inventory_events WHERE id = %s", (ev["id"],),
        )
        assert float(row["multiplier"]) == 1.5, \
            "a foreign-tenant PATCH must not alter another tenant's event"

    def test_delete_other_tenants_event_leaves_row_intact(
        self, client, analyst_headers, make_tenant_user_headers,
    ):
        ev = self._create_event(client, analyst_headers)
        other_headers = make_tenant_user_headers(role="analyst")

        resp = client.delete(
            f"/api/v1/inventory/events/{ev['id']}", headers=other_headers,
        )
        # delete_event's DELETE is tenant-scoped in its WHERE clause and does
        # not check existence first, so it reports success (204) even though
        # it deleted 0 rows for the wrong tenant — assert the row itself is
        # what actually matters.
        assert resp.status_code == 204
        assert query_one(
            "SELECT id FROM inventory_events WHERE id = %s", (ev["id"],),
        ) is not None, "a foreign-tenant DELETE must not remove another tenant's event"

        client.delete(f"/api/v1/inventory/events/{ev['id']}", headers=analyst_headers)

    def test_multipliers_list_on_other_tenants_event_404(
        self, client, analyst_headers, make_tenant_user_headers,
    ):
        ev = self._create_event(client, analyst_headers)
        other_headers = make_tenant_user_headers(role="analyst")

        resp = client.get(
            f"/api/v1/inventory/events/{ev['id']}/multipliers", headers=other_headers,
        )
        assert resp.status_code == 404

    def test_multiplier_upsert_on_other_tenants_event_404_and_no_row_created(
        self, client, analyst_headers, make_tenant_user_headers,
    ):
        ev = self._create_event(client, analyst_headers)
        other_headers = make_tenant_user_headers(role="analyst")

        resp = client.put(
            f"/api/v1/inventory/events/{ev['id']}/multipliers",
            json={"scope": "sku", "scope_value": "X", "multiplier": 2.0},
            headers=other_headers,
        )
        assert resp.status_code == 404

        rows = query(
            "SELECT id FROM inventory_event_multipliers WHERE event_id = %s", (ev["id"],),
        )
        assert rows == [], \
            "a foreign-tenant multiplier upsert must not create a row against another tenant's event"

    def test_simulate_other_tenants_event_404(
        self, client, analyst_headers, make_tenant_user_headers,
    ):
        ev = self._create_event(client, analyst_headers)
        other_headers = make_tenant_user_headers(role="viewer")

        resp = client.post(
            "/api/v1/inventory/events/simulate",
            json={"session_id": "sess_x", "event_id": ev["id"]},
            headers=other_headers,
        )
        assert resp.status_code == 404
