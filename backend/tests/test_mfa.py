"""Two-step sign-in, the Python half: the login challenge and its verification.

Enrollment, the recovery-code lifecycle and the tenant policy are Rust routes
(`backend-rs/src/routes/mfa/`); these tests seed what those routes write
straight into the shared tables, with the same primitives, and drive the
Python login against it. The fixed vectors in `backend-rs/test-vectors/mfa.json`
are read by the Rust unit tests too, which is what makes "Python and Rust agree
bit for bit" a checked fact and not a comment.
"""

import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest

from backend.auth import mfa
from backend.config import settings
from backend.db.connection import execute, query, query_one

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "backend-rs" / "test-vectors" / "mfa.json").read_text(encoding="utf-8")
)

LOGIN = "/api/v1/auth/login"
VERIFY = "/api/v1/auth/mfa/verify"


# ── Fixed vectors (shared with the Rust tests) ───────────────────────────────

class TestSharedVectors:
    def test_rfc6238_appendix_b(self):
        secret = VECTORS["rfc6238"]["secret_base32"]
        for case in VECTORS["rfc6238"]["cases"]:
            step = mfa.time_step(case["time"])
            assert mfa.hotp(secret, step, 8) == case["code8"], case
            assert mfa.hotp(secret, step, 6) == case["code8"][2:], case

    def test_rfc4226_appendix_d(self):
        secret = mfa.base64.b32encode(b"12345678901234567890").decode()
        expected = ["755224", "287082", "359152", "969429", "338314",
                    "254676", "287922", "162583", "399871", "520489"]
        assert [mfa.hotp(secret, i) for i in range(10)] == expected

    def test_window_is_one_step_either_side_and_no_more(self):
        w = VECTORS["window"]
        secret, now = w["secret_base32"], w["now"]
        for off in w["accepted_offsets"]:
            code = mfa.hotp(secret, mfa.time_step(now) + off)
            assert mfa.matching_step(secret, code, now=now) == mfa.time_step(now) + off
        for off in w["rejected_offsets"]:
            code = mfa.hotp(secret, mfa.time_step(now) + off)
            assert mfa.matching_step(secret, code, now=now) is None

    def test_a_step_at_or_before_the_last_used_one_never_matches(self):
        secret, now = VECTORS["window"]["secret_base32"], VECTORS["window"]["now"]
        s = mfa.time_step(now)
        assert mfa.matching_step(secret, mfa.hotp(secret, s), now=now, last_used_step=s) is None
        assert mfa.matching_step(secret, mfa.hotp(secret, s - 1), now=now, last_used_step=s) is None
        assert mfa.matching_step(secret, mfa.hotp(secret, s + 1), now=now, last_used_step=s) == s + 1

    def test_malformed_codes_never_match(self):
        secret, now = VECTORS["window"]["secret_base32"], VECTORS["window"]["now"]
        for bad in ["", "12345", "1234567", "12345a", "١٢٣٤٥٦", "      "]:
            assert mfa.matching_step(secret, bad, now=now) is None

    def test_recovery_hash_matches_the_rust_vectors(self, monkeypatch):
        monkeypatch.setattr(settings, "secret_key", VECTORS["recovery"]["secret_key"])
        for case in VECTORS["recovery"]["cases"]:
            assert mfa.recovery_code_hash(case["code"]) == case["hash"]
        assert mfa.recovery_code_hash("abcde-fghjk") == mfa.recovery_code_hash("ABCDE FGHJK")

    def test_a_fernet_token_has_the_shape_rust_writes_and_reads(self):
        from cryptography.fernet import Fernet
        f = Fernet(VECTORS["fernet"]["key"].encode())
        assert f.decrypt(VECTORS["fernet"]["token"].encode()).decode() == VECTORS["fernet"]["plaintext"]


# ── Helpers: what the Rust enrollment routes leave behind ────────────────────

