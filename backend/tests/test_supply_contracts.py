"""Blanket supply contracts against Postgres (backend/inventory/supply_contract_service.py).

Pins the endpoints (permission pairs, read-backs from the tables), the
idempotent materialisation into `committed_demand`, the append-only revision
chain, withdrawal by cancelling (never deleting), warehouse scope, and that
the materialised commitments reach the purchase maths through the existing
`active_by_sku` path. The pure half is `test_supply_contracts_pure.py`.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from uuid import uuid4

import psycopg2
import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query, query_one
from backend.inventory import committed_demand_service as cd_svc
from backend.inventory import supply_contract_service as svc
from backend.users import service as user_svc

URL = "/api/v1/supply-contracts"


def _month_start(offset: int) -> date:
    t = date.today().replace(day=1)
    return svc._add_months(t, offset)


def _body(**over):
    """12 monthly releases of 100 starting next month: all inside the
    180-day horizon except the last six."""
    b = {
        "customer": "Big Corp",
        "lines": [{"sku": "SKU-K", "total_quantity": 1200}],
        "period_start": _month_start(1).isoformat(),
        "period_end": (_month_start(13) - timedelta(days=1)).isoformat(),
        "schedule_kind": "monthly",
        "tolerance_pct": 5,
        "status": "active",
    }
    b.update(over)
    return b


def _create(client, headers, **over):
    r = client.post(URL, json=_body(**over), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _contracts(tid):
    return query("SELECT * FROM supply_contracts WHERE tenant_id = %s ORDER BY revision", (tid,))


def _commitments(tid, root_id=None):
    sql = "SELECT * FROM committed_demand WHERE tenant_id = %s"
    params = [tid]
    if root_id:
        sql += " AND contract_root_id = %s"
        params.append(root_id)
    return query(sql + " ORDER BY contract_release_date, created_at", tuple(params))


def _expected_materialised(start: date, n: int = 12) -> int:
    limit = date.today() + timedelta(days=svc.MATERIALISE_HORIZON_DAYS)
    return sum(1 for k in range(n) if svc._add_months(start, k) <= limit)


class TestCreate:

    def test_permission_pair_and_the_stored_rows(
            self, client, viewer_headers, analyst_headers, analyst_user, registered_user):
        tid = registered_user["tenant"]["id"]
        denied = client.post(URL, json=_body(), headers=viewer_headers)
        assert denied.status_code == 403
        assert _contracts(tid) == [] and _commitments(tid) == []

        c = _create(client, analyst_headers)
        rows = _contracts(tid)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == row["root_id"] == c["root_id"] == c["id"]
        assert (row["revision"], row["status"], row["customer"], row["schedule_kind"]) == (
            1, "active", "Big Corp", "monthly")
        assert row["created_by"] == analyst_user["user"]["id"]
        assert row["superseded_by"] is None

        com = _commitments(tid, c["root_id"])
        assert len(com) == _expected_materialised(_month_start(1)) == c["materialised"]
        assert {(x["source"], x["contract_id"], x["status"], x["quantity"], x["probability"],
                 x["customer"]) for x in com} == {
            ("contract", row["id"], "open", 100.0, 1.0, "Big Corp")}
        assert all(x["contract_release_date"] == x["delivery_date"] for x in com)
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'supply_contract.created' AND resource = %s",
                     (tid, c["root_id"]))

    def test_a_draft_materialises_nothing_until_activated(
            self, client, analyst_headers, viewer_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers, status="draft")
        assert _commitments(tid) == []

        denied = client.post(f"{URL}/{c['root_id']}/status",
                             json={"status": "active", "expected_revision": 1},
                             headers=viewer_headers)
        assert denied.status_code == 403
        assert [r["status"] for r in _contracts(tid)] == ["draft"]

        r = client.post(f"{URL}/{c['root_id']}/status",
                        json={"status": "active", "expected_revision": 1},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        rows = _contracts(tid)
        assert [(x["revision"], x["status"]) for x in rows] == [(1, "draft"), (2, "active")]
        assert rows[0]["superseded_by"] == rows[1]["id"]
        assert len(_commitments(tid)) == _expected_materialised(_month_start(1))

    def test_a_release_without_a_quantity_is_refused_and_nothing_is_written(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        r = client.post(URL, headers=analyst_headers, json=_body(
            lines=[{"sku": "SKU-K"}], schedule_kind="explicit",
            releases=[{"date": _month_start(1).isoformat(), "quantity": 10},
                      {"date": _month_start(2).isoformat()}]))
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "supply_contract_releases_invalid"
        assert body["error_params"]["errors"][0]["code"] == "supply_contract_release_quantity_missing"
        assert body["error_params"]["errors"][0]["row"] == 2
        assert _contracts(tid) == [] and _commitments(tid) == []

    def test_past_releases_are_materialised_and_shown_overdue(
            self, client, analyst_headers, viewer_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        start = _month_start(-3)
        c = _create(client, analyst_headers, period_start=start.isoformat(),
                    period_end=(_month_start(3) - timedelta(days=1)).isoformat(),
                    lines=[{"sku": "SKU-K", "total_quantity": 600}])
        n_past = sum(1 for k in range(6) if svc._add_months(start, k) < date.today())
        assert n_past >= 3
        past = [x for x in _commitments(tid, c["root_id"])
                if x["contract_release_date"] < date.today()]
        assert len(past) == n_past and all(x["status"] == "open" for x in past)
        listed = client.get(URL, headers=viewer_headers).json()["data"]["items"]
        p = next(i for i in listed if i["root_id"] == c["root_id"])["progress"]
        assert p["overdue_count"] == n_past and p["behind_schedule"] is True
        assert p["delivered"] == 0 and p["remaining"] == 600


class TestIdempotency:

    def test_materialising_again_never_duplicates(self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers)
        before = len(_commitments(tid))
        first = svc.materialise_active(tid)
        second = svc.materialise_active(tid)
        assert first == {"contracts": 1, "created": 0, "failed": 0}
        assert second == first
        assert len(_commitments(tid)) == before
        keys = [(x["sku"], x["contract_release_date"]) for x in _commitments(tid, c["root_id"])]
        assert len(keys) == len(set(keys))

    def test_the_database_refuses_a_second_live_row_for_a_release(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers)
        one = _commitments(tid, c["root_id"])[0]
        with pytest.raises(psycopg2.errors.UniqueViolation):
            execute(
                """INSERT INTO committed_demand
                       (tenant_id, sku, delivery_date, quantity, created_by, source,
                        contract_id, contract_root_id, contract_release_date)
                   VALUES (%s, %s, %s, 1, 'x', 'contract', %s, %s, %s)""",
                (tid, one["sku"], one["delivery_date"], one["contract_id"],
                 c["root_id"], one["contract_release_date"]))

    def test_a_far_release_appears_when_the_horizon_reaches_it(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers)
        before = len(_commitments(tid))
        later = svc.materialise_active(tid, today=date.today() + timedelta(days=400))
        assert later["created"] == 12 - before
        assert len(_commitments(tid, c["root_id"])) == 12


class TestRevisionAndWithdrawal:

    def test_revise_permission_pair_withdraws_open_and_keeps_fulfilled(
            self, client, viewer_headers, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers)
        first = _commitments(tid, c["root_id"])
        fulfilled = first[0]
        r = client.post(f"/api/v1/committed-demand/{fulfilled['id']}/status",
                        json={"status": "fulfilled"}, headers=analyst_headers)
        assert r.status_code == 200, r.text

        new_terms = {k: v for k, v in _body(lines=[{"sku": "SKU-K", "total_quantity": 2400}]).items()
                     if k != "status"}
        denied = client.post(f"{URL}/{c['root_id']}/revisions",
                             json={**new_terms, "expected_revision": 1}, headers=viewer_headers)
        assert denied.status_code == 403
        assert len(_contracts(tid)) == 1

        ok = client.post(f"{URL}/{c['root_id']}/revisions",
                         json={**new_terms, "expected_revision": 1}, headers=analyst_headers)
        assert ok.status_code == 200, ok.text
        rows = _contracts(tid)
        assert [x["revision"] for x in rows] == [1, 2]
        assert rows[0]["superseded_by"] == rows[1]["id"] and rows[1]["superseded_by"] is None
        assert rows[0]["lines"][0]["total_quantity"] == 1200     # the old row is untouched

        after = {x["id"]: x for x in _commitments(tid, c["root_id"])}
        # Nothing was deleted.
        assert {x["id"] for x in first} <= set(after)
        assert after[fulfilled["id"]]["status"] == "fulfilled"
        assert after[fulfilled["id"]]["contract_withdrawn_at"] is None
        for x in first[1:]:
            assert after[x["id"]]["status"] == "cancelled"
            assert after[x["id"]]["contract_withdrawn_at"] is not None
        live_open = [x for x in after.values()
                     if x["status"] == "open" and x["contract_withdrawn_at"] is None]
        assert {x["contract_id"] for x in live_open} == {rows[1]["id"]}
        assert {x["quantity"] for x in live_open} == {200.0}
        # The fulfilled release is not called off a second time.
        assert fulfilled["contract_release_date"] not in {x["contract_release_date"] for x in live_open}
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'supply_contract.revised' AND resource = %s",
                     (tid, c["root_id"]))

    def test_a_stale_revision_is_refused_and_changes_nothing(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers)
        terms = {k: v for k, v in _body().items() if k != "status"}
        assert client.post(f"{URL}/{c['root_id']}/revisions", headers=analyst_headers,
                           json={**terms, "expected_revision": 1}).status_code == 200
        before = _commitments(tid)
        r = client.post(f"{URL}/{c['root_id']}/revisions", headers=analyst_headers,
                        json={**terms, "customer": "Other", "expected_revision": 1})
        assert r.status_code == 409 and r.json()["error_code"] == "supply_contract_stale"
        assert [x["customer"] for x in _contracts(tid)] == ["Big Corp", "Big Corp"]
        assert _commitments(tid) == before

    def test_cancel_withdraws_open_commitments_and_is_final(
            self, client, viewer_headers, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers)
        denied = client.post(f"{URL}/{c['root_id']}/status", headers=viewer_headers,
                             json={"status": "cancelled", "expected_revision": 1})
        assert denied.status_code == 403
        assert all(x["status"] == "open" for x in _commitments(tid))

        r = client.post(f"{URL}/{c['root_id']}/status", headers=analyst_headers,
                        json={"status": "cancelled", "expected_revision": 1})
        assert r.status_code == 200, r.text
        assert [x["status"] for x in _contracts(tid)] == ["active", "cancelled"]
        com = _commitments(tid)
        assert com and all(x["status"] == "cancelled" and x["contract_withdrawn_at"] for x in com)
        assert cd_svc.active_by_sku(tid) == {}

        again = client.post(f"{URL}/{c['root_id']}/status", headers=analyst_headers,
                            json={"status": "active", "expected_revision": 2})
        assert again.status_code == 409
        assert again.json()["error_code"] == "supply_contract_transition_invalid"
        revise = client.post(f"{URL}/{c['root_id']}/revisions", headers=analyst_headers,
                             json={**{k: v for k, v in _body().items() if k != "status"},
                                   "expected_revision": 2})
        assert revise.status_code == 409 and revise.json()["error_code"] == "supply_contract_final"
        assert len(_contracts(tid)) == 2

    def test_a_withdrawn_commitment_cannot_be_reopened(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers)
        client.post(f"{URL}/{c['root_id']}/status", headers=analyst_headers,
                    json={"status": "closed", "expected_revision": 1})
        one = _commitments(tid)[0]
        r = client.post(f"/api/v1/committed-demand/{one['id']}/status",
                        json={"status": "open"}, headers=analyst_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "committed_demand_withdrawn"
        assert query_one("SELECT status FROM committed_demand WHERE id = %s",
                         (one["id"],))["status"] == "cancelled"

    def test_contract_fields_of_a_materialised_commitment_are_locked(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        _create(client, analyst_headers)
        one = _commitments(tid)[0]
        r = client.patch(f"/api/v1/committed-demand/{one['id']}", json={"sku": "OTHER"},
                         headers=analyst_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "committed_demand_contract_locked"
        ok = client.patch(f"/api/v1/committed-demand/{one['id']}", json={"quantity": 90},
                          headers=analyst_headers)
        assert ok.status_code == 200, ok.text
        row = query_one("SELECT sku, quantity FROM committed_demand WHERE id = %s", (one["id"],))
        assert (row["sku"], row["quantity"]) == ("SKU-K", 90.0)


class TestFulfilment:

    def test_delivered_comes_from_fulfilled_commitments(
            self, client, analyst_headers, viewer_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        c = _create(client, analyst_headers, period_start=_month_start(-2).isoformat(),
                    period_end=(_month_start(10) - timedelta(days=1)).isoformat())
        past = [x for x in _commitments(tid, c["root_id"])
                if x["contract_release_date"] < date.today()]
        client.patch(f"/api/v1/committed-demand/{past[0]['id']}", json={"quantity": 80},
                     headers=analyst_headers)
        client.post(f"/api/v1/committed-demand/{past[0]['id']}/status",
                    json={"status": "fulfilled"}, headers=analyst_headers)
        d = client.get(f"{URL}/{c['root_id']}", headers=viewer_headers).json()["data"]
        p = d["progress"]
        assert p["delivered"] == 80 and p["remaining"] == 1120
        assert p["overdue_count"] == len(past) - 1         # the past months not delivered
        assert [r["revision"] for r in d["revisions"]] == [1]


class TestReachesThePurchaseMaths:

    def test_materialised_commitments_are_what_active_by_sku_reads(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        assert cd_svc.active_by_sku(tid) == {}
        c = _create(client, analyst_headers)
        active = cd_svc.active_by_sku(tid)
        assert set(active) == {"SKU-K"}
        assert len(active["SKU-K"]) == _expected_materialised(_month_start(1))

    def test_history_already_contains_it_adds_nothing(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        _create(client, analyst_headers, on_top_of_base=False)
        assert _commitments(tid) and cd_svc.active_by_sku(tid) == {}

    def test_no_contracts_no_change(self, client, viewer_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        assert client.get(URL, headers=viewer_headers).json()["data"]["items"] == []
        assert svc.materialise_active(tid) == {"contracts": 0, "created": 0, "failed": 0}
        assert _commitments(tid) == []


class TestWarehouseScope:

    @pytest.fixture
    def scoped(self, client, registered_user):
        tid = registered_user["tenant"]["id"]
        wh = {}
        for name in ("Norte", "Sur"):
            wh[name] = query_one(
                "INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s) RETURNING id",
                (tid, name))["id"]
        u = user_svc.create_user(tenant_id=tid, email=f"norte-{uuid4().hex[:8]}@example.com",
                                 password="TestPass123!", role="analyst")
        user_svc.mark_verified(tid, u["id"])
        execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                (json.dumps([wh["Norte"]]), u["id"]))
        tok = create_access_token(u["id"], tid, "analyst", email_verified=True)
        return tid, wh, {"Authorization": f"Bearer {tok}"}

    def test_scoped_user_writes_only_in_its_warehouses(self, client, scoped):
        tid, wh, h = scoped
        r = client.post(URL, json=_body(), headers=h)
        assert r.status_code == 403
        assert r.json()["error_code"] == "supply_contract_scope_company_wide"
        r = client.post(URL, json=_body(warehouse_id=wh["Sur"]), headers=h)
        assert r.status_code == 403 and r.json()["error_code"] == "warehouse_out_of_scope"
        assert _contracts(tid) == [] and _commitments(tid) == []

        ok = client.post(URL, json=_body(warehouse_id=wh["Norte"]), headers=h)
        assert ok.status_code == 201, ok.text
        assert {x["warehouse_id"] for x in _commitments(tid)} == {wh["Norte"]}

    def test_scoped_user_sees_only_its_warehouses(self, client, scoped, analyst_headers):
        tid, wh, h = scoped
        everyone = _create(client, analyst_headers)
        sur = _create(client, analyst_headers, warehouse_id=wh["Sur"])
        norte = _create(client, analyst_headers, warehouse_id=wh["Norte"])
        items = client.get(URL, headers=h).json()["data"]["items"]
        assert [i["root_id"] for i in items] == [norte["root_id"]]
        assert client.get(f"{URL}/{sur['root_id']}", headers=h).status_code == 403
        assert client.get(f"{URL}/{everyone['root_id']}", headers=h).status_code == 403
        # Unrestricted users see all three.
        assert len(client.get(URL, headers=analyst_headers).json()["data"]["items"]) == 3

    def test_scoped_user_cannot_cancel_another_warehouses_contract(
            self, client, scoped, analyst_headers):
        tid, wh, h = scoped
        sur = _create(client, analyst_headers, warehouse_id=wh["Sur"])
        r = client.post(f"{URL}/{sur['root_id']}/status", headers=h,
                        json={"status": "cancelled", "expected_revision": 1})
        assert r.status_code == 403
        assert [x["status"] for x in _contracts(tid)] == ["active"]
        assert all(x["status"] == "open" for x in _commitments(tid))


def test_tenant_export_and_erasure_name_the_table():
    from backend.tenants import data_export
    assert ("supply_contracts", "supply_contracts", "*") in data_export._EXPORT_SPECS
    assert "supply_contracts" in data_export._DELETE_ORDER
