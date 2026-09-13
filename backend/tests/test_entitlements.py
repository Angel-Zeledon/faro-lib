"""What a tenant may do, now that a tier only decides how much.

The old catalog these tests were written against is gone: no starter /
professional / enterprise, no feature sets, no `require_feature`. Two tiers
remain and both ship every feature — `free` with short ceilings, `paid` with
none — so what is tested here is the machinery underneath: limit resolution,
per-tenant `quota` overrides, the trial clock, and a few guards that the
FEATURE walls really did come down and cannot creep back in.

The tier-by-tier ceilings themselves live in `test_tiers.py`.
"""

import pytest
from datetime import datetime, timedelta, timezone

from backend.entitlements.plans import PLANS
from backend.entitlements import service as ent


@pytest.mark.offline
def test_a_paying_tenant_counts_nothing():
    """SKUs, users, warehouses and saved sessions are the four numbers a
    customer used to buy more of. On the paid tier they are unlimited, and this
    test is what would notice a ceiling quietly appearing there."""
    paid = PLANS["paid"]
    assert paid.max_skus is None
    assert paid.max_users is None
    assert paid.max_locations is None
    assert paid.max_sessions is None


@pytest.mark.offline
def test_the_infrastructure_ceiling_is_not_for_sale():
    """Concurrent jobs protect the server, not a price list, so both tiers get
    the same number. Upload size is the one commercial ceiling that survives on
    paid, because an upload is read into memory before it is anything else."""
    assert PLANS["free"].max_concurrent_jobs == PLANS["paid"].max_concurrent_jobs == 8
    assert PLANS["paid"].max_dataset_size_mb == 2000


def _tenant(trial_ends_at=None, quota=None, tier="paid"):
    """A tenant dict for the pure-function tests. Defaults to `paid` so these
    exercise limit MERGING rather than re-asserting the free ceilings, which
    test_tiers.py owns."""
    return {"id": "ten_x", "tier": tier,
            "trial_ends_at": trial_ends_at, "quota": quota or {}}


@pytest.mark.offline
def test_tenant_limits_merge_override():
    limits = ent.tenant_limits(_tenant(quota={"max_skus": 999}))
    assert limits["max_skus"] == 999          # the agreement with one customer
    assert limits["max_users"] is None        # everything else stays unlimited
    assert limits["max_dataset_size_mb"] == 2000


@pytest.mark.offline
def test_trial_state_and_read_only():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert ent.trial_state(_tenant(trial_ends_at=None)) == "active"
    assert ent.trial_state(_tenant(trial_ends_at=future)) == "trialing"
    assert ent.trial_state(_tenant(trial_ends_at=past)) == "expired"
    assert ent.is_read_only(_tenant(trial_ends_at=past)) is True
    assert ent.is_read_only(_tenant(trial_ends_at=future)) is False


from backend.tenants import service as tenant_svc
from backend.db.connection import execute as _db_execute, _json as _db_json


def _set_quota(tenant_id: str, quota: dict) -> None:
    """Narrow one tenant the only way that is left: an explicit agreement."""
    _db_execute("UPDATE tenants SET quota = %s WHERE id = %s",
                (_db_json(quota), tenant_id))


def test_create_tenant_starts_free_and_not_on_a_countdown(client):  # client fixture ensures migrations ran
    """Signup used to open a 14-day trial that ended in a read-only account.
    The free tier replaced it: a permanent home with short ceilings, so a new
    tenant lands on `free` with nothing expiring over it."""
    t = tenant_svc.create_tenant("Acme Trial Co")
    try:
        assert t["tier"] == "free"
        assert t["trial_ends_at"] is None
    finally:
        _db_execute("DELETE FROM tenants WHERE id = %s", (t["id"],))


from fastapi import FastAPI  # noqa: F401  (kept for the TestClient import below)
from fastapi.testclient import TestClient


