"""Continuous audit export: the pure rules, then every claim read back from the
tables. No test posts to a real server: the single network seam is the `send`
argument of `deliver_stream` (the real one is the webhook SSRF-guarded `_send`).

The property that matters most is `test_concurrent_writers_*`: with several
transactions writing at once and committing out of order, a draining reader
delivers every row exactly once, in order, and never one BEHIND its cursor.
"""

import json
import random
import threading
import time
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.audit_stream import policy
from backend.audit_stream import service as stream
from backend.db.connection import execute, get_conn, query, query_one
from backend.webhooks import signing
from backend.webhooks.service import Attempt


# -- Pure ---------------------------------------------------------------------

@pytest.mark.offline
class TestCursor:
    def test_round_trip(self):
        assert policy.parse_cursor(policy.format_cursor(123, 456)) == (123, 456)
        assert policy.parse_cursor("0:0") == (0, 0)

    @pytest.mark.parametrize("bad", ["", "1", "1:", ":1", "a:1", "1:b", "-1:2", "1:-2", "1:2:3",
                                     "1.5:2", " 1:2", "1:2 ", "١:2", None, 7,
                                     "99999999999999999999:1"])
    def test_rejects_anything_else(self, bad):
        assert policy.parse_cursor(bad) is None


@pytest.mark.offline
class TestBatchFormat:
    def _rec(self, i, text="x"):
        return {"schema": policy.SCHEMA, "cursor": f"1:{i}", "id": f"act_{i}", "context": {"t": text}}

    def test_one_compact_json_object_per_line(self):
        body, n = policy.to_ndjson([self._rec(1), self._rec(2, "ñandú")])
        assert n == 2 and body.endswith(b"\n")
        lines = body.decode("utf-8").split("\n")
        assert lines[-1] == ""
        assert [json.loads(l)["cursor"] for l in lines[:-1]] == ["1:1", "1:2"]
        assert "ñandú" in lines[1]            # UTF-8, not \u escapes
        assert ", " not in lines[0] and ": " not in lines[0]

    def test_byte_cap_cuts_between_records_and_reports_how_many(self):
        one = len(policy.to_ndjson([self._rec(1)])[0])
        body, n = policy.to_ndjson([self._rec(i) for i in range(10)], max_bytes=one * 3 + 1)
        assert n == 3 and len(body) <= one * 3 + 1

    def test_a_lone_oversized_record_is_sent_whole(self):
        body, n = policy.to_ndjson([self._rec(1, "y" * 5000), self._rec(2)], max_bytes=100)
        assert n == 1 and len(body) > 100

    def test_record_carries_stable_identity_and_the_stored_action(self):
        row = {"id": "act_1", "tenant_id": "t", "user_id": "u", "action": "audit.dataset.deleted",
               "resource": "ds1", "status": "success", "context": '{"a": 1}',
               "created_at": date(2026, 1, 2), "stream_xid": 9, "stream_seq": 4}
        rec = policy.record_for(row, kind="audit", event="dataset.deleted", target_type="dataset")
        assert rec["cursor"] == "9:4" and rec["action"] == "audit.dataset.deleted"
        assert rec["event"] == "dataset.deleted" and rec["context"] == {"a": 1}
        assert rec["at"] == "2026-01-02" and rec["schema"] == "stockai.audit.v1"


@pytest.mark.offline
class TestRetryRules:
    def test_backoff_grows_then_repeats_the_last_delay(self):
        delays = [policy.next_delay_seconds(n) for n in range(1, 12)]
        assert delays[:7] == list(policy.BACKOFF_SECONDS)
        assert set(delays[7:]) == {policy.BACKOFF_SECONDS[-1]}
        assert policy.next_delay_seconds(0) == policy.BACKOFF_SECONDS[0]

    @pytest.mark.parametrize("status,error,expected", [
        (200, None, "success"), (204, None, "success"),
        (500, None, "retry"), (401, None, "retry"), (404, None, "retry"),
        (301, None, "retry"), (None, "timeout", "retry"), (None, None, "retry"),
    ])
    def test_only_a_2xx_is_success_and_nothing_is_permanent(self, status, error, expected):
        assert policy.classify_outcome(status, error) == expected

    def test_failure_days_are_the_webhook_rule(self):
        d = date(2026, 1, 1)
        assert policy.advance_failure_days(0, None, d) == (1, d)
        assert policy.advance_failure_days(1, d, d) == (1, d)
        assert policy.advance_failure_days(1, d, d + timedelta(days=1)) == (2, d + timedelta(days=1))
        assert not policy.should_disable(2) and policy.should_disable(3)


