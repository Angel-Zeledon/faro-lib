"""Developer-facing inventory sync: profiles as code, idempotency keys, API-key
parity, health for monitoring, run detail + CSV, the doctor, recipes, the
transform tester. State-based where state changes.
"""

from __future__ import annotations

import json

import pytest

from backend.db.connection import execute, query, query_one
from backend.inventory_sync import document, engine, profiles
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    MAPPING, _booted, _profile, _put, _run, _source, _stock, src_db, src_table,
)
from backend.tests.test_public_api_with_a_key import key_headers, read_only_headers  # noqa: F401

URL = "/api/v1/inventory-sync"


def _doc(table, slug="erp-stock", **over):
    d = {
        "schema": "stockai.inventory-sync/v1", "slug": slug, "name": "ERP stock",
        "source": {"data_source": {"ref": "ERP"}, "kind": "table", "schema": "public", "object": table},
        "mapping": {"sku": {"column": "codigo"}, "warehouse": {"column": "almacen"},
                    "current_stock": {"column": "existencia"}},
        "policies": {"allow_new_warehouses": True},
    }
    d.update(over)
    return d


class TestProfileDocuments:

    def test_schema_is_valid_json_schema_and_accepts_an_export(self, test_tenant, src_db, src_table):
        jsonschema = pytest.importorskip("jsonschema")
        schema = document.json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        tid = test_tenant["id"]
        p = _profile(tid, _source(tid), src_table)
        exported = document.from_profile(profiles.get(tid, p["id"]))
        jsonschema.Draft202012Validator(schema).validate(exported)
        assert "password" not in json.dumps(exported).lower()

    def test_an_export_keeps_the_masters_and_the_unknown_warehouse_rule(
            self, client, analyst_headers, test_tenant, src_db, src_table):
        # Found walking /sincronizacion: "profile as code" dropped the
        # warehouse / supplier masters and the unknown-warehouse rule, so a
        # profile applied from its own export rejected every row whose
        # warehouse was a code the master used to translate.
        jsonschema = pytest.importorskip("jsonschema")
        tid = test_tenant["id"]
        masters = {"warehouses": {"source_kind": "table", "source_schema": "public", "source_object": "whm",
                                  "code_column": "code", "name_column": "descr", "city_column": "city"},
                   "suppliers": {"source_kind": "table", "source_schema": "public", "source_object": "supm",
                                 "code_column": "vendor", "name_column": "vname", "lead_time_column": "lead",
                                 "source_wins": True}}
        p = _profile(tid, _source(tid), src_table, masters=masters, unknown_warehouse="default",
                     default_warehouse="Central")
        exported = document.from_profile(profiles.get(tid, p["id"]))
        jsonschema.Draft202012Validator(document.json_schema()).validate(exported)
        assert document.validate(exported) == []
        exported["slug"], exported["name"] = "copy-of-erp", "Copy"
        r = client.post(f"{URL}/profiles/apply", json=exported, headers=analyst_headers)
        assert r.status_code == 200, r.text
        copy = profiles.get(tid, r.json()["data"]["profile_id"])
        original = profiles.get(tid, p["id"])
        assert copy["masters"] == original["masters"]
        assert copy["masters"]["warehouses"]["source_object"] == "whm"
        assert copy["masters"]["suppliers"]["source_wins"] is True
        assert copy["unknown_warehouse"] == "default"

    def test_apply_creates_then_is_idempotent_by_slug(self, client, analyst_headers, test_tenant,
                                                      src_db, src_table):
        tid = test_tenant["id"]
        _source(tid)
        r = client.post(f"{URL}/profiles/apply", json=_doc(src_table), headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["action"] == "created"
        r2 = client.post(f"{URL}/profiles/apply", json=_doc(src_table), headers=analyst_headers)
        assert r2.json()["data"]["action"] == "unchanged"
        assert len(query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,))) == 1
        changed = _doc(src_table)
        changed["mapping"]["current_stock"]["multiplier"] = 12
        r3 = client.post(f"{URL}/profiles/apply", json=changed, headers=analyst_headers)
        assert r3.json()["data"]["action"] == "updated"
        row = query_one("SELECT mapping FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,))
        assert row["mapping"]["current_stock"]["multiplier"] == 12

    def test_errors_name_the_exact_path(self, client, analyst_headers, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _source(tid)
        bad = _doc(src_table)
        bad["mapping"]["current_stock"]["cast"] = "cast_int"
        bad["brake"] = {"max_vanished_pct": 400}
        bad["surprise"] = 1
        r = client.post(f"{URL}/profiles/apply", json=bad, headers=analyst_headers)
        assert r.status_code == 422
        paths = {e["path"] for e in r.json()["error_params"]["errors"]}
        assert {"mapping.current_stock.cast", "brake.max_vanished_pct", "surprise"} <= paths
        assert query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,)) == []
        missing_src = _doc(src_table)
        missing_src["source"]["data_source"]["ref"] = "Nope"
        r = client.post(f"{URL}/profiles/apply", json=missing_src, headers=analyst_headers)
        assert r.json()["error_params"]["path"] == "source.data_source.ref"

    def test_yaml_round_trip_and_dry_run(self, client, analyst_headers, test_tenant, src_db, src_table):
        import yaml
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        _source(tid)
        body = yaml.safe_dump(_doc(src_table))
        r = client.post(f"{URL}/profiles/apply?dry_run=true", content=body,
                        headers={**analyst_headers, "Content-Type": "application/yaml"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["dry_run"] and data["preview"]["would_create"] == 1
        assert query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,)) == []
        assert _stock(tid) == {}
        client.post(f"{URL}/profiles/apply", content=body,
                    headers={**analyst_headers, "Content-Type": "application/yaml"})
        pid = query_one("SELECT id FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,))["id"]
        out = client.get(f"{URL}/profiles/{pid}/export?format=yaml", headers=analyst_headers)
        assert out.status_code == 200 and yaml.safe_load(out.text)["slug"] == "erp-stock"

    def test_viewer_cannot_apply(self, client, viewer_headers, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _source(tid)
        r = client.post(f"{URL}/profiles/apply", json=_doc(src_table), headers=viewer_headers)
        assert r.status_code == 403
        assert query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,)) == []


