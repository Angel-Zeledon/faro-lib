"""Profile endpoints to link and verify a WhatsApp number (in-app path)."""
import re

import pytest

from backend.config import settings
from backend.db.connection import query_one


@pytest.fixture(autouse=True)
def _no_twilio(monkeypatch):
    """The local .env may carry real Twilio sandbox creds. Tests must never hit
    the Twilio API: default to the not-configured path (link returns
    debug_code) and blow up on any unexpected transport call. Delivery-path
    tests monkeypatch is_configured/send_whatsapp themselves on top of this."""
    def _unexpected_send(*args, **kwargs):
        raise AssertionError("unexpected real Twilio send during tests")

    monkeypatch.setattr("backend.notifications.whatsapp.is_configured", lambda: False)
    monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp", _unexpected_send)


def _link(client, headers, number):
    return client.post("/api/v1/users/me/whatsapp/link",
                       json={"whatsapp_number": number}, headers=headers)


def _confirm(client, headers, code):
    return client.post("/api/v1/users/me/whatsapp/confirm",
                       json={"code": code}, headers=headers)


def _patch_me_number(client, headers, number):
    return client.patch("/api/v1/users/me",
                        json={"whatsapp_number": number}, headers=headers)


def _link_and_verify(client, headers, number):
    resp = _link(client, headers, number)
    assert resp.status_code == 200, resp.text
    code = resp.json()["data"]["debug_code"]
    resp = _confirm(client, headers, code)
    assert resp.status_code == 200, resp.text


def test_link_then_confirm_sets_verified_at(client, auth_headers, registered_user):
    uid = registered_user["user"]["id"]
    resp = _link(client, auth_headers, "+573009990001")
    assert resp.status_code == 200, resp.text
    code = resp.json()["data"]["debug_code"]

    # Not yet verified in the DB.
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] == "+573009990001"
    assert row["whatsapp_verified_at"] is None

    resp = client.post("/api/v1/users/me/whatsapp/confirm",
                       json={"code": code}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is not None


def test_link_rejects_bad_format(client, auth_headers):
    resp = _link(client, auth_headers, "3009990002")
    assert resp.status_code == 422


def test_confirm_wrong_code_400_and_unverified(client, auth_headers, registered_user):
    uid = registered_user["user"]["id"]
    _link(client, auth_headers, "+573009990003")
    resp = client.post("/api/v1/users/me/whatsapp/confirm",
                       json={"code": "000000"}, headers=auth_headers)
    assert resp.status_code == 400
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is None


def test_link_requires_auth(client):
    resp = client.post("/api/v1/users/me/whatsapp/link",
                       json={"whatsapp_number": "+573009990004"})
    assert resp.status_code in (401, 403)


def test_unlink_clears_number_and_verified_at(client, auth_headers, registered_user):
    """Unlink (PATCH /users/me with an empty number) must null BOTH columns.
    Leaving whatsapp_verified_at set for a now-absent number is stale, dangling
    state — and would make a later, unrelated number look already-verified."""
    uid = registered_user["user"]["id"]
    resp = _link(client, auth_headers, "+573009990005")
    code = resp.json()["data"]["debug_code"]
    client.post("/api/v1/users/me/whatsapp/confirm",
                json={"code": code}, headers=auth_headers)
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] == "+573009990005"
    assert row["whatsapp_verified_at"] is not None

    unlink = client.patch("/api/v1/users/me", json={"whatsapp_number": ""}, headers=auth_headers)
    assert unlink.status_code == 200, unlink.text
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] is None
    assert row["whatsapp_verified_at"] is None


def test_link_number_verified_by_other_user_conflicts_409(
    client, auth_headers, registered_user, make_tenant_user_headers
):
    """A number already VERIFIED by another user cannot be linked again."""
    resp = _link(client, auth_headers, "+573009990006")
    code = resp.json()["data"]["debug_code"]
    client.post("/api/v1/users/me/whatsapp/confirm",
                json={"code": code}, headers=auth_headers)

    other = make_tenant_user_headers(role="analyst")
    dup = _link(client, other, "+573009990006")
    assert dup.status_code == 409, dup.text
    assert dup.json()["error_code"] == "whatsapp_number_taken"
    # The other user's number stays unset — the conflict blocked the write.
    from backend.db.connection import query_one as _q
    # (no row created/updated for the other user with this number)
    owners = _q("SELECT COUNT(*) AS n FROM users WHERE whatsapp_number = %s", ("+573009990006",))
    assert owners["n"] == 1


# ── PATCH /users/me must not bypass verification ──────────────────────────────
# identity.resolve_sender authenticates the WhatsApp bot purely on
# number + whatsapp_verified_at, so the profile PATCH must never carry a
# verified timestamp over to a different number, nor accept a number another
# user already verified.

def test_patch_me_new_number_clears_verified_at(client, auth_headers, registered_user):
    uid = registered_user["user"]["id"]
    _link_and_verify(client, auth_headers, "+573009990011")

    resp = _patch_me_number(client, auth_headers, "+573009990012")
    assert resp.status_code == 200, resp.text
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] == "+573009990012"
    assert row["whatsapp_verified_at"] is None


def test_patch_me_same_number_keeps_verified_at(client, auth_headers, registered_user):
    uid = registered_user["user"]["id"]
    _link_and_verify(client, auth_headers, "+573009990013")

    resp = _patch_me_number(client, auth_headers, "+573009990013")
    assert resp.status_code == 200, resp.text
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] == "+573009990013"
    assert row["whatsapp_verified_at"] is not None


