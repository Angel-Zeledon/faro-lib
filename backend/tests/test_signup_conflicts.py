"""Signup must refuse a taken WhatsApp number cleanly, and never strand a tenant.

Both of these were live defects. The guard in `signup` only rejected numbers a
*verified* user held, while `users_whatsapp_number_uniq` is unique over every
non-null number — so a second signup with an unverified number passed the check
and died on the index as an unhandled 500. And because the tenant row is written
before its first user, every one of those failures left behind a tenant with no
users that nobody could ever log into.
"""
import uuid

from backend.db.connection import query_one


def _payload(**over):
    tag = uuid.uuid4().hex[:10]
    body = {
        "tenant_name": f"Empresa {tag}",
        "email": f"owner.{tag}@faro-e2e.io",   # no MX: undeliverable by design
        "password": "FaroQA2026!",
        "full_name": "Owner Test",
        "whatsapp_number": f"+5068{uuid.uuid4().int % 10**7:07d}",
    }
    body.update(over)
    return body


def _tenant_count(name):
    row = query_one("SELECT COUNT(*) AS n FROM tenants WHERE name = %s", (name,))
    return row["n"] if row else 0


class TestWhatsAppNumberConflict:
    def test_second_signup_with_the_same_number_is_refused_not_a_500(self, client):
        first = _payload()
        r1 = client.post("/api/v1/auth/signup", json=first)
        assert r1.status_code == 201, r1.text

        second = _payload(whatsapp_number=first["whatsapp_number"])
        r2 = client.post("/api/v1/auth/signup", json=second)

        assert r2.status_code == 409, (
            f"expected a clean conflict, got {r2.status_code}: {r2.text}")
        assert r2.json()["error_code"] == "whatsapp_number_taken"

    def test_the_holder_does_not_have_to_be_verified(self, client):
        """The first account is never verified in this test — that is the case
        the original guard missed, because it only looked at verified holders."""
        first = _payload()
        assert client.post("/api/v1/auth/signup", json=first).status_code == 201

        owner = query_one(
            "SELECT whatsapp_verified_at FROM users WHERE email = %s", (first["email"],))
        assert owner["whatsapp_verified_at"] is None, "precondition: holder unverified"

        r = client.post("/api/v1/auth/signup",
                        json=_payload(whatsapp_number=first["whatsapp_number"]))
        assert r.status_code == 409, r.text

    def test_a_refused_signup_leaves_no_tenant_behind(self, client):
        first = _payload()
        assert client.post("/api/v1/auth/signup", json=first).status_code == 201

        second = _payload(whatsapp_number=first["whatsapp_number"])
        assert client.post("/api/v1/auth/signup", json=second).status_code == 409

        assert _tenant_count(second["tenant_name"]) == 0, (
            "the rejected signup left a tenant with no users behind")

    def test_a_refused_signup_leaves_no_user_behind(self, client):
        first = _payload()
        assert client.post("/api/v1/auth/signup", json=first).status_code == 201

        second = _payload(whatsapp_number=first["whatsapp_number"])
        client.post("/api/v1/auth/signup", json=second)

        assert query_one("SELECT id FROM users WHERE email = %s", (second["email"],)) is None


class TestEmailConflict:
    def test_duplicate_email_is_refused_and_strands_nothing(self, client):
        first = _payload()
        assert client.post("/api/v1/auth/signup", json=first).status_code == 201

        second = _payload(email=first["email"])
        r = client.post("/api/v1/auth/signup", json=second)

        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "email_already_registered"
        assert _tenant_count(second["tenant_name"]) == 0


class TestHappyPathStillWorks:
    def test_distinct_details_create_tenant_and_admin(self, client):
        body = _payload()
        r = client.post("/api/v1/auth/signup", json=body)
        assert r.status_code == 201, r.text

        data = r.json()["data"]
        user = query_one("SELECT tenant_id, role FROM users WHERE email = %s", (body["email"],))
        assert user is not None, "the admin was not written"
        assert user["role"] == "admin"
        assert user["tenant_id"] == data["tenant"]["id"]
        assert _tenant_count(body["tenant_name"]) == 1
