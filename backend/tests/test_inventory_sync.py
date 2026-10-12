"""Inventory sync: the run writes what the source says and nothing else.

The "customer database" is a separate database (`stockai_sync_src`) on the test
Postgres server, reached through the real read-only data-source path — the
same SSRF check, sql_guard and session the product uses. Every assertion about
stock is a direct query on `inventory_stock` / `inventory_snapshots`.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg2
import pytest

from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.inventory_sync import engine, profiles, scheduler

SRC_DB = "stockai_sync_src"
URL = "/api/v1/inventory-sync"


def _admin_params(dbname: str) -> dict:
    from urllib.parse import urlsplit
    u = urlsplit(settings.database_url)
    return dict(host=u.hostname or "127.0.0.1", port=u.port or 5432, user=u.username or "postgres",
                password=u.password or "postgres", dbname=dbname)


@pytest.fixture(autouse=True)
def _booted(client):
    """App startup runs the migrations and opens the pool."""
    return client


@pytest.fixture(scope="module")
def src_db():
    conn = psycopg2.connect(**_admin_params("postgres"))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (SRC_DB,))
        if not cur.fetchone():
            cur.execute(f"CREATE DATABASE {SRC_DB}")
    conn.close()
    c = psycopg2.connect(**_admin_params(SRC_DB))
    c.autocommit = True
    yield c
    c.close()


@pytest.fixture
def src_table(src_db):
    name = f"stock_{uuid4().hex[:8]}"
    with src_db.cursor() as cur:
        cur.execute(f"CREATE TABLE {name} (codigo TEXT, almacen TEXT, existencia NUMERIC, "
                    "costo NUMERIC, activo INT DEFAULT 1, updated_at TIMESTAMP DEFAULT NOW())")
    yield name
    with src_db.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {name}")


def _put(src_db, table, rows):
    with src_db.cursor() as cur:
        cur.execute(f"DELETE FROM {table}")
        for r in rows:
            cur.execute(f"INSERT INTO {table} (codigo, almacen, existencia, costo, activo, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (r[0], r[1], r[2], r[3] if len(r) > 3 else None, r[4] if len(r) > 4 else 1,
                         r[5] if len(r) > 5 else datetime(2026, 1, 1)))


def _source(tenant_id: str, user_id: str = "usr_test") -> str:
    from backend.datasources.service import create_sql_source
    p = _admin_params(SRC_DB)
    src = create_sql_source(tenant_id, user_id, "ERP", p["host"], p["port"], SRC_DB, p["user"],
                            p["password"], "postgresql", ssl_mode="disable")
    execute("UPDATE datasets SET connection_status = 'connected' WHERE id = %s", (src["id"],))
    return src["id"]


MAPPING = {"sku": "codigo", "warehouse": "almacen", "current_stock": "existencia",
           "unit_cost": "costo"}


def _profile(tenant_id, source_id, table, **kw):
    body = {"name": "ERP stock", "data_source_id": source_id, "source_kind": "table",
            "source_schema": "public", "source_object": table, "mapping": MAPPING,
            "allow_new_warehouses": True, "interval_minutes": 60}
    body.update(kw)
    return profiles.create(tenant_id, "usr_test", body)


def _run(tenant_id, profile_id, **kw):
    run_id = engine.start_run(tenant_id, profile_id, "manual")
    return engine.execute_run(tenant_id, profile_id, run_id, **kw)


def _stock(tenant_id):
    return {(r["sku"], r["warehouse"]): r for r in query(
        "SELECT * FROM inventory_stock WHERE tenant_id = %s", (tenant_id,))}


def _snapshots(tenant_id):
    return query_one("SELECT COUNT(*) AS n FROM inventory_snapshots WHERE tenant_id = %s",
                     (tenant_id,))["n"]


# ── Pure units ────────────────────────────────────────────────────────────────

class TestMapper:

    def test_transforms_multiplier_value_map_case_and_default(self):
        from backend.inventory_sync import fields as F, mapper
        m = F.normalize_mapping({
            "sku": {"column": "c", "case": "upper"},
            "current_stock": {"column": "q", "multiplier": 12},
            "warehouse": {"column": "w", "value_map": {"01": "Norte"}, "default": "principal"},
        })
        assert mapper.map_row({"c": " ab-1 ", "q": 2, "w": "01"}, m) == {
            "sku": "AB-1", "warehouse": "Norte", "current_stock": 24.0}
        assert mapper.map_row({"c": "x", "q": "3", "w": None}, m)["warehouse"] == "principal"

    @pytest.mark.parametrize("row,code", [
        ({"c": "", "q": 1}, "sku_blank"),
        ({"c": "A\x00", "q": 1}, "row_has_nul"),
        ({"c": "A", "q": "N/D"}, "not_a_number"),
        ({"c": "A", "q": "1.250"}, "number_format_unclear"),
        ({"c": "A", "q": -3}, "negative_quantity"),
        ({"c": "A", "q": 2e9}, "out_of_range"),
        ({"c": "A", "q": None}, "quantity_missing"),
    ])
    def test_rejections_carry_a_code(self, row, code):
        from backend.inventory_sync import fields as F, mapper
        m = F.normalize_mapping({"sku": "c", "current_stock": "q"})
        with pytest.raises(mapper.Rejected) as exc:
            mapper.map_row(row, m)
        assert exc.value.code == code

    def test_negative_policy_zero_clamps(self):
        from backend.inventory_sync import fields as F, mapper
        m = F.normalize_mapping({"sku": "c", "current_stock": "q"})
        assert mapper.map_row({"c": "A", "q": -3}, m, negative_policy="zero")["current_stock"] == 0.0

    def test_decimal_option_settles_the_ambiguous_dot(self):
        from backend.inventory_sync import fields as F, mapper
        comma = F.normalize_mapping({"sku": "c", "current_stock": {"column": "q", "decimal": "comma"}})
        dot = F.normalize_mapping({"sku": "c", "current_stock": {"column": "q", "decimal": "dot"}})
        assert mapper.map_row({"c": "A", "q": "1.250"}, comma)["current_stock"] == 1250.0
        assert mapper.map_row({"c": "A", "q": "1.250"}, dot)["current_stock"] == 1.25

    def test_filters(self):
        from backend.inventory_sync import mapper
        assert mapper.passes_filters({"a": 1}, [{"column": "a", "op": "eq", "value": "1"}])
        assert not mapper.passes_filters({"a": 0}, [{"column": "a", "op": "eq", "value": "1"}])
        assert mapper.passes_filters({"w": "01"}, [{"column": "w", "op": "in", "value": ["01", "02"]}])
        assert not mapper.passes_filters({"q": 5}, [{"column": "q", "op": "gt", "value": "5"}])

    def test_a_mapping_without_sku_or_quantity_is_refused(self):
        from backend.errors import AppError
        from backend.inventory_sync import fields as F
        with pytest.raises(AppError) as exc:
            F.normalize_mapping({"sku": "c"})
        assert exc.value.params["reason"] == "required_missing"


class TestSuggest:

    def test_sap_mard(self):
        from backend.inventory_sync.suggest import suggest_mapping
        cols = [{"name": n, "type": t} for n, t in
                [("MATNR", "varchar"), ("WERKS", "varchar"), ("LABST", "numeric"), ("EISBE", "numeric")]]
        m = suggest_mapping(cols)["mapping"]
        assert m["sku"]["column"] == "MATNR"
        assert m["current_stock"]["column"] == "LABST" and m["current_stock"]["label"] == "high"
        assert m["warehouse"]["column"] == "WERKS"
        assert m["min_stock"]["column"] == "EISBE"

    def test_spanish_and_odoo_and_text_quantity_penalised(self):
        from backend.inventory_sync.suggest import suggest_mapping, rank_tables
        m = suggest_mapping([{"name": "Cod_Articulo"}, {"name": "Existencia"}, {"name": "Almacén"}])["mapping"]
        assert (m["sku"]["column"], m["current_stock"]["column"], m["warehouse"]["column"]) == (
            "Cod_Articulo", "Existencia", "Almacén")
        weak = suggest_mapping([{"name": "codigo"}, {"name": "stock", "type": "text",
                                                     "samples": ["mucho", "poco"]}])["mapping"]
        assert weak["current_stock"]["label"] == "low"
        ranked = rank_tables([{"name": "kardex_movimientos"}, {"name": "stock_quant"},
                              {"name": "clientes"}])
        assert ranked[0]["name"] == "stock_quant"
        assert "clientes" not in [r["name"] for r in ranked]


class TestMongoGuard:

    def test_write_stages_and_server_js_are_refused(self):
        from backend.datasources import mongo
        from backend.errors import AppError
        for bad in ([{"$match": {}}, {"$out": "x"}], [{"$merge": {"into": "x"}}],
                    [{"$facet": {"a": [{"$merge": "x"}]}}],
                    [{"$lookup": {"from": "x", "pipeline": [{"$out": "y"}], "as": "z"}}],
                    [{"$match": {"$where": "sleep(1)"}}]):
            with pytest.raises(AppError):
                mongo.validate_pipeline(bad)
        with pytest.raises(AppError):
            mongo.validate_filter({"$where": "1"})
        assert mongo.validate_pipeline([{"$match": {"a": 1}}, {"$group": {"_id": "$s"}}])

    def test_connection_string_and_tls_options(self):
        from backend.datasources import connection as C, mongo
        from backend.errors import AppError
        parsed = C.parse_connection_string("mongodb://u:p@db.example.com:27017/erp?authSource=admin&tls=true")
        assert parsed["engine"] == "mongodb" and parsed["auth_source"] == "admin"
        assert parsed["ssl_mode"] == "require"
        with pytest.raises(AppError):
            C.parse_connection_string("mongodb+srv://u:p@cluster.example.com/erp")
        spec = C.ConnectionSpec("mongodb", "db.example.com", 27017, "erp", "u", "p", ssl_mode="require")
        kw = mongo.client_kwargs(spec, "203.0.113.5", 30, None)
        assert kw["host"] == "203.0.113.5" and kw["directConnection"] and kw["tlsInsecure"]
        full = C.ConnectionSpec("mongodb", "db.example.com", 27017, "erp", "u", "p", ssl_mode="verify-full")
        assert mongo.client_kwargs(full, "203.0.113.5", 30, None)["host"] == "db.example.com"

    def test_a_mongo_source_is_refused_by_the_sql_paths(self):
        from backend.datasources import client
        from backend.datasources.connection import ConnectionSpec
        from backend.errors import AppError
        with pytest.raises(AppError) as exc:
            client.require_sql_engine(ConnectionSpec("mongodb", "h", 1, "d", "u"))
        assert exc.value.code == "data_source_engine_not_sql"


# ── The run, asserted on the database ─────────────────────────────────────────

class TestRun:

    def test_first_run_creates_and_counts_match_the_database(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10, 2.5), ("B2", "Norte", 0, 1), ("C3", "Sur", 7)])
        p = _profile(tid, _source(tid), src_table)
        run = _run(tid, p["id"])
        assert run["status"] == "success", run
        stock = _stock(tid)
        assert set(stock) == {("A1", "Norte"), ("B2", "Norte"), ("C3", "Sur")}
        assert float(stock[("A1", "Norte")]["current_stock"]) == 10
        assert float(stock[("A1", "Norte")]["unit_cost"]) == 2.5
        assert (run["created"], run["updated"], run["unchanged"], run["rejected"]) == (3, 0, 0, 0)
        assert _snapshots(tid) == 3
        keys = query("SELECT * FROM inventory_sync_keys WHERE profile_id = %s", (p["id"],))
        assert len(keys) == 3
        assert query("SELECT 1 FROM inventory_sync_staging WHERE run_id = %s", (run["id"],)) == []

    def test_rerun_is_idempotent_and_writes_nothing(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10, 2.5), ("B2", "Norte", 4, 1)])
        p = _profile(tid, _source(tid), src_table)
        _run(tid, p["id"])
        before = {k: r["updated_at"] for k, r in _stock(tid).items()}
        snaps = _snapshots(tid)
        run = _run(tid, p["id"])
        assert (run["status"], run["created"], run["updated"], run["unchanged"]) == ("success", 0, 0, 2)
        assert {k: r["updated_at"] for k, r in _stock(tid).items()} == before
        assert _snapshots(tid) == snaps

    def test_snapshot_only_when_the_quantity_changed(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10, 2.5), ("B2", "Norte", 4, 1)])
        p = _profile(tid, _source(tid), src_table)
        _run(tid, p["id"])
        snaps = _snapshots(tid)
        _put(src_db, src_table, [("A1", "Norte", 10, 3.0), ("B2", "Norte", 4, 1)])   # cost only
        run = _run(tid, p["id"])
        assert (run["updated"], run["unchanged"]) == (1, 1)
        assert float(_stock(tid)[("A1", "Norte")]["unit_cost"]) == 3.0
        assert _snapshots(tid) == snaps
        _put(src_db, src_table, [("A1", "Norte", 6, 3.0), ("B2", "Norte", 4, 1)])    # quantity
        run = _run(tid, p["id"])
        assert run["updated"] == 1
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 6
        assert _snapshots(tid) == snaps + 1

    def test_safety_brake_on_an_empty_source_changes_nothing(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10), ("B2", "Norte", 4)])
        p = _profile(tid, _source(tid), src_table, missing_policy="zero")
        _run(tid, p["id"])
        before = {k: (float(r["current_stock"]), r["updated_at"]) for k, r in _stock(tid).items()}
        _put(src_db, src_table, [])
        run = _run(tid, p["id"])
        assert run["status"] == "braked" and run["error_code"] == "brake_source_empty"
        assert {k: (float(r["current_stock"]), r["updated_at"]) for k, r in _stock(tid).items()} == before
        row = profiles.get(tid, p["id"])
        assert row["last_status"] == "braked"
        ev = query("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = 'inventory_sync.braked'",
                   (tid,))
        assert len(ev) == 1 and ev[0]["context"]["severity"] == "critical"
        # A second braked attempt is the same episode: no second alert.
        _run(tid, p["id"])
        assert len(query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                         "AND action = 'inventory_sync.braked'", (tid,))) == 1

    def test_safety_brake_when_many_skus_vanish(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        rows = [(f"S{i}", "Norte", 5) for i in range(10)]
        _put(src_db, src_table, rows)
        p = _profile(tid, _source(tid), src_table, missing_policy="zero", brake_max_vanished_pct=20,
                     brake_min_row_ratio=0)
        _run(tid, p["id"])
        _put(src_db, src_table, rows[:5])
        run = _run(tid, p["id"])
        assert run["status"] == "braked" and run["error_code"] == "brake_skus_vanished"
        assert all(float(r["current_stock"]) == 5 for r in _stock(tid).values())
        # The person looked and confirmed: override once, the policy applies.
        run = _run(tid, p["id"], override_brake=True)
        assert run["status"] == "success" and run["missing"] == 5 and run["missing_applied"] == 5
        stock = _stock(tid)
        assert sum(1 for r in stock.values() if float(r["current_stock"]) == 0) == 5
        assert len(stock) == 10   # never deleted

    def test_missing_policy_ignore_never_touches_vanished_rows(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        rows = [(f"S{i}", "Norte", 5) for i in range(10)]
        _put(src_db, src_table, rows)
        p = _profile(tid, _source(tid), src_table, brake_max_vanished_pct=50, brake_min_row_ratio=0)
        _run(tid, p["id"])
        _put(src_db, src_table, rows[:9])
        run = _run(tid, p["id"])
        assert run["status"] == "success" and run["missing"] == 1 and run["missing_applied"] == 0
        assert float(_stock(tid)[("S9", "Norte")]["current_stock"]) == 5

    def test_a_failing_source_leaves_stock_untouched(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table)
        _run(tid, p["id"])
        before = {k: (float(r["current_stock"]), r["updated_at"]) for k, r in _stock(tid).items()}
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} RENAME TO {src_table}_gone")
        try:
            run = _run(tid, p["id"])
        finally:
            with src_db.cursor() as cur:
                cur.execute(f"ALTER TABLE {src_table}_gone RENAME TO {src_table}")
        assert run["status"] == "failed" and run["error_code"]
        assert {k: (float(r["current_stock"]), r["updated_at"]) for k, r in _stock(tid).items()} == before
        row = profiles.get(tid, p["id"])
        assert row["consecutive_failures"] == 1 and row["last_status"] == "failed"

    def test_rejections_are_reported_and_duplicates_both_rejected(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10), ("A1", "Norte", 3), ("", "Norte", 1),
                                 ("B2", "Norte", -4), ("C3", "Norte", 2), ("D4", "Norte", 2),
                                 ("E5", "Norte", 2), ("F6", "Norte", 2)])
        p = _profile(tid, _source(tid), src_table)
        run = _run(tid, p["id"])
        assert run["status"] == "success"
        assert run["rejected"] == 4   # both A1 rows, the blank SKU, the negative
        assert set(_stock(tid)) == {("C3", "Norte"), ("D4", "Norte"), ("E5", "Norte"), ("F6", "Norte")}
        codes = {r["code"] for r in run["rejected_sample"]}
        assert {"duplicate_key", "sku_blank", "negative_quantity"} <= codes

    def test_duplicate_policy_sum_adds_lots(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10), ("A1", "Norte", 3)])
        p = _profile(tid, _source(tid), src_table, duplicate_policy="sum")
        run = _run(tid, p["id"])
        assert run["rejected"] == 0
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 13

    def test_unknown_warehouse_is_rejected_unless_allowed(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        from backend.inventory import warehouse_service as wh
        wh.create_warehouse(tid, "Norte")
        _put(src_db, src_table, [("A1", "Norte", 10), ("B2", "Marte", 3)])
        p = _profile(tid, _source(tid), src_table, allow_new_warehouses=False, brake_max_rejected_pct=60)
        result = engine.dry_run(tid, profiles.get(tid, p["id"]))
        assert result["rejected_by_code"].get("unknown_warehouse") == 1
        assert "Marte" in result["unknown_warehouses"]
        assert _stock(tid) == {}   # the dry run wrote nothing

    def test_manual_edit_is_overwritten_and_reported(self, test_tenant, src_db, src_table):
        from backend.inventory import service as svc
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table)
        _run(tid, p["id"])
        svc.upsert_stock(tid, "A1", {"current_stock": 99, "warehouse": "Norte"})
        run = _run(tid, p["id"])
        assert run["manual_overwritten"] == 1 and run["updated"] == 1
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 10
        assert run["overwritten_sample"][0]["manual_value"] == 99
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'inventory_sync.manual_edits_overwritten'", (tid,))

    def test_incremental_watermark_advances_only_on_success(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10, None, 1, datetime(2026, 3, 1, 10, 0)),
                                 ("B2", "Norte", 4, None, 1, datetime(2026, 3, 2, 10, 0))])
        p = _profile(tid, _source(tid), src_table, mode="incremental", watermark_column="updated_at",
                     overlap_minutes=0)
        run = _run(tid, p["id"])
        assert run["status"] == "success"
        wm = profiles.get(tid, p["id"])["watermark_value"]
        assert wm["value"].startswith("2026-03-02T10:00")
        # Only the newer row is read next time.
        with src_db.cursor() as cur:
            cur.execute(f"INSERT INTO {src_table} (codigo, almacen, existencia, updated_at) "
                        "VALUES ('C3', 'Norte', 1, '2026-03-05 09:00')")
        run = _run(tid, p["id"])
        assert run["rows_read"] == 2 and run["created"] == 1   # B2 (>= watermark) + C3
        wm2 = profiles.get(tid, p["id"])["watermark_value"]
        assert wm2["value"].startswith("2026-03-05T09:00")
        # A failing run leaves it where it was.
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} RENAME TO {src_table}_x")
        try:
            run = _run(tid, p["id"])
        finally:
            with src_db.cursor() as cur:
                cur.execute(f"ALTER TABLE {src_table}_x RENAME TO {src_table}")
        assert run["status"] == "failed"
        assert profiles.get(tid, p["id"])["watermark_value"] == wm2

    def test_row_cap_fails_before_writing(self, test_tenant, src_db, src_table, monkeypatch):
        tid = test_tenant["id"]
        _put(src_db, src_table, [(f"S{i}", "Norte", 1) for i in range(5)])
        p = _profile(tid, _source(tid), src_table)
        monkeypatch.setattr(settings, "inventory_sync_max_rows_per_run", 3)
        run = _run(tid, p["id"])
        assert run["status"] == "failed" and run["error_code"] == "inventory_sync_too_many_rows"
        assert _stock(tid) == {}


# ── Scheduler: lease, stale alert ─────────────────────────────────────────────

class TestScheduler:

    def test_two_workers_never_claim_the_same_profile(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _profile(tid, _source(tid), src_table)
        execute("UPDATE inventory_sync_profiles SET enabled = TRUE, next_run_at = NOW() - INTERVAL '1 minute' "
                "WHERE id = %s", (p["id"],))
        got: list = []
        barrier = threading.Barrier(4)

        def worker(n):
            barrier.wait()
            row = scheduler.claim_due(owner=f"w{n}")
            if row and row["id"] == p["id"]:
                got.append(n)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(got) == 1
        from backend.errors import AppError
        with pytest.raises(AppError) as exc:
            scheduler.claim_one(tid, p["id"], owner="manual")
        assert exc.value.code == "inventory_sync_running"

    def test_release_backs_off_after_failures(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _profile(tid, _source(tid), src_table, interval_minutes=10)
        execute("UPDATE inventory_sync_profiles SET consecutive_failures = 3, lease_owner = 'w', "
                "lease_until = NOW() + INTERVAL '5 minutes' WHERE id = %s", (p["id"],))
        scheduler.release(p["id"], "w")
        row = profiles.get(tid, p["id"])
        assert row["lease_owner"] is None
        wait = (row["next_run_at"] - datetime.now(timezone.utc)).total_seconds() / 60
        assert 38 <= wait <= 41   # 10 * 2^(3-1)

    def test_stale_alert_once_per_episode(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _profile(tid, _source(tid), src_table, interval_minutes=10)
        execute("UPDATE inventory_sync_profiles SET enabled = TRUE, "
                "last_success_at = NOW() - INTERVAL '1 hour' WHERE id = %s", (p["id"],))
        scheduler.alert_stale()
        scheduler.alert_stale()
        ev = query("SELECT * FROM activity_logs WHERE tenant_id = %s AND action = 'inventory_sync.stale'",
                   (tid,))
        assert len(ev) == 1 and ev[0]["context"]["severity"] == "critical"
        assert profiles.public(profiles.get(tid, p["id"]))["stale"] is True


# ── API: permission pairs and the dry-run gate ────────────────────────────────

class TestApi:

    def _body(self, source_id, table):
        return {"name": "ERP", "data_source_id": source_id, "source_kind": "table",
                "source_schema": "public", "source_object": table, "mapping": MAPPING,
                "allow_new_warehouses": True}

    def test_create_permission_pair(self, client, viewer_headers, analyst_headers, test_tenant,
                                    src_db, src_table):
        tid = test_tenant["id"]
        sid = _source(tid)
        r = client.post(f"{URL}/profiles", json=self._body(sid, src_table), headers=viewer_headers)
        assert r.status_code == 403
        assert query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,)) == []
        r = client.post(f"{URL}/profiles", json=self._body(sid, src_table), headers=analyst_headers)
        assert r.status_code == 201, r.text
        row = query_one("SELECT * FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,))
        assert row["mapping"]["sku"]["column"] == "codigo" and row["enabled"] is False
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'audit.inventory_sync.profile_created'", (tid,))

    def test_enable_requires_a_dry_run_and_viewer_cannot_run(self, client, viewer_headers,
                                                             analyst_headers, test_tenant,
                                                             src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        sid = _source(tid)
        pid = client.post(f"{URL}/profiles", json=self._body(sid, src_table),
                          headers=analyst_headers).json()["data"]["id"]
        r = client.post(f"{URL}/profiles/{pid}/enable", headers=analyst_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "inventory_sync_dry_run_required"
        assert profiles.get(tid, pid)["enabled"] is False
        r = client.post(f"{URL}/profiles/{pid}/run", headers=analyst_headers)
        assert r.status_code == 409
        dr = client.post(f"{URL}/profiles/{pid}/dry-run", headers=analyst_headers)
        assert dr.status_code == 200, dr.text
        assert dr.json()["data"]["would_create"] == 1
        assert _stock(tid) == {}
        r = client.post(f"{URL}/profiles/{pid}/enable", headers=viewer_headers)
        assert r.status_code == 403 and profiles.get(tid, pid)["enabled"] is False
        r = client.post(f"{URL}/profiles/{pid}/enable", headers=analyst_headers)
        assert r.status_code == 200 and profiles.get(tid, pid)["enabled"] is True
        r = client.post(f"{URL}/profiles/{pid}/run", headers=viewer_headers)
        assert r.status_code == 403
        assert query("SELECT 1 FROM inventory_sync_runs WHERE profile_id = %s", (pid,)) == []
        r = client.post(f"{URL}/profiles/{pid}/run", headers=analyst_headers)
        assert r.status_code == 202, r.text
        for _ in range(100):
            run = query_one("SELECT status FROM inventory_sync_runs WHERE profile_id = %s", (pid,))
            if run["status"] != "running":
                break
            time.sleep(0.1)
        assert run["status"] == "success"
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 10
        # Editing the mapping turns it off until previewed again.
        r = client.patch(f"{URL}/profiles/{pid}", json={"mapping": {**MAPPING, "unit_cost": None}},
                         headers=analyst_headers)
        assert r.status_code == 200 and r.json()["data"]["disabled_by_edit"] is True
        assert profiles.get(tid, pid)["enabled"] is False

    def test_delete_permission_pair_keeps_stock(self, client, viewer_headers, analyst_headers,
                                                test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table)
        _run(tid, p["id"])
        assert client.delete(f"{URL}/profiles/{p['id']}", headers=viewer_headers).status_code == 403
        assert profiles.get(tid, p["id"]) is not None
        assert client.delete(f"{URL}/profiles/{p['id']}", headers=analyst_headers).status_code == 200
        assert profiles.get(tid, p["id"]) is None
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 10

    def test_status_reads_are_open_to_viewers_and_other_tenants_see_nothing(
            self, client, viewer_headers, make_tenant_user_headers, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _profile(tid, _source(tid), src_table)
        r = client.get(f"{URL}/profiles", headers=viewer_headers)
        assert r.status_code == 200 and [x["id"] for x in r.json()["data"]] == [p["id"]]
        other = make_tenant_user_headers("analyst")
        assert client.get(f"{URL}/profiles/{p['id']}", headers=other).status_code == 404
        assert client.get(f"{URL}/profiles", headers=other).json()["data"] == []

    def test_profile_ceiling(self, test_tenant, src_db, src_table, monkeypatch):
        from backend.errors import AppError
        tid = test_tenant["id"]
        sid = _source(tid)
        monkeypatch.setattr(settings, "inventory_sync_max_profiles_per_tenant", 1)
        _profile(tid, sid, src_table)
        with pytest.raises(AppError) as exc:
            _profile(tid, sid, src_table)
        assert exc.value.code == "inventory_sync_profile_limit"
        assert query_one("SELECT COUNT(*) AS n FROM inventory_sync_profiles WHERE tenant_id = %s",
                         (tid,))["n"] == 1

    def test_interval_below_minimum_is_refused(self, test_tenant, src_db, src_table):
        from backend.errors import AppError
        tid = test_tenant["id"]
        with pytest.raises(AppError) as exc:
            _profile(tid, _source(tid), src_table, interval_minutes=1)
        assert exc.value.code == "inventory_sync_value_out_of_range"