class TestIdempotencyAndKeys:

    def test_idempotency_key_creates_once(self, client, analyst_headers, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        sid = _source(tid)
        body = {"name": "x", "data_source_id": sid, "source_kind": "table", "source_schema": "public",
                "source_object": src_table, "mapping": MAPPING}
        h = {**analyst_headers, "Idempotency-Key": "ci-run-42"}
        a = client.post(f"{URL}/profiles", json=body, headers=h)
        b = client.post(f"{URL}/profiles", json=body, headers=h)
        assert a.status_code == 201 and b.status_code == 201
        assert a.json()["data"]["id"] == b.json()["data"]["id"]
        assert len(query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,))) == 1
        c = client.post(f"{URL}/profiles", json={**body, "name": "other"}, headers=h)
        assert c.status_code == 409 and c.json()["error_code"] == "inventory_sync_idempotency_key_reused"

    def test_write_key_configures_read_key_only_reads(self, client, key_headers, read_only_headers,
                                                      test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        sid = _source(tid)
        body = {"name": "k", "data_source_id": sid, "source_kind": "table", "source_schema": "public",
                "source_object": src_table, "mapping": MAPPING}
        r = client.post(f"{URL}/profiles", json=body, headers=read_only_headers)
        assert r.status_code == 403
        assert query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,)) == []
        r = client.post(f"{URL}/profiles", json=body, headers=key_headers)
        assert r.status_code == 201, r.text
        assert client.get(f"{URL}/profiles", headers=read_only_headers).status_code == 200
        assert client.get(f"{URL}/health", headers=read_only_headers).status_code == 200


class TestMonitoring:

    def test_health_is_200_then_503_strict_when_blocked(self, client, analyst_headers, test_tenant,
                                                        src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table)
        _run(tid, p["id"])
        r = client.get(f"{URL}/profiles/{p['id']}/health?strict=true", headers=analyst_headers)
        assert r.status_code == 200 and r.json()["data"]["status"] == "ok"
        assert r.json()["data"]["last_success_age_s"] is not None
        execute("UPDATE inventory_sync_profiles SET health = 'blocked', health_reason = 'data_source_auth_failed' "
                "WHERE id = %s", (p["id"],))
        r = client.get(f"{URL}/profiles/{p['id']}/health?strict=true", headers=analyst_headers)
        assert r.status_code == 503
        d = r.json()["data"]
        assert d["status"] == "blocked" and d["reason_category"] == "customer" and d["retries_on_its_own"] is False
        assert client.get(f"{URL}/health?strict=true", headers=analyst_headers).status_code == 503

    def test_run_detail_and_rejected_csv(self, client, analyst_headers, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10), ("", "Norte", 2), ("=cmd()", "Norte", -1),
                                 ("B2", "Norte", 3), ("C3", "Norte", 3), ("D4", "Norte", 3)])
        p = _profile(tid, _source(tid), src_table)
        run = _run(tid, p["id"])
        d = client.get(f"{URL}/profiles/{p['id']}/runs/{run['id']}", headers=analyst_headers).json()["data"]
        assert d["rejected"] == 2 and d["outcome"] is None
        csv_r = client.get(f"{URL}/profiles/{p['id']}/runs/{run['id']}/rejected.csv", headers=analyst_headers)
        assert csv_r.status_code == 200 and "text/csv" in csv_r.headers["content-type"]
        body = csv_r.text
        assert "sku_blank" in body and "negative_quantity" in body and "'=cmd()" in body


