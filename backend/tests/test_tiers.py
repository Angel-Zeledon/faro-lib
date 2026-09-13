"""Free tier vs paid tier: the only thing money changes in Faro.

There are no feature gates and no checkout. Both tiers ship every screen; the
tier decides how much fits, and a tenant crosses over because somebody talked to
us and `tenants.tier` was set. So what has to be true, and is tested here:

1. The ceilings resolve per tier, an unknown tier fails to FREE (failing to paid
   would hand out an unlimited account and nobody would ever notice), and a
   per-tenant `quota` override still beats both.
2. A free tenant is actually stopped — with no row leaking past the refusal —
   and a paid tenant doing the identical thing is not.
3. The refusal reaches the frontend as something it can translate. The guard
   raises `HTTPException(detail={"code": ...})`, which FastAPI would have sent
   as a bare dict; the frontend reads `error_code`. Without the promotion in
   `backend/main.py` the user sees a stringified Python dict at the exact moment
   the product is trying to sell them something.
4. The ask to pay is recorded before anything can go wrong with notifying us.

`testing_mode` is on in this repo's .env and bypasses every quota, so these
tests turn it off themselves. That is the house rule, and it is why a limiter
can ship green while never having been exercised.
"""

from uuid import uuid4

from backend.db.connection import execute, query, query_one, _json
from backend.entitlements.plans import PLANS
from backend.entitlements.service import tenant_limits, tenant_tier


def _set_tier(tenant_id: str, tier: str) -> None:
    execute("UPDATE tenants SET tier = %s WHERE id = %s", (tier, tenant_id))


def _set_quota(tenant_id: str, quota: dict) -> None:
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json(quota), tenant_id))


def _stock_count(tenant_id: str) -> int:
    return query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id = %s", (tenant_id,)
    )["c"]


def _key_count(tenant_id: str) -> int:
    return query_one(
        "SELECT COUNT(*) AS c FROM api_keys WHERE tenant_id = %s", (tenant_id,)
    )["c"]


def _stock_body(**over):
    return {"current_stock": 5, "min_stock": 1, "lead_time_days": 7, **over}


# ── 1. Resolution ────────────────────────────────────────────────────────────

def test_free_limits_are_short_and_paid_limits_are_open():
    free = tenant_limits({"tier": "free"})
    paid = tenant_limits({"tier": "paid"})
    for key in ("max_skus", "max_users", "max_locations", "max_sessions",
                "max_api_keys", "max_api_calls_per_day"):
        assert isinstance(free[key], int), f"{key} must be a real ceiling on free"
        assert paid[key] is None, f"{key} must be unlimited on paid"
    # The infrastructure ceiling is NOT for sale: identical on both tiers.
    assert free["max_concurrent_jobs"] == paid["max_concurrent_jobs"]


def test_unknown_or_missing_tier_falls_back_to_free():
    """The dangerous default is 'paid'. A NULL column, a typo somebody put in
    psql, a row that predates the migration — all of them must land on the
    narrow tier, because an accidentally-unlimited account is invisible."""
    assert tenant_tier({}) == "free"
    assert tenant_tier({"tier": None}) == "free"
    assert tenant_tier({"tier": "enterprise"}) == "free"
    assert tenant_limits({"tier": "enterprise"}) == tenant_limits({"tier": "free"})


def test_quota_override_beats_the_tier_in_both_directions():
    widened = tenant_limits({"tier": "free", "quota": {"max_skus": 5000}})
    assert widened["max_skus"] == 5000
    assert widened["max_users"] == PLANS["free"].max_users  # untouched keys stay

    narrowed = tenant_limits({"tier": "paid", "quota": {"max_skus": 10}})
    assert narrowed["max_skus"] == 10


# ── 2 + 3. Enforcement, and what the refusal looks like on the wire ──────────

