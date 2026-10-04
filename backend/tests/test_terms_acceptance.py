"""Acceptance of the Terms of Service and the Privacy Policy.

What has to be true:

1. A signup records WHEN the person accepted and WHICH version
   (`backend/users/terms.py`), in the users row itself.
2. A signup without the box ticked creates nothing at all — no user and no
   tenant — and answers the stable code `terms_not_accepted`, which the form
   renders in the visitor's language.
3. A trial account records acceptance at creation: /prueba states that entering
   means accepting both documents.
4. Users that existed before acceptance was recorded — and users an admin
   invites — keep NULL in both columns and keep working. Nobody's acceptance is
   invented after the fact.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.db.connection import execute, query_one
from backend.tenants.data_export import delete_tenant
from backend.users import service as user_svc
from backend.users.terms import TERMS_VERSION


def _body(**over):
    tag = uuid4().hex[:10]
    body = {
        "tenant_name": f"Terms {tag}",
        "email": f"terms.{tag}@stockai-e2e.io",   # no MX: undeliverable by design
        "password": "FaroQA2026!",
        "full_name": "Terms Test",
        "whatsapp_number": f"+5067{uuid4().int % 10**7:07d}",
        "accept_terms": True,
    }
    body.update(over)
    return body


@pytest.fixture
def cleanup():
    tenants: list[str] = []
    yield tenants
    for tenant_id in tenants:
        if query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (tenant_id,)):
            delete_tenant(tenant_id)


class TestSignupRecordsAcceptance:
    def test_accepted_signup_stores_the_time_and_the_current_version(self, client, cleanup):
        body = _body()
        before = datetime.now(timezone.utc) - timedelta(seconds=5)
        resp = client.post("/api/v1/auth/signup", json=body)
        assert resp.status_code == 201, resp.text
        cleanup.append(resp.json()["data"]["tenant"]["id"])

        row = query_one(
            "SELECT terms_accepted_at, terms_version FROM users WHERE email = %s",
            (body["email"],),
        )
        assert row is not None, "201 returned but no user row exists"
        assert row["terms_version"] == TERMS_VERSION
        assert row["terms_accepted_at"] is not None, "acceptance was not timestamped"
        assert before <= row["terms_accepted_at"] <= datetime.now(timezone.utc) + timedelta(seconds=5)

    @pytest.mark.parametrize("patch,label", [
        ({"accept_terms": False}, "box left unticked"),
        ({"accept_terms": None}, "explicit null"),
        ({}, "field missing entirely"),
    ])
    def test_signup_without_acceptance_is_refused_and_creates_nothing(
        self, client, patch, label,
    ):
        body = _body()
        if patch:
            body.update(patch)
        else:
            body.pop("accept_terms")

        resp = client.post("/api/v1/auth/signup", json=body)

        assert resp.status_code == 400, f"{label}: {resp.status_code} {resp.text}"
        assert resp.json()["error_code"] == "terms_not_accepted", label
        assert query_one(
            "SELECT id FROM users WHERE email = %s", (body["email"],),
        ) is None, f"{label}: refused, and a user row was created anyway"
        assert query_one(
            "SELECT id FROM tenants WHERE name = %s", (body["tenant_name"],),
        ) is None, f"{label}: refused, and a tenant was left behind"

    def test_refusal_comes_before_every_other_check(self, client, registered_user):
        """An unticked box on an email that is already registered must say so
        about the box, and must not reveal that the address has an account."""
        body = _body(email=registered_user["email"], accept_terms=False)
        resp = client.post("/api/v1/auth/signup", json=body)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "terms_not_accepted"
        assert query_one(
            "SELECT COUNT(*) AS n FROM users WHERE email = %s", (registered_user["email"],),
        )["n"] == 1


class TestTrialRecordsAcceptance:
    def test_trial_account_is_created_with_acceptance(self, client, cleanup):
        resp = client.post("/api/v1/trial")
        assert resp.status_code == 201, resp.text
        email = resp.json()["data"]["email"]
        row = query_one(
            "SELECT tenant_id, terms_accepted_at, terms_version FROM users WHERE email = %s",
            (email,),
        )
        assert row is not None
        cleanup.append(row["tenant_id"])
        assert row["terms_version"] == TERMS_VERSION
        assert row["terms_accepted_at"] is not None


class TestExistingUsersAreUnaffected:
    def test_a_user_created_without_acceptance_keeps_null_and_still_logs_in(
        self, client, registered_user,
    ):
        # `registered_user` is created the way every pre-2026-10-02 user was:
        # no acceptance passed.
        row = query_one(
            "SELECT terms_accepted_at, terms_version FROM users WHERE id = %s",
            (registered_user["user"]["id"],),
        )
        assert row["terms_accepted_at"] is None
        assert row["terms_version"] is None

        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"], "password": registered_user["password"],
        })
        assert resp.status_code == 200, resp.text

    def test_a_row_inserted_without_the_columns_reads_null(self, client, test_tenant):
        """What a user written by the code before this change looks like: the
        INSERT never named the new columns. The migration adds them with no
        default, so the row reads NULL rather than an invented acceptance."""
        user_id = f"usr_legacy_{uuid4().hex[:10]}"
        email = f"legacy-{uuid4().hex[:8]}@example.com"
        execute(
            """INSERT INTO users (id, tenant_id, email, full_name, role, hashed_password,
                                  email_verified, status, created_at, updated_at)
               VALUES (%s, %s, %s, 'Legacy', 'analyst', 'x', TRUE, 'active', NOW(), NOW())""",
            (user_id, test_tenant["id"], email),
        )
        row = query_one(
            "SELECT terms_accepted_at, terms_version FROM users WHERE id = %s", (user_id,),
        )
        assert row["terms_accepted_at"] is None
        assert row["terms_version"] is None

    def test_an_invited_user_does_not_inherit_the_admins_acceptance(self, test_tenant):
        invited = user_svc.create_user_admin(
            tenant_id=test_tenant["id"],
            email=f"invited-{uuid4().hex[:8]}@example.com",
            temp_password="TempPass123!",
        )
        row = query_one(
            "SELECT terms_accepted_at, terms_version FROM users WHERE id = %s",
            (invited["id"],),
        )
        assert row["terms_accepted_at"] is None
        assert row["terms_version"] is None
