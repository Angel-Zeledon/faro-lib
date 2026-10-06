"""Cost centers and chained approval, as the Python decision path serves them.

The management routes (centers, chains) are Rust only, so these tests write the
rows the way Rust does (direct SQL) and drive everything Python owns: the
requirement, the gate on every send, the request, the level-by-level decision,
the inbox, the order's attribution and the cost-center budget. Every claim is
read back from the database.
"""

from __future__ import annotations

import json
from datetime import date
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.inventory import po_approval_service as svc
from backend.inventory import roi_service
from backend.users import service as user_svc

LOG_PO = "/api/v1/inventory/log-po"
BUDGETS = "/api/v1/inventory/budgets"
ADMIN = {"kind": "role", "role": "admin"}
ANALYST = {"kind": "role", "role": "analyst"}


def _po(tid, qty=100, cost=60.0, center=None, escalate=False):
    po = roi_service.log_po_generation(
        tid, "sess-test",
        [{"sku": f"A-{uuid4().hex[:6]}", "final_qty": qty, "unit_cost": cost,
          "status": "approved", "supplier": "Acme"}],
        cost_center_id=center, chain_escalate=escalate)
    return po["id"]


def _person(client, tid, role="analyst"):
    email = f"{role}-{uuid4().hex[:8]}@example.com"
    user = user_svc.create_user(tenant_id=tid, email=email, password="TestPass123!",
                                role=role, full_name=f"{role.title()} {uuid4().hex[:4]}")
    user_svc.mark_verified(tid, user["id"])
    token = client.post("/api/v1/auth/login", json={
        "email": email, "password": "TestPass123!"}).json()["data"]["access_token"]
    return user["id"], {"Authorization": f"Bearer {token}"}


def _center(tid, code, parent=None, active=True):
    return query_one(
        "INSERT INTO cost_centers (tenant_id, code, name, parent_id, active, created_by) "
        "VALUES (%s, %s, %s, %s, %s, 'test') RETURNING id",
        (tid, code, f"Center {code}", parent, active))["id"]


def _chain(tid, bands, center=None, active=True):
    cid = query_one(
        "INSERT INTO approval_chains (tenant_id, name, cost_center_id, active, created_by) "
        "VALUES (%s, 'chain', %s, %s, 'test') RETURNING id", (tid, center, active))["id"]
    for min_amount, levels in bands:
        execute("INSERT INTO approval_chain_bands (tenant_id, chain_id, min_amount, levels) "
                "VALUES (%s, %s, %s, %s::jsonb)", (tid, cid, min_amount, json.dumps(levels)))
    return cid


def _named(*ids):
    return {"kind": "users", "user_ids": list(ids)}


def _req(client, headers, po):
    return client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=headers)


def _approve(client, headers, po, comment=None):
    return client.post(f"/api/v1/inventory/po/{po}/approval/approve",
                       json={"comment": comment} if comment else None, headers=headers)


def _reject(client, headers, po, comment="no good reason"):
    return client.post(f"/api/v1/inventory/po/{po}/approval/reject",
                       json={"comment": comment}, headers=headers)


def _state(po):
    return query_one("SELECT approval_status, approved_amount FROM inventory_po_log WHERE id = %s",
                     (po,))


def _steps(po):
    return query("SELECT s.level_no, s.status, s.decided_by FROM po_approval_steps s "
                 "JOIN po_approvals a ON a.id = s.approval_id WHERE a.po_log_id = %s "
                 "ORDER BY a.requested_at, s.level_no", (po,))


def _events(tid, action, po):
    return query("SELECT user_id, context FROM activity_logs WHERE tenant_id = %s "
                 "AND action = %s AND resource = %s", (tid, action, po))


@pytest.fixture(autouse=True)
def _mails(monkeypatch):
    from backend.notifications import email as email_mod
    sent = {"request": [], "decision": []}
    monkeypatch.setattr(email_mod, "send_po_approval_request_email",
                        lambda **kw: sent["request"].append(kw) or True)
    monkeypatch.setattr(email_mod, "send_po_approval_decision_email",
                        lambda **kw: sent["decision"].append(kw) or True)
    return sent


