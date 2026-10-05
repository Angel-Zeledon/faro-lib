"""Billing against a real database: who may pay, what a webhook may change,
and that nothing else can change a tier.

The provider APIs are never called: `stripe_api` / `paypal_api` are patched,
and webhooks are signed with the test secret exactly as Stripe signs them, so
the signature check under test is the real one.

Every test asserts the DATABASE, not the response: the tier in `tenants`, the
rows in `billing_subscriptions` / `billing_events`, the audit rows.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.billing import paypal_api, service as billing_svc, stripe_api
from backend.billing.signatures import stripe_signature
from backend.db.connection import execute, query, query_one

API = "/api/v1/billing"
SECRET = "whsec_pytest_billing_secret"
FUTURE = datetime.now(timezone.utc) + timedelta(days=25)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _clean_billing_overrides():
    from backend.service_config import store
    execute("DELETE FROM service_config WHERE service = 'billing'")
    store.invalidate()


@pytest.fixture(autouse=True)
def _billing_rows_are_removed(client):
    """`test_tenant` deletes the tenant row; the event log has no FK to it."""
    _clean_billing_overrides()
    yield
    execute("DELETE FROM billing_events WHERE tenant_id IS NULL "
            "OR tenant_id NOT IN (SELECT id FROM tenants)")
    _clean_billing_overrides()


@pytest.fixture
def billing_off(monkeypatch):
    for key in ("stripe_secret_key", "stripe_webhook_secret", "stripe_price_id_full",
                "paypal_client_id", "paypal_client_secret", "paypal_webhook_id",
                "paypal_plan_id_full"):
        monkeypatch.setattr(f"backend.config.settings.{key}", "")
    monkeypatch.setattr("backend.config.settings.paypal_mode", "sandbox")


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setattr("backend.config.settings.stripe_secret_key", "sk_test_pytest")
    monkeypatch.setattr("backend.config.settings.stripe_webhook_secret", SECRET)
    monkeypatch.setattr("backend.config.settings.stripe_price_id_full", "price_pytest")
    monkeypatch.setattr("backend.config.settings.paypal_client_id", "pp_client")
    monkeypatch.setattr("backend.config.settings.paypal_client_secret", "pp_secret")
    monkeypatch.setattr("backend.config.settings.paypal_webhook_id", "WH-PYTEST")
    monkeypatch.setattr("backend.config.settings.paypal_plan_id_full", "P-PYTEST")
    monkeypatch.setattr("backend.config.settings.paypal_mode", "sandbox")
    monkeypatch.setattr("backend.config.settings.billing_price_usd_full", 59.0)
    return True


class StripeFake:
    """What Stripe 'holds' for each subscription, and every call made to it."""

    def __init__(self, monkeypatch):
        self.subs: dict[str, dict] = {}
        self.calls: list[str] = []
        monkeypatch.setattr(stripe_api, "retrieve_subscription", self._retrieve)
        monkeypatch.setattr(stripe_api, "create_checkout_session", self._checkout)
        monkeypatch.setattr(stripe_api, "create_portal_session", self._portal)

    def _retrieve(self, cfg, sub_id):
        self.calls.append(f"retrieve:{sub_id}")
        return self.subs[sub_id]

    def _checkout(self, cfg, **kw):
        self.calls.append("checkout")
        self.last_checkout = kw
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    def _portal(self, cfg, **kw):
        self.calls.append("portal")
        return "https://billing.stripe.com/p/session/test_1"

    def set(self, sub_id, tenant_id, status="active", **extra):
        obj = {
            "id": sub_id, "status": status, "customer": f"cus_{tenant_id[-8:]}",
            "metadata": {"tenant_id": tenant_id},
            "current_period_end": int(FUTURE.timestamp()),
            "cancel_at_period_end": False,
        }
        obj.update(extra)
        self.subs[sub_id] = obj
        return obj


@pytest.fixture
def stripe_fake(monkeypatch):
    return StripeFake(monkeypatch)


def _send_stripe(client, *, event_id, etype, sub_id, created=None, secret=SECRET,
                 tamper=False):
    if etype.startswith("invoice."):
        obj = {"id": f"in_{event_id}", "object": "invoice", "subscription": sub_id}
    else:
        obj = {"id": sub_id, "object": "subscription"}
    body = json.dumps({
        "id": event_id, "type": etype,
        "created": created if created is not None else int(time.time()),
        "data": {"object": obj},
    }).encode()
    ts = int(time.time())
    header = f"t={ts},v1={stripe_signature(secret, ts, body)}"
    if tamper:
        body = body.replace(b'"type"', b'"tYpe"')
    return client.post(f"{API}/stripe/webhook", content=body,
                       headers={"Stripe-Signature": header,
                                "Content-Type": "application/json"})


def _tier(tenant_id):
    return query_one("SELECT tier, tier_source FROM tenants WHERE id = %s", (tenant_id,))


def _events(tenant_id=None):
    if tenant_id:
        return query("SELECT * FROM billing_events WHERE tenant_id = %s", (tenant_id,))
    return query("SELECT * FROM billing_events")


def _sid():
    return f"sub_{uuid4().hex[:12]}"


# ── Status when nothing is configured ────────────────────────────────────────

class TestUnconfigured:

    def test_status_says_payments_are_off_and_names_the_variables(
        self, client, auth_headers, billing_off,
    ):
        r = client.get(f"{API}/status", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["payments"]["enabled"] is False
        assert data["payments"]["providers"] == []
        assert "STRIPE_SECRET_KEY" in data["payments"]["missing"]["stripe"]
        assert "PAYPAL_CLIENT_ID" in data["payments"]["missing"]["paypal"]
        assert data["can_purchase"] is False
        assert data["purchase_block"] == "billing_not_configured"
        assert data["tier"] == "free"

    def test_a_viewer_learns_off_but_not_which_variable(self, client, viewer_headers, billing_off):
        r = client.get(f"{API}/status", headers=viewer_headers)
        assert r.status_code == 200
        assert r.json()["data"]["payments"]["missing"] is None

    def test_checkout_is_a_stated_refusal_not_a_500(
        self, client, auth_headers, registered_user, billing_off,
    ):
        tid = registered_user["tenant"]["id"]
        r = client.post(f"{API}/checkout", json={"provider": "stripe"}, headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "billing_provider_not_configured"
        assert "STRIPE_SECRET_KEY" in r.json()["error_params"]["missing"]
        assert billing_svc.subscriptions_for(tid) == []
        assert _tier(tid)["tier"] == "free"

    def test_a_webhook_with_no_provider_configured_changes_nothing(
        self, client, registered_user, billing_off,
    ):
        tid = registered_user["tenant"]["id"]
        r = _send_stripe(client, event_id="evt_off", etype="customer.subscription.updated",
                         sub_id="sub_off")
        assert r.status_code == 503
        assert r.json()["error_code"] == "billing_provider_not_configured"
        assert query("SELECT 1 FROM billing_events WHERE event_id = 'evt_off'") == []
        assert _tier(tid)["tier"] == "free"

    def test_the_public_offer_says_no_online_checkout(self, client, billing_off):
        r = client.get(f"{API}/offer")
        assert r.status_code == 200
        assert r.json()["data"]["online_checkout"] is False
        assert r.json()["data"]["price_usd_monthly"] is None


# ── Checkout permissions ─────────────────────────────────────────────────────

class TestCheckoutPermissions:

    def test_a_viewer_cannot_start_a_checkout(
        self, client, viewer_headers, viewer_user, billing_on, stripe_fake,
    ):
        tid = viewer_user["tenant"]["id"]
        r = client.post(f"{API}/checkout", json={"provider": "stripe"}, headers=viewer_headers)
        assert r.status_code == 403
        assert stripe_fake.calls == []
        assert billing_svc.subscriptions_for(tid) == []
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'audit.billing.checkout_started'", (tid,)) == []

    def test_an_analyst_cannot_start_a_checkout(
        self, client, analyst_headers, analyst_user, billing_on, stripe_fake,
    ):
        r = client.post(f"{API}/checkout", json={"provider": "stripe"}, headers=analyst_headers)
        assert r.status_code == 403
        assert stripe_fake.calls == []

    def test_an_admin_gets_a_hosted_url_and_the_tier_does_not_move(
        self, client, auth_headers, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        r = client.post(f"{API}/checkout", json={"provider": "stripe"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["url"].startswith("https://checkout.stripe.com/")
        assert stripe_fake.calls == ["checkout"]
        assert stripe_fake.last_checkout["tenant_id"] == tid
        assert stripe_fake.last_checkout["customer_email"] == registered_user["email"]
        assert "/facturacion?checkout=success" in stripe_fake.last_checkout["success_url"]
        # Paying is not paid: only the verified webhook moves the tier.
        assert _tier(tid)["tier"] == "free"
        audit = query("SELECT context FROM activity_logs WHERE tenant_id = %s "
                      "AND action = 'audit.billing.checkout_started'", (tid,))
        assert len(audit) == 1

    def test_paypal_checkout_records_a_pending_subscription_that_grants_nothing(
        self, client, auth_headers, registered_user, billing_on, monkeypatch,
    ):
        tid = registered_user["tenant"]["id"]
        monkeypatch.setattr(paypal_api, "create_subscription", lambda cfg, **kw: {
            "id": "I-PENDING1", "url": "https://www.sandbox.paypal.com/webapps/billing/subscriptions?ba_token=x"})
        r = client.post(f"{API}/checkout", json={"provider": "paypal"}, headers=auth_headers)
        assert r.status_code == 200, r.text
        rows = billing_svc.subscriptions_for(tid)
        assert [(x["provider"], x["status"]) for x in rows] == [("paypal", "approval_pending")]
        assert _tier(tid)["tier"] == "free"

    def test_a_trial_account_cannot_buy(
        self, client, auth_headers, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        execute("UPDATE tenants SET tier = 'demo' WHERE id = %s", (tid,))
        r = client.post(f"{API}/checkout", json={"provider": "stripe"}, headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "billing_trial_account"
        assert stripe_fake.calls == []
        assert _tier(tid)["tier"] == "demo"

    def test_corporate_is_never_bought_online(
        self, client, auth_headers, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        execute("UPDATE tenants SET tier = 'corporate' WHERE id = %s", (tid,))
        r = client.post(f"{API}/checkout", json={"provider": "stripe"}, headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "billing_corporate_plan"
        assert stripe_fake.calls == []

    def test_a_provider_outage_is_a_stated_502_and_records_nothing(
        self, client, auth_headers, registered_user, billing_on, monkeypatch,
    ):
        from backend.billing.providers import ProviderError

        def _down(cfg, **kw):
            raise ProviderError("stripe", "checkout", kind="unreachable")
        monkeypatch.setattr(stripe_api, "create_checkout_session", _down)
        r = client.post(f"{API}/checkout", json={"provider": "stripe"}, headers=auth_headers)
        assert r.status_code == 502
        assert r.json()["error_code"] == "billing_provider_unavailable"
        assert "sk_test_pytest" not in r.text

    def test_portal_viewer_denied_admin_gets_the_hosted_portal(
        self, client, auth_headers, viewer_headers, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        assert _send_stripe(client, event_id=f"evt_{uuid4().hex}", etype="customer.subscription.created",
                            sub_id=sid).status_code == 200
        r = client.post(f"{API}/portal", json={}, headers=viewer_headers)
        assert r.status_code == 403
        assert "portal" not in stripe_fake.calls
        r = client.post(f"{API}/portal", json={}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["url"].startswith("https://billing.stripe.com/")
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'audit.billing.portal_opened'", (tid,))


# ── Webhooks: the only door to the tier ──────────────────────────────────────

class TestStripeWebhooks:

    def test_a_verified_event_moves_free_to_paid_and_records_it(
        self, client, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        r = _send_stripe(client, event_id="evt_up_" + sid, etype="customer.subscription.created",
                         sub_id=sid)
        assert r.status_code == 200, r.text
        assert _tier(tid) == {"tier": "paid", "tier_source": "billing"}
        sub = query_one("SELECT * FROM billing_subscriptions WHERE provider_subscription_id = %s", (sid,))
        assert sub["tenant_id"] == tid and sub["status"] == "active"
        ev = _events(tid)
        assert [(e["event_id"], e["outcome"]) for e in ev] == [("evt_up_" + sid, "applied_tier_changed")]
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'billing.plan_activated'", (tid,))
        assert query_one("SELECT provider_customer_id FROM billing_customers WHERE tenant_id = %s",
                         (tid,))["provider_customer_id"] == f"cus_{tid[-8:]}"

    def test_a_replay_changes_nothing(self, client, registered_user, billing_on, stripe_fake):
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        eid = "evt_replay_" + sid
        assert _send_stripe(client, event_id=eid, etype="customer.subscription.created",
                            sub_id=sid).status_code == 200
        before = query_one("SELECT updated_at FROM billing_subscriptions "
                           "WHERE provider_subscription_id = %s", (sid,))
        # Stripe now says unpaid — but this is the SAME event delivered again.
        stripe_fake.set(sid, tid, status="unpaid")
        r = _send_stripe(client, event_id=eid, etype="customer.subscription.created", sub_id=sid)
        assert r.status_code == 200
        assert r.json()["outcome"] == "duplicate"
        assert _tier(tid)["tier"] == "paid"
        assert len(_events(tid)) == 1
        after = query_one("SELECT status, updated_at FROM billing_subscriptions "
                          "WHERE provider_subscription_id = %s", (sid,))
        assert after["status"] == "active" and after["updated_at"] == before["updated_at"]

    def test_an_out_of_order_event_is_recorded_and_ignored(
        self, client, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        now = int(time.time())
        stripe_fake.set(sid, tid, status="active")
        assert _send_stripe(client, event_id="evt_new_" + sid, etype="customer.subscription.updated",
                            sub_id=sid, created=now).status_code == 200
        assert _tier(tid)["tier"] == "paid"
        # An older event arrives late, and the object it is checked against
        # claims the subscription lapsed: it must not downgrade anybody.
        stripe_fake.set(sid, tid, status="unpaid")
        r = _send_stripe(client, event_id="evt_old_" + sid, etype="customer.subscription.updated",
                         sub_id=sid, created=now - 600)
        assert r.status_code == 200
        assert r.json()["outcome"] == "stale_ignored"
        assert _tier(tid)["tier"] == "paid"
        assert query_one("SELECT status FROM billing_subscriptions WHERE provider_subscription_id = %s",
                         (sid,))["status"] == "active"
        outcomes = {e["event_id"]: e["outcome"] for e in _events(tid)}
        assert outcomes["evt_old_" + sid] == "stale_ignored"

    @pytest.mark.parametrize("variant", ["wrong_secret", "tampered", "stale", "missing"])
    def test_a_forged_webhook_is_400_and_changes_nothing(
        self, client, registered_user, billing_on, stripe_fake, variant,
    ):
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        eid = f"evt_forged_{variant}_{sid}"
        if variant == "wrong_secret":
            r = _send_stripe(client, event_id=eid, etype="customer.subscription.created",
                             sub_id=sid, secret="whsec_forger")
        elif variant == "tampered":
            r = _send_stripe(client, event_id=eid, etype="customer.subscription.created",
                             sub_id=sid, tamper=True)
        elif variant == "stale":
            body = json.dumps({"id": eid, "type": "customer.subscription.created",
                               "created": int(time.time()),
                               "data": {"object": {"id": sid}}}).encode()
            old = int(time.time()) - 3600
            r = client.post(f"{API}/stripe/webhook", content=body, headers={
                "Stripe-Signature": f"t={old},v1={stripe_signature(SECRET, old, body)}"})
        else:
            r = client.post(f"{API}/stripe/webhook", content=b'{"id":"x","type":"y"}')
        assert r.status_code == 400
        assert r.json()["error_code"] == "billing_webhook_signature_invalid"
        assert _tier(tid) == {"tier": "free", "tier_source": "manual"}
        assert query("SELECT 1 FROM billing_events WHERE event_id = %s", (eid,)) == []
        assert billing_svc.subscriptions_for(tid) == []
        assert not any(c.startswith("retrieve") for c in stripe_fake.calls), \
            "an unverified event must not even be looked up"

    def test_a_manual_paid_tenant_is_never_downgraded_by_billing(
        self, client, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        execute("UPDATE tenants SET tier = 'paid', tier_source = 'manual' WHERE id = %s", (tid,))
        sid = _sid()
        stripe_fake.set(sid, tid, status="unpaid")
        assert _send_stripe(client, event_id="evt_m_" + sid, etype="customer.subscription.updated",
                            sub_id=sid).status_code == 200
        assert _tier(tid) == {"tier": "paid", "tier_source": "manual"}

    def test_corporate_is_untouched_by_any_event(
        self, client, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        execute("UPDATE tenants SET tier = 'corporate' WHERE id = %s", (tid,))
        sid = _sid()
        stripe_fake.set(sid, tid, status="canceled", ended_at=int(time.time()) - 10)
        assert _send_stripe(client, event_id="evt_c_" + sid, etype="customer.subscription.deleted",
                            sub_id=sid).status_code == 200
        assert _tier(tid)["tier"] == "corporate"

    def test_cancellation_keeps_paid_until_period_end_then_the_sweep_downgrades(
        self, client, registered_user, auth_headers, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        _send_stripe(client, event_id="evt_a_" + sid, etype="customer.subscription.created", sub_id=sid)
        # A forecast the tenant made while paying: it must survive the downgrade.
        created = client.post("/api/v1/sessions", json={"name": "kept"}, headers=auth_headers)
        assert created.status_code in (200, 201), created.text
        session_id = created.json()["data"]["id"]

        stripe_fake.set(sid, tid, cancel_at_period_end=True)
        _send_stripe(client, event_id="evt_b_" + sid, etype="customer.subscription.updated", sub_id=sid)
        assert _tier(tid)["tier"] == "paid"

        later = FUTURE + timedelta(hours=1)
        decision = billing_svc.reconcile_tenant(tid, now=later)
        assert decision.changed and decision.tier == "free"
        assert _tier(tid) == {"tier": "free", "tier_source": "billing"}
        # Permanent-data rule: nothing the tenant had is gone.
        assert query_one("SELECT id FROM sessions WHERE id = %s", (session_id,))
        assert billing_svc.subscriptions_for(tid)
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'billing.plan_downgraded'", (tid,))

    def test_past_due_keeps_paid_inside_the_grace_and_drops_after(
        self, client, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        _send_stripe(client, event_id="evt_p1_" + sid, etype="customer.subscription.created", sub_id=sid)
        stripe_fake.set(sid, tid, status="past_due")
        _send_stripe(client, event_id="evt_p2_" + sid, etype="invoice.payment_failed", sub_id=sid)
        row = query_one("SELECT past_due_since FROM billing_subscriptions "
                        "WHERE provider_subscription_id = %s", (sid,))
        assert row["past_due_since"] is not None
        assert _tier(tid)["tier"] == "paid"
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'billing.payment_failed'", (tid,))
        assert not billing_svc.reconcile_tenant(
            tid, now=row["past_due_since"] + timedelta(days=6)).changed
        assert billing_svc.reconcile_tenant(
            tid, now=row["past_due_since"] + timedelta(days=7, minutes=1)).tier == "free"

    def test_an_event_for_another_tenants_subscription_never_reaches_this_one(
        self, client, registered_user, billing_on, stripe_fake, make_tenant_user_headers,
    ):
        tid_a = registered_user["tenant"]["id"]
        headers_b, tid_b = make_tenant_user_headers(role="admin", return_tenant_id=True)
        sid = _sid()
        stripe_fake.set(sid, tid_a)
        _send_stripe(client, event_id="evt_iso1_" + sid, etype="customer.subscription.created", sub_id=sid)
        # Same subscription, metadata now (somehow) naming tenant B: the stored
        # owner wins, and B is untouched.
        stripe_fake.set(sid, tid_b)
        _send_stripe(client, event_id="evt_iso2_" + sid, etype="customer.subscription.updated", sub_id=sid)
        assert _tier(tid_b)["tier"] == "free"
        assert billing_svc.subscriptions_for(tid_b) == []
        status_b = client.get(f"{API}/status", headers=headers_b).json()["data"]
        assert status_b["subscription"] is None
        r = client.post(f"{API}/portal", json={}, headers=headers_b)
        assert r.status_code == 404
        assert r.json()["error_code"] == "billing_no_subscription"

    def test_an_event_naming_no_known_tenant_is_recorded_and_changes_nothing(
        self, client, billing_on, stripe_fake,
    ):
        sid = _sid()
        stripe_fake.set(sid, "tenant_that_does_not_exist")
        r = _send_stripe(client, event_id="evt_ghost_" + sid,
                         etype="customer.subscription.created", sub_id=sid)
        assert r.status_code == 200
        row = query_one("SELECT outcome, tenant_id FROM billing_events WHERE event_id = %s",
                        ("evt_ghost_" + sid,))
        assert row["outcome"] == "ignored_unknown_tenant" and row["tenant_id"] is None


# ── Erasure, export and the API-key wall ─────────────────────────────────────

class TestTenantDataAndSurface:

    def test_erase_removes_every_billing_row(self, client, billing_on, stripe_fake):
        from backend.tenants import data_export
        from backend.tenants.service import create_tenant
        tid = create_tenant(f"pytest-{uuid4().hex[:10]}")["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        _send_stripe(client, event_id="evt_er_" + sid, etype="customer.subscription.created", sub_id=sid)
        assert billing_svc.subscriptions_for(tid) and _events(tid)
        data_export.delete_tenant(tid)
        for table in ("billing_customers", "billing_subscriptions", "billing_events"):
            assert query(f"SELECT 1 FROM {table} WHERE tenant_id = %s", (tid,)) == [], table

    def test_erasing_an_account_that_still_pays_is_refused(
        self, client, auth_headers, registered_user, billing_on, stripe_fake,
    ):
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        _send_stripe(client, event_id="evt_live_" + sid, etype="customer.subscription.created", sub_id=sid)
        r = client.request("DELETE", "/api/v1/tenant", json={"confirm": "DELETE"}, headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "tenant_has_active_subscription"
        assert query_one("SELECT id FROM tenants WHERE id = %s", (tid,))

    def test_the_export_carries_billing_rows(self, client, registered_user, billing_on, stripe_fake):
        import io
        import zipfile
        from backend.tenants import data_export
        tid = registered_user["tenant"]["id"]
        sid = _sid()
        stripe_fake.set(sid, tid)
        _send_stripe(client, event_id="evt_ex_" + sid, etype="customer.subscription.created", sub_id=sid)
        zf = zipfile.ZipFile(io.BytesIO(data_export.build_export_zip(tid)))
        subs = json.loads(zf.read("billing_subscriptions.json"))
        assert [s["provider_subscription_id"] for s in subs] == [sid]
        assert "sk_test_pytest" not in zf.read("billing_events.json").decode()

    def test_no_billing_route_is_reachable_with_an_api_key(self, app):
        from backend.api.public_surface import exposure
        billing_routes = [r for r in app.routes if getattr(r, "path", "").startswith("/api/v1/billing")]
        assert len(billing_routes) >= 6
        assert not any(exposure(r).exposed for r in billing_routes)
