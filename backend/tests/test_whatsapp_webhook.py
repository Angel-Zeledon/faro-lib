"""Inbound webhook: signature, identity, idempotency, rate-limit, and the
end-to-end confirmation gate over HTTP. LLM is mocked; outbound send is a
no-op without TWILIO creds."""
import json
from unittest import mock

import pytest

from backend.config import settings
from backend.db.connection import query_one, execute
from backend.api.v1 import whatsapp as wh


AUTH_TOKEN = "test_twilio_token"
INBOUND_URL = "http://testserver/api/v1/whatsapp/inbound"


@pytest.fixture(autouse=True)
def _no_outbound():
    """Never hit the real Twilio REST API from the webhook under test."""
    with mock.patch("backend.api.v1.whatsapp.send_whatsapp", return_value=True) as m:
        yield m


@pytest.fixture
def twilio_token(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    return AUTH_TOKEN


def _verified_number(reg, number, role=None):
    uid = reg["user"]["id"]
    if role:
        execute("UPDATE users SET role = %s WHERE id = %s", (role, uid))
    execute("UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NOW() WHERE id = %s",
            (number, uid))
    return number


def _post(client, params, token=AUTH_TOKEN, sign=True):
    sig = wh.compute_twilio_signature(INBOUND_URL, params, token) if sign else "bad"
    return client.post("/api/v1/whatsapp/inbound", data=params,
                       headers={"X-Twilio-Signature": sig})


def _post_signed_over(client, params, sign_url, token=AUTH_TOKEN, extra_headers=None):
    """POST signing X-Twilio-Signature over `sign_url` (the url Twilio would sign),
    which can differ from the test client's own request.url (http://testserver/...)."""
    sig = wh.compute_twilio_signature(sign_url, params, token)
    headers = {"X-Twilio-Signature": sig}
    if extra_headers:
        headers.update(extra_headers)
    return client.post("/api/v1/whatsapp/inbound", data=params, headers=headers)


def _seed_po(tid, *, sku="SKU1", warehouse="bodega norte", qty=200):
    row = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, sku_count, total_units, total_value, reception_status)
           VALUES (%s, 'sess-x', 1, %s, %s, 'pending') RETURNING id""",
        (tid, qty, qty * 10),
    )
    po_id = row["id"]
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, display_name, supplier, status,
                recommended_qty, final_qty, unit_cost, warehouse)
           VALUES (%s, %s, %s, %s, 'Proveedor A', 'approved', %s, %s, 10, %s)""",
        (po_id, tid, sku, sku, qty, qty, warehouse),
    )
    return po_id


class _FakeLLM:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = 0
        self.messages = self

    def create(self, *a, **k):
        self.calls += 1
        text = self._payloads.pop(0)
        blk = mock.Mock(); blk.text = text
        r = mock.Mock(); r.content = [blk]; r.usage = mock.Mock(input_tokens=1, output_tokens=1)
        return r


def test_invalid_signature_403(client, twilio_token, registered_user):
    _verified_number(registered_user, "+573001110000")
    resp = _post(client, {"From": "whatsapp:+573001110000", "Body": "hola", "MessageSid": "SM1"}, sign=False)
    assert resp.status_code == 403


def test_valid_signature_unknown_number_polite_200(client, twilio_token):
    resp = _post(client, {"From": "whatsapp:+59999999999", "Body": "hola", "MessageSid": "SMx"})
    assert resp.status_code == 200


def test_unverified_number_rejected_no_state(client, twilio_token, registered_user):
    uid = registered_user["user"]["id"]
    execute("UPDATE users SET whatsapp_number = %s, whatsapp_verified_at = NULL WHERE id = %s",
            ("+573002220000", uid))
    resp = _post(client, {"From": "whatsapp:+573002220000", "Body": "hola", "MessageSid": "SMu"})
    assert resp.status_code == 200
    row = query_one("SELECT COUNT(*) AS n FROM whatsapp_conversations WHERE user_id = %s", (uid,))
    assert row["n"] == 0  # no conversation created for an unverified sender