class TestNoChainNoChange:

    def test_an_order_with_a_center_but_no_chain_behaves_as_before(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        center = _center(tid, "OPS")
        po = _po(tid, qty=1000, cost=1000.0, center=center)
        svc.assert_sendable(tid, po_log_id=po)                      # does not raise
        resp = _req(client, analyst_headers, po)
        assert resp.status_code == 409 and resp.json()["error_code"] == "po_approval_not_required"
        assert query("SELECT 1 FROM po_approvals WHERE po_log_id = %s", (po,)) == []
        assert query("SELECT 1 FROM po_approval_steps WHERE tenant_id = %s", (tid,)) == []
        req = svc.requirement(tid, query_one("SELECT * FROM inventory_po_log WHERE id = %s", (po,)))
        assert "chain" not in req

    def test_an_inactive_chain_changes_nothing(self, test_tenant):
        tid = test_tenant["id"]
        _chain(tid, [(1, [ADMIN])], active=False)
        po = _po(tid)
        svc.assert_sendable(tid, po_log_id=po)


class TestFailClosed:

    def test_no_center_and_no_default_chain_blocks_every_send_path(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        ops = _center(tid, "OPS")
        _chain(tid, [(100, [ADMIN])], center=ops)       # covers OPS only
        po = _po(tid, center=None)
        with pytest.raises(AppError) as exc:
            svc.assert_sendable(tid, po_log_id=po)
        assert exc.value.code == "po_approval_chain_unresolved"
        assert exc.value.params["reason"] == "no_cost_center"
        resp = client.post(f"/api/v1/inventory/po/{po}/send", headers=analyst_headers)
        assert resp.status_code == 409 and resp.json()["error_code"] == "po_approval_chain_unresolved"
        assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po,))["sent_at"] is None
        # asking for approval cannot resolve it either, and writes nothing
        resp = _req(client, analyst_headers, po)
        assert resp.status_code == 409 and resp.json()["error_params"]["reason"] == "no_cost_center"
        assert query("SELECT 1 FROM po_approvals WHERE po_log_id = %s", (po,)) == []

    def test_a_value_below_every_band_is_free_but_an_unknown_value_is_not(
            self, test_tenant):
        tid = test_tenant["id"]
        _chain(tid, [(1000, [ADMIN])])
        svc.assert_sendable(tid, po_log_id=_po(tid, qty=1, cost=5.0))   # 5 < 1000: free
        no_cost = roi_service.log_po_generation(
            tid, "sess-test", [{"sku": "NC", "final_qty": 5, "unit_cost": None,
                                "status": "approved", "supplier": "Acme"}])["id"]
        with pytest.raises(AppError) as exc:
            svc.assert_sendable(tid, po_log_id=no_cost)
        assert exc.value.params["reason"] == "amount_unknown"

    def test_inactive_unknown_and_malformed(self, test_tenant):
        tid = test_tenant["id"]
        gone = _center(tid, "OLD", active=False)
        _chain(tid, [(1, [ADMIN])])
        for center, reason in ((gone, "cost_center_invalid"), ("ghost", "cost_center_invalid")):
            po = _po(tid, center=center)
            with pytest.raises(AppError) as exc:
                svc.assert_sendable(tid, po_log_id=po)
            assert exc.value.params["reason"] == reason
        # a chain whose levels were corrupted behind our back is not trusted
        execute("UPDATE approval_chain_bands SET levels = '[]'::jsonb WHERE tenant_id = %s", (tid,))
        po = _po(tid)
        with pytest.raises(AppError) as exc:
            svc.assert_sendable(tid, po_log_id=po)
        assert exc.value.params["reason"] == "chain_invalid"

    def test_history_rows_report_unresolved_not_not_required(self, client, auth_headers,
                                                             test_tenant):
        tid = test_tenant["id"]
        _chain(tid, [(1, [ADMIN])], center=_center(tid, "OPS"))
        _po(tid)                                         # no center: unresolved
        rows = client.get("/api/v1/inventory/po-history/page", headers=auth_headers
                          ).json()["data"]["items"]
        assert rows and all(r["approval"] == {"required": True, "status": "chain_unresolved"}
                            for r in rows)


