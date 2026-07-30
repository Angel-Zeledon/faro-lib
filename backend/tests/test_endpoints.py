"""
Endpoint integration tests — auth flow, sessions CRUD, datasets CRUD, the
configuration wizard, users.

Rewritten against tests/README.md. The rule applied to every test here: the
response is not evidence. `data["name"] == "renamed"` is our own request body
coming back, and `"access_token" in data` is a key existing. So each test now
either reads the row back out of Postgres, or decodes the token it was given and
checks the claims against the row, or compares two responses that must be
indistinguishable.

Deleted rather than strengthened (all duplicated a stronger test elsewhere):
  test_logout_succeeds, test_token_revoked_after_logout   → test_edge_cases.py
      TestBadCredentialsChangeNothing (blocklist row + refused write)
  test_create_session_returns_201, test_viewer_cannot_create_session,
  test_upload_csv_returns_201, test_viewer_cannot_upload → test_edge_cases.py
      TestSessionAndDatasetPermissionPairs (permission pair + DB row)
  test_unauthenticated_request_returns_403_or_401        → test_edge_cases.py
      TestEveryRouteAuthenticates (the whole route table, not one route)
  test_forgot_password_returns_ok_regardless_of_email    → test_edge_cases.py
      TestForgotPasswordIsNotAnEnumerationOracle (no OTP row + identical body)
"""

import random
from uuid import uuid4

import pytest

from backend.db.connection import execute, query_one


def unique_phone() -> str:
    """A fresh E.164 number — signup requires one and rejects numbers already
    verified by another account (see TestSignupWhatsapp)."""
    return f"+506{random.SystemRandom().randint(10_000_000, 99_999_999)}"


