import pytest
from datetime import datetime, timedelta, timezone

from backend.entitlements.plans import Feature, PLAN_CATALOG
from backend.entitlements import service as ent


@pytest.mark.offline
def test_catalog_has_three_plans():
    assert set(PLAN_CATALOG) == {"starter", "professional", "enterprise"}


@pytest.mark.offline
def test_each_tier_is_a_superset_of_the_lower():
    starter = PLAN_CATALOG["starter"].features
    pro = PLAN_CATALOG["professional"].features
    ent = PLAN_CATALOG["enterprise"].features
    assert starter <= pro <= ent


@pytest.mark.offline
def test_starter_excludes_paid_features_but_includes_core():
    starter = PLAN_CATALOG["starter"].features
    assert Feature.SEMAPHORE in starter
    assert Feature.EMAIL_ALERTS in starter
    assert Feature.WHATSAPP_ALERTS not in starter
    assert Feature.API_ACCESS not in starter


@pytest.mark.offline
def test_enterprise_only_features():
    pro = PLAN_CATALOG["professional"].features
    assert Feature.API_ACCESS not in pro
    assert Feature.BOM not in pro
    assert Feature.WEBHOOKS not in pro
    ent = PLAN_CATALOG["enterprise"].features
    assert {Feature.API_ACCESS, Feature.BOM, Feature.WEBHOOKS} <= ent


@pytest.mark.offline
def test_numeric_limits():
    assert PLAN_CATALOG["starter"].max_skus == 500
    assert PLAN_CATALOG["professional"].max_skus == 5000
    assert PLAN_CATALOG["enterprise"].max_skus is None
    assert PLAN_CATALOG["starter"].max_users == 2
    assert PLAN_CATALOG["starter"].max_locations == 1


def _tenant(plan="starter", trial_ends_at=None, quota=None):
    return {"id": "ten_x", "plan": plan, "trial_ends_at": trial_ends_at,
            "quota": quota or {}}


@pytest.mark.offline
def test_has_feature_by_plan():
    assert ent.has_feature(_tenant("professional"), Feature.WHATSAPP_ALERTS)
    assert not ent.has_feature(_tenant("starter"), Feature.WHATSAPP_ALERTS)


@pytest.mark.offline
def test_unknown_plan_falls_back_to_starter():
    assert ent.get_plan_def("garbage").max_skus == 500


@pytest.mark.offline
def test_tenant_limits_merge_override():
    limits = ent.tenant_limits(_tenant("starter", quota={"max_skus": 999}))
    assert limits["max_skus"] == 999          # override wins
    assert limits["max_users"] == 2           # catalog default preserved


@pytest.mark.offline
def test_trial_state_and_read_only():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert ent.trial_state(_tenant(trial_ends_at=None)) == "active"
    assert ent.trial_state(_tenant(trial_ends_at=future)) == "trialing"
    assert ent.trial_state(_tenant(trial_ends_at=past)) == "expired"
    assert ent.is_read_only(_tenant(trial_ends_at=past)) is True
    assert ent.is_read_only(_tenant(trial_ends_at=future)) is False


@pytest.mark.offline
def test_required_plans_for():
    assert ent.required_plans_for(Feature.WHATSAPP_ALERTS) == ["professional", "enterprise"]
    assert ent.required_plans_for(Feature.API_ACCESS) == ["enterprise"]


from backend.tenants import service as tenant_svc
from backend.db.connection import execute as _db_execute


def test_create_tenant_starts_on_starter_trial(client):  # client fixture ensures migrations ran
    t = tenant_svc.create_tenant("Acme Trial Co")
    try:
        assert t["plan"] == "starter"
        assert t["trial_ends_at"] is not None
        ends = t["trial_ends_at"]
        delta = ends - datetime.now(timezone.utc)
        assert timedelta(days=13) < delta < timedelta(days=15)
    finally:
        _db_execute("DELETE FROM tenants WHERE id = %s", (t["id"],))


from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _mini_app():
    from backend.auth.guards import CurrentUser
    from backend.entitlements.guards import require_feature
    app = FastAPI()

    @app.get("/whatsapp-thing")
    def thing(user: CurrentUser = Depends(require_feature(Feature.WHATSAPP_ALERTS))):
        return {"ok": True}

    return app


