"""
Integration tests for the Configuration page endpoints:
  - GET  /api/v1/models
  - GET  /api/v1/me/preferences
  - PATCH /api/v1/me/preferences
  - GET  /api/v1/me/activity
  - GET  /api/v1/me/activity/action-types
  - PATCH /api/v1/users/me  (profile update)
"""

import pytest
from uuid import uuid4


# ── self-contained fixtures (bypass conftest auth_headers which uses .local domain) ──

@pytest.fixture
def cfg_tenant():
    from backend.tenants.service import create_tenant
    from backend.db.connection import execute
    name = f"cfg-{uuid4().hex[:8]}"
    t = create_tenant(name)
    yield t
    execute("DELETE FROM tenants WHERE id = %s", (t["id"],))


@pytest.fixture
def cfg_user(cfg_tenant):
    from backend.users import service as svc
    email = f"cfguser-{uuid4().hex[:8]}@example.com"
    u = svc.create_user(cfg_tenant["id"], email, "Cfg123!", "admin", "Config Tester")
    svc.mark_verified(cfg_tenant["id"], u["id"])
    return {"user": u, "tenant": cfg_tenant, "email": email, "password": "Cfg123!"}


@pytest.fixture
def cfg_headers(client, cfg_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": cfg_user["email"],
        "password": cfg_user["password"],
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ── /models ───────────────────────────────────────────────────────────────────

def test_list_models_no_auth(client):
    """GET /models is public — no token needed."""
    r = client.get("/api/v1/models")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert isinstance(data, list)
    assert len(data) == 9
    names = {m["name"] for m in data}
    # `global_lgbm` is fitted ONCE across the whole catalogue rather than once
    # per SKU, which is why it is its own category below.
    assert {"global_lgbm", "lightgbm", "xgboost", "prophet", "arima", "sarimax",
            "ets", "croston", "lstm"} == names
    for m in data:
        assert "name"        in m
        assert "category"    in m
        assert "status"      in m
        assert "description" in m


def test_model_categories(client):
    r = client.get("/api/v1/models")
    data = r.json()["data"]
    cats = {m["category"] for m in data}
    assert cats == {"Global", "ML", "Statistical", "Deep Learning"}


def test_catalogue_matches_the_engine(client):
    """
    The catalogue and the engine must not drift apart.

    This endpoint is a hand-maintained list; the engine has its own. A model
    present in one and absent from the other is either unselectable in the UI or
    selectable and untrainable — and the two hardcoded assertions above only
    catch it when someone remembers to update them.
    """
    from forecasting_core.models.factory import ModelFactory

    names = {m["name"] for m in client.get("/api/v1/models").json()["data"]}
    assert names == set(ModelFactory.available_models())


def test_model_statuses(client):
    r = client.get("/api/v1/models")
    data = r.json()["data"]
    statuses = {m["status"] for m in data}
    assert statuses <= {"available", "beta", "disabled"}


# ── /me/preferences ───────────────────────────────────────────────────────────

def test_get_preferences_defaults(client, cfg_headers):
    """First call for a new user returns valid defaults."""
    r = client.get("/api/v1/me/preferences", headers=cfg_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["language"] in ("es", "en")
    assert data["theme"]    in ("dark", "light")


def test_update_preferences_language_en(client, cfg_headers):
    r = client.patch("/api/v1/me/preferences", json={"language": "en"}, headers=cfg_headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["language"] == "en"


def test_update_preferences_language_es(client, cfg_headers):
    r = client.patch("/api/v1/me/preferences", json={"language": "es"}, headers=cfg_headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["language"] == "es"


def test_update_preferences_theme_light(client, cfg_headers):
    r = client.patch("/api/v1/me/preferences", json={"theme": "light"}, headers=cfg_headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["theme"] == "light"


def test_update_preferences_theme_dark(client, cfg_headers):
    r = client.patch("/api/v1/me/preferences", json={"theme": "dark"}, headers=cfg_headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["theme"] == "dark"


def test_update_preferences_invalid_language(client, cfg_headers):
    r = client.patch("/api/v1/me/preferences", json={"language": "fr"}, headers=cfg_headers)
    assert r.status_code == 400


def test_update_preferences_invalid_theme(client, cfg_headers):
    r = client.patch("/api/v1/me/preferences", json={"theme": "solarized"}, headers=cfg_headers)
    assert r.status_code == 400


def test_preferences_persist_across_calls(client, cfg_headers):
    """PATCH then GET confirms the value stuck."""
    client.patch("/api/v1/me/preferences", json={"language": "en", "theme": "light"}, headers=cfg_headers)
    r = client.get("/api/v1/me/preferences", headers=cfg_headers)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["language"] == "en"
    assert d["theme"]    == "light"


def test_preferences_partial_update_preserves_other(client, cfg_headers):
    """Patching only language should not reset theme."""
    client.patch("/api/v1/me/preferences", json={"language": "es", "theme": "dark"}, headers=cfg_headers)
    client.patch("/api/v1/me/preferences", json={"language": "en"}, headers=cfg_headers)
    r = client.get("/api/v1/me/preferences", headers=cfg_headers)
    d = r.json()["data"]
    assert d["language"] == "en"
    assert d["theme"]    == "dark"  # unchanged


def test_preferences_requires_auth(client):
    r = client.get("/api/v1/me/preferences")
    assert r.status_code in (401, 403)


# ── /me/activity ─────────────────────────────────────────────────────────────

def test_get_activity_returns_structure(client, cfg_headers):
    r = client.get("/api/v1/me/activity", headers=cfg_headers)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)
    assert isinstance(body["total"], int)


def test_get_activity_pagination(client, cfg_headers):
    r = client.get("/api/v1/me/activity?limit=5&offset=0", headers=cfg_headers)
    assert r.status_code == 200
    assert len(r.json()["data"]["items"]) <= 5


def test_get_activity_action_filter(client, cfg_headers):
    r = client.get("/api/v1/me/activity?action=nonexistent_action", headers=cfg_headers)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["items"] == []
    assert body["total"] == 0


def test_get_activity_invalid_limit(client, cfg_headers):
    r = client.get("/api/v1/me/activity?limit=0", headers=cfg_headers)
    assert r.status_code == 422  # ge=1 constraint


def test_get_activity_action_types_is_list(client, cfg_headers):
    r = client.get("/api/v1/me/activity/action-types", headers=cfg_headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["data"], list)


def test_activity_requires_auth(client):
    r = client.get("/api/v1/me/activity")
    assert r.status_code in (401, 403)


# ── /users/me PATCH ──────────────────────────────────────────────────────────

def test_update_profile_name(client, cfg_headers):
    r = client.patch("/api/v1/users/me", json={"full_name": "Updated Name"}, headers=cfg_headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["full_name"] == "Updated Name"


def test_update_profile_name_trimmed(client, cfg_headers):
    r = client.patch("/api/v1/users/me", json={"full_name": "  Trimmed  "}, headers=cfg_headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["full_name"] == "Trimmed"


def test_update_profile_blank_name_rejected(client, cfg_headers):
    r = client.patch("/api/v1/users/me", json={"full_name": "   "}, headers=cfg_headers)
    assert r.status_code == 400


def test_update_profile_no_fields_returns_current(client, cfg_headers):
    r = client.patch("/api/v1/users/me", json={}, headers=cfg_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert "email" in data
    assert "role"  in data
