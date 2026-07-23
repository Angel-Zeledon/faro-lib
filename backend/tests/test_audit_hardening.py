"""
Tests for the 2026-07-04 security-audit fixes
(docs/auditoria_integral_faro_2026-07-04.md, findings #2, #3, #6).

Covers:
- DB-backed auth rate limiting (survives process restarts, shared across workers)
- Password-reset OTP: 15-minute expiry and burn-after-5-wrong-guesses
- Settings refuses TESTING_MODE=true in production
"""

import uuid

import pytest

from backend.config import settings
from backend.db.connection import execute, query_one


def _unique_email() -> str:
    return f"hardening-{uuid.uuid4().hex[:10]}@pytest.example.com"


class TestDbBackedRateLimit:
    def test_login_rate_limited_after_5_attempts_and_events_persisted(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(settings, "testing_mode", False)
        email = _unique_email()
        key = f"login:{email}"

        for i in range(5):
            resp = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Wrong-pass-123"},
            )
            assert resp.status_code == 401, f"attempt {i + 1} should reach auth check"

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Wrong-pass-123"},
        )
        assert resp.status_code == 429

        # The window must live in Postgres, not process memory: exactly the 5
        # allowed attempts were recorded (the blocked 6th is not).
        row = query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (key,))
        assert row["n"] == 5

        execute("DELETE FROM auth_rate_events WHERE key = %s", (key,))


class TestOtpHardening:
    def test_reset_otp_expires_in_15_minutes(self, client, registered_user, monkeypatch):
        monkeypatch.setattr(settings, "testing_mode", False)
        from backend.api.v1.auth import _issue_reset_otp

        entry = query_one(
            "SELECT id AS user_id, tenant_id FROM users WHERE email = %s",
            (registered_user["email"],),
        )
        _issue_reset_otp(entry["user_id"], entry["tenant_id"])

        row = query_one(
            """SELECT expires_at, NOW() AS db_now FROM pw_change_codes
               WHERE user_id = %s AND purpose = 'reset' AND used = FALSE
               ORDER BY created_at DESC LIMIT 1""",
            (entry["user_id"],),
        )
        remaining = (row["expires_at"] - row["db_now"]).total_seconds()
        assert 13 * 60 < remaining <= 15 * 60, (
            f"OTP lives {remaining / 60:.1f} min — expected ~15 min, not 30 hours"
        )
        execute("DELETE FROM pw_change_codes WHERE user_id = %s", (entry["user_id"],))

    def test_reset_otp_burned_after_5_wrong_guesses(self, client, registered_user, monkeypatch):
        monkeypatch.setattr(settings, "testing_mode", False)
        from backend.api.v1.auth import _issue_reset_otp

        entry = query_one(
            "SELECT id AS user_id, tenant_id FROM users WHERE email = %s",
            (registered_user["email"],),
        )
        code = _issue_reset_otp(entry["user_id"], entry["tenant_id"])
        wrong = "000000" if code != "000000" else "111111"

        for i in range(5):
            resp = client.post(
                "/api/v1/auth/forgot-password/verify",
                json={"email": registered_user["email"], "code": wrong},
            )
            assert resp.status_code == 400, f"wrong guess {i + 1} should be rejected"

        row = query_one(
            """SELECT attempts FROM pw_change_codes
               WHERE user_id = %s AND purpose = 'reset' AND used = FALSE
               ORDER BY created_at DESC LIMIT 1""",
            (entry["user_id"],),
        )
        assert row["attempts"] == 5

        # Even the CORRECT code must now be rejected — the code is burned.
        resp = client.post(
            "/api/v1/auth/forgot-password/verify",
            json={"email": registered_user["email"], "code": code},
        )
        assert resp.status_code == 400

        execute("DELETE FROM pw_change_codes WHERE user_id = %s", (entry["user_id"],))
        execute("DELETE FROM auth_rate_events WHERE key = %s",
                (f"otp:{registered_user['email'].lower()}",))

    def test_correct_code_still_works_within_attempt_budget(
        self, client, registered_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "testing_mode", False)
        from backend.api.v1.auth import _issue_reset_otp

        entry = query_one(
            "SELECT id AS user_id, tenant_id FROM users WHERE email = %s",
            (registered_user["email"],),
        )
        code = _issue_reset_otp(entry["user_id"], entry["tenant_id"])
        wrong = "000000" if code != "000000" else "111111"

        # 4 wrong guesses stay under the 5-attempt budget
        for _ in range(4):
            assert client.post(
                "/api/v1/auth/forgot-password/verify",
                json={"email": registered_user["email"], "code": wrong},
            ).status_code == 400

        resp = client.post(
            "/api/v1/auth/forgot-password/verify",
            json={"email": registered_user["email"], "code": code},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["reset_token"]

        execute("DELETE FROM pw_change_codes WHERE user_id = %s", (entry["user_id"],))
        execute("DELETE FROM auth_rate_events WHERE key = %s",
                (f"otp:{registered_user['email'].lower()}",))


class TestTestingModeGuard:
    @pytest.mark.offline
    def test_settings_refuse_testing_mode_in_production(self):
        from backend.config import Settings

        with pytest.raises(Exception, match="TESTING_MODE"):
            Settings(
                secret_key="x" * 32,
                database_url="postgresql://u:p@localhost/db",
                frontend_url="http://localhost:3000",
                testing_mode=True,
                environment="production",
            )

    @pytest.mark.offline
    def test_settings_allow_testing_mode_in_development(self):
        from backend.config import Settings

        s = Settings(
            secret_key="x" * 32,
            database_url="postgresql://u:p@localhost/db",
            frontend_url="http://localhost:3000",
            testing_mode=True,
            environment="development",
        )
        assert s.testing_mode is True