def _email(prefix: str) -> str:
    """@faro-e2e.io has no MX record, so no test address can reach a person."""
    return f"{prefix}-{uuid4().hex[:8]}@faro-e2e.io"


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_reports_the_real_version_and_a_real_queue_count(
        self, client, test_tenant, configured_session,
    ):
        """`{"status": "ok"}` can be hardcoded; `queued_jobs` cannot. Queue a job
        and require the number to move, which is the only way this endpoint
        proves it still reaches the database it claims to be reporting on."""
        from backend.config import settings

        before = client.get("/health")
        assert before.status_code == 200
        body = before.json()
        assert body["status"] == "ok"
        assert body["version"] == settings.app_version
        baseline = body["queued_jobs"]

        from backend.training.job_service import create_job
        job = create_job(test_tenant["id"], configured_session["id"], "usr_test")
        try:
            after = client.get("/health").json()
            assert after["queued_jobs"] == baseline + 1, (
                "queued_jobs did not move after a job was queued — /health is not "
                "reading the jobs table"
            )
        finally:
            execute("DELETE FROM jobs WHERE id = %s", (job["id"],))


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestSignup:
    def test_signup_creates_user_and_tenant(self, client):
        email = _email("new")
        tenant_name = f"tenant-{uuid4().hex[:6]}"
        resp = client.post("/api/v1/auth/signup", json={
            "email": email,
            "password": "StrongPass123!",
            "tenant_name": tenant_name,
            "full_name": "Test User",
            "whatsapp_number": unique_phone(),
        })
        assert resp.status_code == 201
        tenant_id = resp.json()["data"]["tenant"]["id"]
        try:
            user = query_one(
                "SELECT tenant_id, role, full_name, email_verified, status, hashed_password "
                "FROM users WHERE email = %s",
                (email,),
            )
            assert user is not None, "201 returned but no user row exists"
            assert user["tenant_id"] == tenant_id
            # The first user of a new tenant owns it.
            assert user["role"] == "admin"
            assert user["full_name"] == "Test User"
            # Verification has not happened yet — signup must not pre-verify.
            assert user["email_verified"] is False
            # The password is hashed, not stored.
            assert user["hashed_password"] != "StrongPass123!"
            assert user["hashed_password"].startswith("$2"), "not a bcrypt hash"

            tenant = query_one("SELECT name, plan FROM tenants WHERE id = %s", (tenant_id,))
            assert tenant is not None and tenant["name"] == tenant_name
            assert tenant["plan"], "a new tenant was created with no plan"
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))

    def test_signup_duplicate_email_returns_409_and_creates_no_second_tenant(
        self, client, registered_user,
    ):
        tenant_name = f"other-tenant-{uuid4().hex[:6]}"
        resp = client.post("/api/v1/auth/signup", json={
            "email": registered_user["email"],
            "password": "StrongPass123!",
            "tenant_name": tenant_name,
            "whatsapp_number": unique_phone(),
        })
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "email_already_registered"
        # The tenant row is created BEFORE the user, so a rejected signup that
        # forgets to roll back strands a tenant nobody can ever log into.
        assert query_one(
            "SELECT id FROM tenants WHERE name = %s", (tenant_name,),
        ) is None, "the duplicate signup left an orphaned tenant behind"
        assert query_one(
            "SELECT COUNT(*) AS n FROM users WHERE email = %s", (registered_user["email"],),
        )["n"] == 1

    @pytest.mark.parametrize("payload_patch,expected_status,label", [
        ({"password": "abc"}, 400, "weak password"),
        ({"email": "not-an-email"}, 422, "malformed email"),
        ({"whatsapp_number": "88887777"}, 422, "phone without country code"),
        ({"password": "Aa1" * 30}, 400, "password over bcrypt's 72-byte limit"),
    ])
    def test_rejected_signup_leaves_no_user_and_no_orphan_tenant(
        self, payload_patch, expected_status, label, client,
    ):
        """
        The 72-byte case is a regression: bcrypt hard-rejects longer passwords
        (ValueError: "password cannot be longer than 72 bytes") and
        validate_strength() had no upper bound, so a long-but-strong password
        sailed past validation and crashed hash_password() with a 500.

        All four share the assertion that matters — signup either happens
        completely or not at all. The tenant is inserted before the user, so a
        half-done signup is visible as a tenant with no users.
        """
        email = _email("rejected")
        tenant_name = f"tenant-{uuid4().hex[:6]}"
        body = {
            "email": email,
            "password": "StrongPass123!",
            "tenant_name": tenant_name,
            "whatsapp_number": unique_phone(),
            **payload_patch,
        }
        resp = client.post("/api/v1/auth/signup", json=body)
        assert resp.status_code == expected_status, f"{label}: {resp.text}"

        assert query_one(
            "SELECT id FROM users WHERE email = %s", (body["email"],),
        ) is None, f"{label} was rejected and a user row was created anyway"
        assert query_one(
            "SELECT id FROM tenants WHERE name = %s", (tenant_name,),
        ) is None, f"{label} was rejected and left an orphaned tenant"


class TestSignupWhatsapp:
    """PENDIENTES #1: the buyer's number is collected at signup so purchase
    orders can be delivered to them for forwarding."""

    def test_number_is_persisted_unverified(self, client):
        email = _email("wa")
        phone = unique_phone()
        resp = client.post("/api/v1/auth/signup", json={
            "email": email,
            "password": "StrongPass123!",
            "tenant_name": f"tenant-{uuid4().hex[:6]}",
            "whatsapp_number": phone,
        })
        assert resp.status_code == 201, resp.text
        row = query_one(
            "SELECT whatsapp_number, whatsapp_verified_at FROM users WHERE email = %s",
            (email,))
        assert row["whatsapp_number"] == phone
        # Collected, but the inbound bot still requires explicit verification.
        assert row["whatsapp_verified_at"] is None
        execute("DELETE FROM tenants WHERE id = %s", (resp.json()["data"]["tenant"]["id"],))

    def test_missing_number_is_rejected_and_no_user_created(self, client):
        email = _email("nophone")
        resp = client.post("/api/v1/auth/signup", json={
            "email": email,
            "password": "StrongPass123!",
            "tenant_name": f"tenant-{uuid4().hex[:6]}",
        })
        assert resp.status_code == 422
        assert query_one("SELECT id FROM users WHERE email = %s", (email,)) is None

    def test_number_verified_by_another_account_is_rejected(self, client, test_tenant):
        from backend.users import service as user_svc

        phone = unique_phone()
        # Somebody already proved ownership of this number.
        owner = user_svc.create_user(
            tenant_id=test_tenant["id"],
            email=_email("owner"),
            password="StrongPass123!",
            whatsapp_number=phone,
        )
        execute(
            "UPDATE users SET whatsapp_verified_at = NOW() WHERE id = %s",
            (owner["id"],))

        email = _email("claim")
        resp = client.post("/api/v1/auth/signup", json={
            "email": email,
            "password": "StrongPass123!",
            "tenant_name": f"tenant-{uuid4().hex[:6]}",
            "whatsapp_number": phone,
        })
        assert resp.status_code == 409, resp.text
        assert resp.json().get("error_code") == "whatsapp_number_taken"
        assert query_one("SELECT id FROM users WHERE email = %s", (email,)) is None
        # The number still belongs to whoever proved it.
        assert query_one(
            "SELECT id FROM users WHERE whatsapp_number = %s", (phone,),
        )["id"] == owner["id"]


