"""Customer commitments under warehouse scope.

Before this, `GET /committed-demand` listed every warehouse's commitments
(customer names, quantities) to a user limited to one warehouse, a scoped
analyst could create or move commitments into any warehouse of the tenant, and
the at-risk verdict a scoped user read was computed from the WHOLE company's
stock. The assistant's `list_committed_demand` tool reads the same list.

The rule now: a scoped user sees, enters and changes only commitments that name
one of their warehouses; an unassigned (company-wide) commitment is for
company-wide users only; and the verdict a scoped user reads is computed from
their warehouses' stock alone. Every refusal also asserts the table unchanged.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.auth.guards import CurrentUser
from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query, query_one
from backend.inventory import committed_demand_service as svc
from backend.inventory import service as inv_svc
from backend.users import service as user_svc

URL = "/api/v1/committed-demand"


def _stock(tid, sku, wh, qty):
    inv_svc.upsert_stock(tid, sku, {"current_stock": qty, "lead_time_days": 10,
                                    "warehouse": wh, "moq": 1})


class World:
    pass


@pytest.fixture
def world(client, registered_user):
    w = World()
    w.client = client
    w.tid = tid = registered_user["tenant"]["id"]
    w.sku = f"CDS-{uuid4().hex[:6]}"
    _stock(tid, w.sku, "principal", 500)
    _stock(tid, w.sku, "Norte", 30)
    w.wh = {r["name"]: r["id"] for r in query(
        "SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))}
    assert {"principal", "Norte"} <= set(w.wh)

    def person(role, scope=None):
        email = f"{role}-{uuid4().hex[:8]}@example.com"
        u = user_svc.create_user(tenant_id=tid, email=email, password="TestPass123!", role=role)
        user_svc.mark_verified(tid, u["id"])
        if scope is not None:
            execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                    (json.dumps([w.wh[n] for n in scope]), u["id"]))
        tok = create_access_token(u["id"], tid, role, email_verified=True)
        return u, {"Authorization": f"Bearer {tok}"}

    w.boss, w.boss_h = person("analyst")
    w.norte, w.norte_h = person("analyst", ["Norte"])
    w.norte_v, w.norte_v_h = person("viewer", ["Norte"])

    due = date.today() + timedelta(days=30)
    w.c_norte = svc.create(tid, "u1", sku=w.sku, delivery_date=due, quantity=100,
                           customer="Norte Corp", warehouse_id=w.wh["Norte"])
    w.c_principal = svc.create(tid, "u1", sku=w.sku, delivery_date=due, quantity=100,
                               customer="Principal Corp", warehouse_id=w.wh["principal"])
    w.c_company = svc.create(tid, "u1", sku=w.sku, delivery_date=due, quantity=100,
                             customer="Company Corp")
    return w


def _count(tid):
    return query_one("SELECT COUNT(*) AS n FROM committed_demand WHERE tenant_id = %s",
                     (tid,))["n"]


def _body(w, **over):
    b = {"sku": w.sku, "delivery_date": (date.today() + timedelta(days=40)).isoformat(),
         "quantity": 5, "customer": "New"}
    b.update(over)
    return b


class TestList:
    def test_company_wide_user_sees_everything(self, world):
        r = world.client.get(URL, headers=world.boss_h)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert {i["id"] for i in data["items"]} == {
            world.c_norte["id"], world.c_principal["id"], world.c_company["id"]}
        assert data["scope"] == "company"

    def test_scoped_user_sees_only_their_warehouse(self, world):
        for headers in (world.norte_h, world.norte_v_h):
            data = world.client.get(URL, headers=headers).json()["data"]
            assert [i["id"] for i in data["items"]] == [world.c_norte["id"]]
            assert {g["customer"] for g in data["by_customer"]} == {"Norte Corp"}
            assert data["scope"] == "warehouses"

    def test_empty_scope_sees_nothing(self, world):
        execute("UPDATE users SET warehouse_scope = '[]'::jsonb WHERE id = %s", (world.norte["id"],))
        data = world.client.get(URL, headers=world.norte_h).json()["data"]
        assert data["items"] == [] and data["by_customer"] == []

    def test_scoped_verdict_uses_only_their_stock(self, world):
        """Norte holds 30; the company holds 530. The Norte commitment of 100 is
        short by 70 for the Norte user — not covered by principal's 500."""
        item = world.client.get(URL, headers=world.norte_h).json()["data"]["items"][0]
        assert item["at_risk"] is True
        assert item["shortfall"] == 70.0

        company = {i["id"]: i for i in
                   world.client.get(URL, headers=world.boss_h).json()["data"]["items"]}
        # Company-wide: 300 committed against 530 on hand — nothing at risk.
        assert company[world.c_norte["id"]]["at_risk"] is False

    def test_assistant_tool_inherits_the_scope(self, world):
        from backend.assistant.account import AccountData
        from backend.assistant.tools import list_committed_demand
        data = AccountData(CurrentUser(world.norte["id"], world.tid, "analyst"))
        out = list_committed_demand(data, {})
        assert out["total_matching"] == 1
        assert [i["customer"] for i in out["items"]] == ["Norte Corp"]
        assert out["items"][0]["shortfall"] == 70.0


