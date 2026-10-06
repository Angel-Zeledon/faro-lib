"""Outbound webhooks against the database: management endpoints, emitters,
the durable delivery queue and its retry state machine, warehouse scope, secret
rotation, auto-disable, tenant isolation and erasure.

The pure rules (signature, backoff table, payload keys, scope maths, transition
maths) are pinned without a database in `test_webhook_policy.py`; here every
claim is read back from the tables. No test posts to a real server: the single
network seam, `backend.webhooks.service._send`, is replaced.
"""

import json
from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.inventory import roi_service
from backend.webhooks import catalog, policy, signing
from backend.webhooks import service as hooks

URL = "/api/v1/webhooks"
_REAL_RESOLVE_ALLOWED = hooks.network.resolve_allowed   # captured before any patch


@pytest.fixture(autouse=True)
def _no_dns_and_no_threads(monkeypatch):
    # Creation resolves the host; the target is fictional.
    monkeypatch.setattr(hooks.network, "resolve_allowed", lambda *a, **k: [])
    monkeypatch.setattr(hooks, "kick", lambda: None)
    # `process_due` claims every due row in the table, so leftovers from other
    # tests would consume a test's scripted answers.
    execute("DELETE FROM webhook_deliveries WHERE status = 'pending'")


def _hook(tid, events=("purchase_order.sent",), scope=None, url=None, **cols):
    row = query_one(
        """INSERT INTO webhooks (id, tenant_id, url, events, secret, warehouse_scope)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s::jsonb) RETURNING id""",
        (tid, url or f"https://example.com/{uuid4().hex[:6]}", list(events),
         "secret-" + uuid4().hex, None if scope is None else json.dumps(scope)))
    for col, value in cols.items():
        execute(f"UPDATE webhooks SET {col} = %s WHERE id = %s", (value, row["id"]))
    return row["id"]


def _deliveries(hook_id):
    return query("SELECT * FROM webhook_deliveries WHERE webhook_id = %s ORDER BY created_at",
                 (hook_id,))


def _po(tid, warehouse=None):
    return roi_service.log_po_generation(
        tid, "sess-test",
        [{"sku": f"A-{uuid4().hex[:6]}", "final_qty": 10, "unit_cost": 5.0,
          "status": "approved", "supplier": "Acme"}],
        destination_warehouse=warehouse)["id"]


class Sent:
    """Replaces `_send`: records every request and answers from a script."""

    def __init__(self, monkeypatch, *answers):
        self.calls, self.answers = [], list(answers)
        monkeypatch.setattr(hooks, "_send", self)

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        answer = self.answers.pop(0) if self.answers else hooks.Attempt(200, None)
        return answer


def _due(delivery_id):
    execute("UPDATE webhook_deliveries SET next_attempt_at = NOW() - INTERVAL '1 second' "
            "WHERE id = %s", (delivery_id,))


# ── Management: permission pairs and state ───────────────────────────────────

