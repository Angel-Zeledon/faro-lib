"""Warehouse and supplier masters (backend/inventory_sync/masters.py).

Stock rows carry codes; a master table says what each code is. Asserted on the
database: which warehouses exist, under which names stock landed, the
remembered code links, suppliers and their lead-time provenance.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.inventory_sync import masters, profiles
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    _booted, _put, _run, _source, _stock, src_db, src_table,
)

URL = "/api/v1/inventory-sync"


@pytest.fixture
def wh_master(src_db):
    name = f"wh_{uuid4().hex[:8]}"
    with src_db.cursor() as cur:
        cur.execute(f"CREATE TABLE {name} (code TEXT, descr TEXT, status TEXT, kind TEXT, city TEXT)")
    yield name
    with src_db.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {name}")


@pytest.fixture
def sup_master(src_db):
    name = f"sup_{uuid4().hex[:8]}"
    with src_db.cursor() as cur:
        cur.execute(f"CREATE TABLE {name} (vendor TEXT, vname TEXT, lead INT, terms TEXT, mail TEXT)")
    yield name
    with src_db.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {name}")


def _fill(src_db, table, rows):
    with src_db.cursor() as cur:
        cur.execute(f"DELETE FROM {table}")
        for r in rows:
            cur.execute(f"INSERT INTO {table} VALUES ({', '.join(['%s'] * len(r))})", r)


def _wh_cfg(table):
    return {"warehouses": {"source_kind": "table", "source_schema": "public", "source_object": table,
                           "code_column": "code", "name_column": "descr", "active_column": "status",
                           "kind_column": "kind", "city_column": "city"}}


def _profile(tid, sid, table, **kw):
    body = {"name": f"m-{uuid4().hex[:4]}", "data_source_id": sid, "source_kind": "table",
            "source_schema": "public", "source_object": table,
            "mapping": {"sku": "codigo", "warehouse": "almacen", "current_stock": "existencia"},
            "interval_minutes": 60}
    body.update(kw)
    p = profiles.create(tid, "usr_test", body)
    return profiles.get(tid, p["id"])


def _warehouses(tid):
    return {r["name"] for r in query("SELECT name FROM warehouses WHERE tenant_id = %s", (tid,))}


class TestNormalisation:

    @pytest.mark.parametrize("a,b", [("007", "7"), (" Bodega  Norte ", "bodega norte"), ("Almacén", "ALMACEN"),
                                     ("0", "000")])
    def test_codes_that_must_match(self, a, b):
        assert masters.norm_code(a) == masters.norm_code(b)

    def test_codes_that_must_not(self):
        assert masters.norm_code("A07") != masters.norm_code("A7")
        assert masters.norm_code("10") != masters.norm_code("1")

    def test_a_master_needs_code_and_name_columns(self):
        from backend.errors import AppError
        with pytest.raises(AppError) as e:
            masters.normalize({"warehouses": {"source_object": "t", "code_column": "c"}}, has_source=True)
        assert e.value.code == "inventory_sync_master_invalid"
        with pytest.raises(AppError):
            masters.normalize({"warehouses": {"source_object": "t", "code_column": "c", "name_column": "n"}},
                              has_source=False)            # a push profile has no source to read it from


class TestWarehouseMaster:

    def test_codes_become_master_names_and_links_are_remembered(self, test_tenant, src_db, src_table, wh_master):
        tid = test_tenant["id"]
        _fill(src_db, wh_master, [("1", "Bodega Norte", "A", "DC", "Quito"), ("2", "Sur", "A", "store", None),
                                  ("7", "Tienda Centro", "I", "store", None)])
        _put(src_db, src_table, [("A1", "01", 10), ("B2", "2", 5), ("C3", "007", 3)])
        p = _profile(tid, _source(tid), src_table, masters=_wh_cfg(wh_master))
        dry = __import__("backend.inventory_sync.engine", fromlist=["x"]).dry_run(tid, dict(p))
        assert set(dry["masters"]["warehouses"]["new"]) == {"Bodega Norte", "Sur", "Tienda Centro"}
        assert dry["masters"]["warehouses"]["inactive"] == ["Tienda Centro"]
        assert _warehouses(tid) == set() or "Bodega Norte" not in _warehouses(tid)   # the dry run wrote nothing
        run = _run(tid, p["id"])
        assert run["status"] == "success", (run["error_code"], run["error_params"])
        stock = {k: float(v["current_stock"]) for k, v in _stock(tid).items()}
        assert stock == {("A1", "Bodega Norte"): 10, ("B2", "Sur"): 5, ("C3", "Tienda Centro"): 3}
        links = {r["source_code"]: r for r in query(
            "SELECT * FROM inventory_sync_warehouses WHERE profile_id = %s", (p["id"],))}
        assert links["1"]["warehouse_name"] == "Bodega Norte" and links["1"]["city"] == "Quito"
        assert links["7"]["active"] is False
        assert "inventory_sync_warehouse_inactive" in (run["warnings"] or {}).get("codes", [])

    def test_a_rename_in_the_source_keeps_stockai_name_and_is_reported(self, test_tenant, src_db, src_table,
                                                                      wh_master):
        tid = test_tenant["id"]
        _fill(src_db, wh_master, [("1", "Bodega Norte", "A", None, None)])
        _put(src_db, src_table, [("A1", "1", 10)])
        p = _profile(tid, _source(tid), src_table, masters=_wh_cfg(wh_master))
        assert _run(tid, p["id"])["status"] == "success"
        _fill(src_db, wh_master, [("1", "Norte Principal", "A", None, None)])
        _put(src_db, src_table, [("A1", "1", 12)])
        run = _run(tid, p["id"])
        assert run["status"] == "success"
        assert "inventory_sync_warehouse_renamed_in_source" in run["warnings"]["codes"]
        assert "Norte Principal" not in _warehouses(tid)                      # no duplicate warehouse
        assert float(_stock(tid)[("A1", "Bodega Norte")]["current_stock"]) == 12
        link = query_one("SELECT * FROM inventory_sync_warehouses WHERE profile_id = %s", (p["id"],))
        assert (link["warehouse_name"], link["source_name"]) == ("Bodega Norte", "Norte Principal")

    def test_a_code_the_master_stops_listing_is_inactive_not_deleted(self, test_tenant, src_db, src_table,
                                                                    wh_master):
        tid = test_tenant["id"]
        _fill(src_db, wh_master, [("1", "Norte", "A", None, None), ("2", "Sur", "A", None, None)])
        _put(src_db, src_table, [("A1", "1", 10), ("B2", "2", 5)])
        p = _profile(tid, _source(tid), src_table, masters=_wh_cfg(wh_master), brake_max_vanished_pct=100,
                     brake_min_row_ratio=0)
        assert _run(tid, p["id"])["status"] == "success"
        _fill(src_db, wh_master, [("1", "Norte", "A", None, None)])
        _put(src_db, src_table, [("A1", "1", 10)])
        assert _run(tid, p["id"])["status"] == "success"
        sur = query_one("SELECT active FROM inventory_sync_warehouses WHERE profile_id = %s AND source_code = '2'",
                        (p["id"],))
        assert sur["active"] is False and "Sur" in _warehouses(tid)
        assert float(_stock(tid)[("B2", "Sur")]["current_stock"]) == 5          # its stock is not erased

    @pytest.mark.parametrize("policy,expect", [("reject", "rejected"), ("default", "defaulted"),
                                               ("create", "created")])
    def test_unknown_codes_follow_the_policy(self, test_tenant, src_db, src_table, wh_master, policy, expect):
        tid = test_tenant["id"]
        _fill(src_db, wh_master, [("1", "Norte", "A", None, None)])
        _put(src_db, src_table, [("A1", "1", 10), ("Z9", "99", 4)])
        p = _profile(tid, _source(tid), src_table, masters=_wh_cfg(wh_master), unknown_warehouse=policy,
                     default_warehouse="Norte", brake_max_rejected_pct=100)
        run = _run(tid, p["id"])
        assert run["status"] == "success", (run["error_code"], run["error_params"])
        stock = {k: float(v["current_stock"]) for k, v in _stock(tid).items()}
        if expect == "rejected":
            assert stock == {("A1", "Norte"): 10} and run["rejected_sample"][0]["code"] == "unknown_warehouse"
            assert "99" not in _warehouses(tid)
        elif expect == "defaulted":
            assert stock == {("A1", "Norte"): 10, ("Z9", "Norte"): 4}
            assert "inventory_sync_warehouse_defaulted" in run["warnings"]["codes"]
        else:
            assert stock == {("A1", "Norte"): 10, ("Z9", "99"): 4} and "99" in _warehouses(tid)

    def test_codes_differing_by_case_accents_zeros_are_one_warehouse_and_collisions_named(
            self, test_tenant, src_db, src_table, wh_master):
        tid = test_tenant["id"]
        _fill(src_db, wh_master, [("A1", "Almacén", "A", None, None), ("a1 ", "Otro nombre", "A", None, None)])
        _put(src_db, src_table, [("S1", "A1", 1), ("S2", " a1", 2)])
        p = _profile(tid, _source(tid), src_table, masters=_wh_cfg(wh_master))
        run = _run(tid, p["id"])
        assert run["status"] == "success"
        assert {w for (_, w) in _stock(tid)} == {"Almacén"}
        assert "inventory_sync_master_code_collision" in run["warnings"]["codes"]

    def test_a_rerun_changes_nothing(self, test_tenant, src_db, src_table, wh_master):
        tid = test_tenant["id"]
        _fill(src_db, wh_master, [("1", "Norte", "A", None, None)])
        _put(src_db, src_table, [("A1", "1", 10)])
        p = _profile(tid, _source(tid), src_table, masters=_wh_cfg(wh_master))
        _run(tid, p["id"])
        before = (len(_warehouses(tid)), query("SELECT * FROM inventory_sync_warehouses WHERE profile_id = %s",
                                               (p["id"],))[0]["warehouse_name"])
        again = _run(tid, p["id"])
        assert (again["created"], again["updated"]) == (0, 0)
        assert (len(_warehouses(tid)), query("SELECT * FROM inventory_sync_warehouses WHERE profile_id = %s",
                                             (p["id"],))[0]["warehouse_name"]) == before

    def test_without_a_master_the_v1_rule_holds(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Nueva", 1)])
        p = _profile(tid, _source(tid), src_table, brake_max_rejected_pct=100)
        run = _run(tid, p["id"])
        assert run["rejected_sample"][0]["code"] == "unknown_warehouse" and _stock(tid) == {}


class TestSupplierMaster:

    def _cfg(self, table, **kw):
        return {"suppliers": {"source_kind": "table", "source_schema": "public", "source_object": table,
                              "code_column": "vendor", "name_column": "vname", "lead_time_column": "lead",
                              "payment_terms_column": "terms", "email_column": "mail", **kw}}

    def test_suppliers_are_created_with_a_declared_lead_time_and_rows_name_them(self, test_tenant, src_db,
                                                                             src_table, sup_master):
        tid = test_tenant["id"]
        _fill(src_db, sup_master, [("V1", "Distribuidora Andina", 12, "30 días", "compras@andina.test"),
                                   ("V2", "Lácteos del Sur", None, None, None)])
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} ADD COLUMN prov TEXT")
        _put(src_db, src_table, [("A1", "Norte", 10)])
        with src_db.cursor() as cur:
            cur.execute(f"UPDATE {src_table} SET prov = 'v1'")
        p = _profile(tid, _source(tid), src_table, allow_new_warehouses=True,
                     mapping={"sku": "codigo", "warehouse": "almacen", "current_stock": "existencia",
                              "supplier": "prov"}, masters=self._cfg(sup_master))
        run = _run(tid, p["id"])
        assert run["status"] == "success", (run["error_code"], run["error_params"])
        s = query_one("SELECT * FROM suppliers WHERE tenant_id = %s AND name = 'Distribuidora Andina'", (tid,))
        assert s["lead_time_days"] == 12 and s["lead_time_set_by"] == "sync" and s["email"] == "compras@andina.test"
        nolead = query_one("SELECT * FROM suppliers WHERE tenant_id = %s AND name = 'Lácteos del Sur'", (tid,))
        assert nolead is not None and nolead["lead_time_set_by"] is None       # NULL lead time: nothing declared
        assert _stock(tid)[("A1", "Norte")]["supplier"] == "Distribuidora Andina"

    def test_a_lead_time_a_person_set_is_kept_unless_the_source_wins(self, test_tenant, src_db, src_table,
                                                                    sup_master):
        tid = test_tenant["id"]
        from backend.inventory import supplier_service as sup
        own = sup.create_supplier(tid, {"name": "Distribuidora Andina", "lead_time_days": 20})
        assert own["lead_time_set_by"] == "user"
        _fill(src_db, sup_master, [("V1", "Distribuidora Andina", 12, None, None)])
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table, allow_new_warehouses=True, masters=self._cfg(sup_master))
        run = _run(tid, p["id"])
        assert "inventory_sync_supplier_lead_time_kept" in run["warnings"]["codes"]
        assert query_one("SELECT lead_time_days FROM suppliers WHERE id = %s", (own["id"],))["lead_time_days"] == 20
        profiles.update(tid, p["id"], {"masters": self._cfg(sup_master, source_wins=True)})
        _run(tid, p["id"], override_brake=True)
        row = query_one("SELECT lead_time_days, lead_time_set_by FROM suppliers WHERE id = %s", (own["id"],))
        assert (row["lead_time_days"], row["lead_time_set_by"]) == (12, "sync")

    def test_a_supplier_the_source_drops_is_not_deleted(self, test_tenant, src_db, src_table, sup_master):
        tid = test_tenant["id"]
        _fill(src_db, sup_master, [("V1", "Uno", 5, None, None), ("V2", "Dos", 5, None, None)])
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table, allow_new_warehouses=True, masters=self._cfg(sup_master))
        _run(tid, p["id"])
        _fill(src_db, sup_master, [("V1", "Uno", 5, None, None)])
        _run(tid, p["id"])
        assert query_one("SELECT active FROM suppliers WHERE tenant_id = %s AND name = 'Dos'", (tid,))["active"]
        assert query_one("SELECT active FROM inventory_sync_suppliers WHERE profile_id = %s AND source_code = 'v2'",
                         (p["id"],))["active"] is False


class TestApi:

    def test_saving_masters_needs_an_analyst(self, client, test_tenant, analyst_headers, viewer_headers,
                                             src_table, wh_master):
        tid = test_tenant["id"]
        sid = _source(tid)
        body = {"name": "m", "data_source_id": sid, "source_kind": "table", "source_schema": "public",
                "source_object": src_table, "mapping": {"sku": "codigo", "warehouse": "almacen",
                                                        "current_stock": "existencia"},
                "masters": _wh_cfg(wh_master), "unknown_warehouse": "default"}
        n0 = query_one("SELECT COUNT(*) AS n FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,))["n"]
        assert client.post(f"{URL}/profiles", json=body, headers=viewer_headers).status_code == 403
        assert query_one("SELECT COUNT(*) AS n FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,))["n"] == n0
        r = client.post(f"{URL}/profiles", json=body, headers=analyst_headers)
        assert r.status_code in (200, 201), r.text
        row = query_one("SELECT masters, unknown_warehouse FROM inventory_sync_profiles WHERE id = %s",
                        (r.json()["data"]["id"],))
        assert row["masters"]["warehouses"]["code_column"] == "code" and row["unknown_warehouse"] == "default"
        bad = client.post(f"{URL}/profiles", json={**body, "name": "m2", "unknown_warehouse": "maybe"},
                          headers=analyst_headers)
        assert bad.status_code == 422 and bad.json()["error_code"] == "inventory_sync_master_invalid"
