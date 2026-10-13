"""ERP recipe presets, schema advice and column projection.

* every preset renders on every engine it names, and what it renders is a
  plain read the SQL guard accepts;
* parameters land in SQL text, so anything outside their strict pattern is
  refused (no quote, comment or statement separator gets through);
* the generic presets run end to end on PostgreSQL and match an independent
  computation of the expected stock;
* the dry run warns about the schema gotchas (client filter, history rows,
  soft deletes) and a run only pulls the columns it uses (a 200 KB notes
  column is never read).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.datasources.sql_guard import validate_read_only_sql
from backend.db.connection import query_one
from backend.errors import AppError
from backend.inventory_sync import advice, engine, presets, profiles, reader
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    _booted, _run, _source, _stock, src_db,
)

URL = "/api/v1/inventory-sync"


def _defaults(pr: dict) -> dict:
    sample = {"sap_client": "100", "code": "USMF", "code_list": "1000,1100", "int_list": "204,207",
              "int": "1", "decimals": "2", "schema": "SAPSR3", "bc_company": "CRONUS USA, Inc.",
              "guid": "437dbf0e-84ff-417a-965d-ed2bb9650972", "prefix": "wp_"}
    out = {}
    for p in pr["params"]:
        v = sample[p["kind"]]
        if p["kind"] == "bc_company":
            v = "CRONUS USA Inc"
        out[p["name"]] = v
    return out


class TestRender:

    @pytest.mark.parametrize("pid", sorted(presets.PRESETS))
    def test_every_preset_renders_a_guarded_read_on_each_engine(self, pid):
        pr = presets.PRESETS[pid]
        for eng in pr["engines"]:
            out = presets.render(pid, eng, _defaults(pr))
            body = out["profile"]
            assert body["source_kind"] == "query" and body["stock_model"] == pr["stock_model"]
            validate_read_only_sql(body["source_query"], eng)     # raises on anything but a plain read
            assert out["preset"]["verified"] is bool(pr.get("verified", False))

    def test_erp_presets_say_they_are_unverified(self):
        unverified = [p for p in presets.catalogue()["presets"] if not p["verified"]]
        assert {p["vendor"] for p in unverified} >= {"SAP", "Oracle", "Microsoft", "Infor", "Odoo"}

    def test_required_client_is_in_the_sql(self):
        sql = presets.render("sap_ecc_mard", "oracle", {"client": "300"})["profile"]["source_query"]
        assert "MANDT = '300'" in sql
        sql = presets.render("dynamics_inventsum", "mssql", {"company": "USMF"})["profile"]["source_query"]
        assert "DATAAREAID = 'usmf'" in sql and "PARTITION" in sql

    @pytest.mark.parametrize("pid,params,reason", [
        ("sap_ecc_mard", {}, "param_required"),
        ("sap_ecc_mard", {"client": "100' OR '1'='1"}, "param_format"),
        ("sap_ecc_mard", {"client": "100", "plants": "1000;DROP TABLE x"}, "param_format"),
        ("sap_ecc_mard", {"client": "100", "schema": "SAPSR3--"}, "param_format"),
        ("dynamics_inventsum", {"company": "us mf"}, "param_format"),
        ("business_central_item_ledger", {"company": "A]; DROP"}, "param_format"),
        ("oracle_ebs_onhand", {"organizations": "1 OR 1=1"}, "param_format"),
        ("sap_ecc_mard", {"client": "100", "evil": "x"}, "param_unknown"),
    ])
    def test_parameters_outside_their_pattern_are_refused(self, pid, params, reason):
        eng = presets.PRESETS[pid]["engines"][0]
        with pytest.raises(AppError) as e:
            presets.render(pid, eng, params)
        assert e.value.code == "inventory_sync_preset_invalid" and e.value.params["reason"] == reason

    @pytest.mark.parametrize("pid", ["sap_ecc_mard", "sap_ecc_mchb", "dynamics_inventsum", "odoo_stock_quant"])
    def test_erp_presets_bring_their_warehouse_master(self, pid):
        from backend.inventory_sync import masters
        pr = presets.PRESETS[pid]
        for eng in pr["engines"]:
            body = presets.render(pid, eng, _defaults(pr))["profile"]
            wh = body["masters"]["warehouses"]
            validate_read_only_sql(wh["source_query"], eng)
            assert masters.normalize(body["masters"], has_source=True)["warehouses"]["code_column"] == "code"
        sap = presets.render("sap_ecc_mard", "oracle", {"client": "200"})["profile"]["masters"]["warehouses"]
        assert "T001L" in sap["source_query"] and "MANDT = '200'" in sap["source_query"]

    def test_wrong_engine_is_refused(self):
        with pytest.raises(AppError) as e:
            presets.render("odoo_stock_quant", "oracle", {})
        assert e.value.params["reason"] == "engine"

    def test_jde_decimals_become_a_multiplier(self):
        body = presets.render("jde_item_location", "oracle", {"decimals": "2"})["profile"]
        assert body["mapping"]["current_stock"]["multiplier"] == pytest.approx(0.01)


class TestApi:

    def test_catalogue_and_render_need_an_analyst(self, client, analyst_headers, viewer_headers):
        assert client.get(f"{URL}/presets", headers=viewer_headers).status_code == 403
        r = client.get(f"{URL}/presets", headers=analyst_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert any(p["id"] == "sap_ecc_mard" for p in data["presets"])
        assert data["not_direct"]["netsuite"]["path"] == "push"
        assert client.post(f"{URL}/presets/sap_ecc_mard/render", headers=viewer_headers,
                           json={"engine": "oracle", "params": {"client": "100"}}).status_code == 403
        r = client.post(f"{URL}/presets/sap_ecc_mard/render", headers=analyst_headers,
                        json={"engine": "oracle", "params": {"client": "100"}})
        assert r.status_code == 200 and "MANDT = '100'" in r.json()["data"]["profile"]["source_query"]
        r = client.post(f"{URL}/presets/sap_ecc_mard/render", headers=analyst_headers,
                        json={"engine": "oracle", "params": {"client": "1'0"}})
        assert r.status_code == 422 and r.json()["error_code"] == "inventory_sync_preset_invalid"


# ── End to end on PostgreSQL ─────────────────────────────────────────────────

@pytest.fixture
def scd2_tables(src_db):
    with src_db.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS stock_history")
        cur.execute("CREATE TABLE stock_history (item_code TEXT, warehouse_code TEXT, on_hand NUMERIC, "
                    "valid_from TIMESTAMP, valid_to TIMESTAMP, deleted_at TIMESTAMP, notes TEXT)")
        big = "x" * 200_000
        rows = [
            ("A", "N", 5, "2026-01-01", "2026-02-01", None, big),      # old version
            ("A", "N", 7, "2026-02-01", None, None, big),              # current
            ("B", "N", 3, "2026-01-01", None, "2026-03-01", None),     # soft-deleted
            ("C", "S", 11, "2026-01-01", None, None, big),
        ]
        cur.executemany("INSERT INTO stock_history VALUES (%s,%s,%s,%s,%s,%s,%s)", rows)
    yield
    with src_db.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS stock_history")


def _from_preset(tid, sid, pid, **extra):
    body = presets.render(pid, "postgresql", {})["profile"]
    body.update({"data_source_id": sid, "allow_new_warehouses": True, "interval_minutes": 60,
                 "name": f"{pid}-{uuid4().hex[:4]}"}, **extra)
    p = profiles.create(tid, "usr_test", body)
    return profiles.get(tid, p["id"])


class TestEndToEnd:

    def test_scd2_preset_reads_only_current_live_rows_and_never_pulls_notes(self, test_tenant, src_db,
                                                                             scd2_tables):
        tid = test_tenant["id"]
        p = _from_preset(tid, _source(tid), "generic_scd2")
        dry = engine.dry_run(tid, dict(p))
        assert dry["rejected"] == 0 and dry["valid"] == 2
        assert not [a for a in dry["advice"] if a["code"] in ("history_rows_unfiltered",
                                                              "soft_deleted_rows_unfiltered")]
        run = _run(tid, p["id"])
        assert run["status"] == "success", run
        got = {k: float(v["current_stock"]) for k, v in _stock(tid).items()}
        # Independent oracle: the current, not-deleted version of each key.
        with src_db.cursor() as cur:
            cur.execute("SELECT item_code, warehouse_code, on_hand FROM stock_history "
                        "WHERE valid_to IS NULL AND deleted_at IS NULL")
            want = {(r[0], r[1]): float(r[2]) for r in cur.fetchall()}
        assert {(s, w.upper()): q for (s, w), q in got.items()} == {(s, w.upper()): q for (s, w), q in want.items()}

    def test_a_table_profile_over_history_is_warned_and_projected(self, test_tenant, src_db, scd2_tables):
        tid = test_tenant["id"]
        body = {"name": "hist", "data_source_id": _source(tid), "source_kind": "table",
                "source_schema": "public", "source_object": "stock_history",
                "mapping": {"sku": "item_code", "warehouse": "warehouse_code", "current_stock": "on_hand"},
                "allow_new_warehouses": True, "interval_minutes": 60, "duplicate_policy": "sum"}
        p = profiles.get(tid, profiles.create(tid, "usr_test", body)["id"])
        prof = dict(p)
        dry = engine.dry_run(tid, prof)
        codes = {a["code"] for a in dry["advice"]}
        assert {"history_rows_unfiltered", "soft_deleted_rows_unfiltered"} <= codes
        # Only the three mapped columns were selected: the 200 KB notes never crossed the wire.
        assert prof["_projection"] == ["item_code", "warehouse_code", "on_hand"]
        assert "notes" not in reader.raw_select(prof, "postgresql")
        assert dry["rejected_by_code"].get("cell_too_large") is None

    def test_union_ledger_matches_the_documents(self, test_tenant, src_db):
        tid = test_tenant["id"]
        with src_db.cursor() as cur:
            for t in ("purchase_receipts", "sales_lines", "stock_adjustments", "transfers"):
                cur.execute(f"DROP TABLE IF EXISTS {t}")
            cur.execute("CREATE TABLE purchase_receipts (receipt_id INT, item_code TEXT, warehouse_code TEXT, "
                        "quantity NUMERIC, posted_at TIMESTAMP, status TEXT)")
            cur.execute("CREATE TABLE sales_lines (line_id INT, item_code TEXT, warehouse_code TEXT, "
                        "quantity NUMERIC, posted_at TIMESTAMP, status TEXT)")
            cur.execute("CREATE TABLE stock_adjustments (adjustment_id INT, item_code TEXT, warehouse_code TEXT, "
                        "quantity_delta NUMERIC, posted_at TIMESTAMP)")
            cur.execute("CREATE TABLE transfers (transfer_id INT, item_code TEXT, from_warehouse TEXT, "
                        "to_warehouse TEXT, quantity NUMERIC, shipped_at TIMESTAMP, received_at TIMESTAMP)")
            cur.execute("INSERT INTO purchase_receipts VALUES (1,'A','N',100,'2026-01-01','posted'),"
                        "(2,'A','N',50,'2026-01-02','draft'),(3,'B','S',20,'2026-01-03','posted')")
            cur.execute("INSERT INTO sales_lines VALUES (1,'A','N',30,'2026-01-05','posted'),"
                        "(2,'B','S',5,'2026-01-06','cancelled')")
            cur.execute("INSERT INTO stock_adjustments VALUES (1,'A','N',-2,'2026-01-07')")
            cur.execute("INSERT INTO transfers VALUES (1,'A','N','S',10,'2026-01-08','2026-01-09'),"
                        "(2,'A','N','S',5,'2026-01-10',NULL)")
        try:
            p = _from_preset(tid, _source(tid), "generic_union_ledger", negative_policy="zero")
            assert _run(tid, p["id"])["status"] == "success"
            got = {(s, w.upper()): float(v["current_stock"]) for (s, w), v in _stock(tid).items()}
            # A/N: +100 -30 -2 -10 -5 = 53 ; A/S: +10 (the second transfer is in transit) ; B/S: +20
            assert got == {("A", "N"): 53.0, ("A", "S"): 10.0, ("B", "S"): 20.0}
        finally:
            with src_db.cursor() as cur:
                for t in ("purchase_receipts", "sales_lines", "stock_adjustments", "transfers"):
                    cur.execute(f"DROP TABLE IF EXISTS {t}")


class TestAdvice:

    def test_missing_client_filter_is_named(self):
        hints = advice.lint({"source_query": "SELECT MATNR, LABST FROM MARD"}, [])
        assert {"code": "erp_client_filter_missing",
                "params": {"vendor": "SAP", "table": "MARD", "column": "MANDT"}} in hints
        assert any(h["code"] == "sap_s4_calculated_stock" for h in hints)

    def test_present_client_filter_is_quiet(self):
        hints = advice.lint({"source_query": "SELECT MATNR FROM MARD WHERE MANDT = '100'"}, [])
        assert not [h for h in hints if h["code"] == "erp_client_filter_missing"]
        hints = advice.lint({"source_kind": "table", "source_object": "INVENTSUM",
                             "filters": [{"column": "dataareaid", "op": "eq", "value": "usmf"}]}, [])
        assert not [h for h in hints if h["code"] == "erp_client_filter_missing"]

    def test_history_marker_referenced_in_the_query_is_quiet(self):
        q = {"source_query": "SELECT * FROM t WHERE dbt_valid_to IS NULL"}
        assert not advice.lint(q, ["sku", "dbt_valid_to"])
        assert advice.lint({"source_query": "SELECT * FROM t"}, ["sku", "dbt_valid_to"])[0]["code"] == \
            "history_rows_unfiltered"

    def test_jde_without_decimals_is_warned(self):
        assert any(h["code"] == "jde_implied_decimals" for h in advice.lint(
            {"source_query": "SELECT LIPQOH FROM F41021", "mapping": {"current_stock": {"column": "LIPQOH"}}}, []))


def test_every_advice_code_has_copy_in_both_languages():
    from pathlib import Path
    ts = (Path(__file__).resolve().parents[2] / "Frontend" / "src" / "i18n" / "translations.ts").read_text(
        encoding="utf-8")
    codes = ["erp_client_filter_missing", "history_rows_unfiltered", "soft_deleted_rows_unfiltered",
             "sap_s4_calculated_stock", "jde_implied_decimals", "bc_remaining_quantity"]
    for c in codes:
        assert ts.count(f"'isync.advice.{c}'") == 2, c
    gotchas = {g for p in presets.PRESETS.values() for g in p["gotchas"]}
    for g in gotchas:
        assert ts.count(f"'isync.gotcha.{g}'") == 2, g
    for k in presets.NOT_DIRECT:
        assert ts.count(f"'isync.not_direct.{k}'") == 2, k
