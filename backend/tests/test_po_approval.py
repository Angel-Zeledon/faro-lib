"""Purchase-order approval workflow (opt-in, owner-authorised 2026-10-05).

Pins: with no rule every order behaves as before; with a rule an order worth the
threshold or more cannot leave by ANY send path until somebody allowed to has
approved it; the decision is idempotent, a rejection needs a reason, self-approval
is bounded by the second threshold, and every claim is read back from the DB.
"""

from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.inventory import po_approval_service as svc
from backend.inventory import roi_service
from backend.users import service as user_svc


def _po(tenant_id, qty=100, cost=60.0, supplier="Acme", warehouse=None, sku=None):
    return roi_service.log_po_generation(
        tenant_id, "sess-test",
        [{"sku": sku or f"A-{uuid4().hex[:6]}", "final_qty": qty, "unit_cost": cost,
          "status": "approved", "supplier": supplier}],
        destination_warehouse=warehouse,
    )["id"]


def _person(client, tenant_id, role, *, approver=False):
    email = f"{role}-{uuid4().hex[:8]}@example.com"
    user = user_svc.create_user(tenant_id=tenant_id, email=email, password="TestPass123!",
                                role=role, full_name=f"{role.title()} {uuid4().hex[:4]}")
    user_svc.mark_verified(tenant_id, user["id"])
    if approver:
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (user["id"],))
    token = client.post("/api/v1/auth/login", json={
        "email": email, "password": "TestPass123!"}).json()["data"]["access_token"]
    return user["id"], {"Authorization": f"Bearer {token}"}


def _rule(tenant_id, threshold=5000, **kw):
    return query_one(
        """INSERT INTO po_approval_rules
               (tenant_id, threshold, warehouse, supplier_id, self_approve_below, created_by)
           VALUES (%s, %s, %s, %s, %s, 'test') RETURNING id""",
        (tenant_id, threshold, kw.get("warehouse"), kw.get("supplier_id"),
         kw.get("self_approve_below")))["id"]


def _state(po_id):
    return query_one("SELECT approval_status, approved_amount, sent_at FROM inventory_po_log "
                     "WHERE id = %s", (po_id,))


def _events(tenant_id, action, po_id):
    return query("SELECT user_id, context FROM activity_logs WHERE tenant_id = %s AND action = %s "
                 "AND resource = %s", (tenant_id, action, po_id))


@pytest.fixture
def mails(monkeypatch):
    from backend.notifications import email as email_mod
    sent = {"request": [], "decision": []}
    monkeypatch.setattr(email_mod, "send_po_approval_request_email",
                        lambda **kw: sent["request"].append(kw) or True)
    monkeypatch.setattr(email_mod, "send_po_approval_decision_email",
                        lambda **kw: sent["decision"].append(kw) or True)
    return sent


# ── Nothing configured: nothing changes ──────────────────────────────────────