class TestDoctorAndHelp:

    def test_doctor_reports_ok_then_names_the_missing_column(self, client, analyst_headers, test_tenant,
                                                             src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table, mode="incremental", watermark_column="updated_at")
        r = client.post(f"{URL}/profiles/{p['id']}/doctor", headers=analyst_headers).json()["data"]
        assert r["ok"] is True, r["first_failure"]
        stages = {s["stage"]: s for s in r["stages"]}
        assert stages["columns"]["status"] == "ok" and stages["sample"]["detail"]["rows"] == 1
        assert stages["index"]["status"] == "warning" and "CREATE INDEX" in stages["index"]["detail"]["suggestion"]
        assert "password" not in json.dumps(r["diagnostic"]).lower()
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} RENAME COLUMN existencia TO stock_now")
        r = client.post(f"{URL}/profiles/{p['id']}/doctor", headers=analyst_headers).json()["data"]
        assert r["ok"] is False and r["first_failure"]["stage"] == "columns"
        assert r["first_failure"]["params"]["column"] == "existencia"
        assert r["first_failure"]["remedy_key"] == "isync.remedy.inventory_sync_column_missing"

    def test_schema_drift_pauses_with_the_column_named(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table)
        _run(tid, p["id"])
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} RENAME COLUMN almacen TO bodega")
        run = _run(tid, p["id"])
        assert run["status"] == "failed" and run["error_code"] == "inventory_sync_column_missing"
        assert run["error_params"]["column"] == "almacen"
        row = profiles.get(tid, p["id"])
        assert row["health"] == "paused" and row["health_reason"] == "inventory_sync_column_missing"
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 10

    def test_recipes_transform_tester_and_view(self, client, analyst_headers, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        sid = _source(tid)
        rec = client.get(f"{URL}/recipes", headers=analyst_headers).json()["data"]
        assert set(rec["grants"]) == {"postgresql", "mysql", "mssql", "oracle", "mongodb"}
        assert "sap_ecc_mard" in rec["examples"] and "MANDT" in rec["examples"]["sap_ecc_mard"]["sql"]
        # Found walking /sincronizacion in Spanish: each title ended in English
        # "(unverified against a live system)". The flag travels as data and
        # the page words it; the title carries no prose.
        mard = rec["examples"]["sap_ecc_mard"]
        assert (mard["verified"], mard["engine"], mard["vendor"]) == (False, "oracle", "SAP")
        assert mard["product"] == "SAP ECC (on Oracle / SQL Server)"
        assert not any("unverified" in ex["title"] for ex in rec["examples"].values())
        assert "python_push" in rec["forwarders"]
        t = client.post(f"{URL}/transform-test", json={"field": "current_stock",
                        "spec": {"multiplier": 12, "decimal": "comma"}, "value": "1,5"},
                        headers=analyst_headers).json()["data"]
        assert t == {"ok": True, "output": 18.0}
        t = client.post(f"{URL}/transform-test", json={"field": "current_stock", "spec": {}, "value": "N/D"},
                        headers=analyst_headers).json()["data"]
        assert t["ok"] is False and t["code"] == "not_a_number"
        v = client.post(f"{URL}/sources/{sid}/suggest-view", json={
            "source_kind": "table", "source_schema": "public", "source_object": src_table,
            "mapping": {"sku": {"column": "codigo"}, "current_stock": {"column": "existencia"}}},
            headers=analyst_headers).json()["data"]
        assert v["sql"].startswith("CREATE OR REPLACE VIEW stockai_inventory") and '"codigo" AS sku' in v["sql"]

    def test_a_view_with_canonical_names_maps_at_full_confidence(self):
        from backend.inventory_sync.suggest import suggest_mapping
        m = suggest_mapping([{"name": "sku"}, {"name": "warehouse"}, {"name": "current_stock"},
                             {"name": "unit_cost"}])["mapping"]
        assert all(m[f]["confidence"] == 1.0 for f in ("sku", "warehouse", "current_stock", "unit_cost"))