def test_patch_me_number_verified_by_other_user_409(
    client, auth_headers, registered_user, make_tenant_user_headers
):
    _link_and_verify(client, auth_headers, "+573009990014")

    other = make_tenant_user_headers(role="analyst")
    me = client.get("/api/v1/users/me", headers=other)
    other_id = me.json()["data"]["id"]

    resp = _patch_me_number(client, other, "+573009990014")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error_code"] == "whatsapp_number_taken"
    # The conflicting write never happened.
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (other_id,))
    assert row["whatsapp_number"] is None
    assert row["whatsapp_verified_at"] is None
    owners = query_one("SELECT COUNT(*) AS n FROM users WHERE whatsapp_number = %s", ("+573009990014",))
    assert owners["n"] == 1


def test_patch_me_requires_auth(client):
    resp = client.patch("/api/v1/users/me", json={"whatsapp_number": "+573009990015"})
    assert resp.status_code in (401, 403)


def test_viewer_can_link_and_verify_own_number(client, viewer_headers, viewer_user):
    """Self-scoped endpoint: any authenticated role may link its own number."""
    uid = viewer_user["user"]["id"]
    _link_and_verify(client, viewer_headers, "+573009990016")
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] == "+573009990016"
    assert row["whatsapp_verified_at"] is not None


# ── Resend cooldown ───────────────────────────────────────────────────────────

def test_link_resend_cooldown_429(client, auth_headers, registered_user, monkeypatch):
    monkeypatch.setattr(settings, "testing_mode", False)
    uid = registered_user["user"]["id"]

    first = _link(client, auth_headers, "+573009990017")
    assert first.status_code == 200, first.text
    code = first.json()["data"]["debug_code"]

    second = _link(client, auth_headers, "+573009990017")
    assert second.status_code == 429, second.text
    body = second.json()
    assert body["error_code"] == "whatsapp_code_resend_cooldown"
    assert 1 <= body["error_params"]["retry_after"] <= 60

    # The blocked call must NOT have replaced the pending code: exactly one
    # unused code row remains and the FIRST code still confirms.
    n = query_one(
        "SELECT COUNT(*) AS n FROM pw_change_codes WHERE user_id = %s AND purpose = 'whatsapp' AND used = FALSE",
        (uid,),
    )
    assert n["n"] == 1
    resp = _confirm(client, auth_headers, code)
    assert resp.status_code == 200, resp.text
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is not None


def test_link_cooldown_bypassed_in_testing_mode(client, auth_headers, registered_user):
    # TESTING_MODE=true (the suite default) must keep back-to-back links working.
    first = _link(client, auth_headers, "+573009990018")
    assert first.status_code == 200, first.text
    second = _link(client, auth_headers, "+573009990018")
    assert second.status_code == 200, second.text


# ── Delivery ──────────────────────────────────────────────────────────────────

def test_link_sends_code_via_twilio_when_configured(client, auth_headers, registered_user, monkeypatch):
    sent = {}

    def fake_send(to_number, body, media_url=None):
        sent["to"] = to_number
        sent["body"] = body
        return True

    monkeypatch.setattr("backend.notifications.whatsapp.is_configured", lambda: True)
    monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp", fake_send)

    uid = registered_user["user"]["id"]
    resp = _link(client, auth_headers, "+573009990019")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["sent"] is True
    # The code must never leak in the response when it was actually delivered.
    assert "debug_code" not in data

    assert sent["to"] == "+573009990019"
    # The body comes from the backend locale catalog (no inline Spanish in code).
    from backend.notifications.locale import render_es
    m = re.search(r"\b(\d{6})\b", sent["body"])
    assert m, f"no 6-digit code in message body: {sent['body']!r}"
    code = m.group(1)
    assert sent["body"] == render_es("whatsapp_verification_code", code=code)

    # The delivered code is the real one: confirming it verifies the number.
    resp = _confirm(client, auth_headers, code)
    assert resp.status_code == 200, resp.text
    row = query_one("SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] == "+573009990019"
    assert row["whatsapp_verified_at"] is not None


def test_link_twilio_delivery_failure_503(client, auth_headers, registered_user, monkeypatch):
    monkeypatch.setattr("backend.notifications.whatsapp.is_configured", lambda: True)
    monkeypatch.setattr("backend.notifications.whatsapp.send_whatsapp", lambda *a, **k: False)

    uid = registered_user["user"]["id"]
    resp = _link(client, auth_headers, "+573009990020")
    assert resp.status_code == 503, resp.text
    assert resp.json()["error_code"] == "whatsapp_delivery_failed"
    assert "debug_code" not in resp.text
    row = query_one("SELECT whatsapp_verified_at FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_verified_at"] is None


def test_link_production_without_twilio_503(client, auth_headers, registered_user, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("backend.notifications.whatsapp.is_configured", lambda: False)

    uid = registered_user["user"]["id"]
    resp = _link(client, auth_headers, "+573009990021")
    assert resp.status_code == 503, resp.text
    assert resp.json()["error_code"] == "whatsapp_delivery_unavailable"
    assert "debug_code" not in resp.text
    # Fails before any state is touched: no pending number, no code issued.
    row = query_one("SELECT whatsapp_number FROM users WHERE id = %s", (uid,))
    assert row["whatsapp_number"] is None
    codes = query_one(
        "SELECT COUNT(*) AS n FROM pw_change_codes WHERE user_id = %s AND purpose = 'whatsapp'",
        (uid,),
    )
    assert codes["n"] == 0


def test_link_no_twilio_outside_production_returns_debug_code(client, auth_headers, monkeypatch):
    monkeypatch.setattr("backend.notifications.whatsapp.is_configured", lambda: False)
    resp = _link(client, auth_headers, "+573009990022")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["sent"] is False
    assert re.fullmatch(r"\d{6}", data["debug_code"])
