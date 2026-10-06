"""Purchase-order approval delegation: the Python DECISION path.

Delegations are created, listed and revoked by the Rust service (no Python
route), so these tests write the `po_approval_delegations` rows directly, the
same rows Rust writes, and then drive the Python routes that still serve the
decision. Every refusal is asserted with its code AND with the database
unchanged (the request still open, the order still pending); every allowed
decision is read back from `po_approvals`, `inventory_po_log` and the activity
log.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query, query_one
from backend.inventory import po_approval_service as svc
from backend.inventory import roi_service
from backend.inventory import warehouse_service
from backend.users import service as user_svc

APPROVAL = "/api/v1/inventory/po/{po}/approval"
PENDING = "/api/v1/inventory/po-approval/pending"


def _po(tid, qty=100, cost=60.0, warehouse=None):
    return roi_service.log_po_generation(
        tid, "sess-test",
        [{"sku": f"A-{uuid4().hex[:6]}", "final_qty": qty, "unit_cost": cost,
          "status": "approved", "supplier": "Acme"}],
        destination_warehouse=warehouse,
    )["id"]


def _person(tid, role, *, approver=False, name=None, scope=None):
    email = f"{role}-{uuid4().hex[:8]}@example.com"
    user = user_svc.create_user(tenant_id=tid, email=email, password="TestPass123!",
                                role=role, full_name=name or f"{role.title()} {uuid4().hex[:4]}")
    user_svc.mark_verified(tid, user["id"])
    if approver:
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (user["id"],))
    if scope is not None:
        execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                (json.dumps(scope), user["id"]))
    token = create_access_token(user["id"], tid, role, email_verified=True)
    return user, {"Authorization": f"Bearer {token}"}


def _delegation(tid, delegator_id, delegate_id, start=-1, end=5, revoked=False):
    """A delegation row, `start` / `end` in days from today (UTC)."""
    return query_one(
        """INSERT INTO po_approval_delegations
               (tenant_id, delegator_id, delegate_id, starts_on, ends_on, created_by, revoked_at)
           VALUES (%s, %s, %s,
                   (NOW() AT TIME ZONE 'UTC')::date + %s, (NOW() AT TIME ZONE 'UTC')::date + %s,
                   %s, CASE WHEN %s THEN NOW() ELSE NULL END) RETURNING id""",
        (tid, delegator_id, delegate_id, start, end, delegator_id, revoked))["id"]


def _rule(tid, threshold=5000, self_approve_below=None):
    return query_one(
        """INSERT INTO po_approval_rules (tenant_id, threshold, self_approve_below, created_by)
           VALUES (%s, %s, %s, 'test') RETURNING id""",
        (tid, threshold, self_approve_below))["id"]


def _request(client, headers, po):
    r = client.post(APPROVAL.format(po=po) + "/request", headers=headers)
    assert r.status_code == 200, r.text
    return r


def _state(po):
    return query_one("SELECT approval_status, approved_amount FROM inventory_po_log WHERE id = %s",
                     (po,))


def _row(po):
    return query_one("SELECT * FROM po_approvals WHERE po_log_id = %s", (po,))


def _assert_untouched(po):
    row = _row(po)
    assert row["status"] == "requested"
    assert row["decided_by"] is None and row["decided_on_behalf_of"] is None
    assert row["delegation_id"] is None
    assert _state(po)["approval_status"] == "pending_approval"


@pytest.fixture(autouse=True)
def _quiet_mail(monkeypatch):
    from backend.notifications import email as email_mod
    monkeypatch.setattr(email_mod, "send_po_approval_request_email", lambda **kw: True)
    monkeypatch.setattr(email_mod, "send_po_approval_decision_email", lambda **kw: True)


@pytest.fixture
def world(client, test_tenant):
    tid = test_tenant["id"]
    w = type("W", (), {})()
    w.client, w.tid = client, tid
    w.approver, w.approver_h = _person(tid, "analyst", approver=True, name="Ana Approver")
    w.requester, w.requester_h = _person(tid, "analyst", name="Rita Requester")
    w.delegate, w.delegate_h = _person(tid, "analyst", name="Dani Delegate")
    _rule(tid)
    w.po = _po(tid)
    _request(client, w.requester_h, w.po)
    return w


def _decide(world, headers, kind="approve", comment=None):
    body = {"comment": comment} if comment is not None else {}
    return world.client.post(APPROVAL.format(po=world.po) + f"/{kind}", headers=headers, json=body)


# ── A delegate decides ───────────────────────────────────────────────────────

class TestDelegateDecides:

    def test_the_delegate_approves_and_both_people_are_recorded(self, world):
        did = _delegation(world.tid, world.approver["id"], world.delegate["id"])
        r = _decide(world, world.delegate_h, comment="covering")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["changed"] is True
        assert r.json()["data"]["on_behalf_of_name"] == "Ana Approver"

        row = _row(world.po)
        assert row["status"] == "approved"
        assert row["decided_by"] == world.delegate["id"]
        assert row["decided_on_behalf_of"] == world.approver["id"]
        assert row["delegation_id"] == did
        assert _state(world.po)["approval_status"] == "approved"

        ev = query_one("SELECT user_id, context FROM activity_logs WHERE tenant_id = %s "
                       "AND action = 'purchase.approval_approved' AND resource = %s",
                       (world.tid, world.po))
        assert ev["user_id"] == world.delegate["id"]
        assert ev["context"]["on_behalf_of"] == "Ana Approver"

        # the order's history names both people
        hist = world.client.get(APPROVAL.format(po=world.po), headers=world.delegate_h
                                ).json()["data"]["history"][0]
        assert hist["decided_by_name"] == "Dani Delegate"
        assert hist["decided_on_behalf_of_name"] == "Ana Approver"

    def test_the_delegate_rejects_on_behalf_with_a_reason(self, world):
        _delegation(world.tid, world.approver["id"], world.delegate["id"])
        r = _decide(world, world.delegate_h, "reject", comment="wrong supplier")
        assert r.status_code == 200, r.text
        row = _row(world.po)
        assert (row["status"], row["decided_by"], row["decided_on_behalf_of"]) == (
            "rejected", world.delegate["id"], world.approver["id"])
        assert _state(world.po)["approval_status"] == "rejected"

    def test_a_repeat_by_the_delegate_changes_nothing(self, world):
        _delegation(world.tid, world.approver["id"], world.delegate["id"])
        assert _decide(world, world.delegate_h).json()["data"]["changed"] is True
        again = _decide(world, world.delegate_h)
        assert again.status_code == 200 and again.json()["data"]["changed"] is False
        assert query_one("SELECT COUNT(*) AS n FROM activity_logs WHERE tenant_id = %s "
                         "AND action = 'purchase.approval_approved'", (world.tid,))["n"] == 1

    def test_the_approver_still_decides_for_themselves_with_no_delegation_trace(self, world):
        _delegation(world.tid, world.approver["id"], world.delegate["id"])
        r = _decide(world, world.approver_h)
        assert r.status_code == 200 and r.json()["data"]["on_behalf_of_name"] is None
        row = _row(world.po)
        assert row["decided_by"] == world.approver["id"]
        assert row["decided_on_behalf_of"] is None and row["delegation_id"] is None


# ── Who may NOT decide, and nothing changes ──────────────────────────────────

class TestRefusals:

    def test_without_a_delegation_a_non_approver_is_refused(self, world):
        r = _decide(world, world.delegate_h)
        assert r.status_code == 403 and r.json()["error_code"] == "po_approval_not_approver"
        _assert_untouched(world.po)

    def test_an_expired_delegation_stops_working_with_no_cleanup(self, world):
        _delegation(world.tid, world.approver["id"], world.delegate["id"], start=-10, end=-1)
        r = _decide(world, world.delegate_h)
        assert r.status_code == 403 and r.json()["error_code"] == "po_approval_not_approver"
        _assert_untouched(world.po)

    def test_the_last_day_still_counts_and_the_day_after_does_not(self, world):
        did = _delegation(world.tid, world.approver["id"], world.delegate["id"], start=-3, end=0)
        # ends today: still in force
        assert svc_active(world) == [did]
        execute("UPDATE po_approval_delegations SET ends_on = ends_on - 1 WHERE id = %s", (did,))
        assert svc_active(world) == []
        assert _decide(world, world.delegate_h).status_code == 403
        _assert_untouched(world.po)

    def test_a_delegation_that_has_not_started_is_refused(self, world):
        _delegation(world.tid, world.approver["id"], world.delegate["id"], start=2, end=9)
        r = _decide(world, world.delegate_h)
        assert r.status_code == 403 and r.json()["error_code"] == "po_approval_not_approver"
        _assert_untouched(world.po)

    def test_a_revoked_delegation_is_refused(self, world):
        _delegation(world.tid, world.approver["id"], world.delegate["id"], revoked=True)
        r = _decide(world, world.delegate_h)
        assert r.status_code == 403 and r.json()["error_code"] == "po_approval_not_approver"
        _assert_untouched(world.po)

    def test_revoking_after_use_ends_the_authority_at_once(self, world):
        did = _delegation(world.tid, world.approver["id"], world.delegate["id"])
        execute("UPDATE po_approval_delegations SET revoked_at = NOW() WHERE id = %s", (did,))
        assert _decide(world, world.delegate_h).status_code == 403
        _assert_untouched(world.po)

    def test_a_delegator_who_is_no_longer_an_approver_lends_nothing(self, world):
        _delegation(world.tid, world.approver["id"], world.delegate["id"])
        execute("UPDATE users SET can_approve_po = FALSE WHERE id = %s", (world.approver["id"],))
        r = _decide(world, world.delegate_h)
        assert r.status_code == 403
        assert r.json()["error_code"] == "po_approval_delegation_not_permitted"
        _assert_untouched(world.po)

    def test_a_deactivated_delegator_lends_nothing(self, world):
        _delegation(world.tid, world.approver["id"], world.delegate["id"])
        execute("UPDATE users SET status = 'inactive' WHERE id = %s", (world.approver["id"],))
        assert _decide(world, world.delegate_h).status_code == 403
        _assert_untouched(world.po)

    def test_a_viewer_can_never_decide_even_with_a_delegation_row(self, world):
        viewer, viewer_h = _person(world.tid, "viewer")
        _delegation(world.tid, world.approver["id"], viewer["id"])
        r = _decide(world, viewer_h)
        assert r.status_code == 403 and r.json()["error_code"] == "role_not_permitted"
        _assert_untouched(world.po)

    def test_a_delegation_is_not_transitive(self, world):
        # Ana lends to Dani; Dani (not an approver) tries to lend to Carl.
        _delegation(world.tid, world.approver["id"], world.delegate["id"])
        carl, carl_h = _person(world.tid, "analyst", name="Carl Chain")
        _delegation(world.tid, world.delegate["id"], carl["id"])
        r = _decide(world, carl_h)
        assert r.status_code == 403
        assert r.json()["error_code"] == "po_approval_delegation_not_permitted"
        _assert_untouched(world.po)

    def test_the_delegator_who_asked_for_the_order_cannot_be_stood_in_for(self, client, test_tenant):
        # Ana asked for her own order (above the self-approval limit): neither
        # she nor her substitute may approve it.
        tid = test_tenant["id"]
        ana, ana_h = _person(tid, "analyst", approver=True, name="Ana Approver")
        dani, dani_h = _person(tid, "analyst", name="Dani Delegate")
        _rule(tid, threshold=5000, self_approve_below=None)
        po = _po(tid)
        # another approver is needed for the request to be allowed at all
        _person(tid, "analyst", approver=True)
        _request(client, ana_h, po)
        _delegation(tid, ana["id"], dani["id"])
        r = client.post(APPROVAL.format(po=po) + "/approve", headers=dani_h, json={})
        assert r.status_code == 403
        assert r.json()["error_code"] == "po_approval_delegation_not_permitted"
        _assert_untouched(po)
        # ... but rejecting her own order on her behalf is not a self-approval
        r = client.post(APPROVAL.format(po=po) + "/reject", headers=dani_h,
                        json={"comment": "withdrawn"})
        assert r.status_code == 200, r.text

    def test_below_the_self_approval_limit_the_substitute_may_approve_it(self, client, test_tenant):
        tid = test_tenant["id"]
        ana, ana_h = _person(tid, "analyst", approver=True)
        dani, dani_h = _person(tid, "analyst")
        _rule(tid, threshold=5000, self_approve_below=9000)
        po = _po(tid)                                # worth 6000: inside the limit
        _request(client, ana_h, po)
        _delegation(tid, ana["id"], dani["id"])
        r = client.post(APPROVAL.format(po=po) + "/approve", headers=dani_h, json={})
        assert r.status_code == 200, r.text
        assert _row(po)["decided_on_behalf_of"] == ana["id"]

    def test_the_delegate_cannot_approve_their_own_order_via_the_delegation(self, client, test_tenant):
        tid = test_tenant["id"]
        ana, _ = _person(tid, "analyst", approver=True)
        dani, dani_h = _person(tid, "analyst")
        _rule(tid, threshold=5000, self_approve_below=None)
        po = _po(tid)
        _request(client, dani_h, po)
        _delegation(tid, ana["id"], dani["id"])
        r = client.post(APPROVAL.format(po=po) + "/approve", headers=dani_h, json={})
        assert r.status_code == 403 and r.json()["error_code"] == "po_approval_self_approval"
        row = _row(po)
        assert row["status"] == "requested" and row["decided_on_behalf_of"] is None


def svc_active(world):
    from backend.inventory import po_delegation_service as ds
    return [d["id"] for d in ds.active_for_delegate(world.tid, world.delegate["id"])]


# ── Warehouse scope of the delegator ─────────────────────────────────────────

class TestDelegatorScope:

    def _scoped(self, client, tid, scope_names):
        warehouse_service.create_warehouse(tid, "Norte")
        warehouse_service.create_warehouse(tid, "Sur")
        ids = {r["name"]: r["id"] for r in query(
            "SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))}
        ana, ana_h = _person(tid, "analyst", approver=True, name="Ana Approver",
                             scope=[ids[n] for n in scope_names])
        dani, dani_h = _person(tid, "analyst", name="Dani Delegate")
        req, req_h = _person(tid, "analyst")
        _rule(tid)
        _delegation(tid, ana["id"], dani["id"])
        return ana, dani, dani_h, req_h

    def test_a_delegate_cannot_decide_outside_the_delegators_warehouses(self, client, test_tenant):
        tid = test_tenant["id"]
        _, dani, dani_h, req_h = self._scoped(client, tid, ["Norte"])
        po = _po(tid, warehouse="Sur")
        _request(client, req_h, po)
        r = client.post(APPROVAL.format(po=po) + "/approve", headers=dani_h, json={})
        assert r.status_code == 403
        assert r.json()["error_code"] == "po_approval_delegation_not_permitted"
        _assert_untouched(po)

    def test_inside_the_delegators_warehouses_the_delegate_decides(self, client, test_tenant):
        tid = test_tenant["id"]
        ana, dani, dani_h, req_h = self._scoped(client, tid, ["Norte"])
        po = _po(tid, warehouse="Norte")
        _request(client, req_h, po)
        r = client.post(APPROVAL.format(po=po) + "/approve", headers=dani_h, json={})
        assert r.status_code == 200, r.text
        assert _row(po)["decided_on_behalf_of"] == ana["id"]

    def test_an_empty_or_unreadable_scope_fails_closed(self, client, test_tenant):
        tid = test_tenant["id"]
        ana, _, dani_h, req_h = self._scoped(client, tid, ["Norte"])
        po = _po(tid, warehouse="Norte")
        _request(client, req_h, po)
        for raw in ("[]", '"not-a-list"', '["no-such-warehouse-id"]'):
            execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s", (raw, ana["id"]))
            r = client.post(APPROVAL.format(po=po) + "/approve", headers=dani_h, json={})
            assert r.status_code == 403, raw
            assert r.json()["error_code"] == "po_approval_delegation_not_permitted"
            _assert_untouched(po)

    def test_a_scoped_delegate_is_still_held_to_their_own_scope(self, client, test_tenant):
        tid = test_tenant["id"]
        warehouse_service.create_warehouse(tid, "Norte")
        warehouse_service.create_warehouse(tid, "Sur")
        ids = {r["name"]: r["id"] for r in query(
            "SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))}
        ana, _ = _person(tid, "analyst", approver=True)             # unrestricted
        dani, dani_h = _person(tid, "analyst", scope=[ids["Norte"]])
        req, req_h = _person(tid, "analyst")
        _rule(tid)
        _delegation(tid, ana["id"], dani["id"])
        po = _po(tid, warehouse="Sur")
        _request(client, req_h, po)
        r = client.post(APPROVAL.format(po=po) + "/approve", headers=dani_h, json={})
        assert r.status_code == 403 and r.json()["error_code"] == "warehouse_out_of_scope"
        _assert_untouched(po)


# ── The delegate's inbox ─────────────────────────────────────────────────────

class TestInbox:

    def test_a_delegate_sees_what_they_may_decide_and_nobody_else_does(self, world):
        plain = world.client.get(PENDING, headers=world.delegate_h).json()["data"]
        assert plain == {"is_approver": False, "items": []}          # unchanged shape

        did = _delegation(world.tid, world.approver["id"], world.delegate["id"])
        mine = world.client.get(PENDING, headers=world.delegate_h).json()["data"]
        assert mine["is_approver"] is False and mine["is_delegate"] is True
        assert [i["po_log_id"] for i in mine["items"]] == [world.po]
        assert mine["items"][0]["can_decide"] is True

        info = world.client.get(APPROVAL.format(po=world.po), headers=world.delegate_h
                                ).json()["data"]
        assert info["can_decide"] is True

        execute("UPDATE po_approval_delegations SET revoked_at = NOW() WHERE id = %s", (did,))
        gone = world.client.get(PENDING, headers=world.delegate_h).json()["data"]
        assert gone == {"is_approver": False, "items": []}
        assert world.client.get(APPROVAL.format(po=world.po), headers=world.delegate_h
                                ).json()["data"]["can_decide"] is False

    def test_an_order_outside_the_delegation_is_hidden_from_the_delegate(self, client, test_tenant):
        tid = test_tenant["id"]
        warehouse_service.create_warehouse(tid, "Norte")
        warehouse_service.create_warehouse(tid, "Sur")
        ids = {r["name"]: r["id"] for r in query(
            "SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))}
        ana, _ = _person(tid, "analyst", approver=True, scope=[ids["Norte"]])
        dani, dani_h = _person(tid, "analyst")
        req, req_h = _person(tid, "analyst")
        _rule(tid)
        _delegation(tid, ana["id"], dani["id"])
        north, south = _po(tid, warehouse="Norte"), _po(tid, warehouse="Sur")
        _request(client, req_h, north)
        _request(client, req_h, south)
        items = client.get(PENDING, headers=dani_h).json()["data"]["items"]
        assert [i["po_log_id"] for i in items] == [north]
        # the approver's own inbox is untouched by any of this
        assert svc.list_pending(tid, ana["id"])["is_approver"] is True
