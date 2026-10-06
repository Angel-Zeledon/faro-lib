"""Decision links for purchase-order approvals: the Python side.

The public page and its decision endpoint are Rust (`backend-rs`, covered by its
unit tests and `tests/contract/approval_links_contract.py`). What Python owns,
and what this file pins with direct DB reads:

* with the switch off (default) a request is exactly what it was: no link row,
  no extra mail content, no WhatsApp row;
* with it on, one link per approver (never the requester), stored as a HASH,
  bound to approver + request + order, expiring, and the mail carries the URL;
* WhatsApp: only the link message, only for a verified number on a plan that
  has the bot, only through the outbox;
* a decision, in the app or by message, runs the SAME rules and revokes every
  open link of the request; `decided_channel` says how it was taken;
* re-sending rotates a token and never reopens a used link.
"""

from uuid import uuid4

import pytest

from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.inventory import po_approval_link_service as links
from backend.inventory import po_approval_service as svc
from backend.inventory import po_confirmation_core as core
from backend.inventory import roi_service
from backend.users import service as user_svc


def _po(tenant_id, qty=100, cost=60.0, supplier="Acme"):
    return roi_service.log_po_generation(
        tenant_id, "sess-test",
        [{"sku": f"A-{uuid4().hex[:6]}", "final_qty": qty, "unit_cost": cost,
          "status": "approved", "supplier": supplier}],
    )["id"]


def _person(tenant_id, role, *, approver=False, whatsapp=None, verified=False):
    email = f"{role}-{uuid4().hex[:8]}@example.com"
    user = user_svc.create_user(tenant_id=tenant_id, email=email, password="TestPass123!",
                                role=role, full_name=f"{role.title()} {uuid4().hex[:4]}")
    user_svc.mark_verified(tenant_id, user["id"])
    if approver:
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (user["id"],))
    if whatsapp:
        execute("UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = "
                "CASE WHEN %s THEN NOW() END WHERE id = %s", (whatsapp, verified, user["id"]))
    return user["id"], email


def _rule(tenant_id, threshold=5000, below=None):
    return query_one(
        """INSERT INTO po_approval_rules (tenant_id, threshold, self_approve_below, created_by)
           VALUES (%s, %s, %s, 'test') RETURNING id""", (tenant_id, threshold, below))["id"]


def _links(po_id):
    return query("SELECT * FROM po_approval_links WHERE po_log_id = %s ORDER BY channel, approver_id",
                 (po_id,))


@pytest.fixture(autouse=True)
def _app_started(client):
    """The session `client` runs the lifespan, which applies the migrations."""
    return client


@pytest.fixture
def mails(monkeypatch):
    from backend.notifications import email as email_mod
    sent = {"request": [], "decision": []}
    monkeypatch.setattr(email_mod, "send_po_approval_request_email",
                        lambda **kw: sent["request"].append(kw) or True)
    monkeypatch.setattr(email_mod, "send_po_approval_decision_email",
                        lambda **kw: sent["decision"].append(kw) or True)
    return sent


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "approval_links_enabled", True)


@pytest.fixture
def world(test_tenant):
    """A requester (analyst) and two approvers, a rule, and an order above it."""
    tid = test_tenant["id"]
    requester, _ = _person(tid, "analyst")
    a1, a1_email = _person(tid, "admin", approver=True)
    a2, a2_email = _person(tid, "analyst", approver=True)
    _rule(tid)
    po = _po(tid)                                      # 6,000 >= 5,000
    return {"tid": tid, "requester": requester, "a1": a1, "a1_email": a1_email,
            "a2": a2, "a2_email": a2_email, "po": po}


def _request(w):
    return svc.request_approval(w["tid"], w["po"], w["requester"])


class TestSwitchOff:

    def test_a_request_issues_no_link_and_changes_no_mail(self, mails, world):
        out = _request(world)
        assert out["changed"] and out["notified"] == 2
        assert _links(world["po"]) == []
        assert len(mails["request"]) == 2
        assert all(m["decision_url"] is None for m in mails["request"])
        assert query("SELECT 1 FROM outbound_messages WHERE tenant_id = %s", (world["tid"],)) == []
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'purchase.approval_links_sent'", (world["tid"],)) == []

    def test_the_switch_defaults_to_off(self):
        assert settings.approval_links_enabled is False


