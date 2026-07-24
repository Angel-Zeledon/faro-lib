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
    # The other user's number stays unset — the conflict blocked the write.
    from backend.db.connection import query_one as _q
    # (no row created/updated for the other user with this number)
    owners = _q("SELECT COUNT(*) AS n FROM users WHERE whatsapp_number = %s", ("+573009990006",))
    assert owners["n"] == 1
