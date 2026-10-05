"""Billing's pure parts: signatures, the state -> tier rule, normalization,
and the webhook handlers' refusal / idempotency paths with the database
stubbed out.

No database, no network: runnable with `--noconftest`. The behaviour against a
real database (replays, ordering, permissions, erasure) is in
`test_billing_api.py`.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.billing import entitlement as ent
from backend.billing import paypal_api, providers, stripe_api, webhooks
from backend.billing.entitlement import SubscriptionState as S
from backend.billing.signatures import stripe_signature, verify_stripe_signature

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
SECRET = "whsec_test_secret_value"


# ── Stripe-Signature ─────────────────────────────────────────────────────────

def _header(secret: str, body: bytes, ts: int) -> str:
    return f"t={ts},v1={stripe_signature(secret, ts, body)}"


class TestStripeSignature:
    BODY = b'{"id":"evt_1","type":"invoice.paid"}'

    def test_a_genuine_signature_verifies(self):
        ts = int(time.time())
        assert verify_stripe_signature(SECRET, _header(SECRET, self.BODY, ts), self.BODY)

    def test_the_wrong_secret_is_refused(self):
        ts = int(time.time())
        header = _header("whsec_someone_else", self.BODY, ts)
        assert not verify_stripe_signature(SECRET, header, self.BODY)

    def test_a_stale_timestamp_is_refused_even_when_correctly_signed(self):
        ts = int(time.time()) - 301
        assert not verify_stripe_signature(SECRET, _header(SECRET, self.BODY, ts), self.BODY)

    def test_a_timestamp_from_the_future_is_refused(self):
        ts = int(time.time()) + 301
        assert not verify_stripe_signature(SECRET, _header(SECRET, self.BODY, ts), self.BODY)

    def test_inside_the_tolerance_is_accepted(self):
        ts = int(time.time()) - 299
        assert verify_stripe_signature(SECRET, _header(SECRET, self.BODY, ts), self.BODY)

    def test_a_tampered_body_is_refused(self):
        ts = int(time.time())
        header = _header(SECRET, self.BODY, ts)
        tampered = self.BODY.replace(b"invoice.paid", b"invoice.void")
        assert not verify_stripe_signature(SECRET, header, tampered)

    def test_the_timestamp_is_part_of_what_is_signed(self):
        """Moving `t` forward to beat the tolerance breaks the signature."""
        old = int(time.time()) - 3600
        sig = stripe_signature(SECRET, old, self.BODY)
        forged = f"t={int(time.time())},v1={sig}"
        assert not verify_stripe_signature(SECRET, forged, self.BODY)

    def test_any_v1_matching_is_enough_while_a_secret_rolls(self):
        ts = int(time.time())
        good = stripe_signature(SECRET, ts, self.BODY)
        header = f"t={ts},v1={'0' * 64},v1={good},v0=ignored"
        assert verify_stripe_signature(SECRET, header, self.BODY)

    @pytest.mark.parametrize("header", [
        None, "", "garbage", "t=abc,v1=00", "v1=deadbeef", "t=123",
        "t=,v1=", ",,,", "t=1=2,v1=x",
    ])
    def test_malformed_headers_are_refused_without_raising(self, header):
        assert not verify_stripe_signature(SECRET, header, self.BODY)

    def test_an_empty_secret_never_verifies(self):
        ts = int(time.time())
        assert not verify_stripe_signature("", _header("", self.BODY, ts), self.BODY)

    def test_v0_alone_is_not_accepted(self):
        ts = int(time.time())
        header = f"t={ts},v0={stripe_signature(SECRET, ts, self.BODY)}"
        assert not verify_stripe_signature(SECRET, header, self.BODY)


# ── The state -> tier rule ───────────────────────────────────────────────────

FUTURE = NOW + timedelta(days=20)
PAST = NOW - timedelta(days=1)


class TestDecideTier:

    def test_an_active_subscription_moves_free_to_paid_and_marks_it_billing(self):
        d = ent.decide_tier("free", "manual", [S("active", FUTURE)], NOW)
        assert (d.tier, d.tier_source, d.changed) == ("paid", "billing", True)
        assert d.reason == "subscription_active"

    def test_trialing_grants_like_active(self):
        d = ent.decide_tier("free", "manual", [S("trialing", FUTURE)], NOW)
        assert d.tier == "paid" and d.changed

    def test_billing_paid_with_active_is_unchanged(self):
        d = ent.decide_tier("paid", "billing", [S("active", FUTURE)], NOW)
        assert (d.tier, d.changed) == ("paid", False)

    def test_past_due_inside_the_grace_keeps_paid(self):
        sub = S("past_due", FUTURE, past_due_since=NOW - timedelta(days=6, hours=23))
        d = ent.decide_tier("paid", "billing", [sub], NOW)
        assert (d.tier, d.changed, d.reason) == ("paid", False, "subscription_in_grace")

    def test_past_due_past_the_grace_drops_to_free(self):
        sub = S("past_due", FUTURE, past_due_since=NOW - timedelta(days=7, seconds=1))
        d = ent.decide_tier("paid", "billing", [sub], NOW)
        assert (d.tier, d.changed, d.reason) == ("free", True, "subscription_lapsed")

    def test_grace_is_exactly_seven_days(self):
        since = NOW - timedelta(days=7)
        sub = S("past_due", past_due_since=since)
        assert ent.grace_until(sub, NOW) == NOW
        assert not ent.grants_access(sub, NOW)
        assert ent.grants_access(sub, NOW - timedelta(seconds=1))

    def test_past_due_with_no_recorded_start_is_graced_from_now(self):
        sub = S("past_due")
        assert ent.grants_access(sub, NOW)
        assert ent.grace_until(sub, NOW) == NOW + timedelta(days=ent.GRACE_DAYS)

    def test_cancel_at_period_end_stays_paid_until_the_period_ends(self):
        sub = S("active", FUTURE, cancel_at_period_end=True)
        d = ent.decide_tier("paid", "billing", [sub], NOW)
        assert (d.tier, d.changed, d.reason) == ("paid", False, "paid_until_period_end")

    def test_cancel_at_period_end_after_the_period_drops_to_free(self):
        sub = S("active", PAST, cancel_at_period_end=True)
        d = ent.decide_tier("paid", "billing", [sub], NOW)
        assert (d.tier, d.changed) == ("free", True)

    def test_canceled_keeps_paid_until_ended_at(self):
        sub = S("canceled", FUTURE, ended_at=FUTURE)
        assert ent.decide_tier("paid", "billing", [sub], NOW).tier == "paid"

    def test_canceled_immediately_ends_at_ended_at_not_at_period_end(self):
        sub = S("canceled", FUTURE, ended_at=PAST)
        d = ent.decide_tier("paid", "billing", [sub], NOW)
        assert (d.tier, d.changed) == ("free", True)

    def test_canceled_without_ended_at_uses_the_paid_period(self):
        assert ent.decide_tier("paid", "billing", [S("canceled", FUTURE)], NOW).tier == "paid"
        assert ent.decide_tier("paid", "billing", [S("canceled", PAST)], NOW).tier == "free"

    def test_canceled_with_no_dates_at_all_grants_nothing(self):
        assert ent.decide_tier("paid", "billing", [S("canceled")], NOW).tier == "free"

    @pytest.mark.parametrize("status", [
        "unpaid", "expired", "refunded", "incomplete_expired", "paused",
    ])
    def test_lapsed_statuses_drop_billing_paid_to_free(self, status):
        d = ent.decide_tier("paid", "billing", [S(status, FUTURE)], NOW)
        assert (d.tier, d.changed, d.tier_source) == ("free", True, "billing")

    @pytest.mark.parametrize("status", ["mystery", "", "ACTIVE"])
    def test_an_unknown_status_never_grants(self, status):
        """Fail closed: a status nobody mapped must not hand out paid."""
        d = ent.decide_tier("free", "manual", [S(status, FUTURE)], NOW)
        assert (d.tier, d.changed) == ("free", False)

    @pytest.mark.parametrize("status", ["incomplete", "approval_pending"])
    def test_a_pending_checkout_neither_grants_nor_revokes(self, status):
        assert not ent.decide_tier("free", "manual", [S(status, FUTURE)], NOW).changed
        d = ent.decide_tier("paid", "billing", [S(status, FUTURE)], NOW)
        assert (d.tier, d.changed) == ("paid", False)

    def test_an_abandoned_second_checkout_does_not_revoke_a_live_one(self):
        subs = [S("active", FUTURE), S("incomplete_expired")]
        assert ent.decide_tier("paid", "billing", subs, NOW).tier == "paid"

    def test_any_one_granting_subscription_is_enough(self):
        subs = [S("canceled", PAST, ended_at=PAST), S("active", FUTURE)]
        d = ent.decide_tier("free", "billing", subs, NOW)
        assert (d.tier, d.changed) == ("paid", True)

    @pytest.mark.parametrize("tier", ["corporate", "demo"])
    @pytest.mark.parametrize("subs", [[S("active", FUTURE)], [S("unpaid")], []])
    def test_corporate_and_demo_are_never_touched(self, tier, subs):
        for source in ("manual", "billing"):
            d = ent.decide_tier(tier, source, subs, NOW)
            assert (d.tier, d.changed, d.reason) == (tier, False, "tier_not_managed")

    def test_a_manual_paid_tenant_is_grandfathered(self):
        """Set to paid by the operator, no subscription: a lapsed (or any)
        subscription state must not undo the handshake deal."""
        for subs in ([], [S("unpaid")], [S("canceled", PAST)]):
            d = ent.decide_tier("paid", "manual", subs, NOW)
            assert (d.tier, d.changed, d.reason) == ("paid", False, "manual_grant")

    def test_a_missing_tier_source_reads_as_manual(self):
        d = ent.decide_tier("paid", None, [S("unpaid")], NOW)
        assert (d.tier, d.changed) == ("paid", False)

    def test_a_drifted_tier_is_left_alone(self):
        d = ent.decide_tier("enterprise", "billing", [S("active", FUTURE)], NOW)
        assert (d.tier, d.changed) == ("enterprise", False)

    def test_no_subscription_changes_nothing(self):
        assert not ent.decide_tier("free", "manual", [], NOW).changed
        assert ent.decide_tier("paid", "billing", [], NOW).tier == "paid"

    def test_lapsing_while_already_free_is_no_change(self):
        d = ent.decide_tier("free", "billing", [S("unpaid")], NOW)
        assert (d.tier, d.changed) == ("free", False)

    def test_naive_datetimes_are_read_as_utc(self):
        naive_future = (NOW + timedelta(days=2)).replace(tzinfo=None)
        sub = S("active", naive_future, cancel_at_period_end=True)
        assert ent.grants_access(sub, NOW)

    def test_access_until(self):
        assert ent.access_until(S("active", FUTURE), NOW) is None
        assert ent.access_until(S("active", FUTURE, cancel_at_period_end=True), NOW) == FUTURE
        assert ent.access_until(S("canceled", FUTURE, ended_at=PAST), NOW) == PAST
        assert ent.access_until(S("unpaid", FUTURE), NOW) is None


class TestPurchaseBlock:

    def test_nothing_configured_blocks_everyone(self):
        assert ent.purchase_block("free", "manual", False, False) == "billing_not_configured"

    def test_a_free_tenant_may_buy(self):
        assert ent.purchase_block("free", "manual", False, True) is None
        assert ent.purchase_block("free", "billing", False, True) is None

    def test_a_trial_account_must_register_first(self):
        assert ent.purchase_block("demo", "manual", False, True) == "billing_trial_account"

    def test_corporate_is_never_bought_online(self):
        assert ent.purchase_block("corporate", "manual", False, True) == "billing_corporate_plan"

    def test_a_manually_paid_tenant_has_nothing_to_buy(self):
        assert ent.purchase_block("paid", "manual", False, True) == "billing_plan_managed_manually"

    def test_a_paying_tenant_manages_instead_of_buying_twice(self):
        assert ent.purchase_block("paid", "billing", True, True) == "billing_subscription_active"

    def test_a_billing_paid_tenant_whose_access_ended_may_buy_again(self):
        assert ent.purchase_block("paid", "billing", False, True) is None


# ── Provider config ──────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, **kw):
        base = dict(
            stripe_secret_key="", stripe_webhook_secret="", stripe_price_id_full="",
            paypal_client_id="", paypal_client_secret="", paypal_webhook_id="",
            paypal_plan_id_full="", paypal_mode="sandbox", billing_price_usd_full=59.0,
        )
        base.update(kw)
        self.__dict__.update(base)


STRIPE_ON = dict(stripe_secret_key="sk_test_x", stripe_webhook_secret=SECRET,
                 stripe_price_id_full="price_x")
PAYPAL_ON = dict(paypal_client_id="cid", paypal_client_secret="csecret",
                 paypal_webhook_id="WH-1", paypal_plan_id_full="P-1")


class TestProviderConfig:

    def test_nothing_set_means_both_off_and_named(self):
        cfg = providers.load(_Cfg())
        assert cfg.enabled_providers() == []
        assert set(cfg.missing("stripe")) == {
            "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_ID_FULL"}
        assert "PAYPAL_CLIENT_SECRET" in cfg.missing("paypal")

    def test_a_half_configured_provider_is_off(self):
        cfg = providers.load(_Cfg(stripe_secret_key="sk_test_x", stripe_price_id_full="price_x"))
        assert cfg.enabled_providers() == []
        assert cfg.missing("stripe") == ["STRIPE_WEBHOOK_SECRET"]

    def test_each_provider_alone_is_enough(self):
        assert providers.load(_Cfg(**STRIPE_ON)).enabled_providers() == ["stripe"]
        assert providers.load(_Cfg(**PAYPAL_ON)).enabled_providers() == ["paypal"]
        both = providers.load(_Cfg(**STRIPE_ON, **PAYPAL_ON))
        assert both.enabled_providers() == ["stripe", "paypal"]

    def test_an_invalid_paypal_mode_turns_paypal_off(self):
        cfg = providers.load(_Cfg(**PAYPAL_ON, paypal_mode="production"))
        assert "PAYPAL_MODE" in cfg.missing("paypal")
        assert not cfg.is_configured("paypal")

    def test_live_and_sandbox_pick_different_hosts(self):
        live = providers.load(_Cfg(**PAYPAL_ON, paypal_mode="live"))
        sandbox = providers.load(_Cfg(**PAYPAL_ON))
        assert live.paypal_api_base == "https://api-m.paypal.com"
        assert sandbox.paypal_api_base == "https://api-m.sandbox.paypal.com"

    def test_a_non_positive_price_turns_payments_off(self):
        cfg = providers.load(_Cfg(**STRIPE_ON, billing_price_usd_full=0))
        assert cfg.enabled_providers() == []
        assert "BILLING_PRICE_USD_FULL" in cfg.missing("stripe")

    def test_missing_names_never_carry_a_value(self):
        cfg = providers.load(_Cfg(stripe_secret_key="sk_test_SENSITIVE"))
        assert all("SENSITIVE" not in name for name in cfg.missing("stripe"))


# ── Normalization ────────────────────────────────────────────────────────────

class TestNormalize:

    def test_stripe_period_end_from_items_in_new_api_versions(self):
        end = int(FUTURE.timestamp())
        n = stripe_api.normalize_subscription({
            "id": "sub_1", "status": "active", "customer": "cus_1",
            "metadata": {"tenant_id": "t1"},
            "items": {"data": [{"current_period_end": end, "price": {"id": "price_x"}}]},
        })
        assert n["current_period_end"] == datetime.fromtimestamp(end, tz=timezone.utc)
        assert (n["tenant_id"], n["plan"], n["provider_customer_id"]) == ("t1", "price_x", "cus_1")

    def test_stripe_cancel_at_inside_the_period_ends_access_then(self):
        end = int(FUTURE.timestamp())
        cancel = int((NOW + timedelta(days=3)).timestamp())
        n = stripe_api.normalize_subscription({
            "id": "sub_1", "status": "active", "current_period_end": end, "cancel_at": cancel,
        })
        assert n["cancel_at_period_end"] is True
        assert n["current_period_end"] == datetime.fromtimestamp(cancel, tz=timezone.utc)

    def test_stripe_cancel_at_beyond_the_period_keeps_renewing(self):
        end = int(FUTURE.timestamp())
        n = stripe_api.normalize_subscription({
            "id": "sub_1", "status": "active", "current_period_end": end,
            "cancel_at": end + 90 * 86400,
        })
        assert n["cancel_at_period_end"] is False

    @pytest.mark.parametrize("raw,failed,expected", [
        ("ACTIVE", 0, "active"),
        ("ACTIVE", 1, "past_due"),
        ("SUSPENDED", 0, "past_due"),
        ("CANCELLED", 0, "canceled"),
        ("EXPIRED", 0, "expired"),
        ("APPROVAL_PENDING", 0, "approval_pending"),
        ("APPROVED", 0, "approval_pending"),
        ("WHATEVER", 0, "whatever"),
    ])
    def test_paypal_status_mapping(self, raw, failed, expected):
        n = paypal_api.normalize_subscription({
            "id": "I-1", "status": raw, "custom_id": "t1", "plan_id": "P-1",
            "billing_info": {"failed_payments_count": failed,
                             "next_billing_time": "2026-11-05T10:00:00Z"},
            "subscriber": {"payer_id": "PAYER"},
        })
        assert n["status"] == expected
        assert n["tenant_id"] == "t1"
        assert n["current_period_end"] == datetime(2026, 11, 5, 10, tzinfo=timezone.utc)

    def test_paypal_webhook_headers_must_all_be_present(self):
        full = {name: "x" for name in paypal_api.WEBHOOK_HEADERS.values()}
        assert paypal_api.webhook_headers(full) is not None
        partial = dict(full)
        partial.pop("paypal-transmission-sig")
        assert paypal_api.webhook_headers(partial) is None


# ── Webhook handlers, database stubbed ───────────────────────────────────────

class _Ledger:
    """Stands in for service: remembers which event ids were applied."""

    def __init__(self):
        self.applied: list[str] = []
        self.unattributed: list[tuple[str, str]] = []
        self.tenants = {}

    def install(self, monkeypatch):
        svc = webhooks.service
        monkeypatch.setattr(svc, "event_seen", lambda p, e: e in self.applied
                            or any(e == u[0] for u in self.unattributed))
        monkeypatch.setattr(svc, "tenant_for_subscription", lambda p, s: self.tenants.get(s))
        monkeypatch.setattr(svc, "tenant_for_customer", lambda p, c: None)

        def _apply(provider, event_id, event_type, created, sub, tenant_id, **_):
            self.applied.append(event_id)
            return svc.Outcome("applied", tenant_id)

        def _unattr(provider, event_id, event_type, created, outcome):
            self.unattributed.append((event_id, outcome))
            return outcome

        monkeypatch.setattr(svc, "apply_event", _apply)
        monkeypatch.setattr(svc, "record_unattributed", _unattr)


def _stripe_event(event_id="evt_1", etype="customer.subscription.updated", sub="sub_1"):
    return json.dumps({
        "id": event_id, "type": etype, "created": int(time.time()),
        "data": {"object": {"id": sub, "customer": "cus_1",
                            "metadata": {"tenant_id": "tenant_1"}}},
    }).encode()


class TestStripeWebhookHandler:

    @pytest.fixture
    def ledger(self, monkeypatch):
        led = _Ledger()
        led.install(monkeypatch)
        monkeypatch.setattr(stripe_api, "retrieve_subscription", lambda cfg, sid: {
            "id": sid, "status": "active", "customer": "cus_1",
            "metadata": {"tenant_id": "tenant_1"},
            "current_period_end": int(FUTURE.timestamp()),
        })
        return led

    @pytest.fixture
    def cfg(self):
        return providers.load(_Cfg(**STRIPE_ON))

    def _sign(self, body):
        return _header(SECRET, body, int(time.time()))

    def test_a_forged_event_touches_nothing(self, ledger, cfg):
        body = _stripe_event()
        with pytest.raises(webhooks.InvalidWebhook):
            webhooks.handle_stripe(cfg, body, _header("whsec_forger", body, int(time.time())))
        assert ledger.applied == [] and ledger.unattributed == []

    def test_an_unsigned_event_touches_nothing(self, ledger, cfg):
        with pytest.raises(webhooks.InvalidWebhook):
            webhooks.handle_stripe(cfg, _stripe_event(), None)
        assert ledger.applied == []

    def test_a_genuine_event_is_applied_once_and_its_replay_is_a_no_op(self, ledger, cfg):
        body = _stripe_event()
        assert webhooks.handle_stripe(cfg, body, self._sign(body)) == "applied"
        assert webhooks.handle_stripe(cfg, body, self._sign(body)) == "duplicate"
        assert ledger.applied == ["evt_1"]

    def test_an_unhandled_type_is_recorded_not_applied(self, ledger, cfg):
        body = _stripe_event(etype="customer.created")
        assert webhooks.handle_stripe(cfg, body, self._sign(body)) == "ignored_unhandled_type"
        assert ledger.applied == []

    def test_an_unreachable_provider_asks_for_a_retry_and_records_nothing(
        self, ledger, cfg, monkeypatch,
    ):
        def _down(cfg, sid):
            raise providers.ProviderError("stripe", "subscription", kind="unreachable")
        monkeypatch.setattr(stripe_api, "retrieve_subscription", _down)
        body = _stripe_event()
        with pytest.raises(webhooks.RetryLater):
            webhooks.handle_stripe(cfg, body, self._sign(body))
        assert ledger.applied == [] and ledger.unattributed == []

    def test_not_configured_refuses_before_reading(self, ledger):
        cfg = providers.load(_Cfg())
        with pytest.raises(webhooks.NotConfigured):
            webhooks.handle_stripe(cfg, _stripe_event(), "t=1,v1=x")

    def test_a_signed_body_that_is_not_json_is_malformed(self, ledger, cfg):
        body = b"not json"
        with pytest.raises(webhooks.InvalidWebhook) as exc:
            webhooks.handle_stripe(cfg, body, self._sign(body))
        assert exc.value.code == "billing_webhook_malformed"

    def test_the_stored_mapping_beats_the_event_metadata(self, ledger, cfg, monkeypatch):
        ledger.tenants["sub_1"] = "tenant_owner"
        seen = {}

        def _apply(provider, event_id, event_type, created, sub, tenant_id, **_):
            seen["tenant"] = tenant_id
            return webhooks.service.Outcome("applied", tenant_id)
        monkeypatch.setattr(webhooks.service, "apply_event", _apply)
        body = _stripe_event()
        webhooks.handle_stripe(cfg, body, self._sign(body))
        assert seen["tenant"] == "tenant_owner"

    def test_invoice_events_find_the_subscription_under_parent(self, ledger, cfg):
        body = json.dumps({
            "id": "evt_inv", "type": "invoice.payment_failed", "created": int(time.time()),
            "data": {"object": {"customer": "cus_1", "parent": {"subscription_details": {
                "subscription": "sub_9", "metadata": {"tenant_id": "tenant_1"}}}}},
        }).encode()
        assert webhooks.handle_stripe(cfg, body, self._sign(body)) == "applied"


class TestPaypalWebhookHandler:

    HEADERS = {name: "v" for name in paypal_api.WEBHOOK_HEADERS.values()}

    @pytest.fixture
    def cfg(self):
        return providers.load(_Cfg(**PAYPAL_ON))

    @pytest.fixture
    def ledger(self, monkeypatch):
        led = _Ledger()
        led.install(monkeypatch)
        monkeypatch.setattr(paypal_api, "access_token", lambda cfg: "tok")
        monkeypatch.setattr(paypal_api, "get_subscription", lambda cfg, sid, token=None: {
            "id": sid, "status": "ACTIVE", "custom_id": "tenant_1",
            "billing_info": {"next_billing_time": "2026-11-05T10:00:00Z"},
        })
        return led

    def _event(self, event_id="WH-EVT-1", etype="BILLING.SUBSCRIPTION.ACTIVATED"):
        return json.dumps({
            "id": event_id, "event_type": etype, "create_time": "2026-10-05T12:00:00Z",
            "resource": {"id": "I-SUB", "custom_id": "tenant_1"},
        }).encode()

    def test_paypal_saying_no_refuses_and_touches_nothing(self, ledger, cfg, monkeypatch):
        monkeypatch.setattr(paypal_api, "verify_webhook", lambda *a, **k: False)
        with pytest.raises(webhooks.InvalidWebhook):
            webhooks.handle_paypal(cfg, self._event(), self.HEADERS)
        assert ledger.applied == [] and ledger.unattributed == []

    def test_missing_transmission_headers_are_refused_without_asking_paypal(
        self, ledger, cfg, monkeypatch,
    ):
        asked = []
        monkeypatch.setattr(paypal_api, "verify_webhook", lambda *a, **k: asked.append(1) or True)
        with pytest.raises(webhooks.InvalidWebhook):
            webhooks.handle_paypal(cfg, self._event(), {})
        assert asked == [] and ledger.applied == []

    def test_verification_that_could_not_run_is_not_a_pass(self, ledger, cfg, monkeypatch):
        def _down(*a, **k):
            raise providers.ProviderError("paypal", "verify", kind="unreachable")
        monkeypatch.setattr(paypal_api, "verify_webhook", _down)
        with pytest.raises(webhooks.RetryLater):
            webhooks.handle_paypal(cfg, self._event(), self.HEADERS)
        assert ledger.applied == []

    def test_verified_event_applies_once(self, ledger, cfg, monkeypatch):
        monkeypatch.setattr(paypal_api, "verify_webhook", lambda *a, **k: True)
        assert webhooks.handle_paypal(cfg, self._event(), self.HEADERS) == "applied"
        assert webhooks.handle_paypal(cfg, self._event(), self.HEADERS) == "duplicate"
        assert ledger.applied == ["WH-EVT-1"]

    def test_a_sale_finds_its_subscription_by_billing_agreement(self, ledger, cfg, monkeypatch):
        monkeypatch.setattr(paypal_api, "verify_webhook", lambda *a, **k: True)
        body = json.dumps({
            "id": "WH-SALE", "event_type": "PAYMENT.SALE.COMPLETED",
            "create_time": "2026-10-05T12:00:00Z",
            "resource": {"id": "SALE-1", "billing_agreement_id": "I-SUB"},
        }).encode()
        assert webhooks.handle_paypal(cfg, body, self.HEADERS) == "applied"
