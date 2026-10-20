"""A sandbox tenant to build an integration against (backend/sandbox/).

What matters: a sandbox key and a real key can never reach each other's data, a
trial cannot mint more throwaway tenants, the cap holds, an administrator can erase
a sandbox but never a real tenant, and the reaper takes an expired one.
"""

import pytest

from backend.db.connection import execute, query, query_one
from backend.sandbox import service
from backend.tenants.data_export import delete_tenant
from backend.trial import service as trial_svc


@pytest.fixture()
def made():
    ids = []
    yield ids
    for tid in ids:
        try:
            delete_tenant(tid)
        except Exception:
            pass


@pytest.fixture()
def no_seed(monkeypatch):
    """Most tests are about isolation and limits, not about training the demo."""
    monkeypatch.setattr(service, "_seed", lambda tenant_id, user_id: True)


def _create(client, headers, made):
    resp = client.post("/api/v1/sandbox", headers=headers)
    if resp.status_code == 201:
        made.append(resp.json()["data"]["id"])
    return resp


class TestCreate:

    def test_viewer_and_analyst_cannot_create_one_and_nothing_is_made(
            self, client, viewer_headers, analyst_headers, test_tenant, no_seed):
        for headers in (viewer_headers, analyst_headers):
            assert client.post("/api/v1/sandbox", headers=headers).status_code == 403
        assert query("SELECT 1 FROM tenants WHERE sandbox_of = %s", (test_tenant["id"],)) == []

    def test_admin_gets_a_separate_tenant_a_key_and_a_login(
            self, client, auth_headers, test_tenant, made, no_seed):
        resp = _create(client, auth_headers, made)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["environment"] == "sandbox" and data["seeded"] is True
        assert data["api_key"].startswith("sk_live_")
        assert data["login"]["email"].endswith("@stockai.demo")
        tenant = query_one("SELECT tier, sandbox_of, trial_ends_at FROM tenants WHERE id = %s",
                           (data["id"],))
        assert tenant["sandbox_of"] == test_tenant["id"] and tenant["tier"] == "demo"
        assert tenant["trial_ends_at"] is not None
        key = query_one("SELECT tenant_id, role, name, expires_at FROM api_keys WHERE tenant_id = %s",
                        (data["id"],))
        assert key["role"] == "analyst" and key["name"] == "sandbox" and key["expires_at"]
        # the key is stored only as a hash
        assert data["api_key"] not in str(query("SELECT * FROM api_keys WHERE tenant_id = %s",
                                                (data["id"],)))

    def test_the_demo_history_is_really_seeded(self, client, auth_headers, made):
        resp = _create(client, auth_headers, made)
        assert resp.status_code == 201, resp.text
        tid = resp.json()["data"]["id"]
        assert query_one("SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s", (tid,))["n"] == 1
        assert query_one("SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id = %s",
                         (tid,))["n"] >= 10

    def test_a_third_live_sandbox_is_refused(self, client, auth_headers, made, no_seed):
        for _ in range(service.MAX_LIVE_PER_COMPANY):
            assert _create(client, auth_headers, made).status_code == 201
        third = _create(client, auth_headers, made)
        assert third.status_code == 409 and third.json()["error_code"] == "sandbox_limit_reached"
        assert len(made) == service.MAX_LIVE_PER_COMPANY

    def test_a_trial_account_cannot_create_a_sandbox(self, test_tenant, no_seed):
        execute("UPDATE tenants SET tier = 'demo' WHERE id = %s", (test_tenant["id"],))
        try:
            with pytest.raises(Exception) as exc:
                service.create_sandbox(test_tenant["id"])
            assert getattr(exc.value, "code", None) == "sandbox_not_for_trial"
        finally:
            execute("UPDATE tenants SET tier = 'free' WHERE id = %s", (test_tenant["id"],))
        assert query("SELECT 1 FROM tenants WHERE sandbox_of = %s", (test_tenant["id"],)) == []

    def test_an_api_key_cannot_mint_a_sandbox(self, client, auth_headers, made, no_seed):
        made_one = _create(client, auth_headers, made).json()["data"]
        resp = client.post("/api/v1/sandbox",
                           headers={"Authorization": f"Bearer {made_one['api_key']}"})
        assert resp.status_code in (401, 403)
        assert len(made) == 1


