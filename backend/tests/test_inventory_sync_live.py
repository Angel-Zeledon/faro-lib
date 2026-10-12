"""Inventory sync end to end against real engines: MySQL, MongoDB, SQL Server,
Oracle. Each target is skipped — not failed — when its port does not answer,
so the suite runs on a machine without them (same rule as
`live_customer_db.py`).

Override the targets with environment variables
``host:port:admin_user:admin_password``:

    STOCKAI_SYNC_MYSQL   default 127.0.0.1:3360:root:rootpass
    STOCKAI_SYNC_MONGO   default 127.0.0.1:27018:root:rootpass
    STOCKAI_SYNC_MSSQL   default 127.0.0.1:14330:sa:StockAi_Pass123
    STOCKAI_SYNC_ORACLE  default 127.0.0.1:15210:stockai:OraPass123   (service FREEPDB1)

Each test builds a small stock table (or collection) with an ERP-shaped
schema, connects through the product's own create + staged connection test,
lets the mapping assistant propose the mapping, runs a sync and asserts the
result with direct queries on `inventory_stock`.
"""

from __future__ import annotations

import os
import socket
from uuid import uuid4

import pytest

from backend.db.connection import execute, query
from backend.inventory_sync import engine, profiles, suggest


def _target(env: str, default: str):
    host, port, user, password = os.environ.get(env, default).split(":", 3)
    return host, int(port), user, password


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _booted(client):
    return client


def _connect(tenant_id, engine_name, host, port, database, user, password, **kw):
    from backend.datasources.service import create_sql_source, test_sql_connection
    src = create_sql_source(tenant_id, "usr_test", f"live-{engine_name}", host, port, database, user,
                            password, engine_name, ssl_mode="disable", **kw)
    probe = test_sql_connection(tenant_id, src["id"])
    assert probe["ok"], probe
    return src["id"]


def _sync_and_assert(tenant_id, source_id, columns, *, kind, obj, schema=None, extra=None):
    proposal = suggest.suggest_mapping(columns)["mapping"]
    mapping = {f: proposal[f]["column"] for f in ("sku", "warehouse", "current_stock") if f in proposal}
    assert set(mapping) == {"sku", "warehouse", "current_stock"}, proposal
    body = {"name": "live", "data_source_id": source_id, "source_kind": kind, "source_schema": schema,
            "source_object": obj, "mapping": mapping, "allow_new_warehouses": True}
    body.update(extra or {})
    p = profiles.create(tenant_id, "usr_test", body)
    dry = engine.dry_run(tenant_id, profiles.get(tenant_id, p["id"]))
    assert dry["would_create"] == 3 and dry["rejected"] == 1, dry
    run_id = engine.start_run(tenant_id, p["id"], "manual")
    run = engine.execute_run(tenant_id, p["id"], run_id)
    assert run["status"] == "success", run
    stock = {(r["sku"], r["warehouse"]): float(r["current_stock"]) for r in query(
        "SELECT sku, warehouse, current_stock FROM inventory_stock WHERE tenant_id = %s", (tenant_id,))}
    assert stock == {("A-1", "01"): 10.0, ("B-2", "01"): 2.5, ("C-3", "02"): 0.0}
    again = engine.execute_run(tenant_id, p["id"], engine.start_run(tenant_id, p["id"], "manual"))
    assert (again["created"], again["updated"], again["unchanged"]) == (0, 0, 3)


ROWS = [("A-1", "01", 10), ("B-2", "01", 2.5), ("C-3", "02", 0), ("", "02", 4)]


class TestMySQL:

    def test_sync_from_mysql(self, test_tenant):
        host, port, user, password = _target("STOCKAI_SYNC_MYSQL", "127.0.0.1:3360:root:rootpass")
        if not _reachable(host, port):
            pytest.skip("MySQL target not reachable")
        import pymysql
        table = f"existencias_{uuid4().hex[:6]}"
        conn = pymysql.connect(host=host, port=port, user=user, password=password, autocommit=True)
        with conn.cursor() as cur:
            cur.execute("CREATE DATABASE IF NOT EXISTS stockai_sync_live")
            cur.execute(f"CREATE TABLE stockai_sync_live.{table} (cod_articulo VARCHAR(40), "
                        "almacen VARCHAR(10), existencia DECIMAL(12,3))")
            for r in ROWS:
                cur.execute(f"INSERT INTO stockai_sync_live.{table} VALUES (%s,%s,%s)", r)
        try:
            sid = _connect(test_tenant["id"], "mysql", host, port, "stockai_sync_live", user, password)
            cols = [{"name": "cod_articulo", "type": "varchar"}, {"name": "almacen", "type": "varchar"},
                    {"name": "existencia", "type": "decimal"}]
            _sync_and_assert(test_tenant["id"], sid, cols, kind="table", obj=table, schema="stockai_sync_live")
        finally:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS stockai_sync_live.{table}")
            conn.close()