def enroll(user_id: str, tenant_id: str, *, recovery=("ABCDE-FGHJK", "KMNPQ-RSTUV")) -> str:
    """An ACTIVE enrollment, written like `POST /mfa/enroll/confirm` writes it."""
    from backend.service_config.crypto import encrypt_value
    secret = mfa.generate_secret()
    execute(
        """INSERT INTO user_mfa (user_id, tenant_id, secret_enc, status, confirmed_at)
           VALUES (%s, %s, %s, 'active', NOW())""",
        (user_id, tenant_id, encrypt_value(secret)),
    )
    for code in recovery:
        execute(
            "INSERT INTO user_mfa_recovery_codes (user_id, tenant_id, code_hash) VALUES (%s, %s, %s)",
            (user_id, tenant_id, mfa.recovery_code_hash(code)),
        )
    return secret


def current_code(secret: str, offset: int = 0) -> str:
    return mfa.hotp(secret, mfa.time_step() + offset)


def login(client, registered):
    return client.post(LOGIN, json={"email": registered["email"], "password": registered["password"]})


def verify(client, token, code):
    return client.post(VERIFY, json={"mfa_token": token, "code": code})


def refresh_count(user_id: str) -> int:
    return query_one("SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = %s", (user_id,))["n"]


def row(sql, params):
    return query_one(sql, params)


@pytest.fixture
def enrolled(registered_user):
    secret = enroll(registered_user["user"]["id"], registered_user["tenant"]["id"])
    return {**registered_user, "secret": secret}


@pytest.fixture
def real_rate_limits(monkeypatch):
    """The local .env runs TESTING_MODE=true, which switches rate limiting off."""
    monkeypatch.setattr(settings, "testing_mode", False)
    yield
    execute("DELETE FROM auth_rate_events WHERE key LIKE 'mfa:%%' OR key LIKE 'login:%%'")


# ── Nothing changes for a user without MFA ───────────────────────────────────

class TestWithoutMfaLoginIsUnchanged:
    def test_no_enrollment_and_no_policy_issues_the_token_pair(self, client, registered_user):
        r = login(client, registered_user)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["access_token"] and data["refresh_token"]
        assert "mfa_required" not in data and "mfa_enrollment_required" not in data
        assert refresh_count(registered_user["user"]["id"]) == 1
        assert query_one("SELECT COUNT(*) AS n FROM mfa_challenges WHERE user_id = %s",
                         (registered_user["user"]["id"],))["n"] == 0

    def test_a_pending_enrollment_does_not_ask_for_a_code(self, client, registered_user):
        # begin without confirm: the secret exists but protects nothing yet
        execute(
            "INSERT INTO user_mfa (user_id, tenant_id, secret_enc, status) VALUES (%s, %s, 'x', 'pending')",
            (registered_user["user"]["id"], registered_user["tenant"]["id"]),
        )
        r = login(client, registered_user)
        assert r.status_code == 200 and "access_token" in r.json()["data"]


# ── The challenge flow ───────────────────────────────────────────────────────

