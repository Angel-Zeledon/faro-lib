"""The message outbox: what a writer may ask for, and what the drain loop does
with each row.

The pure rules (the parameter contract per kind, the retry schedule) need no
database; every other claim is read back from `outbound_messages`. The only
network seams are `email._send` and `whatsapp._send`, replaced per test, so
nothing here leaves the machine.
"""

import pytest

from backend.db.connection import execute, query, query_one
from backend.notifications import email as email_mod
from backend.notifications import outbox
from backend.notifications import whatsapp as wa_mod


class Mailbox:
    """Records what the senders were asked to deliver; scripted failures."""

    def __init__(self):
        self.sent: list[tuple] = []
        self.fail_with: Exception | None = None

    def email(self, to, subject, html, attachment=None, tenant_id=None):
        if self.fail_with:
            raise self.fail_with
        self.sent.append(("email", to, subject, html, tenant_id))

    def whatsapp(self, to_number, body, media_url=None, tenant_id=None):
        if self.fail_with:
            raise self.fail_with
        self.sent.append(("whatsapp", to_number, body, tenant_id))


@pytest.fixture
def box(monkeypatch):
    b = Mailbox()
    monkeypatch.setattr(email_mod, "_send", b.email)
    monkeypatch.setattr(wa_mod, "_send", b.whatsapp)
    monkeypatch.setattr(email_mod, "is_configured", lambda tenant_id=None: True)
    monkeypatch.setattr(wa_mod, "is_configured", lambda tenant_id=None: True)
    # `process_due` claims every due row in the table: leftovers of other
    # tests would consume this test's scripted answers.
    execute("DELETE FROM outbound_messages WHERE status = 'pending'")
    return b


def _row(message_id):
    return query_one("SELECT * FROM outbound_messages WHERE id = %s", (message_id,))


def _make(tenant, kind="password_reset_otp", recipient="person@example.com", params=None, **kw):
    params = {"code": "123456"} if params is None else params
    return outbox.enqueue(tenant, "email", kind, recipient, params, **kw)


# ── The contract, without a database ─────────────────────────────────────────

class TestContract:

    def test_every_kind_is_registered_once_under_its_channel(self):
        assert set(outbox.CHANNELS) == {c for c, _ in outbox.KINDS}
        for (channel, name), kind in outbox.KINDS.items():
            assert (kind.channel, kind.name) == (channel, name)
            assert not set(kind.required) & set(kind.optional)

    def test_params_are_checked_against_the_kind(self):
        ok = {"code": "1"}
        assert outbox.check_params("email", "password_reset_otp", ok) is None
        assert outbox.check_params("email", "nope", ok) == "unknown_kind"
        assert outbox.check_params("whatsapp", "password_reset_otp", ok) == "unknown_kind"
        assert outbox.check_params("email", "password_reset_otp", {}) == "missing_param:code"
        assert outbox.check_params("email", "password_reset_otp", {"code": None}) == "missing_param:code"
        assert outbox.check_params("email", "password_reset_otp", {"code": "1", "x": 1}) == "unexpected_param:x"
        assert outbox.check_params("email", "password_reset_otp", ["code"]) == "params_not_an_object"

    def test_backoff_gives_five_attempts_then_stops(self):
        assert outbox.MAX_ATTEMPTS == 5
        assert [outbox.next_delay_seconds(n) for n in range(0, 6)] == [None, 30, 120, 600, 2400, None]

    def test_errors_are_cut_and_cleaned(self):
        assert len(outbox.truncate_error("x" * 1000)) == outbox.MAX_ERROR_LENGTH
        assert "\x00" not in outbox.truncate_error("a\x00b")


# ── Enqueue ──────────────────────────────────────────────────────────────────