def test_require_feature_blocks_starter(monkeypatch, make_tenant_user_headers):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers = make_tenant_user_headers(plan="starter")
    r = TestClient(_mini_app()).get("/whatsapp-thing", headers=headers)
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["code"] == "PLAN_UPGRADE_REQUIRED"
    assert body["feature"] == "whatsapp_alerts"
    assert body["current_plan"] == "starter"
    assert body["required_plans"] == ["professional", "enterprise"]


def test_require_feature_allows_professional(monkeypatch, make_tenant_user_headers):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers = make_tenant_user_headers(plan="professional")
    r = TestClient(_mini_app()).get("/whatsapp-thing", headers=headers)
    assert r.status_code == 200


def test_user_limit_on_starter(monkeypatch, make_tenant_user_headers):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="admin", return_tenant_id=True
    )
    client = TestClient(app)
    # tenant already has 1 admin user; Starter allows 2 → first create OK
    r1 = client.post("/api/v1/users", headers=headers,
                     json={"email": "u2@x.com", "full_name": "U2",
                           "password": "pw12345678", "role": "analyst"})
    assert r1.status_code in (200, 201)

    before = query_one("SELECT COUNT(*) AS c FROM users WHERE tenant_id=%s",
                       (tenant_id,))["c"]
    # 3rd user exceeds Starter's max_users=2 → blocked, count unchanged
    r2 = client.post("/api/v1/users", headers=headers,
                     json={"email": "u3@x.com", "full_name": "U3",
                           "password": "pw12345678", "role": "analyst"})
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "PLAN_LIMIT_REACHED"
    after = query_one("SELECT COUNT(*) AS c FROM users WHERE tenant_id=%s",
                      (tenant_id,))["c"]
    assert after == before