class TestChallengeFlow:
    def test_password_ok_answers_with_a_challenge_and_no_session(self, client, enrolled):
        uid = enrolled["user"]["id"]
        before_login = row("SELECT last_login_at FROM users WHERE id = %s", (uid,))["last_login_at"]
        r = login(client, enrolled)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["mfa_required"] is True
        assert data["methods"] == ["totp", "recovery_code"]
        assert 0 < data["expires_in"] <= 300
        assert "access_token" not in data and "refresh_token" not in data
        # no session was created and the login was not stamped yet
        assert refresh_count(uid) == 0
        assert row("SELECT last_login_at FROM users WHERE id = %s", (uid,))["last_login_at"] == before_login
        # only the SHA-256 of the opaque token is stored
        stored = row("SELECT token_hash, purpose, attempts FROM mfa_challenges WHERE user_id = %s", (uid,))
        assert stored["token_hash"] == hashlib.sha256(data["mfa_token"].encode()).hexdigest()
        assert stored["token_hash"] != data["mfa_token"]
        assert (stored["purpose"], stored["attempts"]) == ("login", 0)

    def test_a_wrong_password_never_reaches_the_challenge(self, client, enrolled):
        r = client.post(LOGIN, json={"email": enrolled["email"], "password": "Wrong-Password-1"})
        assert r.status_code == 401 and r.json()["error_code"] == "invalid_credentials"
        assert query_one("SELECT COUNT(*) AS n FROM mfa_challenges WHERE user_id = %s",
                         (enrolled["user"]["id"],))["n"] == 0

    def test_a_good_code_issues_the_normal_token_pair(self, client, enrolled):
        uid = enrolled["user"]["id"]
        token = login(client, enrolled).json()["data"]["mfa_token"]
        r = verify(client, token, current_code(enrolled["secret"]))
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["token_type"] == "bearer" and data["expires_in"] == 900
        assert data["user"]["id"] == uid and data["user"]["email"] == enrolled["email"]
        # the access token is a real one
        me = client.get("/api/v1/entitlements", headers={"Authorization": f"Bearer {data['access_token']}"})
        assert me.status_code == 200
        # state: refresh token stored, login stamped, challenge consumed, step recorded
        assert refresh_count(uid) == 1
        assert row("SELECT last_login_at FROM users WHERE id = %s", (uid,))["last_login_at"] is not None
        assert row("SELECT consumed_at FROM mfa_challenges WHERE user_id = %s", (uid,))["consumed_at"] is not None
        assert row("SELECT last_used_step FROM user_mfa WHERE user_id = %s", (uid,))["last_used_step"] >= mfa.time_step() - 1
        # refresh-token rotation and the session cut are untouched by MFA
        assert row("SELECT sessions_invalid_before FROM users WHERE id = %s", (uid,))["sessions_invalid_before"] is None
        rr = client.post("/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert rr.status_code == 200 and rr.json()["data"]["access_token"]

    def test_a_challenge_is_single_use(self, client, enrolled):
        token = login(client, enrolled).json()["data"]["mfa_token"]
        assert verify(client, token, current_code(enrolled["secret"])).status_code == 200
        again = verify(client, token, current_code(enrolled["secret"], 1))
        assert again.status_code == 401 and again.json()["error_code"] == "mfa_challenge_invalid"
        assert refresh_count(enrolled["user"]["id"]) == 1

    def test_an_unknown_expired_or_foreign_purpose_token_is_refused(self, client, enrolled):
        uid, tid = enrolled["user"]["id"], enrolled["tenant"]["id"]
        code = current_code(enrolled["secret"])
        assert verify(client, "not-a-token", code).json()["error_code"] == "mfa_challenge_invalid"
        # expired
        raw, _ = mfa.create_challenge(uid, tid, mfa.PURPOSE_LOGIN)
        execute("UPDATE mfa_challenges SET expires_at = NOW() - INTERVAL '1 second' WHERE user_id = %s", (uid,))
        assert verify(client, raw, code).json()["error_code"] == "mfa_challenge_invalid"
        # an enrollment token is not a login challenge
        raw, _ = mfa.create_challenge(uid, tid, mfa.PURPOSE_ENROLL)
        r = verify(client, raw, code)
        assert r.status_code == 401 and r.json()["error_code"] == "mfa_challenge_invalid"
        assert refresh_count(uid) == 0

    def test_a_new_login_supersedes_the_open_challenge(self, client, enrolled):
        first = login(client, enrolled).json()["data"]["mfa_token"]
        second = login(client, enrolled).json()["data"]["mfa_token"]
        assert first != second
        r = verify(client, first, current_code(enrolled["secret"]))
        assert r.status_code == 401 and r.json()["error_code"] == "mfa_challenge_invalid"
        assert verify(client, second, current_code(enrolled["secret"])).status_code == 200

    def test_an_account_deactivated_between_password_and_code_gets_no_session(self, client, enrolled):
        uid = enrolled["user"]["id"]
        token = login(client, enrolled).json()["data"]["mfa_token"]
        execute("UPDATE users SET status = 'inactive' WHERE id = %s", (uid,))
        r = verify(client, token, current_code(enrolled["secret"]))
        assert r.status_code == 403 and r.json()["error_code"] == "account_not_active"
        assert refresh_count(uid) == 0

    def test_body_validation(self, client):
        assert client.post(VERIFY, json={}).status_code == 422
        assert client.post(VERIFY, json={"mfa_token": "t", "code": ""}).status_code == 422
        assert client.post(VERIFY, json={"mfa_token": "t", "code": "x" * 65}).status_code == 422


# ── Replay, guesses and throttling ───────────────────────────────────────────

class TestCodeChecks:
    def test_a_used_code_cannot_open_a_second_login(self, client, enrolled):
        uid, secret = enrolled["user"]["id"], enrolled["secret"]
        code = current_code(secret)
        t1 = login(client, enrolled).json()["data"]["mfa_token"]
        assert verify(client, t1, code).status_code == 200
        stored = row("SELECT last_used_step FROM user_mfa WHERE user_id = %s", (uid,))["last_used_step"]

        t2 = login(client, enrolled).json()["data"]["mfa_token"]
        replay = verify(client, t2, code)
        assert replay.status_code == 401 and replay.json()["error_code"] == "mfa_code_invalid"
        # the replay changed nothing: same step, no new session
        assert row("SELECT last_used_step FROM user_mfa WHERE user_id = %s", (uid,))["last_used_step"] == stored
        assert refresh_count(uid) == 1
        # an EARLIER step of the window is dead too
        assert verify(client, t2, current_code(secret, -1)).status_code == 401
        # the next step is fine (a fresh challenge: the previous ones spent guesses)
        t3 = login(client, enrolled).json()["data"]["mfa_token"]
        assert verify(client, t3, current_code(secret, 1)).status_code == 200
        assert row("SELECT last_used_step FROM user_mfa WHERE user_id = %s", (uid,))["last_used_step"] > stored

    def test_a_wrong_code_spends_a_guess_and_issues_nothing(self, client, enrolled):
        uid = enrolled["user"]["id"]
        token = login(client, enrolled).json()["data"]["mfa_token"]
        wrong = "000000" if current_code(enrolled["secret"]) != "000000" else "111111"
        r = verify(client, token, wrong)
        assert r.status_code == 401 and r.json()["error_code"] == "mfa_code_invalid"
        assert row("SELECT attempts, consumed_at FROM mfa_challenges WHERE user_id = %s", (uid,)) == {
            "attempts": 1, "consumed_at": None}
        assert refresh_count(uid) == 0

    def test_five_guesses_kill_the_challenge_even_for_the_right_code(self, client, enrolled):
        uid = enrolled["user"]["id"]
        token = login(client, enrolled).json()["data"]["mfa_token"]
        wrong = "000000" if current_code(enrolled["secret"]) != "000000" else "111111"
        for _ in range(mfa.CHALLENGE_MAX_ATTEMPTS):
            assert verify(client, token, wrong).json()["error_code"] == "mfa_code_invalid"
        r = verify(client, token, current_code(enrolled["secret"]))
        assert r.status_code == 401 and r.json()["error_code"] == "mfa_challenge_invalid"
        assert refresh_count(uid) == 0
        assert row("SELECT attempts FROM mfa_challenges WHERE user_id = %s", (uid,))["attempts"] == 5

    def test_attempts_are_counted_atomically_under_concurrency(self, enrolled):
        """The increment IS the check: N parallel claims of one challenge get
        at most CHALLENGE_MAX_ATTEMPTS successes between them."""
        from concurrent.futures import ThreadPoolExecutor
        raw, _ = mfa.create_challenge(enrolled["user"]["id"], enrolled["tenant"]["id"], mfa.PURPOSE_LOGIN)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: mfa.claim_attempt(raw, mfa.PURPOSE_LOGIN), range(12)))
        assert sum(1 for r in results if r) == mfa.CHALLENGE_MAX_ATTEMPTS

    def test_the_per_user_throttle_stops_guessing_across_challenges(self, client, enrolled, real_rate_limits):
        uid, tid = enrolled["user"]["id"], enrolled["tenant"]["id"]
        wrong = "000000" if current_code(enrolled["secret"]) != "000000" else "111111"
        for _ in range(10):
            raw, _ = mfa.create_challenge(uid, tid, mfa.PURPOSE_LOGIN)
            assert verify(client, raw, wrong).json()["error_code"] == "mfa_code_invalid"
        raw, _ = mfa.create_challenge(uid, tid, mfa.PURPOSE_LOGIN)
        r = verify(client, raw, current_code(enrolled["secret"]))
        assert r.status_code == 429 and r.json()["error_code"] == "too_many_attempts"
        assert query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (f"mfa:{uid}",))["n"] == 10
        assert refresh_count(uid) == 0


