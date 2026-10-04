"""API keys on the whole app: scopes, the walls, and the meter.

Every action a person can take in StockAI is now callable with an `sk_live_*`
key (backend/api/public_surface.py decides which). Three things have to hold
for that to be safe and billable:

* a `read` key never changes anything, and a `write` key changes exactly what
  an analyst could;
* the classes the owner named — auth, user/password management, instance
  configuration, tenant export/deletion, key management — refuse every key;
* every call that reached an endpoint is counted once, in Postgres, and a
  counter that cannot be written never fails the call it was counting.

Every assertion reads the database, not the status code alone.
"""

import logging
import threading
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.auth import api_key_auth
from backend.auth.api_key_auth import hash_key
from backend.db.connection import execute, query, query_one


def _mint(tenant_id: str, role: str, name: str | None = None) -> tuple[str, str]:
    """A key row exactly as the endpoint writes one → (raw secret, key id)."""
    raw = f"sk_live_{uuid4().hex}{uuid4().hex}"
    row = query_one(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, 'usr_test', %s)
           RETURNING id""",
        (tenant_id, name or f"key-{role}", hash_key(raw), role, raw[-4:]),
    )
    return raw, row["id"]


def _h(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def _calls(tenant_id: str, key_id: str | None = None) -> int:
    if key_id is None:
        row = query_one(
            "SELECT COALESCE(SUM(calls), 0) AS n FROM api_usage_daily WHERE tenant_id = %s",
            (tenant_id,))
    else:
        row = query_one(
            """SELECT COALESCE(SUM(calls), 0) AS n FROM api_usage_daily
                WHERE tenant_id = %s AND api_key_id = %s""", (tenant_id, key_id))
    return int(row["n"])


def _stock(tenant_id: str, sku: str):
    return query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tenant_id, sku))


# ── Scope column ─────────────────────────────────────────────────────────────

class TestTheScopeColumn:
    def test_scope_is_derived_from_role_for_every_row(self, test_tenant):
        """Generated, so an analyst key minted before scopes existed is a write
        key — a flat 'read' backfill would have silently broken its sync."""
        _raw, rid = _mint(test_tenant["id"], "viewer")
        _raw, wid = _mint(test_tenant["id"], "analyst")
        rows = {r["id"]: r["scope"] for r in query(
            "SELECT id, scope FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],))}
        assert rows == {rid: "read", wid: "write"}

    def test_creating_a_write_key_by_scope(self, client, test_tenant, analyst_headers):
        resp = client.post("/api/v1/api-keys", json={"name": "erp", "scope": "write"},
                           headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["scope"] == "write"
        row = query_one("SELECT role, scope FROM api_keys WHERE tenant_id = %s",
                        (test_tenant["id"],))
        assert (row["role"], row["scope"]) == ("analyst", "write")

    def test_default_scope_is_read(self, client, test_tenant, auth_headers):
        resp = client.post("/api/v1/api-keys", json={"name": "bi"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        row = query_one("SELECT scope FROM api_keys WHERE tenant_id = %s", (test_tenant["id"],))
        assert row["scope"] == "read"

    @pytest.mark.parametrize("body", [
        {"name": "x", "scope": "admin"},
        {"name": "x", "scope": "write", "role": "viewer"},
    ])
    def test_invalid_scope_creates_nothing(self, client, test_tenant, auth_headers, body):
        resp = client.post("/api/v1/api-keys", json=body, headers=auth_headers)
        assert resp.status_code == 422
        assert query_one("SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s",
                         (test_tenant["id"],))["n"] == 0

    def test_viewer_cannot_create_a_key(self, client, test_tenant, viewer_headers):
        resp = client.post("/api/v1/api-keys", json={"name": "x", "scope": "read"},
                           headers=viewer_headers)
        assert resp.status_code == 403
        assert query_one("SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s",
                         (test_tenant["id"],))["n"] == 0


# ── The permission pair, in keys ─────────────────────────────────────────────

class TestReadAndWriteKeys:
    def test_read_key_is_refused_on_a_write_and_nothing_changes(self, client, test_tenant):
        raw, kid = _mint(test_tenant["id"], "viewer")
        sku = f"SCOPE-{uuid4().hex[:8]}"
        resp = client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 5},
                          headers=_h(raw))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "api_key_scope_insufficient"
        assert _stock(test_tenant["id"], sku) is None
        assert _calls(test_tenant["id"], kid) == 0, "a refused call was billed"

    def test_write_key_writes_the_row_and_is_metered(self, client, test_tenant):
        raw, kid = _mint(test_tenant["id"], "analyst")
        sku = f"SCOPE-{uuid4().hex[:8]}"
        resp = client.put(f"/api/v1/inventory/stock/{sku}", json={"current_stock": 7},
                          headers=_h(raw))
        assert resp.status_code == 200, resp.text
        assert _stock(test_tenant["id"], sku)["current_stock"] == 7
        assert _calls(test_tenant["id"], kid) == 1

    def test_read_key_reads_an_area_beyond_the_old_eight(self, client, test_tenant, analyst_headers):
        """Suppliers were never on the old public list; now they are reachable."""
        name = f"Prov {uuid4().hex[:6]}"
        assert client.post("/api/v1/inventory/suppliers", json={"name": name},
                           headers=analyst_headers).status_code in (200, 201)
        raw, _ = _mint(test_tenant["id"], "viewer")
        resp = client.get("/api/v1/inventory/suppliers", headers=_h(raw))
        assert resp.status_code == 200, resp.text
        assert name in resp.text

    def test_write_key_creates_a_supplier(self, client, test_tenant):
        raw, _ = _mint(test_tenant["id"], "analyst")
        name = f"Prov {uuid4().hex[:6]}"
        resp = client.post("/api/v1/inventory/suppliers", json={"name": name}, headers=_h(raw))
        assert resp.status_code in (200, 201), resp.text
        assert query_one("SELECT 1 AS ok FROM suppliers WHERE tenant_id = %s AND name = %s",
                         (test_tenant["id"], name))


# ── The walls ────────────────────────────────────────────────────────────────

class TestKeysNeverReachTheExcludedClasses:
    def test_a_key_cannot_mint_a_key(self, client, test_tenant):
        raw, _ = _mint(test_tenant["id"], "analyst")
        resp = client.post("/api/v1/api-keys", json={"name": "child", "scope": "write"},
                           headers=_h(raw))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "api_key_route_not_exposed"
        assert query_one("SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s",
                         (test_tenant["id"],))["n"] == 1

    def test_a_key_cannot_revoke_a_key(self, client, test_tenant):
        raw, kid = _mint(test_tenant["id"], "analyst")
        resp = client.delete(f"/api/v1/api-keys/{kid}", headers=_h(raw))
        assert resp.status_code == 403
        assert query_one("SELECT 1 AS ok FROM api_keys WHERE id = %s", (kid,))

    def test_a_key_cannot_change_a_users_role(self, client, test_tenant, viewer_user):
        raw, _ = _mint(test_tenant["id"], "analyst")
        uid = viewer_user["user"]["id"] if "user" in viewer_user else viewer_user["id"]
        resp = client.patch(f"/api/v1/users/{uid}", json={"role": "admin"}, headers=_h(raw))
        assert resp.status_code == 403
        assert query_one("SELECT role FROM users WHERE id = %s", (uid,))["role"] == "viewer"

    def test_a_key_cannot_delete_the_tenant(self, client, test_tenant):
        raw, _ = _mint(test_tenant["id"], "analyst")
        resp = client.request("DELETE", "/api/v1/tenant", headers=_h(raw),
                              json={"confirm": test_tenant.get("name", "")})
        assert resp.status_code == 403
        assert query_one("SELECT 1 AS ok FROM tenants WHERE id = %s", (test_tenant["id"],))

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/v1/tenant/export"),
        ("GET", "/api/v1/users"),
        ("GET", "/api/v1/users/me"),
        ("GET", "/api/v1/service-config/services"),
        ("GET", "/api/v1/api-keys"),
        ("GET", "/api/v1/api-keys/usage"),
        ("PUT", "/api/v1/planning"),
    ])
    def test_refused_and_not_billed(self, client, test_tenant, method, path):
        raw, kid = _mint(test_tenant["id"], "analyst")
        resp = client.request(method, path, headers=_h(raw))
        assert resp.status_code == 403, f"{method} {path} answered {resp.status_code}"
        assert _calls(test_tenant["id"], kid) == 0


# ── Outward actions need a verified admin on the tenant ──────────────────────

class TestOutwardActionsNeedAVerifiedAdmin:
    def test_refused_when_no_admin_is_verified(self, client, test_tenant):
        raw, _ = _mint(test_tenant["id"], "analyst")
        resp = client.post("/api/v1/inventory/alerts/send-now?session_id=none",
                           headers=_h(raw))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "api_key_tenant_unverified"

    def test_passes_the_gate_once_an_admin_is_verified(self, client, test_tenant, registered_user):
        raw, _ = _mint(test_tenant["id"], "analyst")
        resp = client.post("/api/v1/inventory/alerts/send-now?session_id=none",
                           headers=_h(raw))
        assert resp.json().get("error_code") != "api_key_tenant_unverified"


# ── Metering ─────────────────────────────────────────────────────────────────

class TestMetering:
    def test_each_call_is_counted_once_per_key_and_day(self, client, test_tenant):
        raw, kid = _mint(test_tenant["id"], "viewer")
        for _ in range(3):
            assert client.get("/api/v1/inventory/stock", headers=_h(raw)).status_code == 200
        row = query_one(
            "SELECT day, calls, key_name FROM api_usage_daily WHERE api_key_id = %s", (kid,))
        assert row["calls"] == 3
        assert row["day"] == datetime.now(timezone.utc).date()
        assert row["key_name"] == "key-viewer"

    def test_a_jwt_call_is_not_metered(self, client, test_tenant, auth_headers):
        client.get("/api/v1/inventory/stock", headers=auth_headers)
        assert _calls(test_tenant["id"]) == 0

    def test_concurrent_calls_all_count(self, test_tenant):
        """The upsert increments under the row lock: no lost updates."""
        _raw, kid = _mint(test_tenant["id"], "viewer")
        threads = [threading.Thread(target=api_key_auth.meter,
                                    args=(kid, test_tenant["id"], "k")) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert _calls(test_tenant["id"], kid) == 20

    def test_a_metering_failure_does_not_fail_the_call_but_logs_error(
            self, client, test_tenant, monkeypatch, caplog):
        raw, kid = _mint(test_tenant["id"], "viewer")
        real = api_key_auth.execute

        def broken(sql, *a, **k):
            if "api_usage_daily" in sql:
                raise RuntimeError("meter store down")
            return real(sql, *a, **k)

        monkeypatch.setattr(api_key_auth, "execute", broken)
        with caplog.at_level(logging.ERROR, logger="backend.auth.api_key_auth"):
            resp = client.get("/api/v1/inventory/stock", headers=_h(raw))
        assert resp.status_code == 200
        assert _calls(test_tenant["id"], kid) == 0
        assert any("UNMETERED" in r.getMessage() for r in caplog.records)

    def test_daily_ceiling_still_enforced_and_refusals_not_billed(
            self, client, test_tenant, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "testing_mode", False)
        execute("""UPDATE tenants SET quota = quota || '{"max_api_calls_per_day": 2}'::jsonb
                    WHERE id = %s""", (test_tenant["id"],))
        raw, kid = _mint(test_tenant["id"], "viewer")
        try:
            codes = [client.get("/api/v1/inventory/stock", headers=_h(raw)).status_code
                     for _ in range(3)]
        finally:
            execute("DELETE FROM auth_rate_events WHERE key IN (%s, %s)",
                    (f"apikey:{kid}", f"apikeyday:{kid}"))
        assert codes == [200, 200, 429]
        assert _calls(test_tenant["id"], kid) == 2


# ── Usage endpoint ───────────────────────────────────────────────────────────

class TestUsageEndpoint:
    def test_admin_sees_the_month_by_day_and_by_key(self, client, test_tenant, auth_headers):
        raw_a, a = _mint(test_tenant["id"], "viewer", "bi")
        raw_b, b = _mint(test_tenant["id"], "analyst", "erp")
        for _ in range(2):
            client.get("/api/v1/inventory/stock", headers=_h(raw_a))
        client.get("/api/v1/inventory/stock", headers=_h(raw_b))
        # A revoked key's calls are still part of the month.
        execute("DELETE FROM api_keys WHERE id = %s", (b,))

        resp = client.get("/api/v1/api-keys/usage", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        today = datetime.now(timezone.utc).date()
        assert data["month"] == today.strftime("%Y-%m")
        assert data["total"] == 3 == _calls(test_tenant["id"])
        assert data["today"] == 3
        assert len(data["by_day"]) == today.day
        assert data["by_day"][-1] == {"day": today.isoformat(), "calls": 3}
        by_key = {k["api_key_id"]: k for k in data["by_key"]}
        assert by_key[a]["calls"] == 2 and by_key[a]["active"] is True
        assert by_key[a]["scope"] == "read"
        assert by_key[b]["calls"] == 1 and by_key[b]["active"] is False
        assert by_key[b]["name"] == "erp"
        assert data["limits"]["per_minute_per_key"] == 120

    def test_another_tenants_calls_are_not_shown(self, client, test_tenant, auth_headers):
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:10]}")
        try:
            raw, _ = _mint(other["id"], "viewer")
            client.get("/api/v1/inventory/stock", headers=_h(raw))
            data = client.get("/api/v1/api-keys/usage", headers=auth_headers).json()["data"]
            assert data["total"] == 0 and data["by_key"] == []
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))

    def test_analyst_is_refused(self, client, analyst_headers):
        assert client.get("/api/v1/api-keys/usage", headers=analyst_headers).status_code == 403

    def test_bad_month_is_422(self, client, auth_headers):
        resp = client.get("/api/v1/api-keys/usage?month=2026-13", headers=auth_headers)
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "invalid_month"

    def test_a_past_month_lists_every_day(self, client, auth_headers):
        data = client.get("/api/v1/api-keys/usage?month=2026-02",
                          headers=auth_headers).json()["data"]
        assert len(data["by_day"]) == 28 and data["total"] == 0