class TestMongo:

    def test_sync_from_mongodb_collection_with_nested_fields(self, test_tenant):
        host, port, user, password = _target("STOCKAI_SYNC_MONGO", "127.0.0.1:27018:root:rootpass")
        if not _reachable(host, port):
            pytest.skip("MongoDB target not reachable")
        from pymongo import MongoClient
        coll = f"stock_{uuid4().hex[:6]}"
        client = MongoClient(host=host, port=port, username=user, password=password, authSource="admin")
        db = client["stockai_sync_live"]
        db[coll].insert_many([{"codigo": s, "almacen": {"code": w}, "existencia": q} for s, w, q in ROWS])
        try:
            sid = _connect(test_tenant["id"], "mongodb", host, port, "stockai_sync_live", user, password)
            # The staged test speaks MongoDB's truth, not SQL Server's copy.
            from backend.datasources.service import test_sql_connection
            stages = {s["stage"]: s for s in test_sql_connection(test_tenant["id"], sid)["stages"]}
            assert stages["select"].get("code") == "data_source_read_only_by_client"
            assert stages["tls"]["status"] == "warning" and stages["tls"]["code"] == "data_source_tls_disabled"
            from backend.datasources import mongo
            from backend.datasources.service import get_source
            cfg = get_source(test_tenant["id"], sid)["sql_config"]
            with mongo.open_mongo(cfg, tenant_id=test_tenant["id"]) as session:
                cols = mongo.sample_fields(session, coll)["columns"]
            assert "almacen.code" in {c["name"] for c in cols}
            _sync_and_assert(test_tenant["id"], sid, cols, kind="collection", obj=coll)
        finally:
            db[coll].drop()
            client.close()

    def test_a_write_stage_never_reaches_the_server(self, test_tenant):
        host, port, user, password = _target("STOCKAI_SYNC_MONGO", "127.0.0.1:27018:root:rootpass")
        if not _reachable(host, port):
            pytest.skip("MongoDB target not reachable")
        from backend.errors import AppError
        sid = _connect(test_tenant["id"], "mongodb", host, port, "stockai_sync_live", user, password)
        with pytest.raises(AppError) as exc:
            profiles.create(test_tenant["id"], "usr_test", {
                "name": "evil", "data_source_id": sid, "source_kind": "pipeline", "source_object": "x",
                "source_query": '[{"$match": {}}, {"$out": "pwned"}]',
                "mapping": {"sku": "a", "current_stock": "b"}})
        assert exc.value.code == "mongo_stage_not_allowed"


class TestSqlServer:

    def test_sync_from_sql_server(self, test_tenant):
        host, port, user, password = _target("STOCKAI_SYNC_MSSQL", "127.0.0.1:14330:sa:StockAi_Pass123")
        if not _reachable(host, port):
            pytest.skip("SQL Server target not reachable")
        import pyodbc
        from backend.datasources.client import best_mssql_odbc_driver
        drv = best_mssql_odbc_driver()
        try:
            conn = pyodbc.connect(f"DRIVER={{{drv}}};SERVER=tcp:{host},{port};UID={user};PWD={password};"
                                  "Encrypt=no", autocommit=True, timeout=5)
        except pyodbc.Error as exc:
            pytest.skip(f"SQL Server not usable from this machine's ODBC driver: {type(exc).__name__}")
        table = f"InventSum_{uuid4().hex[:6]}"
        cur = conn.cursor()
        cur.execute("IF DB_ID('stockai_sync_live') IS NULL CREATE DATABASE stockai_sync_live")
        cur.execute(f"CREATE TABLE stockai_sync_live.dbo.{table} (ItemId NVARCHAR(40), "
                    "InventLocationId NVARCHAR(10), PhysicalInvent DECIMAL(12,3))")
        for r in ROWS:
            cur.execute(f"INSERT INTO stockai_sync_live.dbo.{table} VALUES (?,?,?)", r)
        try:
            sid = _connect(test_tenant["id"], "mssql", host, port, "stockai_sync_live", user, password)
            cols = [{"name": "ItemId", "type": "nvarchar"}, {"name": "InventLocationId", "type": "nvarchar"},
                    {"name": "PhysicalInvent", "type": "decimal"}]
            _sync_and_assert(test_tenant["id"], sid, cols, kind="table", obj=table, schema="dbo")
        finally:
            cur.execute(f"DROP TABLE stockai_sync_live.dbo.{table}")
            conn.close()


class TestOracle:

    def test_sync_from_oracle_query(self, test_tenant):
        host, port, user, password = _target("STOCKAI_SYNC_ORACLE", "127.0.0.1:15210:stockai:OraPass123")
        if not _reachable(host, port):
            pytest.skip("Oracle target not reachable")
        import oracledb
        try:
            conn = oracledb.connect(user=user, password=password, host=host, port=port,
                                    service_name="FREEPDB1", tcp_connect_timeout=5)
        except oracledb.Error as exc:
            pytest.skip(f"Oracle not ready: {type(exc).__name__}")
        table = f"MTL_ONHAND_{uuid4().hex[:6]}".upper()
        cur = conn.cursor()
        cur.execute(f"CREATE TABLE {table} (SEGMENT1 VARCHAR2(40), SUBINVENTORY_CODE VARCHAR2(10), "
                    "TRANSACTION_QUANTITY NUMBER)")
        cur.executemany(f"INSERT INTO {table} VALUES (:1, :2, :3)", [r if r[0] else (None, r[1], r[2]) for r in ROWS])
        conn.commit()
        # A READ ONLY transaction on Oracle refuses a table whose definition is
        # younger than its snapshot granularity (ORA-01466) — a real ERP that
        # rebuilds a table mid-sync fails that run out loud. Here, just wait.
        import time
        time.sleep(4)
        try:
            sid = _connect(test_tenant["id"], "oracle", host, port, "FREEPDB1", user, password)
            # A query source (an ERP view or join), not a table.
            q = (f"SELECT SEGMENT1 AS ITEM_CODE, SUBINVENTORY_CODE, SUM(TRANSACTION_QUANTITY) AS ON_HAND "
                 f"FROM {table} GROUP BY SEGMENT1, SUBINVENTORY_CODE")
            cols = [{"name": "ITEM_CODE", "type": ""}, {"name": "SUBINVENTORY_CODE", "type": ""},
                    {"name": "ON_HAND", "type": "number"}]
            _sync_and_assert(test_tenant["id"], sid, cols, kind="query", obj=None,
                             extra={"source_query": q})
        finally:
            cur.execute(f"DROP TABLE {table} PURGE")
            conn.close()