class TestRecoveryCodes:
    def test_a_recovery_code_logs_in_once(self, client, enrolled):
        uid = enrolled["user"]["id"]
        t1 = login(client, enrolled).json()["data"]["mfa_token"]
        r = verify(client, t1, "abcde fghjk")  # case and spacing are forgiven
        assert r.status_code == 200, r.text
        assert refresh_count(uid) == 1
        used = row("SELECT used_at FROM user_mfa_recovery_codes WHERE user_id = %s AND code_hash = %s",
                   (uid, mfa.recovery_code_hash("ABCDE-FGHJK")))
        assert used["used_at"] is not None
        # the other code is untouched
        assert mfa.recovery_codes_remaining(uid) == 1

        t2 = login(client, enrolled).json()["data"]["mfa_token"]
        again = verify(client, t2, "ABCDE-FGHJK")
        assert again.status_code == 401 and again.json()["error_code"] == "mfa_code_invalid"
        assert refresh_count(uid) == 1

    def test_using_one_is_a_warning_event_with_a_reason_and_no_code_in_it(self, client, enrolled):
        uid, tid = enrolled["user"]["id"], enrolled["tenant"]["id"]
        t = login(client, enrolled).json()["data"]["mfa_token"]
        assert verify(client, t, "KMNPQ-RSTUV").status_code == 200
        ev = query_one(
            "SELECT context FROM activity_logs WHERE tenant_id = %s AND user_id = %s AND action = %s",
            (tid, uid, "account.mfa_recovery_code_used"),
        )
        assert ev["context"]["severity"] == "warning"
        assert ev["context"]["reason"] == "recovery_code_used_to_sign_in"
        assert ev["context"]["remaining"] == 1
        assert "KMNPQ" not in json.dumps(ev["context"])

    def test_two_requests_racing_for_one_recovery_code_get_one_win(self, enrolled):
        from concurrent.futures import ThreadPoolExecutor
        uid = enrolled["user"]["id"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            res = list(pool.map(lambda _: mfa.verify_second_factor(uid, "ABCDE-FGHJK"), range(8)))
        assert res.count("recovery") == 1

    def test_two_requests_racing_for_one_totp_step_get_one_win(self, enrolled):
        from concurrent.futures import ThreadPoolExecutor
        uid = enrolled["user"]["id"]
        code = current_code(enrolled["secret"])
        with ThreadPoolExecutor(max_workers=8) as pool:
            res = list(pool.map(lambda _: mfa.verify_second_factor(uid, code), range(8)))
        assert res.count("totp") == 1


# ── Required by the tenant ───────────────────────────────────────────────────

class TestRequiredByTenant:
    def _require(self, tenant_id):
        execute("UPDATE tenants SET mfa_required = TRUE WHERE id = %s", (tenant_id,))

    def test_an_unenrolled_user_gets_an_enrollment_token_and_no_session(self, client, registered_user):
        uid, tid = registered_user["user"]["id"], registered_user["tenant"]["id"]
        self._require(tid)
        r = login(client, registered_user)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["mfa_enrollment_required"] is True and data["enrollment_token"]
        assert "access_token" not in data and "refresh_token" not in data
        assert refresh_count(uid) == 0
        stored = row("SELECT purpose, token_hash FROM mfa_challenges WHERE user_id = %s", (uid,))
        assert stored["purpose"] == "enroll"
        assert stored["token_hash"] == hashlib.sha256(data["enrollment_token"].encode()).hexdigest()
        # an enrollment token cannot be passed off as a login challenge
        v = verify(client, data["enrollment_token"], "123456")
        assert v.json()["error_code"] == "mfa_challenge_invalid"

    def test_an_enrolled_user_still_goes_through_the_normal_challenge(self, client, enrolled):
        self._require(enrolled["tenant"]["id"])
        data = login(client, enrolled).json()["data"]
        assert data["mfa_required"] is True and "mfa_enrollment_required" not in data
        assert verify(client, data["mfa_token"], current_code(enrolled["secret"])).status_code == 200

    def test_the_policy_is_per_tenant(self, client, registered_user):
        """Another tenant requiring MFA does not touch this one."""
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            self._require(other["id"])
            assert "access_token" in login(client, registered_user).json()["data"]
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


# ── What is deliberately exempt ──────────────────────────────────────────────

class TestExemptions:
    def test_social_and_enterprise_sign_ins_follow_the_providers_own_mfa(self, client, enrolled):
        """Documented and tested: an identity provider's sign-in is its own
        proof of the person, so neither an enrollment nor a tenant that
        REQUIRES MFA stops the exchange from issuing a session."""
        from backend.auth.social import flow
        execute("UPDATE tenants SET mfa_required = TRUE WHERE id = %s", (enrolled["tenant"]["id"],))
        for provider in ("google", "sso"):
            code = flow.issue_handoff(enrolled["user"], provider, False)
            r = client.post("/api/v1/auth/oauth/exchange", json={"code": code})
            assert r.status_code == 200, r.text
            data = r.json()["data"]
            assert data["access_token"] and data["refresh_token"]
            assert "mfa_required" not in data and "mfa_enrollment_required" not in data
        # and no challenge was minted on that path
        assert query_one("SELECT COUNT(*) AS n FROM mfa_challenges WHERE user_id = %s",
                         (enrolled["user"]["id"],))["n"] == 0

    def test_api_keys_are_unaffected(self, client, enrolled):
        from backend.auth.jwt_handler import create_access_token
        execute("UPDATE tenants SET mfa_required = TRUE WHERE id = %s", (enrolled["tenant"]["id"],))
        tok = create_access_token(enrolled["user"]["id"], enrolled["tenant"]["id"], "admin")
        made = client.post("/api/v1/api-keys", headers={"Authorization": f"Bearer {tok}"}, json={"name": "k"})
        assert made.status_code == 200, made.text
        key = made.json()["data"]["key"]
        r = client.get("/api/v1/entitlements", headers={"X-API-Key": key})
        assert r.status_code == 200


# ── Cleanup ──────────────────────────────────────────────────────────────────

class TestErasure:
    def test_deleting_a_user_removes_every_mfa_row(self, enrolled):
        from backend.users import service as user_svc
        uid, tid = enrolled["user"]["id"], enrolled["tenant"]["id"]
        mfa.create_challenge(uid, tid, mfa.PURPOSE_LOGIN)
        user_svc.delete_user(tid, uid)
        for table in ("user_mfa", "user_mfa_recovery_codes", "mfa_challenges"):
            assert query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = %s", (uid,))["n"] == 0

    def test_tenant_erasure_lists_the_mfa_tables_and_keeps_them_out_of_the_export(self):
        from backend.tenants import data_export
        for table in ("user_mfa", "user_mfa_recovery_codes", "mfa_challenges"):
            assert table in data_export._DELETE_ORDER
            assert table in data_export._OMITTED_FROM_EXPORT
        order = data_export._DELETE_ORDER
        assert order.index("user_mfa") < order.index("users")

    def test_erasing_a_tenant_leaves_no_mfa_rows(self, enrolled):
        from backend.tenants.data_export import delete_tenant
        uid, tid = enrolled["user"]["id"], enrolled["tenant"]["id"]
        mfa.create_challenge(uid, tid, mfa.PURPOSE_LOGIN)
        delete_tenant(tid)
        for table in ("user_mfa", "user_mfa_recovery_codes", "mfa_challenges"):
            assert query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id = %s", (tid,))["n"] == 0