@pytest.mark.offline
def test_classify_separates_the_audit_trail_from_the_rest_of_the_feed():
    assert stream.classify("audit.dataset.deleted") == ("audit", "dataset.deleted", "dataset")
    assert stream.classify("session.delete") == ("audit", "session.deleted", "session")
    assert stream.classify("audit_stream.auto_disabled") == (
        "audit", "audit_stream.auto_disabled", "audit_stream")
    assert stream.classify("alert.sent") == ("activity", None, None)


# -- Database -----------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _stream_row(tid, *, cursor=(0, 0), **cols):
    execute(
        """INSERT INTO audit_streams (tenant_id, url, secret, cursor_xid, cursor_seq)
           VALUES (%s, 'https://siem.example.test/in', %s, %s, %s)""",
        (tid, "secret-" + uuid4().hex, cursor[0], cursor[1]))
    for col, value in cols.items():
        execute(f"UPDATE audit_streams SET {col} = %s WHERE tenant_id = %s", (value, tid))


def _act(tid, action="alert.sent", n=1, *, conn=None):
    ids = []
    for _ in range(n):
        rid = "act_" + uuid4().hex[:12]
        execute(
            """INSERT INTO activity_logs (id, tenant_id, user_id, action, resource, context, status)
               VALUES (%s, %s, 'u1', %s, 'r', '{"k": 1}', 'success')""",
            (rid, tid, action), conn=conn)
        ids.append(rid)
    return ids


def _row(tid):
    return query_one("SELECT * FROM audit_streams WHERE tenant_id = %s", (tid,))


def _log(tid):
    return query("SELECT * FROM audit_stream_deliveries WHERE tenant_id = %s ORDER BY created_at, id",
                 (tid,))


def _lease(tid):
    rows = stream.claim_due(tenant_id=tid)
    assert len(rows) == 1
    return rows[0]


def _head(tid):
    r = query_one("SELECT max(stream_xid) AS x FROM activity_logs WHERE tenant_id = %s", (tid,))
    return r["x"]


class Sender:
    def __init__(self, *answers):
        self.calls = []
        self.answers = list(answers)

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        if self.answers:
            a = self.answers.pop(0)
            return a(body) if callable(a) else a
        return Attempt(200, None)

    def ids(self, call=0):
        return [json.loads(l)["id"] for l in self.calls[call][1].decode().splitlines()]


class TestWriters:
    def test_every_writer_is_stamped_by_column_defaults(self, test_tenant):
        from backend.activity.service import log_action
        tid = test_tenant["id"]
        log_action(tid, "u1", "alert.sent", "x", {"a": 1})
        _act(tid, n=2)
        rows = query("SELECT stream_xid, stream_seq FROM activity_logs WHERE tenant_id = %s "
                     "ORDER BY stream_seq", (tid,))
        assert len(rows) == 3 and all(r["stream_xid"] and r["stream_seq"] for r in rows)
        seqs = [r["stream_seq"] for r in rows]
        assert seqs == sorted(set(seqs))

    def test_rows_without_a_position_are_never_streamed(self, test_tenant):
        tid = test_tenant["id"]
        execute("""INSERT INTO activity_logs (id, tenant_id, user_id, action, stream_xid, stream_seq)
                   VALUES ('act_old', %s, 'u', 'alert.sent', NULL, NULL)""", (tid,))
        fresh = _act(tid, n=2)
        assert [r["id"] for r in stream.read_batch(tid, (0, 0), 10)] == fresh


