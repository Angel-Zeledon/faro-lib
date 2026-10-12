"""Stock models: aggregate (lots summed) and movement ledger (stock = SUM of
signed movements), checked against a brute-force Python oracle.

The "customer database" is the PostgreSQL test source of test_inventory_sync.
Every assertion about stock reads inventory_stock / the ledger tables.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one
from backend.inventory_sync import engine, ledger, profiles
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    _booted, _run, _source, _stock, src_db,
)

TYPES = {"REC": 1, "SALE": -1, "RET": 1, "ADJ": 1, "TIN": 1, "TOUT": -1, "VOID": 0}


@pytest.fixture
def mov_table(src_db):
    name = f"mov_{uuid4().hex[:8]}"
    with src_db.cursor() as cur:
        cur.execute(f"CREATE TABLE {name} (id BIGINT PRIMARY KEY, item TEXT, wh TEXT, qty NUMERIC, "
                    "mtype TEXT, status TEXT DEFAULT 'P', posted_at TIMESTAMP DEFAULT NOW(), factor NUMERIC DEFAULT 1)")
    yield name
    with src_db.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {name}")


def _insert(src_db, table, rows):
    with src_db.cursor() as cur:
        for r in rows:
            cur.execute(f"INSERT INTO {table} (id, item, wh, qty, mtype, status, posted_at, factor) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (r["id"], r["item"], r["wh"], r["qty"], r["mtype"], r.get("status", "P"),
                         r.get("posted_at", datetime(2026, 1, 1)), r.get("factor", 1)))


def _oracle(rows, factor=False):
    bal = defaultdict(float)
    for r in rows:
        if r.get("status", "P") != "P":
            continue
        q = float(r["qty"]) * TYPES[r["mtype"]] * (float(r.get("factor", 1)) if factor else 1)
        bal[(r["item"], r["wh"])] += q
    return dict(bal)


def _ledger_profile(tid, sid, table, **kw):
    cfg = {"sign": {"mode": "type_map", "type_column": "mtype", "type_map": TYPES},
           "id_column": "id", "status_column": "status", "posted_values": ["P"]}
    cfg.update(kw.pop("cfg", {}))
    body = {"name": "ledger", "data_source_id": sid, "source_kind": "table", "source_schema": "public",
            "source_object": table, "stock_model": "ledger", "model_config": cfg,
            "mapping": {"sku": "item", "warehouse": "wh", "current_stock": "qty"},
            "allow_new_warehouses": True, "negative_policy": "zero",
            "brake_max_vanished_pct": 100, "brake_min_row_ratio": 0, "brake_max_jump_pct": 100}
    body.update(kw)
    return profiles.create(tid, "usr_test", body)


def _balances(pid):
    return {(r["sku"], r["warehouse"]): float(r["balance"]) for r in query(
        "SELECT sku, warehouse, balance FROM inventory_sync_ledger_balances WHERE profile_id = %s "
        "AND pending_run IS NULL", (pid,))}


def _close(a: dict, b: dict) -> bool:
    keys = set(a) | set(b)
    return all(abs(a.get(k, 0.0) - b.get(k, 0.0)) < 1e-6 for k in keys)


def _random_movements(rnd, start_id, n, skus=8, whs=3):
    out = []
    for i in range(n):
        out.append({"id": start_id + i, "item": f"P{rnd.randrange(skus)}", "wh": f"W{rnd.randrange(whs)}",
                    "qty": rnd.choice([1, 2, 3, 5, 10, 12.5, 40]), "mtype": rnd.choice(list(TYPES)),
                    "status": rnd.choice(["P", "P", "P", "D", "X"])})
    return out


class TestLedger:

    def test_full_run_matches_the_oracle_and_stock_clamps_negatives(self, test_tenant, src_db, mov_table):
        tid = test_tenant["id"]
        rows = _random_movements(random.Random(1), 1, 300)
        _insert(src_db, mov_table, rows)
        p = _ledger_profile(tid, _source(tid), mov_table)
        run = _run(tid, p["id"])
        assert run["status"] == "success", run
        expected = _oracle(rows)
        assert _close(_balances(p["id"]), expected)
        stock = _stock(tid)
        for k, v in expected.items():
            assert abs(float(stock[k]["current_stock"]) - max(0.0, v)) < 1e-6
        assert profiles.get(tid, p["id"])["cursor_value"]["value"] == "300"

    @pytest.mark.parametrize("seed", [7, 11, 23])
    def test_incremental_converges_under_any_batching_and_replays(self, test_tenant, src_db, mov_table, seed):
        """Property: whatever the order the movements arrive in batches, and
        however often the cursor is rewound (replay), the balance converges."""
        tid = test_tenant["id"]
        rnd = random.Random(seed)
        all_rows = _random_movements(rnd, 1, 400)
        p = _ledger_profile(tid, _source(tid), mov_table)
        _insert(src_db, mov_table, all_rows[:50])
        _run(tid, p["id"])                                         # first: full
        inserted = 50
        while inserted < len(all_rows):
            step = rnd.randint(1, 80)
            _insert(src_db, mov_table, all_rows[inserted:inserted + step])
            inserted += step
            if rnd.random() < 0.3:
                # replay: rewind the cursor; the applied-ids store absorbs it
                execute("UPDATE inventory_sync_profiles SET cursor_value = %s WHERE id = %s",
                        ('{"type": "number", "value": "%d"}' % max(1, inserted - 120), p["id"]))
            run = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "manual", kind="incremental"))
            assert run["status"] == "success", run
        assert _close(_balances(p["id"]), _oracle(all_rows))

    def test_late_commit_is_caught_by_reconcile_and_healed(self, test_tenant, src_db, mov_table):
        tid = test_tenant["id"]
        rows = [{"id": i, "item": "A", "wh": "W", "qty": 10, "mtype": "REC"} for i in range(10, 20)]
        _insert(src_db, mov_table, rows)
        p = _ledger_profile(tid, _source(tid), mov_table)
        _run(tid, p["id"])
        late = {"id": 5, "item": "A", "wh": "W", "qty": 30, "mtype": "SALE"}   # id BELOW the cursor
        _insert(src_db, mov_table, [late])
        inc = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "manual", kind="incremental"))
        assert inc["status"] == "success" and _balances(p["id"])[("A", "W")] == 100   # missed (by design)
        rec = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "reconcile", kind="reconcile"))
        assert rec["status"] == "success" and rec["drift_count"] == 1
        assert _balances(p["id"])[("A", "W")] == 70
        assert float(_stock(tid)[("A", "W")]["current_stock"]) == 70
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s AND action = 'inventory_sync.ledger_drift'",
                     (tid,))

    def test_drift_with_brake_policy_changes_nothing(self, test_tenant, src_db, mov_table):
        tid = test_tenant["id"]
        _insert(src_db, mov_table, [{"id": i, "item": "A", "wh": "W", "qty": 10, "mtype": "REC"} for i in range(1, 6)])
        p = _ledger_profile(tid, _source(tid), mov_table, cfg={"drift_policy": "brake"})
        _run(tid, p["id"])
        with src_db.cursor() as cur:                                   # a void edits an old movement
            cur.execute(f"UPDATE {mov_table} SET status = 'V' WHERE id = 1")
        rec = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "reconcile", kind="reconcile"))
        assert rec["status"] == "braked" and rec["error_code"] == "brake_ledger_drift"
        assert _balances(p["id"])[("A", "W")] == 50 and float(_stock(tid)[("A", "W")]["current_stock"]) == 50

    def test_transfers_uom_and_reversals(self, test_tenant, src_db, mov_table):
        tid = test_tenant["id"]
        rows = [
            {"id": 1, "item": "A", "wh": "NORTH", "qty": 10, "mtype": "REC", "factor": 12},   # 10 boxes of 12
            {"id": 2, "item": "A", "wh": "NORTH", "qty": 24, "mtype": "TOUT"},
            {"id": 3, "item": "A", "wh": "SOUTH", "qty": 24, "mtype": "TIN"},
            {"id": 4, "item": "A", "wh": "NORTH", "qty": 5, "mtype": "SALE"},
            {"id": 5, "item": "A", "wh": "NORTH", "qty": -5, "mtype": "SALE"},                # storno row
        ]
        _insert(src_db, mov_table, rows)
        p = _ledger_profile(tid, _source(tid), mov_table, cfg={"factor_column": "factor"})
        assert _run(tid, p["id"])["status"] == "success"
        assert _close(_balances(p["id"]), _oracle(rows, factor=True))
        assert _balances(p["id"])[("A", "NORTH")] == 120 - 24 and _balances(p["id"])[("A", "SOUTH")] == 24

    def test_failed_incremental_rolls_everything_back(self, test_tenant, src_db, mov_table):
        tid = test_tenant["id"]
        _insert(src_db, mov_table, [{"id": 1, "item": "A", "wh": "W", "qty": 10, "mtype": "REC"}])
        p = _ledger_profile(tid, _source(tid), mov_table)
        _run(tid, p["id"])
        cursor = profiles.get(tid, p["id"])["cursor_value"]
        _insert(src_db, mov_table, [{"id": 2, "item": "A", "wh": "W", "qty": 4, "mtype": "SALE"}])
        # The apply step fails (our database refuses a statement) after the
        # movements were read and their balances computed as pending.
        from backend.inventory_sync import apply as applier
        original = applier.apply_batch

        def boom(*a, **kw):
            raise RuntimeError("disk full")
        applier.apply_batch = boom
        try:
            run = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "manual", kind="incremental"))
        finally:
            applier.apply_batch = original
        assert run["status"] == "failed"
        assert profiles.get(tid, p["id"])["cursor_value"] == cursor
        assert _balances(p["id"])[("A", "W")] == 10
        assert query("SELECT 1 FROM inventory_sync_applied_movements WHERE profile_id = %s", (p["id"],)) == []
        assert query("SELECT 1 FROM inventory_sync_ledger_balances WHERE profile_id = %s AND pending_run IS NOT NULL",
                     (p["id"],)) == []
        assert float(_stock(tid)[("A", "W")]["current_stock"]) == 10
        # And the next run picks the movement up exactly once.
        run = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "manual", kind="incremental"))
        assert run["status"] == "success" and _balances(p["id"])[("A", "W")] == 6

    def test_dry_run_shows_the_arithmetic(self, test_tenant, src_db, mov_table):
        tid = test_tenant["id"]
        rows = [{"id": 1, "item": "A", "wh": "W", "qty": 40, "mtype": "REC"},
                {"id": 2, "item": "A", "wh": "W", "qty": 12, "mtype": "SALE"},
                {"id": 3, "item": "A", "wh": "W", "qty": 3, "mtype": "SALE"},
                {"id": 4, "item": "A", "wh": "W", "qty": 9, "mtype": "SALE", "status": "D"}]
        _insert(src_db, mov_table, rows)
        p = _ledger_profile(tid, _source(tid), mov_table)
        dry = engine.dry_run(tid, profiles.get(tid, p["id"]))
        assert dry["would_create"] == 1 and _stock(tid) == {}
        ex = dry["examples"][0]
        assert ex["sku"] == "A" and ex["total"] == 25
        assert [m["signed"] for m in ex["movements"]] == [40, -12, -3, 0]
        assert ex["movements"][3]["counted"] is False

    def test_reconcile_view(self, test_tenant, src_db, mov_table):
        tid = test_tenant["id"]
        _insert(src_db, mov_table, [{"id": 1, "item": "A", "wh": "W", "qty": 40, "mtype": "REC"}])
        p = _ledger_profile(tid, _source(tid), mov_table)
        _run(tid, p["id"])
        _insert(src_db, mov_table, [{"id": 2, "item": "A", "wh": "W", "qty": 15, "mtype": "SALE"}])
        view = ledger.reconcile_view(tid, profiles.get(tid, p["id"]), "A", "W")
        assert view["stockai"] == 40 and view["running_balance"] == 40
        assert view["source_sum"] == 25 and view["difference"] == -15

    def test_ledger_config_validation(self, test_tenant, src_db, mov_table):
        from backend.errors import AppError
        tid = test_tenant["id"]
        sid = _source(tid)
        for bad in ({"sign": {"mode": "type_map", "type_column": "mtype", "type_map": {"REC": 2}}, "id_column": "id"},
                    {"sign": {"mode": "nope"}, "id_column": "id"},
                    {"sign": {"mode": "signed"}},
                    {"sign": {"mode": "signed"}, "id_column": "id", "status_column": "status"}):
            with pytest.raises(AppError) as exc:
                profiles.create(tid, "usr_test", {
                    "name": "x", "data_source_id": sid, "source_kind": "table", "source_object": mov_table,
                    "stock_model": "ledger", "model_config": bad,
                    "mapping": {"sku": "item", "current_stock": "qty"}})
            assert exc.value.code == "inventory_sync_model_invalid"


class TestAggregate:

    @pytest.fixture
    def lots(self, src_db):
        name = f"lots_{uuid4().hex[:8]}"
        with src_db.cursor() as cur:
            cur.execute(f"CREATE TABLE {name} (item TEXT, wh TEXT, lot TEXT, qty NUMERIC, "
                        "updated_at TIMESTAMP DEFAULT '2026-01-01')")
        yield name
        with src_db.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {name}")

    def _prof(self, tid, sid, table, **kw):
        body = {"name": "lots", "data_source_id": sid, "source_kind": "table", "source_schema": "public",
                "source_object": table, "stock_model": "aggregate", "model_config": {"aggregate": "sum"},
                "mapping": {"sku": "item", "warehouse": "wh", "current_stock": "qty"},
                "allow_new_warehouses": True}
        body.update(kw)
        return profiles.create(tid, "usr_test", body)

    def test_lots_are_summed_by_the_source(self, test_tenant, src_db, lots):
        tid = test_tenant["id"]
        with src_db.cursor() as cur:
            cur.execute(f"INSERT INTO {lots} (item, wh, lot, qty) VALUES "
                        "('A','W','L1',10),('A','W','L2',5),('A','X','L3',2),('B','W','L4',7)")
        p = self._prof(tid, _source(tid), lots)
        dry = engine.dry_run(tid, profiles.get(tid, p["id"]))
        ex = {(e["sku"], e["warehouse"]): e for e in dry["examples"]}
        assert ex[("A", "W")]["total"] == 15 and ex[("A", "W")]["row_count"] == 2
        run = _run(tid, p["id"])
        assert run["status"] == "success" and run["rows_read"] == 3      # grouped at the source
        stock = _stock(tid)
        assert {k: float(v["current_stock"]) for k, v in stock.items()} == {
            ("A", "W"): 15, ("A", "X"): 2, ("B", "W"): 7}

    def test_incremental_aggregate_resums_every_lot_of_a_changed_key(self, test_tenant, src_db, lots):
        tid = test_tenant["id"]
        with src_db.cursor() as cur:
            cur.execute(f"INSERT INTO {lots} (item, wh, lot, qty) VALUES ('A','W','L1',10),('A','W','L2',5),"
                        "('B','W','L3',7)")
        p = self._prof(tid, _source(tid), lots, mode="incremental", watermark_column="updated_at",
                       overlap_minutes=0)
        _run(tid, p["id"])
        with src_db.cursor() as cur:   # one lot of A changes; B untouched
            cur.execute(f"UPDATE {lots} SET qty = 8, updated_at = '2026-02-01' WHERE lot = 'L2'")
        run = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "manual"))
        # Key A (changed) is re-summed over ALL its lots; B (boundary row,
        # >= watermark) is re-read and left unchanged.
        assert run["status"] == "success" and run["updated"] == 1 and run["created"] == 0
        assert float(_stock(tid)[("A", "W")]["current_stock"]) == 18     # 10 + 8, not 8
        assert float(_stock(tid)[("B", "W")]["current_stock"]) == 7

    def test_min_and_max(self, test_tenant, src_db, lots):
        tid = test_tenant["id"]
        with src_db.cursor() as cur:
            cur.execute(f"INSERT INTO {lots} (item, wh, lot, qty) VALUES ('A','W','L1',10),('A','W','L2',5)")
        p = self._prof(tid, _source(tid), lots, model_config={"aggregate": "min"})
        _run(tid, p["id"])
        assert float(_stock(tid)[("A", "W")]["current_stock"]) == 5

    def test_filters_are_refused_for_grouped_models(self, test_tenant, src_db, lots):
        from backend.errors import AppError
        tid = test_tenant["id"]
        with pytest.raises(AppError) as exc:
            self._prof(tid, _source(tid), lots, filters=[{"column": "lot", "op": "eq", "value": "L1"}])
        assert exc.value.params["reason"] == "filters_need_snapshot"