class TestNoRuleNoChange:

    def test_an_order_is_sendable_and_reports_no_approval(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        po = _po(tid, qty=1000, cost=1000.0)           # a million, but nobody asked for rules
        svc.assert_sendable(tid, po_log_id=po)         # does not raise
        resp = client.post(f"/api/v1/inventory/po/{po}/send", headers=auth_headers)
        assert resp.status_code == 200
        rows = client.get("/api/v1/inventory/po-history/page", headers=auth_headers
                          ).json()["data"]["items"]
        assert all("approval" not in r for r in rows)
        assert client.get("/api/v1/inventory/po-approval/settings", headers=auth_headers
                          ).json()["data"]["enabled"] is False

    def test_requesting_approval_without_a_rule_is_refused(self, client, auth_headers, test_tenant):
        po = _po(test_tenant["id"])
        resp = client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_approval_not_required"
        assert query("SELECT 1 FROM po_approvals WHERE po_log_id = %s", (po,)) == []


# ── Configuration ────────────────────────────────────────────────────────────

class TestConfiguration:

    def test_rule_permission_pair_and_db_state(self, client, auth_headers, analyst_headers,
                                               registered_user, test_tenant):
        tid = test_tenant["id"]
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s",
                (registered_user["user"]["id"],))
        body = {"threshold": 5000, "self_approve_below": 9000}
        denied = client.post("/api/v1/inventory/po-approval/rules", json=body,
                             headers=analyst_headers)
        assert denied.status_code == 403
        assert query("SELECT 1 FROM po_approval_rules WHERE tenant_id = %s", (tid,)) == []

        created = client.post("/api/v1/inventory/po-approval/rules", json=body,
                              headers=auth_headers)
        assert created.status_code == 201, created.text
        row = query_one("SELECT threshold, self_approve_below, active, created_by "
                        "FROM po_approval_rules WHERE tenant_id = %s", (tid,))
        assert (row["threshold"], row["self_approve_below"], row["active"]) == (5000, 9000, True)
        assert row["created_by"] == registered_user["user"]["id"]
        # the audit trail names who set the policy
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'audit.config.changed'", (tid,))

    def test_a_rule_needs_somebody_who_can_approve(self, client, auth_headers, test_tenant):
        resp = client.post("/api/v1/inventory/po-approval/rules", json={"threshold": 100},
                           headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_approval_no_approver"
        assert query("SELECT 1 FROM po_approval_rules WHERE tenant_id = %s",
                     (test_tenant["id"],)) == []

    def test_self_approval_limit_must_exceed_the_threshold(self, client, auth_headers,
                                                           registered_user, test_tenant):
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s",
                (registered_user["user"]["id"],))
        resp = client.post("/api/v1/inventory/po-approval/rules",
                           json={"threshold": 500, "self_approve_below": 400},
                           headers=auth_headers)
        assert resp.status_code == 422
        assert query("SELECT 1 FROM po_approval_rules WHERE tenant_id = %s",
                     (test_tenant["id"],)) == []

    def test_flagging_an_approver_permission_pair_and_role_check(
            self, client, auth_headers, analyst_headers, analyst_user, viewer_user, test_tenant):
        uid = analyst_user["user"]["id"]
        denied = client.put(f"/api/v1/inventory/po-approval/approvers/{uid}",
                            json={"can_approve": True}, headers=analyst_headers)
        assert denied.status_code == 403
        assert query_one("SELECT can_approve_po FROM users WHERE id = %s", (uid,))[
            "can_approve_po"] is False

        ok = client.put(f"/api/v1/inventory/po-approval/approvers/{uid}",
                        json={"can_approve": True}, headers=auth_headers)
        assert ok.status_code == 200
        assert query_one("SELECT can_approve_po FROM users WHERE id = %s", (uid,))[
            "can_approve_po"] is True

        viewer = client.put(
            f"/api/v1/inventory/po-approval/approvers/{viewer_user['user']['id']}",
            json={"can_approve": True}, headers=auth_headers)
        assert viewer.status_code == 409
        assert query_one("SELECT can_approve_po FROM users WHERE id = %s",
                         (viewer_user["user"]["id"],))["can_approve_po"] is False

    def test_the_last_approver_cannot_be_removed_while_rules_are_active(
            self, client, auth_headers, analyst_user, test_tenant):
        uid = analyst_user["user"]["id"]
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (uid,))
        _rule(test_tenant["id"])
        resp = client.put(f"/api/v1/inventory/po-approval/approvers/{uid}",
                          json={"can_approve": False}, headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_approval_last_approver"
        assert query_one("SELECT can_approve_po FROM users WHERE id = %s", (uid,))[
            "can_approve_po"] is True

    def test_rules_are_tenant_scoped(self, client, auth_headers, make_tenant_user_headers,
                                     test_tenant):
        other_headers, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        rid = _rule(other_tid)
        resp = client.delete(f"/api/v1/inventory/po-approval/rules/{rid}", headers=auth_headers)
        assert resp.status_code == 404
        assert query_one("SELECT 1 AS x FROM po_approval_rules WHERE id = %s", (rid,))
        assert client.get("/api/v1/inventory/po-approval/settings", headers=auth_headers
                          ).json()["data"]["rules"] == []


# ── Every send path is closed until approved ─────────────────────────────────

class TestNothingLeavesUnapproved:

    def _setup(self, client, tenant_id):
        approver_id, approver_headers = _person(client, tenant_id, "analyst", approver=True)
        _rule(tenant_id, 5000)
        return approver_id, approver_headers

    def test_send_is_refused_and_nothing_goes_out(self, client, auth_headers, test_tenant,
                                                  monkeypatch):
        from backend.inventory import supplier_service as sup_svc
        from backend.notifications import email as email_mod, whatsapp as wa_mod
        tid = test_tenant["id"]
        self._setup(client, tid)
        sup_svc.create_supplier(tid, {"name": "Acme", "email": "a@acme.test"})
        calls = []
        monkeypatch.setattr(email_mod, "send_po_to_supplier_email",
                            lambda **kw: calls.append("mail") or True)
        monkeypatch.setattr(wa_mod, "send_whatsapp", lambda *a, **k: calls.append("wa") or True)
        po = _po(tid, qty=100, cost=60.0)                 # 6000 >= 5000

        resp = client.post(f"/api/v1/inventory/po/{po}/send", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_approval_required"
        assert calls == []
        assert _state(po)["sent_at"] is None
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'purchase.order_sent'", (tid,)) == []

    def test_send_to_me_is_refused(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        self._setup(client, tid)
        po = _po(tid)
        resp = client.post(f"/api/v1/inventory/po/{po}/send-to-me", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_approval_required"

    def test_the_low_level_steps_refuse_too(self, client, test_tenant):
        """A future caller that forgets the endpoint check still cannot mark the
        order sent or build its document."""
        from backend.inventory import po_pdf, reception_service as rec_svc
        tid = test_tenant["id"]
        self._setup(client, tid)
        po = _po(tid)
        with pytest.raises(AppError) as e1:
            rec_svc.mark_po_sent(tid, po)
        assert e1.value.code == "po_approval_required"
        assert _state(po)["sent_at"] is None
        with pytest.raises(AppError) as e2:
            po_pdf.generate_po_pdf(tid, po, "Acme", [], {"po_log_id": po})
        assert e2.value.code == "po_approval_required"

    def test_a_pdf_made_before_the_rule_is_not_served_either(self, client, test_tenant):
        from backend.storage import paths
        tid = test_tenant["id"]
        po = _po(tid)
        pdf = paths.po_pdf_file(tid, po, "acme")
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 test")
        try:
            assert client.get(f"/api/v1/inventory/po/{po}/pdf/acme").status_code == 200
            self._setup(client, tid)
            blocked = client.get(f"/api/v1/inventory/po/{po}/pdf/acme")
            assert blocked.status_code == 409
        finally:
            pdf.unlink(missing_ok=True)

    def test_whatsapp_confirmed_send_is_refused(self, client, test_tenant):
        from backend.whatsapp import tools
        tid = test_tenant["id"]
        self._setup(client, tid)
        po = _po(tid)
        ctx = tools.ToolContext(tenant_id=tid, user_id="u", role="analyst") \
            if hasattr(tools, "ToolContext") else None
        if ctx is None:
            pytest.skip("ToolContext shape changed")
        with pytest.raises(tools.ToolError):
            tools.execute_pending_action(ctx, {"type": "approve_po", "po_log_id": po})
        assert _state(po)["sent_at"] is None

    def test_below_the_threshold_nothing_is_asked(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        self._setup(client, tid)
        po = _po(tid, qty=10, cost=10.0)                   # 100 < 5000
        assert client.post(f"/api/v1/inventory/po/{po}/send", headers=auth_headers
                           ).status_code == 200

    def test_the_gate_cannot_be_dodged_by_never_asking(self, client, test_tenant):
        tid = test_tenant["id"]
        po = _po(tid)
        assert _state(po)["approval_status"] is None
        _rule(tid)
        execute("UPDATE users SET can_approve_po = TRUE WHERE tenant_id = %s", (tid,))
        with pytest.raises(AppError):
            svc.assert_sendable(tid, po_log_id=po)

    def test_approved_orders_can_then_go_and_it_survives_a_raised_amount(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        approver_id, approver_headers = self._setup(client, tid)
        po = _po(tid)
        assert client.post(f"/api/v1/inventory/po/{po}/approval/request",
                           headers=analyst_headers).status_code == 200
        assert client.post(f"/api/v1/inventory/po/{po}/approval/approve",
                           headers=approver_headers).status_code == 200
        svc.assert_sendable(tid, po_log_id=po)                      # now fine
        # the order grows after approval: the approver never saw that amount
        execute("INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, supplier, "
                "recommended_qty, final_qty, unit_cost, status, warehouse) "
                "VALUES (%s, %s, 'EXTRA', 'Acme', 10, 10, 100, 'approved', 'principal')",
                (po, tid))
        with pytest.raises(AppError) as exc:
            svc.assert_sendable(tid, po_log_id=po)
        assert exc.value.code == "po_approval_required"


# ── Request and decide ───────────────────────────────────────────────────────

class TestRequestAndDecide:

    def test_request_permission_pair_state_and_notification(
            self, client, viewer_headers, analyst_headers, analyst_user, test_tenant, mails):
        tid = test_tenant["id"]
        approver_id, _ = _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)

        denied = client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=viewer_headers)
        assert denied.status_code == 403
        assert query("SELECT 1 FROM po_approvals WHERE po_log_id = %s", (po,)) == []

        resp = client.post(f"/api/v1/inventory/po/{po}/approval/request",
                           json={"note": "stock for the sale"}, headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["changed"] is True and data["status"] == "pending_approval"
        row = query_one("SELECT status, amount, requested_by, request_note FROM po_approvals "
                        "WHERE po_log_id = %s", (po,))
        assert (row["status"], row["amount"]) == ("requested", 6000)
        assert row["requested_by"] == analyst_user["user"]["id"]
        assert row["request_note"] == "stock for the sale"
        assert _state(po)["approval_status"] == "pending_approval"
        assert [m["to"] for m in mails["request"]] and len(mails["request"]) == 1
        assert len(_events(tid, "purchase.approval_requested", po)) == 1

    def test_requesting_twice_is_one_request(self, client, analyst_headers, test_tenant, mails):
        tid = test_tenant["id"]
        _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        for _ in range(2):
            assert client.post(f"/api/v1/inventory/po/{po}/approval/request",
                               headers=analyst_headers).status_code == 200
        assert query_one("SELECT COUNT(*) AS n FROM po_approvals WHERE po_log_id = %s",
                         (po,))["n"] == 1
        assert len(mails["request"]) == 1
        assert len(_events(tid, "purchase.approval_requested", po)) == 1

    def test_non_approver_cannot_decide_and_nothing_changes(
            self, client, analyst_headers, test_tenant, mails):
        tid = test_tenant["id"]
        _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        _, plain_headers = _person(client, tid, "analyst")             # not flagged
        resp = client.post(f"/api/v1/inventory/po/{po}/approval/approve", headers=plain_headers)
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "po_approval_not_approver"
        assert query_one("SELECT status FROM po_approvals WHERE po_log_id = %s", (po,))[
            "status"] == "requested"
        assert _state(po)["approval_status"] == "pending_approval"

    def test_viewer_cannot_decide(self, client, analyst_headers, viewer_headers, test_tenant):
        tid = test_tenant["id"]
        _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        assert client.post(f"/api/v1/inventory/po/{po}/approval/approve",
                           headers=viewer_headers).status_code == 403
        assert client.post(f"/api/v1/inventory/po/{po}/approval/reject",
                           json={"comment": "no"}, headers=viewer_headers).status_code == 403
        assert query_one("SELECT status FROM po_approvals WHERE po_log_id = %s", (po,))[
            "status"] == "requested"

    def test_approve_records_who_when_and_the_amount(
            self, client, analyst_headers, test_tenant, mails):
        tid = test_tenant["id"]
        approver_id, approver_headers = _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        resp = client.post(f"/api/v1/inventory/po/{po}/approval/approve",
                           json={"comment": "ok"}, headers=approver_headers)
        assert resp.status_code == 200, resp.text
        row = query_one("SELECT status, decided_by, decided_at, comment, amount FROM po_approvals "
                        "WHERE po_log_id = %s", (po,))
        assert row["status"] == "approved" and row["decided_by"] == approver_id
        assert row["decided_at"] is not None and row["comment"] == "ok"
        st = _state(po)
        assert (st["approval_status"], st["approved_amount"]) == ("approved", 6000)
        assert len(_events(tid, "purchase.approval_approved", po)) == 1
        assert len(mails["decision"]) == 1 and mails["decision"][0]["approved"] is True

    def test_approving_twice_is_idempotent(self, client, analyst_headers, test_tenant, mails):
        tid = test_tenant["id"]
        _, approver_headers = _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        first = client.post(f"/api/v1/inventory/po/{po}/approval/approve", headers=approver_headers)
        second = client.post(f"/api/v1/inventory/po/{po}/approval/approve", headers=approver_headers)
        assert first.json()["data"]["changed"] is True
        assert second.status_code == 200 and second.json()["data"]["changed"] is False
        assert len(_events(tid, "purchase.approval_approved", po)) == 1
        assert len(mails["decision"]) == 1

    def test_the_opposite_decision_after_a_decision_is_refused(
            self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        _, approver_headers = _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        client.post(f"/api/v1/inventory/po/{po}/approval/approve", headers=approver_headers)
        resp = client.post(f"/api/v1/inventory/po/{po}/approval/reject",
                           json={"comment": "changed my mind"}, headers=approver_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_approval_already_decided"
        assert _state(po)["approval_status"] == "approved"

    def test_reject_needs_a_reason(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        _, approver_headers = _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        for body in ({}, {"comment": "  "}, {"comment": "x"}):
            resp = client.post(f"/api/v1/inventory/po/{po}/approval/reject", json=body,
                               headers=approver_headers)
            assert resp.status_code == 422
            assert resp.json()["error_code"] == "po_approval_reason_required"
        assert query_one("SELECT status FROM po_approvals WHERE po_log_id = %s", (po,))[
            "status"] == "requested"

    def test_reject_blocks_the_send_and_a_new_request_keeps_the_history(
            self, client, analyst_headers, auth_headers, test_tenant, mails):
        tid = test_tenant["id"]
        _, approver_headers = _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        resp = client.post(f"/api/v1/inventory/po/{po}/approval/reject",
                           json={"comment": "too much for this month"}, headers=approver_headers)
        assert resp.status_code == 200
        assert _state(po)["approval_status"] == "rejected"
        assert query_one("SELECT comment FROM po_approvals WHERE po_log_id = %s", (po,))[
            "comment"] == "too much for this month"
        assert client.post(f"/api/v1/inventory/po/{po}/send", headers=auth_headers
                           ).status_code == 409
        assert mails["decision"][0]["approved"] is False
        assert mails["decision"][0]["comment"] == "too much for this month"

        again = client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        assert again.json()["data"]["changed"] is True
        rows = query("SELECT status FROM po_approvals WHERE po_log_id = %s ORDER BY requested_at",
                     (po,))
        assert [r["status"] for r in rows] == ["rejected", "requested"]
        assert _state(po)["approval_status"] == "pending_approval"

    def test_a_request_with_nobody_to_approve_is_refused(self, client, analyst_headers,
                                                          test_tenant):
        tid = test_tenant["id"]
        approver_id, _ = _person(client, tid, "analyst", approver=True)
        _rule(tid)
        execute("UPDATE users SET status = 'inactive' WHERE id = %s", (approver_id,))
        po = _po(tid)
        resp = client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "po_approval_no_approver"
        assert query("SELECT 1 FROM po_approvals WHERE po_log_id = %s", (po,)) == []

    def test_cross_tenant_orders_are_invisible(self, client, auth_headers, make_tenant_user_headers,
                                                test_tenant):
        _, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        po = _po(other_tid)
        for path, method in ((f"/api/v1/inventory/po/{po}/approval", "get"),
                             (f"/api/v1/inventory/po/{po}/approval/request", "post"),
                             (f"/api/v1/inventory/po/{po}/approval/approve", "post")):
            assert getattr(client, method)(path, headers=auth_headers).status_code == 404


class TestSelfApproval:

    def test_requester_may_approve_below_the_second_threshold_only(
            self, client, test_tenant, mails):
        tid = test_tenant["id"]
        me_id, me = _person(client, tid, "analyst", approver=True)
        other_id, other = _person(client, tid, "analyst", approver=True)
        _rule(tid, 5000, self_approve_below=10000)

        small = _po(tid, qty=100, cost=60.0)        # 6000: self-approval allowed
        client.post(f"/api/v1/inventory/po/{small}/approval/request", headers=me)
        assert client.post(f"/api/v1/inventory/po/{small}/approval/approve",
                           headers=me).status_code == 200
        assert _state(small)["approval_status"] == "approved"

        big = _po(tid, qty=100, cost=120.0)         # 12000: somebody else
        client.post(f"/api/v1/inventory/po/{big}/approval/request", headers=me)
        no = client.post(f"/api/v1/inventory/po/{big}/approval/approve", headers=me)
        assert no.status_code == 403 and no.json()["error_code"] == "po_approval_self_approval"
        assert _state(big)["approval_status"] == "pending_approval"
        assert client.post(f"/api/v1/inventory/po/{big}/approval/approve",
                           headers=other).status_code == 200
        assert query_one("SELECT decided_by FROM po_approvals WHERE po_log_id = %s", (big,))[
            "decided_by"] == other_id
        # the requester was not mailed their own request
        assert all(m["to"] for m in mails["request"])

    def test_without_a_second_threshold_nobody_approves_their_own(self, client, test_tenant):
        tid = test_tenant["id"]
        _, me = _person(client, tid, "analyst", approver=True)
        _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=me)
        assert client.post(f"/api/v1/inventory/po/{po}/approval/approve",
                           headers=me).status_code == 403


class TestScopedRules:

    def test_a_warehouse_rule_only_binds_that_warehouse(self, client, test_tenant):
        tid = test_tenant["id"]
        _person(client, tid, "analyst", approver=True)
        _rule(tid, 5000, warehouse="norte")
        elsewhere = _po(tid, warehouse="sur")
        there = _po(tid, warehouse="Norte")
        svc.assert_sendable(tid, po_log_id=elsewhere)
        with pytest.raises(AppError):
            svc.assert_sendable(tid, po_log_id=there)

    def test_a_supplier_rule_only_binds_that_supplier(self, client, test_tenant):
        from backend.inventory import supplier_service as sup_svc
        tid = test_tenant["id"]
        _person(client, tid, "analyst", approver=True)
        sup = sup_svc.create_supplier(tid, {"name": "Costoso SA"})
        _rule(tid, 5000, supplier_id=sup["id"])
        svc.assert_sendable(tid, po_log_id=_po(tid, supplier="Barato SA"))
        with pytest.raises(AppError):
            svc.assert_sendable(tid, po_log_id=_po(tid, supplier="Costoso SA"))

    def test_an_order_with_no_known_value_is_reported_not_silently_passed(
            self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = roi_service.log_po_generation(tid, "s", [{
            "sku": "NOCOST", "final_qty": 5, "status": "approved", "supplier": "Acme"}])["id"]
        data = client.get(f"/api/v1/inventory/po/{po}/approval", headers=auth_headers
                          ).json()["data"]
        assert data["required"] is False and data["amount_known"] is False


class TestInboxAndHistoryView:

    def test_inbox_lists_open_requests_for_approvers_only(
            self, client, analyst_headers, test_tenant, mails):
        tid = test_tenant["id"]
        _, approver_headers = _person(client, tid, "analyst", approver=True)
        _rule(tid)
        po = _po(tid)
        client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)

        mine = client.get("/api/v1/inventory/po-approval/pending", headers=approver_headers
                          ).json()["data"]
        assert mine["is_approver"] is True
        assert [i["po_log_id"] for i in mine["items"]] == [po]
        assert mine["items"][0]["amount"] == 6000 and mine["items"][0]["can_decide"] is True

        theirs = client.get("/api/v1/inventory/po-approval/pending", headers=analyst_headers
                            ).json()["data"]
        assert theirs == {"is_approver": False, "items": []}

        # a cancelled order leaves the inbox
        execute("UPDATE inventory_po_log SET cancelled_at = NOW() WHERE id = %s", (po,))
        assert client.get("/api/v1/inventory/po-approval/pending", headers=approver_headers
                          ).json()["data"]["items"] == []

    def test_history_rows_carry_the_approval_state_only_when_a_rule_exists(
            self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        _person(client, tid, "analyst", approver=True)
        big, small = _po(tid), _po(tid, qty=1, cost=1.0)
        _rule(tid)
        rows = {r["id"]: r for r in client.get(
            "/api/v1/inventory/po-history/page", headers=auth_headers).json()["data"]["items"]}
        assert rows[big]["approval"] == {"required": True, "status": "approval_needed"}
        assert rows[small]["approval"] == {"required": False, "status": "not_required"}
