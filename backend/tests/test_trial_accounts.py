"""Trial accounts handed out from the landing (`backend/trial/`).

What has to be true:

1. `POST /trial` with no credentials returns a user and a password that log in,
   into a `demo` tenant with an end date. Nothing trains until they enter: the
   page sends them to the existing one-click demo.
2. The account can reach nobody: the address is never verified, so outward
   actions are refused, and the email transport refuses the address itself.
3. It ends: past `trial_ends_at` login and refresh refuse it, and the reaper
   erases it — except while it has an unanswered request to talk to us.
4. The ceilings hold: per address, and across the installation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.db.connection import execute, query_one
from backend.entitlements.plans import DEMO, PLANS
from backend.tenants.data_export import delete_tenant
from backend.trial import service as trial_svc


@pytest.fixture
def trial(client):
    resp = client.post("/api/v1/trial")
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    user = query_one("SELECT id, tenant_id FROM users WHERE email = %s", (data["email"],))
    assert user is not None
    yield {**data, "tenant_id": user["tenant_id"], "user_id": user["id"]}
    if query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (user["tenant_id"],)):
        delete_tenant(user["tenant_id"])


def _login(client, trial):
    return client.post("/api/v1/auth/login",
                       json={"email": trial["email"], "password": trial["password"]})


def _expire(tenant_id: str) -> None:
    execute("UPDATE tenants SET trial_ends_at = %s WHERE id = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=1), tenant_id))


# ── 1. Creation ──────────────────────────────────────────────────────────────

def test_trial_creates_a_demo_tenant_with_an_end_and_trains_nothing_yet(client, trial):
    tenant = query_one("SELECT tier, trial_ends_at FROM tenants WHERE id = %s",
                       (trial["tenant_id"],))
    assert tenant["tier"] == DEMO
    remaining = tenant["trial_ends_at"] - datetime.now(timezone.utc)
    assert timedelta(hours=23, minutes=55) < remaining <= timedelta(hours=24)

    user = query_one("SELECT role, email_verified FROM users WHERE id = %s",
                     (trial["user_id"],))
    assert user["role"] == "admin"
    # Never verified: this is what keeps every outward action closed.
    assert user["email_verified"] is False
    assert trial["email"].endswith("@" + trial_svc.TRIAL_EMAIL_DOMAIN)

    # A trial nobody enters costs no training.
    assert query_one("SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s",
                     (trial["tenant_id"],))["n"] == 0


def test_the_trial_fits_the_one_click_demo(client, trial):
    """Where /prueba sends the visitor: the demo must fit the demo tier's
    ceilings, or the trial's first screen is a limit error."""
    token = _login(client, trial).json()["data"]["access_token"]
    resp = client.post("/api/v1/demo/quickstart", json={},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 202, resp.text
    assert query_one("SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id = %s",
                     (trial["tenant_id"],))["n"] == 5
    assert query_one("SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s",
                     (trial["tenant_id"],))["n"] >= 1


def test_the_returned_credentials_log_in(client, trial):
    resp = _login(client, trial)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["user"]["tenant_id"] == trial["tenant_id"]


def test_the_password_is_stored_hashed_only(trial):
    row = query_one("SELECT hashed_password FROM users WHERE id = %s", (trial["user_id"],))
    assert trial["password"] not in row["hashed_password"]


def test_demo_tier_ceilings_are_below_free():
    demo, free = PLANS[DEMO], PLANS["free"]
    for key in ("max_skus", "max_users", "max_sessions", "max_dataset_size_mb"):
        assert getattr(demo, key) <= getattr(free, key), key
    assert demo.max_concurrent_jobs < free.max_concurrent_jobs


# ── 2. It reaches nobody ─────────────────────────────────────────────────────

def test_an_outward_action_is_refused_for_the_unverified_trial_user(client, trial):
    token = _login(client, trial).json()["data"]["access_token"]
    resp = client.post(
        "/api/v1/users",
        json={"email": "someone@stockai-e2e.io", "role": "analyst"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    assert query_one("SELECT COUNT(*) AS n FROM users WHERE tenant_id = %s",
                     (trial["tenant_id"],))["n"] == 1


def test_the_transport_refuses_a_trial_address(monkeypatch):
    from backend.notifications import email as email_mod
    calls = []
    monkeypatch.setattr(email_mod, "_send_resend", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(email_mod, "_send_smtp", lambda *a, **k: calls.append(a))
    with pytest.raises(email_mod.EmailDeliveryError):
        email_mod._transport_send("demo-abc123@stockai.demo", "s", "<p>x</p>")
    assert calls == []


def test_a_trial_must_say_how_to_reach_it_when_it_writes_to_us(client, trial):
    token = _login(client, trial).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    refused = client.post("/api/v1/entitlements/upgrade-request",
                          json={"message": "me interesa"}, headers=headers)
    assert refused.status_code == 400, refused.text
    assert refused.json()["error_code"] == "contact_required"
    assert query_one("SELECT COUNT(*) AS n FROM upgrade_requests WHERE tenant_id = %s",
                     (trial["tenant_id"],))["n"] == 0

    accepted = client.post("/api/v1/entitlements/upgrade-request",
                           json={"message": "me interesa", "contact": "+50688887777"},
                           headers=headers)
    assert accepted.status_code == 201, accepted.text
    row = query_one("SELECT contact FROM upgrade_requests WHERE tenant_id = %s",
                    (trial["tenant_id"],))
    assert row["contact"] == "+50688887777"


# ── 3. It ends ───────────────────────────────────────────────────────────────

def test_login_refuses_an_expired_trial(client, trial):
    _expire(trial["tenant_id"])
    resp = _login(client, trial)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "trial_account_expired"


def test_refresh_refuses_an_expired_trial(client, trial):
    refresh = _login(client, trial).json()["data"]["refresh_token"]
    _expire(trial["tenant_id"])
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401, resp.text
    assert resp.json()["error_code"] == "trial_account_expired"


def test_reaper_erases_expired_trials_and_nothing_else(client, trial, test_tenant):
    other = client.post("/api/v1/trial").json()["data"]
    other_tid = query_one("SELECT tenant_id FROM users WHERE email = %s",
                          (other["email"],))["tenant_id"]
    try:
        _expire(trial["tenant_id"])
        # A real tenant with a past date (suspended by hand) is not a trial.
        execute("UPDATE tenants SET trial_ends_at = %s WHERE id = %s",
                (datetime.now(timezone.utc) - timedelta(days=1), test_tenant["id"]))

        trial_svc.reap_expired_trials()

        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (trial["tenant_id"],)) is None
        assert query_one("SELECT 1 AS x FROM users WHERE id = %s", (trial["user_id"],)) is None
        assert query_one("SELECT COUNT(*) AS n FROM jobs WHERE tenant_id = %s",
                         (trial["tenant_id"],))["n"] == 0
        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (other_tid,)) is not None
        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (test_tenant["id"],)) is not None
    finally:
        delete_tenant(other_tid)


def test_reaper_keeps_an_expired_trial_that_asked_to_talk(client, trial):
    token = _login(client, trial).json()["data"]["access_token"]
    client.post("/api/v1/entitlements/upgrade-request",
                json={"contact": "lead@stockai-e2e.io"},
                headers={"Authorization": f"Bearer {token}"})
    _expire(trial["tenant_id"])

    trial_svc.reap_expired_trials()
    assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (trial["tenant_id"],)) is not None

    execute("UPDATE upgrade_requests SET status = 'contacted' WHERE tenant_id = %s",
            (trial["tenant_id"],))
    trial_svc.reap_expired_trials()
    assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (trial["tenant_id"],)) is None