class TestIssuing:

    def test_one_hashed_expiring_link_per_approver_and_never_the_requester(self, on, mails, world):
        _request(world)
        rows = _links(world["po"])
        assert sorted(r["approver_id"] for r in rows) == sorted([world["a1"], world["a2"]])
        approval = query_one("SELECT id FROM po_approvals WHERE po_log_id = %s", (world["po"],))
        for r in rows:
            assert r["channel"] == "email" and r["scope"] == "decide"
            assert r["approval_id"] == approval["id"] and r["tenant_id"] == world["tid"]
            assert len(r["token_hash"]) == 64 and r["used_at"] is None and r["revoked_at"] is None
        hours = query_one("SELECT EXTRACT(EPOCH FROM (expires_at - issued_at)) / 3600 AS h "
                          "FROM po_approval_links WHERE po_log_id = %s LIMIT 1", (world["po"],))["h"]
        assert abs(float(hours) - links.LINK_TTL_HOURS) < 0.01

    def test_the_mail_carries_the_url_and_only_its_hash_is_stored(self, on, mails, world):
        _request(world)
        by_to = {m["to"]: m for m in mails["request"]}
        for approver_id, email in ((world["a1"], world["a1_email"]), (world["a2"], world["a2_email"])):
            url = by_to[email]["decision_url"]
            assert url.startswith(f"{settings.frontend_url.rstrip('/')}/aprobar/")
            token = url.rsplit("/", 1)[1]
            assert core.token_is_wellformed(token) and len(token) == 43       # 256 bits
            row = query_one("SELECT token_hash FROM po_approval_links WHERE po_log_id = %s "
                            "AND approver_id = %s", (world["po"], approver_id))
            assert row["token_hash"] == core.hash_token(token)
            # the token is nowhere in the table, in any column
            assert query_one("SELECT 1 AS x FROM po_approval_links WHERE row(po_approval_links.*)::text "
                             "LIKE %s", (f"%{token}%",)) is None

    def test_the_trail_records_how_many_links_went_out(self, on, mails, world):
        _request(world)
        ev = query("SELECT user_id, resource, context FROM activity_logs WHERE tenant_id = %s "
                   "AND action = 'purchase.approval_links_sent'", (world["tid"],))
        assert len(ev) == 1 and ev[0]["resource"] == world["po"]
        assert ev[0]["user_id"] == world["requester"] and ev[0]["context"]["count"] == 2

    def test_a_failure_issuing_links_never_blocks_the_request(self, on, mails, world, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db hiccup")
        monkeypatch.setattr(links, "issue_links", boom)
        out = _request(world)
        assert out["changed"] and out["notified"] == 2
        assert all(m["decision_url"] is None for m in mails["request"])
        assert query_one("SELECT status FROM po_approvals WHERE po_log_id = %s",
                         (world["po"],))["status"] == "requested"

    def test_resending_rotates_the_token_and_never_reopens_a_used_link(self, on, mails, world):
        _request(world)
        approval = query_one("SELECT id FROM po_approvals WHERE po_log_id = %s", (world["po"],))
        before = {r["approver_id"]: r["token_hash"] for r in _links(world["po"])}
        # a1 acted on their link; a2 did not
        execute("UPDATE po_approval_links SET used_at = NOW(), used_decision = 'approved' "
                "WHERE po_log_id = %s AND approver_id = %s", (world["po"], world["a1"]))
        issued = links.issue_links(world["tid"], world["po"], approval["id"],
                                   [{"id": world["a1"], "email": world["a1_email"]},
                                    {"id": world["a2"], "email": world["a2_email"]}],
                                   created_by=world["requester"])
        assert [i["approver_id"] for i in issued] == [world["a2"]]
        after = {r["approver_id"]: r["token_hash"] for r in _links(world["po"])}
        assert after[world["a1"]] == before[world["a1"]]          # used: untouched
        assert after[world["a2"]] != before[world["a2"]]          # rotated: the old link is dead
        assert after[world["a2"]] == core.hash_token(issued[0]["token"])


class TestWhatsapp:

    def _wa_world(self, test_tenant, monkeypatch, *, verified=True, configured=True):
        from backend.notifications import whatsapp as wa
        monkeypatch.setattr(wa, "is_configured", lambda tenant_id=None: configured)
        tid = test_tenant["id"]
        requester, _ = _person(tid, "analyst")
        a1, _e = _person(tid, "admin", approver=True, whatsapp="+50688887777", verified=verified)
        _rule(tid)
        return {"tid": tid, "requester": requester, "a1": a1, "po": _po(tid)}

    def test_a_verified_number_gets_only_the_link_message_through_the_outbox(
            self, on, mails, test_tenant, monkeypatch):
        w = self._wa_world(test_tenant, monkeypatch)
        _request(w)
        rows = query("SELECT * FROM outbound_messages WHERE tenant_id = %s AND channel = 'whatsapp'",
                     (w["tid"],))
        assert len(rows) == 1 and rows[0]["kind"] == "po_approval_link"
        assert rows[0]["recipient"] == "+50688887777" and rows[0]["status"] == "pending"
        p = rows[0]["params"]
        assert set(p) == {"po_log_id", "amount", "decision_token", "approver_id"}
        link = query_one("SELECT token_hash, channel FROM po_approval_links WHERE channel = 'whatsapp' "
                         "AND po_log_id = %s", (w["po"],))
        assert link["token_hash"] == core.hash_token(p["decision_token"])
        # the email link exists too: two channels, two independent tokens
        assert len(_links(w["po"])) == 2

    def test_an_unverified_number_gets_nothing(self, on, mails, test_tenant, monkeypatch):
        w = self._wa_world(test_tenant, monkeypatch, verified=False)
        _request(w)
        assert query("SELECT 1 FROM outbound_messages WHERE tenant_id = %s", (w["tid"],)) == []
        assert [r["channel"] for r in _links(w["po"])] == ["email"]

    def test_no_channel_configured_means_no_whatsapp_link(self, on, mails, test_tenant, monkeypatch):
        w = self._wa_world(test_tenant, monkeypatch, configured=False)
        _request(w)
        assert [r["channel"] for r in _links(w["po"])] == ["email"]

    def test_a_plan_without_the_bot_gets_the_email_link_only(self, on, mails, test_tenant, monkeypatch):
        w = self._wa_world(test_tenant, monkeypatch)
        monkeypatch.setattr(settings, "testing_mode", False)        # the plan gate is real now
        execute("UPDATE tenants SET tier = 'free' WHERE id = %s", (w["tid"],))
        _request(w)
        assert [r["channel"] for r in _links(w["po"])] == ["email"]
        assert query("SELECT 1 FROM outbound_messages WHERE tenant_id = %s", (w["tid"],)) == []

    def test_the_sender_renders_spanish_from_the_catalog_and_sends_once(
            self, on, mails, test_tenant, monkeypatch):
        from backend.notifications import outbox
        from backend.notifications import whatsapp as wa
        w = self._wa_world(test_tenant, monkeypatch)
        _request(w)
        sent = []
        monkeypatch.setattr(wa, "send_whatsapp",
                            lambda to, body, media_url=None, tenant_id=None, plan_gated=False:
                            sent.append((to, body, plan_gated)) or True)
        assert outbox.process_due() == 1
        (to, body, gated), = sent
        assert to == "+50688887777" and gated is True
        assert "/aprobar/" in body and str(links.LINK_TTL_HOURS) in body
        row = query_one("SELECT status, params FROM outbound_messages WHERE tenant_id = %s", (w["tid"],))
        assert row["status"] == "sent" and row["params"] == {}      # the token is scrubbed once sent


class TestDecisions:

    def test_an_in_app_decision_revokes_every_open_link_of_the_request(self, on, mails, world):
        _request(world)
        out = svc.decide(world["tid"], world["po"], world["a1"], "approved")
        assert out["changed"] and out["channel"] is None
        rows = _links(world["po"])
        assert len(rows) == 2 and all(r["revoked_at"] is not None and r["revoked_reason"] == "decided"
                                      for r in rows)
        row = query_one("SELECT status, decided_by, decided_channel FROM po_approvals WHERE po_log_id = %s",
                        (world["po"],))
        assert (row["status"], row["decided_by"], row["decided_channel"]) == ("approved", world["a1"], None)

    def test_a_message_decision_is_recorded_with_its_channel_and_the_same_effects(self, on, mails, world):
        _request(world)
        out = svc.decide(world["tid"], world["po"], world["a2"], "approved", channel="message")
        assert out["changed"] and out["channel"] == "message"
        row = query_one("SELECT status, decided_by, decided_channel FROM po_approvals WHERE po_log_id = %s",
                        (world["po"],))
        assert (row["status"], row["decided_by"], row["decided_channel"]) == ("approved", world["a2"], "message")
        po = query_one("SELECT approval_status, approved_amount FROM inventory_po_log WHERE id = %s",
                       (world["po"],))
        assert po["approval_status"] == "approved" and po["approved_amount"] == pytest.approx(6000)
        assert all(r["revoked_reason"] == "decided" for r in _links(world["po"]))
        assert svc.history(world["tid"], world["po"])[0]["decided_channel"] == "message"
        # the requester hears about it exactly as for an in-app decision
        assert len(mails["decision"]) == 1

    def test_a_message_decision_obeys_the_rules_the_app_obeys(self, on, mails, world):
        _request(world)
        # not an approver (flag removed after the message went out)
        execute("UPDATE users SET can_approve_po = FALSE WHERE id = %s", (world["a2"],))
        with pytest.raises(AppError) as e:
            svc.decide(world["tid"], world["po"], world["a2"], "approved", channel="message")
        assert e.value.code == "po_approval_not_approver"
        # a rejection still needs its reason
        with pytest.raises(AppError) as e:
            svc.decide(world["tid"], world["po"], world["a1"], "rejected", channel="message")
        assert e.value.code == "po_approval_reason_required"
        row = query_one("SELECT status, decided_channel FROM po_approvals WHERE po_log_id = %s", (world["po"],))
        assert row["status"] == "requested" and row["decided_channel"] is None
        assert all(r["revoked_at"] is None for r in _links(world["po"]))

    def test_self_approval_is_refused_through_a_message_too(self, on, mails, test_tenant):
        tid = test_tenant["id"]
        me, _ = _person(tid, "admin", approver=True)
        other, _ = _person(tid, "admin", approver=True)
        _rule(tid, threshold=5000)                                  # no self-approval limit
        po = _po(tid)
        svc.request_approval(tid, po, me)
        with pytest.raises(AppError) as e:
            svc.decide(tid, po, me, "approved", channel="message")
        assert e.value.code == "po_approval_self_approval"
        assert query_one("SELECT status FROM po_approvals WHERE po_log_id = %s", (po,))["status"] == "requested"

    def test_an_unknown_channel_is_refused(self, world):
        with pytest.raises(ValueError):
            svc.decide(world["tid"], world["po"], world["a1"], "approved", channel="sms")


class TestPlumbing:

    def test_the_new_outbox_kinds_are_registered(self):
        from backend.notifications import outbox
        assert outbox.check_params("whatsapp", "po_approval_link",
                                   {"po_log_id": "p", "amount": 1, "decision_token": "t"}) is None
        assert outbox.check_params("whatsapp", "po_approval_link", {"po_log_id": "p", "amount": 1}) \
            == "missing_param:decision_token"
        assert outbox.check_params("email", "po_approval_request",
                                   {"po_log_id": "p", "amount": 1, "decision_token": "t"}) is None

    def test_the_events_are_declared_and_the_channel_is_whitelisted(self):
        from backend.activity.events import spec_for
        for action in ("purchase.approval_approved", "purchase.approval_rejected"):
            assert "channel" in spec_for(action).detail_keys
        for action in ("purchase.approval_links_sent", "purchase.approval_links_revoked"):
            assert spec_for(action).severity == "info"

    def test_the_audit_trail_shows_the_channel(self, test_tenant):
        from backend.activity.events import record_event
        from backend.audit.service import list_audit
        tid = test_tenant["id"]
        user, _ = _person(tid, "admin", approver=True)
        record_event(tid, user, "purchase.approval_approved", resource="po_x",
                     details={"reference": "OC-1", "value": 10.0, "channel": "message"})
        items = list_audit(tid, action="purchase_order.approval_approved")["items"]
        assert items and items[0]["after"]["channel"] == "message"

    def test_the_export_never_carries_the_token_hash_and_erasure_covers_the_table(self):
        from backend.tenants import data_export as de
        spec = next(s for s in de._EXPORT_SPECS if s[1] == "po_approval_links")
        assert "token_hash" not in spec[2]
        order = de._DELETE_ORDER
        # children before parents: the links reference po_approvals
        assert order.index("po_approval_links") < order.index("po_approvals")
