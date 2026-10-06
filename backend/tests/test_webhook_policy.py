"""Pure rules of outbound webhooks: signature, retry schedule, payload schema
stability, scope filtering and once-per-transition. No database, no network."""

import json
from datetime import date, datetime, timezone

import pytest

from backend.webhooks import catalog, policy, signing
from backend.webhooks.service import select_recipients

pytestmark = pytest.mark.offline


# ── signature ────────────────────────────────────────────────────────────────

def test_signature_is_hmac_over_timestamp_dot_body():
    import hashlib, hmac
    body = b'{"a":1}'
    expected = hmac.new(b"s3cret", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert signing.sign("s3cret", 1700000000, body) == expected
    assert signing.signature_header("s3cret", 1700000000, body) == f"t=1700000000,v1={expected}"


def test_receiver_recipe_accepts_a_fresh_valid_signature():
    body = b'{"id":"evt_1"}'
    header = signing.signature_header("k", 1700000000, body)
    assert signing.verify("k", header, body, now=1700000100)


def test_receiver_recipe_rejects_tamper_wrong_secret_and_replay():
    body = b'{"id":"evt_1"}'
    header = signing.signature_header("k", 1700000000, body)
    assert not signing.verify("k", header, b'{"id":"evt_2"}', now=1700000100)
    assert not signing.verify("other", header, body, now=1700000100)
    assert not signing.verify("k", header, body, now=1700000000 + 301)   # replay window
    assert not signing.verify("k", "garbage", body, now=1700000100)
    assert not signing.verify("k", "t=abc,v1=00", body, now=1700000100)


def test_legacy_header_still_signs_the_body_alone():
    import hashlib, hmac
    body = b"{}"
    assert signing.legacy_signature("k", body) == \
        "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()


# ── retry schedule ───────────────────────────────────────────────────────────

def test_backoff_schedule_is_five_attempts_over_about_an_hour():
    assert policy.MAX_ATTEMPTS == 5
    delays = [policy.next_delay_seconds(n) for n in range(1, 5)]
    assert delays == [60, 300, 900, 2400]
    assert sum(delays) == 3660
    assert policy.next_delay_seconds(5) is None      # spent: no sixth attempt
    assert policy.next_delay_seconds(0) is None


def test_which_outcomes_retry():
    c = policy.classify_outcome
    assert c(200, None) == policy.OUTCOME_SUCCESS
    assert c(204, None) == policy.OUTCOME_SUCCESS
    for retried in (500, 502, 503, 408, 429):
        assert c(retried, None) == policy.OUTCOME_RETRY
    assert c(None, "timeout") == policy.OUTCOME_RETRY
    for final in (301, 400, 401, 404, 410):
        assert c(final, None) == policy.OUTCOME_FAIL
    assert c(None, None) == policy.OUTCOME_FAIL


def test_stored_error_is_truncated_and_nul_free():
    out = policy.truncate_error("x" * 1000 + "\x00")
    assert len(out) == policy.MAX_ERROR_LENGTH and "\x00" not in out


# ── auto-disable ─────────────────────────────────────────────────────────────

def test_failure_days_count_each_calendar_day_once():
    d1, d2, d3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
    days, last = policy.advance_failure_days(0, None, d1)
    assert (days, last) == (1, d1)
    assert policy.advance_failure_days(days, last, d1) == (1, d1)   # same day: no change
    days, last = policy.advance_failure_days(days, last, d2)
    assert not policy.should_disable(days)
    days, last = policy.advance_failure_days(days, last, d3)
    assert days == 3 and policy.should_disable(days)


# ── payload schema stability ─────────────────────────────────────────────────

_PO = ("po_log_id", "po_number", "warehouse", "warehouse_id", "sku_count",
       "total_units", "total_value")
PINNED = {
    "job.completed": ("job_id", "session_id"),
    "job.failed": ("job_id", "session_id", "error"),
    "purchase_order.approved": _PO + ("approved_amount", "decided_by"),
    "purchase_order.rejected": _PO + ("decided_by",),
    "purchase_order.sent": _PO + ("sent_at",),
    "purchase_order.cancelled": _PO + ("cancelled_at", "cancelled_by"),
    "stockout.imminent": ("sku", "warehouse", "warehouse_id", "signal", "current_stock",
                          "coverage_days", "reorder_point", "detected_on"),
    "commitment.at_risk": ("commitment_id", "sku", "warehouse", "warehouse_id", "customer",
                           "delivery_date", "quantity", "shortfall",
                           "latest_safe_order_date", "order_date_passed"),
    "commitment.fulfilled": ("commitment_id", "sku", "warehouse", "warehouse_id", "customer",
                             "delivery_date", "quantity", "fulfilled_at"),
    "webhook.test": ("webhook_id",),
}


def test_every_event_data_keys_are_pinned():
    # Changing a tuple here is a breaking change for receivers: bump API_VERSION.
    assert {n: e.data_keys for n, e in catalog.EVENT_TYPES.items()} == PINNED
    assert catalog.API_VERSION == "2026-10-05"


def test_envelope_shape_and_null_filling():
    when = datetime(2026, 10, 5, 14, 3, 11, tzinfo=timezone.utc)
    env = catalog.build_envelope("stockout.imminent", "t1", {"sku": "A"}, occurred_at=when)
    assert list(env) == ["id", "type", "api_version", "occurred_at", "tenant_id", "data"]
    assert env["id"].startswith("evt_") and len(env["id"]) == 4 + 32
    assert env["occurred_at"] == "2026-10-05T14:03:11Z"
    assert env["tenant_id"] == "t1" and env["type"] == "stockout.imminent"
    assert list(env["data"]) == list(PINNED["stockout.imminent"])
    assert env["data"]["sku"] == "A" and env["data"]["warehouse"] is None
    json.dumps(env)                                    # serialisable


def test_undeclared_data_key_is_refused_not_leaked():
    with pytest.raises(ValueError):
        catalog.build_envelope("job.completed", "t", {"job_id": "1", "email": "x@y.z"})


def test_no_event_carries_personal_fields():
    banned = {"email", "phone", "whatsapp", "comment", "note", "reason", "password"}
    for name, keys in PINNED.items():
        assert not banned & set(keys), name


# ── scope ────────────────────────────────────────────────────────────────────

def test_scope_allows_rules():
    allows = policy.scope_allows
    assert allows(None, "w1", True)                    # company-wide gets everything
    assert allows(None, None, True)
    assert allows(["w1", "w2"], "w1", True)
    assert not allows(["w1"], "w2", True)              # another warehouse
    assert not allows(["w1"], None, True)              # a company figure
    assert not allows([], "w1", True)                  # [] means none, never all
    assert allows(["w1"], None, False)                 # job events are not about a warehouse


def test_unreadable_scope_fails_closed():
    assert policy.parse_scope(None) is None
    assert policy.parse_scope('["w1"]') == ["w1"]
    assert policy.parse_scope("not json") == []
    assert policy.parse_scope({"w1": 1}) == []
    assert not policy.scope_allows(policy.parse_scope("not json"), "w1", True)


def test_select_recipients_filters_hooks_by_warehouse():
    hooks = [
        {"id": "company", "warehouse_scope": None},
        {"id": "north", "warehouse_scope": ["wN"]},
        {"id": "south", "warehouse_scope": '["wS"]'},
        {"id": "none", "warehouse_scope": []},
    ]
    ids = lambda ev, wh: sorted(h["id"] for h in select_recipients(hooks, ev, wh))
    assert ids("stockout.imminent", "wN") == ["company", "north"]
    assert ids("stockout.imminent", "wS") == ["company", "south"]
    assert ids("commitment.at_risk", None) == ["company"]
    assert ids("job.completed", None) == ["company", "none", "north", "south"]


def test_manageable_by_and_narrowing():
    assert policy.manageable_by(None, None) and policy.manageable_by(None, ["w1"])
    assert policy.manageable_by(["w1", "w2"], ["w1"])
    assert not policy.manageable_by(["w1"], None)          # company hook: not theirs
    assert not policy.manageable_by(["w1"], ["w1", "w9"])
    assert policy.narrow_scope(None, None) == (None, [])
    assert policy.narrow_scope(None, ["w1"]) == (["w1"], [])
    assert policy.narrow_scope(["w1"], None) == (["w1"], [])          # inherits
    assert policy.narrow_scope(["w1"], ["w1", "w2"]) == (["w1", "w2"], ["w2"])


# ── once per transition ──────────────────────────────────────────────────────

def test_newly_entered_emits_only_on_entry():
    assert policy.newly_entered([], ["a", "b"]) == ["a", "b"]
    assert policy.newly_entered(["a"], ["a", "b"]) == ["b"]            # a stays: not new
    assert policy.newly_entered(["a", "b"], ["a", "b"]) == []          # nothing changed
    assert policy.newly_entered(["a", "b"], ["a"]) == []               # leaving emits nothing
    # leave, then re-enter on a later pass: a new transition
    state = {"a"}
    state_after_exit = set()
    assert policy.newly_entered(state_after_exit, ["a"]) == ["a"]
    assert policy.newly_entered([], ["x", "x"]) == ["x"]               # no duplicates


# ── SSRF: refused before any connection, and never retried ───────────────────

@pytest.mark.parametrize("url", [
    "https://169.254.169.254/latest/meta-data",     # cloud metadata (link-local)
    "https://10.0.0.5/hook",                         # private network
    "https://127.0.0.1:8011/api",                    # loopback
    "https://[::1]/hook",
])
def test_send_refuses_internal_targets_without_connecting(monkeypatch, url):
    from backend.webhooks import service
    monkeypatch.setattr(service.network, "allow_private_hosts", lambda: False)

    def _no_network(*a, **k):
        raise AssertionError("a connection was attempted")
    monkeypatch.setattr(service.httpx, "Client", _no_network)
    attempt = service._send(url, b"{}", {})
    assert attempt.status_code is None and attempt.permanent is True
    assert attempt.error


def test_validate_target_rejects_credentials_and_non_https(monkeypatch):
    from backend.errors import AppError
    from backend.webhooks import service
    monkeypatch.setattr(service.network, "allow_private_hosts", lambda: False)
    for bad in ("http://example.com/x", "https://user:pw@example.com/x", "https:///x"):
        with pytest.raises(AppError) as exc:
            service.validate_target(bad)
        assert exc.value.code == "webhook_url_invalid"
    with pytest.raises(AppError) as exc:
        service.validate_target("https://169.254.169.254/")
    assert exc.value.code == "webhook_host_forbidden"
    with pytest.raises(AppError) as exc:
        service.validate_target("https://192.168.1.10/")
    assert exc.value.code == "webhook_host_not_allowed"


# ── run_transition: entry emits once, staying emits nothing ──────────────────

def _fake_store(monkeypatch, service, store):
    monkeypatch.setattr(service, "subscribers", lambda t, e: [{"id": "h", "warehouse_scope": None}])
    monkeypatch.setattr(service, "_load_state", lambda t, k: set(store))
    monkeypatch.setattr(service, "_save_state",
                        lambda t, k, cur: (store.clear(), store.update(cur)))


def test_run_transition_emits_once_per_entry(monkeypatch):
    from backend.webhooks import service
    store: set[str] = set()
    sent: list[str] = []
    _fake_store(monkeypatch, service, store)
    monkeypatch.setattr(service, "emit",
                        lambda t, e, data, **kw: sent.append(data["sku"]) or 1)

    def row(sku):
        return {"sku": sku}, "w1"

    service.run_transition("t", "stockout", "stockout.imminent", {"A|w1": row("A")})
    assert sent == ["A"]
    service.run_transition("t", "stockout", "stockout.imminent",
                           {"A|w1": row("A"), "B|w1": row("B")})
    assert sent == ["A", "B"]                          # A stayed: not repeated
    service.run_transition("t", "stockout", "stockout.imminent", {"B|w1": row("B")})
    assert sent == ["A", "B"] and store == {"B|w1"}    # A left: nothing emitted
    service.run_transition("t", "stockout", "stockout.imminent",
                           {"A|w1": row("A"), "B|w1": row("B")})
    assert sent == ["A", "B", "A"]                     # A re-entered: new transition


def test_run_transition_failure_keeps_state_so_the_event_is_not_lost(monkeypatch):
    from backend.webhooks import service
    store: set[str] = set()
    _fake_store(monkeypatch, service, store)

    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(service, "emit", _boom)
    with pytest.raises(RuntimeError):
        service.run_transition("t", "stockout", "stockout.imminent", {"A|w1": ({"sku": "A"}, "w1")})
    assert store == set()                              # nothing remembered: retried next pass


def test_run_transition_without_subscribers_emits_nothing_and_forgets(monkeypatch):
    from backend.webhooks import service
    saved = []
    monkeypatch.setattr(service, "subscribers", lambda t, e: [])
    monkeypatch.setattr(service, "_save_state", lambda t, k, cur: saved.append(set(cur)))
    assert service.run_transition("t", "k", "stockout.imminent", {"A|w1": ({}, "w1")}) == 0
    assert saved == [set()]
