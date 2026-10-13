"""Stock that arrived before any forecast is listed, honestly.

A customer who connects the ERP first (the order /sincronizacion suggests) had
300 synced rows in `inventory_stock` and an Inventario that said "0 SKUs":
the status endpoint answered 400 without a completed session and the screen
showed the welcome card. `GET /inventory/status?without_forecast=true` now
lists every stock row as SIN_DATOS (no demand, no recommendation), and the
freshness chip reports a sync that ran but was never switched on.

Nothing that plans may start counting these rows: no session, no
recommendation log, the default request still answers 400.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from backend.auth.jwt_handler import create_access_token
from backend.db.connection import execute, query, query_one
from backend.inventory import service as inv_svc
from backend.inventory_sync import profiles, scheduler
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    _booted, _profile, _put, _run, _source, _stock, src_db, src_table,
)
from backend.users import service as user_svc

STATUS = "/api/v1/inventory/status"


def _hand(tid, sku, wh, qty, supplier=None):
    inv_svc._ensure_warehouse(tid, wh)
    execute("INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock, supplier, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, NOW())", (tid, sku, wh, qty, supplier))


def _headers(tid, role="analyst", scope=None):
    u = user_svc.create_user(tenant_id=tid, email=f"u-{uuid4().hex[:8]}@stockai-e2e.io",
                             password="TestPass123!", role=role)
    user_svc.mark_verified(tid, u["id"])
    if scope:
        ids = [r["id"] for r in query("SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))
               if r["name"] in scope]
        execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s", (json.dumps(ids), u["id"]))
    return {"Authorization": f"Bearer {create_access_token(u['id'], tid, role, email_verified=True)}"}


def _rec_log(tid) -> int:
    return query_one("SELECT COUNT(*) AS n FROM inventory_recommendation_log WHERE tenant_id = %s",
                     (tid,))["n"]


class TestStatusWithoutForecast:

    def test_the_default_request_still_refuses_without_a_session(self, client, test_tenant):
        tid = test_tenant["id"]
        _hand(tid, "A1", "Norte", 10)
        r = client.get(STATUS, headers=_headers(tid, "viewer"))
        assert r.status_code == 400 and r.json()["error_code"] == "no_completed_session"

    def test_every_stock_row_is_listed_as_sin_datos_with_no_recommendation(self, client, test_tenant,
                                                                         src_db, src_table):
        tid = test_tenant["id"]
        _hand(tid, "HAND1", "Norte", 4, supplier="Acme")
        _hand(tid, "HAND1", "Sur", 6, supplier="Acme")
        _put(src_db, src_table, [(f"SYNC{i}", "Norte", 10 + i, 2.5) for i in range(3)])
        p = _profile(tid, _source(tid), src_table)
        assert _run(tid, p["id"])["status"] == "success"
        log_before = _rec_log(tid)

        r = client.get(STATUS, params={"without_forecast": "true", "limit": 50},
                       headers=_headers(tid, "viewer"))

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["stock_only"] is True
        items = {i["sku"]: i for i in data["items"]}
        assert set(items) == {"HAND1", "SYNC0", "SYNC1", "SYNC2"}
        assert len(items) == len({k[0] for k in _stock(tid)})          # every SKU in the table
        for i in items.values():
            assert i["signal"] == "SIN_DATOS"
            assert i["recommended_qty"] is None and i["daily_demand"] is None
            assert i["coverage_days"] is None and i["has_forecast"] is False
        assert items["HAND1"]["current_stock"] == 10.0                  # summed over warehouses
        assert items["HAND1"]["supplier"] == "Acme"
        assert items["HAND1"]["warehouses"] == [{"warehouse": "Norte", "current_stock": 4.0},
                                                {"warehouse": "Sur", "current_stock": 6.0}]
        assert items["SYNC1"]["current_stock"] == 11.0 and items["SYNC1"]["unit_cost"] == 2.5
        s = data["summary"]
        assert (s["total_skus"], s["sin_datos"], s["ok"], s["order_now"], s["order_soon"],
                s["overstock"], s["with_forecast"]) == (4, 4, 0, 0, 0, 0, 0)
        assert data["page"]["total"] == 4
        # Nobody was told to buy anything: nothing is written to the recommendation log.
        assert _rec_log(tid) == log_before == 0

    def test_filters_and_paging_work_on_the_stock_list(self, client, test_tenant):
        tid = test_tenant["id"]
        for i in range(5):
            _hand(tid, f"P{i}", "Norte", i + 1, supplier="Acme" if i < 2 else "Beta")
        h = _headers(tid, "viewer")
        page = client.get(STATUS, params={"without_forecast": "true", "limit": 2, "offset": 2,
                                          "sort": "name"}, headers=h).json()["data"]
        assert [i["sku"] for i in page["items"]] == ["P2", "P3"] and page["page"]["total"] == 5
        acme = client.get(STATUS, params={"without_forecast": "true", "supplier": "acme"},
                          headers=h).json()["data"]
        assert {i["sku"] for i in acme["items"]} == {"P0", "P1"}
        assert client.get(STATUS, params={"without_forecast": "true", "signal": "OK"},
                          headers=h).json()["data"]["items"] == []

    def test_an_empty_account_gets_an_empty_list(self, client, test_tenant):
        tid = test_tenant["id"]
        r = client.get(STATUS, params={"without_forecast": "true", "limit": 50},
                       headers=_headers(tid, "viewer"))
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["items"] == [] and data["summary"]["total_skus"] == 0

    def test_a_warehouse_scoped_user_sees_only_their_warehouses(self, client, test_tenant):
        tid = test_tenant["id"]
        _hand(tid, "N1", "Norte", 3)
        _hand(tid, "S1", "Sur", 7)
        _hand(tid, "BOTH", "Norte", 1)
        _hand(tid, "BOTH", "Sur", 100)
        data = client.get(STATUS, params={"without_forecast": "true"},
                          headers=_headers(tid, "analyst", scope=["Norte"])).json()["data"]
        items = {i["sku"]: i for i in data["items"]}
        assert set(items) == {"N1", "BOTH"}
        assert items["BOTH"]["current_stock"] == 1.0
        assert items["BOTH"]["warehouses"] == [{"warehouse": "Norte", "current_stock": 1.0}]
        assert "Sur" not in json.dumps(data)

    def test_with_a_completed_session_the_flag_changes_nothing(self, client, auth_headers,
                                                               completed_session, registered_user):
        tid = registered_user["tenant"]["id"]
        _hand(tid, "NOT_IN_SESSION", "principal", 5)
        plain = client.get(STATUS, headers=auth_headers).json()["data"]
        flagged = client.get(STATUS, params={"without_forecast": "true"}, headers=auth_headers).json()["data"]
        for d in (plain, flagged):
            d.pop("computed_at", None)
        assert flagged == plain
        assert "stock_only" not in flagged
        assert "NOT_IN_SESSION" not in {i["sku"] for i in flagged["items"]}

    def test_by_warehouse_still_needs_a_session(self, client, test_tenant):
        tid = test_tenant["id"]
        _hand(tid, "A1", "Norte", 10)
        r = client.get(STATUS, params={"without_forecast": "true", "by_warehouse": "true"},
                       headers=_headers(tid, "viewer"))
        assert r.status_code == 400 and r.json()["error_code"] == "no_completed_session"


class TestFreshnessOfASyncThatWasNeverSwitchedOn:

    def test_the_chip_reports_it_and_refresh_runs_it(self, client, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table)
        profiles.mark_dry_run(tid, p["id"], profiles.get(tid, p["id"])["config_hash"])
        assert _run(tid, p["id"])["status"] == "success"           # "Sync now", never enabled
        assert profiles.get(tid, p["id"])["enabled"] is False

        fresh = client.get("/api/v1/inventory-sync/freshness", headers=_headers(tid, "viewer")).json()["data"]
        assert fresh["state"] == "stale"
        assert fresh["data_as_of"] is not None
        (only,) = fresh["profiles"]
        assert only["profile_id"] == p["id"] and only["enabled"] is False
        assert only["reason"] == "inventory_sync_disabled"

        _put(src_db, src_table, [("A1", "Norte", 12)])
        out = scheduler.refresh(tid, "usr_test", wait_s=20)
        assert [x["status"] for x in out["profiles"]] == ["done"]
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 12
        assert profiles.get(tid, p["id"])["enabled"] is False      # refreshing did not switch it on

    def test_a_live_sync_hides_a_switched_off_one(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        live = _profile(tid, _source(tid), src_table, name="Live")
        execute("UPDATE inventory_sync_profiles SET enabled = TRUE WHERE id = %s", (live["id"],))
        _profile(tid, _source(tid), src_table, name="Off")
        assert _run(tid, live["id"])["status"] == "success"
        fresh = scheduler.freshness(tid)
        assert fresh["state"] == "fresh"
        assert [x["name"] for x in fresh["profiles"]] == ["Live"]

    def test_no_sync_at_all_is_still_none(self, test_tenant):
        assert scheduler.freshness(test_tenant["id"]) == {"state": "none", "data_as_of": None, "profiles": []}

    def test_the_copy_explains_a_switched_off_sync(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[2] / "Frontend" / "src" / "i18n" / "translations.ts").read_text(
            encoding="utf-8")
        for key in ("'isync.code.inventory_sync_disabled'", "'isync.remedy.inventory_sync_disabled'"):
            assert text.count(key) == 2, key