class TestCreate:

    def test_viewer_denied_analyst_and_admin_create(self, client, viewer_headers,
                                                    analyst_headers, auth_headers, test_tenant):
        tid = test_tenant["id"]
        body = {"url": "https://example.com/h", "events": ["purchase_order.sent"]}
        denied = client.post(URL, json=body, headers=viewer_headers)
        assert denied.status_code == 403
        assert query("SELECT 1 FROM webhooks WHERE tenant_id = %s", (tid,)) == []

        for headers in (analyst_headers, auth_headers):
            ok = client.post(URL, json=body, headers=headers)
            assert ok.status_code == 200, ok.text
            data = ok.json()["data"]
            row = query_one("SELECT * FROM webhooks WHERE id = %s", (data["id"],))
            assert row["tenant_id"] == tid and row["events"] == ["purchase_order.sent"]
            assert row["warehouse_scope"] is None and row["created_by"]
            # the secret is shown once and is the stored one
            assert data["secret"] == row["secret"] and len(data["secret"]) == 64

    def test_the_secret_is_never_listed_again(self, client, analyst_headers, auth_headers):
        created = client.post(URL, json={"url": "https://example.com/h",
                                         "events": ["job.failed"]}, headers=analyst_headers)
        listed = client.get(URL, headers=auth_headers).json()["data"]
        mine = next(h for h in listed if h["id"] == created.json()["data"]["id"])
        assert "secret" not in mine and mine["disabled_at"] is None

    def test_internal_addresses_are_refused_and_nothing_is_written(
            self, client, analyst_headers, test_tenant, monkeypatch):
        # The real address checks for this test (literal IPs: no DNS involved).
        monkeypatch.setattr(hooks.network, "resolve_allowed", _REAL_RESOLVE_ALLOWED)
        monkeypatch.setattr(hooks.network, "allow_private_hosts", lambda: False)
        for url, code in (("https://169.254.169.254/latest", "webhook_host_forbidden"),
                          ("https://10.0.0.7/h", "webhook_host_not_allowed")):
            resp = client.post(URL, json={"url": url, "events": ["job.failed"]},
                               headers=analyst_headers)
            assert resp.status_code == 422 and resp.json()["error_code"] == code, resp.text
        assert query("SELECT 1 FROM webhooks WHERE tenant_id = %s", (test_tenant["id"],)) == []

    def test_a_plan_without_the_api_cannot_create_one(self, make_tenant_user_headers, client,
                                                      monkeypatch):
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        headers, tid = make_tenant_user_headers(role="analyst", return_tenant_id=True)
        execute("UPDATE tenants SET tier = 'free' WHERE id = %s", (tid,))
        resp = client.post(URL, json={"url": "https://example.com/h", "events": ["job.failed"]},
                           headers=headers)
        assert resp.status_code == 403 and resp.json()["error_code"] == "plan_feature_locked"
        assert query("SELECT 1 FROM webhooks WHERE tenant_id = %s", (tid,)) == []

    def test_a_scoped_creator_gets_a_scoped_hook_and_cannot_reach_past_it(
            self, client, registered_user, test_tenant):
        tid = test_tenant["id"]
        from backend.auth.jwt_handler import create_access_token
        from backend.users import service as user_svc
        from backend.inventory import warehouse_service as wh_svc
        wh = {n: wh_svc.create_warehouse(tid, n)["id"] for n in ("Norte", "Sur")}
        u = user_svc.create_user(tenant_id=tid, email=f"s-{uuid4().hex[:8]}@example.com",
                                 password="TestPass123!", role="analyst")
        user_svc.mark_verified(tid, u["id"])
        execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                (json.dumps([wh["Norte"]]), u["id"]))
        headers = {"Authorization": "Bearer " + create_access_token(
            u["id"], tid, "analyst", email_verified=True)}

        mine = client.post(URL, json={"url": "https://example.com/n",
                                      "events": ["stockout.imminent"]}, headers=headers)
        assert mine.status_code == 200, mine.text
        assert json.loads(json.dumps(query_one(
            "SELECT warehouse_scope FROM webhooks WHERE id = %s",
            (mine.json()["data"]["id"],))["warehouse_scope"])) == [wh["Norte"]]

        outside = client.post(URL, json={"url": "https://example.com/s",
                                         "events": ["stockout.imminent"],
                                         "warehouse_ids": [wh["Sur"]]}, headers=headers)
        assert outside.status_code == 403
        assert query("SELECT 1 FROM webhooks WHERE url = 'https://example.com/s'") == []

        # a company-wide hook is invisible and untouchable to the scoped user
        company = _hook(tid)
        assert company not in [h["id"] for h in client.get(URL, headers=headers).json()["data"]]
        assert client.delete(f"{URL}/{company}", headers=headers).status_code == 404
        assert query_one("SELECT 1 AS x FROM webhooks WHERE id = %s", (company,))


