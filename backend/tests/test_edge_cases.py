"""
Edge cases, authorization boundaries and validation — rewritten against
tests/README.md.

Every test in this file has to be able to go red. In practice that means:

  * A refusal is asserted together with the state that did NOT change. A 403 on
    an endpoint that answers 403 *and* writes the row is a passing bug, so the
    second assertion is the test.
  * A success is asserted against the database row, never against the response
    echo of the request body — the echo was our own input.
  * The two blanket properties ("nothing is reachable without a token", "the
    excuse list cannot rot") are audits over the app's own route table, so a
    router mounted tomorrow is covered the moment it exists. That replaces the
    old per-request 401/403 checks, which each proved one route was mounted.
"""

from uuid import uuid4

import jwt
import pytest

from backend.db.connection import query_one


# ═══════════════════════════════════════════════════════════════════════════════
# Audit — no route is reachable without a token
#
# This replaces test_missing_auth_header_returns_403 /
# test_wrong_token_type_prefix_returns_401 / test_endpoints.py's
# test_unauthenticated_request_returns_403_or_401. Those hit ONE route with no
# credential and asserted a status in (401, 403); they could not notice a new
# router mounted without `get_current_user`, which is the failure that actually
# costs something. The audit walks all ~257 routes instead.
# ═══════════════════════════════════════════════════════════════════════════════

# Keyed exactly as FastAPI reports the route. The value is why it is open.
UNAUTHENTICATED = {
    # You cannot be authenticated before you authenticate.
    "POST /api/v1/auth/signup": "creates the account and its tenant",
    "POST /api/v1/auth/login": "issues the credential",
    "POST /api/v1/auth/refresh": "the refresh token IS the credential",
    "POST /api/v1/auth/logout": "revoking your own session needs no role",
    "POST /api/v1/auth/verify-email": "the emailed token is the credential",
    "POST /api/v1/auth/resend-verification": "you cannot verify without it",
    "POST /api/v1/auth/forgot-password": "you are locked out by definition",
    "POST /api/v1/auth/forgot-password/verify": "same flow, still locked out",
    "POST /api/v1/auth/reset-password": "the emailed token is the credential",
    # Machine callers authorised by request signature, not by a user token.
    "POST /api/v1/billing/webhook": "Stripe webhook, verified by signature",
    "POST /api/v1/whatsapp/inbound": "Twilio webhook, verified by signature",
    # Deliberate: Twilio's MediaUrl fetch cannot carry a Bearer token, and the
    # id is unguessable. See the docstring on the route itself.
    "GET /api/v1/inventory/po/{po_log_id}/pdf/{supplier_slug}": (
        "WhatsApp media fetch cannot send a Bearer token"
    ),
    # Static catalogues and liveness — no tenant data.
    "GET /api/v1/models": "static catalogue of available model types",
    "GET /health": "liveness probe",
}


def _dependency_calls(dependant) -> list:
    """Every dependency function reachable from a route, at any depth."""
    out = []
    stack = list(getattr(dependant, "dependencies", []) or [])
    while stack:
        d = stack.pop()
        if getattr(d, "call", None) is not None:
            out.append(d.call)
        stack.extend(getattr(d, "dependencies", []) or [])
    return out