class TestChainedDecision:

    def _setup(self, client, tid, levels=None):
        a_id, a_h = _person(client, tid, "analyst")
        b_id, b_h = _person(client, tid, "analyst")
        _chain(tid, [(1000, levels or [_named(a_id), _named(b_id)])])
        return a_id, a_h, b_id, b_h

    def test_two_levels_in_order_each_by_a_different_person(
            self, client, analyst_headers, analyst_user, viewer_headers, test_tenant, _mails):
        tid = test_tenant["id"]
        a_id, a_h, b_id, b_h = self._setup(client, tid)
        po = _po(tid)                                           # 6000
        # a viewer cannot ask; nothing is written
        assert _req(client, viewer_headers, po).status_code == 403
        assert query("SELECT 1 FROM po_approvals WHERE po_log_id = %s", (po,)) == []

        resp = _req(client, analyst_headers, po)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["changed"] is True
        row = query_one("SELECT status, chain_id, chain_fingerprint, amount FROM po_approvals "
                        "WHERE po_log_id = %s", (po,))
        assert row["status"] == "requested" and row["chain_id"] and row["amount"] == 6000
        assert [(s["level_no"], s["status"]) for s in _steps(po)] == [(1, "pending"), (2, "pending")]
        assert _state(po)["approval_status"] == "pending_approval"
        # only the FIRST level's person was mailed
        assert [m["to"] for m in _mails["request"]] == [
            query_one("SELECT email FROM users WHERE id = %s", (a_id,))["email"]]

        # level 2's person cannot jump the queue, and nothing changes
        wrong = _approve(client, b_h, po)
        assert wrong.status_code == 403 and wrong.json()["error_code"] == "po_approval_chain_wrong_level"
        assert [s["status"] for s in _steps(po)] == ["pending", "pending"]

        # level 1 approves: NOT approved yet, still unsendable
        first = _approve(client, a_h, po, "ok from me")
        assert first.status_code == 200 and first.json()["data"]["level_progress"] is True
        assert [(s["status"], s["decided_by"]) for s in _steps(po)] == [
            ("approved", a_id), ("pending", None)]
        assert query_one("SELECT status FROM po_approvals WHERE po_log_id = %s", (po,)
                         )["status"] == "requested"
        assert _state(po) == {"approval_status": "pending_approval", "approved_amount": None}
        with pytest.raises(AppError):
            svc.assert_sendable(tid, po_log_id=po)
        assert len(_events(tid, "purchase.approval_level_approved", po)) == 1
        assert _events(tid, "purchase.approval_approved", po) == []
        # the same click again is the same decision
        again = _approve(client, a_h, po)
        assert again.status_code == 200 and again.json()["data"]["changed"] is False
        assert len(_events(tid, "purchase.approval_level_approved", po)) == 1
        # the person who approved level 1 is also named... only for level 2 would be refused
        # level 2 approves: now it is approved and can go
        last = _approve(client, b_h, po)
        assert last.status_code == 200, last.text
        assert _state(po) == {"approval_status": "approved", "approved_amount": 6000.0}
        assert query_one("SELECT status, decided_by FROM po_approvals WHERE po_log_id = %s",
                         (po,)) == {"status": "approved", "decided_by": b_id}
        assert [s["status"] for s in _steps(po)] == ["approved", "approved"]
        svc.assert_sendable(tid, po_log_id=po)
        assert len(_events(tid, "purchase.approval_approved", po)) == 1

    def test_the_requester_never_approves_a_level_even_when_named(
            self, client, test_tenant):
        tid = test_tenant["id"]
        r_id, r_h = _person(client, tid, "analyst")
        b_id, _ = _person(client, tid, "analyst")
        _chain(tid, [(1, [_named(r_id, b_id)])])
        po = _po(tid)
        assert _req(client, r_h, po).status_code == 200
        resp = _approve(client, r_h, po)
        assert resp.status_code == 403 and resp.json()["error_code"] == "po_approval_self_approval"
        assert _steps(po)[0]["status"] == "pending"

    def test_one_person_never_signs_two_levels(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        x_id, x_h = _person(client, tid, "admin")
        y_id, y_h = _person(client, tid, "admin")
        _chain(tid, [(1, [ADMIN, ADMIN])])
        po = _po(tid)
        assert _req(client, analyst_headers, po).status_code == 200
        assert _approve(client, x_h, po).status_code == 200
        # x fits level 2 by role, but a second click is NOT a second signature
        again = _approve(client, x_h, po)
        assert again.status_code == 200 and again.json()["data"]["changed"] is False
        assert [(s["status"], s["decided_by"]) for s in _steps(po)] == [
            ("approved", x_id), ("pending", None)]
        assert _state(po)["approval_status"] == "pending_approval"
        d = client.get(f"/api/v1/inventory/po/{po}/approval", headers=x_h).json()["data"]
        assert d["can_decide"] is False                        # the screen offers x nothing
        inbox = client.get("/api/v1/inventory/po-approval/pending", headers=x_h).json()["data"]
        assert [i["po_log_id"] for i in inbox["items"] if i["can_decide"]] == []
        assert _approve(client, y_h, po).status_code == 200
        assert _state(po)["approval_status"] == "approved"
        assert query_one("SELECT decided_by FROM po_approvals WHERE po_log_id = %s", (po,)
                         )["decided_by"] == y_id

    def test_a_rejection_at_any_level_rejects_the_order(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        a_id, a_h, b_id, b_h = self._setup(client, tid)
        po = _po(tid)
        assert _req(client, analyst_headers, po).status_code == 200
        assert _approve(client, a_h, po).status_code == 200
        # a reason is required, and without one nothing changes
        bad = client.post(f"/api/v1/inventory/po/{po}/approval/reject", json={"comment": ""},
                          headers=b_h)
        assert bad.status_code == 422
        assert _steps(po)[1]["status"] == "pending"
        resp = _reject(client, b_h, po, "too expensive for this quarter")
        assert resp.status_code == 200, resp.text
        assert [s["status"] for s in _steps(po)] == ["approved", "rejected"]
        assert _state(po) == {"approval_status": "rejected", "approved_amount": None}
        assert query_one("SELECT status, decided_by FROM po_approvals WHERE po_log_id = %s",
                         (po,)) == {"status": "rejected", "decided_by": b_id}
        with pytest.raises(AppError):
            svc.assert_sendable(tid, po_log_id=po)
        assert len(_events(tid, "purchase.approval_rejected", po)) == 1

    def test_a_request_nobody_can_finish_is_refused_up_front(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        only, _ = _person(client, tid, "admin")
        # two admin levels but (besides the requester) only the admin created here and
        # the test tenant's own users: make the pool exactly one eligible admin
        execute("UPDATE users SET status = 'inactive' WHERE tenant_id = %s AND id <> %s "
                "AND role = 'admin'", (tid, only))
        _chain(tid, [(1, [ADMIN, ADMIN])])
        po = _po(tid)
        resp = _req(client, analyst_headers, po)
        assert resp.status_code == 409 and resp.json()["error_code"] == "po_approval_no_approver"
        assert resp.json()["error_params"]["level"] == 2
        assert query("SELECT 1 FROM po_approvals WHERE po_log_id = %s", (po,)) == []

    def test_a_role_level_accepts_that_role_or_above(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        adm_id, adm_h = _person(client, tid, "admin")
        _chain(tid, [(1, [ANALYST])])
        po = _po(tid)
        assert _req(client, analyst_headers, po).status_code == 200
        assert _approve(client, adm_h, po).status_code == 200
        assert _state(po)["approval_status"] == "approved"

    def test_a_viewer_cannot_decide_even_when_named(self, client, test_tenant, analyst_headers,
                                                    viewer_headers, viewer_user):
        tid = test_tenant["id"]
        v_id = viewer_user["user"]["id"] if "user" in viewer_user else viewer_user["id"]
        b_id, _ = _person(client, tid, "analyst")
        _chain(tid, [(1, [_named(v_id, b_id)])])
        po = _po(tid)
        assert _req(client, analyst_headers, po).status_code == 200
        resp = _approve(client, viewer_headers, po)
        assert resp.status_code == 403
        assert _steps(po)[0]["status"] == "pending"


class TestStaleness:

    def test_editing_the_chain_freezes_an_open_request_until_it_is_asked_again(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        a_id, a_h = _person(client, tid, "analyst")
        b_id, b_h = _person(client, tid, "analyst")
        chain = _chain(tid, [(1, [_named(a_id)])])
        po = _po(tid)
        assert _req(client, analyst_headers, po).status_code == 200
        first = query_one("SELECT id, chain_fingerprint FROM po_approvals WHERE po_log_id = %s", (po,))
        # an admin changes who approves while the request is open
        execute("UPDATE approval_chain_bands SET levels = %s::jsonb WHERE chain_id = %s",
                (json.dumps([_named(b_id)]), chain))
        stale = _approve(client, a_h, po)
        assert stale.status_code == 409 and stale.json()["error_code"] == "po_approval_chain_changed"
        assert _steps(po)[0]["status"] == "pending" and _state(po)["approval_status"] == "pending_approval"
        # asking again replaces the stale request (kept in the history) with a fresh one
        again = _req(client, analyst_headers, po)
        assert again.status_code == 200 and again.json()["data"]["changed"] is True
        rows = query("SELECT id, status, comment, chain_fingerprint FROM po_approvals "
                     "WHERE po_log_id = %s ORDER BY requested_at, id", (po,))
        assert len(rows) == 2
        assert rows[0]["id"] == first["id"] and rows[0]["status"] == "rejected"
        assert rows[0]["comment"] == "superseded:chain_changed"
        assert rows[1]["status"] == "requested" and rows[1]["chain_fingerprint"] != first["chain_fingerprint"]
        assert _approve(client, a_h, po).status_code == 403          # no longer the approver
        assert _approve(client, b_h, po).status_code == 200
        assert _state(po)["approval_status"] == "approved"

    def test_an_approval_made_under_other_terms_no_longer_counts(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        a_id, a_h = _person(client, tid, "analyst")
        chain = _chain(tid, [(1, [_named(a_id)])])
        po = _po(tid)
        assert _req(client, analyst_headers, po).status_code == 200
        assert _approve(client, a_h, po).status_code == 200
        svc.assert_sendable(tid, po_log_id=po)
        execute("UPDATE approval_chain_bands SET levels = %s::jsonb WHERE chain_id = %s",
                (json.dumps([ADMIN]), chain))
        with pytest.raises(AppError) as exc:
            svc.assert_sendable(tid, po_log_id=po)
        assert exc.value.code == "po_approval_required"
        assert exc.value.params["approval_status"] == "approval_needed"

    def test_an_order_approved_the_old_way_is_asked_again_when_a_chain_arrives(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        approver, _ = _person(client, tid, "analyst")
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (approver,))
        a_id, a_h = _person(client, tid, "analyst")
        rule = query_one("INSERT INTO po_approval_rules (tenant_id, threshold, created_by) "
                         "VALUES (%s, 100, 'test') RETURNING id", (tid,))["id"]
        po = _po(tid)
        _, ap_h = _person(client, tid, "analyst")
        execute("UPDATE users SET can_approve_po = TRUE WHERE tenant_id = %s AND role = 'analyst'", (tid,))
        assert _req(client, analyst_headers, po).status_code == 200
        assert _approve(client, ap_h, po).status_code == 200
        svc.assert_sendable(tid, po_log_id=po)
        _chain(tid, [(1, [_named(a_id)])])                   # a chain arrives afterwards
        with pytest.raises(AppError):
            svc.assert_sendable(tid, po_log_id=po)
        # and it can be asked again, under the chain
        assert _req(client, analyst_headers, po).status_code == 200
        assert _approve(client, a_h, po).status_code == 200
        svc.assert_sendable(tid, po_log_id=po)
        assert rule


class TestEscalationAndInbox:

    def test_an_order_that_went_past_a_budget_needs_the_top_band(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        a_id, a_h = _person(client, tid, "analyst")
        adm_id, adm_h = _person(client, tid, "admin")
        _chain(tid, [(1000, [_named(a_id)]), (100000, [_named(a_id), _named(adm_id)])])
        small = _po(tid, qty=2, cost=1000.0, escalate=True)       # 2000: band 1, escalated to band 2
        assert _req(client, analyst_headers, small).status_code == 200
        assert len(_steps(small)) == 2
        plain = _po(tid, qty=2, cost=1000.0)
        assert _req(client, analyst_headers, plain).status_code == 200
        assert len(_steps(plain)) == 1

    def test_the_inbox_shows_a_person_only_the_level_they_fit(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        a_id, a_h = _person(client, tid, "analyst")
        b_id, b_h = _person(client, tid, "analyst")
        _chain(tid, [(1, [_named(a_id), _named(b_id)])])
        po = _po(tid)
        assert _req(client, analyst_headers, po).status_code == 200

        def inbox(h):
            return client.get("/api/v1/inventory/po-approval/pending", headers=h).json()["data"]

        mine = inbox(a_h)
        assert mine["is_approver"] is True and [i["po_log_id"] for i in mine["items"]] == [po]
        assert mine["items"][0]["can_decide"] is True and mine["items"][0]["level_no"] == 1
        assert inbox(b_h)["items"] == []                       # level 2 is not open yet
        assert _approve(client, a_h, po).status_code == 200
        assert inbox(a_h)["items"] == []
        assert [i["po_log_id"] for i in inbox(b_h)["items"]] == [po]

    def test_describe_carries_steps_and_can_decide_for_the_open_level(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        a_id, a_h = _person(client, tid, "analyst")
        _chain(tid, [(1, [_named(a_id)])])
        po = _po(tid)
        assert _req(client, analyst_headers, po).status_code == 200
        d = client.get(f"/api/v1/inventory/po/{po}/approval", headers=a_h).json()["data"]
        assert d["can_decide"] is True and d["status"] == "pending_approval"
        assert d["open_request"]["steps"][0]["level"]["users"][0]["id"] == a_id
        d2 = client.get(f"/api/v1/inventory/po/{po}/approval", headers=analyst_headers).json()["data"]
        assert d2["can_decide"] is False


class TestAttributionAndBudget:

    def test_log_po_attributes_the_center_and_refuses_a_bad_one(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        ops = _center(tid, "OPS")
        old = _center(tid, "OLD", active=False)
        body = {"cost_center_id": ops, "items": [{
            "sku": "S1", "supplier": "Acme", "signal": "PEDIR_YA", "recommended_qty": 3,
            "final_qty": 3, "unit_cost": 10.0, "status": "approved"}]}
        ok = client.post(LOG_PO, params={"session_id": "sess_x1"}, json=body, headers=analyst_headers)
        assert ok.status_code == 201, ok.text
        po = ok.json()["data"]["id"]
        assert query_one("SELECT cost_center_id, chain_escalate FROM inventory_po_log WHERE id = %s",
                         (po,)) == {"cost_center_id": ops, "chain_escalate": False}
        before = query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s", (tid,))["n"]
        for center, status, code in ((old, 409, "cost_center_inactive"),
                                     ("ghost", 404, "cost_center_not_found")):
            bad = client.post(LOG_PO, params={"session_id": "sess_x2"},
                              json={**body, "cost_center_id": center}, headers=analyst_headers)
            assert bad.status_code == status and bad.json()["error_code"] == code
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
                         (tid,))["n"] == before

    def test_another_tenants_center_does_not_exist(self, client, analyst_headers, test_tenant,
                                                   make_tenant_user_headers):
        _, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        foreign = _center(other_tid, "OPS")
        resp = client.post(LOG_PO, params={"session_id": "sess_x3"}, headers=analyst_headers,
                           json={"cost_center_id": foreign, "items": [{
                               "sku": "S1", "supplier": "Acme", "signal": "PEDIR_YA",
                               "recommended_qty": 1, "final_qty": 1, "unit_cost": 1.0,
                               "status": "approved"}]})
        assert resp.status_code == 404 and resp.json()["error_code"] == "cost_center_not_found"

    def test_a_cost_center_budget_covers_its_children_and_a_hard_cap_still_refuses(
            self, client, analyst_headers, auth_headers, test_tenant):
        tid = test_tenant["id"]
        parent = _center(tid, "ROOT")
        child = _center(tid, "KID", parent=parent)
        other = _center(tid, "OTHER")
        made = client.post(BUDGETS, headers=analyst_headers, json={
            "period_type": "month", "period_start": date.today().isoformat(), "amount": 1000.0,
            "scope_type": "cost_center", "scope_value": parent, "hard_cap": True})
        assert made.status_code == 201, made.text
        assert query_one("SELECT scope_type, scope_value FROM purchase_budgets WHERE tenant_id = %s",
                         (tid,)) == {"scope_type": "cost_center", "scope_value": parent}

        def order(center, qty, reason=None, headers=analyst_headers):
            body = {"cost_center_id": center, "items": [{
                "sku": f"K-{uuid4().hex[:4]}", "supplier": "Acme", "signal": "PEDIR_YA",
                "recommended_qty": qty, "final_qty": qty, "unit_cost": 10.0, "status": "approved"}]}
            if reason:
                body["budget_override_reason"] = reason
            return client.post(LOG_PO, params={"session_id": f"s_{uuid4().hex[:5]}"}, json=body,
                               headers=headers)

        assert order(child, 60).status_code == 201                  # 600 of 1000, via the child
        assert order(other, 500).status_code == 201                 # another center: not counted
        refused = order(child, 60)                                  # 600 more would be 1200
        assert refused.status_code == 409 and refused.json()["error_code"] == "purchase_budget_hard_cap"
        assert query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s "
                         "AND cost_center_id = %s", (tid, child))["n"] == 1
        overridden = order(child, 60, reason="launch", headers=auth_headers)
        assert overridden.status_code == 201, overridden.text
        po = overridden.json()["data"]["id"]
        assert query_one("SELECT chain_escalate FROM inventory_po_log WHERE id = %s", (po,)
                         )["chain_escalate"] is True               # over budget: top band
        assert len(query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                         "AND action = 'purchase_budget.override'", (tid,))) == 1

    def test_a_budget_for_an_inactive_or_foreign_center_is_refused(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        old = _center(tid, "OLD", active=False)
        for value in (old, "ghost"):
            r = client.post(BUDGETS, headers=analyst_headers, json={
                "period_type": "month", "period_start": date.today().isoformat(),
                "amount": 10.0, "scope_type": "cost_center", "scope_value": value})
            assert r.status_code == 422
            assert r.json()["error_code"] == "purchase_budget_scope_not_found"
        assert query("SELECT 1 FROM purchase_budgets WHERE tenant_id = %s", (tid,)) == []

    def test_a_cost_center_budget_is_labelled_with_its_center(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        ops = _center(tid, "OPS")
        made = client.post(BUDGETS, headers=analyst_headers, json={
            "period_type": "month", "period_start": date.today().isoformat(),
            "amount": 50.0, "scope_type": "cost_center", "scope_value": ops})
        assert made.status_code == 201
        st = client.get("/api/v1/inventory/budget/status", headers=analyst_headers).json()["data"]
        assert st["budget"]["scope_label"] == "OPS Center OPS"
        assert st["budgets"][0]["scope_type"] == "cost_center"


def test_the_new_tables_are_in_the_export_and_erase_lists():
    from backend.tenants import data_export
    exported = {t for _, t, _ in data_export._EXPORT_SPECS}
    for table in ("cost_centers", "approval_chains", "approval_chain_bands", "po_approval_steps"):
        assert table in exported and table in data_export._DELETE_ORDER
    order = data_export._DELETE_ORDER
    assert order.index("po_approval_steps") < order.index("po_approvals")
    assert order.index("approval_chains") < order.index("cost_centers")
    assert order.index("approval_chain_bands") < order.index("approval_chains")
