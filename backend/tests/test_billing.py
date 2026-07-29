"""Billing, and specifically the one thing that must not be possible: a client
raising its own plan.

`tenants.plan` drives every entitlement in the app. If a browser request can
change it, the whole plan model is decoration — so the tests that matter here
are the negative ones: an unsigned webhook is refused, a replayed webhook is a
no-op, a non-admin cannot start a purchase, and no endpoint takes a plan name and
writes it.

Nothing here talks to Stripe. The webhook path is exercised by signing payloads
with the same secret the app is configured with, which is exactly what Stripe
does — so signature verification is under test rather than mocked away.
"""
import hashlib
import hmac
import json
import time
import uuid

import pytest

from backend.config import settings
from backend.db.connection import query_one

WEBHOOK_SECRET = "whsec_test_faro_e2e_secret"


@pytest.fixture(autouse=True)
def stripe_configured(monkeypatch):
    """A key so the endpoints are reachable, and a webhook secret so signatures
    can be verified. No network call is made by any test in this file."""
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy", raising=False)
    monkeypatch.setattr(settings, "stripe_webhook_secret", WEBHOOK_SECRET, raising=False)
    monkeypatch.setattr(settings, "stripe_price_professional_monthly",
                        "price_pro_monthly_test", raising=False)
    monkeypatch.setattr(settings, "stripe_price_professional_yearly", "", raising=False)


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    signed = f"{ts}.".encode() + payload
    mac = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _subscription_event(tenant_id: str, status="active",
                        price="price_pro_monthly_test", etype="customer.subscription.updated"):
    return {
        "id": f"evt_{uuid.uuid4().hex[:20]}",
        "type": etype,
        "data": {"object": {
            "id": f"sub_{uuid.uuid4().hex[:14]}",
            "status": status,
            "customer": f"cus_{uuid.uuid4().hex[:14]}",
            "metadata": {"tenant_id": tenant_id},
            "items": {"data": [{"price": {"id": price}}]},
        }},
    }


def _post_webhook(client, event, signature=None):
    body = json.dumps(event).encode()
    headers = {"stripe-signature": signature if signature is not None else _sign(body)}
    return client.post("/api/v1/billing/webhook", content=body, headers=headers)


def _plan(tenant_id):
    row = query_one("SELECT plan FROM tenants WHERE id = %s", (tenant_id,))
    return row["plan"] if row else None


def _tid(test_tenant):
    return test_tenant["id"] if isinstance(test_tenant, dict) else test_tenant


class TestNobodyCanRaiseTheirOwnPlan:
    def test_no_endpoint_accepts_a_plan_name_and_applies_it(
        self, client, auth_headers, test_tenant
    ):
        """The obvious attack: ask to be on Professional.

        What matters is the row afterwards, not the status code. Checkout does
        reach out to Stripe and will fail on the dummy key used here — the point
        is that it cannot have written the plan on its way there, whatever it
        returns or raises.
        """
        tid = _tid(test_tenant)
        before = _plan(tid)
        for path in ("/api/v1/billing/subscription", "/api/v1/billing/checkout",
                     "/api/v1/billing/portal"):
            try:
                client.post(path, headers=auth_headers, json={"plan": "professional"})
            except Exception:
                pass          # a Stripe call failing is not this test's subject
        assert _plan(tid) == before, "a request changed the tenant's plan"

    def test_unsigned_webhook_is_refused_and_changes_nothing(self, client, test_tenant):
        tid = _tid(test_tenant)
        before = _plan(tid)
        event = _subscription_event(tid)
        r = client.post("/api/v1/billing/webhook", content=json.dumps(event).encode())
        assert r.status_code == 400, r.text
        assert _plan(tid) == before

    def test_wrong_signature_is_refused_and_changes_nothing(self, client, test_tenant):
        tid = _tid(test_tenant)
        before = _plan(tid)
        event = _subscription_event(tid)
        body = json.dumps(event).encode()
        r = _post_webhook(client, event, signature=_sign(body, secret="whsec_attacker"))
        assert r.status_code == 400, r.text
        assert r.json()["error_code"] == "billing_signature_invalid"
        assert _plan(tid) == before

    def test_a_body_edited_after_signing_is_refused(self, client, test_tenant):
        """Signature covers the bytes, so tampering must invalidate it."""
        tid = _tid(test_tenant)
        before = _plan(tid)
        event = _subscription_event(tid)
        signature = _sign(json.dumps(event).encode())
        event["data"]["object"]["status"] = "active"
        event["data"]["object"]["items"]["data"][0]["price"]["id"] = "price_pro_monthly_test"
        tampered = json.dumps(event).encode() + b" "
        r = client.post("/api/v1/billing/webhook", content=tampered,
                        headers={"stripe-signature": signature})
        assert r.status_code == 400
        assert _plan(tid) == before