class TestCreate:
    def test_scoped_analyst_creates_in_own_warehouse(self, world):
        before = _count(world.tid)
        r = world.client.post(URL, json=_body(world, warehouse_id=world.wh["Norte"]),
                              headers=world.norte_h)
        assert r.status_code == 201, r.text
        row = query_one("SELECT warehouse_id, created_by FROM committed_demand WHERE id = %s",
                        (r.json()["data"]["id"],))
        assert row == {"warehouse_id": world.wh["Norte"], "created_by": world.norte["id"]}
        assert _count(world.tid) == before + 1

    def test_scoped_analyst_refused_in_another_warehouse(self, world):
        before = _count(world.tid)
        r = world.client.post(URL, json=_body(world, warehouse_id=world.wh["principal"]),
                              headers=world.norte_h)
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "warehouse_out_of_scope"
        assert r.json()["error_params"] == {"warehouse": "principal"}
        assert _count(world.tid) == before

    def test_scoped_analyst_refused_without_a_warehouse(self, world):
        before = _count(world.tid)
        r = world.client.post(URL, json=_body(world), headers=world.norte_h)
        assert r.status_code == 422, r.text
        assert r.json()["error_code"] == "committed_demand_warehouse_required"
        assert _count(world.tid) == before

    def test_scoped_viewer_refused(self, world):
        before = _count(world.tid)
        r = world.client.post(URL, json=_body(world, warehouse_id=world.wh["Norte"]),
                              headers=world.norte_v_h)
        assert r.status_code == 403
        assert _count(world.tid) == before

    def test_company_wide_user_may_leave_it_unassigned(self, world):
        r = world.client.post(URL, json=_body(world), headers=world.boss_h)
        assert r.status_code == 201, r.text
        row = query_one("SELECT warehouse_id FROM committed_demand WHERE id = %s",
                        (r.json()["data"]["id"],))
        assert row["warehouse_id"] is None

    def test_bulk_with_one_foreign_row_writes_nothing(self, world):
        before = _count(world.tid)
        rows = [_body(world, warehouse_id=world.wh["Norte"]),
                _body(world, warehouse_id=world.wh["principal"])]
        r = world.client.post(f"{URL}/bulk", json={"rows": rows}, headers=world.norte_h)
        assert r.status_code == 403, r.text
        assert _count(world.tid) == before

    def test_bulk_inside_scope_writes_all(self, world):
        before = _count(world.tid)
        rows = [_body(world, warehouse_id=world.wh["Norte"]) for _ in range(3)]
        r = world.client.post(f"{URL}/bulk", json={"rows": rows}, headers=world.norte_h)
        assert r.status_code == 201, r.text
        assert _count(world.tid) == before + 3


class TestChange:
    def test_cannot_edit_a_commitment_outside_scope(self, world):
        r = world.client.patch(f"{URL}/{world.c_principal['id']}", json={"quantity": 1},
                               headers=world.norte_h)
        assert r.status_code == 404
        assert r.json()["error_code"] == "committed_demand_not_found"
        assert query_one("SELECT quantity FROM committed_demand WHERE id = %s",
                         (world.c_principal["id"],))["quantity"] == 100

    def test_cannot_edit_an_unassigned_commitment(self, world):
        r = world.client.patch(f"{URL}/{world.c_company['id']}", json={"quantity": 1},
                               headers=world.norte_h)
        assert r.status_code == 404
        assert query_one("SELECT quantity FROM committed_demand WHERE id = %s",
                         (world.c_company["id"],))["quantity"] == 100

    def test_cannot_move_own_commitment_out_of_scope(self, world):
        for wid in (world.wh["principal"], None):
            r = world.client.patch(f"{URL}/{world.c_norte['id']}", json={"warehouse_id": wid},
                                   headers=world.norte_h)
            assert r.status_code in (403, 422), r.text
        assert query_one("SELECT warehouse_id FROM committed_demand WHERE id = %s",
                         (world.c_norte["id"],))["warehouse_id"] == world.wh["Norte"]

    def test_edits_own_commitment(self, world):
        r = world.client.patch(f"{URL}/{world.c_norte['id']}", json={"quantity": 7},
                               headers=world.norte_h)
        assert r.status_code == 200, r.text
        assert query_one("SELECT quantity FROM committed_demand WHERE id = %s",
                         (world.c_norte["id"],))["quantity"] == 7

    def test_status_change_outside_scope_refused(self, world):
        r = world.client.post(f"{URL}/{world.c_principal['id']}/status",
                              json={"status": "cancelled"}, headers=world.norte_h)
        assert r.status_code == 404
        assert query_one("SELECT status FROM committed_demand WHERE id = %s",
                         (world.c_principal["id"],))["status"] == "open"

    def test_status_change_inside_scope(self, world):
        r = world.client.post(f"{URL}/{world.c_norte['id']}/status",
                              json={"status": "fulfilled"}, headers=world.norte_h)
        assert r.status_code == 200, r.text
        assert query_one("SELECT status FROM committed_demand WHERE id = %s",
                         (world.c_norte["id"],))["status"] == "fulfilled"