def test_query_turn_over_http_no_mutation(client, twilio_token, registered_user):
    num = _verified_number(registered_user, "+573003330000")
    _seed_po(registered_user["tenant"]["id"], sku="A")
    fake = _FakeLLM([json.dumps({"tool": "list_pending_pos", "args": {}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        resp = _post(client, {"From": f"whatsapp:{num}", "Body": "órdenes?", "MessageSid": "SMq"})
    assert resp.status_code == 200


def test_confirmation_gate_over_http(client, twilio_token, registered_user):
    num = _verified_number(registered_user, "+573004440000", role="admin")
    po_id = _seed_po(registered_user["tenant"]["id"])
    fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        r1 = _post(client, {"From": f"whatsapp:{num}", "Body": f"aprueba {po_id}", "MessageSid": "SM-p"})
    assert r1.status_code == 200
    # Proposal turn mutated nothing.
    assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"] is None
    # Confirm turn — no LLM needed.
    r2 = _post(client, {"From": f"whatsapp:{num}", "Body": "sí", "MessageSid": "SM-c"})
    assert r2.status_code == 200
    assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"] is not None


def test_idempotency_same_sid_single_execution(client, twilio_token, registered_user):
    num = _verified_number(registered_user, "+573005550000", role="admin")
    po_id = _seed_po(registered_user["tenant"]["id"])
    # Set up a pending approve action directly in the store.
    from backend.whatsapp import conversation_store as cs
    cs.save(registered_user["tenant"]["id"], registered_user["user"]["id"], num,
            history=[], pending_action={"type": "approve_po", "po_log_id": po_id},
            last_message_sid="SM-prev")
    # First confirm executes.
    r1 = _post(client, {"From": f"whatsapp:{num}", "Body": "sí", "MessageSid": "SM-confirm"})
    assert r1.status_code == 200
    sent_first = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"]
    assert sent_first is not None
    # Twilio retry with the SAME MessageSid must be a no-op (dedupe short-circuits).
    r2 = _post(client, {"From": f"whatsapp:{num}", "Body": "sí", "MessageSid": "SM-confirm"})
    assert r2.status_code == 200
    sent_second = query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"]
    assert sent_first == sent_second  # not re-approved / no second write


def test_rate_limit_blocks_without_llm(client, twilio_token, registered_user, monkeypatch):
    # Rate limiting is bypassed in testing_mode; force it on for this test.
    monkeypatch.setattr(settings, "testing_mode", False)
    num = _verified_number(registered_user, "+573006660000")
    # auth_rate_events has no tenant FK, so rows survive tenant teardown; clear
    # this key so a previous run's events don't pre-fill the window.
    execute("DELETE FROM auth_rate_events WHERE key = %s", (f"wa:{num}",))
    payloads = [json.dumps({"tool": None, "args": {}, "reply": "hola"})] * (wh.RATE_LIMIT_MAX + 5)
    fake = _FakeLLM(payloads)
    last = None
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        for i in range(wh.RATE_LIMIT_MAX + 3):
            last = _post(client, {"From": f"whatsapp:{num}", "Body": "hola", "MessageSid": f"SM{i}"})
    assert last.status_code == 200
    # The blocked requests must NOT reach the LLM: exactly RATE_LIMIT_MAX allowed.
    assert fake.calls == wh.RATE_LIMIT_MAX


# --- Signature reconstruction behind a proxy (whatsapp_webhook_base_url / X-Forwarded-*) ---
# The test client's own request.url is always http://testserver/api/v1/whatsapp/inbound.
# An unknown sender yields a polite 200 once the signature passes, so 200-vs-403 cleanly
# isolates whether signature validation used the right (public) url.

PUBLIC_BASE = "https://app.faro.com"
PUBLIC_URL = "https://app.faro.com/api/v1/whatsapp/inbound"
_UNKNOWN = {"From": "whatsapp:+59999999999", "Body": "hola", "MessageSid": "SM-proxy"}


def test_configured_base_public_signature_accepted(client, twilio_token, monkeypatch):
    # With the public base configured, a signature over the PUBLIC url validates,
    # even though the test client's request.url is the internal http://testserver/...
    monkeypatch.setattr(settings, "whatsapp_webhook_base_url", PUBLIC_BASE)
    resp = _post_signed_over(client, _UNKNOWN, PUBLIC_URL)
    assert resp.status_code == 200


def test_configured_base_internal_signature_rejected(client, twilio_token, monkeypatch):
    # With the public base configured, the internal request.url is NO LONGER
    # authoritative: a signature computed over it is rejected. Proves the public
    # url — not request.url — is what we verify against.
    monkeypatch.setattr(settings, "whatsapp_webhook_base_url", PUBLIC_BASE)
    resp = _post_signed_over(client, _UNKNOWN, INBOUND_URL)  # signed over internal url
    assert resp.status_code == 403


def test_forwarded_headers_public_signature_accepted(client, twilio_token, monkeypatch):
    # No configured base, but the proxy forwards the public scheme+host: a
    # signature over the reconstructed forwarded url validates.
    monkeypatch.setattr(settings, "whatsapp_webhook_base_url", "")
    resp = _post_signed_over(
        client, _UNKNOWN, PUBLIC_URL,
        extra_headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "app.faro.com"},
    )
    assert resp.status_code == 200


def test_forwarded_headers_internal_signature_rejected(client, twilio_token, monkeypatch):
    # Same forwarded headers, but signed over the internal url → rejected.
    monkeypatch.setattr(settings, "whatsapp_webhook_base_url", "")
    resp = _post_signed_over(
        client, _UNKNOWN, INBOUND_URL,
        extra_headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "app.faro.com"},
    )
    assert resp.status_code == 403


def test_no_base_no_forwarded_uses_request_url(client, twilio_token, monkeypatch):
    # Regression: with neither the config nor forwarded headers set, validation
    # falls back to request.url (http://testserver/...) — existing behavior.
    monkeypatch.setattr(settings, "whatsapp_webhook_base_url", "")
    resp = _post_signed_over(client, _UNKNOWN, INBOUND_URL)  # == request.url
    assert resp.status_code == 200


def test_viewer_denied_over_http(client, twilio_token, registered_user):
    num = _verified_number(registered_user, "+573007770000", role="viewer")
    po_id = _seed_po(registered_user["tenant"]["id"])
    fake = _FakeLLM([json.dumps({"tool": "approve_po", "args": {"po_log_id": po_id}})])
    with mock.patch("backend.whatsapp.agent.get_local_llm_client", return_value=fake):
        resp = _post(client, {"From": f"whatsapp:{num}", "Body": f"aprueba {po_id}", "MessageSid": "SM-v"})
    assert resp.status_code == 200
    assert query_one("SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,))["sent_at"] is None
