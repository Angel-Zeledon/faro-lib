"""API access, MCP access and the WhatsApp bot are paid-only (owner, 2026-10-05).

`free` has none of the three; `demo` has the API and MCP but not the bot; `paid` and `corporate` have all.
Every refusal is the same structured 403 — error_code `plan_feature_locked`,
error_params {feature, required_plan} — raised by one function,
`entitlements.service.ensure_feature`. These tests turn testing_mode off
themselves: it bypasses the check, and the local .env has it on.
"""

from uuid import uuid4

import pytest

from backend.auth.api_key_auth import KEY_PREFIX, hash_key
from backend.db.connection import execute, query_one

MCP = "/api/v1/mcp"


def _set_tier(tenant_id: str, tier: str) -> None:
    execute("UPDATE tenants SET tier = %s WHERE id = %s", (tier, tenant_id))


def _key_count(tenant_id: str) -> int:
    return query_one("SELECT COUNT(*) AS c FROM api_keys WHERE tenant_id = %s",
                     (tenant_id,))["c"]


def _mint(tenant_id: str, role: str = "analyst") -> str:
    """A key row written directly: a free tenant can no longer mint one through
    the endpoint, but old keys from before the change still exist in the table."""
    raw = KEY_PREFIX + uuid4().hex + uuid4().hex
    execute(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4)
           VALUES (gen_random_uuid()::text, %s, 'old-key', %s, %s, 'usr_test', %s)""",
        (tenant_id, hash_key(raw), role, raw[-4:]),
    )
    return raw


def _bearer(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


def _assert_locked(resp, feature: str) -> None:
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["error_code"] == "plan_feature_locked"
    assert body["error_params"] == {"feature": feature, "required_plan": "paid"}


@pytest.fixture
def real_limits(monkeypatch):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)


# ── API keys: creation ───────────────────────────────────────────────────────

@pytest.mark.parametrize("tier", ["free"])
def test_key_creation_is_refused_without_the_api_and_nothing_is_written(
    tier, real_limits, make_tenant_user_headers, client,
):
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, tier)
    resp = client.post("/api/v1/api-keys", json={"name": "erp", "role": "viewer"},
                       headers=headers)
    _assert_locked(resp, "api")
    assert _key_count(tenant_id) == 0


@pytest.mark.parametrize("tier", ["paid", "corporate"])
def test_key_creation_is_allowed_on_paid_and_corporate(
    tier, real_limits, make_tenant_user_headers, client,
):
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, tier)
    resp = client.post("/api/v1/api-keys", json={"name": "erp", "role": "viewer"},
                       headers=headers)
    assert resp.status_code in (200, 201), resp.text
    assert _key_count(tenant_id) == 1


def test_key_creation_permission_pair_is_unchanged(
    real_limits, make_tenant_user_headers, client,
):
    """Viewer denied with state unchanged, analyst allowed — on a paid tenant,
    so the plan is not what is being measured."""
    v_headers, tenant_id = make_tenant_user_headers(role="viewer", return_tenant_id=True)
    _set_tier(tenant_id, "paid")
    denied = client.post("/api/v1/api-keys", json={"name": "k", "role": "viewer"},
                         headers=v_headers)
    assert denied.status_code == 403
    assert denied.json().get("error_code") != "plan_feature_locked"
    assert _key_count(tenant_id) == 0

    a_headers, a_tenant = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(a_tenant, "paid")
    ok = client.post("/api/v1/api-keys", json={"name": "k", "role": "viewer"},
                     headers=a_headers)
    assert ok.status_code in (200, 201), ok.text
    assert _key_count(a_tenant) == 1


# ── API keys: authenticating with one that already exists ────────────────────

def test_an_existing_key_of_a_free_tenant_stops_working_and_says_why(
    real_limits, test_tenant, client,
):
    _set_tier(test_tenant["id"], "free")
    raw = _mint(test_tenant["id"])
    resp = client.get("/api/v1/inventory/stock", headers=_bearer(raw))
    _assert_locked(resp, "api")
    assert "plan" in resp.json()["detail"].lower()
    # Refused before metering: the call was neither counted nor billed.
    assert query_one("SELECT COALESCE(SUM(calls), 0) AS n FROM api_usage_daily "
                     "WHERE tenant_id = %s", (test_tenant["id"],))["n"] == 0
    # The key itself is untouched — nothing is deleted, it works again on paid.
    assert _key_count(test_tenant["id"]) == 1
    _set_tier(test_tenant["id"], "paid")
    assert client.get("/api/v1/inventory/stock", headers=_bearer(raw)).status_code == 200


@pytest.mark.parametrize("tier", ["paid", "corporate"])
def test_an_existing_key_works_on_paid_and_corporate(
    tier, real_limits, test_tenant, client,
):
    _set_tier(test_tenant["id"], tier)
    raw = _mint(test_tenant["id"])
    assert client.get("/api/v1/inventory/stock", headers=_bearer(raw)).status_code == 200


def test_a_bad_key_is_still_a_plain_401_not_a_plan_error(real_limits, client):
    resp = client.get("/api/v1/inventory/stock",
                      headers=_bearer(KEY_PREFIX + "does-not-exist"))
    assert resp.status_code == 401


# ── MCP ──────────────────────────────────────────────────────────────────────

_RPC = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def test_mcp_is_refused_for_a_free_tenants_key_naming_mcp(
    real_limits, test_tenant, client,
):
    _set_tier(test_tenant["id"], "free")
    raw = _mint(test_tenant["id"])
    _assert_locked(client.post(MCP, json=_RPC, headers=_bearer(raw)), "mcp")


def test_mcp_is_refused_for_a_free_tenants_browser_session(
    real_limits, make_tenant_user_headers, client,
):
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _assert_locked(client.post(MCP, json=_RPC, headers=headers), "mcp")


def test_mcp_is_refused_when_the_plan_has_api_but_the_override_removes_mcp(
    real_limits, test_tenant, client,
):
    _set_tier(test_tenant["id"], "paid")
    execute("""UPDATE tenants SET quota = '{"mcp_access": false}'::jsonb WHERE id = %s""",
            (test_tenant["id"],))
    raw = _mint(test_tenant["id"])
    assert client.get("/api/v1/inventory/stock", headers=_bearer(raw)).status_code == 200
    _assert_locked(client.post(MCP, json=_RPC, headers=_bearer(raw)), "mcp")


@pytest.mark.parametrize("tier", ["paid", "corporate"])
def test_mcp_is_allowed_on_paid_and_corporate(tier, real_limits, test_tenant, client):
    _set_tier(test_tenant["id"], tier)
    raw = _mint(test_tenant["id"])
    resp = client.post(MCP, json=_RPC, headers=_bearer(raw))
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["tools"]


# ── GET /entitlements ────────────────────────────────────────────────────────

@pytest.mark.parametrize("tier,want", [
    ("free", {"api": False, "mcp": False, "whatsapp_bot": False}),
    # The demo has the API and MCP, never the WhatsApp bot.
    ("demo", {"api": True, "mcp": True, "whatsapp_bot": False}),
    ("paid", {"api": True, "mcp": True, "whatsapp_bot": True}),
    ("corporate", {"api": True, "mcp": True, "whatsapp_bot": True}),
])
def test_entitlements_endpoint_reports_tier_and_features(
    tier, want, make_tenant_user_headers, client,
):
    # Reports the plan's truth even with testing_mode on: it is a read, and the
    # UI must be able to render the locked state.
    headers, tenant_id = make_tenant_user_headers(role="viewer", return_tenant_id=True)
    _set_tier(tenant_id, tier)
    data = client.get("/api/v1/entitlements", headers=headers).json()["data"]
    assert data["tier"] == tier
    assert data["features"] == want
    # Existing fields keep their shape; the feature booleans are not in limits.
    assert {"limits", "usage", "contact", "trial", "read_only"} <= set(data)
    assert "api_access" not in data["limits"]


# ── Outbound WhatsApp alerts ─────────────────────────────────────────────────

def test_a_gated_whatsapp_send_is_skipped_for_free_and_attempted_for_paid(
    real_limits, test_tenant, monkeypatch,
):
    from backend.notifications import whatsapp as wa
    attempts = []
    monkeypatch.setattr(wa, "is_configured", lambda tid=None: True)
    monkeypatch.setattr(wa, "_send", lambda *a, **k: attempts.append(a) or "SM1")

    _set_tier(test_tenant["id"], "free")
    assert wa.send_whatsapp("+573001112233", "alert", tenant_id=test_tenant["id"],
                            plan_gated=True) is False
    assert attempts == []
    assert wa.failure_reason(test_tenant["id"]) == "plan_feature_locked"

    _set_tier(test_tenant["id"], "paid")
    assert wa.send_whatsapp("+573001112233", "alert", tenant_id=test_tenant["id"],
                            plan_gated=True) is True
    assert len(attempts) == 1