def _all_routes(fastapi_app):
    for route in fastapi_app.routes:
        if not hasattr(route, "dependant") or not getattr(route, "methods", None):
            continue
        for method in sorted(route.methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            yield f"{method} {route.path}", route


def _authenticates(route) -> bool:
    from backend.auth import guards as g
    return g.get_current_user in _dependency_calls(route.dependant)


class TestEveryRouteAuthenticates:
    def test_no_route_reads_tenant_data_without_a_token(self, app):
        open_routes = [
            rid for rid, route in _all_routes(app)
            if rid not in UNAUTHENTICATED and not _authenticates(route)
        ]
        assert not open_routes, (
            "These routes do not depend on get_current_user, so anyone on the "
            "internet can call them. Add the guard, or add the path to "
            "UNAUTHENTICATED in this file with a reason:\n  "
            + "\n  ".join(sorted(open_routes))
        )

    def test_the_walk_actually_sees_routes(self, app):
        """A silent zero would make the assertion above pass vacuously."""
        found = list(_all_routes(app))
        assert len(found) > 200, (
            f"only {len(found)} routes discovered — the walk is probably broken"
        )

    def test_every_open_path_still_exists(self, app):
        live = {rid for rid, _ in _all_routes(app)}
        stale = [rid for rid in UNAUTHENTICATED if rid not in live]
        assert not stale, (
            "These paths are excused from authentication but no longer exist. "
            "Delete them so the exception list keeps meaning something:\n  "
            + "\n  ".join(sorted(stale))
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Bad credentials must change nothing
#
# Six former tests (missing header, malformed JWT, wrong scheme, refresh token
# used as access token, expired token, revoked token) asserted only a status
# code. Each now attempts a real WRITE and the tenant's session table is read
# back: a guard that answers 401 and creates the row anyway fails here.
# ═══════════════════════════════════════════════════════════════════════════════

class TestBadCredentialsChangeNothing:

    def _headers_for(self, kind, client, registered_user):
        from datetime import datetime, timedelta, timezone
        from backend.config import settings

        if kind == "no_header":
            return {}
        if kind == "malformed_jwt":
            return {"Authorization": "Bearer not.a.jwt"}
        if kind == "wrong_scheme":
            return {"Authorization": "Token sometoken"}
        if kind == "opaque_refresh_token":
            # The raw refresh token is not a JWT at all (secrets.token_urlsafe),
            # so it can never decode — this pins that it is not silently
            # interchangeable with an access token.
            login = client.post("/api/v1/auth/login", json={
                "email": registered_user["email"],
                "password": registered_user["password"],
            })
            return {"Authorization": f"Bearer {login.json()['data']['refresh_token']}"}
        if kind == "refresh_type_claim":
            # Correctly signed with this app's key, but minted as type="refresh".
            # Only the `type != "access"` check in get_current_user refuses it.
            payload = {
                "sub": registered_user["user"]["id"],
                "tenant_id": registered_user["tenant"]["id"],
                "role": "admin",
                "jti": f"refresh-{uuid4().hex}",
                "type": "refresh",
                "exp": (datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp(),
            }
            return {"Authorization": f"Bearer {jwt.encode(payload, settings.secret_key, algorithm='HS256')}"}
        if kind == "expired":
            payload = {
                "sub": registered_user["user"]["id"],
                "tenant_id": registered_user["tenant"]["id"],
                "role": "admin",
                "jti": f"expired-{uuid4().hex}",
                "type": "access",
                "exp": (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp(),
            }
            token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
            return {"Authorization": f"Bearer {token}"}
        if kind == "revoked":
            login = client.post("/api/v1/auth/login", json={
                "email": registered_user["email"],
                "password": registered_user["password"],
            })
            token = login.json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            client.post("/api/v1/auth/logout", headers=headers)
            return headers
        raise AssertionError(f"unknown credential kind {kind}")

    # One status per case, never a tuple: `in (401, 403)` passes either way and
    # so distinguishes nothing. These are what the app actually answers today —
    # HTTPBearer(auto_error=True) raises 401 "Not authenticated" for both a
    # missing header and a non-bearer scheme.
    @pytest.mark.parametrize("kind,expected", [
        ("no_header", 401),
        ("wrong_scheme", 401),
        ("malformed_jwt", 401),
        ("opaque_refresh_token", 401),
        ("refresh_type_claim", 401),
        ("expired", 401),
        ("revoked", 401),            # jti is in revoked_tokens
    ])
    def test_write_is_refused_and_no_row_is_created(
        self, kind, expected, client, registered_user, test_tenant,
    ):
        headers = self._headers_for(kind, client, registered_user)
        name = f"forged-{kind}-{uuid4().hex[:6]}"

        resp = client.post("/api/v1/sessions", json={"name": name}, headers=headers)
        assert resp.status_code == expected, resp.text

        # The refusal has to be a refusal, not a 401 alongside a write.
        row = query_one("SELECT id FROM sessions WHERE name = %s", (name,))
        assert row is None, f"{kind} was refused with {expected} but created a session anyway"
        count = query_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE tenant_id = %s", (test_tenant["id"],)
        )
        assert count["n"] == 0, f"{kind} wrote {count['n']} session rows into the tenant"

    def test_logout_actually_revokes_the_jti_it_was_given(self, client, registered_user):
        """The `revoked` case above depends on this being a real revocation and
        not, say, a 401 caused by a malformed token. Pin the mechanism."""
        from backend.auth.jwt_handler import decode_token

        login = client.post("/api/v1/auth/login", json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        })
        token = login.json()["data"]["access_token"]
        jti = decode_token(token)["jti"]
        assert query_one("SELECT jti FROM revoked_tokens WHERE jti = %s", (jti,)) is None

        assert client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200

        row = query_one(
            "SELECT jti, expires_at FROM revoked_tokens WHERE jti = %s AND expires_at > NOW()",
            (jti,),
        )
        assert row is not None, "logout returned 200 without blocklisting the token's jti"
        # Every refresh token of that user dies with it, or the session survives
        # its own logout via /auth/refresh.
        left = query_one(
            "SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = %s",
            (registered_user["user"]["id"],),
        )
        assert left["n"] == 0, "logout left a usable refresh token behind"


# ═══════════════════════════════════════════════════════════════════════════════
# Password reset: the 200 is not the point, the absence of an oracle is
# ═══════════════════════════════════════════════════════════════════════════════

class TestForgotPasswordIsNotAnEnumerationOracle:

    def test_unknown_address_issues_no_code_and_looks_identical(self, client, registered_user):
        tenant_id = registered_user["tenant"]["id"]
        ghost = f"ghost-{uuid4().hex}@faro-e2e.io"

        ghost_resp = client.post("/api/v1/auth/forgot-password", json={"email": ghost})
        assert ghost_resp.status_code == 200
        assert query_one(
            "SELECT COUNT(*) AS n FROM pw_change_codes WHERE tenant_id = %s", (tenant_id,)
        )["n"] == 0, "an OTP was issued for an address that has no account"

        real_resp = client.post(
            "/api/v1/auth/forgot-password", json={"email": registered_user["email"]},
        )
        assert real_resp.status_code == 200
        # Identical payload — every field except the envelope's own per-request
        # timestamp. Any difference (a field, a count, a word) tells an attacker
        # which addresses are registered.
        def _payload(resp):
            body = dict(resp.json())
            body.pop("meta", None)
            return body

        assert _payload(real_resp) == _payload(ghost_resp), (
            "the response differs between a known and an unknown address — "
            "forgot-password is an account-enumeration oracle"
        )

    def test_known_address_issues_exactly_one_live_reset_code(self, client, registered_user):
        user_id = registered_user["user"]["id"]
        assert client.post(
            "/api/v1/auth/forgot-password", json={"email": registered_user["email"]},
        ).status_code == 200

        row = query_one(
            """SELECT COUNT(*) AS n FROM pw_change_codes
               WHERE user_id = %s AND purpose = 'reset'
                     AND used = FALSE AND expires_at > NOW()""",
            (user_id,),
        )
        assert row["n"] == 1, f"expected exactly 1 live reset code, found {row['n']}"

        # Asking twice must not leave two live codes usable at once.
        client.post("/api/v1/auth/forgot-password", json={"email": registered_user["email"]})
        row = query_one(
            """SELECT COUNT(*) AS n FROM pw_change_codes
               WHERE user_id = %s AND purpose = 'reset'
                     AND used = FALSE AND expires_at > NOW()""",
            (user_id,),
        )
        assert row["n"] == 1, f"a second request left {row['n']} live codes"

    def test_the_stored_code_is_hashed_not_plaintext(self, client, registered_user):
        """A leaked pw_change_codes table must not be a list of working codes."""
        assert client.post(
            "/api/v1/auth/forgot-password", json={"email": registered_user["email"]},
        ).status_code == 200
        row = query_one(
            "SELECT code_hash FROM pw_change_codes WHERE user_id = %s ORDER BY created_at DESC",
            (registered_user["user"]["id"],),
        )
        assert row is not None
        assert len(row["code_hash"]) == 64, "code_hash is not a sha256 digest"
        assert not row["code_hash"].isdigit(), "the 6-digit OTP is stored in the clear"


# ═══════════════════════════════════════════════════════════════════════════════
# Permission pairs — viewer denied AND state unchanged, then analyst succeeds
# AND the row is in the database
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionAndDatasetPermissionPairs:

    def test_session_create(self, client, viewer_headers, analyst_headers, test_tenant, analyst_user):
        tenant_id = test_tenant["id"]
        denied_name = f"viewer-attempt-{uuid4().hex[:6]}"
        resp = client.post(
            "/api/v1/sessions", json={"name": denied_name}, headers=viewer_headers,
        )
        assert resp.status_code == 403
        assert query_one("SELECT id FROM sessions WHERE name = %s", (denied_name,)) is None

        allowed_name = f"analyst-ok-{uuid4().hex[:6]}"
        resp = client.post(
            "/api/v1/sessions", json={"name": allowed_name}, headers=analyst_headers,
        )
        assert resp.status_code == 201
        row = query_one(
            "SELECT tenant_id, name, status, created_by FROM sessions WHERE id = %s",
            (resp.json()["data"]["id"],),
        )
        assert row is not None, "201 returned but no session row exists"
        assert row["name"] == allowed_name
        assert row["tenant_id"] == tenant_id
        assert row["status"] == "DRAFT"
        assert row["created_by"] == analyst_user["user"]["id"]

    def test_dataset_upload(self, client, viewer_headers, analyst_headers, test_tenant, csv_bytes):
        tenant_id = test_tenant["id"]
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("denied.csv", csv_bytes, "text/csv")},
            headers=viewer_headers,
        )
        assert resp.status_code == 403
        assert query_one(
            "SELECT id FROM datasets WHERE tenant_id = %s AND original_filename = %s",
            (tenant_id, "denied.csv"),
        ) is None, "a viewer's upload was rejected with 403 and stored anyway"

        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("allowed.csv", csv_bytes, "text/csv")},
            headers=analyst_headers,
        )
        assert resp.status_code == 201
        row = query_one(
            "SELECT original_filename, file_type, size_bytes, file_path FROM datasets WHERE id = %s",
            (resp.json()["data"]["id"],),
        )
        assert row is not None, "201 returned but no dataset row exists"
        assert row["original_filename"] == "allowed.csv"
        assert row["file_type"] == "csv"
        # The bytes we sent, not a truncated or empty write.
        assert row["size_bytes"] == len(csv_bytes)
        from pathlib import Path
        stored = Path(row["file_path"])
        assert stored.exists(), f"dataset row points at a file that is not there: {stored}"
        assert stored.read_bytes() == csv_bytes, "the stored file is not what was uploaded"

    def test_columns_configuration(self, client, viewer_headers, analyst_headers, test_session):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403
        row = query_one("SELECT columns_cfg FROM session_configs WHERE session_id = %s", (sid,))
        assert row is None or row["columns_cfg"] is None, (
            "a viewer was refused with 403 but the column configuration was written"
        )

        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "date", "target_column": "sales"},
            headers=analyst_headers,
        )
        assert resp.status_code == 200
        row = query_one("SELECT columns_cfg FROM session_configs WHERE session_id = %s", (sid,))
        assert row is not None and row["columns_cfg"] is not None
        assert row["columns_cfg"]["date_column"] == "date"
        assert row["columns_cfg"]["target_column"] == "sales"

    def test_training_start(self, client, viewer_headers, analyst_headers, configured_session, test_tenant):
        sid = configured_session["id"]
        tenant_id = test_tenant["id"]

        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=viewer_headers)
        assert resp.status_code == 403
        assert query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s", (tenant_id,)
        )["n"] == 0, "a viewer was refused with 403 but a training job was queued"
        assert query_one("SELECT status FROM sessions WHERE id = %s", (sid,))["status"] \
            == configured_session["status"]

        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=analyst_headers)
        assert resp.status_code == 202
        job_id = resp.json()["data"]["job_id"]
        job = query_one(
            "SELECT tenant_id, session_id, status FROM jobs WHERE id = %s", (job_id,)
        )
        assert job is not None, "202 returned but no job row exists"
        assert job["tenant_id"] == tenant_id
        assert job["session_id"] == sid
        assert job["status"] == "QUEUED"
        assert query_one("SELECT status FROM sessions WHERE id = %s", (sid,))["status"] == "QUEUED"

    def test_viewer_reads_real_rows_not_just_a_200(self, client, viewer_headers, test_session):
        """The read half of the pair. A guard that denies viewers everything
        (over-broad) has to fail too, and it has to fail on content: a 200 with
        an empty list would pass the old version of this test."""
        resp = client.get("/api/v1/sessions", headers=viewer_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert test_session["id"] in [s["id"] for s in items], (
            "a viewer got 200 but cannot see the tenant's session"
        )


class TestTenantIsolation:
    def test_another_tenants_session_is_invisible_and_unmodifiable(self, client, auth_headers, test_session):
        """One tenant's session must be a 404 for another tenant on read AND on
        write, and the write must leave the row untouched."""
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        from backend.db.connection import execute

        sid = test_session["id"]
        original_name = query_one("SELECT name FROM sessions WHERE id = %s", (sid,))["name"]

        t2 = create_tenant(f"isolated-{uuid4().hex[:8]}")
        email2 = f"iso-{uuid4().hex[:8]}@faro-e2e.io"
        u2 = user_svc.create_user(t2["id"], email2, "TestPass123!", "admin")
        user_svc.mark_verified(t2["id"], u2["id"])
        try:
            login = client.post(
                "/api/v1/auth/login", json={"email": email2, "password": "TestPass123!"},
            )
            headers2 = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

            assert client.get(f"/api/v1/sessions/{sid}", headers=headers2).status_code == 404
            assert sid not in [
                s["id"] for s in
                client.get("/api/v1/sessions", headers=headers2).json()["data"]["items"]
            ], "tenant B's session list leaks tenant A's session"

            assert client.patch(
                f"/api/v1/sessions/{sid}", json={"name": "stolen"}, headers=headers2,
            ).status_code == 404
            assert client.delete(f"/api/v1/sessions/{sid}", headers=headers2).status_code == 404

            row = query_one("SELECT name FROM sessions WHERE id = %s", (sid,))
            assert row is not None, "a cross-tenant DELETE returned 404 and deleted the row"
            assert row["name"] == original_name, (
                "a cross-tenant PATCH returned 404 and renamed the row anyway"
            )
            # And the owner still sees it.
            assert client.get(f"/api/v1/sessions/{sid}", headers=auth_headers).status_code == 200
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (t2["id"],))