def test_bulk_import_blocks_new_warehouses_beyond_max_locations(
    monkeypatch, make_tenant_user_headers, client,
):
    """
    Regression for the max_locations bypass: POST /bulk used to create every
    NEW warehouse name it saw (via svc.upsert_stock -> _ensure_warehouse) with
    no limit check at all, so a Starter tenant (max_locations=1) could get
    unlimited warehouses through a CSV import even though POST /warehouses
    enforced the same limit correctly. A CSV introducing 2 distinct new
    warehouse names must be blocked before anything is written — the
    warehouses AND inventory_stock row counts must be unchanged after.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="admin", return_tenant_id=True
    )

    wh_before = query_one(
        "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    stock_before = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert wh_before == 0  # fresh tenant, no warehouse auto-seeded

    csv_text = (
        "sku,current_stock,warehouse\n"
        "BULKWH-A,10,Norte\n"
        "BULKWH-B,20,Sur\n"
    )
    r = client.post(
        "/api/v1/inventory/bulk",
        files={"file": ("stock.csv", csv_text.encode("utf-8"), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["code"] == "PLAN_LIMIT_REACHED"
    assert body["limit"] == "max_locations"

    wh_after = query_one(
        "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    stock_after = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert wh_after == wh_before          # no warehouse row leaked through
    assert stock_after == stock_before    # no partial stock insert either


def test_put_stock_blocks_new_warehouse_beyond_max_locations(
    monkeypatch, make_tenant_user_headers, client,
):
    """Same bypass, direct PUT /stock/{sku} path: a Starter tenant already at
    its 1-warehouse cap must not get a 2nd warehouse auto-created by writing
    stock for a SKU tagged with a brand-new warehouse name."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="admin", return_tenant_id=True
    )

    # First write establishes warehouse #1 ("principal"), consuming the cap.
    r0 = client.put(
        "/api/v1/inventory/stock/PUTWH-1",
        json={"current_stock": 5},
        headers=headers,
    )
    assert r0.status_code == 200

    wh_before = query_one(
        "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert wh_before == 1

    # Second write targets a NEW warehouse name -> would be warehouse #2,
    # exceeding Starter's max_locations=1.
    r1 = client.put(
        "/api/v1/inventory/stock/PUTWH-2",
        json={"current_stock": 7, "warehouse": "Norte"},
        headers=headers,
    )
    assert r1.status_code == 403
    assert r1.json()["detail"]["code"] == "PLAN_LIMIT_REACHED"
    assert r1.json()["detail"]["limit"] == "max_locations"

    wh_after = query_one(
        "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert wh_after == wh_before
    stock_row = query_one(
        "SELECT 1 FROM inventory_stock WHERE tenant_id=%s AND sku='PUTWH-2'", (tenant_id,)
    )
    assert stock_row is None   # blocked write must not have created the stock row


def test_patch_stock_blocks_new_warehouse_beyond_max_locations(
    monkeypatch, make_tenant_user_headers, client,
):
    """
    Regression for the 4th max_locations bypass path: PATCH /stock/{sku} calls
    svc.get_stock(tenant_id, sku) WITHOUT a warehouse filter, so its 404 check
    passes as long as the SKU exists in ANY warehouse. If the PATCH body then
    sets a DIFFERENT warehouse, svc.upsert_stock inserts a brand-new
    (tenant_id, sku, warehouse) row and auto-creates the warehouse via
    _ensure_warehouse — bypassing max_locations entirely, with no per-caller
    guard on this endpoint (unlike PUT /stock, POST /bulk and receive_po).
    The fix enforces max_locations inside upsert_stock itself so every caller
    is covered.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="admin", return_tenant_id=True
    )

    # Seed SKU X in the default warehouse -> warehouse #1 ("principal"),
    # consuming Starter's max_locations=1 cap.
    r0 = client.put(
        "/api/v1/inventory/stock/X",
        json={"current_stock": 10},
        headers=headers,
    )
    assert r0.status_code == 200

    wh_before = query_one(
        "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    stock_before = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert wh_before == 1
    assert stock_before == 1

    # PATCH the SKU into a DIFFERENT warehouse -> would be warehouse #2,
    # exceeding Starter's max_locations=1.
    r1 = client.patch(
        "/api/v1/inventory/stock/X",
        json={"warehouse": "Norte"},
        headers=headers,
    )
    assert r1.status_code == 403
    assert r1.json()["detail"]["code"] == "PLAN_LIMIT_REACHED"
    assert r1.json()["detail"]["limit"] == "max_locations"

    wh_after = query_one(
        "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    stock_after = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert wh_after == wh_before        # no new warehouse leaked through
    assert stock_after == stock_before  # no new inventory_stock row created


def test_bulk_import_blocks_new_skus_beyond_max_skus_quota_override(
    monkeypatch, make_tenant_user_headers, client,
):
    """
    max_skus enforcement, exercised through a per-tenant quota override rather
    than the full 500-row Starter catalog value — cheap to set up and doubles
    as proof that quota overrides actually take effect.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import execute, query_one, _json

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", role="admin", return_tenant_id=True
    )
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json({"max_skus": 1}), tenant_id))

    stock_before = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_before == 0

    csv_text = (
        "sku,current_stock\n"
        "QUOTASKU-A,10\n"
        "QUOTASKU-B,20\n"
    )
    r = client.post(
        "/api/v1/inventory/bulk",
        files={"file": ("stock.csv", csv_text.encode("utf-8"), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["code"] == "PLAN_LIMIT_REACHED"
    assert body["limit"] == "max_skus"

    stock_after = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_after == stock_before   # blocked import created nothing


def test_expired_trial_blocks_mutation_but_allows_read(
    monkeypatch, make_tenant_user_headers
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(
        plan="starter", expired_trial=True, return_tenant_id=True
    )
    client = TestClient(app)

    # read still works
    assert client.get("/api/v1/sessions", headers=headers).status_code == 200

    # mutation blocked with TRIAL_EXPIRED, and no session row created
    before = query_one(
        "SELECT COUNT(*) AS c FROM sessions WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    r = client.post("/api/v1/sessions", headers=headers, json={"name": "X"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "TRIAL_EXPIRED"
    after = query_one(
        "SELECT COUNT(*) AS c FROM sessions WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert after == before
