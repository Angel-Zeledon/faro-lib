"""`GET /inventory-sync/ownership`: which fields a live sync will overwrite.

The editors (product editor, stock update view, CSV import, physical count)
warn BEFORE a person saves a value the source will replace at its next run.
The answer follows the rule the apply step enforces: the precedence winner of
each (sku, warehouse) among the profiles that hold it, reported only when that
winner will run again (enabled, or on demand).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query
from backend.inventory_sync import profiles
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    _booted, _profile, _put, _run, _source, _stock, src_db, src_table,
)
from backend.users import service as user_svc

URL = "/api/v1/inventory-sync/ownership"


def _headers(tid, role="viewer", scope=None):
    u = user_svc.create_user(tenant_id=tid, email=f"o-{uuid4().hex[:8]}@stockai-e2e.io",
                             password="TestPass123!", role=role)
    user_svc.mark_verified(tid, u["id"])
    if scope:
        ids = [r["id"] for r in query("SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))
               if r["name"] in scope]
        execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s", (json.dumps(ids), u["id"]))
    return {"Authorization": f"Bearer {create_access_token(u['id'], tid, role, email_verified=True)}"}


def _enable(pid):
    execute("UPDATE inventory_sync_profiles SET enabled = TRUE, next_run_at = NOW() + INTERVAL '40 minutes' "
            "WHERE id = %s", (pid,))


@pytest.fixture
def synced(test_tenant, src_db, src_table):
    tid = test_tenant["id"]
    _put(src_db, src_table, [("A1", "Norte", 10, 2.5), ("B2", "Sur", 5, 1.0)])
    p = _profile(tid, _source(tid), src_table, name="ERP Bodega")
    assert _run(tid, p["id"])["status"] == "success"
    return tid, p


def _get(client, headers, **params):
    r = client.get(URL, params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_live_sync_reports_its_keys_fields_and_next_run(client, synced):
    tid, p = synced
    _enable(p["id"])
    data = _get(client, _headers(tid))
    keys = {(i["sku"], i["warehouse"]): i for i in data["items"]}
    assert set(keys) == {("A1", "Norte"), ("B2", "Sur")}
    assert keys[("A1", "Norte")]["profile_id"] == p["id"]
    assert set(keys[("A1", "Norte")]["fields"]) == {"current_stock", "unit_cost"}
    (prof,) = data["profiles"]
    assert prof["name"] == "ERP Bodega" and prof["push"] is False
    nxt = profiles.get(tid, p["id"])["next_run_at"]
    assert prof["next_sync_at"] == nxt.isoformat()
    assert data["truncated"] is False


def test_a_switched_off_sync_owns_nothing(client, synced):
    tid, _ = synced
    assert _get(client, _headers(tid))["items"] == []


def test_skus_narrow_the_answer_and_a_huge_list_is_refused(client, synced):
    tid, p = synced
    _enable(p["id"])
    h = _headers(tid)
    assert [i["sku"] for i in _get(client, h, skus="B2,ZZ")["items"]] == ["B2"]
    assert _get(client, h, skus=" , ")["items"] == []
    r = client.get(URL, params={"skus": ",".join(f"S{i}" for i in range(1001))}, headers=h)
    assert r.status_code == 422 and r.json()["error_code"] == "inventory_sync_too_many_skus"


def test_the_precedence_winner_owns_the_key(client, synced, src_db, src_table):
    tid, first = synced
    _enable(first["id"])
    second = _profile(tid, _source(tid), src_table, name="Better", priority=10)
    profiles.mark_dry_run(tid, second["id"], profiles.get(tid, second["id"])["config_hash"])
    assert _run(tid, second["id"])["status"] == "success"
    _enable(second["id"])
    items = _get(client, _headers(tid))["items"]
    assert {i["profile_id"] for i in items} == {second["id"]}
    # The winner switched off: it will not write, and the loser is skipped by
    # the apply step while the winner holds the key, so nobody overwrites it.
    execute("UPDATE inventory_sync_profiles SET enabled = FALSE WHERE id = %s", (second["id"],))
    assert _get(client, _headers(tid))["items"] == []


def test_push_and_on_demand_syncs_have_no_predictable_time(client, synced):
    tid, p = synced
    execute("UPDATE inventory_sync_profiles SET freshness = 'on_demand' WHERE id = %s", (p["id"],))
    (prof,) = _get(client, _headers(tid))["profiles"]
    assert prof["freshness"] == "on_demand" and prof["next_sync_at"] is None
    execute("UPDATE inventory_sync_profiles SET freshness = 'scheduled', enabled = TRUE, source_kind = 'push' "
            "WHERE id = %s", (p["id"],))
    (prof,) = _get(client, _headers(tid))["profiles"]
    assert prof["push"] is True and prof["next_sync_at"] is None


def test_a_warehouse_scoped_user_sees_only_their_warehouses(client, synced):
    tid, p = synced
    _enable(p["id"])
    data = _get(client, _headers(tid, "analyst", scope=["Norte"]))
    assert [(i["sku"], i["warehouse"]) for i in data["items"]] == [("A1", "Norte")]
    assert "Sur" not in json.dumps(data)


def test_another_tenant_sees_nothing(client, synced, make_tenant_user_headers):
    tid, p = synced
    _enable(p["id"])
    other = make_tenant_user_headers(role="admin")
    data = _get(client, other)
    assert data == {"items": [], "profiles": [], "truncated": False}


def test_requires_a_signed_in_user(client, synced):
    assert client.get(URL).status_code in (401, 403)