class TestSignedWebhookMovesThePlan:
    def test_active_subscription_puts_the_tenant_on_professional(
        self, client, test_tenant
    ):
        tid = _tid(test_tenant)
        r = _post_webhook(client, _subscription_event(tid, status="active"))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "applied"
        assert _plan(tid) == "professional"

    def test_cancellation_falls_back_to_starter_rather_than_locking_out(
        self, client, test_tenant
    ):
        tid = _tid(test_tenant)
        _post_webhook(client, _subscription_event(tid, status="active"))
        assert _plan(tid) == "professional"

        r = _post_webhook(client, _subscription_event(
            tid, status="canceled", etype="customer.subscription.deleted"))
        assert r.status_code == 200, r.text
        assert _plan(tid) == "starter", "a cancelled tenant should read its own data"

    def test_past_due_does_not_keep_the_paid_plan(self, client, test_tenant):
        tid = _tid(test_tenant)
        _post_webhook(client, _subscription_event(tid, status="active"))
        _post_webhook(client, _subscription_event(tid, status="past_due"))
        assert _plan(tid) == "starter"

    def test_a_price_this_deployment_does_not_sell_never_grants_a_plan(
        self, client, test_tenant
    ):
        """A signed event for an unknown price must not be mapped to anything."""
        tid = _tid(test_tenant)
        r = _post_webhook(client, _subscription_event(
            tid, status="active", price="price_someone_elses"))
        assert r.status_code == 200, r.text
        assert _plan(tid) == "starter"


class TestIdempotency:
    def test_the_same_event_twice_is_applied_once(self, client, test_tenant):
        tid = _tid(test_tenant)
        event = _subscription_event(tid, status="active")

        first = _post_webhook(client, event)
        second = _post_webhook(client, event)

        assert first.json()["data"]["status"] == "applied"
        assert second.json()["data"]["status"] == "duplicate", (
            "Stripe retries deliveries; the second must be a no-op")
        row = query_one("SELECT COUNT(*) AS n FROM stripe_events WHERE id = %s",
                        (event["id"],))
        assert row["n"] == 1

    def test_a_retry_returns_2xx_so_stripe_stops_retrying(self, client, test_tenant):
        event = _subscription_event(_tid(test_tenant), status="active")
        _post_webhook(client, event)
        assert _post_webhook(client, event).status_code == 200


class TestPurchaseIsAdminOnly:
    def test_viewer_cannot_start_a_checkout(self, client, viewer_headers):
        r = client.post("/api/v1/billing/checkout", headers=viewer_headers,
                        json={"plan": "professional"})
        assert r.status_code == 403, r.text

    def test_analyst_cannot_start_a_checkout(self, client, analyst_headers):
        """Committing the company to a recurring charge is not analyst work."""
        r = client.post("/api/v1/billing/checkout", headers=analyst_headers,
                        json={"plan": "professional"})
        assert r.status_code == 403, r.text

    def test_viewer_cannot_open_the_billing_portal(self, client, viewer_headers):
        assert client.post("/api/v1/billing/portal",
                           headers=viewer_headers).status_code == 403

    def test_a_plan_with_no_configured_price_is_not_for_sale(
        self, client, auth_headers
    ):
        """Enterprise is quoted per operation, so it has no price id."""
        r = client.post("/api/v1/billing/checkout", headers=auth_headers,
                        json={"plan": "enterprise"})
        assert r.status_code == 400, r.text
        assert r.json()["error_code"] == "billing_plan_not_for_sale"

    def test_yearly_is_refused_while_no_yearly_price_is_configured(
        self, client, auth_headers
    ):
        r = client.post("/api/v1/billing/checkout", headers=auth_headers,
                        json={"plan": "professional", "interval": "yearly"})
        assert r.status_code == 400, r.text


class TestReadingYourOwnBilling:
    def test_every_role_can_see_which_plan_the_company_is_on(
        self, client, viewer_headers
    ):
        r = client.get("/api/v1/billing/subscription", headers=viewer_headers)
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        assert "plan" in body and "purchasable" in body

    def test_unauthenticated_cannot_read_it(self, client):
        assert client.get("/api/v1/billing/subscription").status_code == 401


class TestWithoutConfiguration:
    def test_webhook_refuses_when_no_signing_secret_is_set(
        self, client, monkeypatch, test_tenant
    ):
        """A deployment that forgot the secret must not fall back to trusting
        the body — that would be the whole vulnerability."""
        monkeypatch.setattr(settings, "stripe_webhook_secret", "", raising=False)
        tid = _tid(test_tenant)
        before = _plan(tid)
        event = _subscription_event(tid)
        r = _post_webhook(client, event)
        assert r.status_code == 503, r.text
        assert _plan(tid) == before

    def test_checkout_reports_billing_is_off_when_no_key_is_set(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(settings, "stripe_secret_key", "", raising=False)
        monkeypatch.setattr(settings, "stripe_price_professional_monthly",
                            "price_pro_monthly_test", raising=False)
        r = client.post("/api/v1/billing/checkout", headers=auth_headers,
                        json={"plan": "professional"})
        assert r.status_code == 503, r.text
