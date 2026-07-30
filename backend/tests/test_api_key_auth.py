"""API keys that actually authenticate.

Until now a key could be minted, listed and revoked, and then authenticated
nothing: every route resolved a JWT and only a JWT. These tests pin the
behaviour that makes a key a credential rather than a decoration, and — more
importantly — the limits on what one can do.

The mutating endpoint used throughout is `PUT /inventory/stock/{sku}`, because
its effect is a row anyone can go and look at: every assertion here reads the
database rather than trusting a status code.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.auth.api_key_auth import hash_key
from backend.config import settings
from backend.db.connection import execute, query_one


def _mint(tenant_id: str, role: str = "viewer", *, expires_at=None, created_by="usr_test") -> str:
    """A key row exactly as the endpoint writes one, returning the raw secret."""
    raw = f"sk_live_{uuid4().hex}{uuid4().hex}"
    execute(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4, expires_at)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s, %s, %s)""",
        (tenant_id, f"test-key-{role}", hash_key(raw), role, created_by, raw[-4:], expires_at),
    )
    return raw


def _headers(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def _stock_row(tenant_id: str, sku: str):
    return query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku),
    )


class TestAKeyIsACredential:

    def test_a_key_reads_the_tenants_own_rows(self, client, test_tenant, analyst_headers):
        """Not just a 200: the key must see the data a person would see."""
        tenant_id = test_tenant["id"]
        sku = f"KEY-READ-{uuid4().hex[:8]}"
        seeded = client.put(
            f"/api/v1/inventory/stock/{sku}", json={"current_stock": 42},
            headers=analyst_headers,
        )
        assert seeded.status_code == 200

        raw = _mint(tenant_id, "viewer")
        resp = client.get("/api/v1/inventory/stock", headers=_headers(raw))

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        rows = body if isinstance(body, list) else body.get("items", body)
        assert any(r.get("sku") == sku for r in rows), (
            "the key authenticated but the tenant's own stock row was not in the response"
        )

    def test_an_unknown_key_is_refused_and_says_nothing(self, client):
        resp = client.get("/api/v1/inventory/stock", headers=_headers("sk_live_" + "0" * 43))
        assert resp.status_code == 401
        # A key that never existed must not be distinguishable from a deleted
        # or expired one, or the endpoint becomes an oracle for probing.
        assert "expired" in resp.text and "not found" not in resp.text.lower()

    def test_a_deleted_key_stops_working_immediately(self, client, test_tenant):
        raw = _mint(test_tenant["id"], "viewer")
        assert client.get("/api/v1/inventory/stock", headers=_headers(raw)).status_code == 200

        execute("DELETE FROM api_keys WHERE key_hash = %s", (hash_key(raw),))
        assert client.get("/api/v1/inventory/stock", headers=_headers(raw)).status_code == 401

    def test_an_expired_key_is_refused(self, client, test_tenant):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        raw = _mint(test_tenant["id"], "viewer", expires_at=past)
        assert client.get("/api/v1/inventory/stock", headers=_headers(raw)).status_code == 401

    def test_a_key_expiring_later_still_works(self, client, test_tenant):
        """The expiry check must compare instants, not merely be non-null."""
        future = datetime.now(timezone.utc) + timedelta(days=1)
        raw = _mint(test_tenant["id"], "viewer", expires_at=future)
        assert client.get("/api/v1/inventory/stock", headers=_headers(raw)).status_code == 200

    def test_the_raw_key_is_never_stored(self, client, test_tenant, auth_headers):
        resp = client.post("/api/v1/api-keys", json={"name": "erp"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        raw = resp.json()["data"]["key"]

        row = query_one(
            "SELECT key_hash, last4 FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],))
        assert row["key_hash"] != raw
        assert row["key_hash"] == hash_key(raw)
        assert row["last4"] == raw[-4:]
        # Nothing anywhere in the row may contain enough of the key to use it.
        assert raw not in str(dict(row))

    def test_a_jwt_still_authenticates(self, client, auth_headers):
        """The regression that matters: humans must be unaffected."""
        assert client.get("/api/v1/inventory/stock", headers=auth_headers).status_code == 200


class TestAKeyCannotExceedItsRole:
    """The permission pair, expressed in keys instead of people."""

    def test_a_viewer_key_is_denied_and_writes_nothing(self, client, test_tenant):
        tenant_id = test_tenant["id"]
        sku = f"KEY-PERM-{uuid4().hex[:8]}"
        raw = _mint(tenant_id, "viewer")

        resp = client.put(
            f"/api/v1/inventory/stock/{sku}", json={"current_stock": 10},
            headers=_headers(raw),
        )

        assert resp.status_code == 403
        assert _stock_row(tenant_id, sku) is None, (
            "the read-only key was refused and the row was written anyway"
        )

    def test_an_analyst_key_writes_the_row(self, client, test_tenant):
        tenant_id = test_tenant["id"]
        sku = f"KEY-PERM-{uuid4().hex[:8]}"
        raw = _mint(tenant_id, "analyst")

        resp = client.put(
            f"/api/v1/inventory/stock/{sku}", json={"current_stock": 10},
            headers=_headers(raw),
        )

        assert resp.status_code == 200, resp.text
        row = _stock_row(tenant_id, sku)
        assert row is not None and row["current_stock"] == 10

    def test_a_viewer_key_cannot_mint_a_stronger_key(self, client, test_tenant):
        """Otherwise the role on a key is a suggestion: a read-only integration
        whose credential leaked could mint itself a writing one.

        The refusal comes from the role guard on the endpoint, which is exactly
        why a key must carry a real role rather than a label: the guard sees a
        viewer and stops, without knowing it is talking to a machine.
        """
        raw = _mint(test_tenant["id"], "viewer")
        before = query_one(
            "SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],))["n"]

        resp = client.post(
            "/api/v1/api-keys", json={"name": "escalated", "role": "analyst"},
            headers=_headers(raw),
        )

        assert resp.status_code == 403
        after = query_one(
            "SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],))["n"]
        assert after == before, "the escalated key was refused and created anyway"


class TestAKeyIsBoundToItsTenant:

    def test_a_key_cannot_read_another_tenants_stock(self, client, test_tenant, analyst_headers):
        """The isolation that matters most once credentials live outside the app."""
        from backend.tenants.service import create_tenant

        victim_sku = f"KEY-ISO-{uuid4().hex[:8]}"
        assert client.put(
            f"/api/v1/inventory/stock/{victim_sku}", json={"current_stock": 77},
            headers=analyst_headers,
        ).status_code == 200

        other = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        try:
            raw = _mint(other["id"], "analyst")
            resp = client.get("/api/v1/inventory/stock", headers=_headers(raw))

            assert resp.status_code == 200
            body = resp.json()["data"]
            rows = body if isinstance(body, list) else body.get("items", body)
            assert not any(r.get("sku") == victim_sku for r in rows), (
                "a key from another tenant read this tenant's stock"
            )
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


class TestAKeyDiesWithThePlan:

    def test_a_downgraded_tenant_loses_api_access(self, client, test_tenant, monkeypatch):
        """A paid capability must not outlive the plan that paid for it.

        Keys are rows: cancelling a plan does not delete them. Checking the
        entitlement only when the key is MINTED would leave every existing key
        working forever after a downgrade.
        """
        # The local .env runs with TESTING_MODE=true, which bypasses every
        # entitlement check — this test is about the check, so it must turn the
        # bypass off itself.
        monkeypatch.setattr(settings, "testing_mode", False)

        raw = _mint(test_tenant["id"], "viewer")
        execute("UPDATE tenants SET plan = 'professional' WHERE id = %s", (test_tenant["id"],))
        assert client.get("/api/v1/inventory/stock", headers=_headers(raw)).status_code == 200

        execute("UPDATE tenants SET plan = 'starter' WHERE id = %s", (test_tenant["id"],))
        resp = client.get("/api/v1/inventory/stock", headers=_headers(raw))

        assert resp.status_code == 403
        assert "PLAN_UPGRADE_REQUIRED" in resp.text


class TestLastUsed:

    def test_first_call_stamps_last_used(self, client, test_tenant):
        raw = _mint(test_tenant["id"], "viewer")
        assert query_one(
            "SELECT last_used FROM api_keys WHERE key_hash = %s", (hash_key(raw),))["last_used"] is None

        client.get("/api/v1/inventory/stock", headers=_headers(raw))

        assert query_one(
            "SELECT last_used FROM api_keys WHERE key_hash = %s", (hash_key(raw),)
        )["last_used"] is not None, "the key was used and 'Last used' still reads never"

    def test_a_burst_of_calls_writes_once(self, client, test_tenant):
        """The throttle is the point: one row update per minute, not per request."""
        raw = _mint(test_tenant["id"], "viewer")
        client.get("/api/v1/inventory/stock", headers=_headers(raw))
        first = query_one(
            "SELECT last_used FROM api_keys WHERE key_hash = %s", (hash_key(raw),))["last_used"]

        for _ in range(5):
            client.get("/api/v1/inventory/stock", headers=_headers(raw))

        again = query_one(
            "SELECT last_used FROM api_keys WHERE key_hash = %s", (hash_key(raw),))["last_used"]
        assert again == first, (
            "every request rewrote last_used; an integration would pay a write per call"
        )


class TestTheKeyEndpoints:

    def test_creating_a_key_records_its_role_and_creator(self, client, test_tenant, auth_headers, registered_user):
        resp = client.post(
            "/api/v1/api-keys", json={"name": "nightly sync", "role": "analyst"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        row = query_one(
            "SELECT name, role, created_by, expires_at FROM api_keys WHERE tenant_id = %s",
            (test_tenant["id"],),
        )
        assert row["name"] == "nightly sync"
        assert row["role"] == "analyst"
        assert row["created_by"] == registered_user["user"]["id"]
        assert row["expires_at"] is None, "no expiry was asked for and one was invented"

    def test_expires_in_days_is_honoured(self, client, test_tenant, auth_headers):
        resp = client.post(
            "/api/v1/api-keys", json={"name": "temp", "expires_in_days": 30},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        row = query_one(
            "SELECT expires_at FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],))
        delta = row["expires_at"] - datetime.now(timezone.utc)
        assert timedelta(days=29) < delta < timedelta(days=31)

    @pytest.mark.parametrize("body", [
        {"name": "bad", "role": "admin"},
        {"name": "bad", "role": "root"},
        {"name": "bad", "expires_in_days": 0},
        {"name": "   "},
    ])
    def test_invalid_requests_create_nothing(self, body, client, test_tenant, auth_headers):
        resp = client.post("/api/v1/api-keys", json=body, headers=auth_headers)
        assert resp.status_code == 422, f"{body} was accepted"
        assert query_one(
            "SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],)
        )["n"] == 0

    def test_the_list_never_returns_the_hash(self, client, test_tenant, auth_headers):
        created = client.post("/api/v1/api-keys", json={"name": "erp"}, headers=auth_headers)
        raw = created.json()["data"]["key"]

        resp = client.get("/api/v1/api-keys", headers=auth_headers)
        assert resp.status_code == 200
        text = resp.text
        assert raw not in text
        assert hash_key(raw) not in text, "the stored hash was served to the client"
        assert raw[-4:] in text, "the list cannot tell two keys apart"

    def test_a_viewer_cannot_create_or_revoke(self, client, test_tenant, viewer_headers, auth_headers):
        resp = client.post("/api/v1/api-keys", json={"name": "nope"}, headers=viewer_headers)
        assert resp.status_code == 403
        assert query_one(
            "SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],)
        )["n"] == 0

        created = client.post("/api/v1/api-keys", json={"name": "real"}, headers=auth_headers)
        key_id = query_one(
            "SELECT id FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],))["id"]
        assert created.status_code == 200

        assert client.delete(f"/api/v1/api-keys/{key_id}", headers=viewer_headers).status_code == 403
        assert query_one(
            "SELECT id FROM api_keys WHERE id = %s", (key_id,)
        ) is not None, "the viewer's revoke was refused and the key was deleted anyway"

    def test_revoking_another_tenants_key_is_a_404_and_leaves_it_alone(
        self, client, test_tenant, auth_headers,
    ):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        try:
            raw = _mint(other["id"], "viewer")
            victim = query_one(
                "SELECT id FROM api_keys WHERE key_hash = %s", (hash_key(raw),))["id"]

            resp = client.delete(f"/api/v1/api-keys/{victim}", headers=auth_headers)

            assert resp.status_code == 404
            assert query_one("SELECT id FROM api_keys WHERE id = %s", (victim,)) is not None
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))
