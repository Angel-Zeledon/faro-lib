"""Measure an inventory sync against a large PostgreSQL source.

    python -m backend.scripts.bench_inventory_sync --rows 1000000

Builds (once) a source table `bench_stock_<rows>` in database
`stockai_sync_bench` on the server named by STOCKAI_BENCH_PG
(host:port:user:password, default 127.0.0.1:5544:postgres:postgres), creates a
throwaway tenant with no ceilings, and times three runs on the same profile:

1. full     — first sync, every row created;
2. nochange — the same source again, every row unchanged;
3. delta    — 1% of the quantities changed (incremental-sized work).

For each it prints wall time, rows/s and the process's peak working set (the
memory high-water mark, read from the OS). The tenant is erased at the end.
Development tool: never point it at a customer's database.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time


def peak_rss_mb() -> float:
    if sys.platform == "win32":
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
        psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.PeakWorkingSetSize / 1e6
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--warehouses", type=int, default=20)
    args = ap.parse_args()

    import psycopg2
    host, port, user, password = os.environ.get(
        "STOCKAI_BENCH_PG", "127.0.0.1:5544:postgres:postgres").split(":", 3)
    admin = psycopg2.connect(host=host, port=int(port), user=user, password=password, dbname="postgres")
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname='stockai_sync_bench'")
    if not cur.fetchone():
        cur.execute("CREATE DATABASE stockai_sync_bench")
    admin.close()
    src = psycopg2.connect(host=host, port=int(port), user=user, password=password,
                           dbname="stockai_sync_bench")
    src.autocommit = True
    table = f"bench_stock_{args.rows}"
    cur = src.cursor()
    cur.execute("SELECT to_regclass(%s)", (table,))
    if cur.fetchone()[0] is None:
        t0 = time.monotonic()
        cur.execute(f"CREATE TABLE {table} (item_code TEXT, warehouse TEXT, on_hand NUMERIC, "
                    "unit_cost NUMERIC, updated_at TIMESTAMP DEFAULT NOW())")
        cur.execute(f"INSERT INTO {table} (item_code, warehouse, on_hand, unit_cost) "
                    f"SELECT 'SKU-' || (g / {args.warehouses}), 'WH-' || (g % {args.warehouses}), "
                    "(g % 997)::numeric, ((g % 89) + 1) / 10.0 "
                    f"FROM generate_series(0, {args.rows - 1}) g")
        print(f"built {table} in {time.monotonic() - t0:.1f}s")

    from backend.config import settings
    from backend.db.connection import execute, init_pool, query_one
    init_pool(settings.database_url)
    from backend.db.migrations import run_all
    run_all()
    settings.testing_mode = True              # no plan ceilings for the bench tenant
    settings.sql_sources_allow_private_hosts = True
    settings.inventory_sync_max_rows_per_run = max(settings.inventory_sync_max_rows_per_run, args.rows)
    from backend.datasources.service import create_sql_source
    from backend.inventory_sync import engine, profiles
    from backend.tenants.service import create_tenant

    tenant = create_tenant(f"bench-{int(time.time())}")
    tid = tenant["id"]
    try:
        ds = create_sql_source(tid, "bench", "bench", host, int(port), "stockai_sync_bench", user,
                               password, "postgresql", ssl_mode="disable")
        execute("UPDATE datasets SET connection_status='connected' WHERE id=%s", (ds["id"],))
        p = profiles.create(tid, "bench", {
            "name": "bench", "data_source_id": ds["id"], "source_kind": "table",
            "source_schema": "public", "source_object": table,
            "mapping": {"sku": "item_code", "warehouse": "warehouse", "current_stock": "on_hand",
                        "unit_cost": "unit_cost"},
            "allow_new_warehouses": True, "brake_max_vanished_pct": 100, "brake_min_row_ratio": 0})

        def timed(label: str) -> dict:
            t0 = time.monotonic()
            run = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "manual"),
                                     override_brake=label == "delta")
            dt = time.monotonic() - t0
            print(f"{label:9s} status={run['status']:8s} read={run['rows_read']:>9} "
                  f"created={run['created']:>9} updated={run['updated']:>8} "
                  f"unchanged={run['unchanged']:>9} time={dt:8.1f}s "
                  f"rate={run['rows_read'] / dt if dt else 0:>9.0f} rows/s "
                  f"peak_rss={peak_rss_mb():7.0f} MB error={run.get('error_code')}")
            return run

        timed("full")
        timed("nochange")
        cur.execute(f"UPDATE {table} SET on_hand = on_hand + 1, updated_at = NOW() "
                    f"WHERE (hashtext(item_code || warehouse) % 100) = 0")
        timed("delta")
        cur.execute(f"UPDATE {table} SET on_hand = on_hand - 1 "
                    f"WHERE (hashtext(item_code || warehouse) % 100) = 0")
    finally:
        from backend.tenants.data_export import delete_tenant
        delete_tenant(tid)
        print("bench tenant erased")


if __name__ == "__main__":
    main()
