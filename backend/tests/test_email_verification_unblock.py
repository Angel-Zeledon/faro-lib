"""
Email verification must never be a dead end.

Before this: ``POST /auth/login`` answered 403 ``email_not_verified`` and the
only verification mail ever sent was the one at signup. A message that landed
in spam locked the account out permanently, with no self-service escape.

Now:
  1. ``POST /auth/resend-verification`` re-issues the link — rate limited, and
     with an anti-enumeration response identical for unknown, verified and
     genuinely-resent addresses.
  2. Login succeeds for an unverified user and mints a token carrying
     ``email_verified: false``. That claim — not a 403 at the door — gates the
     handful of actions that reach outside the tenant (invites, integrations,
     notification sends). Everything else stays open.
  3. When signup's mail could not be sent, the response carries the
     verification URL so the UI shows it instead of pointing at an empty inbox.

Every assertion below reads the DB directly where state is involved; status
codes alone would pass just as happily against the old broken behavior.
"""

from unittest import mock
from uuid import uuid4

import pytest

from backend.auth.jwt_handler import decode_token
from backend.config import settings
from backend.db.connection import execute, query_one
from backend.users import service as user_svc


# ── Helpers ────────────────────────────────────────────────────────────────

PASSWORD = "TestPass123!"


def _make_unverified_user(tenant_id: str, role: str = "admin") -> dict:
    """A user in exactly the state signup leaves them in: active, unverified."""
    email = f"unverified-{uuid4().hex[:8]}@example.com"
    user = user_svc.create_user(
        tenant_id=tenant_id, email=email, password=PASSWORD,
        role=role, full_name="Unverified Person",
    )
    row = query_one("SELECT email_verified, status FROM users WHERE id = %s", (user["id"],))
    assert row["email_verified"] is False, "fixture precondition"
    assert row["status"] == "active", "fixture precondition"
    return {"user": user, "email": email, "password": PASSWORD}