class TestEnqueue:

    def test_a_row_is_written_exactly_as_asked(self, test_tenant):
        mid = _make(test_tenant["id"], recipient="  a@b.co ", created_by="usr_1", dedupe_key="k1", ttl_seconds=120)
        row = _row(mid)
        assert (row["tenant_id"], row["channel"], row["kind"], row["recipient"]) == (
            test_tenant["id"], "email", "password_reset_otp", "a@b.co")
        assert row["params"] == {"code": "123456"}
        assert (row["status"], row["attempts"], row["created_by"], row["dedupe_key"]) == ("pending", 0, "usr_1", "k1")
        assert row["sent_at"] is None and row["last_error"] is None
        ttl = (row["expires_at"] - row["created_at"]).total_seconds()
        assert 119 <= ttl <= 121

    def test_a_refused_request_writes_nothing(self, test_tenant):
        tid = test_tenant["id"]
        assert outbox.enqueue(tid, "email", "nope", "a@b.co", {}) is None
        assert outbox.enqueue(tid, "email", "password_reset_otp", "a@b.co", {}) is None
        assert outbox.enqueue(tid, "email", "password_reset_otp", "   ", {"code": "1"}) is None
        assert outbox.enqueue(tid, "sms", "password_reset_otp", "a@b.co", {"code": "1"}) is None
        assert query("SELECT 1 FROM outbound_messages WHERE tenant_id = %s", (tid,)) == []

    def test_the_same_dedupe_key_queues_once_per_tenant(self, test_tenant, make_tenant_user_headers):
        _, other = make_tenant_user_headers(role="admin", return_tenant_id=True)
        tid = test_tenant["id"]
        first = _make(tid, dedupe_key="evt-1")
        assert first is not None
        assert _make(tid, dedupe_key="evt-1") is None
        assert _make(other, dedupe_key="evt-1") is not None     # another tenant is another key space
        assert _make(tid, dedupe_key="evt-2") is not None
        assert _make(tid) is not None and _make(tid) is not None  # no key: never deduplicated
        assert len(query("SELECT 1 FROM outbound_messages WHERE tenant_id = %s", (tid,))) == 4

    def test_the_ttl_is_clamped(self, test_tenant):
        row = _row(_make(test_tenant["id"], ttl_seconds=10 ** 9))
        assert (row["expires_at"] - row["created_at"]).total_seconds() <= outbox.MAX_TTL_SECONDS + 1


# ── Drain ────────────────────────────────────────────────────────────────────