class TestDelivery:
    def test_delivers_in_order_signed_and_advances_the_cursor(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        ids = _act(tid, n=3)
        send = Sender()
        assert stream.deliver_stream(_lease(tid), send=send) == "delivered"
        assert send.ids() == ids
        url, body, headers = send.calls[0]
        assert url == "https://siem.example.test/in"
        assert headers["Content-Type"] == "application/x-ndjson"
        assert headers["X-StockAI-Batch-Records"] == "3"
        secret = _row(tid)["secret"]
        assert signing.verify(secret, headers["X-StockAI-Signature"], body)
        assert not signing.verify(secret + "x", headers["X-StockAI-Signature"], body)
        recs = [json.loads(l) for l in body.decode().splitlines()]
        assert recs[2]["cursor"] == headers["X-StockAI-Batch-Last"]
        assert recs[0]["cursor"] == headers["X-StockAI-Batch-First"]
        assert all(r["tenant_id"] == tid and r["kind"] == "activity" for r in recs)
        row = _row(tid)
        assert policy.format_cursor(row["cursor_xid"], row["cursor_seq"]) == recs[2]["cursor"]
        assert row["delivered_records"] == 3 and row["consecutive_failures"] == 0
        assert row["lease_token"] is None and row["last_success_at"] is not None
        log = _log(tid)
        assert [(l["kind"], l["status"], l["records"]) for l in log] == [("batch", "delivered", 3)]

    def test_nothing_new_sends_nothing(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        _act(tid, n=2)
        stream.deliver_stream(_lease(tid), send=Sender())
        execute("UPDATE audit_streams SET next_attempt_at = NOW() WHERE tenant_id = %s", (tid,))
        send = Sender()
        assert stream.deliver_stream(_lease(tid), send=send) == "idle"
        assert send.calls == [] and len(_log(tid)) == 1

    def test_a_full_batch_is_followed_at_once_and_a_partial_one_waits(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid, batch_size=2)
        _act(tid, n=3)
        stream.deliver_stream(_lease(tid), send=Sender())
        assert query_one("SELECT next_attempt_at <= NOW() AS due FROM audit_streams "
                         "WHERE tenant_id = %s", (tid,))["due"] is True
        stream.deliver_stream(_lease(tid), send=Sender())          # the 1 left
        assert query_one("SELECT next_attempt_at > NOW() AS later FROM audit_streams "
                         "WHERE tenant_id = %s", (tid,))["later"] is True
        assert _row(tid)["delivered_records"] == 3

    def test_an_audit_row_is_normalised_in_the_record(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        _act(tid, action="audit.webhook.deleted")
        send = Sender()
        stream.deliver_stream(_lease(tid), send=send)
        rec = json.loads(send.calls[0][1])
        assert (rec["kind"], rec["event"], rec["target_type"]) == (
            "audit", "webhook.deleted", "webhook")

    def test_a_failure_keeps_the_cursor_and_the_same_rows_come_again(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        ids = _act(tid, n=2)
        down = Sender(Attempt(503, None))
        assert stream.deliver_stream(_lease(tid), send=down) == "failed"
        row = _row(tid)
        assert (row["cursor_xid"], row["cursor_seq"]) == (0, 0)
        assert row["consecutive_failures"] == 1 and row["failure_days"] == 1
        assert row["last_status_code"] == 503 and row["last_error"] == "http_503"
        assert row["enabled"] is True and row["lease_token"] is None
        assert query_one("SELECT next_attempt_at > NOW() AS later FROM audit_streams "
                         "WHERE tenant_id = %s", (tid,))["later"] is True
        # more rows arrive while it is down: the retry carries them too, in order
        ids += _act(tid)
        execute("UPDATE audit_streams SET next_attempt_at = NOW() WHERE tenant_id = %s", (tid,))
        up = Sender()
        assert stream.deliver_stream(_lease(tid), send=up) == "delivered"
        assert up.ids() == ids
        row = _row(tid)
        assert row["consecutive_failures"] == 0 and row["failure_days"] == 0
        assert [l["status"] for l in _log(tid)] == ["failed", "delivered"]

    def test_a_timeout_after_the_receiver_got_it_resends_the_same_batch(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        ids = _act(tid, n=2)
        lost = Sender(Attempt(None, "timeout"))
        assert stream.deliver_stream(_lease(tid), send=lost) == "failed"
        execute("UPDATE audit_streams SET next_attempt_at = NOW() WHERE tenant_id = %s", (tid,))
        again = Sender()
        stream.deliver_stream(_lease(tid), send=again)
        assert lost.ids() == again.ids() == ids          # at least once: the id is the dedupe key

    def test_one_batch_per_tenant_in_flight(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        _act(tid)
        assert len(stream.claim_due(tenant_id=tid)) == 1
        assert stream.claim_due(tenant_id=tid) == []

    def test_an_expired_lease_is_reclaimed(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        stream.claim_due(tenant_id=tid)
        execute("UPDATE audit_streams SET lease_until = NOW() - interval '1 second' "
                "WHERE tenant_id = %s", (tid,))
        assert len(stream.claim_due(tenant_id=tid)) == 1

    def test_a_disabled_stream_is_not_claimed(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid, enabled=False)
        _act(tid)
        assert stream.claim_due(tenant_id=tid) == []

    def test_a_tenant_never_receives_another_tenants_rows(self, test_tenant):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            tid = test_tenant["id"]
            _stream_row(tid)
            mine = _act(tid)
            _act(other["id"], n=2)
            send = Sender()
            stream.deliver_stream(_lease(tid), send=send)
            assert send.ids() == mine
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))
            execute("DELETE FROM activity_logs WHERE tenant_id = %s", (other["id"],))

    def test_a_replay_during_flight_wins_over_the_late_success(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        _act(tid, n=3)

        def replay_while_in_flight(body):
            execute("UPDATE audit_streams SET cursor_xid = 0, cursor_seq = 1 WHERE tenant_id = %s",
                    (tid,))
            return Attempt(200, None)

        out = stream.deliver_stream(_lease(tid), send=Sender(replay_while_in_flight))
        assert out == "superseded"
        row = _row(tid)
        assert (row["cursor_xid"], row["cursor_seq"]) == (0, 1)        # the replay, untouched
        assert row["delivered_records"] == 0 and row["lease_token"] is None
        assert [l["status"] for l in _log(tid)] == ["superseded"]

    def test_the_log_is_trimmed(self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        monkeypatch.setattr(stream, "LOG_KEEP_ROWS", 3)
        for _ in range(6):
            stream._log_attempt(tid, kind="batch", status="delivered", records=1)
        assert len(_log(tid)) == 3


class TestAutoDisable:
    def _events(self, tid):
        return query("SELECT * FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'audit_stream.auto_disabled'", (tid,))

    def test_third_failing_day_switches_it_off_keeps_the_cursor_and_tells_the_account(
            self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid, failure_days=2, last_failure_on=date.today() - timedelta(days=1),
                    consecutive_failures=40)
        _act(tid)
        assert stream.deliver_stream(_lease(tid), send=Sender(Attempt(500, None))) == "disabled"
        row = _row(tid)
        assert row["enabled"] is False and row["disabled_reason"] == "failing_for_days"
        assert row["disabled_at"] is not None and (row["cursor_xid"], row["cursor_seq"]) == (0, 0)
        ev = self._events(tid)
        assert len(ev) == 1
        ctx = ev[0]["context"]
        assert ctx["severity"] == "warning" and ctx["reason"] == "audit_stream_failing_for_days"
        assert ctx["reason_params"] == {"days": 3} and ctx["host"] == "siem.example.test"
        assert stream.claim_due(tenant_id=tid) == []

    def test_two_failures_on_one_day_count_once(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        _act(tid)
        stream.deliver_stream(_lease(tid), send=Sender(Attempt(500, None)))
        execute("UPDATE audit_streams SET next_attempt_at = NOW() WHERE tenant_id = %s", (tid,))
        stream.deliver_stream(_lease(tid), send=Sender(Attempt(500, None)))
        row = _row(tid)
        assert row["failure_days"] == 1 and row["consecutive_failures"] == 2 and row["enabled"]
        assert self._events(tid) == []

    def test_a_refused_address_disables_at_once(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        _act(tid)
        refused = Attempt(None, "data_source_host_forbidden siem", True)
        assert stream.deliver_stream(_lease(tid), send=Sender(refused)) == "disabled"
        row = _row(tid)
        assert row["enabled"] is False and row["disabled_reason"] == "host_refused"
        assert self._events(tid)[0]["context"]["reason"] == "audit_stream_host_refused"

    def test_a_success_resets_the_streak(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid, failure_days=2, last_failure_on=date.today() - timedelta(days=1),
                    consecutive_failures=5)
        _act(tid)
        assert stream.deliver_stream(_lease(tid), send=Sender()) == "delivered"
        row = _row(tid)
        assert (row["failure_days"], row["consecutive_failures"], row["last_failure_on"]) == (0, 0, None)

    def test_a_crash_in_delivery_is_a_failure_not_a_stall(self, test_tenant, monkeypatch):
        tid = test_tenant["id"]
        _stream_row(tid)
        _act(tid)

        def boom(stream_row, **kw):
            raise RuntimeError("boom")
        monkeypatch.setattr(stream, "deliver_stream", boom)
        assert stream._deliver_safely(_lease(tid)) == "failed"
        row = _row(tid)
        assert row["consecutive_failures"] == 1 and row["last_error"] == "RuntimeError"
        assert row["lease_token"] is None


class TestTestDelivery:
    def test_a_test_sends_one_record_and_leaves_cursor_and_counters_alone(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid, test_requested_at=_now())
        ids = _act(tid, n=2)
        send = Sender()
        assert stream.deliver_stream(_lease(tid), send=send) == "test"
        rec = json.loads(send.calls[0][1])
        assert rec["kind"] == "test" and rec["cursor"] is None
        row = _row(tid)
        assert row["test_requested_at"] is None and (row["cursor_xid"], row["cursor_seq"]) == (0, 0)
        assert [(l["kind"], l["status"]) for l in _log(tid)] == [("test", "delivered")]
        # the real rows follow on the next turn
        again = Sender()
        stream.deliver_stream(_lease(tid), send=again)
        assert again.ids() == ids

    def test_a_failing_test_never_counts_toward_auto_disable(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid, test_requested_at=_now())
        stream.deliver_stream(_lease(tid), send=Sender(Attempt(500, None)))
        row = _row(tid)
        assert row["consecutive_failures"] == 0 and row["failure_days"] == 0 and row["enabled"]
        assert [(l["kind"], l["status"]) for l in _log(tid)] == [("test", "failed")]

    def test_a_disabled_stream_can_still_be_tested(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid, enabled=False, test_requested_at=_now())
        send = Sender()
        assert stream.deliver_stream(_lease(tid), send=send) == "test"
        assert len(send.calls) == 1 and _row(tid)["enabled"] is False
        assert stream.claim_due(tenant_id=tid) == []


class TestOrderUnderConcurrentWriters:
    """The reason the position is the writing transaction and not `created_at`."""

    def test_a_row_still_in_an_open_transaction_holds_later_commits_back(self, test_tenant):
        tid = test_tenant["id"]
        slow = get_conn()
        conn = slow.__enter__()
        try:
            slow_id = _act(tid, conn=conn)[0]                 # xid A, uncommitted
            fast_id = _act(tid)[0]                            # xid B > A, committed
            # B is committed and visible, but A's transaction has not finished: a
            # cursor that moved past B would leave A behind it for ever.
            assert stream.read_batch(tid, (0, 0), 10) == []
        finally:
            slow.__exit__(None, None, None)                   # commit A
        rows = stream.read_batch(tid, (0, 0), 10)
        assert [r["id"] for r in rows] == [slow_id, fast_id]  # A first (earlier xid), none lost

    def test_a_rolled_back_writer_does_not_stall_the_stream(self, test_tenant):
        tid = test_tenant["id"]
        slow = get_conn()
        conn = slow.__enter__()
        try:
            _act(tid, conn=conn)
            keep = _act(tid)[0]
            assert stream.read_batch(tid, (0, 0), 10) == []
            conn.rollback()
        finally:
            slow.__exit__(None, None, None)
        assert [r["id"] for r in stream.read_batch(tid, (0, 0), 10)] == [keep]

    def test_concurrent_writers_each_row_exactly_once_in_order(self, test_tenant):
        tid = test_tenant["id"]
        _stream_row(tid)
        writers, per_writer = 6, 40
        written: list[str] = []
        lock = threading.Lock()
        done = threading.Event()
        stop = threading.Event()      # a failing run must end the drain, not hang pytest
        errors: list[BaseException] = []

        def writer(seed):
            rnd = random.Random(seed)
            try:
                for _ in range(per_writer):
                    ctx = get_conn()
                    conn = ctx.__enter__()
                    try:
                        # several rows per transaction, a pause BETWEEN insert and
                        # commit so commits interleave out of insert order
                        ids = _act(tid, n=rnd.randint(1, 3), conn=conn)
                        time.sleep(rnd.random() * 0.01)
                    finally:
                        ctx.__exit__(None, None, None)
                    with lock:
                        written.extend(ids)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(writers)]
        for t in threads:
            t.start()

        delivered: list[str] = []
        positions: list[tuple[int, int]] = []

        def drain():
            send = Sender()
            n = 0
            while not stop.is_set():
                execute("UPDATE audit_streams SET next_attempt_at = NOW() WHERE tenant_id = %s", (tid,))
                row = stream.claim_due(tenant_id=tid)
                if not row:
                    time.sleep(0.01)
                    continue
                before = len(send.calls)
                result = stream.deliver_stream(row[0], send=send)
                if len(send.calls) > before:
                    for line in send.calls[-1][1].decode().splitlines():
                        rec = json.loads(line)
                        delivered.append(rec["id"])
                        positions.append(tuple(int(x) for x in rec["cursor"].split(":")))
                if done.is_set() and result == "idle":
                    return
                n += 1

        drainer = threading.Thread(target=drain, daemon=True)
        drainer.start()
        for t in threads:
            t.join()
        done.set()
        drainer.join(timeout=60)
        stop.set()
        assert not drainer.is_alive(), "the drain never caught up"
        drainer.join(timeout=5)
        assert errors == []
        assert len(written) == len(set(written)) > writers * per_writer
        assert sorted(delivered) == sorted(written), "a row was lost or duplicated"
        assert len(delivered) == len(set(delivered))
        assert positions == sorted(positions), "delivery order is the stream order"
        row = _row(tid)
        assert row["delivered_records"] == len(written)
        assert (row["cursor_xid"], row["cursor_seq"]) == positions[-1]


class TestTenantErasure:
    def test_erasing_a_tenant_removes_its_destination_and_log(self):
        from backend.tenants.data_export import delete_tenant
        from backend.tenants.service import create_tenant
        t = create_tenant(f"pytest-{uuid4().hex[:10]}")
        tid = t["id"]
        _stream_row(tid)
        _act(tid)
        stream._log_attempt(tid, kind="batch", status="delivered", records=1)
        delete_tenant(tid)
        for table in ("audit_streams", "audit_stream_deliveries", "activity_logs"):
            assert query_one(f"SELECT 1 AS x FROM {table} WHERE tenant_id = %s", (tid,)) is None

    def test_the_export_never_carries_the_signing_secret(self):
        from backend.tenants import data_export
        spec = {name: cols for name, _t, cols in data_export._EXPORT_SPECS}
        assert "audit_streams" in spec and "audit_stream_deliveries" in spec
        columns = [c.strip() for c in spec["audit_streams"].split(",")]
        assert "secret" not in columns and "lease_token" not in columns
