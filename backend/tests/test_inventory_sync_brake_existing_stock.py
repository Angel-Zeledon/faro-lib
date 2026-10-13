"""The quantity-jump brake judges a run against the stock that is THERE.

It used to compare only with keys this profile had written before, so the
first run of a new sync pointed at the wrong table (boxes instead of units, a
test copy) overwrote good stock that a hand entry, an upload or another sync
had written, and passed. Every assertion about stock is a direct query.
"""

from __future__ import annotations

from pathlib import Path

from backend.db.connection import execute, query, query_one
from backend.inventory import service as inv_svc
from backend.inventory_sync import engine, profiles
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    _booted, _profile, _put, _run, _source, _stock, src_db, src_table,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE_TS = ROOT / "Frontend" / "src" / "i18n" / "translations.ts"

N = 30


def _hand_stock(tid: str, rows: list[tuple[str, str, float]]) -> None:
    """Stock typed by a person (no sync key anywhere)."""
    for sku, wh, qty in rows:
        inv_svc._ensure_warehouse(tid, wh)
        execute("INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock, updated_at) "
                "VALUES (%s, %s, %s, %s, NOW())", (tid, sku, wh, qty))


def _frozen(tid: str) -> dict:
    return {k: (float(v["current_stock"]), v["updated_at"]) for k, v in _stock(tid).items()}


def _keys(profile_id: str) -> int:
    return query_one("SELECT COUNT(*) AS n FROM inventory_sync_keys WHERE profile_id = %s",
                     (profile_id,))["n"]


def _previewed(tid: str, p: dict) -> dict:
    return engine.dry_run(tid, profiles.get(tid, p["id"]))


