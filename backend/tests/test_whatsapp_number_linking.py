"""Profile endpoints to link and verify a WhatsApp number (in-app path)."""
from backend.db.connection import query_one


def _link(client, headers, number):
    return client.post("/api/v1/users/me/whatsapp/link",
                       json={"whatsapp_number": number}, headers=headers)


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
