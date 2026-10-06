"""Per-tenant session and password policy: what Python enforces.

The admin routes that WRITE the policy are Rust-only (`backend-rs/src/routes/
session_policy.rs`, covered by its unit tests and the contract harness), so
these tests insert the `tenant_session_policies` row directly and then drive the
real Python endpoints: login, refresh, password reset, the JWT guard.

The rule every group below starts from: **no policy = unchanged**. The first
class proves it in the database (no write, no new column touched), because a
feature that only "seems" inert is the kind that skews a tenant that never
asked for it.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt as pyjwt
import pytest

from backend.auth.jwt_handler import create_access_token, create_signed_token
from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.users import service as user_svc

NEW_PASSWORD = "Brand-New-Pass-77!"


# ── helpers ──────────────────────────────────────────────────────────────────

def _set_policy(tenant_id: str, **cols) -> None:
    cols = {"tenant_id": tenant_id, **cols}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "tenant_id")
    conflict = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
    execute(
        f"INSERT INTO tenant_session_policies ({names}) VALUES ({marks}) "
        f"ON CONFLICT (tenant_id) {conflict}",
        tuple(cols.values()),
    )


def _login(client, who: dict, password: str | None = None):
    return client.post("/api/v1/auth/login", json={
        "email": who["email"], "password": password or who["password"],
    })


def _refresh(client, raw: str):
    return client.post("/api/v1/auth/refresh", json={"refresh_token": raw})


def _forged(user: dict, *, iat_ago_s: float = 0.0, sat_ago_s: float | None = None) -> dict:
    """An access token minted `iat_ago_s` seconds ago (and a `sat` claim)."""
    now = datetime.now(timezone.utc).timestamp()
    claims = {
        "sub": user["id"], "tenant_id": user["tenant_id"], "role": user.get("role", "admin"),
        "email_verified": True, "jti": uuid4().hex[:16], "type": "access",
        "iat": now - iat_ago_s, "exp": now + 900,
    }
    if sat_ago_s is not None:
        claims["sat"] = now - sat_ago_s
    token = pyjwt.encode(claims, settings.secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _row(user_id: str) -> dict:
    return query_one(
        "SELECT last_activity_at, password_changed_at, failed_login_count, locked_until "
        "FROM users WHERE id = %s", (user_id,))


def _events(tenant_id: str, action: str) -> list[dict]:
    return query("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = %s",
                 (tenant_id, action))


def _tokens(user_id: str) -> int:
    return query_one("SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = %s",
                     (user_id,))["n"]


@pytest.fixture
def tenant_id(registered_user):
    return registered_user["tenant"]["id"]


@pytest.fixture
def admin(registered_user):
    return {"id": registered_user["user"]["id"], "tenant_id": registered_user["tenant"]["id"],
            "role": "admin", "email": registered_user["email"],
            "password": registered_user["password"]}


# ── no policy = unchanged ────────────────────────────────────────────────────

class TestNoPolicyChangesNothing:
    def test_login_refresh_and_requests_work_and_write_no_policy_state(
        self, client, admin,
    ):
        r = _login(client, admin)
        assert r.status_code == 200
        before = _row(admin["id"])
        headers = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
        for _ in range(3):
            assert client.get("/api/v1/me/preferences", headers=headers).status_code == 200
        ref = _refresh(client, r.json()["data"]["refresh_token"])
        assert ref.status_code == 200
        after = _row(admin["id"])
        # Requests wrote nothing: the activity column moved only at login.
        assert after["last_activity_at"] == before["last_activity_at"]
        assert after["failed_login_count"] == 0 and after["locked_until"] is None

    def test_a_wrong_password_counts_nothing_without_a_lockout_policy(self, client, admin):
        for _ in range(8):
            assert _login(client, admin, "Wrong-pass-1").status_code == 401
        assert _row(admin["id"])["failed_login_count"] == 0
        assert _login(client, admin).status_code == 200

    def test_a_row_with_every_limit_unset_is_the_same_as_no_row(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id)  # all NULL / FALSE
        for _ in range(6):
            assert _login(client, admin, "Wrong-pass-1").status_code == 401
        assert _login(client, admin).status_code == 200
        assert _row(admin["id"])["failed_login_count"] == 0

    def test_six_logins_keep_the_five_newest_sessions_as_always(self, client, admin):
        for _ in range(6):
            assert _login(client, admin).status_code == 200
        assert _tokens(admin["id"]) == 5

    def test_the_login_token_has_no_sat_claim_and_a_refreshed_one_does(self, client, admin):
        r = _login(client, admin).json()["data"]
        claims = pyjwt.decode(r["access_token"], settings.secret_key, algorithms=["HS256"])
        assert "sat" not in claims
        started = query_one("SELECT created_at FROM refresh_tokens WHERE user_id = %s",
                            (admin["id"],))["created_at"]
        new = _refresh(client, r["refresh_token"]).json()["data"]["access_token"]
        sat = pyjwt.decode(new, settings.secret_key, algorithms=["HS256"])["sat"]
        assert abs(sat - started.timestamp()) < 0.001


# ── maximum session lifetime ─────────────────────────────────────────────────

class TestMaxSessionLifetime:
    def test_a_token_older_than_the_limit_is_refused_and_a_younger_one_is_not(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, max_session_hours=8)
        old = client.get("/api/v1/me/preferences", headers=_forged(admin, iat_ago_s=9 * 3600))
        assert old.status_code == 401
        assert old.json()["error_code"] == "session_max_lifetime"
        assert old.json()["error_params"] == {"hours": 8}
        young = client.get("/api/v1/me/preferences", headers=_forged(admin, iat_ago_s=7 * 3600))
        assert young.status_code == 200

    def test_the_session_start_claim_beats_the_tokens_own_age(self, client, admin, tenant_id):
        """A refreshed token is minutes old but the session is 9 hours old."""
        _set_policy(tenant_id, max_session_hours=8)
        r = client.get("/api/v1/me/preferences",
                       headers=_forged(admin, iat_ago_s=60, sat_ago_s=9 * 3600))
        assert r.status_code == 401 and r.json()["error_code"] == "session_max_lifetime"

    def test_without_the_policy_the_same_old_session_is_fine(self, client, admin):
        r = client.get("/api/v1/me/preferences",
                       headers=_forged(admin, iat_ago_s=60, sat_ago_s=100 * 3600))
        assert r.status_code == 200

    def test_a_policy_of_another_tenant_does_not_apply(self, client, admin):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            _set_policy(other["id"], max_session_hours=1)
            r = client.get("/api/v1/me/preferences",
                           headers=_forged(admin, iat_ago_s=5 * 3600))
            assert r.status_code == 200
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_refresh_past_the_limit_is_refused_and_the_refresh_token_is_deleted(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, max_session_hours=8)
        raw = _login(client, admin).json()["data"]["refresh_token"]
        execute("UPDATE refresh_tokens SET created_at = NOW() - interval '9 hours' "
                "WHERE user_id = %s", (admin["id"],))
        r = _refresh(client, raw)
        assert r.status_code == 401 and r.json()["error_code"] == "session_max_lifetime"
        assert _tokens(admin["id"]) == 0, "the refused session must not be retryable"
        # ...and the same token is now simply invalid.
        assert _refresh(client, raw).json()["error_code"] == "refresh_token_invalid"

    def test_refresh_inside_the_limit_carries_the_session_start_forward(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, max_session_hours=8)
        raw = _login(client, admin).json()["data"]["refresh_token"]
        execute("UPDATE refresh_tokens SET created_at = NOW() - interval '7 hours' "
                "WHERE user_id = %s", (admin["id"],))
        r = _refresh(client, raw)
        assert r.status_code == 200
        claims = pyjwt.decode(r.json()["data"]["access_token"], settings.secret_key,
                              algorithms=["HS256"])
        assert datetime.now(timezone.utc).timestamp() - claims["sat"] > 6.9 * 3600
        # The new token is bounded by the ORIGINAL start, not by its own age.
        execute("UPDATE refresh_tokens SET created_at = NOW() - interval '9 hours' "
                "WHERE user_id = %s", (admin["id"],))
        assert _refresh(client, raw).status_code == 401


# ── idle timeout ─────────────────────────────────────────────────────────────

class TestIdleTimeout:
    def test_idle_past_the_limit_is_refused(self, client, admin, tenant_id):
        _set_policy(tenant_id, idle_timeout_minutes=30)
        execute("UPDATE users SET last_activity_at = NOW() - interval '31 minutes' "
                "WHERE id = %s", (admin["id"],))
        r = client.get("/api/v1/me/preferences", headers=_forged(admin))
        assert r.status_code == 401 and r.json()["error_code"] == "session_idle_timeout"
        assert r.json()["error_params"] == {"minutes": 30}
        # A refused request is not activity: the person stays idle until login.
        assert _row(admin["id"])["last_activity_at"] < datetime.now(timezone.utc) - timedelta(minutes=30)

    def test_a_request_inside_the_limit_counts_as_activity_but_is_throttled(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, idle_timeout_minutes=30)
        execute("UPDATE users SET last_activity_at = NOW() - interval '10 minutes' "
                "WHERE id = %s", (admin["id"],))
        assert client.get("/api/v1/me/preferences", headers=_forged(admin)).status_code == 200
        first = _row(admin["id"])["last_activity_at"]
        assert first > datetime.now(timezone.utc) - timedelta(seconds=30)
        # A second request right away does not write again.
        assert client.get("/api/v1/me/preferences", headers=_forged(admin)).status_code == 200
        assert _row(admin["id"])["last_activity_at"] == first

    def test_a_background_poll_is_checked_but_never_keeps_the_session_alive(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, idle_timeout_minutes=30)
        execute("UPDATE users SET last_activity_at = NOW() - interval '10 minutes' "
                "WHERE id = %s", (admin["id"],))
        before = _row(admin["id"])["last_activity_at"]
        poll = {**_forged(admin), "X-StockAI-Background": "1"}
        assert client.get("/api/v1/me/preferences", headers=poll).status_code == 200
        assert _row(admin["id"])["last_activity_at"] == before
        # ...and once idle, the poll is refused like anything else.
        execute("UPDATE users SET last_activity_at = NOW() - interval '31 minutes' "
                "WHERE id = %s", (admin["id"],))
        r = client.get("/api/v1/me/preferences", headers={**_forged(admin), "X-StockAI-Background": "1"})
        assert r.status_code == 401

    def test_never_active_since_the_policy_was_set_is_not_a_lockout(
        self, client, admin, tenant_id,
    ):
        execute("UPDATE users SET last_activity_at = NULL WHERE id = %s", (admin["id"],))
        _set_policy(tenant_id, idle_timeout_minutes=30)
        assert client.get("/api/v1/me/preferences", headers=_forged(admin)).status_code == 200
        assert _row(admin["id"])["last_activity_at"] is not None

    def test_login_resets_the_idle_clock(self, client, admin, tenant_id):
        _set_policy(tenant_id, idle_timeout_minutes=30)
        execute("UPDATE users SET last_activity_at = NOW() - interval '5 hours' "
                "WHERE id = %s", (admin["id"],))
        r = _login(client, admin)
        assert r.status_code == 200
        headers = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
        assert client.get("/api/v1/me/preferences", headers=headers).status_code == 200

    def test_refresh_while_idle_is_refused_and_deletes_the_refresh_token(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, idle_timeout_minutes=30)
        raw = _login(client, admin).json()["data"]["refresh_token"]
        execute("UPDATE users SET last_activity_at = NOW() - interval '45 minutes' "
                "WHERE id = %s", (admin["id"],))
        r = _refresh(client, raw)
        assert r.status_code == 401 and r.json()["error_code"] == "session_idle_timeout"
        assert _tokens(admin["id"]) == 0

    def test_no_idle_limit_means_no_activity_writes_at_all(self, client, admin, tenant_id):
        _set_policy(tenant_id, max_session_hours=8)  # a policy, but not an idle one
        execute("UPDATE users SET last_activity_at = NULL WHERE id = %s", (admin["id"],))
        assert client.get("/api/v1/me/preferences", headers=_forged(admin)).status_code == 200
        assert _row(admin["id"])["last_activity_at"] is None

    def test_an_api_key_is_not_subject_to_the_session_policy(self, client, admin, tenant_id):
        """Integrations have no session: the lifetime and idle limits belong to
        people's tokens, and a key keeps working while its creator sits idle."""
        from backend.auth.api_key_auth import hash_key
        raw = f"sk_live_{uuid4().hex}{uuid4().hex}"
        execute(
            """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4)
               VALUES (gen_random_uuid()::text, %s, 'policy-key', %s, 'viewer', 'usr_test', %s)""",
            (tenant_id, hash_key(raw), raw[-4:]))
        _set_policy(tenant_id, idle_timeout_minutes=5, max_session_hours=1)
        execute("UPDATE users SET last_activity_at = NOW() - interval '9 hours' "
                "WHERE id = %s", (admin["id"],))
        r = client.get("/api/v1/alerts", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 200, r.text


# ── password rules ───────────────────────────────────────────────────────────

class TestPasswordRules:
    def _reset(self, client, admin, password: str):
        token = create_signed_token({"sub": admin["id"], "tenant_id": admin["tenant_id"],
                                     "purpose": "password_reset"}, expires_minutes=15)
        return client.post("/api/v1/auth/reset-password",
                           json={"token": token, "new_password": password})

    def _hash(self, user_id: str) -> str:
        return query_one("SELECT hashed_password FROM users WHERE id = %s", (user_id,))[
            "hashed_password"]

    def test_minimum_length_refuses_a_short_password_and_changes_nothing(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, min_password_length=14)
        before = self._hash(admin["id"])
        r = self._reset(client, admin, "Short-pass-1")
        assert r.status_code == 400
        assert r.json()["error_code"] == "password_policy"
        params = r.json()["error_params"]
        assert params["min_length"] == 14 and params["broken"] == ["min_length"]
        assert self._hash(admin["id"]) == before
        assert self._reset(client, admin, "Long-enough-pass-1").status_code == 200
        assert self._hash(admin["id"]) != before

    def test_mixed_case_and_symbol_are_each_enforced(self, client, admin, tenant_id):
        _set_policy(tenant_id, require_mixed_case=True, require_symbol=True)
        r = self._reset(client, admin, "alllowercase123")
        assert r.json()["error_params"]["broken"] == ["mixed_case", "symbol"]
        r = self._reset(client, admin, "MixedCase1234")
        assert r.json()["error_params"]["broken"] == ["symbol"]
        r = self._reset(client, admin, "lowercase-123!")
        assert r.json()["error_params"]["broken"] == ["mixed_case"]
        assert self._reset(client, admin, "MixedCase-123").status_code == 200

    def test_the_product_rules_still_come_first(self, client, admin, tenant_id):
        _set_policy(tenant_id, min_password_length=14)
        r = self._reset(client, admin, "nodigitsatall-here")
        assert r.json()["error_code"] == "password_invalid"

    def test_no_policy_keeps_the_eight_character_rule(self, client, admin):
        assert self._reset(client, admin, "Abcdef12").status_code == 200

    def test_a_rejected_password_leaves_the_reset_link_usable(self, client, admin, tenant_id):
        _set_policy(tenant_id, min_password_length=20)
        token = create_signed_token({"sub": admin["id"], "tenant_id": admin["tenant_id"],
                                     "purpose": "password_reset"}, expires_minutes=15)
        body = {"token": token, "new_password": "Too-short-1"}
        assert client.post("/api/v1/auth/reset-password", json=body).status_code == 400
        body["new_password"] = "A-long-enough-password-1"
        assert client.post("/api/v1/auth/reset-password", json=body).status_code == 200

    def test_change_password_request_applies_the_callers_tenant_policy(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, min_password_length=16)
        headers = _forged(admin)
        r = client.post("/api/v1/users/me/change-password/request",
                        json={"new_password": "Short-pass-1"}, headers=headers)
        assert r.status_code == 400 and r.json()["error_code"] == "password_policy"
        assert query_one("SELECT COUNT(*) AS n FROM pw_change_codes WHERE user_id = %s",
                         (admin["id"],))["n"] == 0, "no code may be issued for a refused password"

    def test_another_tenants_policy_does_not_apply(self, client, admin):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            _set_policy(other["id"], min_password_length=40)
            assert self._reset(client, admin, "Abcdef12").status_code == 200
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


# ── password maximum age ─────────────────────────────────────────────────────

class TestPasswordMaxAge:
    def test_an_old_password_is_refused_at_login_after_the_password_matched(
        self, client, admin, tenant_id,
    ):
        execute("UPDATE users SET password_changed_at = NOW() - interval '100 days' "
                "WHERE id = %s", (admin["id"],))
        _set_policy(tenant_id, password_max_age_days=90,
                    password_max_age_since=datetime.now(timezone.utc) - timedelta(days=95))
        r = _login(client, admin)
        assert r.status_code == 403 and r.json()["error_code"] == "password_expired"
        assert r.json()["error_params"] == {"max_age_days": 90}
        # A WRONG password still says nothing about the age.
        assert _login(client, admin, "Wrong-pass-1").json()["error_code"] == "invalid_credentials"
        # No session was opened by the refused login.
        assert _tokens(admin["id"]) == 0

    def test_switching_the_limit_on_never_locks_out_old_passwords(
        self, client, admin, tenant_id,
    ):
        """`password_max_age_since` = the day the limit was set: an old password
        gets the whole limit from then."""
        execute("UPDATE users SET password_changed_at = NOW() - interval '400 days' "
                "WHERE id = %s", (admin["id"],))
        _set_policy(tenant_id, password_max_age_days=90,
                    password_max_age_since=datetime.now(timezone.utc))
        assert _login(client, admin).status_code == 200

    def test_a_password_inside_the_limit_logs_in(self, client, admin, tenant_id):
        execute("UPDATE users SET password_changed_at = NOW() - interval '10 days' "
                "WHERE id = %s", (admin["id"],))
        _set_policy(tenant_id, password_max_age_days=90,
                    password_max_age_since=datetime.now(timezone.utc) - timedelta(days=200))
        assert _login(client, admin).status_code == 200

    def test_a_never_changed_password_ages_from_account_creation(
        self, client, admin, tenant_id,
    ):
        execute("UPDATE users SET created_at = NOW() - interval '200 days', "
                "password_changed_at = NULL WHERE id = %s", (admin["id"],))
        _set_policy(tenant_id, password_max_age_days=90,
                    password_max_age_since=datetime.now(timezone.utc) - timedelta(days=150))
        assert _login(client, admin).json()["error_code"] == "password_expired"

    def test_an_account_without_a_password_never_expires(self, client, admin, tenant_id):
        execute("UPDATE users SET password_changed_at = NOW() - interval '400 days', "
                "has_password = FALSE WHERE id = %s", (admin["id"],))
        from backend.auth.session_policy import get_policy, password_age_refusal
        _set_policy(tenant_id, password_max_age_days=90,
                    password_max_age_since=datetime.now(timezone.utc) - timedelta(days=300))
        user = query_one("SELECT * FROM users WHERE id = %s", (admin["id"],))
        assert password_age_refusal(user, get_policy(tenant_id)) is None

    def test_resetting_the_password_restarts_the_clock_and_logs_in(
        self, client, admin, tenant_id,
    ):
        execute("UPDATE users SET password_changed_at = NOW() - interval '100 days' "
                "WHERE id = %s", (admin["id"],))
        _set_policy(tenant_id, password_max_age_days=90,
                    password_max_age_since=datetime.now(timezone.utc) - timedelta(days=95))
        assert _login(client, admin).status_code == 403
        token = create_signed_token({"sub": admin["id"], "tenant_id": tenant_id,
                                     "purpose": "password_reset"}, expires_minutes=15)
        assert client.post("/api/v1/auth/reset-password",
                           json={"token": token, "new_password": NEW_PASSWORD}).status_code == 200
        assert _row(admin["id"])["password_changed_at"] is not None
        assert _login(client, admin, NEW_PASSWORD).status_code == 200


# ── concurrent sessions ──────────────────────────────────────────────────────

class TestConcurrentSessions:
    def test_the_limit_keeps_only_the_newest_sessions(self, client, admin, tenant_id):
        _set_policy(tenant_id, max_concurrent_sessions=2)
        raws = [_login(client, admin).json()["data"]["refresh_token"] for _ in range(3)]
        assert _tokens(admin["id"]) == 2
        assert _refresh(client, raws[0]).status_code == 401, "the oldest session was evicted"
        assert _refresh(client, raws[1]).status_code == 200
        assert _refresh(client, raws[2]).status_code == 200

    def test_a_limit_of_one_means_the_new_login_wins(self, client, admin, tenant_id):
        _set_policy(tenant_id, max_concurrent_sessions=1)
        first = _login(client, admin).json()["data"]["refresh_token"]
        second = _login(client, admin).json()["data"]["refresh_token"]
        assert _tokens(admin["id"]) == 1
        assert _refresh(client, first).status_code == 401
        assert _refresh(client, second).status_code == 200

    def test_other_users_sessions_are_not_touched(
        self, client, admin, tenant_id, analyst_user,
    ):
        _set_policy(tenant_id, max_concurrent_sessions=1)
        _login(client, analyst_user)
        _login(client, admin)
        _login(client, admin)
        assert _tokens(analyst_user["user"]["id"]) == 1
        assert _tokens(admin["id"]) == 1


# ── lockout ──────────────────────────────────────────────────────────────────

class TestLockout:
    def test_the_threshold_locks_the_account_and_a_correct_password_proves_nothing(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, lockout_threshold=3, lockout_minutes=10)
        for n in (1, 2, 3):
            r = _login(client, admin, "Wrong-pass-1")
            assert r.status_code == 401 and r.json()["error_code"] == "invalid_credentials"
            assert _row(admin["id"])["failed_login_count"] == n
        row = _row(admin["id"])
        assert row["locked_until"] is not None
        minutes = (row["locked_until"] - datetime.now(timezone.utc)).total_seconds() / 60
        assert 9 < minutes <= 10

        r = _login(client, admin)  # the CORRECT password
        assert r.status_code == 403 and r.json()["error_code"] == "account_locked"
        assert r.json()["error_params"]["minutes"] in (9, 10)
        assert _tokens(admin["id"]) == 0, "a locked account opened no session"
        assert _row(admin["id"])["failed_login_count"] == 3, "a locked account counts nothing more"

    def test_one_lockout_event_is_recorded_once_with_its_reason(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, lockout_threshold=3)
        for _ in range(3):
            _login(client, admin, "Wrong-pass-1")
        _login(client, admin)  # refused while locked: no second event
        events = _events(tenant_id, "account.user_locked_out")
        assert len(events) == 1
        ctx = events[0]["context"]
        assert ctx["reason"] == "too_many_failed_logins"
        assert ctx["email"] == admin["email"] and ctx["attempts"] == 3
        assert ctx["severity"] == "warning" and ctx["kind"] == "account"
        assert events[0]["user_id"] == admin["id"]
        # The default period is written when none is set... by the admin route; a
        # row inserted by hand without one falls back to 15 minutes.
        row = _row(admin["id"])
        mins = (row["locked_until"] - datetime.now(timezone.utc)).total_seconds() / 60
        assert 14 < mins <= 15

    def test_a_correct_password_resets_the_run_of_failures(self, client, admin, tenant_id):
        _set_policy(tenant_id, lockout_threshold=3)
        _login(client, admin, "Wrong-pass-1")
        _login(client, admin, "Wrong-pass-1")
        assert _login(client, admin).status_code == 200
        assert _row(admin["id"])["failed_login_count"] == 0
        # Two more failures after the reset are not three in a row.
        _login(client, admin, "Wrong-pass-1")
        _login(client, admin, "Wrong-pass-1")
        assert _row(admin["id"])["locked_until"] is None
        assert _login(client, admin).status_code == 200

    def test_an_expired_lock_is_cleared_at_the_next_attempt(self, client, admin, tenant_id):
        _set_policy(tenant_id, lockout_threshold=3, lockout_minutes=10)
        execute("UPDATE users SET failed_login_count = 3, "
                "locked_until = NOW() - interval '1 minute' WHERE id = %s", (admin["id"],))
        assert _login(client, admin).status_code == 200
        row = _row(admin["id"])
        assert row["failed_login_count"] == 0 and row["locked_until"] is None

    def test_after_an_expired_lock_the_count_starts_again_from_zero(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, lockout_threshold=3, lockout_minutes=10)
        execute("UPDATE users SET failed_login_count = 3, "
                "locked_until = NOW() - interval '1 minute' WHERE id = %s", (admin["id"],))
        assert _login(client, admin, "Wrong-pass-1").status_code == 401
        row = _row(admin["id"])
        assert row["failed_login_count"] == 1 and row["locked_until"] is None

    def test_an_unknown_email_is_unaffected_and_still_the_generic_error(
        self, client, tenant_id,
    ):
        _set_policy(tenant_id, lockout_threshold=3)
        for _ in range(5):
            r = client.post("/api/v1/auth/login", json={
                "email": f"nobody-{uuid4().hex[:6]}@example.com", "password": "Whatever-1234"})
            assert r.status_code == 401 and r.json()["error_code"] == "invalid_credentials"

    def test_failures_in_one_tenant_never_lock_another_tenants_user(
        self, client, admin, tenant_id,
    ):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            _set_policy(other["id"], lockout_threshold=3)  # not the admin's tenant
            for _ in range(5):
                _login(client, admin, "Wrong-pass-1")
            assert _row(admin["id"])["failed_login_count"] == 0
            assert _login(client, admin).status_code == 200
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_a_password_reset_lifts_the_lock(self, client, admin, tenant_id):
        _set_policy(tenant_id, lockout_threshold=3)
        for _ in range(3):
            _login(client, admin, "Wrong-pass-1")
        assert _login(client, admin).status_code == 403
        token = create_signed_token({"sub": admin["id"], "tenant_id": tenant_id,
                                     "purpose": "password_reset"}, expires_minutes=15)
        assert client.post("/api/v1/auth/reset-password",
                           json={"token": token, "new_password": NEW_PASSWORD}).status_code == 200
        row = _row(admin["id"])
        assert row["failed_login_count"] == 0 and row["locked_until"] is None
        assert _login(client, admin, NEW_PASSWORD).status_code == 200

    def test_removing_the_lockout_setting_releases_a_locked_account(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, lockout_threshold=3)
        for _ in range(3):
            _login(client, admin, "Wrong-pass-1")
        assert _login(client, admin).status_code == 403
        _set_policy(tenant_id, lockout_threshold=None, lockout_minutes=None)
        assert _login(client, admin).status_code == 200

    def test_the_lock_is_checked_before_the_password_even_for_a_wrong_one(
        self, client, admin, tenant_id,
    ):
        _set_policy(tenant_id, lockout_threshold=3)
        for _ in range(3):
            _login(client, admin, "Wrong-pass-1")
        r = _login(client, admin, "Wrong-pass-1")
        assert r.status_code == 403 and r.json()["error_code"] == "account_locked"

    def test_an_atomic_threshold_under_concurrent_guesses(self, admin, tenant_id):
        """Each wrong password is one UPDATE ... +1, so the count never loses a
        guess to a read-modify-write race."""
        from concurrent.futures import ThreadPoolExecutor
        from backend.auth.session_policy import get_policy, record_failed_login
        _set_policy(tenant_id, lockout_threshold=20, lockout_minutes=5)
        policy = get_policy(tenant_id)
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda _: record_failed_login(tenant_id, admin["id"], policy),
                          range(16)))
        assert _row(admin["id"])["failed_login_count"] == 16
        assert _row(admin["id"])["locked_until"] is None