class TestDrain:

    def test_a_row_is_sent_through_the_existing_sender_and_scrubbed(self, test_tenant, box):
        mid = _make(test_tenant["id"], recipient="who@example.com", params={"code": "654321"})
        assert outbox.process_due() == 1
        row = _row(mid)
        assert (row["status"], row["attempts"], row["last_error"]) == ("sent", 1, None)
        assert row["sent_at"] is not None and row["next_attempt_at"] is None
        assert row["params"] == {}, "a one-time code must not outlive the send"
        (channel, to, subject, html, _tenant), = box.sent
        assert (channel, to) == ("email", "who@example.com")
        assert "654321" in html and subject

    def test_each_kind_reaches_its_sender_with_its_params(self, test_tenant, box):
        tid = test_tenant["id"]
        outbox.enqueue(tid, "email", "verification", "v@example.com",
                       {"verify_url": "https://x.test/verify?t=abc", "full_name": "Vera"})
        outbox.enqueue(tid, "email", "password_reset", "r@example.com", {"reset_url": "https://x.test/reset?t=def"})
        outbox.enqueue(tid, "email", "account_setup", "s@example.com", {"setup_url": "https://x.test/setup?t=ghi"})
        outbox.enqueue(tid, "email", "change_password_code", "c@example.com", {"code": "111111"})
        outbox.enqueue(tid, "whatsapp", "verification_code", "+50688887777", {"code": "222222"})
        assert outbox.process_due() == 5
        bodies = {s[1]: s for s in box.sent}
        assert "https://x.test/verify?t=abc" in bodies["v@example.com"][3] and "Vera" in bodies["v@example.com"][3]
        assert "https://x.test/reset?t=def" in bodies["r@example.com"][3]
        assert "https://x.test/setup?t=ghi" in bodies["s@example.com"][3]
        assert "111111" in bodies["c@example.com"][3]
        assert "222222" in bodies["+50688887777"][2]
        assert {r["status"] for r in query("SELECT status FROM outbound_messages WHERE tenant_id = %s", (tid,))} == {"sent"}

    def test_po_approval_kinds_resolve_names_and_the_reference_at_send_time(self, test_tenant, registered_user, box):
        from backend.inventory import roi_service
        tid = registered_user["tenant"]["id"]
        po = roi_service.log_po_generation(
            tid, "sess-test", [{"sku": "A-1", "final_qty": 10, "unit_cost": 5.0, "status": "approved",
                                "supplier": "Acme"}])["id"]
        outbox.enqueue(tid, "email", "po_approval_request", "approver@example.com",
                       {"po_log_id": po, "amount": 1234.5, "requester_id": registered_user["user"]["id"]})
        outbox.enqueue(tid, "email", "po_approval_decision", "asker@example.com",
                       {"po_log_id": po, "amount": 1234.5, "approved": False, "comment": "too much",
                        "decider_id": registered_user["user"]["id"]})
        assert outbox.process_due() == 2
        by_to = {s[1]: s for s in box.sent}
        assert po in by_to["approver@example.com"][3] and "Test Admin" in by_to["approver@example.com"][3]
        assert by_to["approver@example.com"][4] == tid, "a tenant's own approvals go through its own transport"
        assert "too much" in by_to["asker@example.com"][3]

    def test_a_trial_address_is_never_mailed_and_says_so(self, test_tenant, box):
        mid = _make(test_tenant["id"], recipient="demo-abc123@stockai.demo")
        outbox.process_due()
        row = _row(mid)
        assert (row["status"], row["last_error"], row["params"]) == ("abandoned", "trial_address", {})
        assert box.sent == []

    def test_no_transport_fails_at_once_with_the_reason(self, test_tenant, box, monkeypatch):
        monkeypatch.setattr(email_mod, "is_configured", lambda tenant_id=None: False)
        mid = _make(test_tenant["id"])
        outbox.process_due()
        row = _row(mid)
        assert (row["status"], row["attempts"], row["last_error"], row["params"]) == (
            "failed", 1, "not_configured", {})
        assert box.sent == []

    def test_a_failing_provider_is_retried_on_the_schedule_then_given_up(self, test_tenant, box):
        box.fail_with = RuntimeError("provider down")
        mid = _make(test_tenant["id"])
        for attempt in range(1, outbox.MAX_ATTEMPTS):
            assert outbox.process_due() == 1
            row = _row(mid)
            assert (row["status"], row["attempts"]) == ("pending", attempt)
            assert row["params"] == {"code": "123456"}, "kept while there is another attempt"
            expected = outbox.BACKOFF_SECONDS[attempt - 1]
            wait = query_one("SELECT EXTRACT(EPOCH FROM (next_attempt_at - NOW())) AS s FROM outbound_messages "
                             "WHERE id = %s", (mid,))["s"]
            assert expected - 5 <= float(wait) <= expected + 1
            execute("UPDATE outbound_messages SET next_attempt_at = NOW() WHERE id = %s", (mid,))
        assert outbox.process_due() == 1
        row = _row(mid)
        assert (row["status"], row["attempts"], row["params"], row["next_attempt_at"]) == (
            "failed", outbox.MAX_ATTEMPTS, {}, None)
        assert row["last_error"]
        assert outbox.process_due() == 0, "a failed row is final"

    def test_a_row_that_is_not_due_is_left_alone(self, test_tenant, box):
        mid = _make(test_tenant["id"])
        execute("UPDATE outbound_messages SET next_attempt_at = NOW() + INTERVAL '1 hour' WHERE id = %s", (mid,))
        assert outbox.process_due() == 0
        assert (_row(mid)["status"], _row(mid)["attempts"]) == ("pending", 0)

    def test_overdue_rows_are_abandoned_and_scrubbed_not_sent(self, test_tenant, box):
        mid = _make(test_tenant["id"])
        execute("UPDATE outbound_messages SET expires_at = NOW() - INTERVAL '1 second' WHERE id = %s", (mid,))
        assert outbox.process_due() == 0
        row = _row(mid)
        assert (row["status"], row["last_error"], row["params"]) == ("abandoned", "expired", {})
        assert box.sent == []

    def test_a_claimed_row_is_not_claimed_twice_until_its_lease_runs_out(self, test_tenant, box):
        mid = _make(test_tenant["id"])
        first = outbox.claim_due()
        assert [r["id"] for r in first] == [mid] and first[0]["attempts"] == 1
        assert outbox.claim_due() == []
        execute("UPDATE outbound_messages SET next_attempt_at = NOW() WHERE id = %s", (mid,))
        assert [r["attempts"] for r in outbox.claim_due()] == [2]

    def test_a_row_the_registry_does_not_know_fails_loudly(self, test_tenant, box):
        row = query_one(
            """INSERT INTO outbound_messages (tenant_id, channel, kind, recipient, params, next_attempt_at, expires_at)
               VALUES (%s, 'email', 'from_the_future', 'a@b.co', '{"x": 1}'::jsonb, NOW(), NOW() + INTERVAL '1 hour')
               RETURNING id""", (test_tenant["id"],))
        outbox.process_due()
        stored = _row(row["id"])
        assert (stored["status"], stored["last_error"], stored["params"]) == ("failed", "unknown_kind", {})
        assert box.sent == []

    def test_a_row_with_the_wrong_params_fails_instead_of_sending_garbage(self, test_tenant, box):
        row = query_one(
            """INSERT INTO outbound_messages (tenant_id, channel, kind, recipient, params, next_attempt_at, expires_at)
               VALUES (%s, 'email', 'password_reset_otp', 'a@b.co', '{}'::jsonb, NOW(), NOW() + INTERVAL '1 hour')
               RETURNING id""", (test_tenant["id"],))
        outbox.process_due()
        stored = _row(row["id"])
        assert (stored["status"], stored["last_error"]) == ("failed", "missing_param:code")
        assert box.sent == []

    def test_one_crashing_row_does_not_stall_the_batch(self, test_tenant, box):
        tid = test_tenant["id"]
        first, second = _make(tid, recipient="one@example.com"), _make(tid, recipient="two@example.com")
        seen = []

        def deliver(row):
            seen.append(row["id"])
            if row["id"] == first:
                raise RuntimeError("boom")
            return outbox.deliver_one(row)

        assert outbox.process_due(deliver=deliver) == 2
        assert sorted(seen) == sorted([first, second])
        assert _row(second)["status"] == "sent"
        assert _row(first)["status"] == "pending", "the lease will bring it back"


# ── Tenant data ──────────────────────────────────────────────────────────────

class TestTenantData:

    def test_export_lists_the_outbox_without_its_payload(self):
        from backend.tenants import data_export
        specs = {stem: cols for stem, _, cols in data_export._EXPORT_SPECS}
        assert "outbound_messages" in specs
        assert "params" not in [c.strip() for c in specs["outbound_messages"].split(",")]

    def test_erasure_removes_the_tenants_messages_and_only_theirs(self, make_tenant_user_headers):
        from backend.tenants import data_export
        _, doomed = make_tenant_user_headers(role="admin", return_tenant_id=True)
        _, survivor = make_tenant_user_headers(role="admin", return_tenant_id=True)
        for tid in (doomed, survivor):
            _make(tid)
        data_export.delete_tenant(doomed)
        assert query("SELECT 1 FROM outbound_messages WHERE tenant_id = %s", (doomed,)) == []
        assert query("SELECT 1 FROM outbound_messages WHERE tenant_id = %s", (survivor,))
