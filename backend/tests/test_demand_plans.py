"""Demand plan versions with sign-off (backend/inventory/demand_plan_service.py,
backend/api/v1/demand_plans.py).

DB tests: they need Postgres. The arithmetic of the snapshot, the diff and the
plan-vs-actual reading is pinned without a database in
`test_demand_plan_math_pure.py`; this file pins what only the database can show:
the frozen row and its events, the permission pairs, the approval authority,
supersession, immutability, warehouse scope, tenant isolation, and that saving
or approving a plan moves no purchase recommendation.
"""

import json
from datetime import date, timedelta
from uuid import uuid4

import psycopg2
import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.inventory import committed_demand_service as cd_svc
from backend.inventory import demand_plan_service as svc
from backend.inventory import forecast_adjustment_service as adj_svc
from backend.inventory import service as inv_svc
from backend.sessions.service import create_session
from backend.users import service as user_svc

URL = "/api/v1/demand-plans"


def _session(tid, skus=("SKU-A", "SKU-B"), per_day=10.0, days=30, start=None,
             backtest=False):
    """A COMPLETED daily session whose forecast starts today."""
    sid = create_session(tid, "usr_test", f"dp-{uuid4().hex[:6]}")["id"]
    start = start or date.today()
    session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [
        {"date": (start + timedelta(days=i)).isoformat(), "value": per_day}
        for i in range(days)]}} for sku in skus})
    execute("UPDATE sessions SET status = 'COMPLETED', granularity = 'daily', "
            "is_backtest = %s WHERE id = %s", (backtest, sid))
    return sid


def _count(tid):
    return query_one("SELECT COUNT(*) AS n FROM demand_plan_versions WHERE tenant_id = %s",
                     (tid,))["n"]


def _status(vid):
    row = query_one("""SELECT to_status FROM demand_plan_version_events
                        WHERE version_id = %s AND kind = 'status' ORDER BY id DESC LIMIT 1""",
                    (vid,))
    return row["to_status"] if row else None


def _create(client, headers, sid, name="Octubre", **kw):
    r = client.post(URL, json={"name": name, "session_id": sid, **kw}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _person(tid, role, *, approver=False, scope=None):
    email = f"{role}-{uuid4().hex[:8]}@example.com"
    u = user_svc.create_user(tenant_id=tid, email=email, password="TestPass123!", role=role)
    user_svc.mark_verified(tid, u["id"])
    if approver:
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (u["id"],))
    if scope is not None:
        execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                (json.dumps(scope), u["id"]))
    tok = create_access_token(u["id"], tid, role, email_verified=True)
    return u, {"Authorization": f"Bearer {tok}"}


# ── Creating a version ───────────────────────────────────────────────────────