def test_user_limit_from_a_quota_override(monkeypatch, make_tenant_user_headers):
    """Nothing caps users by default any more, so this exercises the override —
    the mechanism a real agreement would use — rather than a tier."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True)
    _set_quota(tenant_id, {"max_users": 2})
    client = TestClient(app)
    # tenant already has 1 admin user; the override allows 2 -> first create OK
    r1 = client.post("/api/v1/users", headers=headers,
                     json={"email": "u2@x.com", "full_name": "U2",
                           "password": "pw12345678", "role": "analyst"})
    assert r1.status_code in (200, 201)

    before = query_one("SELECT COUNT(*) AS c FROM users WHERE tenant_id=%s",
                       (tenant_id,))["c"]
    # 3rd user exceeds max_users=2 -> blocked, count unchanged
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
    no limit check at all, so a tenant capped at one warehouse could get
    unlimited ones through a CSV import even though POST /warehouses enforced
    the same limit correctly. A CSV introducing 2 distinct new warehouse names
    must be blocked before anything is written — the warehouses AND
    inventory_stock row counts must be unchanged after.

    Nothing caps warehouses by default now, so the cap here is a quota
    override: the bypass is what is under test, not the number.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True)
    _set_quota(tenant_id, {"max_locations": 1})

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
    """Same bypass, direct PUT /stock/{sku} path: a tenant already at its
    1-warehouse cap (quota override) must not get a 2nd warehouse auto-created
    by writing stock for a SKU tagged with a brand-new warehouse name."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True)
    _set_quota(tenant_id, {"max_locations": 1})

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
    # exceeding max_locations=1.
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

    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True)
    _set_quota(tenant_id, {"max_locations": 1})

    # Seed SKU X in the default warehouse -> warehouse #1 ("principal"),
    # consuming the max_locations=1 cap.
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
    # exceeding max_locations=1.
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
    than a shipped ceiling — cheap to set up and doubles
    as proof that quota overrides actually take effect.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import execute, query_one, _json

    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True
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


def test_dataset_sync_respects_max_skus(monkeypatch, make_tenant_user_headers):
    """
    Regression: the dataset-sync path (runner.py -> sync_stock_from_dataset,
    the PRIMARY way SKUs enter Faro via Quick Start upload) had NO max_skus
    enforcement at all, unlike PUT /stock and POST /bulk. A tenant could seed
    thousands of SKUs through an upload despite a low quota.

    Calls sync_stock_from_dataset directly (its actual signature) with a
    2-new-SKU dataset against a max_skus=1 quota override, and asserts the
    pre-loop chokepoint blocks it BEFORE any row is inserted (no partial
    write) rather than silently truncating to 1 row.
    """
    import pandas as pd
    from fastapi import HTTPException
    from backend.inventory.service import sync_stock_from_dataset
    from backend.db.connection import execute, query_one, _json

    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    _, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True
    )
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json({"max_skus": 1}), tenant_id))

    stock_before = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_before == 0

    df = pd.DataFrame({
        "sku":           ["DSKU-A", "DSKU-B"],
        "fecha":         ["2026-01-01", "2026-01-01"],
        "current_stock": [10, 20],
    })

    with pytest.raises(HTTPException) as exc_info:
        sync_stock_from_dataset(tenant_id, df, group_col="sku", date_col="fecha")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "PLAN_LIMIT_REACHED"
    assert exc_info.value.detail["limit"] == "max_skus"

    stock_after = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_after <= 1
    assert stock_after == stock_before  # blocked BEFORE the loop: nothing inserted