class TestLogin:
    def test_login_issues_tokens_that_match_the_stored_user(self, client, registered_user):
        """`"access_token" in data` says a key exists. Decode it instead and
        check every claim against the row, then check the refresh token was
        actually stored (hashed) so it can be revoked later."""
        from backend.auth.jwt_handler import decode_token, hash_token

        user_id = registered_user["user"]["id"]
        before = query_one("SELECT last_login_at FROM users WHERE id = %s", (user_id,))["last_login_at"]

        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["token_type"] == "bearer"

        claims = decode_token(data["access_token"])
        row = query_one("SELECT tenant_id, role FROM users WHERE id = %s", (user_id,))
        assert claims["sub"] == user_id
        assert claims["tenant_id"] == row["tenant_id"]
        assert claims["role"] == row["role"]
        assert claims["type"] == "access"
        assert claims["jti"], "no jti — the token could never be revoked"

        # The refresh token is stored only as a hash, and it is the one we got.
        stored = query_one(
            "SELECT user_id FROM refresh_tokens WHERE hash = %s",
            (hash_token(data["refresh_token"]),),
        )
        assert stored is not None, "the refresh token was handed out but never stored"
        assert stored["user_id"] == user_id
        assert query_one(
            "SELECT COUNT(*) AS n FROM refresh_tokens WHERE hash = %s",
            (data["refresh_token"],),
        )["n"] == 0, "the refresh token is stored in the clear"

        after = query_one("SELECT last_login_at FROM users WHERE id = %s", (user_id,))["last_login_at"]
        assert after is not None and after != before, "last_login_at was not updated"

    def test_wrong_password_is_refused_and_leaves_no_session(self, client, registered_user):
        user_id = registered_user["user"]["id"]
        resp = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": "wrongpassword",
        })
        assert resp.status_code == 401
        assert query_one(
            "SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = %s", (user_id,),
        )["n"] == 0, "a failed login minted a refresh token"
        assert query_one(
            "SELECT last_login_at FROM users WHERE id = %s", (user_id,),
        )["last_login_at"] is None, "a failed login was recorded as a login"

    def test_unknown_email_is_indistinguishable_from_a_wrong_password(
        self, client, registered_user,
    ):
        """Distinguishing them would make login an account-enumeration oracle."""
        unknown = client.post("/api/v1/auth/login", json={
            "email": _email("nobody"), "password": "AnyPass123!",
        })
        wrong = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"], "password": "AnyPass123!",
        })
        assert unknown.status_code == wrong.status_code == 401

        def _payload(resp):
            body = dict(resp.json())
            body.pop("meta", None)
            return body

        assert _payload(unknown) == _payload(wrong), (
            "the login error differs for an unknown address — it can be used to "
            "enumerate accounts"
        )

    def test_login_unverified_user_is_let_in_but_flagged(self, client, test_tenant):
        """Unverified is no longer a locked door.

        It used to 403, which left anyone whose verification mail hit spam
        permanently outside with no self-service way back and nothing of the
        product seen. They now get in with `email_verified: false` on the token,
        and only outward-facing actions (inviting users, integrations, sending
        notifications) are refused — see test_email_verification_unblock.py for
        those, including the pair that proves an unverified admin cannot invite.
        """
        from backend.auth.jwt_handler import decode_token
        from backend.users import service as user_svc
        email = _email("unverified")
        user = user_svc.create_user(
            tenant_id=test_tenant["id"],
            email=email,
            password="TestPass123!",
            role="analyst",
        )
        assert query_one(
            "SELECT email_verified FROM users WHERE id = %s", (user["id"],),
        )["email_verified"] is False

        resp = client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "TestPass123!",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["user"]["email_verified"] is False
        # The claim travels on the token, which is what the guards read.
        assert decode_token(resp.json()["data"]["access_token"])["email_verified"] is False