class TestCreate:

    def test_permission_pair_and_the_frozen_row(
            self, client, viewer_headers, analyst_headers, analyst_user, registered_user):
        tid = registered_user["tenant"]["id"]
        sid = _session(tid, days=10)
        denied = client.post(URL, json={"name": "x", "session_id": sid}, headers=viewer_headers)
        assert denied.status_code == 403
        assert _count(tid) == 0

        data = _create(client, analyst_headers, sid, horizon_periods=7, note="first cut")
        row = query_one("SELECT * FROM demand_plan_versions WHERE tenant_id = %s", (tid,))
        assert row["id"] == data["id"] and row["session_id"] == sid
        assert row["created_by"] == analyst_user["user"]["id"]
        assert (row["name"], row["sku_count"], row["horizon_periods"]) == ("Octubre", 2, 7)
        assert row["first_period"] == date.today() and row["anchor_date"] == date.today()
        snap = row["snapshot"]
        assert snap["skus"]["SKU-A"]["p"] == [10.0] * 7
        assert row["totals"]["plan"] == 140.0
        assert row["snapshot_bytes"] == len(json.dumps(snap, separators=(",", ":")).encode())
        events = query("SELECT * FROM demand_plan_version_events WHERE version_id = %s", (row["id"],))
        assert [(e["kind"], e["from_status"], e["to_status"], e["actor_id"], e["comment"])
                for e in events] == [("status", None, "draft", analyst_user["user"]["id"], "first cut")]
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'demand_plan.created' AND resource = %s", (tid, row["id"]))

    def test_the_snapshot_carries_adjustments_and_commitments(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        sid = _session(tid, skus=("SKU-A",), days=5)
        today = date.today()
        adj_svc.create(tid, sid, "u1", sku="SKU-A", start_date=today + timedelta(days=1),
                       end_date=today + timedelta(days=2), mode="percent", value=50,
                       reason_code="promotion")
        cd_svc.create(tid, "u1", sku="SKU-A", delivery_date=today + timedelta(days=3),
                      quantity=40, probability=0.5)
        cd_svc.create(tid, "u1", sku="NEW-SKU", delivery_date=today, quantity=8)
        cd_svc.create(tid, "u1", sku="SKU-A", delivery_date=today + timedelta(days=3),
                      quantity=999, on_top_of_base=False)      # already in history: never added
        data = _create(client, analyst_headers, sid)
        snap = query_one("SELECT snapshot FROM demand_plan_versions WHERE id = %s",
                         (data["id"],))["snapshot"]
        a = snap["skus"]["SKU-A"]
        assert a["f"] == [10.0] * 5
        assert a["a"] == [0.0, 5.0, 5.0, 0.0, 0.0]
        assert a["c"] == [0.0, 0.0, 0.0, 20.0, 0.0]
        assert a["p"] == [10.0, 15.0, 15.0, 30.0, 10.0]
        assert snap["skus"]["NEW-SKU"]["f"] == [None] * 5
        assert snap["skus"]["NEW-SKU"]["p"] == [8.0, 0.0, 0.0, 0.0, 0.0]
        assert data["totals"]["skus_without_forecast"] == 1

    def test_saving_and_approving_a_plan_moves_no_purchase_recommendation(
            self, client, analyst_headers, auth_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        sid = _session(tid, skus=("SKU-A",), days=40)
        inv_svc.upsert_stock(tid, "SKU-A", {"current_stock": 50.0, "lead_time_days": 20,
                                            "moq": 1.0})
        keys = ("recommended_qty", "daily_demand", "signal", "coverage_days", "reorder_point")

        def snapshot_row():
            row = {i["sku"]: i for i in inv_svc.get_inventory_status(tid, sid)}["SKU-A"]
            return {k: row.get(k) for k in keys}

        before = snapshot_row()
        v = _create(client, analyst_headers, sid)
        client.post(f"{URL}/{v['id']}/submit", json={}, headers=analyst_headers)
        assert client.post(f"{URL}/{v['id']}/approve", json={},
                           headers=auth_headers).status_code == 200
        assert _status(v["id"]) == "approved"
        assert snapshot_row() == before

    def test_session_refusals_carry_codes(self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        r = client.post(URL, json={"name": "x"}, headers=analyst_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "demand_plan_no_session"
        bt = _session(tid, backtest=True)
        r = client.post(URL, json={"name": "x", "session_id": bt}, headers=analyst_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "demand_plan_session_not_usable"
        assert r.json()["error_params"]["reason"] == "backtest"
        old = _session(tid, start=date.today() - timedelta(days=60), days=10)
        r = client.post(URL, json={"name": "x", "session_id": old}, headers=analyst_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "demand_plan_no_future_periods"
        r = client.post(URL, json={"name": "x", "session_id": "nope"}, headers=analyst_headers)
        assert r.status_code == 404
        assert _count(tid) == 0

    def test_the_version_ceiling_refuses_and_writes_nothing(
            self, client, analyst_headers, registered_user, monkeypatch):
        tid = registered_user["tenant"]["id"]
        sid = _session(tid, days=5)
        monkeypatch.setattr(svc, "MAX_VERSIONS", 1)
        _create(client, analyst_headers, sid)
        r = client.post(URL, json={"name": "again", "session_id": sid}, headers=analyst_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "demand_plan_version_limit"
        assert _count(tid) == 1


# ── Immutability ─────────────────────────────────────────────────────────────

class TestImmutable:

    def test_the_database_refuses_to_rewrite_a_version_or_its_history(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        with pytest.raises(psycopg2.Error):
            execute("UPDATE demand_plan_versions SET name = 'edited' WHERE id = %s", (v["id"],))
        with pytest.raises(psycopg2.Error):
            execute("UPDATE demand_plan_version_events SET to_status = 'approved' "
                    "WHERE version_id = %s", (v["id"],))
        assert query_one("SELECT name FROM demand_plan_versions WHERE id = %s",
                         (v["id"],))["name"] == "Octubre"
        assert _status(v["id"]) == "draft"

    def test_no_route_deletes_a_version(self, client, auth_headers, analyst_headers,
                                        registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        r = client.delete(f"{URL}/{v['id']}", headers=auth_headers)
        assert r.status_code == 405
        assert _count(tid) == 1


# ── Sign-off ─────────────────────────────────────────────────────────────────

class TestSignOff:

    def test_submit_permission_pair(self, client, viewer_headers, analyst_headers,
                                    analyst_user, registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        assert client.post(f"{URL}/{v['id']}/submit", json={},
                           headers=viewer_headers).status_code == 403
        assert _status(v["id"]) == "draft"
        r = client.post(f"{URL}/{v['id']}/submit", json={"comment": "ready"},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert _status(v["id"]) == "submitted"
        ev = query_one("""SELECT * FROM demand_plan_version_events WHERE version_id = %s
                          ORDER BY id DESC LIMIT 1""", (v["id"],))
        assert (ev["from_status"], ev["actor_id"], ev["comment"]) == (
            "draft", analyst_user["user"]["id"], "ready")

    def test_a_draft_cannot_be_approved_directly(self, client, analyst_headers, auth_headers,
                                                 registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        r = client.post(f"{URL}/{v['id']}/approve", json={}, headers=auth_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "demand_plan_transition_invalid"
        assert _status(v["id"]) == "draft"

    def test_only_an_approver_decides(self, client, analyst_headers, auth_headers,
                                      registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        client.post(f"{URL}/{v['id']}/submit", json={}, headers=analyst_headers)
        _, other_analyst = _person(tid, "analyst")
        r = client.post(f"{URL}/{v['id']}/approve", json={}, headers=other_analyst)
        assert r.status_code == 403 and r.json()["error_code"] == "demand_plan_not_approver"
        assert _status(v["id"]) == "submitted"
        r = client.post(f"{URL}/{v['id']}/approve", json={"comment": "ok"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert _status(v["id"]) == "approved"
        ev = query_one("""SELECT * FROM demand_plan_version_events WHERE version_id = %s
                          ORDER BY id DESC LIMIT 1""", (v["id"],))
        assert ev["actor_id"] == registered_user["user"]["id"] and ev["details"] == {}
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'demand_plan.approved' AND resource = %s", (tid, v["id"]))

    def test_a_flagged_analyst_may_approve(self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        client.post(f"{URL}/{v['id']}/submit", json={}, headers=analyst_headers)
        _, flagged = _person(tid, "analyst", approver=True)
        assert client.post(f"{URL}/{v['id']}/approve", json={},
                           headers=flagged).status_code == 200
        assert _status(v["id"]) == "approved"

    def test_the_submitter_cannot_approve_while_somebody_else_can(
            self, client, auth_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        _person(tid, "analyst", approver=True)          # a second approver exists
        v = _create(client, auth_headers, _session(tid, days=5))
        client.post(f"{URL}/{v['id']}/submit", json={}, headers=auth_headers)
        r = client.post(f"{URL}/{v['id']}/approve", json={}, headers=auth_headers)
        assert r.status_code == 403 and r.json()["error_code"] == "demand_plan_self_approval"
        assert _status(v["id"]) == "submitted"

    def test_the_only_approver_may_approve_their_own_and_it_is_recorded(
            self, client, auth_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        assert [a["id"] for a in svc.approvers(tid)] == [registered_user["user"]["id"]]
        v = _create(client, auth_headers, _session(tid, days=5))
        client.post(f"{URL}/{v['id']}/submit", json={}, headers=auth_headers)
        r = client.post(f"{URL}/{v['id']}/approve", json={}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["data"]["self_approved"] is True
        ev = query_one("""SELECT details FROM demand_plan_version_events WHERE version_id = %s
                          ORDER BY id DESC LIMIT 1""", (v["id"],))
        assert ev["details"] == {"self_approved": True}

    def test_approving_a_newer_version_supersedes_the_older_one(
            self, client, analyst_headers, auth_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        sid = _session(tid, days=5)
        old = _create(client, analyst_headers, sid, name="Sep")
        new = _create(client, analyst_headers, sid, name="Oct")
        for v in (old, new):
            client.post(f"{URL}/{v['id']}/submit", json={}, headers=analyst_headers)
        client.post(f"{URL}/{old['id']}/approve", json={}, headers=auth_headers)
        r = client.post(f"{URL}/{new['id']}/approve", json={}, headers=auth_headers)
        assert r.json()["data"]["superseded"] == [old["id"]]
        assert _status(old["id"]) == "superseded" and _status(new["id"]) == "approved"
        ev = query_one("""SELECT * FROM demand_plan_version_events WHERE version_id = %s
                          ORDER BY id DESC LIMIT 1""", (old["id"],))
        assert ev["from_status"] == "approved" and ev["details"] == {"superseded_by": new["id"]}
        approved = [i for i in client.get(URL, headers=auth_headers).json()["data"]["items"]
                    if i["status"] == "approved"]
        assert [i["id"] for i in approved] == [new["id"]]

    def test_reject_needs_a_reason_and_is_final(self, client, analyst_headers, auth_headers,
                                                registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        client.post(f"{URL}/{v['id']}/submit", json={}, headers=analyst_headers)
        r = client.post(f"{URL}/{v['id']}/reject", json={"comment": " "}, headers=auth_headers)
        assert r.status_code == 422
        assert r.json()["error_code"] == "demand_plan_reject_reason_required"
        assert _status(v["id"]) == "submitted"
        r = client.post(f"{URL}/{v['id']}/reject", json={"comment": "too optimistic"},
                        headers=auth_headers)
        assert r.status_code == 200 and _status(v["id"]) == "rejected"
        again = client.post(f"{URL}/{v['id']}/submit", json={}, headers=analyst_headers)
        assert again.status_code == 409
        assert _status(v["id"]) == "rejected"

    def test_comment_permission_pair(self, client, viewer_headers, analyst_headers,
                                     registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        assert client.post(f"{URL}/{v['id']}/comments", json={"comment": "hi"},
                           headers=viewer_headers).status_code == 403
        r = client.post(f"{URL}/{v['id']}/comments", json={"comment": "check SKU-B"},
                        headers=analyst_headers)
        assert r.status_code == 201
        rows = query("""SELECT kind, to_status, comment FROM demand_plan_version_events
                         WHERE version_id = %s ORDER BY id""", (v["id"],))
        assert [(x["kind"], x["to_status"], x["comment"]) for x in rows][-1] == (
            "comment", None, "check SKU-B")
        assert _status(v["id"]) == "draft"       # a comment is not a status change


# ── Reading, diff, accuracy ──────────────────────────────────────────────────

class TestRead:

    def test_viewer_reads_list_detail_and_lines(self, client, viewer_headers, analyst_headers,
                                                registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        listed = client.get(URL, headers=viewer_headers).json()["data"]
        assert [i["id"] for i in listed["items"]] == [v["id"]]
        assert listed["items"][0]["status"] == "draft"
        assert "snapshot" not in listed["items"][0]
        assert listed["can_approve"] is False
        detail = client.get(f"{URL}/{v['id']}", headers=viewer_headers).json()["data"]
        assert len(detail["by_period"]) == 5 and detail["events"][0]["to_status"] == "draft"
        lines = client.get(f"{URL}/{v['id']}/lines", params={"q": "sku-b"},
                           headers=viewer_headers).json()["data"]
        assert [r["sku"] for r in lines["items"]] == ["SKU-B"]

    def test_diff_names_the_skus_that_moved(self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        sid = _session(tid, days=5)
        a = _create(client, analyst_headers, sid, name="A")
        cd_svc.create(tid, "u1", sku="SKU-B", delivery_date=date.today() + timedelta(days=1),
                      quantity=30)
        b = _create(client, analyst_headers, sid, name="B")
        d = client.get(f"{URL}/diff", params={"a": a["id"], "b": b["id"]},
                       headers=analyst_headers).json()["data"]
        assert d["status"] == "ok"
        assert [(r["sku"], r["change"]) for r in d["items"]] == [("SKU-B", 30.0)]

    def test_accuracy_before_any_period_has_passed(self, client, analyst_headers,
                                                   registered_user):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        out = client.get(f"{URL}/{v['id']}/accuracy", headers=analyst_headers).json()["data"]
        assert out["status"] == "periods_not_passed" and out["aggregate"] is None

    def test_another_tenants_version_is_not_found(self, client, analyst_headers, registered_user,
                                                  make_tenant_user_headers):
        tid = registered_user["tenant"]["id"]
        v = _create(client, analyst_headers, _session(tid, days=5))
        foreign = make_tenant_user_headers(role="admin")
        for path in ("", "/lines", "/accuracy"):
            assert client.get(f"{URL}/{v['id']}{path}", headers=foreign).status_code == 404
        r = client.post(f"{URL}/{v['id']}/submit", json={}, headers=foreign)
        assert r.status_code == 404
        assert _status(v["id"]) == "draft"
        assert client.get(URL, headers=foreign).json()["data"]["items"] == []


# ── Warehouse scope: versions are company-wide objects ───────────────────────

class TestWarehouseScope:

    def test_a_scoped_user_neither_reads_nor_writes(self, client, analyst_headers,
                                                    registered_user):
        tid = registered_user["tenant"]["id"]
        sid = _session(tid, days=5)
        v = _create(client, analyst_headers, sid)
        _, scoped = _person(tid, "analyst", scope=[])
        for r in (client.get(URL, headers=scoped),
                  client.get(f"{URL}/{v['id']}", headers=scoped),
                  client.post(URL, json={"name": "x", "session_id": sid}, headers=scoped),
                  client.post(f"{URL}/{v['id']}/submit", json={}, headers=scoped)):
            assert r.status_code == 403
            assert r.json()["error_code"] == "warehouse_scope_company_totals"
        assert _count(tid) == 1 and _status(v["id"]) == "draft"


# ── Tenant export and erasure know the tables ────────────────────────────────

def test_tenant_export_and_erasure_list_both_tables():
    from backend.tenants import data_export
    exported = {t for _, t, _ in data_export._EXPORT_SPECS}
    assert {"demand_plan_versions", "demand_plan_version_events"} <= exported
    order = data_export._DELETE_ORDER
    assert order.index("demand_plan_version_events") < order.index("demand_plan_versions")
