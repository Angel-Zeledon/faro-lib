"""Inventory sync and users limited to some warehouses.

A sync profile reads the WHOLE source and writes every warehouse it names, so
it is company configuration: a warehouse-scoped user may not create, run,
refresh, preview or diagnose one, and may not read its stock-bearing outputs
(the reconcile view, run details, rejected-row CSV, health). The freshness chip
carries no stock and no warehouse, so they see it - with `can_refresh: false`.
An unrestricted analyst keeps every capability (the permission pair).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query, query_one
from backend.inventory_sync import profiles
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    _booted, _profile, _put, _run, _source, _stock, src_db, src_table,
)
from backend.users import service as user_svc

URL = "/api/v1/inventory-sync"
COMPANY_TOTALS = "warehouse_scope_company_totals"
COMPANY_SETTING = "warehouse_scope_company_setting"


@pytest.fixture
def world(client, test_tenant, src_db, src_table):
    tid = test_tenant["id"]
    _put(src_db, src_table, [("A1", "Norte", 10), ("B2", "Sur", 5)])
    p = _profile(tid, _source(tid), src_table)
    profiles.mark_dry_run(tid, p["id"], profiles.get(tid, p["id"])["config_hash"])
    execute("UPDATE inventory_sync_profiles SET enabled = TRUE WHERE id = %s", (p["id"],))
    assert _run(tid, p["id"])["status"] == "success"
    wh = {r["name"]: r["id"] for r in query("SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))}

    def person(scope=None):
        u = user_svc.create_user(tenant_id=tid, email=f"an-{uuid4().hex[:8]}@stockai-e2e.io",
                                 password="TestPass123!", role="analyst")
        user_svc.mark_verified(tid, u["id"])
        if scope:
            execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                    (json.dumps([wh[n] for n in scope]), u["id"]))
        return {"Authorization": f"Bearer {create_access_token(u['id'], tid, 'analyst', email_verified=True)}"}

    return {"tid": tid, "pid": p["id"], "client": client, "norte": person(["Norte"]), "boss": person()}


def _code(r):
    return r.json().get("error_code")


def test_scoped_user_cannot_read_company_stock_through_reconcile(world):
    c, pid = world["client"], world["pid"]
    r = c.get(f"{URL}/profiles/{pid}/reconcile?sku=B2", headers=world["norte"])   # B2 lives in Sur
    assert r.status_code == 403 and _code(r) == COMPANY_TOTALS
    assert "current_stock" not in r.text and "Sur" not in r.text
    # The unrestricted analyst passes the scope gate: this profile is a snapshot,
    # so the answer is the model refusal, not the scope one.
    ok = c.get(f"{URL}/profiles/{pid}/reconcile?sku=B2", headers=world["boss"])
    assert ok.status_code == 422 and _code(ok) == "inventory_sync_model_invalid"


@pytest.mark.parametrize("path", ["/profiles", "/profiles/{pid}", "/profiles/{pid}/runs",
                                  "/profiles/{pid}/health", "/health", "/profiles/{pid}/export"])
def test_scoped_user_cannot_read_profiles_runs_or_health(world, path):
    c = world["client"]
    r = c.get(URL + path.format(pid=world["pid"]), headers=world["norte"])
    assert r.status_code == 403 and _code(r) == COMPANY_TOTALS
    assert c.get(URL + path.format(pid=world["pid"]), headers=world["boss"]).status_code in (200, 503)


def test_scoped_user_cannot_see_rejected_rows_of_other_warehouses(world):
    c, tid, pid = world["client"], world["tid"], world["pid"]
    run = query_one("SELECT id FROM inventory_sync_runs WHERE profile_id = %s ORDER BY started_at DESC LIMIT 1",
                    (pid,))
    for path in (f"/profiles/{pid}/runs/{run['id']}", f"/profiles/{pid}/runs/{run['id']}/rejected.csv"):
        r = c.get(URL + path, headers=world["norte"])
        assert r.status_code == 403 and _code(r) == COMPANY_TOTALS
        assert c.get(URL + path, headers=world["boss"]).status_code == 200


def test_scoped_user_cannot_refresh_or_run_and_stock_stays(world, src_db, src_table):
    c, tid, pid = world["client"], world["tid"], world["pid"]
    _put(src_db, src_table, [("A1", "Norte", 99), ("B2", "Sur", 99)])
    before = {k: float(v["current_stock"]) for k, v in _stock(tid).items()}
    runs_before = query_one("SELECT COUNT(*) AS n FROM inventory_sync_runs WHERE profile_id = %s", (pid,))["n"]
    for method, path, body in (("post", "/refresh", {"skus": ["A1"]}), ("post", "/refresh", {}),
                               ("post", f"/profiles/{pid}/run", {}), ("post", f"/profiles/{pid}/dry-run", None),
                               ("post", f"/profiles/{pid}/doctor", None), ("post", f"/profiles/{pid}/disable", None),
                               ("delete", f"/profiles/{pid}", None)):
        kw = {"json": body} if body is not None else {}
        r = getattr(c, method)(URL + path, headers=world["norte"], **kw)
        assert r.status_code == 403 and _code(r) == COMPANY_SETTING, (path, r.text)
    assert {k: float(v["current_stock"]) for k, v in _stock(tid).items()} == before
    assert query_one("SELECT COUNT(*) AS n FROM inventory_sync_runs WHERE profile_id = %s",
                     (pid,))["n"] == runs_before
    assert profiles.get(tid, pid)["enabled"] is True
    # The unrestricted analyst can.
    r = c.post(f"{URL}/refresh", headers=world["boss"], json={"wait_seconds": 20})
    assert r.status_code == 200
    assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 99


def test_freshness_is_visible_to_a_scoped_user_without_refresh(world):
    c = world["client"]
    r = c.get(f"{URL}/freshness", headers=world["norte"])
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["can_refresh"] is False and "current_stock" not in r.text
    assert c.get(f"{URL}/freshness", headers=world["boss"]).json()["data"]["can_refresh"] is True