class TestRefresh:
    def test_refresh_returns_a_token_that_actually_works(self, client, registered_user):
        from backend.auth.jwt_handler import decode_token

        login = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        refresh_token = login.json()["data"]["refresh_token"]

        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        new_token = resp.json()["data"]["access_token"]
        claims = decode_token(new_token)
        assert claims["sub"] == registered_user["user"]["id"]
        assert claims["type"] == "access"
        assert claims["jti"] != decode_token(login.json()["data"]["access_token"])["jti"], (
            "refresh handed back the same jti, so revoking one revokes both"
        )
        # A token you cannot use is not a token.
        assert client.get(
            "/api/v1/sessions", headers={"Authorization": f"Bearer {new_token}"},
        ).status_code == 200

    def test_refresh_invalid_token_returns_401_and_no_token(self, client):
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "invalid_token"})
        assert resp.status_code == 401
        assert "access_token" not in resp.text, "a rejected refresh still leaked a token"


# ── Sessions CRUD ─────────────────────────────────────────────────────────────

class TestSessionsCRUD:
    def test_create_session_persists_its_tags(self, client, auth_headers):
        resp = client.post("/api/v1/sessions", json={
            "name": f"tagged-{uuid4().hex[:4]}",
            "tags": ["prod", "weekly"],
        }, headers=auth_headers)
        assert resp.status_code == 201
        row = query_one("SELECT tags FROM sessions WHERE id = %s", (resp.json()["data"]["id"],))
        assert row["tags"] == ["prod", "weekly"], (
            f"tags were accepted but stored as {row['tags']!r}"
        )

    def test_list_sessions_returns_created(self, client, auth_headers, test_session, test_tenant):
        resp = client.get("/api/v1/sessions", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()["data"]
        listed = {s["id"]: s for s in body["items"]}
        assert test_session["id"] in listed
        assert listed[test_session["id"]]["name"] == test_session["name"]
        # `total` is a separate COUNT query, so it can disagree with the page.
        assert body["total"] == query_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE tenant_id = %s", (test_tenant["id"],),
        )["n"]

    def test_get_session_returns_the_stored_row(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        row = query_one(
            "SELECT name, description, status, pipeline_step, created_by FROM sessions WHERE id = %s",
            (sid,),
        )
        for field in ("name", "description", "status", "pipeline_step", "created_by"):
            assert data[field] == row[field], f"{field} differs from the stored row"

    @pytest.mark.parametrize("path,code", [
        ("/api/v1/sessions/sess_nonexistent", "session_not_found"),
        ("/api/v1/datasets/ds_nonexistent", "dataset_not_found"),
    ])
    def test_unknown_id_is_a_404_with_a_stable_code(self, path, code, client, auth_headers):
        """The frontend renders the Spanish from `error_code`, so the code is
        part of the contract — a 404 whose code changed would silently degrade
        to the English fallback in the UI."""
        resp = client.get(path, headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["error_code"] == code

    def test_update_session_name_writes_the_row(self, client, auth_headers, test_session):
        sid = test_session["id"]
        new_name = f"renamed-{uuid4().hex[:6]}"
        resp = client.patch(
            f"/api/v1/sessions/{sid}",
            json={"name": new_name},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        row = query_one("SELECT name, updated_at, created_at FROM sessions WHERE id = %s", (sid,))
        assert row["name"] == new_name, "the response echoed the new name but the row kept the old one"
        assert row["updated_at"] >= row["created_at"]

    def test_delete_session_removes_the_row_and_its_config(self, client, auth_headers):
        create = client.post("/api/v1/sessions", json={"name": "to-delete"}, headers=auth_headers)
        sid = create.json()["data"]["id"]
        # create_session also inserts the session_configs row; both must go.
        assert query_one("SELECT session_id FROM session_configs WHERE session_id = %s", (sid,)) is not None

        resp = client.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert resp.status_code == 204
        assert query_one("SELECT id FROM sessions WHERE id = %s", (sid,)) is None, (
            "204 returned but the session row is still there"
        )
        assert query_one(
            "SELECT session_id FROM session_configs WHERE session_id = %s", (sid,),
        ) is None, "the session was deleted but its config blob was orphaned"

    def test_pagination_pages_are_disjoint_and_complete(self, client, auth_headers, test_tenant):
        created = []
        for i in range(5):
            r = client.post(
                "/api/v1/sessions", json={"name": f"page-{i}-{uuid4().hex[:4]}"},
                headers=auth_headers,
            )
            created.append(r.json()["data"]["id"])

        total_in_db = query_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE tenant_id = %s", (test_tenant["id"],),
        )["n"]

        page1 = client.get("/api/v1/sessions?skip=0&limit=2", headers=auth_headers).json()["data"]
        page2 = client.get("/api/v1/sessions?skip=2&limit=2", headers=auth_headers).json()["data"]
        assert len(page1["items"]) == 2, f"limit=2 returned {len(page1['items'])} items"
        assert len(page2["items"]) == 2
        assert page1["total"] == page2["total"] == total_in_db

        ids1 = [s["id"] for s in page1["items"]]
        ids2 = [s["id"] for s in page2["items"]]
        assert not set(ids1) & set(ids2), (
            f"page 1 and page 2 overlap ({set(ids1) & set(ids2)}) — skip is ignored"
        )

        # Walking every page must reach every row exactly once.
        seen = []
        for skip in range(0, total_in_db, 2):
            seen += [
                s["id"] for s in
                client.get(f"/api/v1/sessions?skip={skip}&limit=2", headers=auth_headers)
                .json()["data"]["items"]
            ]
        assert len(seen) == len(set(seen)) == total_in_db, (
            f"paging through {total_in_db} rows yielded {len(seen)} ids "
            f"({len(set(seen))} distinct) — rows are duplicated or skipped"
        )
        assert set(created) <= set(seen)


# ── Datasets CRUD ─────────────────────────────────────────────────────────────

class TestDatasetsCRUD:
    def test_list_datasets_includes_upload(self, client, auth_headers, uploaded_dataset, test_tenant):
        resp = client.get("/api/v1/datasets", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert uploaded_dataset["id"] in [d["id"] for d in body["items"]]
        assert body["total"] == query_one(
            "SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s "
            "AND COALESCE(source_type, 'file') != 'sql'",
            (test_tenant["id"],),
        )["n"]

    def test_get_dataset_matches_the_row_and_hides_credentials(
        self, client, auth_headers, uploaded_dataset,
    ):
        did = uploaded_dataset["id"]
        # A SQL source stores an encrypted connection string here; it must never
        # be serialized, whatever the row holds.
        execute(
            "UPDATE datasets SET sql_config = %s WHERE id = %s",
            ('{"dsn": "postgresql://user:SECRETPASSWORD@host/db"}', did),
        )
        resp = client.get(f"/api/v1/datasets/{did}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        row = query_one(
            "SELECT original_filename, file_type, size_bytes FROM datasets WHERE id = %s", (did,),
        )
        assert data["original_filename"] == row["original_filename"]
        assert data["file_type"] == row["file_type"]
        assert data["size_bytes"] == row["size_bytes"]
        assert "sql_config" not in data
        assert "SECRETPASSWORD" not in resp.text, "connection credentials reached the client"

    def test_upload_unsupported_extension_stores_nothing(
        self, client, auth_headers, test_tenant,
    ):
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("data.txt", b"col1,col2\n1,2", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert query_one(
            "SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s", (test_tenant["id"],),
        )["n"] == 0, "a rejected file type was still registered as a dataset"
        # And nothing was written to disk under the tenant either.
        from backend.storage import paths
        tenant_dir = paths.dataset_dir(test_tenant["id"], "")
        assert not tenant_dir.exists() or not any(tenant_dir.iterdir()), (
            f"a rejected upload left files in {tenant_dir}"
        )


# ── Configuration wizard ──────────────────────────────────────────────────────

class TestConfigurationWizard:
    def test_attach_dataset_writes_the_link_and_advances_the_session(
        self, client, auth_headers, test_session, uploaded_dataset,
    ):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": uploaded_dataset["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        row = query_one("SELECT dataset_id, status, pipeline_step FROM sessions WHERE id = %s", (sid,))
        assert row["dataset_id"] == uploaded_dataset["id"], (
            "the response echoed the dataset id but sessions.dataset_id was not set"
        )
        # The wizard's state machine has to move, or the next step 409s.
        assert row["status"] == "DATASET_LOADED"
        assert row["pipeline_step"] == "inspect"

    def test_attach_nonexistent_dataset_leaves_the_session_alone(
        self, client, auth_headers, test_session,
    ):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": f"ds_{uuid4().hex[:12]}"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "dataset_not_found"
        row = query_one("SELECT dataset_id, status FROM sessions WHERE id = %s", (sid,))
        assert row["dataset_id"] is None, "a 404 attach still wrote sessions.dataset_id"
        assert row["status"] == "DRAFT"

    def test_wizard_steps_are_persisted_exactly_as_configured(
        self, client, auth_headers, configured_session,
    ):
        """One test for the three config blobs the `configured_session` fixture
        writes. The old versions asserted `"lags" in cfg` and
        `"selected_models" in cfg` — a key existing says nothing about whether
        the user's choice survived, and the runner reads these values verbatim."""
        sid = configured_session["id"]
        row = query_one(
            "SELECT columns_cfg, features_cfg, models_cfg FROM session_configs WHERE session_id = %s",
            (sid,),
        )
        assert row is not None, "the wizard ran but session_configs has no row"

        assert row["columns_cfg"]["date_column"] == "date"
        assert row["columns_cfg"]["target_column"] == "sales"
        assert row["columns_cfg"]["sku_column"] == "sku"

        assert row["features_cfg"]["lags"] == [1, 7]
        assert row["features_cfg"]["rolling"] == [7]
        assert row["features_cfg"]["diffs"] == [1]
        assert row["features_cfg"]["calendar"] is True

        assert row["models_cfg"]["selected_models"] == ["lightgbm"]
        assert row["models_cfg"]["mode"] == "selected"

        # And the GET endpoints must serve the same values, not a default.
        for step, expected in (
            ("columns", {"date_column": "date", "target_column": "sales"}),
            ("features", {"lags": [1, 7], "rolling": [7]}),
            ("models", {"selected_models": ["lightgbm"]}),
        ):
            got = client.get(
                f"/api/v1/sessions/{sid}/configure/{step}", headers=auth_headers,
            ).json()["data"]
            for k, v in expected.items():
                assert got[k] == v, f"GET configure/{step} returned {k}={got[k]!r}"

    def test_session_reaches_models_configured(self, client, auth_headers, configured_session):
        """Pinned to the one status the fixture is documented to produce. The
        old version accepted any of five, so a wizard that stalled at
        DATASET_LOADED — which makes training 409 — passed."""
        sid = configured_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
        assert resp.json()["data"]["status"] == "MODELS_CONFIGURED"
        assert query_one("SELECT status FROM sessions WHERE id = %s", (sid,))["status"] \
            == "MODELS_CONFIGURED"

    def test_config_summary_serves_the_stored_blobs(self, client, auth_headers, configured_session):
        sid = configured_session["id"]
        resp = client.get(f"/api/v1/sessions/{sid}/config-summary", headers=auth_headers)
        assert resp.status_code == 200
        cfg = resp.json()["data"]
        row = query_one(
            "SELECT columns_cfg, features_cfg, models_cfg FROM session_configs WHERE session_id = %s",
            (sid,),
        )
        # `is not None` on each key was the whole old test; compare the values.
        assert cfg["columns"]["target_column"] == row["columns_cfg"]["target_column"] == "sales"
        assert cfg["features"]["lags"] == row["features_cfg"]["lags"] == [1, 7]
        assert cfg["models"]["selected_models"] == row["models_cfg"]["selected_models"] == ["lightgbm"]

    def test_upload_and_attach_does_both_halves(self, client, auth_headers, test_session, csv_bytes):
        """The route's whole reason to exist is that it attaches as well as
        uploads, and the attach half was asserted nowhere."""
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/upload",
            files={"file": ("sales2.csv", csv_bytes, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        dataset_id = resp.json()["data"]["id"]

        ds = query_one(
            "SELECT original_filename, size_bytes FROM datasets WHERE id = %s", (dataset_id,),
        )
        assert ds is not None and ds["original_filename"] == "sales2.csv"
        assert ds["size_bytes"] == len(csv_bytes)

        row = query_one("SELECT dataset_id, status FROM sessions WHERE id = %s", (sid,))
        assert row["dataset_id"] == dataset_id, "uploaded but never attached to the session"
        assert row["status"] == "DATASET_LOADED"

    def test_business_config_is_persisted(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"service_level": 0.98, "lead_time_days": 5},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        row = query_one("SELECT business_cfg FROM session_configs WHERE session_id = %s", (sid,))
        assert row["business_cfg"]["service_level"] == 0.98
        assert row["business_cfg"]["lead_time_days"] == 5

    def test_business_config_rejects_stockout_multiplier_below_one(self, client, auth_headers, test_session):
        """
        The MILP optimizer derives stockout_cost = order_cost * multiplier
        (backend/inventory/optimizer_service.py). A multiplier < 1 makes
        stockout_cost < order_cost, at which point the solver finds it
        cheaper to leave demand permanently unmet than to ever order —
        silently zeroing out every recommendation. Reject it at the API
        boundary instead of letting it through to configure a broken plan.
        """
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/config/business",
            json={"stockout_cost_multiplier": 0.5},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        row = query_one("SELECT business_cfg FROM session_configs WHERE session_id = %s", (sid,))
        assert row is None or row["business_cfg"] is None, (
            "the 422 was returned and the broken multiplier was stored anyway"
        )

    def test_forecast_config_is_persisted(self, client, auth_headers, test_session):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/config/forecast",
            json={"horizon": 21, "quantiles": [0.1, 0.9]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        row = query_one("SELECT forecast_cfg FROM session_configs WHERE session_id = %s", (sid,))
        assert row["forecast_cfg"]["horizon"] == 21
        assert row["forecast_cfg"]["quantiles"] == [0.1, 0.9]


# ── User management ───────────────────────────────────────────────────────────

class TestUsers:
    def test_list_users_is_scoped_to_the_callers_tenant(
        self, client, auth_headers, registered_user,
    ):
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc

        other = create_tenant(f"other-{uuid4().hex[:8]}")
        other_email = _email("outsider")
        try:
            user_svc.create_user(other["id"], other_email, "TestPass123!", "admin")
            resp = client.get("/api/v1/users", headers=auth_headers)
            assert resp.status_code == 200
            emails = [u["email"] for u in resp.json()["data"]["items"]]
            assert registered_user["email"] in emails
            assert other_email not in emails, "the user list leaks another tenant's users"
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_password_hash_never_appears_in_the_response(self, client, auth_headers, registered_user):
        """Checking `"hashed_password" not in u` only covers that one key name.
        Take the real hash out of the database and search the whole serialized
        response for it, so a rename or a nested echo fails too."""
        real_hash = query_one(
            "SELECT hashed_password FROM users WHERE id = %s", (registered_user["user"]["id"],),
        )["hashed_password"]
        assert real_hash and real_hash.startswith("$2"), "no hash to look for — test is vacuous"

        resp = client.get("/api/v1/users", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        assert real_hash not in resp.text, "the stored password hash was serialized to the client"
        for u in items:
            assert "hashed_password" not in u