# ── the data itself ──────────────────────────────────────────────────────────

class TestPolicyTable:
    def test_a_value_outside_the_bounds_cannot_be_stored(self, tenant_id):
        import psycopg2
        for col, bad in [("max_session_hours", 0), ("max_session_hours", 169),
                         ("idle_timeout_minutes", 4), ("min_password_length", 7),
                         ("password_max_age_days", 6), ("max_concurrent_sessions", 0),
                         ("lockout_threshold", 2), ("lockout_minutes", 0)]:
            with pytest.raises(Exception) as exc:
                _set_policy(tenant_id, **{col: bad})
            assert "check" in str(exc.value).lower(), (col, bad)

    def test_erasing_the_tenant_removes_its_policy(self, test_tenant):
        _set_policy(test_tenant["id"], max_session_hours=8)
        execute("DELETE FROM tenants WHERE id = %s", (test_tenant["id"],))
        assert query_one("SELECT 1 AS x FROM tenant_session_policies WHERE tenant_id = %s",
                         (test_tenant["id"],)) is None

    def test_the_vocabulary_matches_the_event_registry(self):
        from backend.activity.events import EVENTS, REASONS
        from backend.audit.catalog import LEGACY
        for action in ("account.session_policy_changed", "account.user_locked_out",
                       "account.user_unlocked"):
            assert action in EVENTS and action in LEGACY
            assert EVENTS[action].severity == "warning"
        assert "too_many_failed_logins" in REASONS
