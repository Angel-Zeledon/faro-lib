"""The published endpoints, called the way a customer actually calls them.

`test_public_api_surface` proves the seven public routes EXIST. That is a
weaker promise than it looks: rename nothing, change what `log-po` accepts, and
that file stays green while every integration in the field breaks.

These tests close the gap by using the credential the documentation tells
customers to use — an API key, not a JWT — and asserting the endpoint does its
job. Until they existed, the only thing that had ever exercised the public API
end to end was one person driving curl by hand, once.

Training is deliberately not run here: it is a real ML job, and
`completed_session` already seeds the results those endpoints read. What is
under test is the contract and the credential, not the engine.
"""

import pytest

from backend.auth import api_key_auth
from backend.db.connection import execute, query_one


@pytest.fixture
def key_headers(client, auth_headers, test_tenant):
    """A read-write key on a plan that includes API access.

    Minted through the same endpoint the screen calls, so a change that breaks
    key creation breaks these tests too — which is the point.
    """
    execute("UPDATE tenants SET plan = 'professional' WHERE id = %s", (test_tenant["id"],))
    r = client.post("/api/v1/api-keys",
                    json={"name": "erp-integration-test", "role": "analyst"},
                    headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    raw = r.json()["data"]["key"]
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture
def read_only_headers(client, auth_headers, test_tenant):
    execute("UPDATE tenants SET plan = 'professional' WHERE id = %s", (test_tenant["id"],))
    r = client.post("/api/v1/api-keys",
                    json={"name": "dashboard-readonly", "role": "viewer"},
                    headers=auth_headers)
    raw = r.json()["data"]["key"]
    return {"Authorization": f"Bearer {raw}"}


class TestAKeyCanDoTheFiveJobs:
    def test_it_can_list_the_sources_it_would_push_to(self, client, key_headers):
        r = client.get("/api/v1/data-sources", headers=key_headers)
        assert r.status_code == 200, (
            f"a documented endpoint refused the documented credential: {r.text[:200]}"
        )

    def test_it_can_read_the_semaforo(self, client, key_headers, completed_session):
        r = client.get(f"/api/v1/inventory/status?session_id={completed_session['id']}",
                       headers=key_headers)
        assert r.status_code == 200, r.text

    def test_it_can_read_the_briefing(self, client, key_headers, completed_session):
        r = client.get(
            f"/api/v1/inventory/morning-briefing?session_id={completed_session['id']}",
            headers=key_headers)
        assert r.status_code == 200, r.text

    def test_it_can_record_an_order_and_the_order_exists(
        self, client, key_headers, completed_session, test_tenant
    ):
        """The one that only looks optional. Without this row the order does not
        exist for Faro: reception is tracked against it and supplier lead-time
        learning reads it."""
        before = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
            (test_tenant["id"],))["n"]

        r = client.post(
            f"/api/v1/inventory/log-po?session_id={completed_session['id']}",
            json={"items": [{
                "sku": "SKU_001", "recommended_qty": 120, "final_qty": 100,
                "status": "modified", "unit_cost": 12.5, "supplier": "Andina",
            }]},
            headers=key_headers)
        assert r.status_code in (200, 201), r.text

        after = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s",
            (test_tenant["id"],))["n"]
        assert after == before + 1, "the endpoint answered success and wrote nothing"

        row = query_one(
            """SELECT total_units, modified_count FROM inventory_po_log
               WHERE tenant_id = %s ORDER BY generated_at DESC LIMIT 1""",
            (test_tenant["id"],))
        assert float(row["total_units"]) == 100.0, (
            "the order recorded the recommended quantity, not the one the buyer "
            "chose — adoption tracking would be measuring the wrong thing"
        )
        assert row["modified_count"] == 1


class TestTheCredentialIsHonouredAsDocumented:
    def test_a_read_only_key_cannot_write(self, client, read_only_headers, completed_session):
        """The docs promise "Solo leer" means exactly that. A viewer key that
        could record orders would make the choice on the key screen a lie."""
        r = client.post(
            f"/api/v1/inventory/log-po?session_id={completed_session['id']}",
            json={"items": []}, headers=read_only_headers)
        assert r.status_code == 403, f"a read-only key wrote: {r.status_code}"

    def test_a_read_only_key_can_still_read(self, client, read_only_headers, completed_session):
        r = client.get(f"/api/v1/inventory/status?session_id={completed_session['id']}",
                       headers=read_only_headers)
        assert r.status_code == 200, r.text

    def test_an_unknown_key_is_refused(self, client):
        r = client.get("/api/v1/data-sources",
                       headers={"Authorization": "Bearer sk_live_this_never_existed"})
        assert r.status_code == 401

    def test_a_revoked_key_stops_working_immediately(
        self, client, auth_headers, test_tenant
    ):
        """Revocation is the only recourse when a key leaks, so it has to bite on
        the next call — not when some cache expires."""
        execute("UPDATE tenants SET plan = 'professional' WHERE id = %s", (test_tenant["id"],))
        created = client.post("/api/v1/api-keys",
                              json={"name": "leaked", "role": "analyst"},
                              headers=auth_headers).json()["data"]
        raw = created["key"]
        headers = {"Authorization": f"Bearer {raw}"}
        assert client.get("/api/v1/data-sources", headers=headers).status_code == 200

        key_id = api_key_auth.resolve(raw)["id"]
        assert client.delete(f"/api/v1/api-keys/{key_id}",
                             headers=auth_headers).status_code in (200, 204)

        assert client.get("/api/v1/data-sources", headers=headers).status_code == 401


class TestThePlanGateIsRealForMachines:
    def test_a_starter_tenant_key_is_refused_with_a_reason(
        self, client, auth_headers, test_tenant, monkeypatch
    ):
        """Documented as "included from Professional". A starter key must be
        refused, and refused in a way the integrator can act on rather than a
        bare 403."""
        from backend.config import settings

        execute("UPDATE tenants SET plan = 'professional' WHERE id = %s", (test_tenant["id"],))
        raw = client.post("/api/v1/api-keys",
                          json={"name": "downgraded", "role": "analyst"},
                          headers=auth_headers).json()["data"]["key"]

        # The downgrade happens AFTER the key exists — the case a customer hits
        # when they change plan, not a key that was never valid.
        execute("UPDATE tenants SET plan = 'starter' WHERE id = %s", (test_tenant["id"],))
        monkeypatch.setattr(settings, "testing_mode", False)

        r = client.get("/api/v1/data-sources",
                       headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 403, (
            f"a key kept working after its plan lost API access ({r.status_code})"
        )
        assert "PLAN_UPGRADE_REQUIRED" in r.text