def _login(client, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _headers(client, email: str) -> dict:
    resp = _login(client, email)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


# ── 1. Resend endpoint ─────────────────────────────────────────────────────

class TestResendVerification:
    def test_resend_sends_a_working_link_for_an_unverified_user(
        self, client, test_tenant
    ):
        acct = _make_unverified_user(test_tenant["id"])

        with mock.patch(
            "backend.notifications.email.send_verification_email", return_value=True
        ) as send:
            resp = client.post(
                "/api/v1/auth/resend-verification", json={"email": acct["email"]},
            )

        assert resp.status_code == 200, resp.text
        assert send.call_count == 1
        to_addr, _name, verify_url = send.call_args[0]
        assert to_addr == acct["email"]

        # The mailed link must actually verify — a resend that ships a dud token
        # is the same dead end with extra steps. Drive it through the real
        # endpoint and read the flag back out of the DB.
        token = verify_url.split("token=")[1]
        verified = client.post("/api/v1/auth/verify-email", json={"token": token})
        assert verified.status_code == 200, verified.text

        row = query_one(
            "SELECT email_verified FROM users WHERE id = %s", (acct["user"]["id"],)
        )
        assert row["email_verified"] is True

    def test_unknown_address_answers_the_same_and_sends_nothing(self, client):
        """Anti-enumeration: byte-identical body to a real resend."""
        real_tenant_free_email = f"ghost-{uuid4().hex[:8]}@example.com"
        with mock.patch(
            "backend.notifications.email.send_verification_email", return_value=True
        ) as send:
            resp = client.post(
                "/api/v1/auth/resend-verification", json={"email": real_tenant_free_email},
            )
        assert resp.status_code == 200, resp.text
        assert send.call_count == 0, "must not mail an address that has no account"
        assert "no existe" not in resp.text.lower() and "not found" not in resp.text.lower()

    def test_already_verified_address_answers_the_same_and_sends_nothing(
        self, client, registered_user
    ):
        with mock.patch(
            "backend.notifications.email.send_verification_email", return_value=True
        ) as send:
            resp = client.post(
                "/api/v1/auth/resend-verification",
                json={"email": registered_user["email"]},
            )
        assert resp.status_code == 200, resp.text
        assert send.call_count == 0

    def test_responses_are_indistinguishable_across_the_three_cases(
        self, client, test_tenant, registered_user
    ):
        """The whole point of the generic answer: an attacker learns nothing."""
        acct = _make_unverified_user(test_tenant["id"])
        with mock.patch(
            "backend.notifications.email.send_verification_email", return_value=True
        ):
            bodies = {
                client.post("/api/v1/auth/resend-verification",
                            json={"email": e}).json()["data"]["message"]
                for e in (acct["email"], registered_user["email"],
                          f"ghost-{uuid4().hex[:8]}@example.com")
            }
        assert len(bodies) == 1, f"responses differ between cases: {bodies}"

    def test_rate_limit_stops_the_fourth_attempt_in_the_window(
        self, client, test_tenant, monkeypatch
    ):
        # The local .env runs TESTING_MODE=true, which disables _check_rate
        # entirely — this test has to turn it back on itself or it can't fail.
        monkeypatch.setattr(settings, "testing_mode", False)
        acct = _make_unverified_user(test_tenant["id"])
        # Rate events are keyed and persisted in Postgres; start from a clean
        # window so a previous test's traffic cannot decide the outcome.
        execute("DELETE FROM auth_rate_events WHERE key = %s",
                (f"resend-verify:{acct['email'].lower()}",))

        with mock.patch(
            "backend.notifications.email.send_verification_email", return_value=True
        ) as send:
            codes = [
                client.post("/api/v1/auth/resend-verification",
                            json={"email": acct["email"]}).status_code
                for _ in range(4)
            ]

        assert codes == [200, 200, 200, 429], codes
        assert send.call_count == 3, "the throttled attempt must not reach the mailer"

        # And the limiter really recorded the window in Postgres.
        row = query_one(
            "SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s",
            (f"resend-verify:{acct['email'].lower()}",),
        )
        assert int(row["n"]) == 3


# ── 2. Limited-mode login ──────────────────────────────────────────────────

class TestUnverifiedUserCanGetIn:
    def test_login_succeeds_and_the_token_says_unverified(self, client, test_tenant):
        acct = _make_unverified_user(test_tenant["id"])

        resp = _login(client, acct["email"])
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["user"]["email_verified"] is False
        assert decode_token(data["access_token"])["email_verified"] is False

        # Logging in must not quietly verify anybody.
        row = query_one(
            "SELECT email_verified FROM users WHERE id = %s", (acct["user"]["id"],)
        )
        assert row["email_verified"] is False

    def test_a_verified_user_gets_the_verified_claim(self, client, registered_user):
        resp = _login(client, registered_user["email"], registered_user["password"])
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["user"]["email_verified"] is True
        assert decode_token(data["access_token"])["email_verified"] is True

    def test_unverified_user_can_read_the_app(self, client, test_tenant):
        """"Limited mode" has to actually let them look around, or the change
        bought nothing."""
        acct = _make_unverified_user(test_tenant["id"])
        headers = _headers(client, acct["email"])

        assert client.get("/api/v1/users/me", headers=headers).status_code == 200
        assert client.get("/api/v1/sessions", headers=headers).status_code == 200

    def test_unverified_user_can_upload_a_dataset(self, client, test_tenant):
        """Uploading data is the first step to seeing value — must stay open."""
        acct = _make_unverified_user(test_tenant["id"])
        headers = _headers(client, acct["email"])

        csv_bytes = b"sku,fecha,ventas\nA1,2024-01-01,10\nA1,2024-01-02,12\n"
        resp = client.post(
            "/api/v1/datasets", headers=headers,
            files={"file": ("sales.csv", csv_bytes, "text/csv")},
        )
        assert resp.status_code in (200, 201), resp.text

        # The upload really landed, not just answered 200.
        row = query_one(
            "SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s",
            (test_tenant["id"],),
        )
        assert int(row["n"]) >= 1

    def test_refresh_reissues_with_the_current_verification_state(
        self, client, test_tenant
    ):
        """Verifying mid-session must lift the limit without a re-login."""
        acct = _make_unverified_user(test_tenant["id"])
        login = _login(client, acct["email"])
        refresh_token = login.json()["data"]["refresh_token"]
        assert decode_token(login.json()["data"]["access_token"])["email_verified"] is False

        user_svc.mark_verified(test_tenant["id"], acct["user"]["id"])

        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200, resp.text
        assert decode_token(resp.json()["data"]["access_token"])["email_verified"] is True


class TestOutwardActionsStillRequireVerification:
    """The line the guard draws: anything that leaves the tenant."""

    def test_unverified_admin_cannot_invite_and_no_user_is_created(
        self, client, test_tenant
    ):
        acct = _make_unverified_user(test_tenant["id"], role="admin")
        headers = _headers(client, acct["email"])
        invitee = f"invitee-{uuid4().hex[:8]}@example.com"
        before = query_one(
            "SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s", (test_tenant["id"],)
        )

        resp = client.post(
            "/api/v1/users", headers=headers,
            json={"email": invitee, "role": "analyst", "full_name": "Invitee"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "email_not_verified"

        # No account was created and nobody was mailed.
        assert query_one("SELECT id FROM users WHERE email = %s", (invitee,)) is None
        after = query_one(
            "SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s", (test_tenant["id"],)
        )
        assert int(after["n"]) == int(before["n"])

    def test_verified_admin_can_invite(self, client, auth_headers, registered_user):
        """Permission pair for the block above — the guard must not be a wall
        for everyone."""
        invitee = f"invitee-{uuid4().hex[:8]}@example.com"
        resp = client.post(
            "/api/v1/users", headers=auth_headers,
            json={"email": invitee, "role": "analyst", "full_name": "Invitee"},
        )
        assert resp.status_code == 201, resp.text
        created = query_one(
            "SELECT tenant_id FROM users WHERE email = %s", (invitee,)
        )
        assert created is not None
        assert created["tenant_id"] == registered_user["tenant"]["id"]

    def test_viewer_is_denied_before_the_verification_guard(
        self, client, viewer_headers, test_tenant
    ):
        """Role first, verification second — a viewer's 403 is unchanged by
        this feature, and still creates nothing."""
        invitee = f"invitee-{uuid4().hex[:8]}@example.com"
        resp = client.post(
            "/api/v1/users", headers=viewer_headers,
            json={"email": invitee, "role": "analyst"},
        )
        assert resp.status_code == 403
        assert resp.json().get("error_code") != "email_not_verified"
        assert query_one("SELECT id FROM users WHERE email = %s", (invitee,)) is None

    def test_unverified_analyst_cannot_fire_the_inventory_alert(
        self, client, test_tenant
    ):
        acct = _make_unverified_user(test_tenant["id"], role="analyst")
        headers = _headers(client, acct["email"])

        with mock.patch(
            "backend.notifications.email.send_inventory_alert_email", return_value=True
        ) as send:
            resp = client.post(
                f"/api/v1/inventory/alerts/send-now?session_id={uuid4()}",
                headers=headers,
            )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "email_not_verified"
        assert send.call_count == 0, "nothing may leave for an unverified account"

    def test_unverified_analyst_cannot_send_a_po_to_suppliers(
        self, client, test_tenant
    ):
        acct = _make_unverified_user(test_tenant["id"], role="analyst")
        headers = _headers(client, acct["email"])

        with mock.patch(
            "backend.notifications.whatsapp.send_whatsapp", return_value=True
        ) as wa:
            resp = client.post(
                f"/api/v1/inventory/po/{uuid4()}/send", headers=headers,
            )
        # 403 from the guard, not the 404 the unknown PO would otherwise give:
        # the block happens before any work.
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "email_not_verified"
        assert wa.call_count == 0

    def test_verifying_lifts_the_block_on_the_next_login(self, client, test_tenant):
        """End to end: the escape hatch actually restores full access."""
        acct = _make_unverified_user(test_tenant["id"], role="admin")
        blocked = client.post(
            "/api/v1/users", headers=_headers(client, acct["email"]),
            json={"email": f"x-{uuid4().hex[:6]}@example.com", "role": "analyst"},
        )
        assert blocked.status_code == 403

        user_svc.mark_verified(test_tenant["id"], acct["user"]["id"])

        invitee = f"after-{uuid4().hex[:8]}@example.com"
        allowed = client.post(
            "/api/v1/users", headers=_headers(client, acct["email"]),
            json={"email": invitee, "role": "analyst"},
        )
        assert allowed.status_code == 201, allowed.text
        assert query_one("SELECT id FROM users WHERE email = %s", (invitee,)) is not None


# ── 3. Honest signup fallback ──────────────────────────────────────────────

class TestSignupShowsTheLinkWhenMailFails:
    def _signup_body(self) -> dict:
        from tests.test_endpoints import unique_phone
        return {
            "email": f"nomail-{uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
            "tenant_name": f"tenant-{uuid4().hex[:6]}",
            "whatsapp_number": unique_phone(),
        }

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        created: list[str] = []
        self._created = created
        yield
        for email in created:
            row = query_one("SELECT tenant_id FROM users WHERE email = %s", (email,))
            if row:
                execute("DELETE FROM tenants WHERE id = %s", (row["tenant_id"],))

    def test_failed_delivery_returns_a_usable_verification_link(self, client):
        body = self._signup_body()
        self._created.append(body["email"])

        with mock.patch(
            "backend.notifications.email.send_verification_email", return_value=False
        ):
            resp = client.post("/api/v1/auth/signup", json=body)

        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["email_sent"] is False
        assert data["verify_url"], "no link on screen = the user is stranded"

        # And it is a real link, not decoration: it verifies the account.
        token = data["verify_url"].split("token=")[1]
        assert client.post(
            "/api/v1/auth/verify-email", json={"token": token}
        ).status_code == 200
        row = query_one(
            "SELECT email_verified FROM users WHERE email = %s", (body["email"],)
        )
        assert row["email_verified"] is True

    def test_successful_delivery_does_not_leak_the_link(self, client):
        """The link is a bypass of proof-of-ownership — it may only appear on
        the path where no mail went out at all."""
        body = self._signup_body()
        self._created.append(body["email"])

        with mock.patch(
            "backend.notifications.email.send_verification_email", return_value=True
        ):
            resp = client.post("/api/v1/auth/signup", json=body)

        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["email_sent"] is True
        assert data["verify_url"] is None
        row = query_one(
            "SELECT email_verified FROM users WHERE email = %s", (body["email"],)
        )
        assert row["email_verified"] is False