def test_patch_stock_respects_max_skus(monkeypatch, make_tenant_user_headers, client):
    """
    Regression for the max_skus class of the max_locations PATCH bug: PATCH
    /stock/{sku} 404-checks svc.get_stock(tenant_id, sku) WITHOUT a warehouse
    filter, so as long as the SKU exists in ANY warehouse the 404 guard passes.
    If the PATCH body then targets a DIFFERENT warehouse, svc.upsert_stock
    inserts a brand-new (tenant_id, sku, warehouse) row — a class of write the
    old per-caller max_skus checks (PUT /stock, POST /bulk) never covered.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import execute, query_one, _json

    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True
    )
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json({"max_skus": 1}), tenant_id))

    # Establish the 1 SKU this tenant's quota allows.
    r0 = client.put(
        "/api/v1/inventory/stock/PSKU-1",
        json={"current_stock": 5},
        headers=headers,
    )
    assert r0.status_code == 200

    stock_before = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_before == 1

    # PATCH the SAME sku into a DIFFERENT warehouse -> a NEW (sku, warehouse)
    # row, which would be stock row #2, exceeding max_skus=1.
    r1 = client.patch(
        "/api/v1/inventory/stock/PSKU-1",
        json={"warehouse": "Sur", "current_stock": 7},
        headers=headers,
    )
    assert r1.status_code == 403
    assert r1.json()["detail"]["code"] == "PLAN_LIMIT_REACHED"
    assert r1.json()["detail"]["limit"] == "max_skus"

    stock_after = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_after == stock_before  # blocked write created no new row


def test_receive_po_respects_max_skus_atomically(monkeypatch, make_tenant_user_headers):
    """
    Regression: receive_po (backend/inventory/reception_service.py) writes in
    multiple auto-committed steps — step 1 accumulates received_qty on every
    PO line, step 2 upserts inventory_stock per line (creating a new row when
    the (sku, warehouse) pair doesn't exist yet), step 3 updates the PO header
    status. Only a pre-loop max_locations check existed; there was no max_skus
    pre-check, so a tenant at its max_skus cap receiving a PO into a
    warehouse it already has (so max_locations doesn't fire) but for a NEW
    (sku, warehouse) pair could 403 mid-loop, after step 1 already committed
    received_qty for every line — a partial write that leaves PO items marked
    received without matching stock, and the header status never updated.

    This asserts both directions: the capped tenant gets a 403 with NOTHING
    written (atomic), and an entitled tenant (quota override raised) succeeds
    and actually writes stock — proving the gate toggles rather than reception
    being unconditionally broken.
    """
    from uuid import uuid4
    from backend.db.connection import execute, query_one, _json
    from backend.inventory import service as inv_svc
    from backend.inventory import roi_service
    from backend.inventory import reception_service as rec_svc
    from fastapi import HTTPException

    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    _, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True
    )
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json({"max_skus": 1}), tenant_id))

    # One existing stock row in "principal" -> tenant is already at its max_skus=1 cap.
    existing_sku = f"RCV_CAP_{uuid4().hex[:8]}"
    inv_svc.upsert_stock(tenant_id, existing_sku, {"current_stock": 10, "warehouse": "principal"})

    stock_count_before = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_count_before == 1

    # PO for a DIFFERENT sku, destined for "principal" (a warehouse that already
    # exists, so the max_locations pre-check does NOT fire) -> a NEW (sku,
    # warehouse) stock row, which would push stock count to 2, over the cap.
    new_sku = f"RCV_NEW_{uuid4().hex[:8]}"
    po = roi_service.log_po_generation(tenant_id, "sess-test", [{
        "sku": new_sku, "final_qty": 20, "status": "approved",
        "warehouse": "principal",
    }])

    with pytest.raises(HTTPException) as exc_info:
        rec_svc.receive_po(tenant_id, po["id"], user_id="u1")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "PLAN_LIMIT_REACHED"
    assert exc_info.value.detail["limit"] == "max_skus"

    # Nothing was written: no new stock row, PO item's received_qty untouched,
    # PO header status untouched.
    stock_count_after = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_count_after == stock_count_before

    po_item = query_one(
        "SELECT received_qty FROM inventory_po_items WHERE po_log_id=%s AND sku=%s",
        (po["id"], new_sku),
    )
    assert po_item["received_qty"] in (None, 0)

    po_log = query_one(
        "SELECT reception_status FROM inventory_po_log WHERE id=%s", (po["id"],)
    )
    assert po_log["reception_status"] == "pending"

    # Entitled case: raise the quota override so the same reception succeeds
    # and actually writes stock -- proving the gate toggles.
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json({"max_skus": 10}), tenant_id))

    result = rec_svc.receive_po(tenant_id, po["id"], user_id="u1")
    assert result["reception_status"] == "received"

    new_row = query_one(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse='principal'",
        (tenant_id, new_sku),
    )
    assert new_row is not None
    assert float(new_row["current_stock"]) == 20.0

    stock_count_final = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_count_final == 2


def test_demo_quickstart_respects_max_skus_atomically(
    monkeypatch, make_tenant_user_headers, client,
):
    """
    Regression for the last "committed prefix, aborted suffix" bug in the demo
    onboarding path: POST /demo/quickstart commits the dataset row, forces the
    session to MODELS_CONFIGURED, and writes the config blobs BEFORE looping
    over the fixed 5-SKU _DEMO_STOCK calling upsert_stock per SKU. A tenant
    already at (or near) its max_skus cap would sail through every one of
    those earlier commits and only get blocked mid-loop by upsert_stock's own
    per-row chokepoint — leaving an orphaned session + demo dataset behind
    plus 1..4 partially-created stock rows, with the training job never
    created.

    This asserts the pre-loop max_skus check blocks the whole call BEFORE the
    first write (no session, no dataset, no stock row leaks through), and
    that an entitled tenant (quota raised) still gets the full quickstart.
    """
    from backend.db.connection import execute, query_one, _json

    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True
    )
    # 1 pre-existing stock row + max_skus=1 quota override -> the demo's 5
    # brand-new SKUs would all exceed the cap.
    from backend.inventory import service as inv_svc
    inv_svc.upsert_stock(tenant_id, "PRE_EXISTING", {"current_stock": 5, "warehouse": "principal"})
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json({"max_skus": 1}), tenant_id))

    stock_before = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    sessions_before = query_one(
        "SELECT COUNT(*) AS c FROM sessions WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    datasets_before = query_one(
        "SELECT COUNT(*) AS c FROM datasets WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_before == 1

    resp = client.post("/api/v1/demo/quickstart", headers=headers)
    assert resp.status_code == 403
    body = resp.json()["detail"]
    assert body["code"] == "PLAN_LIMIT_REACHED"
    assert body["limit"] == "max_skus"

    # Atomicity: nothing partially written -- no new stock row, no orphaned
    # session, no orphaned dataset.
    stock_after = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    sessions_after = query_one(
        "SELECT COUNT(*) AS c FROM sessions WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    datasets_after = query_one(
        "SELECT COUNT(*) AS c FROM datasets WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_after == stock_before
    assert sessions_after == sessions_before
    assert datasets_after == datasets_before

    # Entitled case: raise the quota override -> quickstart succeeds and
    # actually creates the session + demo stock, proving the gate toggles.
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json({"max_skus": 100}), tenant_id))

    resp2 = client.post("/api/v1/demo/quickstart", headers=headers)
    assert resp2.status_code == 202, resp2.text
    data = resp2.json()["data"]

    sess = query_one(
        "SELECT status FROM sessions WHERE id=%s AND tenant_id=%s", (data["session_id"], tenant_id)
    )
    assert sess is not None
    assert sess["status"] == "QUEUED"

    stock_final = query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id=%s", (tenant_id,)
    )["c"]
    assert stock_final == stock_before + 5  # the 1 pre-existing + all 5 demo SKUs


def test_expired_trial_blocks_mutation_but_allows_read(
    monkeypatch, make_tenant_user_headers
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    from backend.db.connection import query_one

    headers, tenant_id = make_tenant_user_headers(expired_trial=True, return_tenant_id=True
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


@pytest.mark.parametrize("path,method", [
    ("/api/v1/documents", "get"),
    ("/api/v1/api-keys", "get"),
])
def test_the_routers_that_used_to_be_walled_answer_everyone(
    monkeypatch, make_tenant_user_headers, path, method
):
    """These two routers answered 403 PLAN_UPGRADE_REQUIRED to anyone below the
    tier that included them. testing_mode OFF is what used to switch that wall
    on, so this is the test that would catch one being put back."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    headers = make_tenant_user_headers()
    r = getattr(TestClient(app), method)(path, headers=headers)
    assert r.status_code == 200, r.text


def test_dead_stock_returns_the_classification_to_everyone(
    monkeypatch, make_tenant_user_headers, client,
):
    """
    GET /dead-stock used to strip `abc`/`xyz`/`abc_xyz` — and the derived
    `action_suggested`, which leaked the classification just as plainly — from
    any tenant without Feature.ABC_XYZ. There is no such feature now, so the
    classification and its action must reach the response. A tenant that got a
    silently thinner payload than the one next door is exactly what the strip
    did, and this is the guard against it coming back.

    Real dead-stock classification depends on stock-history-derived demand
    data that's impractical to seed deterministically through the API, so this
    monkeypatches the two service calls the handler makes
    (get_inventory_status / get_stock_history) to produce exactly one
    dead-stock item, while still exercising the real endpoint handler
    end-to-end.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)

    def _fake_item():
        return {
            "sku": "DEAD-1", "has_stock": True, "current_stock": 50,
            "daily_demand": 5, "unit_cost": 2.0, "abc": "C", "xyz": "Z",
            "signal": "OK", "display_name": "Dead Item", "supplier": "Acme",
        }

    # first_stock == last_stock == 50 -> depletion 0; expected = 5 * 2 = 10;
    # 0 < 10 * 0.20 -> classified as dead stock.
    history = [{"stock": 50}, {"stock": 50}]

    monkeypatch.setattr(
        "backend.inventory.service.get_inventory_status",
        lambda tenant_id, session_id, *a, **kw: [_fake_item()],
    )
    monkeypatch.setattr(
        "backend.inventory.service.get_stock_history",
        lambda tenant_id, sku, days=30: history,
    )

    headers = make_tenant_user_headers(role="analyst")
    r = client.get(
        "/api/v1/inventory/dead-stock",
        params={"session_id": "sess_x"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["abc"] == "C"
    # The action is a code plus an English fallback; the frontend renders
    # `inventory.dead_action_<code>`.
    assert items[0]["action_suggested_code"] == "return_to_supplier"
    assert items[0]["action_suggested"] == "Return to the supplier"


def test_send_now_fires_both_channels_for_any_tenant(
    monkeypatch, make_tenant_user_headers, client,
):
    """
    POST /alerts/send-now once 403-ed the whole endpoint for tenants without
    WHATSAPP_ALERTS, then — after that was fixed — fired the email and skipped
    the WhatsApp. Neither wall exists now: a test fire that quietly skipped a
    channel would tell the user their channel works when it was never tried.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.db.connection import execute

    critical_item = [{
        "sku": "SKU-1", "signal": "PEDIR_YA", "display_name": "X",
        "coverage_days": 1.0, "recommended_qty": 10,
    }]
    monkeypatch.setattr(
        "backend.inventory.service.get_inventory_status",
        lambda tenant_id, session_id, *a, **kw: critical_item,
    )

    sent_calls = []

    def _fake_send_whatsapp(number, text, *_a, **_kw):
        sent_calls.append((number, text))
        return True

    monkeypatch.setattr(
        "backend.notifications.whatsapp.send_whatsapp", _fake_send_whatsapp
    )

    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True)
    execute(
        "UPDATE users SET whatsapp_number=%s WHERE tenant_id=%s",
        ("+573003334444", tenant_id),
    )
    r = client.post(
        "/api/v1/inventory/alerts/send-now",
        params={"session_id": "sess_x"},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    data = r.json()["data"]
    assert data["sent"] is True
    assert data["whatsapp_sent"] == 1
    assert len(sent_calls) == 1
    assert sent_calls[0][0] == "+573003334444"


def test_entitlements_endpoint_reports_a_trial_not_a_plan(
    monkeypatch, make_tenant_user_headers,
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.main import app
    headers = make_tenant_user_headers()
    r = TestClient(app).get("/api/v1/entitlements", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"] if "data" in r.json() else r.json()

    # A fresh tenant is free, so the ceilings it reports are the free ones —
    # and `usage` arrives beside them, keyed identically, so the UI can never
    # pair a count with the wrong limit.
    assert data["tier"] == "free"
    assert data["limits"]["max_skus"] == PLANS["free"].max_skus
    assert data["limits"]["max_dataset_size_mb"] == PLANS["free"].max_dataset_size_mb
    assert data["usage"]["max_skus"] == 0
    assert data["read_only"] is False
    assert data["trial"]["state"] == "active"

    # The three keys the UI used to build padlocks out of. Their absence is the
    # contract: a `features` map that answers True to everything invites the
    # browser to keep asking, and a `plan` string invites it to compare.
    for gone in ("plan", "features", "feature_plans"):
        assert gone not in data, f"/entitlements still reports {gone!r}"