# ── Inventory permissions: mutations require analyst or above ────────────────
#
# 2026-07-04 audit (docs/auditoria_integral_faro_2026-07-04.md, finding #1):
# the previous "intentionally ungated" decision was reversed — inventory data
# drives real purchase orders, so a read-only viewer must not be able to mutate
# stock, events, suppliers, BOM or run bulk imports. Every mutation now depends
# on `require_analyst_or_above`; reads stay open to any authenticated user.
#
# These are permission PAIRS, and the "denied" half is checked against the
# DATABASE rather than against a second API call: a read endpoint that filters
# by tenant would hide a leaked write from the API but not from a direct query.

class TestInventoryMutationPermissions:
    def test_stock_upsert_viewer_denied_analyst_allowed(self, client, viewer_headers, analyst_headers, test_tenant):
        tenant_id = test_tenant["id"]
        sku = f"PYTEST-PERM-{uuid4().hex[:8]}"
        put = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 10},
            headers=viewer_headers,
        )
        assert put.status_code == 403
        assert query_one(
            "SELECT id FROM inventory_stock WHERE tenant_id = %s AND sku = %s", (tenant_id, sku),
        ) is None, "the viewer's PUT was refused and the stock row was created anyway"

        put = client.put(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 10},
            headers=analyst_headers,
        )
        assert put.status_code == 200
        row = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
            (tenant_id, sku),
        )
        assert row is not None and row["current_stock"] == 10

        assert client.delete(f"/api/v1/inventory/stock/{sku}", headers=viewer_headers).status_code == 403
        assert query_one(
            "SELECT id FROM inventory_stock WHERE tenant_id = %s AND sku = %s", (tenant_id, sku),
        ) is not None, "the viewer's DELETE was refused and the row was deleted anyway"

        assert client.delete(f"/api/v1/inventory/stock/{sku}", headers=analyst_headers).status_code == 204
        assert query_one(
            "SELECT id FROM inventory_stock WHERE tenant_id = %s AND sku = %s", (tenant_id, sku),
        ) is None, "the analyst's DELETE returned 204 without removing the row"

    def test_event_mutations_viewer_denied_analyst_allowed(self, client, viewer_headers, analyst_headers, test_tenant):
        tenant_id = test_tenant["id"]
        body = {"name": "perm-event", "start_date": "2026-11-01", "end_date": "2026-11-30"}
        assert client.post("/api/v1/inventory/events", json=body, headers=viewer_headers).status_code == 403
        assert query_one(
            "SELECT COUNT(*) AS n FROM inventory_events WHERE tenant_id = %s", (tenant_id,),
        )["n"] == 0, "the viewer's POST was refused and the event was created anyway"

        create = client.post("/api/v1/inventory/events", json=body, headers=analyst_headers)
        assert create.status_code == 201
        event_id = create.json()["data"]["id"]
        assert query_one(
            "SELECT name FROM inventory_events WHERE id = %s", (event_id,),
        )["name"] == "perm-event"

        patch = client.patch(
            f"/api/v1/inventory/events/{event_id}",
            json={"multiplier": 2.0},
            headers=viewer_headers,
        )
        assert patch.status_code == 403
        assert query_one(
            "SELECT multiplier FROM inventory_events WHERE id = %s", (event_id,),
        )["multiplier"] != 2.0, "the viewer's PATCH was refused and the multiplier changed anyway"

        patch = client.patch(
            f"/api/v1/inventory/events/{event_id}",
            json={"multiplier": 2.0},
            headers=analyst_headers,
        )
        assert patch.status_code == 200
        assert query_one(
            "SELECT multiplier FROM inventory_events WHERE id = %s", (event_id,),
        )["multiplier"] == 2.0

        assert client.delete(f"/api/v1/inventory/events/{event_id}", headers=viewer_headers).status_code == 403
        assert query_one("SELECT id FROM inventory_events WHERE id = %s", (event_id,)) is not None
        assert client.delete(f"/api/v1/inventory/events/{event_id}", headers=analyst_headers).status_code == 204
        assert query_one("SELECT id FROM inventory_events WHERE id = %s", (event_id,)) is None

    def test_supplier_mutations_viewer_denied_analyst_allowed(self, client, viewer_headers, analyst_headers, test_tenant):
        tenant_id = test_tenant["id"]
        assert client.post(
            "/api/v1/inventory/suppliers", json={"name": "perm-supplier"}, headers=viewer_headers,
        ).status_code == 403
        assert query_one(
            "SELECT COUNT(*) AS n FROM suppliers WHERE tenant_id = %s", (tenant_id,),
        )["n"] == 0, "the viewer's POST was refused and the supplier was created anyway"

        create = client.post(
            "/api/v1/inventory/suppliers", json={"name": "perm-supplier"}, headers=analyst_headers,
        )
        assert create.status_code == 201
        supplier_id = create.json()["data"]["id"]

        assert client.patch(
            f"/api/v1/inventory/suppliers/{supplier_id}",
            json={"name": "renamed"},
            headers=viewer_headers,
        ).status_code == 403
        assert query_one(
            "SELECT name FROM suppliers WHERE id = %s", (supplier_id,),
        )["name"] == "perm-supplier"

        assert client.patch(
            f"/api/v1/inventory/suppliers/{supplier_id}",
            json={"name": "renamed"},
            headers=analyst_headers,
        ).status_code == 200
        assert query_one(
            "SELECT name FROM suppliers WHERE id = %s", (supplier_id,),
        )["name"] == "renamed"

        assert client.delete(
            f"/api/v1/inventory/suppliers/{supplier_id}", headers=viewer_headers,
        ).status_code == 403
        assert client.delete(
            f"/api/v1/inventory/suppliers/{supplier_id}", headers=analyst_headers,
        ).status_code == 204
        # Suppliers are deactivated rather than deleted; either way they must be
        # gone from the tenant's live list.
        assert supplier_id not in [
            s["id"] for s in
            client.get("/api/v1/inventory/suppliers", headers=analyst_headers).json()["data"]
        ]

    def test_bom_mutations_viewer_denied_analyst_allowed(self, client, viewer_headers, analyst_headers, test_tenant):
        tenant_id = test_tenant["id"]
        parent, child = f"PERM-P-{uuid4().hex[:6]}", f"PERM-C-{uuid4().hex[:6]}"
        for sku in (parent, child):
            assert client.put(
                f"/api/v1/inventory/stock/{sku}", json={"current_stock": 1}, headers=analyst_headers,
            ).status_code == 200

        assert client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 5.0},
            headers=viewer_headers,
        ).status_code == 403
        assert query_one(
            "SELECT COUNT(*) AS n FROM bom_items WHERE tenant_id = %s AND parent_sku = %s",
            (tenant_id, parent),
        )["n"] == 0, "the viewer's BOM write was refused and stored anyway"

        assert client.put(
            f"/api/v1/inventory/bom/{parent}/{child}",
            json={"quantity": 5.0},
            headers=analyst_headers,
        ).status_code == 200
        assert query_one(
            "SELECT quantity FROM bom_items WHERE tenant_id = %s AND parent_sku = %s AND child_sku = %s",
            (tenant_id, parent, child),
        )["quantity"] == 5.0

        assert client.delete(
            f"/api/v1/inventory/bom/{parent}/{child}", headers=viewer_headers,
        ).status_code == 403
        assert query_one(
            "SELECT COUNT(*) AS n FROM bom_items WHERE tenant_id = %s AND parent_sku = %s",
            (tenant_id, parent),
        )["n"] == 1, "the viewer's BOM delete was refused and removed the row anyway"

        assert client.delete(
            f"/api/v1/inventory/bom/{parent}/{child}", headers=analyst_headers,
        ).status_code == 204
        assert query_one(
            "SELECT COUNT(*) AS n FROM bom_items WHERE tenant_id = %s AND parent_sku = %s",
            (tenant_id, parent),
        )["n"] == 0

    def test_bulk_import_viewer_denied_analyst_allowed(self, client, viewer_headers, analyst_headers, test_tenant):
        tenant_id = test_tenant["id"]
        sku = f"PYTEST-BULK-{uuid4().hex[:8]}"
        csv_content = f"sku,current_stock,lead_time_days\n{sku},25,10\n".encode()

        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", csv_content, "text/csv")},
            headers=viewer_headers,
        )
        assert resp.status_code == 403
        assert query_one(
            "SELECT id FROM inventory_stock WHERE tenant_id = %s AND sku = %s", (tenant_id, sku),
        ) is None, "the viewer's bulk import was refused and imported anyway"

        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("stock.csv", csv_content, "text/csv")},
            headers=analyst_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["imported"] == 1
        row = query_one(
            "SELECT current_stock, lead_time_days FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s",
            (tenant_id, sku),
        )
        assert row is not None, "the import reported 1 row and wrote none"
        assert row["current_stock"] == 25
        assert row["lead_time_days"] == 10

    def test_viewer_can_still_read_inventory(self, client, viewer_headers, analyst_headers):
        """Over-broad guards must fail too — and on content, not on the status.
        Seed one row of each kind as an analyst, then require the viewer to see
        it: a guard that returns 200 with an empty list fails here."""
        sku = f"PYTEST-READ-{uuid4().hex[:8]}"
        assert client.put(
            f"/api/v1/inventory/stock/{sku}", json={"current_stock": 7}, headers=analyst_headers,
        ).status_code == 200
        event = client.post(
            "/api/v1/inventory/events",
            json={"name": "readable", "start_date": "2026-11-01", "end_date": "2026-11-02"},
            headers=analyst_headers,
        )
        assert event.status_code == 201
        supplier = client.post(
            "/api/v1/inventory/suppliers", json={"name": "readable-supplier"}, headers=analyst_headers,
        )
        assert supplier.status_code == 201

        stock = client.get("/api/v1/inventory/stock", headers=viewer_headers)
        assert stock.status_code == 200
        assert sku in [s["sku"] for s in stock.json()["data"]]

        events = client.get("/api/v1/inventory/events", headers=viewer_headers)
        assert events.status_code == 200
        assert event.json()["data"]["id"] in [e["id"] for e in events.json()["data"]]

        suppliers = client.get("/api/v1/inventory/suppliers", headers=viewer_headers)
        assert suppliers.status_code == 200
        assert supplier.json()["data"]["id"] in [s["id"] for s in suppliers.json()["data"]]