# ── 4. Ceilings ──────────────────────────────────────────────────────────────

def test_one_address_gets_three_trials_a_day(client, monkeypatch):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    address = "203.0.113.77"
    execute("DELETE FROM auth_rate_events WHERE key = %s", (f"trial:{address}",))
    created = []
    try:
        for _ in range(trial_svc.MAX_TRIALS_PER_ADDRESS):
            r = client.post("/api/v1/trial", headers={"X-Forwarded-For": address})
            assert r.status_code == 201, r.text
            created.append(r.json()["data"]["email"])
        before = query_one("SELECT COUNT(*) AS n FROM tenants WHERE tier = %s", (DEMO,))["n"]
        blocked = client.post("/api/v1/trial", headers={"X-Forwarded-For": address})
        assert blocked.status_code == 429, blocked.text
        assert blocked.json()["error_code"] == "trial_limit_per_address"
        assert query_one("SELECT COUNT(*) AS n FROM tenants WHERE tier = %s",
                         (DEMO,))["n"] == before
    finally:
        execute("DELETE FROM auth_rate_events WHERE key = %s", (f"trial:{address}",))
        for email in created:
            row = query_one("SELECT tenant_id FROM users WHERE email = %s", (email,))
            if row:
                delete_tenant(row["tenant_id"])


def test_installation_ceiling_refuses_without_writing(client, monkeypatch):
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    monkeypatch.setattr(trial_svc, "MAX_LIVE_TRIALS", 0)
    execute("DELETE FROM auth_rate_events WHERE key LIKE 'trial:%%'")
    before = query_one("SELECT COUNT(*) AS n FROM tenants", ())["n"]
    resp = client.post("/api/v1/trial")
    assert resp.status_code == 503, resp.text
    assert resp.json()["error_code"] == "trial_capacity_reached"
    assert query_one("SELECT COUNT(*) AS n FROM tenants", ())["n"] == before