class TestDeleteRotateTestEnable:

    def test_delete_permission_pair_removes_hook_and_log(self, client, viewer_headers,
                                                         analyst_headers, test_tenant):
        hid = _hook(test_tenant["id"])
        hooks.enqueue_test(test_tenant["id"], hid)
        assert client.delete(f"{URL}/{hid}", headers=viewer_headers).status_code == 403
        assert query_one("SELECT 1 AS x FROM webhooks WHERE id = %s", (hid,))
        assert client.delete(f"{URL}/{hid}", headers=analyst_headers).status_code == 200
        assert query("SELECT 1 FROM webhooks WHERE id = %s", (hid,)) == []
        assert _deliveries(hid) == []

    def test_rotation_permission_pair_and_the_old_secret_stops_verifying(
            self, client, viewer_headers, analyst_headers, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid)
        before = query_one("SELECT secret FROM webhooks WHERE id = %s", (hid,))["secret"]
        assert client.post(f"{URL}/{hid}/rotate-secret", headers=viewer_headers).status_code == 403
        assert query_one("SELECT secret FROM webhooks WHERE id = %s", (hid,))["secret"] == before

        resp = client.post(f"{URL}/{hid}/rotate-secret", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        row = query_one("SELECT secret, secret_rotated_at FROM webhooks WHERE id = %s", (hid,))
        assert row["secret"] == resp.json()["data"]["secret"] != before
        assert row["secret_rotated_at"] is not None

        sent = Sent(monkeypatch)
        delivery = hooks.enqueue_test(tid, hid)
        hooks.process_due()
        _, body, headers = sent.calls[-1]
        assert signing.verify(row["secret"], headers["X-StockAI-Signature"], body)
        assert not signing.verify(before, headers["X-StockAI-Signature"], body)
        assert query_one("SELECT status FROM webhook_deliveries WHERE id = %s",
                         (delivery,))["status"] == "delivered"

    def test_send_test_permission_pair_and_a_signed_delivery_with_headers(
            self, client, viewer_headers, analyst_headers, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid)
        denied = client.post(f"{URL}/{hid}/test", headers=viewer_headers)
        assert denied.status_code == 403 and _deliveries(hid) == []

        resp = client.post(f"{URL}/{hid}/test", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        did = resp.json()["data"]["delivery_id"]
        row = _deliveries(hid)[0]
        assert row["id"] == did and row["is_test"] is True and row["status"] == "pending"
        assert json.loads(row["payload"])["type"] == "webhook.test"

        sent = Sent(monkeypatch)
        assert hooks.process_due() >= 1
        _, body, headers = sent.calls[-1]
        assert headers["X-StockAI-Event"] == "webhook.test"
        assert headers["X-StockAI-Delivery"] == did
        secret = query_one("SELECT secret FROM webhooks WHERE id = %s", (hid,))["secret"]
        assert signing.verify(secret, headers["X-StockAI-Signature"], body)
        assert headers["X-Signature"] == signing.legacy_signature(secret, body)
        done = query_one("SELECT * FROM webhook_deliveries WHERE id = %s", (did,))
        assert (done["status"], done["attempts"], done["last_status_code"]) == ("delivered", 1, 200)

    def test_enable_permission_pair_resets_the_streak(self, client, viewer_headers,
                                                      analyst_headers, test_tenant):
        hid = _hook(test_tenant["id"], disabled_at=date.today(), failure_days=3,
                    disabled_reason="failing_for_days")
        assert client.post(f"{URL}/{hid}/enable", headers=viewer_headers).status_code == 403
        assert query_one("SELECT disabled_at FROM webhooks WHERE id = %s", (hid,))["disabled_at"]
        assert client.post(f"{URL}/{hid}/enable", headers=analyst_headers).status_code == 200
        row = query_one("SELECT disabled_at, failure_days FROM webhooks WHERE id = %s", (hid,))
        assert row["disabled_at"] is None and row["failure_days"] == 0

    def test_deliveries_log_is_readable_without_the_payload(self, client, viewer_headers,
                                                            test_tenant):
        hid = _hook(test_tenant["id"])
        did = hooks.enqueue_test(test_tenant["id"], hid)
        listed = client.get(f"{URL}/{hid}/deliveries", headers=viewer_headers)
        assert listed.status_code == 200
        row = listed.json()["data"][0]
        assert row["id"] == did and "payload" not in row
        assert {"status", "attempts", "last_status_code", "last_error",
                "next_attempt_at"} <= set(row)

    def test_another_tenants_hook_is_a_404_everywhere(self, client, auth_headers,
                                                      make_tenant_user_headers):
        _, other = make_tenant_user_headers(role="analyst", return_tenant_id=True)
        foreign = _hook(other)
        for method, path in (("delete", ""), ("post", "/rotate-secret"), ("post", "/test"),
                             ("post", "/enable"), ("get", "/deliveries")):
            resp = getattr(client, method)(f"{URL}/{foreign}{path}", headers=auth_headers)
            assert resp.status_code == 404, (method, path, resp.text)
        assert query_one("SELECT 1 AS x FROM webhooks WHERE id = %s", (foreign,))
        assert foreign not in [h["id"] for h in client.get(URL, headers=auth_headers
                                                           ).json()["data"]]


# ── Emitters: the code path that causes an event queues it ───────────────────

class TestEmitters:

    def _one(self, hid, event):
        rows = _deliveries(hid)
        assert len(rows) == 1, rows
        env = json.loads(rows[0]["payload"])
        assert env["type"] == event == rows[0]["event_type"]
        assert env["id"] == rows[0]["event_id"] and env["api_version"] == catalog.API_VERSION
        assert list(env["data"]) == list(catalog.EVENT_TYPES[event].data_keys)
        return env

    def test_sent_cancelled_and_resend_once(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        sent_hook = _hook(tid, ["purchase_order.sent"])
        cancel_hook = _hook(tid, ["purchase_order.cancelled"])
        po = _po(tid)
        assert client.post(f"/api/v1/inventory/po/{po}/send", headers=analyst_headers
                           ).status_code == 200
        env = self._one(sent_hook, "purchase_order.sent")
        assert env["tenant_id"] == tid and env["data"]["po_log_id"] == po
        assert env["data"]["sent_at"] and env["data"]["total_value"] == 50.0
        # sending again is not a new "sent"
        client.post(f"/api/v1/inventory/po/{po}/send", headers=analyst_headers)
        assert len(_deliveries(sent_hook)) == 1

        other = _po(tid)
        assert client.post(f"/api/v1/inventory/po/{other}/cancel", json={"reason": "x"},
                           headers=analyst_headers).status_code == 200
        env = self._one(cancel_hook, "purchase_order.cancelled")
        assert env["data"]["po_log_id"] == other and env["data"]["cancelled_at"]
        client.post(f"/api/v1/inventory/po/{other}/cancel", headers=analyst_headers)
        assert len(_deliveries(cancel_hook)) == 1

    def test_approved_and_rejected(self, client, analyst_headers, test_tenant, registered_user):
        from backend.inventory import po_approval_service as approvals
        tid = test_tenant["id"]
        approver = registered_user["user"]["id"]
        execute("UPDATE users SET can_approve_po = TRUE WHERE id = %s", (approver,))
        execute("""INSERT INTO po_approval_rules (tenant_id, threshold, created_by)
                   VALUES (%s, 1, 'test')""", (tid,))
        approved_hook = _hook(tid, ["purchase_order.approved"])
        rejected_hook = _hook(tid, ["purchase_order.rejected"])
        ok_po, no_po = _po(tid), _po(tid)
        for po in (ok_po, no_po):
            client.post(f"/api/v1/inventory/po/{po}/approval/request", headers=analyst_headers)
        approvals.decide(tid, ok_po, approver, "approved")
        env = self._one(approved_hook, "purchase_order.approved")
        assert env["data"]["po_log_id"] == ok_po and env["data"]["decided_by"] == approver
        assert env["data"]["approved_amount"] == 50.0
        approvals.decide(tid, ok_po, approver, "approved")        # idempotent: no second event
        assert len(_deliveries(approved_hook)) == 1

        approvals.decide(tid, no_po, approver, "rejected", comment="over budget this month")
        env = self._one(rejected_hook, "purchase_order.rejected")
        assert env["data"]["po_log_id"] == no_po
        assert "comment" not in env["data"]

    def test_commitment_fulfilled_once_per_real_transition(self, client, analyst_headers,
                                                           test_tenant):
        tid = test_tenant["id"]
        hid = _hook(tid, ["commitment.fulfilled"])
        created = client.post("/api/v1/committed-demand", json={
            "sku": "SKU-A", "quantity": 100, "customer": "ACME",
            "delivery_date": (date.today() + timedelta(days=30)).isoformat()},
            headers=analyst_headers).json()["data"]
        for _ in range(2):
            client.post(f"/api/v1/committed-demand/{created['id']}/status",
                        json={"status": "fulfilled"}, headers=analyst_headers)
        env = self._one(hid, "commitment.fulfilled")
        assert env["data"]["commitment_id"] == created["id"] and env["data"]["customer"] == "ACME"
        assert env["data"]["fulfilled_at"]

    def test_job_events_still_reach_every_hook_with_the_envelope(self, test_tenant):
        tid = test_tenant["id"]
        plain, scoped = _hook(tid, ["job.completed"]), _hook(tid, ["job.completed"], scope=[])
        hooks.fire_webhooks(tid, "job.completed", {"job_id": "j1", "session_id": "s1"})
        for hid in (plain, scoped):
            env = self._one(hid, "job.completed")
            assert env["data"] == {"job_id": "j1", "session_id": "s1"}

    def test_a_failing_enqueue_never_breaks_the_caller(self, test_tenant, monkeypatch):
        _hook(test_tenant["id"], ["job.failed"])
        monkeypatch.setattr(hooks, "_enqueue", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("db hiccup")))
        assert hooks.emit(test_tenant["id"], "job.failed",
                          {"job_id": "j", "session_id": "s", "error": "e"}) == 0


# ── Warehouse scope ──────────────────────────────────────────────────────────

class TestScope:

    def _warehouses(self, tid):
        from backend.inventory import warehouse_service as wh_svc
        return {n: wh_svc.create_warehouse(tid, n)["id"] for n in ("Norte", "Sur")}

    def test_a_warehouse_event_reaches_company_wide_and_matching_hooks_only(self, test_tenant):
        tid = test_tenant["id"]
        wh = self._warehouses(tid)
        company = _hook(tid, ["purchase_order.sent"])
        norte = _hook(tid, ["purchase_order.sent"], scope=[wh["Norte"]])
        sur = _hook(tid, ["purchase_order.sent"], scope=[wh["Sur"]])
        none = _hook(tid, ["purchase_order.sent"], scope=[])
        po = _po(tid, warehouse="Norte")
        from backend.inventory import reception_service
        reception_service.mark_po_sent(tid, po)
        assert [len(_deliveries(h)) for h in (company, norte, sur, none)] == [1, 1, 0, 0]
        data = json.loads(_deliveries(norte)[0]["payload"])["data"]
        assert data["warehouse"] == "Norte" and data["warehouse_id"] == wh["Norte"]

    def test_a_company_wide_commitment_never_reaches_a_scoped_hook(self, test_tenant,
                                                                    client, analyst_headers):
        tid = test_tenant["id"]
        wh = self._warehouses(tid)
        company = _hook(tid, ["commitment.fulfilled"])
        norte = _hook(tid, ["commitment.fulfilled"], scope=[wh["Norte"]])
        created = client.post("/api/v1/committed-demand", json={
            "sku": "SKU-W", "quantity": 5, "delivery_date":
            (date.today() + timedelta(days=9)).isoformat()}, headers=analyst_headers).json()["data"]
        client.post(f"/api/v1/committed-demand/{created['id']}/status",
                    json={"status": "fulfilled"}, headers=analyst_headers)
        assert len(_deliveries(company)) == 1 and _deliveries(norte) == []


# ── Once per transition (state in the database) ──────────────────────────────

class TestTransitions:

    def _rows(self, *skus):
        return [{"sku": s, "warehouse": "principal", "signal": "PEDIR_YA", "current_stock": 3,
                 "coverage_days": 1.5, "reorder_point": 20} for s in skus]

    def test_stockout_imminent_only_when_a_sku_enters_pedir_ya(self, test_tenant):
        tid = test_tenant["id"]
        hid = _hook(tid, ["stockout.imminent"])
        skus = lambda: [json.loads(d["payload"])["data"]["sku"] for d in _deliveries(hid)]
        hooks.stockout_transitions(tid, self._rows("A"), default_warehouse="principal")
        assert skus() == ["A"]
        hooks.stockout_transitions(tid, self._rows("A"), default_warehouse="principal")
        assert skus() == ["A"]                                   # still critical: silent
        hooks.stockout_transitions(tid, self._rows("A", "B"), default_warehouse="principal")
        assert skus() == ["A", "B"]
        hooks.stockout_transitions(tid, [], default_warehouse="principal")   # both recovered
        assert query("SELECT 1 FROM webhook_transition_state WHERE tenant_id = %s "
                     "AND kind = 'stockout'", (tid,)) == []
        hooks.stockout_transitions(tid, self._rows("A"), default_warehouse="principal")
        assert skus() == ["A", "B", "A"]                         # re-entered: new transition
        data = json.loads(_deliveries(hid)[0]["payload"])["data"]
        assert data["signal"] == "PEDIR_YA" and data["detected_on"] == date.today().isoformat()

    def test_no_subscriber_means_no_events_and_no_remembered_state(self, test_tenant):
        tid = test_tenant["id"]
        assert hooks.stockout_transitions(tid, self._rows("A"), default_warehouse="principal") == 0
        assert query("SELECT 1 FROM webhook_transition_state WHERE tenant_id = %s", (tid,)) == []

    def test_commitment_at_risk_once_per_transition(self, client, analyst_headers, test_tenant,
                                                    monkeypatch):
        from backend.inventory import committed_demand_service as cd
        tid = test_tenant["id"]
        hid = _hook(tid, ["commitment.at_risk"])
        created = client.post("/api/v1/committed-demand", json={
            "sku": "SKU-R", "quantity": 500, "customer": "BigCo", "probability": 1,
            "delivery_date": (date.today() + timedelta(days=40)).isoformat()},
            headers=analyst_headers).json()["data"]
        flagged = {"on": True}

        def _annotate(tenant_id, items, warehouse_names=None):
            for i in items:
                i.update({"at_risk": flagged["on"], "shortfall": 120.0,
                          "latest_safe_order_date": date.today().isoformat(),
                          "order_date_passed": False, "covered_units": 0})
            return items
        monkeypatch.setattr(cd, "annotate_risk", _annotate)

        assert hooks.commitment_risk_transitions(tid) == 1
        assert hooks.commitment_risk_transitions(tid) == 0       # still at risk: silent
        env = json.loads(_deliveries(hid)[0]["payload"])
        assert env["data"]["commitment_id"] == created["id"] and env["data"]["shortfall"] == 120.0
        flagged["on"] = False
        hooks.commitment_risk_transitions(tid)
        flagged["on"] = True
        assert hooks.commitment_risk_transitions(tid) == 1       # at risk again
        assert len(_deliveries(hid)) == 2


# ── The retry state machine ──────────────────────────────────────────────────

class TestRetries:

    def _delivery(self, tid, hid):
        return hooks.enqueue_test(tid, hid)

    def _row(self, did):
        return query_one("SELECT * FROM webhook_deliveries WHERE id = %s", (did,))

    def test_5xx_retries_with_backoff_then_gives_up_after_five_attempts(
            self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid)
        did = self._delivery(tid, hid)
        Sent(monkeypatch, *[hooks.Attempt(503, None)] * 5)
        expected = [60, 300, 900, 2400]
        for attempt in range(1, 5):
            hooks.process_due()
            row = self._row(did)
            assert (row["status"], row["attempts"], row["last_status_code"]) == (
                "pending", attempt, 503)
            assert row["last_error"] == "http_503"
            wait = query_one("SELECT EXTRACT(EPOCH FROM (next_attempt_at - NOW())) AS s "
                             "FROM webhook_deliveries WHERE id = %s", (did,))["s"]
            assert expected[attempt - 1] - 5 <= wait <= expected[attempt - 1]
            _due(did)
        hooks.process_due()
        row = self._row(did)
        assert (row["status"], row["attempts"], row["next_attempt_at"]) == ("failed", 5, None)

    def test_not_due_yet_is_not_attempted(self, test_tenant, monkeypatch):
        hid = _hook(test_tenant["id"])
        did = self._delivery(test_tenant["id"], hid)
        sent = Sent(monkeypatch, hooks.Attempt(500, None))
        hooks.process_due()
        hooks.process_due()                       # backoff has not elapsed
        assert len(sent.calls) == 1 and self._row(did)["attempts"] == 1

    def test_4xx_fails_at_once_and_timeouts_retry(self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid)
        gone, slow = self._delivery(tid, hid), self._delivery(tid, hid)
        Sent(monkeypatch, hooks.Attempt(410, None), hooks.Attempt(None, "timeout"))
        hooks.process_due()
        assert self._row(gone)["status"] == "failed" and self._row(gone)["attempts"] == 1
        assert self._row(slow)["status"] == "pending" and self._row(slow)["last_error"] == "timeout"

    def test_a_refused_address_is_not_retried(self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid)
        did = self._delivery(tid, hid)
        Sent(monkeypatch, hooks.Attempt(None, "data_source_host_forbidden x", permanent=True))
        hooks.process_due()
        assert (self._row(did)["status"], self._row(did)["attempts"]) == ("failed", 1)

    def test_the_stored_error_is_truncated(self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        did = self._delivery(tid, _hook(tid))
        Sent(monkeypatch, hooks.Attempt(None, "x" * 5000))
        hooks.process_due()
        assert len(self._row(did)["last_error"]) == policy.MAX_ERROR_LENGTH

    def test_two_claims_never_take_the_same_delivery(self, test_tenant):
        tid = test_tenant["id"]
        did = self._delivery(tid, _hook(tid))
        first = [r["id"] for r in hooks.claim_due(100)]
        second = [r["id"] for r in hooks.claim_due(100)]
        assert did in first and did not in second

    def test_an_unfinished_claim_is_retried_after_its_lease(self, test_tenant):
        tid = test_tenant["id"]
        did = self._delivery(tid, _hook(tid))
        hooks.claim_due(100)                       # a worker took it and died
        _due(did)
        assert did in [r["id"] for r in hooks.claim_due(100)]

    def test_success_clears_the_failure_streak(self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid, failure_days=2, last_failure_on=date.today() - timedelta(days=1))
        did = hooks.enqueue_test(tid, hid)
        execute("UPDATE webhook_deliveries SET is_test = FALSE WHERE id = %s", (did,))
        Sent(monkeypatch, hooks.Attempt(200, None))
        hooks.process_due()
        row = query_one("SELECT failure_days, last_failure_on FROM webhooks WHERE id = %s", (hid,))
        assert row["failure_days"] == 0 and row["last_failure_on"] is None

    def test_a_deleted_or_disabled_hook_abandons_its_queue(self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid, ["job.failed"])
        hooks.emit(tid, "job.failed", {"job_id": "j", "session_id": "s", "error": "e"})
        execute("UPDATE webhooks SET disabled_at = NOW() WHERE id = %s", (hid,))
        sent = Sent(monkeypatch)
        hooks.process_due()
        assert sent.calls == []
        assert _deliveries(hid)[0]["status"] == "abandoned"
        assert hooks.emit(tid, "job.failed", {"job_id": "j", "session_id": "s", "error": "e"}) == 0


# ── Auto-disable ─────────────────────────────────────────────────────────────

class TestAutoDisable:

    def test_a_hook_failing_on_three_different_days_is_switched_off_and_the_account_told(
            self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid, failure_days=2, last_failure_on=date.today() - timedelta(days=1))
        did = hooks.enqueue_test(tid, hid)
        execute("UPDATE webhook_deliveries SET is_test = FALSE WHERE id = %s", (did,))
        Sent(monkeypatch, hooks.Attempt(404, None))
        hooks.process_due()
        row = query_one("SELECT disabled_at, disabled_reason, failure_days FROM webhooks "
                        "WHERE id = %s", (hid,))
        assert row["disabled_at"] is not None and row["failure_days"] == 3
        assert row["disabled_reason"] == "failing_for_days"
        events = query("SELECT context FROM activity_logs WHERE tenant_id = %s "
                       "AND action = 'webhook.auto_disabled' AND resource = %s", (tid, hid))
        assert len(events) == 1
        ctx = events[0]["context"]
        assert ctx["reason"] == "webhook_failing_for_days" and ctx["reason_params"]["days"] == 3

    def test_two_failures_on_one_day_count_once_and_test_events_never_count(
            self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        hid = _hook(tid)
        Sent(monkeypatch, *[hooks.Attempt(404, None)] * 3)
        real = [hooks.enqueue_test(tid, hid) for _ in range(2)]
        execute("UPDATE webhook_deliveries SET is_test = FALSE WHERE id = ANY(%s)", (real,))
        hooks.enqueue_test(tid, hid)                            # a real test event
        hooks.process_due()
        row = query_one("SELECT failure_days, disabled_at FROM webhooks WHERE id = %s", (hid,))
        assert row["failure_days"] == 1 and row["disabled_at"] is None


# ── Tenant data: export and erasure know the new tables ──────────────────────

class TestTenantData:

    def test_export_lists_the_log_without_payload_or_secret(self):
        from backend.tenants import data_export
        specs = {stem: cols for stem, _, cols in data_export._EXPORT_SPECS}
        assert "payload" not in specs["webhook_deliveries"]
        assert "secret" not in specs["webhooks"].split(", ")
        assert "warehouse_scope" in specs["webhooks"]

    def test_erasure_removes_hooks_deliveries_and_transition_state(self, make_tenant_user_headers):
        from backend.tenants import data_export
        _, doomed = make_tenant_user_headers(role="admin", return_tenant_id=True)
        _, survivor = make_tenant_user_headers(role="admin", return_tenant_id=True)
        for tid in (doomed, survivor):
            hid = _hook(tid, ["stockout.imminent"])
            hooks.enqueue_test(tid, hid)
            hooks.stockout_transitions(tid, [{"sku": "A", "warehouse": "principal",
                                              "signal": "PEDIR_YA"}],
                                       default_warehouse="principal")
        data_export.delete_tenant(doomed)
        for table in ("webhooks", "webhook_deliveries", "webhook_transition_state"):
            assert query(f"SELECT 1 FROM {table} WHERE tenant_id = %s", (doomed,)) == [], table
            assert query(f"SELECT 1 FROM {table} WHERE tenant_id = %s", (survivor,)), table