# ═══════════════════════════════════════════════════════════════════════════════
# Input validation — a 422 that still writes is the failure being looked for
# ═══════════════════════════════════════════════════════════════════════════════

class TestInputValidation:
    def test_create_session_without_name_returns_422_and_creates_nothing(
        self, client, auth_headers, test_tenant,
    ):
        resp = client.post("/api/v1/sessions", json={}, headers=auth_headers)
        assert resp.status_code == 422
        assert query_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE tenant_id = %s", (test_tenant["id"],)
        )["n"] == 0

    def test_configure_columns_empty_date_column_is_rejected_and_persists_nothing(
        self, client, auth_headers, test_session,
    ):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/columns",
            json={"date_column": "", "target_column": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
        # A stable code, because the frontend renders the Spanish from it.
        assert resp.json()["error_code"] == "column_required"
        assert resp.json()["error_params"]["field"] == "date"
        row = query_one("SELECT columns_cfg FROM session_configs WHERE session_id = %s", (sid,))
        assert row is None or row["columns_cfg"] is None, (
            "an empty column configuration was rejected with 422 and stored anyway"
        )
        assert query_one("SELECT status FROM sessions WHERE id = %s", (sid,))["status"] == "DRAFT"

    def test_out_of_range_train_ratio_must_not_be_accepted(self, client, auth_headers, test_session):
        """
        A session whose train_ratio is outside (0, 1) cannot train: the worker
        feeds validation_cfg straight into `ForecastEngine.from_dict`
        (backend/workers/runner.py:140 → :732), and `SessionConfig.from_dict`
        calls `validate()`, which raises
        ConfigError("training.train_ratio must be between 0 and 1")
        (ForecastingCore/forecasting_core/config/config.py:262).

        `ValidationConfigRequest.train_ratio` (backend/schemas/configuration.py)
        is a bare `float = 0.8` with no Field(gt=0, lt=1), so the wizard accepts
        1.5, persists it, and the failure only surfaces when the job dies.
        The boundary is the place to refuse it.
        """
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/configure/validation",
            json={"train_ratio": 1.5},
            headers=auth_headers,
        )
        assert resp.status_code == 422, (
            "train_ratio=1.5 was accepted; this session is now configured to "
            "fail at training time with ConfigError"
        )
        row = query_one("SELECT validation_cfg FROM session_configs WHERE session_id = %s", (sid,))
        assert row is None or row["validation_cfg"] is None

    def test_valid_train_ratio_is_stored_verbatim(self, client, auth_headers, test_session):
        """Guards the test above: the endpoint must still accept a sane value,
        so a blanket rejection would not be mistaken for a fix."""
        sid = test_session["id"]
        assert client.post(
            f"/api/v1/sessions/{sid}/configure/validation",
            json={"train_ratio": 0.75},
            headers=auth_headers,
        ).status_code == 200
        row = query_one("SELECT validation_cfg FROM session_configs WHERE session_id = %s", (sid,))
        assert row["validation_cfg"]["train_ratio"] == 0.75

    def test_signup_missing_required_fields_creates_no_user(self, client):
        email = f"partial-{uuid4().hex[:8]}@faro-e2e.io"
        resp = client.post("/api/v1/auth/signup", json={"email": email})
        assert resp.status_code == 422
        assert query_one("SELECT id FROM users WHERE email = %s", (email,)) is None

    def test_upload_oversized_file_is_blocked_by_plan_limit(self, client, auth_headers, test_tenant, monkeypatch):
        # The size cap is bypassed under TESTING_MODE, so the test must turn it
        # off itself or it can never fail on a local .env with TESTING_MODE=true.
        from backend.config import settings
        monkeypatch.setattr(settings, "testing_mode", False)
        # >200MB: over the Starter plan's max_dataset_size_mb, which is now the
        # authoritative cap (403 PLAN_LIMIT_REACHED, not the old generic 400).
        fake_big = b"a" * (200 * 1024 * 1024 + 1)
        resp = client.post(
            "/api/v1/datasets",
            files={"file": ("big.csv", fake_big, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "PLAN_LIMIT_REACHED"
        assert detail["limit"] == "max_dataset_size_mb"
        assert query_one(
            "SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s", (test_tenant["id"],)
        )["n"] == 0, "the upload was refused over the plan limit and stored anyway"

    def test_attach_nonexistent_dataset_leaves_the_session_unattached(
        self, client, auth_headers, test_session,
    ):
        sid = test_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/dataset",
            json={"dataset_id": ""},
            headers=auth_headers,
        )
        assert resp.status_code in (404, 422)
        row = query_one("SELECT dataset_id, status FROM sessions WHERE id = %s", (sid,))
        assert row["dataset_id"] is None, "a rejected attach set sessions.dataset_id anyway"
        assert row["status"] == "DRAFT"

    @pytest.mark.parametrize("token_kind", ["garbage", "forged_signature", "wrong_purpose"])
    def test_reset_password_with_an_untrusted_token_changes_nothing(
        self, token_kind, client, registered_user,
    ):
        """A reset token is a password-change capability, so all three ways of
        not having one must leave the hash alone: unparseable, correctly shaped
        but signed with somebody else's key, and a genuine token minted for a
        different purpose (e.g. an email-verification link)."""
        from backend.auth.jwt_handler import create_signed_token
        from backend.config import settings

        user_id = registered_user["user"]["id"]
        claims = {
            "sub": user_id,
            "tenant_id": registered_user["tenant"]["id"],
            "purpose": "password_reset",
        }
        if token_kind == "garbage":
            token = "invalid.token.here"
        elif token_kind == "forged_signature":
            token = jwt.encode(
                {**claims, "exp": 9_999_999_999}, settings.secret_key + "-wrong",
                algorithm="HS256",
            )
        else:
            token = create_signed_token({**claims, "purpose": "email_verify"}, expires_minutes=15)

        before = query_one("SELECT hashed_password FROM users WHERE id = %s", (user_id,))["hashed_password"]
        resp = client.post("/api/v1/auth/reset-password", json={
            "token": token, "new_password": "NewPass123!",
        })
        assert resp.status_code == 400, resp.text

        after = query_one("SELECT hashed_password FROM users WHERE id = %s", (user_id,))["hashed_password"]
        assert after == before, (
            f"a {token_kind} reset token was refused with 400 and changed the password hash anyway"
        )
        # And the real password still works, so the account was not locked out.
        assert client.post("/api/v1/auth/login", json={
            "email": registered_user["email"], "password": registered_user["password"],
        }).status_code == 200

    def test_reset_password_weak_is_refused_with_a_valid_token(self, client, registered_user):
        """The token is genuine here, so only the strength check can refuse —
        and it must refuse without writing."""
        from backend.auth.jwt_handler import create_signed_token
        user_id = registered_user["user"]["id"]
        before = query_one("SELECT hashed_password FROM users WHERE id = %s", (user_id,))["hashed_password"]

        token = create_signed_token({
            "sub": user_id,
            "tenant_id": registered_user["tenant"]["id"],
            "purpose": "password_reset",
        }, expires_minutes=15)
        resp = client.post("/api/v1/auth/reset-password", json={
            "token": token, "new_password": "weak",
        })
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "password_invalid"
        assert query_one(
            "SELECT hashed_password FROM users WHERE id = %s", (user_id,),
        )["hashed_password"] == before, "a weak password was refused and stored anyway"

        # Same token, strong password: proves the 400 above came from the
        # strength rule and not from a broken token path.
        strong = "AnotherStrongPass123!"
        assert client.post("/api/v1/auth/reset-password", json={
            "token": token, "new_password": strong,
        }).status_code == 200
        assert query_one(
            "SELECT hashed_password FROM users WHERE id = %s", (user_id,),
        )["hashed_password"] != before
        assert client.post("/api/v1/auth/login", json={
            "email": registered_user["email"], "password": strong,
        }).status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Session state machine — the 409 must also mean nothing happened
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionStateEdgeCases:
    def test_cannot_delete_running_session(self, client, auth_headers, test_tenant):
        from backend.sessions.service import create_session, force_status

        s = create_session(test_tenant["id"], "usr_test", "running-session")
        force_status(test_tenant["id"], s["id"], "RUNNING")
        resp = client.delete(f"/api/v1/sessions/{s['id']}", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "session_running_cannot_delete"
        row = query_one("SELECT status FROM sessions WHERE id = %s", (s["id"],))
        assert row is not None, "the 409 was returned and the running session was deleted anyway"
        assert row["status"] == "RUNNING"

    def test_cannot_start_training_on_running_session(self, client, auth_headers, test_tenant):
        from backend.sessions.service import create_session, force_status

        s = create_session(test_tenant["id"], "usr_test", "already-running")
        force_status(test_tenant["id"], s["id"], "RUNNING")
        resp = client.post(f"/api/v1/sessions/{s['id']}/train", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "session_not_trainable"
        assert query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s", (test_tenant["id"],)
        )["n"] == 0, "a second training run was queued for an already-RUNNING session"

    @pytest.mark.parametrize("status", ["DRAFT", "RUNNING"])
    def test_results_before_completion_are_refused_and_no_results_exist(
        self, status, client, auth_headers, test_tenant,
    ):
        from backend.sessions.service import create_session, force_status

        s = create_session(test_tenant["id"], "usr_test", f"results-{status}")
        force_status(test_tenant["id"], s["id"], status)
        resp = client.get(f"/api/v1/sessions/{s['id']}/results", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "session_still_training"
        # The refusal has to be truthful: there really are no results to serve.
        assert query_one(
            "SELECT COUNT(*) AS n FROM session_results WHERE session_id = %s", (s["id"],)
        )["n"] == 0

    def test_cancel_completed_job_leaves_it_completed(
        self, client, auth_headers, completed_session, registered_user,
    ):
        from backend.training.job_service import create_job, mark_completed

        tid = registered_user["tenant"]["id"]
        job = create_job(tid, completed_session["id"], "usr_test")
        mark_completed(tid, job["id"])

        resp = client.delete(f"/api/v1/jobs/{job['id']}", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "job_not_cancellable"
        assert query_one(
            "SELECT status FROM jobs WHERE id = %s", (job["id"],)
        )["status"] == "COMPLETED", "a finished job was flipped to CANCELLED by a 409"


# ═══════════════════════════════════════════════════════════════════════════════
# Quotas
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuotaLimits:
    def test_concurrent_job_limit_enforced(self, client, auth_headers, configured_session, test_tenant, monkeypatch):
        from backend.training.job_service import create_job, mark_running
        from backend.db.connection import execute
        # The concurrency cap is bypassed under TESTING_MODE — disable per-test.
        from backend.config import settings
        monkeypatch.setattr(settings, "testing_mode", False)

        # Fill up to limit (3) with fake RUNNING jobs
        sid = configured_session["id"]
        job_ids = []
        for _ in range(3):
            j = create_job(test_tenant["id"], sid, "usr_test")
            mark_running(test_tenant["id"], j["id"], "worker_test")
            job_ids.append(j["id"])

        resp = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert resp.status_code == 429
        assert resp.json()["error_code"] == "too_many_active_jobs"
        # The cap has to actually stop the work, not just answer 429.
        assert query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s", (test_tenant["id"],)
        )["n"] == 3, "the 429 was returned and a fourth job was queued anyway"
        assert query_one(
            "SELECT status FROM sessions WHERE id = %s", (sid,)
        )["status"] == configured_session["status"]

        # Cleanup: cancel the fake jobs
        for jid in job_ids:
            execute(
                "UPDATE jobs SET status = 'CANCELLED', completed_at = NOW() WHERE id = %s",
                (jid,),
            )