class TestFirstRunAgainstExistingStock:

    def test_first_run_over_hand_entered_stock_brakes_and_changes_nothing(self, test_tenant, src_db,
                                                                          src_table):
        tid = test_tenant["id"]
        _hand_stock(tid, [(f"S{i}", "Norte", 10) for i in range(N)])
        before = _frozen(tid)
        _put(src_db, src_table, [(f"S{i}", "Norte", 1000) for i in range(N)])   # boxes -> units
        p = _profile(tid, _source(tid), src_table)

        run = _run(tid, p["id"])

        assert run["status"] == "braked", run
        assert run["error_code"] == "brake_mass_quantity_change"
        assert run["error_params"]["matched"] == N
        assert run["error_params"]["jumped"] == N
        assert _frozen(tid) == before                       # not one quantity moved
        assert _keys(p["id"]) == 0                           # and nothing was claimed
        assert profiles.get(tid, p["id"])["health"] == "paused"

        # Idempotent: the same source brakes the same way and still writes nothing.
        again = _run(tid, p["id"])
        assert again["status"] == "braked"
        assert (again["error_params"]["matched"], again["error_params"]["jumped"]) == (N, N)
        assert _frozen(tid) == before

    def test_the_human_override_applies_it_once(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _hand_stock(tid, [(f"S{i}", "Norte", 10) for i in range(N)])
        _put(src_db, src_table, [(f"S{i}", "Norte", 1000) for i in range(N)])
        p = _profile(tid, _source(tid), src_table)
        assert _run(tid, p["id"])["status"] == "braked"

        run = _run(tid, p["id"], override_brake=True)

        assert run["status"] == "success", run
        assert run["brake_overridden"] is True
        stock = _stock(tid)
        assert {float(stock[(f"S{i}", "Norte")]["current_stock"]) for i in range(N)} == {1000.0}
        # Once: the next run is judged again (and now passes: it matches what is there).
        assert _run(tid, p["id"])["status"] == "success"

    def test_stock_written_by_a_sync_this_one_outranks_is_judged(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [(f"S{i}", "Norte", 10) for i in range(N)])
        first = _profile(tid, _source(tid), src_table, name="Good ERP")
        assert _run(tid, first["id"])["status"] == "success"
        before = _frozen(tid)

        other = src_table + "_b"
        with src_db.cursor() as cur:
            cur.execute(f"CREATE TABLE {other} (LIKE {src_table} INCLUDING DEFAULTS)")
        try:
            _put(src_db, other, [(f"S{i}", "Norte", 1000) for i in range(N)])
            # A lower priority number wins the keys, so this run WOULD overwrite them.
            second = _profile(tid, _source(tid), other, name="Wrong table", priority=10)
            run = _run(tid, second["id"])
            assert run["status"] == "braked", run
            assert run["error_code"] == "brake_mass_quantity_change"
            assert _frozen(tid) == before
        finally:
            with src_db.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {other}")

    def test_keys_another_sync_owns_are_not_counted(self, test_tenant, src_db, src_table):
        """Rows the run will not write (another profile has precedence) cannot
        make it brake: they are skipped and counted as owned elsewhere."""
        tid = test_tenant["id"]
        _put(src_db, src_table, [(f"S{i}", "Norte", 10) for i in range(N)])
        first = _profile(tid, _source(tid), src_table, name="Good ERP")
        assert _run(tid, first["id"])["status"] == "success"
        before = _frozen(tid)
        other = src_table + "_c"
        with src_db.cursor() as cur:
            cur.execute(f"CREATE TABLE {other} (LIKE {src_table} INCLUDING DEFAULTS)")
        try:
            _put(src_db, other, [(f"S{i}", "Norte", 1000) for i in range(N)])
            second = _profile(tid, _source(tid), other, name="Second")   # same priority, newer
            run = _run(tid, second["id"])
            assert run["status"] == "success", run
            assert run["owned_elsewhere"] == N
            assert _frozen(tid) == before
        finally:
            with src_db.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {other}")


class TestNoFalsePositives:

    def test_first_run_on_an_empty_inventory_passes(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [(f"S{i}", "Norte", 1000) for i in range(N)])
        p = _profile(tid, _source(tid), src_table)
        run = _run(tid, p["id"])
        assert run["status"] == "success", run
        assert run["created"] == N
        assert len(_stock(tid)) == N

    def test_first_run_that_overlaps_nothing_passes(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _hand_stock(tid, [(f"OLD{i}", "Norte", 10) for i in range(N)])
        _put(src_db, src_table, [(f"NEW{i}", "Norte", 1000) for i in range(N)])
        p = _profile(tid, _source(tid), src_table)
        run = _run(tid, p["id"])
        assert run["status"] == "success", run
        assert run["created"] == N
        assert float(_stock(tid)[("OLD0", "Norte")]["current_stock"]) == 10.0

    def test_ordinary_day_to_day_changes_pass(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _hand_stock(tid, [(f"S{i}", "Norte", 10 + i) for i in range(N)])
        # Sales and receptions: up and down, a few to zero, none by 10x.
        _put(src_db, src_table, [(f"S{i}", "Norte", (0 if i % 7 == 0 else (10 + i) * (1.8 if i % 2 else 0.4)))
                                 for i in range(N)])
        p = _profile(tid, _source(tid), src_table)
        run = _run(tid, p["id"])
        assert run["status"] == "success", run
        assert run["updated"] == N


class TestPreviewSaysItFirst:

    def test_dry_run_warns_before_enabling(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _hand_stock(tid, [(f"S{i}", "Norte", 10) for i in range(N)])
        before = _frozen(tid)
        _put(src_db, src_table, [(f"S{i}", "Norte", 1000) for i in range(N)])
        p = _profile(tid, _source(tid), src_table)

        out = _previewed(tid, p)

        warn = out["brake_warning"]
        assert warn["code"] == "brake_mass_quantity_change"
        assert (warn["params"]["matched"], warn["params"]["jumped"]) == (N, N)
        assert _frozen(tid) == before

    def test_dry_run_is_quiet_for_ordinary_changes(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _hand_stock(tid, [(f"S{i}", "Norte", 10) for i in range(N)])
        _put(src_db, src_table, [(f"S{i}", "Norte", 12) for i in range(N)])
        p = _profile(tid, _source(tid), src_table)
        assert _previewed(tid, p)["brake_warning"] is None

    def test_preview_and_run_agree(self, test_tenant, src_db, src_table):
        """The preview's verdict is the run's verdict on the same data."""
        tid = test_tenant["id"]
        _hand_stock(tid, [(f"S{i}", "Norte", 10) for i in range(N)])
        _put(src_db, src_table, [(f"S{i}", "Norte", 1000 if i < 10 else 11) for i in range(N)])
        p = _profile(tid, _source(tid), src_table, brake_max_jump_pct=20)
        warn = _previewed(tid, p)["brake_warning"]
        run = _run(tid, p["id"])
        assert warn is not None and run["status"] == "braked"
        assert warn["params"]["jumped"] == run["error_params"]["jumped"] == 10
        assert warn["params"]["matched"] == run["error_params"]["matched"] == N


def test_the_copy_counts_overlap_and_jumps_and_names_the_override():
    text = CATALOGUE_TS.read_text(encoding="utf-8")
    for line in [ln for ln in text.splitlines() if "'isync.code.brake_mass_quantity_change'" in ln]:
        assert "{matched}" in line and "{jumped}" in line, line
    remedies = [ln for ln in text.splitlines() if "'isync.remedy.brake_mass_quantity_change'" in ln]
    assert len(remedies) == 2
    es, en = remedies
    assert "Aplicar de todos modos, una vez" in es, es
    assert "Apply anyway, once" in en, en