def test_free_tenant_is_stopped_at_max_skus_and_no_row_leaks(
    monkeypatch, make_tenant_user_headers, client,
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _set_quota(tenant_id, {"max_skus": 1})   # 1 instead of 100, so the test is cheap

    first = client.put("/api/v1/inventory/stock/SKU-A", json=_stock_body(), headers=headers)
    assert first.status_code == 200, first.text
    assert _stock_count(tenant_id) == 1

    blocked = client.put("/api/v1/inventory/stock/SKU-B", json=_stock_body(), headers=headers)
    assert blocked.status_code == 403, blocked.text
    body = blocked.json()
    # The dict shape the guard has always sent, unchanged...
    assert body["detail"]["code"] == "PLAN_LIMIT_REACHED"
    assert body["detail"]["limit"] == "max_skus"
    # ...plus the envelope the frontend actually reads. Without these two the
    # user is shown a stringified dict, or the generic "something failed".
    assert body["error_code"] == "PLAN_LIMIT_REACHED"
    assert body["error_params"]["limit"] == "max_skus"
    assert body["error_params"]["max"] == 1
    assert body["error_params"]["tier"] == "free"
    # The refusal must not have half-written the row it refused.
    assert _stock_count(tenant_id) == 1
    assert query("SELECT 1 FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
                 (tenant_id, "SKU-B")) == []


def test_paid_tenant_sails_past_the_same_ceiling(
    monkeypatch, make_tenant_user_headers, client,
):
    """The identical request, one column different. This is the whole product
    difference, so it is worth asserting as a pair rather than trusting that
    `None` means unlimited somewhere upstream."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "paid")

    for sku in ("SKU-A", "SKU-B", "SKU-C"):
        r = client.put(f"/api/v1/inventory/stock/{sku}", json=_stock_body(), headers=headers)
        assert r.status_code == 200, r.text
    assert _stock_count(tenant_id) == 3


def test_free_tenant_gets_one_api_key_and_paid_gets_more(
    monkeypatch, make_tenant_user_headers, client,
):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")

    first = client.post("/api/v1/api-keys", json={"name": "erp", "role": "viewer"},
                        headers=headers)
    assert first.status_code in (200, 201), first.text
    assert _key_count(tenant_id) == 1

    second = client.post("/api/v1/api-keys", json={"name": "second", "role": "viewer"},
                         headers=headers)
    assert second.status_code == 403, second.text
    assert second.json()["error_code"] == "PLAN_LIMIT_REACHED"
    assert second.json()["error_params"]["limit"] == "max_api_keys"
    assert _key_count(tenant_id) == 1, "a refused key must not exist"

    _set_tier(tenant_id, "paid")
    third = client.post("/api/v1/api-keys", json={"name": "second", "role": "viewer"},
                        headers=headers)
    assert third.status_code in (200, 201), third.text
    assert _key_count(tenant_id) == 2


def test_free_tier_daily_api_ceiling_stops_a_key_the_minute_window_would_allow(
    monkeypatch, make_tenant_user_headers, client,
):
    """The per-minute limiter (120) and the per-day one are different windows.
    With a daily ceiling of 2, the third call must be refused while the minute
    window is nowhere near full — otherwise the daily number is decoration."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.auth import api_key_auth

    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _set_quota(tenant_id, {"max_api_calls_per_day": 2})

    key_id = f"tier-day-{uuid4().hex[:8]}"
    assert api_key_auth.check_rate(key_id, tenant_id) is True
    assert api_key_auth.check_rate(key_id, tenant_id) is True
    assert api_key_auth.check_rate(key_id, tenant_id) is False, (
        "the daily ceiling never fired — a free key can read all day"
    )

    # Same key, a tenant with no daily ceiling: the minute window alone decides.
    _set_tier(tenant_id, "paid")
    _set_quota(tenant_id, {})
    assert api_key_auth.check_rate(f"tier-day-{uuid4().hex[:8]}", tenant_id) is True


def test_a_refused_daily_call_does_not_burn_a_minute_slot(
    monkeypatch, make_tenant_user_headers,
):
    """Both windows are checked before either is written. A call refused by the
    daily ceiling that still consumed a per-minute slot would make the minute
    counter drift up all day for no calls actually served."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.auth import api_key_auth

    _, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _set_quota(tenant_id, {"max_api_calls_per_day": 1})

    key_id = f"tier-burn-{uuid4().hex[:8]}"
    assert api_key_auth.check_rate(key_id, tenant_id) is True
    assert api_key_auth.check_rate(key_id, tenant_id) is False

    minute_used = query_one(
        "SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (f"apikey:{key_id}",)
    )["n"]
    assert minute_used == 1, f"the refused call still counted: {minute_used} minute events"


# ── 4. The ask to pay ────────────────────────────────────────────────────────

def _requests_for(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM upgrade_requests WHERE tenant_id = %s ORDER BY created_at",
        (tenant_id,),
    )


def test_viewer_cannot_file_an_upgrade_request(make_tenant_user_headers, client):
    headers, tenant_id = make_tenant_user_headers(role="viewer", return_tenant_id=True)
    resp = client.post("/api/v1/entitlements/upgrade-request",
                       json={"message": "we need more"}, headers=headers)
    assert resp.status_code == 403, resp.text
    assert _requests_for(tenant_id) == [], "a refused request must leave no row"


def test_analyst_files_an_upgrade_request_and_it_is_stored(
    monkeypatch, make_tenant_user_headers, client,
):
    # BOTH addresses cleared, not just the notify one: the endpoint falls back
    # to contact_email, which is configured on this machine. Clearing only
    # `upgrade_notify_email` would leave a live destination and this test would
    # be asserting the wrong branch.
    monkeypatch.setattr("backend.config.settings.upgrade_notify_email", "")
    monkeypatch.setattr("backend.config.settings.contact_email", "")
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)

    resp = client.post(
        "/api/v1/entitlements/upgrade-request",
        json={"limit_key": "max_skus", "message": "1200 products",
              "contact": "+506 8888 7777"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["created"] is True
    # No notify address configured: the send cannot have happened, and the row
    # is exactly why that is survivable.
    assert resp.json()["data"]["notified"] is False

    rows = _requests_for(tenant_id)
    assert len(rows) == 1
    assert rows[0]["limit_key"] == "max_skus"
    assert rows[0]["message"] == "1200 products"
    assert rows[0]["contact"] == "+506 8888 7777"
    assert rows[0]["status"] == "new"


def test_the_notify_address_falls_back_to_the_public_contact_email(
    monkeypatch, make_tenant_user_headers, client,
):
    """A deployment that configured only CONTACT_EMAIL still gets told when
    somebody wants to pay. The fallback is the difference between "we read the
    table weekly" and "we answer the same day", so it is worth a test of its
    own rather than being implied by the one above."""
    monkeypatch.setattr("backend.config.settings.upgrade_notify_email", "")
    monkeypatch.setattr("backend.config.settings.contact_email", "owner@example.com")
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)

    sent = {}
    import backend.notifications.email as email_mod
    monkeypatch.setattr(
        email_mod, "send_upgrade_request_email",
        lambda **kw: sent.update(kw) or True,
    )

    resp = client.post("/api/v1/entitlements/upgrade-request",
                       json={"message": "we grew"}, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["notified"] is True
    assert sent["to"] == "owner@example.com"
    # The person, not the id — whoever reads it needs somebody to answer.
    assert "@" in sent["requester"], f"requester is not contactable: {sent['requester']!r}"
    assert len(_requests_for(tenant_id)) == 1


def test_a_second_ask_updates_the_open_one_instead_of_piling_up(
    monkeypatch, make_tenant_user_headers, client,
):
    monkeypatch.setattr("backend.config.settings.upgrade_notify_email", "")
    monkeypatch.setattr("backend.config.settings.contact_email", "")
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)

    client.post("/api/v1/entitlements/upgrade-request",
                json={"message": "first"}, headers=headers)
    second = client.post("/api/v1/entitlements/upgrade-request",
                         json={"message": "second, with more detail"}, headers=headers)
    assert second.status_code == 201, second.text
    assert second.json()["data"]["created"] is False

    rows = _requests_for(tenant_id)
    assert len(rows) == 1, "the funnel is read by hand — one open ask per tenant"
    assert rows[0]["message"] == "second, with more detail"


def test_a_read_only_tenant_can_still_ask_to_pay(
    monkeypatch, make_tenant_user_headers, client,
):
    """The suspended account is the one most likely to be writing to us.
    Refusing its message because it is read-only would be refusing the sale.

    testing_mode has to be off for this to mean anything: with it on, the
    read-only guard is bypassed for every endpoint and the test would pass
    against an endpoint that does block them."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    monkeypatch.setattr("backend.config.settings.upgrade_notify_email", "")
    monkeypatch.setattr("backend.config.settings.contact_email", "")
    headers, tenant_id = make_tenant_user_headers(
        role="analyst", expired_trial=True, return_tenant_id=True,
    )
    resp = client.post("/api/v1/entitlements/upgrade-request",
                       json={"message": "reactivate us"}, headers=headers)
    assert resp.status_code == 201, resp.text
    assert len(_requests_for(tenant_id)) == 1


# ── 5. What the app itself reports ───────────────────────────────────────────

def test_entitlements_reports_tier_limits_and_usage_against_them(
    make_tenant_user_headers, client,
):
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    client.put("/api/v1/inventory/stock/SKU-A", json=_stock_body(), headers=headers)

    data = client.get("/api/v1/entitlements", headers=headers).json()["data"]
    assert data["tier"] == "free"
    assert data["limits"]["max_skus"] == PLANS["free"].max_skus
    # Usage is keyed by the limit it counts against, so the UI can never pair a
    # number with the wrong ceiling.
    assert data["usage"]["max_skus"] == 1
    assert data["usage"]["max_users"] >= 1
    assert set(data["contact"]) == {"whatsapp", "email"}


# ── 6. The schema the whole thing rests on ───────────────────────────────────

def test_tier_column_defaults_to_free_and_refuses_null():
    col = query_one(
        "SELECT column_default, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'tenants' AND column_name = 'tier'"
    )
    assert col is not None, "migrations did not add tenants.tier"
    assert col["is_nullable"] == "NO"
    assert "free" in (col["column_default"] or "")


def test_a_brand_new_tenant_is_free_and_not_on_a_countdown(test_tenant):
    """Signup used to start a 14-day trial that ended in a read-only account.
    Free is a permanent home now — a new tenant must land on it with no expiry
    hanging over it."""
    row = query_one("SELECT tier, trial_ends_at FROM tenants WHERE id = %s",
                    (test_tenant["id"],))
    assert row["tier"] == "free"
    assert row["trial_ends_at"] is None