class TestIsolation:

    def test_a_sandbox_key_sees_the_sandbox_and_never_the_real_tenant(
            self, client, auth_headers, completed_session, made):
        real_session = completed_session["id"]
        data = _create(client, auth_headers, made).json()["data"]
        key = {"Authorization": f"Bearer {data['api_key']}"}
        listing = client.get("/api/v1/sessions", headers=key)
        assert listing.status_code == 200, listing.text
        assert real_session not in listing.text
        assert client.get(f"/api/v1/sessions/{real_session}", headers=key).status_code in (403, 404)

    def test_a_real_key_never_reaches_the_sandbox(self, client, auth_headers, made, no_seed):
        data = _create(client, auth_headers, made).json()["data"]
        from backend.sessions import service as session_svc
        sandbox_session = session_svc.create_session(data["id"], "u", "inside the sandbox")
        sid = sandbox_session.get("session_id") or sandbox_session["id"]
        assert client.get(f"/api/v1/sessions/{sid}", headers=auth_headers).status_code in (403, 404)
        assert sid not in client.get("/api/v1/sessions", headers=auth_headers).text

    def test_writes_in_the_sandbox_leave_the_real_tenant_alone(
            self, client, auth_headers, test_tenant, made, no_seed):
        before = query_one("SELECT COUNT(*) AS n FROM sessions WHERE tenant_id = %s",
                           (test_tenant["id"],))["n"]
        data = _create(client, auth_headers, made).json()["data"]
        key = {"Authorization": f"Bearer {data['api_key']}"}
        client.post("/api/v1/sessions", json={"name": "from the sandbox"}, headers=key)
        after = query_one("SELECT COUNT(*) AS n FROM sessions WHERE tenant_id = %s",
                          (test_tenant["id"],))["n"]
        assert after == before


class TestEnding:

    def test_listing_shows_only_this_companys_live_sandboxes(
            self, client, auth_headers, made, no_seed):
        mine = _create(client, auth_headers, made).json()["data"]["id"]
        other = trial_svc.create_trial_account(include_ids=True)["tenant_id"]
        made.append(other)
        execute("UPDATE tenants SET sandbox_of = 'someone-else' WHERE id = %s", (other,))
        ids = [s["id"] for s in client.get("/api/v1/sandbox", headers=auth_headers).json()["data"]]
        assert ids == [mine]

    def test_admin_deletes_their_sandbox_and_it_is_gone(self, client, auth_headers, made, no_seed):
        sid = _create(client, auth_headers, made).json()["data"]["id"]
        assert client.delete(f"/api/v1/sandbox/{sid}", headers=auth_headers).status_code == 200
        assert query("SELECT 1 FROM tenants WHERE id = %s", (sid,)) == []
        assert query("SELECT 1 FROM api_keys WHERE tenant_id = %s", (sid,)) == []

    def test_viewer_cannot_delete_and_nothing_changes(
            self, client, auth_headers, viewer_headers, made, no_seed):
        sid = _create(client, auth_headers, made).json()["data"]["id"]
        assert client.delete(f"/api/v1/sandbox/{sid}", headers=viewer_headers).status_code == 403
        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (sid,)) is not None

    def test_a_real_tenant_can_never_be_erased_through_this_door(
            self, client, auth_headers, test_tenant):
        resp = client.delete(f"/api/v1/sandbox/{test_tenant['id']}", headers=auth_headers)
        assert resp.status_code == 404
        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (test_tenant["id"],)) is not None

    def test_another_companys_sandbox_is_not_found(self, client, auth_headers, made):
        other = trial_svc.create_trial_account(include_ids=True)["tenant_id"]
        made.append(other)
        execute("UPDATE tenants SET sandbox_of = 'someone-else' WHERE id = %s", (other,))
        assert client.delete(f"/api/v1/sandbox/{other}", headers=auth_headers).status_code == 404
        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (other,)) is not None

    def test_an_expired_sandbox_is_reaped_and_a_live_one_is_not(
            self, client, auth_headers, made, no_seed):
        old = _create(client, auth_headers, made).json()["data"]["id"]
        young = _create(client, auth_headers, made).json()["data"]["id"]
        execute("UPDATE tenants SET trial_ends_at = NOW() - interval '1 hour' WHERE id = %s", (old,))
        assert [s["id"] for s in client.get("/api/v1/sandbox", headers=auth_headers).json()["data"]] == [young]
        trial_svc.reap_expired_trials()
        assert query("SELECT 1 FROM tenants WHERE id = %s", (old,)) == []
        assert query_one("SELECT 1 AS x FROM tenants WHERE id = %s", (young,)) is not None
