"""Giant-database harness for inventory sync: load, measure, break, verify.

    python -m backend.scripts.giant_sync load    --engine postgresql --rows 1000000 --ledger 10000000
    python -m backend.scripts.giant_sync measure --engine postgresql --scenarios snapshot,lots,ledger
    python -m backend.scripts.giant_sync chaos   --engine mysql --cases kill_source,pause_source
    python -m backend.scripts.giant_sync drop    --engine postgresql

Everything is generated SERVER-SIDE from deterministic formulas (no random
state, so a run is reproducible bit for bit) and the expected stock is
computed independently in Python from the same formulas — the "oracle" every
run is compared with, key by key, 100 %.

Scenarios (tables in database / schema ``giant_src`` of each engine):

  snapshot     one row per item x warehouse (row_id, updated_at for watermarks)
  lots         4 rows (lots / bins) per item x warehouse -> aggregate SUM
  ledger       movement ledger, sign by type, status filter, opening balances,
               id gaps (late commits re-inserted later -> reconcile drift);
               declaratively PARTITIONED on PostgreSQL
  wide         200 columns + a 4 KB notes column that must never be pulled
  history      SCD2 versions + soft deletes + two companies (client column)
  union        receipts + sales + adjustments + transfers (UNION ALL ledger)
  views        a view over a view over the snapshot table
  poly         (MongoDB) three document shapes normalised by a pipeline

Modes measured per scenario: full, nochange, incremental (1 % changed),
refresh (on demand, whole profile), scoped refresh (100 SKUs), continuous (one
poll cycle after 1 000 changes), push (snapshot rows ingested in 50 k
batches; a replayed batch id is a no-op).

For every step it records wall time, rows/s, the peak working set of THIS
process during the step (sampled), the longest statement and the lock modes
seen on the customer database while it ran, and the oracle comparison.
Results are appended as JSON lines to ``--out``.

StockAI's own side runs on the database named by STOCKAI_GIANT_APP_DB
(default: database giant_app on the dedicated container at 127.0.0.1:5570),
never on the development database, so a giant run cannot disturb anything
else. Development tool: never point it at a customer's database.

Targets (host:port:user:password), defaults are the local containers:

    STOCKAI_GIANT_PG     127.0.0.1:5570:postgres:giantpass
    STOCKAI_GIANT_MYSQL  127.0.0.1:3370:root:giantpass
    STOCKAI_SYNC_MSSQL   127.0.0.1:14330:sa:StockAi_Pass123
    STOCKAI_SYNC_ORACLE  127.0.0.1:15210:stockai:OraPass123   (service FREEPDB1)
    STOCKAI_SYNC_MONGO   127.0.0.1:27018:root:rootpass
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

SRC_DB = "giant_src"
BASE_TS = datetime(2025, 1, 1)
WAREHOUSES = 20
LOTS = 4
MOVES_PER_KEY = 50
TYPES = ("REC", "SAL", "SAL", "ADJ", "TRF_OUT", "TRF_IN")
SIGN = {"OPEN": 1, "REC": 1, "SAL": -1, "ADJ": 1, "TRF_OUT": -1, "TRF_IN": 1}
GAP_EVERY = 1000          # ledger ids with m % 1000 == 999 are left out of the load ("late commits")
CONTAINERS = {"postgresql": None, "mysql": "stockai_giant_mysql", "mssql": "stockai_live_mssql",
              "oracle": "stockai_live_oracle", "mongodb": "stockai_live_mongo"}


def target(engine: str) -> tuple[str, int, str, str]:
    env, default = {
        "postgresql": ("STOCKAI_GIANT_PG", "127.0.0.1:5570:postgres:giantpass"),
        "mysql": ("STOCKAI_GIANT_MYSQL", "127.0.0.1:3370:root:giantpass"),
        "mssql": ("STOCKAI_SYNC_MSSQL", "127.0.0.1:14330:sa:StockAi_Pass123"),
        "oracle": ("STOCKAI_SYNC_ORACLE", "127.0.0.1:15210:stockai:OraPass123"),
        "mongodb": ("STOCKAI_SYNC_MONGO", "127.0.0.1:27018:root:rootpass"),
    }[engine]
    h, p, u, pw = os.environ.get(env, default).split(":", 3)
    return h, int(p), u, pw


# ── Memory of this process ───────────────────────────────────────────────────

def working_set_mb() -> float:
    if sys.platform == "win32":
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / 1e6
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6


# ── Raw connections to the source engines ────────────────────────────────────

def raw_conn(engine: str, database: Optional[str] = SRC_DB):
    h, p, u, pw = target(engine)
    if engine == "postgresql":
        import psycopg2
        c = psycopg2.connect(host=h, port=p, user=u, password=pw, dbname=database or "postgres")
        c.autocommit = True
        return c
    if engine == "mysql":
        import pymysql
        return pymysql.connect(host=h, port=p, user=u, password=pw, database=database, autocommit=True,
                               read_timeout=3600, write_timeout=3600)
    if engine == "mssql":
        import pyodbc
        from backend.datasources.client import best_mssql_odbc_driver
        db = f"DATABASE={database};" if database else ""
        return pyodbc.connect(f"DRIVER={{{best_mssql_odbc_driver()}}};SERVER=tcp:{h},{p};UID={u};PWD={pw};"
                              f"{db}Encrypt=no", autocommit=True, timeout=10)
    if engine == "oracle":
        import oracledb
        c = oracledb.connect(user=u, password=pw, host=h, port=p, service_name="FREEPDB1")
        c.autocommit = True
        return c
    raise ValueError(engine)


def mongo_db():
    from pymongo import MongoClient
    h, p, u, pw = target("mongodb")
    return MongoClient(host=h, port=p, username=u, password=pw, authSource="admin")[SRC_DB]


def run_sql(engine: str, sql: str, conn=None) -> None:
    own = conn is None
    conn = conn or raw_conn(engine)
    try:
        cur = conn.cursor()
        # Tagged so the source monitor does not blame the sync for the
        # harness's own data changes.
        cur.execute(sql if sql.lstrip().upper().startswith("BEGIN") else f"/* giant-harness */ {sql}")
        if engine in ("mysql",):
            conn.commit()
    finally:
        if own:
            conn.close()


def scalar(engine: str, sql: str, conn=None) -> Any:
    own = conn is None
    conn = conn or raw_conn(engine)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        if own:
            conn.close()


# ── Dialect helpers (generation SQL) ─────────────────────────────────────────

class D:
    def __init__(self, engine: str):
        self.e = engine

    def cat(self, *parts: str) -> str:
        if self.e in ("postgresql", "oracle"):
            return " || ".join(parts)
        return f"CONCAT({', '.join(parts)})"

    def mod(self, a: str, b: Any) -> str:
        return f"(({a}) % {b})" if self.e == "mssql" else f"MOD({a}, {b})"

    def idiv(self, a: str, b: Any) -> str:
        if self.e == "mysql":
            return f"(({a}) DIV {b})"
        if self.e == "oracle":
            return f"TRUNC(({a}) / {b})"
        return f"(({a}) / {b})"

    def ts(self, seconds: str) -> str:
        if self.e == "postgresql":
            return f"(TIMESTAMP '2025-01-01 00:00:00' + ({seconds}) * INTERVAL '1 second')"
        if self.e == "mysql":
            return f"TIMESTAMPADD(SECOND, {seconds}, '2025-01-01 00:00:00')"
        if self.e == "mssql":
            return f"DATEADD(SECOND, CAST({seconds} AS INT), CAST('2025-01-01' AS DATETIME2))"
        return f"(TIMESTAMP '2025-01-01 00:00:00' + NUMTODSINTERVAL({seconds}, 'SECOND'))"

    def now(self) -> str:
        return {"postgresql": "NOW()", "mysql": "NOW()", "mssql": "SYSDATETIME()",
                "oracle": "SYSTIMESTAMP"}[self.e]

    def text(self, n: int = 40) -> str:
        return f"VARCHAR2({n})" if self.e == "oracle" else f"VARCHAR({n})"

    def num(self) -> str:
        return "NUMBER(14,3)" if self.e == "oracle" else "DECIMAL(14,3)"

    def big(self) -> str:
        return "NUMBER(19)" if self.e == "oracle" else "BIGINT"

    def stamp(self) -> str:
        return {"postgresql": "TIMESTAMP", "mysql": "DATETIME", "mssql": "DATETIME2",
                "oracle": "TIMESTAMP"}[self.e]

    def clob(self) -> str:
        return {"postgresql": "TEXT", "mysql": "LONGTEXT", "mssql": "NVARCHAR(MAX)", "oracle": "CLOB"}[self.e]

    def pad(self, ch: str, n: int) -> str:
        if self.e == "mssql":
            return f"REPLICATE(CAST('{ch}' AS NVARCHAR(MAX)), {n})"
        if self.e == "oracle":
            return f"TO_CLOB(RPAD('{ch}', {min(n, 4000)}, '{ch}'))"
        return f"REPEAT('{ch}', {n})" if self.e in ("mysql", "postgresql") else ""

    def drop(self, name: str, view: bool = False) -> str:
        kind = "VIEW" if view else "TABLE"
        if self.e == "oracle":
            return (f"BEGIN EXECUTE IMMEDIATE 'DROP {kind} {name}{'' if view else ' PURGE'}'; "
                    f"EXCEPTION WHEN OTHERS THEN NULL; END;")
        return f"DROP {kind} IF EXISTS {name}"


def _series(engine: str, conn, total: int, insert_head: str, select_body: str) -> None:
    """INSERT ... SELECT over g = 0 .. total-1, a million at a time, from a
    1 000 000-row numbers table (built once from digit cross joins)."""
    cur = conn.cursor()
    chunk = 1_000_000
    for k in range(0, (total + chunk - 1) // chunk):
        lo = k * chunk
        g = f"(n + {lo})"
        body = select_body.replace("{g}", g)
        cur.execute(f"{insert_head} {body} FROM g_nums WHERE n < {min(chunk, total - lo)}")
        if engine == "mysql":
            conn.commit()
        print(f"    {min(lo + chunk, total):>12,} rows", flush=True)


def _numbers(engine: str, conn) -> None:
    d = D(engine)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM g_nums")
        if cur.fetchone()[0] == 1_000_000:
            return
    except Exception:  # noqa: BLE001 - not there yet
        if engine == "postgresql":
            pass
    for stmt in (d.drop("g_digits"), d.drop("g_nums")):
        cur.execute(stmt)
    cur.execute(f"CREATE TABLE g_digits (d {d.big()})")
    for i in range(10):
        cur.execute(f"INSERT INTO g_digits (d) VALUES ({i})")
    cur.execute(f"CREATE TABLE g_nums (n {d.big()} PRIMARY KEY)")
    cur.execute("INSERT INTO g_nums (n) SELECT a.d + 10*b.d + 100*c.d + 1000*e.d + 10000*f.d + 100000*h.d "
                "FROM g_digits a CROSS JOIN g_digits b CROSS JOIN g_digits c CROSS JOIN g_digits e "
                "CROSS JOIN g_digits f CROSS JOIN g_digits h")
    if engine == "mysql":
        conn.commit()


# ── Scenario definitions: generation, profile, oracle ────────────────────────

def snap_row(g: int) -> tuple[str, str, int]:
    return f"S{g // WAREHOUSES}", f"W{g % WAREHOUSES}", (g * 7919) % 1000


def load_snapshot(engine: str, conn, rows: int, table: str = "g_snap") -> None:
    d = D(engine)
    cur = conn.cursor()
    cur.execute(d.drop(table))
    cur.execute(f"CREATE TABLE {table} (row_id {d.big()} PRIMARY KEY, item_code {d.text()}, "
                f"warehouse {d.text(10)}, on_hand {d.num()}, unit_cost {d.num()}, updated_at {d.stamp()})")
    _series(engine, conn, rows, f"INSERT INTO {table} (row_id, item_code, warehouse, on_hand, unit_cost, updated_at) SELECT",
            f"{{g}}, {d.cat(chr(39) + 'S' + chr(39), d.idiv('{g}', WAREHOUSES))}, "
            f"{d.cat(chr(39) + 'W' + chr(39), d.mod('{g}', WAREHOUSES))}, {d.mod('{g} * 7919', 1000)}, "
            f"{d.mod('{g}', 89)} + 1, {d.ts(d.mod('{g}', 86400))}")
    for ix in (f"CREATE INDEX {table}_upd ON {table} (updated_at)",
               f"CREATE INDEX {table}_item ON {table} (item_code)"):
        cur.execute(ix)


def expect_snapshot(rows: int, changed: Callable[[int], int] = lambda g: 0) -> dict:
    out = {}
    for g in range(rows):
        s, w, q = snap_row(g)
        out[(s, w)] = q + changed(g)
    return out


def load_lots(engine: str, conn, rows: int) -> None:
    d = D(engine)
    cur = conn.cursor()
    cur.execute(d.drop("g_lots"))
    cur.execute(f"CREATE TABLE g_lots (row_id {d.big()} PRIMARY KEY, item_code {d.text()}, warehouse {d.text(10)}, "
                f"lot {d.text(10)}, on_hand {d.num()}, updated_at {d.stamp()})")
    key = d.idiv("{g}", LOTS)
    _series(engine, conn, rows, "INSERT INTO g_lots (row_id, item_code, warehouse, lot, on_hand, updated_at) SELECT",
            f"{{g}}, {d.cat(chr(39) + 'L' + chr(39), d.idiv(key, WAREHOUSES))}, "
            f"{d.cat(chr(39) + 'W' + chr(39), d.mod(key, WAREHOUSES))}, "
            f"{d.cat(chr(39) + 'LOT' + chr(39), d.mod('{g}', LOTS))}, {d.mod('{g} * 31', 50)}, "
            f"{d.ts(d.mod('{g}', 86400))}")
    cur.execute("CREATE INDEX g_lots_item ON g_lots (item_code)")


def expect_lots(rows: int) -> dict:
    out: dict = {}
    for g in range(rows):
        k = g // LOTS
        key = (f"L{k // WAREHOUSES}", f"W{k % WAREHOUSES}")
        out[key] = out.get(key, 0) + (g * 31) % 50
    return out


def ledger_keys(moves: int) -> int:
    return max(1, moves // MOVES_PER_KEY)


def move_row(m: int, keys: int) -> Optional[tuple[str, str, str, int, str]]:
    """(sku, warehouse, type, qty, status) of movement m."""
    if m < keys:
        k, mtype, qty, status = m, "OPEN", 500, "posted"
    else:
        k = (m * 2654435761) % keys
        mtype = TYPES[m % 6]
        qty = m % 20 + 1
        status = "draft" if m % 17 == 0 else "posted"
    return f"M{k // WAREHOUSES}", f"W{k % WAREHOUSES}", mtype, qty, status


def load_ledger(engine: str, conn, moves: int, *, partitioned: bool = False) -> None:
    d = D(engine)
    cur = conn.cursor()
    keys = ledger_keys(moves)
    cur.execute(d.drop("g_ledger"))
    cols = (f"movement_id {d.big()} NOT NULL, item_code {d.text()}, warehouse {d.text(10)}, "
            f"movement_type {d.text(10)}, quantity {d.num()}, status {d.text(10)}, posted_at {d.stamp()}")
    if partitioned and engine == "postgresql":
        cur.execute(f"CREATE TABLE g_ledger ({cols}) PARTITION BY RANGE (movement_id)")
        step = max(1, moves // 10 + 1)
        for i in range(10):
            hi = "MAXVALUE" if i == 9 else str((i + 1) * step)
            cur.execute(f"CREATE TABLE g_ledger_p{i} PARTITION OF g_ledger FOR VALUES FROM ({i * step}) TO ({hi})")
    else:
        cur.execute(f"CREATE TABLE g_ledger ({cols})")
    k_expr = f"CASE WHEN {{g}} < {keys} THEN {{g}} ELSE {d.mod('{g} * 2654435761', keys)} END"
    typ = ("CASE " + " ".join(f"WHEN {d.mod('{g}', 6)} = {i} THEN '{t}'" for i, t in enumerate(TYPES)) + " END")
    _series(engine, conn, moves,
            "INSERT INTO g_ledger (movement_id, item_code, warehouse, movement_type, quantity, status, posted_at) SELECT",
            f"{{g}}, {d.cat(chr(39) + 'M' + chr(39), d.idiv(k_expr, WAREHOUSES))}, "
            f"{d.cat(chr(39) + 'W' + chr(39), d.mod(k_expr, WAREHOUSES))}, "
            f"CASE WHEN {{g}} < {keys} THEN 'OPEN' ELSE {typ} END, "
            f"CASE WHEN {{g}} < {keys} THEN 500 ELSE {d.mod('{g}', 20)} + 1 END, "
            f"CASE WHEN {{g}} >= {keys} AND {d.mod('{g}', 17)} = 0 THEN 'draft' ELSE 'posted' END, "
            f"{d.ts('{g}')}"
            .replace("FROM", "FROM"))
    # Leave gaps: ids that "commit late" and are re-inserted after the first sync.
    cur.execute(f"DELETE FROM g_ledger WHERE {d.mod('movement_id', GAP_EVERY)} = {GAP_EVERY - 1} "
                f"AND movement_id >= {keys}")
    if engine == "mysql":
        conn.commit()
    cur.execute("CREATE INDEX g_ledger_id ON g_ledger (movement_id)")
    cur.execute("CREATE INDEX g_ledger_item ON g_ledger (item_code)")


def expect_ledger(moves: int, *, with_gaps: bool, extra: int = 0) -> dict:
    keys = ledger_keys(moves)
    raw: dict = {}
    for m in range(moves + extra):
        if with_gaps and m >= keys and m % GAP_EVERY == GAP_EVERY - 1 and m < moves:
            continue
        s, w, t, q, st = move_row(m, keys)
        if st != "posted":
            raw.setdefault((s, w), 0)
            continue
        raw[(s, w)] = raw.get((s, w), 0) + SIGN[t] * q
    return {k: max(0, v) for k, v in raw.items()}


def ledger_profile_extra() -> dict:
    return {"stock_model": "ledger", "negative_policy": "zero",
            "model_config": {"id_column": "movement_id", "cursor_column": "movement_id",
                             "status_column": "status", "posted_values": ["posted"],
                             "sign": {"mode": "type_map", "type_column": "movement_type",
                                      "type_map": SIGN, "unknown": "reject"}}}


def load_wide(engine: str, conn, rows: int) -> None:
    d = D(engine)
    cur = conn.cursor()
    cur.execute(d.drop("g_wide"))
    extra = ", ".join(f"c{i:03d} {d.text(30)}" for i in range(196))
    cur.execute(f"CREATE TABLE g_wide (row_id {d.big()} PRIMARY KEY, item_code {d.text()}, warehouse {d.text(10)}, "
                f"on_hand {d.num()}, {extra}, notes {d.clob()})")
    fill = ", ".join("'xxxxxxxxxxxxxxxxxxxxxxxxxxxxx'" for _ in range(196))
    cols = ", ".join(f"c{i:03d}" for i in range(196))
    _series(engine, conn, rows, f"INSERT INTO g_wide (row_id, item_code, warehouse, on_hand, {cols}, notes) SELECT",
            f"{{g}}, {d.cat(chr(39) + 'S' + chr(39), d.idiv('{g}', WAREHOUSES))}, "
            f"{d.cat(chr(39) + 'W' + chr(39), d.mod('{g}', WAREHOUSES))}, {d.mod('{g} * 7919', 1000)}, "
            f"{fill}, {d.pad('n', 4000)}")


def hist_row(g: int) -> tuple[str, str, str, int, int, bool]:
    key = g // 6
    company = "C1" if (g // 3) % 2 == 0 else "C2"
    version = g % 3
    deleted = key % 50 == 0 and version == 2
    return company, f"H{key // WAREHOUSES}", f"W{key % WAREHOUSES}", (g * 13) % 700, version, deleted


def load_history(engine: str, conn, rows: int) -> None:
    d = D(engine)
    cur = conn.cursor()
    cur.execute(d.drop("g_hist"))
    cur.execute(f"CREATE TABLE g_hist (row_id {d.big()} PRIMARY KEY, company {d.text(4)}, item_code {d.text()}, "
                f"warehouse {d.text(10)}, on_hand {d.num()}, valid_from {d.stamp()}, valid_to {d.stamp()}, "
                f"deleted_at {d.stamp()})")
    key = d.idiv("{g}", 6)
    _series(engine, conn, rows,
            "INSERT INTO g_hist (row_id, company, item_code, warehouse, on_hand, valid_from, valid_to, deleted_at) SELECT",
            f"{{g}}, CASE WHEN {d.mod(d.idiv('{g}', 3), 2)} = 0 THEN 'C1' ELSE 'C2' END, "
            f"{d.cat(chr(39) + 'H' + chr(39), d.idiv(key, WAREHOUSES))}, "
            f"{d.cat(chr(39) + 'W' + chr(39), d.mod(key, WAREHOUSES))}, {d.mod('{g} * 13', 700)}, "
            f"{d.ts(d.mod('{g}', 3) + ' * 1000')}, "
            f"CASE WHEN {d.mod('{g}', 3)} = 2 THEN NULL ELSE {d.ts(d.mod('{g}', 3) + ' * 1000 + 1000')} END, "
            f"CASE WHEN {d.mod(key, 50)} = 0 AND {d.mod('{g}', 3)} = 2 THEN {d.ts('5000')} ELSE NULL END")
    cur.execute("CREATE INDEX g_hist_cur ON g_hist (company, valid_to)")


HISTORY_QUERY = ("SELECT item_code, warehouse, on_hand FROM g_hist "
                 "WHERE company = 'C1' AND valid_to IS NULL AND deleted_at IS NULL")


def expect_history(rows: int) -> dict:
    out = {}
    for g in range(rows):
        c, s, w, q, v, deleted = hist_row(g)
        if c == "C1" and v == 2 and not deleted:
            out[(s, w)] = q
    return out


def load_union(engine: str, conn, moves: int) -> None:
    """purchase_receipts / sales_lines / stock_adjustments / transfers, the
    shape the `generic_union_ledger` preset reads."""
    d = D(engine)
    cur = conn.cursor()
    per = max(1, moves // 4)
    keys = max(1, per // 10)
    k = d.mod("{g} * 7", keys)
    sku = d.cat(chr(39) + "U" + chr(39), d.idiv(k, WAREHOUSES))
    wh = d.cat(chr(39) + "W" + chr(39), d.mod(k, WAREHOUSES))
    wh2 = d.cat(chr(39) + "W" + chr(39), d.mod(k + " + 1", WAREHOUSES))
    for t in ("purchase_receipts", "sales_lines", "stock_adjustments", "transfers"):
        cur.execute(d.drop(t))
    cur.execute(f"CREATE TABLE purchase_receipts (receipt_id {d.big()}, item_code {d.text()}, warehouse_code {d.text(10)}, "
                f"quantity {d.num()}, posted_at {d.stamp()}, status {d.text(10)})")
    cur.execute(f"CREATE TABLE sales_lines (line_id {d.big()}, item_code {d.text()}, warehouse_code {d.text(10)}, "
                f"quantity {d.num()}, posted_at {d.stamp()}, status {d.text(10)})")
    cur.execute(f"CREATE TABLE stock_adjustments (adjustment_id {d.big()}, item_code {d.text()}, "
                f"warehouse_code {d.text(10)}, quantity_delta {d.num()}, posted_at {d.stamp()})")
    cur.execute(f"CREATE TABLE transfers (transfer_id {d.big()}, item_code {d.text()}, from_warehouse {d.text(10)}, "
                f"to_warehouse {d.text(10)}, quantity {d.num()}, shipped_at {d.stamp()}, received_at {d.stamp()})")
    _series(engine, conn, per, "INSERT INTO purchase_receipts SELECT",
            f"{{g}}, {sku}, {wh}, {d.mod('{g}', 30)} + 20, {d.ts('{g}')}, "
            f"CASE WHEN {d.mod('{g}', 11)} = 0 THEN 'draft' ELSE 'posted' END")
    _series(engine, conn, per, "INSERT INTO sales_lines SELECT",
            f"{{g}}, {sku}, {wh}, {d.mod('{g}', 7)} + 1, {d.ts('{g}')}, "
            f"CASE WHEN {d.mod('{g}', 13)} = 0 THEN 'cancelled' ELSE 'posted' END")
    _series(engine, conn, per, "INSERT INTO stock_adjustments SELECT",
            f"{{g}}, {sku}, {wh}, {d.mod('{g}', 5)} - 2, {d.ts('{g}')}")
    _series(engine, conn, per, "INSERT INTO transfers SELECT",
            f"{{g}}, {sku}, {wh}, {wh2}, {d.mod('{g}', 3)} + 1, {d.ts('{g}')}, "
            f"CASE WHEN {d.mod('{g}', 9)} = 0 THEN NULL ELSE {d.ts('{g} + 60')} END")


def expect_union(moves: int) -> dict:
    per = max(1, moves // 4)
    keys = max(1, per // 10)
    raw: dict = {}

    def add(k, q):
        raw[k] = raw.get(k, 0) + q
    for g in range(per):
        k = (g * 7) % keys
        a = (f"U{k // WAREHOUSES}", f"W{k % WAREHOUSES}")
        b = (f"U{k // WAREHOUSES}", f"W{(k + 1) % WAREHOUSES}")
        if g % 11:
            add(a, g % 30 + 20)
        if g % 13:
            add(a, -(g % 7 + 1))
        add(a, g % 5 - 2)
        add(a, -(g % 3 + 1))
        if g % 9:
            add(b, g % 3 + 1)
    return {k: max(0, v) for k, v in raw.items()}


def load_views(engine: str, conn) -> None:
    d = D(engine)
    cur = conn.cursor()
    cur.execute(d.drop("g_view2", view=True))
    cur.execute(d.drop("g_view1", view=True))
    cur.execute("CREATE VIEW g_view1 AS SELECT row_id, item_code, warehouse, on_hand, updated_at FROM g_snap "
                "WHERE on_hand >= 0")
    cur.execute("CREATE VIEW g_view2 AS SELECT item_code AS sku, warehouse AS wh, on_hand AS qty, updated_at "
                "FROM g_view1")


# ── MongoDB generation ───────────────────────────────────────────────────────

def load_mongo(rows: int, moves: int, poly: int) -> None:
    db = mongo_db()
    for name in ("g_snap", "g_ledger", "g_poly"):
        db[name].drop()
    batch = []
    for g in range(rows):
        s, w, q = snap_row(g)
        batch.append({"_id": g, "item": {"code": s}, "warehouse": w, "onHand": q,
                      "updatedAt": datetime(2025, 1, 1) + __import__("datetime").timedelta(seconds=g % 86400)})
        if len(batch) == 20000:
            db.g_snap.insert_many(batch, ordered=False)
            batch = []
    if batch:
        db.g_snap.insert_many(batch, ordered=False)
    db.g_snap.create_index("updatedAt")
    db.g_snap.create_index("item.code")
    keys = ledger_keys(moves)
    batch = []
    for m in range(moves):
        if m >= keys and m % GAP_EVERY == GAP_EVERY - 1:
            continue
        s, w, t, q, st = move_row(m, keys)
        batch.append({"_id": m, "movement_id": m, "item_code": s, "warehouse": w, "movement_type": t,
                      "quantity": q, "status": st})
        if len(batch) == 20000:
            db.g_ledger.insert_many(batch, ordered=False)
            batch = []
    if batch:
        db.g_ledger.insert_many(batch, ordered=False)
    db.g_ledger.create_index("movement_id")
    batch = []
    for g in range(poly):
        s, w, q = snap_row(g)
        shape = g % 3
        doc = ({"sku": s, "wh": w, "qty": q} if shape == 0 else
               {"product": {"code": s}, "location": {"code": w}, "stock": {"onHand": q}} if shape == 1 else
               {"itemNo": s, "site": w, "balance": str(q)})
        doc["_id"] = g
        batch.append(doc)
        if len(batch) == 20000:
            db.g_poly.insert_many(batch, ordered=False)
            batch = []
    if batch:
        db.g_poly.insert_many(batch, ordered=False)


POLY_PIPELINE = [{"$project": {
    "_id": 0,
    "sku": {"$ifNull": ["$sku", {"$ifNull": ["$product.code", "$itemNo"]}]},
    "wh": {"$ifNull": ["$wh", {"$ifNull": ["$location.code", "$site"]}]},
    "qty": {"$ifNull": ["$qty", {"$ifNull": ["$stock.onHand", {"$toDouble": "$balance"}]}]},
}}]


# ── StockAI side ─────────────────────────────────────────────────────────────

def boot_app() -> None:
    from backend.config import settings
    url = os.environ.get("STOCKAI_GIANT_APP_DB", "postgresql://postgres:giantpass@127.0.0.1:5570/giant_app")
    import psycopg2
    from urllib.parse import urlparse
    u = urlparse(url)
    admin = psycopg2.connect(host=u.hostname, port=u.port, user=u.username, password=u.password, dbname="postgres")
    admin.autocommit = True
    c = admin.cursor()
    c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (u.path.lstrip("/"),))
    if not c.fetchone():
        c.execute(f"CREATE DATABASE {u.path.lstrip('/')}")
    admin.close()
    settings.database_url = url
    from backend.db.connection import init_pool
    init_pool(url)
    from backend.db.migrations import run_all
    run_all()
    settings.testing_mode = True
    settings.sql_sources_allow_private_hosts = True
    settings.inventory_sync_max_rows_per_run = 100_000_000
    settings.inventory_sync_max_ingest_rows = 100_000


def make_source(tid: str, engine: str) -> str:
    from backend.datasources.service import create_sql_source
    from backend.db.connection import execute
    h, p, u, pw = target(engine)
    database = "FREEPDB1" if engine == "oracle" else SRC_DB
    src = create_sql_source(tid, "giant", f"giant-{engine}", h, p, database, u, pw, engine, ssl_mode="disable")
    execute("UPDATE datasets SET connection_status = 'connected' WHERE id = %s", (src["id"],))
    return src["id"]


def stock_of(tid: str) -> dict:
    from backend.db.connection import query
    out = {}
    for r in query("SELECT sku, warehouse, current_stock FROM inventory_stock WHERE tenant_id = %s", (tid,)):
        out[(r["sku"], r["warehouse"].upper())] = float(r["current_stock"])
    return out


def compare(tid: str, expected: dict) -> dict:
    got = stock_of(tid)
    exp = {(s, w.upper()): float(q) for (s, w), q in expected.items()}
    missing = [k for k in exp if k not in got]
    extra = [k for k in got if k not in exp]
    wrong = [k for k in exp if k in got and abs(got[k] - exp[k]) > 1e-6]
    return {"expected": len(exp), "got": len(got), "missing": len(missing), "extra": len(extra),
            "wrong": len(wrong), "match": not (missing or extra or wrong),
            "sample": [str(x) for x in (missing[:2] + extra[:2] + wrong[:2])]}


def stock_fingerprint(tid: str) -> tuple:
    from backend.db.connection import query_one
    r = query_one("SELECT COUNT(*) AS n, COALESCE(SUM(current_stock), 0) AS s, MAX(updated_at) AS u "
                  "FROM inventory_stock WHERE tenant_id = %s", (tid,))
    return (r["n"], float(r["s"]), str(r["u"]))


# ── Customer-side monitor ────────────────────────────────────────────────────

class SourceMonitor:
    """Samples the source while a step runs: the longest running statement of
    any OTHER session on the source database, and the lock modes held."""

    def __init__(self, engine: str):
        self.engine = engine
        self.max_s = 0.0
        self.locks: set[str] = set()
        self.lock_samples: set[str] = set()
        self.error: Optional[str] = None
        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None

    def _sample(self, conn) -> None:
        cur = conn.cursor() if self.engine != "mongodb" else None
        if self.engine == "postgresql":
            # Client sessions only (autovacuum on freshly loaded tables is not ours),
            # and not the harness's own data-changing statements (they say so).
            cur.execute("SELECT COALESCE(MAX(EXTRACT(EPOCH FROM NOW() - query_start)), 0) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid() AND state = 'active' "
                        "AND backend_type = 'client backend' AND query NOT ILIKE '%%giant-harness%%'", (SRC_DB,))
            self.max_s = max(self.max_s, float(cur.fetchone()[0] or 0))
            cur.execute("SELECT DISTINCT l.mode, LEFT(a.query, 60) FROM pg_locks l "
                        "JOIN pg_stat_activity a ON a.pid = l.pid "
                        "WHERE a.datname = %s AND a.pid <> pg_backend_pid() AND l.locktype = 'relation' "
                        "AND a.backend_type = 'client backend' AND a.query NOT ILIKE '%%giant-harness%%'", (SRC_DB,))
            for mode, q in cur.fetchall():
                self.locks.add(mode)
                if mode != "AccessShareLock":
                    self.lock_samples.add(q)
        elif self.engine == "mysql":
            cur.execute("SELECT COALESCE(MAX(TIME), 0) FROM information_schema.PROCESSLIST "
                        "WHERE DB = %s AND COMMAND <> 'Sleep' AND ID <> CONNECTION_ID()", (SRC_DB,))
            self.max_s = max(self.max_s, float(cur.fetchone()[0] or 0))
            cur.execute("SELECT DISTINCT LOCK_MODE FROM performance_schema.data_locks WHERE OBJECT_SCHEMA = %s",
                        (SRC_DB,))
            self.locks.update(str(r[0]) for r in cur.fetchall())
        elif self.engine == "mssql":
            cur.execute("SELECT COALESCE(MAX(r.total_elapsed_time), 0) / 1000.0 FROM sys.dm_exec_requests r "
                        "WHERE r.database_id = DB_ID(?) AND r.session_id <> @@SPID", SRC_DB)
            self.max_s = max(self.max_s, float(cur.fetchone()[0] or 0))
            cur.execute("SELECT DISTINCT request_mode FROM sys.dm_tran_locks WHERE resource_database_id = DB_ID(?) "
                        "AND request_session_id <> @@SPID AND resource_type <> 'DATABASE'", SRC_DB)
            self.locks.update(str(r[0]) for r in cur.fetchall())
        elif self.engine == "oracle":
            cur.execute("SELECT NVL(MAX(last_call_et), 0) FROM v$session WHERE username = USER "
                        "AND status = 'ACTIVE' AND sid <> SYS_CONTEXT('USERENV', 'SID')")
            self.max_s = max(self.max_s, float(cur.fetchone()[0] or 0))
            cur.execute("SELECT DISTINCT l.lmode FROM v$lock l JOIN v$session s ON s.sid = l.sid "
                        "WHERE s.username = USER AND s.sid <> SYS_CONTEXT('USERENV', 'SID') AND l.type = 'TM'")
            self.locks.update(f"TM{r[0]}" for r in cur.fetchall())
        elif self.engine == "mongodb":
            ops = conn.client.admin.command("currentOp", {"active": True, "ns": {"$regex": f"^{SRC_DB}\\."}})
            for op in ops.get("inprog", []):
                self.max_s = max(self.max_s, float(op.get("secs_running") or 0))

    def _loop(self) -> None:
        try:
            conn = mongo_db() if self.engine == "mongodb" else raw_conn(self.engine)
        except Exception as exc:  # noqa: BLE001
            self.error = type(exc).__name__
            return
        while not self._stop.is_set():
            try:
                self._sample(conn)
            except Exception as exc:  # noqa: BLE001 - monitoring must never break a run
                self.error = f"{type(exc).__name__}: {str(exc)[:80]}"
                try:
                    conn = mongo_db() if self.engine == "mongodb" else raw_conn(self.engine)
                except Exception:  # noqa: BLE001
                    pass
            self._stop.wait(0.5)

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._t:
            self._t.join(timeout=5)


class MemSampler:
    def __init__(self):
        self.peak = 0.0
        self._stop = threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, working_set_mb())
            self._stop.wait(0.2)

    def __enter__(self):
        self.peak = working_set_mb()
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()


# ── Measuring ────────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self, path: str, engine: str):
        self.path, self.engine = path, engine

    def write(self, rec: dict) -> dict:
        rec = {"engine": self.engine, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        brief = {k: rec.get(k) for k in ("scenario", "mode", "status", "error", "seconds", "rows", "rows_per_s",
                                         "peak_mb", "src_max_stmt_s", "src_locks")}
        brief["oracle"] = (rec.get("oracle") or {}).get("match")
        print("  ", json.dumps(brief, default=str), flush=True)
        return rec


@contextmanager
def measured(engine: str) -> Iterator[dict]:
    box: dict = {}
    with SourceMonitor(engine) as mon, MemSampler() as mem:
        t0 = time.monotonic()
        yield box
        box["seconds"] = round(time.monotonic() - t0, 2)
    box["peak_mb"] = round(mem.peak, 0)
    box["src_max_stmt_s"] = round(mon.max_s, 1)
    box["src_locks"] = sorted(mon.locks)
    if mon.lock_samples:
        box["src_lock_queries"] = sorted(mon.lock_samples)[:3]
    if mon.error:
        box["monitor_error"] = mon.error


def do_run(tid: str, pid: str, *, kind: Optional[str] = None, scope: Optional[dict] = None,
           override: bool = False) -> dict:
    from backend.inventory_sync import engine as eng
    run_id = eng.start_run(tid, pid, "manual", kind=kind, scope=scope)
    return eng.execute_run(tid, pid, run_id, override_brake=override)


def step(rec: Recorder, engine: str, scenario: str, mode: str, fn: Callable[[], dict],
         expected: Optional[Callable[[], dict]] = None, tid: Optional[str] = None) -> dict:
    with measured(engine) as box:
        try:
            run = fn() or {}
        except Exception as exc:  # noqa: BLE001 - a harness step reports, it does not stop the matrix
            run = {"status": "exception", "error_code": f"{type(exc).__name__}: {str(exc)[:200]}"}
    rows = run.get("rows_read") or run.get("rows") or 0
    out = {"scenario": scenario, "mode": mode, "status": run.get("status"), "error": run.get("error_code"),
           "rows": rows, "created": run.get("created"), "updated": run.get("updated"),
           "unchanged": run.get("unchanged"), "rejected": run.get("rejected"),
           "rows_per_s": round(rows / box["seconds"]) if box.get("seconds") and rows else None, **box}
    if expected is not None and tid:
        out["oracle"] = compare(tid, expected())
    return rec.write(out)


def new_tenant(name: str) -> str:
    from backend.tenants.service import create_tenant
    return create_tenant(f"giant-{name}-{int(time.time())}")["id"]


def erase(tid: str) -> None:
    from backend.tenants.data_export import delete_tenant
    delete_tenant(tid)


def make_profile(tid: str, sid: str, **body) -> dict:
    from backend.inventory_sync import profiles
    base = {"name": f"giant-{body.get('source_object') or 'q'}", "data_source_id": sid,
            "allow_new_warehouses": True, "interval_minutes": 60, "brake_max_vanished_pct": 20,
            "brake_min_row_ratio": 0.5, "duplicate_policy": "reject"}
    base.update(body)
    p = profiles.create(tid, "giant", base)
    # The harness is the person who checked the preview: mark it and turn it on
    # (no scheduler loop runs here, so "on" only lets refresh / polls pick it).
    profiles.mark_dry_run(tid, p["id"], profiles.get(tid, p["id"])["config_hash"])
    profiles.set_enabled(tid, p["id"], True)
    return profiles.get(tid, p["id"])


def _q(engine: str, sql: str) -> str:
    """Oracle folds unquoted names to upper case; our generated tables were
    created unquoted, so their columns are upper case there."""
    return sql


def cols(engine: str, *names: str) -> list[str]:
    return [n.upper() for n in names] if engine == "oracle" else list(names)


def snapshot_mapping(engine: str, sku="item_code", wh="warehouse", qty="on_hand") -> dict:
    s, w, q = cols(engine, sku, wh, qty)
    return {"sku": s, "warehouse": w, "current_stock": q}


def schema_kw(engine: str) -> dict:
    if engine == "postgresql":
        return {"source_schema": "public"}
    if engine == "mssql":
        return {"source_schema": "dbo"}
    if engine == "mysql":
        return {"source_schema": SRC_DB}
    if engine == "oracle":
        return {"source_schema": target("oracle")[2].upper()}
    return {}


def table_name(engine: str, name: str) -> str:
    return name.upper() if engine == "oracle" else name


# ── The measurement matrix ───────────────────────────────────────────────────

def measure(engine: str, scenarios: list[str], rows: int, moves: int, out: str) -> None:
    from backend.inventory_sync import ingest, profiles, scheduler
    from backend.db.connection import execute
    rec = Recorder(out, engine)
    d = D(engine)
    for scenario in scenarios:
        print(f"== {engine} / {scenario}", flush=True)
        tid = new_tenant(f"{engine[:5]}-{scenario}")
        try:
            sid = make_source(tid, engine) if scenario != "push" else None
            if scenario in ("snapshot", "views"):
                if engine == "mongodb":
                    p = make_profile(tid, sid, source_kind="collection", source_object="g_snap",
                                     mapping={"sku": "item.code", "warehouse": "warehouse", "current_stock": "onHand"},
                                     watermark_column="updatedAt")
                elif scenario == "views":
                    p = make_profile(tid, sid, source_kind="table", source_object=table_name(engine, "g_view2"),
                                     mapping=snapshot_mapping(engine, "sku", "wh", "qty"), **schema_kw(engine),
                                     watermark_column=cols(engine, "updated_at")[0])
                else:
                    p = make_profile(tid, sid, source_kind="table", source_object=table_name(engine, "g_snap"),
                                     mapping=snapshot_mapping(engine), **schema_kw(engine),
                                     watermark_column=cols(engine, "updated_at")[0])
                exp = lambda: expect_snapshot(rows)  # noqa: E731
                step(rec, engine, scenario, "full", lambda: do_run(tid, p["id"]), exp, tid)
                step(rec, engine, scenario, "nochange", lambda: do_run(tid, p["id"]), exp, tid)
                if scenario == "views":
                    continue
                # 1 % of the rows change; incremental reads them by watermark.
                execute("UPDATE inventory_sync_profiles SET mode = 'incremental' WHERE id = %s", (p["id"],))
                _bump(engine, rows, every=100, delta=1)
                exp1 = lambda: expect_snapshot(rows, lambda g: 1 if g % 100 == 0 else 0)  # noqa: E731
                step(rec, engine, scenario, "incremental_1pct",
                     lambda: do_run(tid, p["id"], kind="incremental"), exp1, tid)
                # On-demand refresh: another 1 %, through the scheduler's door.
                _bump(engine, rows, every=100, delta=1)
                exp2 = lambda: expect_snapshot(rows, lambda g: 2 if g % 100 == 0 else 0)  # noqa: E731
                execute("UPDATE inventory_sync_profiles SET last_refresh_at = NULL WHERE id = %s", (p["id"],))
                step(rec, engine, scenario, "refresh",
                     lambda: _refresh(tid, p["id"]), exp2, tid)
                # Scoped refresh of 100 SKUs whose rows changed.
                skus = [f"S{(g // WAREHOUSES)}" for g in range(0, rows, max(1, rows // 100))][:100]
                _bump(engine, rows, every=100, delta=1)
                exp3 = lambda: expect_snapshot(rows, lambda g: (3 if f"S{g // WAREHOUSES}" in set(skus) else 2)  # noqa: E731
                                               if g % 100 == 0 else 0)
                execute("UPDATE inventory_sync_profiles SET last_refresh_at = NULL WHERE id = %s", (p["id"],))
                step(rec, engine, scenario, "scoped_refresh_100_skus",
                     lambda: _refresh(tid, p["id"], skus=skus), exp3, tid)
                # Continuous: one poll cycle after 1 000 changes, timed from the change.
                execute("UPDATE inventory_sync_profiles SET freshness = 'continuous', mode = 'incremental' "
                        "WHERE id = %s", (p["id"],))
                # The first continuous cycle is a reconcile (it sets the next one);
                # the measured one after it is a plain poll.
                step(rec, engine, scenario, "continuous_first_cycle", lambda: _poll(tid, p["id"]),
                     lambda: expect_snapshot(rows, lambda g: 3 if g % 100 == 0 else 0), tid)
                _bump(engine, rows, every=max(1, rows // 1000), delta=5, only_first=1000)
                step(rec, engine, scenario, "continuous_poll_1000",
                     lambda: _poll(tid, p["id"]), None, tid)
                _bump(engine, rows, every=100, delta=-3)            # back to the generator's values
                _bump(engine, rows, every=max(1, rows // 1000), delta=-5, only_first=1000)
                step(rec, engine, scenario, "reconcile_full", lambda: do_run(tid, p["id"], kind="full",
                                                                             override=True),
                     lambda: expect_snapshot(rows), tid)
            elif scenario == "lots":
                p = make_profile(tid, sid, source_kind="table", source_object=table_name(engine, "g_lots"),
                                 mapping=snapshot_mapping(engine), stock_model="aggregate",
                                 model_config={"aggregate": "sum"}, **schema_kw(engine))
                step(rec, engine, scenario, "full", lambda: do_run(tid, p["id"]), lambda: expect_lots(rows), tid)
                step(rec, engine, scenario, "nochange", lambda: do_run(tid, p["id"]), lambda: expect_lots(rows), tid)
            elif scenario == "ledger":
                if engine == "mongodb":
                    p = make_profile(tid, sid, source_kind="collection", source_object="g_ledger",
                                     mapping={"sku": "item_code", "warehouse": "warehouse",
                                              "current_stock": "quantity"}, **ledger_profile_extra())
                else:
                    extra = ledger_profile_extra()
                    mc = extra["model_config"]
                    for k in ("id_column", "cursor_column", "status_column"):
                        mc[k] = cols(engine, mc[k])[0]
                    mc["sign"]["type_column"] = cols(engine, "movement_type")[0]
                    p = make_profile(tid, sid, source_kind="table", source_object=table_name(engine, "g_ledger"),
                                     mapping=snapshot_mapping(engine, qty="quantity"), **schema_kw(engine),
                                     **{k: v for k, v in extra.items() if k != "model_config"}, model_config=mc)
                exp = lambda: expect_ledger(moves, with_gaps=True)  # noqa: E731
                step(rec, engine, scenario, "full", lambda: do_run(tid, p["id"]), exp, tid)
                step(rec, engine, scenario, "nochange", lambda: do_run(tid, p["id"], kind="full"), exp, tid)
                if engine != "mongodb":
                    # New movements after the cursor: incremental, exactly once.
                    _append_moves(engine, moves, 10_000)
                    exp2 = lambda: _ledger_after(moves, appended=10_000, gaps_filled=False)  # noqa: E731
                    step(rec, engine, scenario, "incremental_10k_moves",
                         lambda: do_run(tid, p["id"], kind="incremental"), exp2, tid)
                    # The late commits (ids below the cursor) arrive: incremental cannot
                    # see them; the reconcile does, counts the drift and heals.
                    _fill_gaps(engine, moves)
                    step(rec, engine, scenario, "incremental_after_late_commits",
                         lambda: do_run(tid, p["id"], kind="incremental"), exp2, tid)
                    exp3 = lambda: _ledger_after(moves, appended=10_000, gaps_filled=True)  # noqa: E731
                    step(rec, engine, scenario, "reconcile_heals_drift",
                         lambda: do_run(tid, p["id"], kind="reconcile"), exp3, tid)
                    _drop_appended(engine, moves)
            elif scenario == "wide":
                n = min(rows, 100_000)
                p = make_profile(tid, sid, source_kind="table", source_object=table_name(engine, "g_wide"),
                                 mapping=snapshot_mapping(engine), **schema_kw(engine))
                step(rec, engine, scenario, "full_projected", lambda: do_run(tid, p["id"]),
                     lambda: expect_snapshot(n), tid)
                from backend.inventory_sync import reader
                orig = reader.prepare_projection
                reader.prepare_projection = lambda t, prof: None
                try:
                    step(rec, engine, scenario, "nochange_select_star_baseline", lambda: do_run(tid, p["id"]),
                         lambda: expect_snapshot(n), tid)
                finally:
                    reader.prepare_projection = orig
                step(rec, engine, scenario, "nochange_projected", lambda: do_run(tid, p["id"]),
                     lambda: expect_snapshot(n), tid)
            elif scenario == "history":
                p = make_profile(tid, sid, source_kind="query", source_query=HISTORY_QUERY,
                                 mapping=snapshot_mapping(engine))
                step(rec, engine, scenario, "full", lambda: do_run(tid, p["id"]),
                     lambda: expect_history(rows), tid)
            elif scenario == "union":
                from backend.inventory_sync import presets
                body = presets.render("generic_union_ledger", engine, {})["profile"]
                mapping = snapshot_mapping(engine, sku="sku", wh="warehouse", qty="quantity")
                mc = body["model_config"]
                mc["id_column"] = mc["cursor_column"] = cols(engine, "movement_id")[0]
                p = make_profile(tid, sid, source_kind="query", source_query=body["source_query"],
                                 mapping=mapping, stock_model="ledger", model_config=mc, negative_policy="zero")
                step(rec, engine, scenario, "full", lambda: do_run(tid, p["id"]), lambda: expect_union(moves), tid)
            elif scenario == "poly" and engine == "mongodb":
                n = min(rows, 300_000)
                p = make_profile(tid, sid, source_kind="pipeline", source_object="g_poly",
                                 source_query=json.dumps(POLY_PIPELINE),
                                 mapping={"sku": "sku", "warehouse": "wh", "current_stock": "qty"})
                step(rec, engine, scenario, "full", lambda: do_run(tid, p["id"]), lambda: expect_snapshot(n), tid)
            elif scenario == "push":
                p = make_profile(tid, None, source_kind="push", name="giant-push",
                                 mapping={"sku": "sku", "warehouse": "wh", "current_stock": "qty"})
                step(rec, engine, scenario, "push_snapshot", lambda: _push(tid, p["id"], rows),
                     lambda: expect_snapshot(rows), tid)
                fp = stock_fingerprint(tid)
                step(rec, engine, scenario, "push_replayed_batch", lambda: _push(tid, p["id"], rows, only_first=True),
                     lambda: expect_snapshot(rows), tid)
                assert stock_fingerprint(tid) == fp
        finally:
            erase(tid)


def _bump(engine: str, rows: int, *, every: int, delta: int, only_first: Optional[int] = None) -> None:
    d = D(engine)
    if engine == "mongodb":
        db = mongo_db()
        flt: dict = {"$expr": {"$eq": [{"$mod": ["$_id", every]}, 0]}}
        if only_first:
            flt["_id"] = {"$lt": every * only_first}
        db.g_snap.update_many(flt, {"$inc": {"onHand": delta}, "$currentDate": {"updatedAt": True}})
        return
    lim = f" AND row_id < {every * only_first}" if only_first else ""
    run_sql(engine, f"UPDATE g_snap SET on_hand = on_hand + ({delta}), updated_at = {d.now()} "
                    f"WHERE {d.mod('row_id', every)} = 0{lim}")


def _refresh(tid: str, pid: str, skus: Optional[list[str]] = None) -> dict:
    from backend.inventory_sync import scheduler
    from backend.db.connection import query_one
    r = scheduler.refresh(tid, "giant", profile_id=pid, skus=skus, wait_s=3600)
    run = query_one("SELECT * FROM inventory_sync_runs WHERE profile_id = %s ORDER BY started_at DESC LIMIT 1",
                    (pid,))
    return {**(run or {}), "refresh_status": r["profiles"][0]["status"] if r.get("profiles") else None}


def _poll(tid: str, pid: str) -> dict:
    from backend.inventory_sync import scheduler
    from backend.db.connection import execute, query_one
    execute("UPDATE inventory_sync_profiles SET next_run_at = NOW() - INTERVAL '1 second', lease_until = NULL "
            "WHERE id = %s", (pid,))
    claimed = scheduler.claim_due(owner="giant", profile_id=pid)
    if not claimed:
        return {"status": "not_claimed"}
    scheduler.run_claimed(claimed, "giant", "continuous")
    return query_one("SELECT * FROM inventory_sync_runs WHERE profile_id = %s ORDER BY started_at DESC LIMIT 1",
                     (pid,)) or {}


def _append_moves(engine: str, moves: int, n: int) -> None:
    d = D(engine)
    keys = ledger_keys(moves)
    k_expr = d.mod("{g} * 2654435761", keys)
    typ = ("CASE " + " ".join(f"WHEN {d.mod('{g}', 6)} = {i} THEN '{t}'" for i, t in enumerate(TYPES)) + " END")
    conn = raw_conn(engine)
    try:
        cur = conn.cursor()
        body = (f"{{g}}, {d.cat(chr(39) + 'M' + chr(39), d.idiv(k_expr, WAREHOUSES))}, "
                f"{d.cat(chr(39) + 'W' + chr(39), d.mod(k_expr, WAREHOUSES))}, {typ}, {d.mod('{g}', 20)} + 1, "
                f"CASE WHEN {d.mod('{g}', 17)} = 0 THEN 'draft' ELSE 'posted' END, {d.ts('{g}')}")
        g = f"(n + {moves})"
        cur.execute("/* giant-harness */ INSERT INTO g_ledger (movement_id, item_code, warehouse, movement_type, quantity, status, "
                    f"posted_at) SELECT {body.replace('{g}', g)} FROM g_nums WHERE n < {n}")
        if engine == "mysql":
            conn.commit()
    finally:
        conn.close()


def _fill_gaps(engine: str, moves: int) -> None:
    d = D(engine)
    keys = ledger_keys(moves)
    k_expr = d.mod("{g} * 2654435761", keys)
    typ = ("CASE " + " ".join(f"WHEN {d.mod('{g}', 6)} = {i} THEN '{t}'" for i, t in enumerate(TYPES)) + " END")
    body = (f"{{g}}, {d.cat(chr(39) + 'M' + chr(39), d.idiv(k_expr, WAREHOUSES))}, "
            f"{d.cat(chr(39) + 'W' + chr(39), d.mod(k_expr, WAREHOUSES))}, {typ}, {d.mod('{g}', 20)} + 1, "
            f"CASE WHEN {d.mod('{g}', 17)} = 0 THEN 'draft' ELSE 'posted' END, {d.ts('{g}')}")
    g = f"(n * {GAP_EVERY} + {GAP_EVERY - 1})"
    conn = raw_conn(engine)
    try:
        cur = conn.cursor()
        cur.execute("/* giant-harness */ INSERT INTO g_ledger (movement_id, item_code, warehouse, movement_type, quantity, status, "
                    f"posted_at) SELECT {body.replace('{g}', g)} FROM g_nums "
                    f"WHERE n * {GAP_EVERY} + {GAP_EVERY - 1} >= {keys} AND n * {GAP_EVERY} + {GAP_EVERY - 1} < {moves}")
        if engine == "mysql":
            conn.commit()
    finally:
        conn.close()


def _drop_appended(engine: str, moves: int) -> None:
    """Put the ledger back the way `load` left it (appended rows out, gaps open)."""
    d = D(engine)
    keys = ledger_keys(moves)
    run_sql(engine, f"DELETE FROM g_ledger WHERE movement_id >= {moves} OR "
                    f"({d.mod('movement_id', GAP_EVERY)} = {GAP_EVERY - 1} AND movement_id >= {keys})")


def _ledger_after(moves: int, *, appended: int, gaps_filled: bool) -> dict:
    keys = ledger_keys(moves)
    raw: dict = {}
    for m in range(moves + appended):
        if not gaps_filled and keys <= m < moves and m % GAP_EVERY == GAP_EVERY - 1:
            continue
        s, w, t, q, st = move_row(m, keys) if m >= keys else move_row(m, keys)
        if st == "posted":
            raw[(s, w)] = raw.get((s, w), 0) + SIGN[t] * q
        else:
            raw.setdefault((s, w), 0)
    return {k: max(0, v) for k, v in raw.items()}


def _push(tid: str, pid: str, rows: int, only_first: bool = False) -> dict:
    from backend.inventory_sync import ingest
    size = 50_000
    total = {"rows": 0, "status": "success"}
    snap = f"giant-{rows}"
    batches = (rows + size - 1) // size
    for b in range(1 if only_first else batches):
        part = [{"sku": f"S{g // WAREHOUSES}", "wh": f"W{g % WAREHOUSES}", "qty": (g * 7919) % 1000}
                for g in range(b * size, min(rows, (b + 1) * size))]
        r = ingest.ingest(tid, "giant", pid, batch_id=f"{snap}-{b}", rows=part, snapshot_id=snap,
                          last=(b == batches - 1))
        total["rows"] += len(part)
        total["last"] = r
        if r.get("status") not in (None, "accepted", "success", "replayed", "received", "applied"):
            total["status"] = str(r.get("status"))
    return total


# ── Loading ──────────────────────────────────────────────────────────────────

def load(engine: str, scenarios: list[str], rows: int, moves: int) -> None:
    if engine == "mongodb":
        t0 = time.monotonic()
        load_mongo(rows, moves, min(rows, 300_000))
        print(f"mongodb loaded in {time.monotonic() - t0:.0f}s")
        return
    if engine in ("postgresql", "mssql", "mysql"):
        admin = raw_conn(engine, None if engine != "postgresql" else "postgres")
        cur = admin.cursor()
        if engine == "postgresql":
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (SRC_DB,))
            if not cur.fetchone():
                cur.execute(f"CREATE DATABASE {SRC_DB}")
        elif engine == "mssql":
            cur.execute(f"IF DB_ID('{SRC_DB}') IS NULL CREATE DATABASE {SRC_DB}")
            cur.execute(f"ALTER DATABASE {SRC_DB} SET RECOVERY SIMPLE")
        else:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {SRC_DB}")
        admin.close()
    conn = raw_conn(engine, None if engine == "oracle" else SRC_DB)
    t0 = time.monotonic()
    _numbers(engine, conn)
    for s in scenarios:
        t1 = time.monotonic()
        print(f"loading {s} ...", flush=True)
        if s in ("snapshot", "views"):
            if s == "snapshot":
                load_snapshot(engine, conn, rows)
            else:
                load_views(engine, conn)
        elif s == "lots":
            load_lots(engine, conn, rows)
        elif s == "ledger":
            load_ledger(engine, conn, moves, partitioned=True)
        elif s == "wide":
            load_wide(engine, conn, min(rows, 100_000))
        elif s == "history":
            load_history(engine, conn, rows)
        elif s == "union":
            load_union(engine, conn, moves)
        if engine == "mysql":
            conn.commit()
        print(f"  {s} loaded in {time.monotonic() - t1:.0f}s", flush=True)
    conn.close()
    print(f"{engine} loaded in {time.monotonic() - t0:.0f}s")


def drop(engine: str) -> None:
    if engine == "mongodb":
        mongo_db().client.drop_database(SRC_DB)
        return
    if engine == "oracle":
        d = D(engine)
        conn = raw_conn(engine)
        cur = conn.cursor()
        for v in ("g_view2", "g_view1"):
            cur.execute(d.drop(v, view=True))
        for t in ("g_snap", "g_snap_copy", "g_lots", "g_ledger", "g_wide", "g_hist", "purchase_receipts",
                  "sales_lines", "stock_adjustments", "transfers", "g_nums", "g_digits"):
            cur.execute(d.drop(t))
        conn.close()
        return
    admin = raw_conn(engine, None if engine != "postgresql" else "postgres")
    cur = admin.cursor()
    if engine == "mssql":
        cur.execute(f"IF DB_ID('{SRC_DB}') IS NOT NULL BEGIN ALTER DATABASE {SRC_DB} SET SINGLE_USER WITH ROLLBACK "
                    f"IMMEDIATE; DROP DATABASE {SRC_DB}; END")
    elif engine == "postgresql":
        cur.execute(f"DROP DATABASE IF EXISTS {SRC_DB} WITH (FORCE)")
    else:
        cur.execute(f"DROP DATABASE IF EXISTS {SRC_DB}")
    admin.close()


# ── Chaos ────────────────────────────────────────────────────────────────────

def chaos(engine: str, cases: list[str], rows: int, out: str) -> None:
    """Each case ends in a handled, visible outcome (run status + code +
    health) and, on failure, stock exactly as it was."""
    from backend.db.connection import execute, query_one
    from backend.inventory_sync import engine as eng, profiles, scheduler
    rec = Recorder(out, engine)
    d = D(engine)
    container = CONTAINERS.get(engine)
    tid = new_tenant(f"chaos-{engine[:5]}")
    try:
        sid = make_source(tid, engine)
        # A private copy, so destructive cases never touch the shared table.
        run_sql(engine, d.drop("g_snap_copy"))
        if engine == "mssql":
            run_sql(engine, "SELECT * INTO g_snap_copy FROM g_snap")
        else:
            run_sql(engine, "CREATE TABLE g_snap_copy AS SELECT * FROM g_snap")
        if engine == "oracle":
            time.sleep(4)        # ORA-01466 on a table younger than the read-only snapshot
        p = make_profile(tid, sid, source_kind="table", source_object=table_name(engine, "g_snap_copy"),
                         mapping=snapshot_mapping(engine), **schema_kw(engine))
        first = step(rec, engine, "chaos", "baseline_full", lambda: do_run(tid, p["id"]),
                     lambda: expect_snapshot(rows), tid)
        if first["status"] != "success":
            return

        def failure_case(name: str, breaker: Callable[[], None], healer: Callable[[], None],
                         runner: Optional[Callable[[], dict]] = None) -> None:
            before = stock_fingerprint(tid)
            breaker()
            try:
                r = step(rec, engine, "chaos", name, runner or (lambda: do_run(tid, p["id"])))
            finally:
                healer()
            row = profiles.get(tid, p["id"])
            r2 = {"scenario": "chaos", "mode": f"{name}:outcome", "status": r["status"], "error": r["error"],
                  "health": row.get("health"), "stock_untouched": stock_fingerprint(tid) == before}
            rec.write(r2)
            execute("UPDATE inventory_sync_profiles SET health = 'healthy', health_reason = NULL WHERE id = %s",
                    (p["id"],))

        for case in cases:
            if case == "zero_rows":
                run_sql(engine, d.drop("g_snap_keep"))
                run_sql(engine, "SELECT * INTO g_snap_keep FROM g_snap_copy" if engine == "mssql"
                        else "CREATE TABLE g_snap_keep AS SELECT * FROM g_snap_copy")
                failure_case("zero_rows", lambda: run_sql(engine, "DELETE FROM g_snap_copy"),
                             lambda: run_sql(engine, "INSERT INTO g_snap_copy SELECT * FROM g_snap_keep"))
            elif case == "mass_delete":
                failure_case("delete_most_rows",
                             lambda: run_sql(engine, f"DELETE FROM g_snap_copy WHERE row_id >= {rows // 10}"),
                             lambda: run_sql(engine, f"INSERT INTO g_snap_copy SELECT * FROM g_snap WHERE row_id >= {rows // 10}"))
            elif case == "schema_drift":
                ren = {"postgresql": "ALTER TABLE g_snap_copy RENAME COLUMN on_hand TO qty_on_hand",
                       "mysql": "ALTER TABLE g_snap_copy RENAME COLUMN on_hand TO qty_on_hand",
                       "mssql": "EXEC sp_rename 'g_snap_copy.on_hand', 'qty_on_hand', 'COLUMN'",
                       "oracle": "ALTER TABLE g_snap_copy RENAME COLUMN on_hand TO qty_on_hand"}[engine]
                back = ren.replace("on_hand TO qty_on_hand", "qty_on_hand TO on_hand").replace(
                    "'g_snap_copy.on_hand', 'qty_on_hand'", "'g_snap_copy.qty_on_hand', 'on_hand'")
                failure_case("column_renamed", lambda: run_sql(engine, ren), lambda: run_sql(engine, back))
            elif case == "kill_source" and container:
                def killer():
                    time.sleep(3)
                    subprocess.run(["docker", "kill", container], capture_output=True)
                def heal():
                    subprocess.run(["docker", "start", container], capture_output=True)
                    _wait_up(engine)
                _bump_copy(engine, rows)
                th = threading.Thread(target=killer)
                failure_case("source_killed_mid_read", th.start, heal,
                             runner=lambda: do_run(tid, p["id"], override=True))
            elif case == "pause_source" and container:
                def pauser():
                    time.sleep(3)
                    subprocess.run(["docker", "pause", container], capture_output=True)
                    time.sleep(40)
                    subprocess.run(["docker", "unpause", container], capture_output=True)
                th = threading.Thread(target=pauser)
                _bump_copy(engine, rows)
                failure_case("network_partition_pause", th.start, lambda: (th.join(), _wait_up(engine)),
                             runner=lambda: do_run(tid, p["id"], override=True))
            elif case == "kill_worker":
                _kill_worker_case(rec, engine, tid, p["id"], rows)
        # After every case: a normal run converges to the source again.
        step(rec, engine, "chaos", "converges_after_chaos", lambda: do_run(tid, p["id"], override=True), None, tid)
    finally:
        try:
            run_sql(engine, d.drop("g_snap_copy"))
            run_sql(engine, d.drop("g_snap_keep"))
        except Exception:  # noqa: BLE001
            pass
        erase(tid)


def _bump_copy(engine: str, rows: int) -> None:
    d = D(engine)
    run_sql(engine, f"UPDATE g_snap_copy SET on_hand = on_hand + 1 WHERE {d.mod('row_id', 3)} = 0")


def _wait_up(engine: str, timeout: float = 240) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            scalar(engine, "SELECT 1 FROM g_nums WHERE n = 1")
            return
        except Exception:  # noqa: BLE001
            time.sleep(3)


def _kill_worker_case(rec: Recorder, engine: str, tid: str, pid: str, rows: int) -> None:
    """A worker process dies mid-APPLY; another worker resumes from the
    checkpoint and the result matches the source."""
    from backend.db.connection import execute, query_one
    from backend.inventory_sync import scheduler
    _bump_copy(engine, rows)
    execute("DELETE FROM inventory_stock WHERE tenant_id = %s", (tid,))   # a full re-create: a long apply
    child = subprocess.Popen([sys.executable, "-m", "backend.scripts.giant_sync", "_run_child",
                              "--tenant", tid, "--profile", pid], cwd=os.getcwd())
    killed_at = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1800:
        r = query_one("SELECT status, stage_complete, brake_passed, checkpoint FROM inventory_sync_runs "
                      "WHERE profile_id = %s ORDER BY started_at DESC LIMIT 1", (pid,))
        if r and r["status"] == "running" and r["brake_passed"] and r["checkpoint"]:
            child.kill()
            killed_at = r["checkpoint"]
            break
        if child.poll() is not None:
            break
        time.sleep(0.2)
    child.wait()
    rec.write({"scenario": "chaos", "mode": "worker_killed_mid_apply", "killed_at_checkpoint": killed_at})
    execute("UPDATE inventory_sync_profiles SET lease_until = NOW() - INTERVAL '1 second', "
            "next_run_at = NOW() - INTERVAL '1 second', enabled = TRUE WHERE id = %s", (pid,))

    def resume():
        claimed = scheduler.claim_due(owner="giant-b", profile_id=pid)
        if not claimed:
            return {"status": "not_claimed"}
        scheduler.run_claimed(claimed, "giant-b", "schedule")
        return query_one("SELECT * FROM inventory_sync_runs WHERE profile_id = %s ORDER BY started_at DESC "
                         "LIMIT 1", (pid,)) or {}
    exp_copy = lambda: expect_snapshot(rows, lambda g: 1 if g % 3 == 0 else 0)  # noqa: E731
    step(rec, engine, "chaos", "resumed_by_another_worker", resume, None, tid)
    rec.write({"scenario": "chaos", "mode": "resumed:oracle", "oracle": _copy_oracle(engine, tid)})


def _copy_oracle(engine: str, tid: str) -> dict:
    """The expected stock of g_snap_copy, read back from the source with a
    plain SELECT (independent of the sync's read path)."""
    conn = mongo_db() if engine == "mongodb" else raw_conn(engine)
    cur = conn.cursor()
    cur.execute("SELECT item_code, warehouse, on_hand FROM g_snap_copy")
    exp = {(r[0], r[1]): float(r[2]) for r in cur.fetchall()}
    conn.close()
    return compare(tid, exp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["load", "measure", "chaos", "drop", "_run_child"])
    ap.add_argument("--engine", default="postgresql")
    ap.add_argument("--scenarios", default="snapshot,lots,ledger,wide,history,union,views")
    ap.add_argument("--cases", default="zero_rows,mass_delete,schema_drift,kill_worker,kill_source,pause_source")
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--ledger", type=int, default=10_000_000)
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "/tmp"), "giant_sync_results.jsonl"))
    ap.add_argument("--tenant")
    ap.add_argument("--profile")
    a = ap.parse_args()
    if a.action == "load":
        load(a.engine, a.scenarios.split(","), a.rows, a.ledger)
        return
    if a.action == "drop":
        drop(a.engine)
        return
    boot_app()
    if a.action == "_run_child":
        do_run(a.tenant, a.profile, override=True)
        return
    if a.action == "measure":
        measure(a.engine, a.scenarios.split(","), a.rows, a.ledger, a.out)
    else:
        chaos(a.engine, a.cases.split(","), a.rows, a.out)


if __name__ == "__main__":
    main()
