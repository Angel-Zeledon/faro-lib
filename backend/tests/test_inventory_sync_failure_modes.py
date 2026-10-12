"""The failure-mode catalogue, as a table of tests.

Each row is one way a sync can go wrong and the VISIBLE outcome it must have:
the run's status and code, the profile's health, whether stock was left
untouched, whether the scheduler retries on its own, and that the screen has
a remedy sentence for it (`isync.remedy.<code>` or its category fallback, in
both languages). Failure modes that are not handled in code are listed in
`failures.LIMITS` with the reason; the last tests keep that list honest.

The "customer database" is the PostgreSQL test source (test_inventory_sync).
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.errors import AppError
from backend.inventory_sync import engine, failures, profiles, scheduler
from backend.tests.test_inventory_sync import (  # noqa: F401 - fixtures
    MAPPING, _admin_params, _booted, _profile, _put, _run, _source, _stock, src_db, src_table,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE_TS = ROOT / "Frontend" / "src" / "i18n" / "translations.ts"


def _snapshot(tid):
    return {k: (float(v["current_stock"]), v["updated_at"]) for k, v in _stock(tid).items()}


def _seed(tid, src_db, src_table, rows=None, **kw):
    _put(src_db, src_table, rows or [("A1", "Norte", 10), ("B2", "Norte", 5)])
    p = _profile(tid, _source(tid), src_table, **kw)
    first = _run(tid, p["id"])
    assert first["status"] == "success", first
    return profiles.get(tid, p["id"])


def _expect(tid, p, run, *, status, code, health, untouched_from=None):
    assert run["status"] == status, run
    assert run["error_code"] == code, run
    row = profiles.get(tid, p["id"])
    assert row["health"] == health, (row["health"], row["health_reason"])
    if untouched_from is not None:
        assert _snapshot(tid) == untouched_from
    outcome = failures.outcome_of(code)
    assert outcome.health == health
    return outcome


def _broken_source(tid, **fields):
    """A data source whose connection fields are wrong in a given way."""
    from backend.datasources.service import create_sql_source
    p = _admin_params("stockai_sync_src")
    args = dict(host=p["host"], port=p["port"], database="stockai_sync_src", username=p["user"],
                password=p["password"])
    args.update(fields)
    src = create_sql_source(tid, "usr_test", f"broken-{uuid4().hex[:4]}", args["host"], args["port"],
                            args["database"], args["username"], args["password"], "postgresql",
                            ssl_mode="disable", connect_timeout_s=fields.get("connect_timeout_s", 3))
    execute("UPDATE datasets SET connection_status = 'connected' WHERE id = %s", (src["id"],))
    return src["id"]


def _open_source_transactions(src_db) -> int:
    with src_db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state LIKE 'idle in transaction%%' AND pid <> pg_backend_pid()")
        return cur.fetchone()[0]


def _repoint(tid, p, source_id):
    """Point an existing profile at another data source (keeps its keys and stock)."""
    execute("UPDATE inventory_sync_profiles SET data_source_id = %s WHERE id = %s", (source_id, p["id"]))


# ── Connection ────────────────────────────────────────────────────────────────

class TestConnection:

    def test_dns_failure_blocks_and_is_not_retried(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        _repoint(tid, p, _broken_source(tid, host="no-such-host.invalid"))
        o = _expect(tid, p, _run(tid, p["id"]), status="failed", code="data_source_dns_failed",
                    health="blocked", untouched_from=before)
        assert o.retries is False

    def test_ssrf_private_address_refused(self, test_tenant, src_db, src_table, monkeypatch):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        _repoint(tid, p, _broken_source(tid, host="localhost"))
        monkeypatch.setattr(settings, "sql_sources_allow_private_hosts", False)
        _expect(tid, p, _run(tid, p["id"]), status="failed", code="data_source_host_not_allowed",
                health="blocked", untouched_from=before)

    def test_connection_refused_degrades_and_retries(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        _repoint(tid, p, _broken_source(tid, port=1))
        o = _expect(tid, p, _run(tid, p["id"]), status="failed", code="data_source_host_unreachable",
                    health="degraded", untouched_from=before)
        assert o.retries is True and "data_source_host_unreachable" in failures.NETWORK_CODES

    def test_wrong_password_blocks_without_retry_and_leaks_nothing(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        _repoint(tid, p, _broken_source(tid, password="Rotated-Secret-123"))
        run = _run(tid, p["id"])
        _expect(tid, p, run, status="failed", code="data_source_auth_failed", health="blocked",
                untouched_from=before)
        dump = str(query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (run["id"],)))
        assert "Rotated-Secret-123" not in dump
        # Not claimed by the scheduler (a retry would lock the customer's account)...
        execute("UPDATE inventory_sync_profiles SET enabled = TRUE, next_run_at = NOW() - INTERVAL '1 minute' "
                "WHERE id = %s", (p["id"],))
        assert scheduler.claim_due(owner="w", profile_id=p["id"]) is None
        # ...until the data source is edited or re-tested.
        execute("UPDATE datasets SET updated_at = NOW() + INTERVAL '1 second' WHERE id = %s",
                (profiles.get(tid, p["id"])["data_source_id"],))
        assert scheduler.claim_due(owner="w2", profile_id=p["id"])["id"] == p["id"]

    def test_missing_grants_block(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        role = f"nogrant_{uuid4().hex[:6]}"
        with src_db.cursor() as cur:
            cur.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'pw-123'")
            cur.execute(f"REVOKE ALL ON {src_table} FROM PUBLIC")
        try:
            _repoint(tid, p, _broken_source(tid, username=role, password="pw-123"))
            _expect(tid, p, _run(tid, p["id"]), status="failed", code="data_source_permission_denied",
                    health="blocked", untouched_from=before)
        finally:
            with src_db.cursor() as cur:
                cur.execute(f"DROP OWNED BY {role}")
                cur.execute(f"DROP ROLE {role}")

    @pytest.mark.parametrize("code,health", [
        ("data_source_connection_lost", "degraded"),     # failover / dropped mid-stream
        ("data_source_busy", "degraded"),                # per-tenant gate saturated
        ("data_source_query_timeout", "degraded"),       # statement timeout on their side
        ("data_source_server_busy", "degraded"),
    ])
    def test_transient_read_failures_after_rows_were_read(self, test_tenant, src_db, src_table, monkeypatch,
                                                          code, health):
        from backend.inventory_sync import strategies
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        _put(src_db, src_table, [("A1", "Norte", 99), ("B2", "Norte", 99)])
        before = _snapshot(tid)
        real = strategies.read_for_run

        def drops(*a, **kw):
            gen = real(*a, **kw)
            yield next(gen)                     # the first batch arrived...
            raise AppError(code, "boom", status_code=503)   # ...then the connection died
        monkeypatch.setattr(strategies, "read_for_run", drops)
        o = _expect(tid, p, _run(tid, p["id"]), status="failed", code=code, health=health,
                    untouched_from=before)
        assert o.retries is True
        assert query("SELECT 1 FROM inventory_sync_staging s JOIN inventory_sync_runs r ON r.id = s.run_id "
                     "WHERE r.profile_id = %s", (p["id"],)) == []

    @pytest.mark.parametrize("text,code", [
        ("ORA-28000: the account is locked", "data_source_account_locked"),
        ("Login failed for user 'x'. Reason: The password of the account has expired. Error: 18487",
         "data_source_password_expired"),
        ("ORA-28001: the password has expired", "data_source_password_expired"),
        ("SSL error: certificate has expired", "data_source_tls_cert_expired"),
    ])
    def test_account_and_certificate_states_are_named(self, text, code):
        from backend.datasources.errors import classify
        assert classify(Exception(text), engine="oracle", during="auth").code == code
        assert failures.outcome_of(code).health == "blocked"

    def test_egress_ips_are_offered_for_network_codes(self, client, analyst_headers, monkeypatch):
        monkeypatch.setattr(settings, "inventory_sync_egress_ips", "203.0.113.10, 203.0.113.11")
        r = client.get("/api/v1/inventory-sync/fields", headers=analyst_headers).json()["data"]
        assert r["egress_ips"] == ["203.0.113.10", "203.0.113.11"]
        assert failures.outcome_of("data_source_connect_timeout").as_dict()["egress_hint"] is True


# ── Schema drift ─────────────────────────────────────────────────────────────

class TestSchemaDrift:

    def test_column_renamed(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} RENAME COLUMN existencia TO qty_now")
        run = _run(tid, p["id"])
        _expect(tid, p, run, status="failed", code="inventory_sync_column_missing", health="paused",
                untouched_from=before)
        assert run["error_params"]["column"] == "existencia"

    def test_table_dropped(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} RENAME TO {src_table}_old")
        try:
            _expect(tid, p, _run(tid, p["id"]), status="failed", code="inventory_sync_object_missing",
                    health="paused", untouched_from=before)
        finally:
            with src_db.cursor() as cur:
                cur.execute(f"ALTER TABLE {src_table}_old RENAME TO {src_table}")

    def test_saved_query_on_a_dropped_column(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = profiles.create(tid, "usr_test", {
            "name": "q", "data_source_id": _source(tid), "source_kind": "query",
            "source_query": f"SELECT codigo, almacen, existencia, costo FROM {src_table}",
            "mapping": MAPPING, "allow_new_warehouses": True})
        assert _run(tid, p["id"])["status"] == "success"
        before = _snapshot(tid)
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} DROP COLUMN costo")
        run = _run(tid, p["id"])
        _expect(tid, p, run, status="failed", code="inventory_sync_column_missing", health="paused",
                untouched_from=before)
        assert run["error_params"]["column"] == "costo"

    def test_type_changed_to_text_trips_the_rejection_brake(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} ALTER COLUMN existencia TYPE TEXT")
            cur.execute(f"UPDATE {src_table} SET existencia = 'mucho'")
        _expect(tid, p, _run(tid, p["id"]), status="braked", code="brake_too_many_rejected",
                health="paused", untouched_from=before)

    def test_editing_the_mapping_unpauses(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} RENAME COLUMN existencia TO qty_now")
        _run(tid, p["id"])
        assert profiles.get(tid, p["id"])["health"] == "paused"
        profiles.update(tid, p["id"], {"mapping": {**MAPPING, "current_stock": "qty_now"}})
        assert profiles.get(tid, p["id"])["health"] == "new"
        assert _run(tid, p["id"])["status"] == "success"


# ── Data quality ──────────────────────────────────────────────────────────────

class TestDataQuality:

    def _one(self, tid, src_db, src_table, rows, **kw):
        _put(src_db, src_table, rows)
        p = _profile(tid, _source(tid), src_table, **kw)
        return _run(tid, p["id"]), p

    def test_unicode_trailing_spaces_and_leading_zeros(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        run, _ = self._one(tid, src_db, src_table,
                           [("ÑANDÚ-漢字", "Norte", 1), ("  A1  ", "Norte", 2), ("007", "Norte", 3)])
        assert run["status"] == "success"
        assert set(_stock(tid)) == {("ÑANDÚ-漢字", "Norte"), ("A1", "Norte"), ("007", "Norte")}

    def test_skus_differing_only_by_case_or_spaces(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        rows = [("a1", "Norte", 1), ("A1 ", "Norte", 2)] + [(f"X{i}", "Norte", 1) for i in range(6)]
        run, _ = self._one(tid, src_db, src_table, rows,
                           mapping={**MAPPING, "sku": {"column": "codigo", "case": "upper"}})
        assert run["status"] == "success" and run["rejected"] == 2      # 'A1' twice: both rejected
        assert ("A1", "Norte") not in _stock(tid)

    def test_decimal_comma_scientific_nan_infinity_huge(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        with src_db.cursor() as cur:
            cur.execute(f"ALTER TABLE {src_table} ALTER COLUMN existencia TYPE TEXT")
        rows = [("A", "Norte", "1,5"), ("B", "Norte", "1e3"), ("C", "Norte", "NaN"), ("D", "Norte", "Infinity"),
                ("E", "Norte", "5000000000"), ("F", "Norte", "7"), ("G", "Norte", "8"), ("H", "Norte", "9")]
        run, _ = self._one(tid, src_db, src_table, rows,
                           mapping={**MAPPING, "current_stock": {"column": "existencia", "decimal": "comma"}},
                           brake_max_rejected_pct=60)
        codes = {r["sku"]: r["code"] for r in run["rejected_sample"]}
        assert codes == {"C": "not_a_number", "D": "not_a_number", "E": "out_of_range"}
        stock = _stock(tid)
        assert float(stock[("A", "Norte")]["current_stock"]) == 1.5
        assert float(stock[("B", "Norte")]["current_stock"]) == 1000

    def test_numeric_sku_column_keeps_the_code(self, test_tenant, src_db):
        tid = test_tenant["id"]
        name = f"num_{uuid4().hex[:6]}"
        with src_db.cursor() as cur:
            cur.execute(f"CREATE TABLE {name} (codigo NUMERIC, almacen TEXT, existencia NUMERIC)")
            cur.execute(f"INSERT INTO {name} VALUES (12345, 'Norte', 1), (12345.0, 'Sur', 2)")
        try:
            p = _profile(tid, _source(tid), name, mapping={k: v for k, v in MAPPING.items() if k != "unit_cost"})
            assert _run(tid, p["id"])["status"] == "success"
            assert {k[0] for k in _stock(tid)} == {"12345"}
        finally:
            with src_db.cursor() as cur:
                cur.execute(f"DROP TABLE {name}")

    def test_null_and_future_watermarks(self, test_tenant, src_db, src_table):
        from datetime import datetime
        tid = test_tenant["id"]
        rows = [("A", "Norte", 1, None, 1, None), ("B", "Norte", 2, None, 1, datetime(2099, 1, 1))]
        _put(src_db, src_table, rows)
        p = _profile(tid, _source(tid), src_table, mode="incremental", watermark_column="updated_at")
        run = _run(tid, p["id"])
        assert run["status"] == "success" and "inventory_sync_future_timestamps" in run["warnings"]["codes"]
        wm = profiles.get(tid, p["id"])["watermark_value"]
        assert not wm["value"].startswith("2099")       # never parked in the future

    def test_negative_policy_zero_and_unknown_warehouse(self, test_tenant, src_db, src_table):
        from backend.inventory import warehouse_service as wh
        tid = test_tenant["id"]
        wh.create_warehouse(tid, "Norte")
        rows = [("A", "Norte", -4)] + [(f"X{i}", "Norte", 1) for i in range(4)] + [("Z", "Marte", 1)]
        run, _ = self._one(tid, src_db, src_table, rows, negative_policy="zero", allow_new_warehouses=False)
        assert float(_stock(tid)[("A", "Norte")]["current_stock"]) == 0
        assert run["rejected_sample"][0]["code"] == "unknown_warehouse"

    def test_mixed_units_via_multiplier(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        self._one(tid, src_db, src_table, [("A", "Norte", 3)],
                  mapping={**MAPPING, "current_stock": {"column": "existencia", "multiplier": 12}})
        assert float(_stock(tid)[("A", "Norte")]["current_stock"]) == 36

    def test_huge_cell_is_rejected_by_name(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        rows = [("A" * 70_000, "Norte", 1)] + [(f"X{i}", "Norte", 1) for i in range(4)]
        run, _ = self._one(tid, src_db, src_table, rows)
        assert run["rejected_sample"][0]["code"] == "cell_too_large"


# ── Our side ─────────────────────────────────────────────────────────────────

class TestOurSide:

    @pytest.mark.parametrize("pgcode,code", [("53100", "inventory_sync_storage_full"),
                                             ("55P03", "inventory_sync_our_db_busy")])
    def test_our_database_failures(self, test_tenant, src_db, src_table, monkeypatch, pgcode, code):
        from backend.inventory_sync import apply as applier
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        _put(src_db, src_table, [("A1", "Norte", 1), ("B2", "Norte", 1)])
        before = _snapshot(tid)

        class PgError(Exception):
            pass
        err = PgError("x")
        err.pgcode = pgcode
        monkeypatch.setattr(applier, "_apply_batch_once", lambda *a, **k: (_ for _ in ()).throw(err))
        monkeypatch.setattr(applier.time, "sleep", lambda s: None)
        o = _expect(tid, p, _run(tid, p["id"]), status="failed", code=code, health="degraded",
                    untouched_from=before)
        assert o.retries is True

    def test_profile_edited_during_the_run_stops_it(self, test_tenant, src_db, src_table, monkeypatch):
        from backend.inventory_sync import strategies
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        _put(src_db, src_table, [("A1", "Norte", 50), ("B2", "Norte", 50)])
        before = _snapshot(tid)
        real = strategies.read_for_run

        def edit_midway(*a, **kw):
            for batch in real(*a, **kw):
                execute("UPDATE inventory_sync_profiles SET config_hash = 'edited' WHERE id = %s", (p["id"],))
                yield batch
        monkeypatch.setattr(strategies, "read_for_run", edit_midway)
        run = _run(tid, p["id"])
        assert run["status"] == "failed" and run["error_code"] == "inventory_sync_profile_changed"
        assert _snapshot(tid) == before

    def test_profile_deleted_during_the_run(self, test_tenant, src_db, src_table, monkeypatch):
        from backend.inventory_sync import strategies
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        real = strategies.read_for_run

        def delete_midway(*a, **kw):
            for batch in real(*a, **kw):
                execute("DELETE FROM inventory_sync_profiles WHERE id = %s", (p["id"],))
                yield batch
        monkeypatch.setattr(strategies, "read_for_run", delete_midway)
        run_id = engine.start_run(tid, p["id"], "manual")
        out = engine.execute_run(tid, p["id"], run_id)
        assert out["status"] == "failed"
        assert query("SELECT 1 FROM inventory_sync_staging WHERE run_id = %s", (run_id,)) == []
        # The half-read source was CLOSED: no transaction left open on the
        # customer's database (it held a share lock that blocked their DDL for
        # minutes, until garbage collection).
        assert _open_source_transactions(src_db) == 0

    def test_plan_ceiling_refuses_new_skus_and_updates_existing(self, test_tenant, src_db, src_table,
                                                                monkeypatch):
        from backend.entitlements import service as ent
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table, rows=[("A1", "Norte", 1)])
        monkeypatch.setattr(settings, "testing_mode", False)
        monkeypatch.setattr(ent, "tenant_limits", lambda t: {"max_skus": 3, "max_locations": 5})
        _put(src_db, src_table, [("A1", "Norte", 9)] + [(f"N{i}", "Norte", 1) for i in range(5)])
        execute("UPDATE inventory_sync_profiles SET brake_max_jump_pct = 100 WHERE id = %s", (p["id"],))
        run = _run(tid, p["id"])
        assert run["status"] == "success" and run["refused_ceiling"] == 3 and run["created"] == 2
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 9
        assert len(_stock(tid)) == 3
        row = profiles.get(tid, p["id"])
        assert row["health"] == "degraded" and row["health_reason"] == "inventory_sync_sku_ceiling"

    def test_tenant_erased_mid_run_ends_quietly(self, test_tenant, src_db, src_table, monkeypatch):
        from backend.inventory_sync import strategies
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-{uuid4().hex[:8]}")
        try:
            tid = other["id"]
            p = _seed(tid, src_db, src_table)
            real = strategies.read_for_run

            def erase_midway(*a, **kw):
                for batch in real(*a, **kw):
                    execute("DELETE FROM tenants WHERE id = %s", (tid,))
                    yield batch
            monkeypatch.setattr(strategies, "read_for_run", erase_midway)
            out = engine.execute_run(tid, p["id"], engine.start_run(tid, p["id"], "manual"))
            assert out["status"] == "failed"
            assert query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,)) == []
            assert _open_source_transactions(src_db) == 0
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))


# ── Safety ───────────────────────────────────────────────────────────────────

class TestSafety:

    def test_mass_quantity_change_brakes(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        rows = [(f"S{i}", "Norte", 10) for i in range(40)]
        p = _seed(tid, src_db, src_table, rows=rows)
        before = _snapshot(tid)
        _put(src_db, src_table, [(f"S{i}", "Norte", 1000) for i in range(40)])     # boxes -> units?
        run = _run(tid, p["id"])
        _expect(tid, p, run, status="braked", code="brake_mass_quantity_change", health="paused",
                untouched_from=before)
        assert run["error_params"]["jumped"] == 40

    def test_wrong_database_is_detected_by_key_overlap(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table, rows=[(f"S{i}", "Norte", 1) for i in range(60)])
        before = _snapshot(tid)
        _put(src_db, src_table, [(f"OTHER{i}", "Norte", 1) for i in range(60)])
        _expect(tid, p, _run(tid, p["id"]), status="braked", code="brake_source_looks_different",
                health="paused", untouched_from=before)

    def test_a_brake_never_unfreezes_without_a_person(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        _put(src_db, src_table, [])
        _run(tid, p["id"])
        _put(src_db, src_table, [("A1", "Norte", 10), ("B2", "Norte", 5)])     # the source heals
        execute("UPDATE inventory_sync_profiles SET enabled = TRUE, next_run_at = NOW() - INTERVAL '1 minute' "
                "WHERE id = %s", (p["id"],))
        assert scheduler.claim_due(owner="w", profile_id=p["id"]) is None        # still paused: a person decides
        assert scheduler.refresh(tid, "usr_test", wait_s=0)["profiles"][0]["status"] == "paused"

    def test_first_sync_has_no_vanished_rule_to_trip(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 1)])
        p = _profile(tid, _source(tid), src_table, brake_max_vanished_pct=0)
        assert _run(tid, p["id"])["status"] == "success"

    def test_alerts_once_per_episode_and_a_recovery_notice(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        _repoint(tid, p, _broken_source(tid, port=1))
        for _ in range(3):
            _run(tid, p["id"])
        assert len(query("SELECT 1 FROM activity_logs WHERE tenant_id = %s AND action = 'inventory_sync.run_failed'",
                         (tid,))) == 1
        execute("UPDATE inventory_sync_profiles SET data_source_id = (SELECT id FROM datasets WHERE tenant_id = %s "
                "AND name = 'ERP') WHERE id = %s", (tid, p["id"]))
        assert _run(tid, p["id"])["status"] == "success"
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s AND action = 'inventory_sync.recovered'",
                     (tid,))
        assert profiles.get(tid, p["id"])["health"] == "healthy"


# ── Security ─────────────────────────────────────────────────────────────────

class TestSecurity:

    def test_a_write_in_a_saved_query_is_refused(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        with pytest.raises(AppError):
            profiles.create(tid, "usr_test", {
                "name": "evil", "data_source_id": _source(tid), "source_kind": "query",
                "source_query": f"WITH x AS (DELETE FROM {src_table} RETURNING *) SELECT * FROM x",
                "mapping": MAPPING})
        assert query("SELECT 1 FROM inventory_sync_profiles WHERE tenant_id = %s", (tid,)) == []
        with src_db.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {src_table}")
            assert cur.fetchone()[0] >= 0

    def test_scoped_refresh_sku_is_a_literal_not_sql(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        profiles.mark_dry_run(tid, p["id"], profiles.get(tid, p["id"])["config_hash"])
        execute("UPDATE inventory_sync_profiles SET enabled = TRUE WHERE id = %s", (p["id"],))
        evil = "x' OR '1'='1"
        out = scheduler.refresh(tid, "usr_test", skus=[evil], wait_s=20)["profiles"][0]
        assert out["status"] == "done"
        run = query_one("SELECT rows_read FROM inventory_sync_runs WHERE id = %s", (out["run_id"],))
        assert run["rows_read"] == 0
        with src_db.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {src_table}")
            assert cur.fetchone()[0] == 2

    def test_runs_of_another_tenant_are_invisible(self, client, make_tenant_user_headers, test_tenant,
                                                  src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        run_id = profiles.get(tid, p["id"])["last_run_id"]
        other = make_tenant_user_headers("analyst")
        r = client.get(f"/api/v1/inventory-sync/profiles/{p['id']}/runs/{run_id}", headers=other)
        assert r.status_code == 404
        r = client.post(f"/api/v1/inventory-sync/profiles/{p['id']}/run", headers=other)
        assert r.status_code == 404

    def test_deeply_nested_documents_are_bounded(self):
        from backend.datasources.mongo import MAX_DEPTH, flatten
        doc: dict = {}
        cur = doc
        for i in range(50):
            cur["n"] = {}
            cur = cur["n"]
        flat = flatten(doc)
        assert all(k.count(".") <= MAX_DEPTH for k in flat)


# ── Multi-source precedence ───────────────────────────────────────────────────

class TestPrecedence:

    def test_two_profiles_on_one_key_are_deterministic_and_warned(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        sid = _source(tid)
        hi = _profile(tid, sid, src_table, priority=10)
        lo = _profile(tid, sid, src_table, priority=50)
        _run(tid, hi["id"])
        execute(f"UPDATE inventory_sync_keys SET synced_values = synced_values WHERE profile_id = %s", (hi["id"],))
        with src_db.cursor() as cur:
            cur.execute(f"UPDATE {src_table} SET existencia = 99")
        run = _run(tid, lo["id"])
        assert run["status"] == "success" and run["owned_elsewhere"] == 1
        assert "owned_by_other_profile" in run["warnings"]["codes"]
        assert float(_stock(tid)[("A1", "Norte")]["current_stock"]) == 10     # the better profile owns it


# ── Rows of the catalogue the classes above did not exercise end to end ──────

class TestCatalogueGaps:

    def test_row_count_drop_brakes(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        rows = [(f"S{i}", "Norte", 5) for i in range(100)]
        p = _seed(tid, src_db, src_table, rows=rows, brake_max_vanished_pct=100)
        before = _snapshot(tid)
        _put(src_db, src_table, rows[:40])                       # 60 % of the rows gone
        _expect(tid, p, _run(tid, p["id"]), status="braked", code="brake_row_count_drop", health="paused",
                untouched_from=before)

    def test_no_valid_rows_brakes(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table, brake_max_rejected_pct=100)
        before = _snapshot(tid)
        _put(src_db, src_table, [("A1", "Norte", -1), ("B2", "Norte", -2)])   # every row rejected
        _expect(tid, p, _run(tid, p["id"]), status="braked", code="brake_no_valid_rows", health="paused",
                untouched_from=before)

    def test_query_failing_on_the_source_pauses(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table, source_kind="query", source_object=None,
                     source_query=f"SELECT codigo, almacen, existencia / (existencia - 10) AS existencia "
                                  f"FROM {src_table}")
        run = _run(tid, p["id"])                                  # division by zero on the customer's side
        o = _expect(tid, p, run, status="failed", code="sql_query_failed",
                    health=failures.outcome_of("sql_query_failed").health)
        assert o.retries is False and _stock(tid) == {}

    def test_database_that_does_not_exist_blocks(self, test_tenant, src_db, src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        before = _snapshot(tid)
        _repoint(tid, p, _broken_source(tid, database="no_such_database_xyz"))
        o = _expect(tid, p, _run(tid, p["id"]), status="failed", code="data_source_database_not_found",
                    health="blocked", untouched_from=before)
        assert o.retries is False

    def test_a_text_watermark_fails_the_first_incremental_read_before_applying(self, test_tenant, src_db,
                                                                               src_table):
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 10)])
        p = _profile(tid, _source(tid), src_table, mode="incremental", watermark_column="almacen")
        run = _run(tid, p["id"])
        assert run["status"] == "failed" and run["error_code"] == "inventory_sync_watermark_unsupported"
        assert _stock(tid) == {}                                  # nothing applied, not "success then fail"
        assert profiles.get(tid, p["id"])["health"] == failures.outcome_of(
            "inventory_sync_watermark_unsupported").health

    def test_a_run_whose_worker_died_while_reading_is_closed_as_interrupted(self, test_tenant, src_db,
                                                                             src_table):
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        dead = engine.start_run(tid, p["id"], "schedule")          # never staged: died while reading
        execute("UPDATE inventory_sync_profiles SET enabled = TRUE, lease_until = NOW() - INTERVAL '1 second', "
                "next_run_at = NOW() - INTERVAL '1 second' WHERE id = %s", (p["id"],))
        claimed = scheduler.claim_due(owner="w-new", profile_id=p["id"])
        scheduler.run_claimed(claimed, "w-new", "schedule")
        row = query_one("SELECT status, error_code FROM inventory_sync_runs WHERE id = %s", (dead,))
        assert (row["status"], row["error_code"]) == ("failed", "inventory_sync_interrupted")
        latest = query_one("SELECT status FROM inventory_sync_runs WHERE profile_id = %s "
                           "ORDER BY started_at DESC LIMIT 1", (p["id"],))
        assert latest["status"] == "success"

    def test_an_unexpected_error_is_internal_degraded_and_retried(self, test_tenant, src_db, src_table,
                                                                 monkeypatch):
        from backend.inventory_sync import apply as applier
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table)
        _put(src_db, src_table, [("A1", "Norte", 1), ("B2", "Norte", 1)])
        before = _snapshot(tid)
        monkeypatch.setattr(applier, "_apply_batch_once",
                            lambda *a, **k: (_ for _ in ()).throw(ValueError("bug")))
        o = _expect(tid, p, _run(tid, p["id"]), status="failed", code="inventory_sync_internal_error",
                    health="degraded", untouched_from=before)
        assert o.retries is True

    def test_location_ceiling_refuses_new_warehouses_and_applies_the_rest(self, test_tenant, src_db,
                                                                         src_table, monkeypatch):
        from backend.entitlements import service as ent
        from backend.inventory import warehouse_service as wh_svc
        tid = test_tenant["id"]
        p = _seed(tid, src_db, src_table, rows=[("A1", "Norte", 1)])
        monkeypatch.setattr(settings, "testing_mode", False)
        have = int(wh_svc.count_warehouses(tid))
        monkeypatch.setattr(ent, "tenant_limits", lambda t: {"max_skus": 1000, "max_locations": have})
        _put(src_db, src_table, [("A1", "Norte", 7), ("B2", "Bodega Nueva", 3)])
        run = _run(tid, p["id"])
        assert run["status"] == "success" and run["refused_ceiling"] == 1
        stock = _stock(tid)
        assert float(stock[("A1", "Norte")]["current_stock"]) == 7 and ("B2", "Bodega Nueva") not in stock
        assert "inventory_sync_location_ceiling" in (run.get("warnings") or {}).get("codes", [])

    def test_doctor_flags_an_unindexed_change_column_with_the_index_to_create(self, test_tenant, src_db,
                                                                             src_table):
        from backend.inventory_sync import doctor
        tid = test_tenant["id"]
        _put(src_db, src_table, [("A1", "Norte", 1)])
        p = _profile(tid, _source(tid), src_table, mode="incremental", watermark_column="updated_at")
        stage = next(s for s in doctor.run(tid, profiles.get(tid, p["id"]))["stages"] if s["stage"] == "index")
        assert stage["status"] == "warning" and stage["code"] == "inventory_sync_filter_not_indexed"
        assert "CREATE INDEX" in stage["detail"]["suggestion"] and src_table in stage["detail"]["suggestion"]
        with src_db.cursor() as cur:
            cur.execute(f"CREATE INDEX ON {src_table} (updated_at)")
        stage = next(s for s in doctor.run(tid, profiles.get(tid, p["id"]))["stages"] if s["stage"] == "index")
        assert stage["status"] == "ok"


# ── The catalogue itself ──────────────────────────────────────────────────────

def _catalogue_text() -> str:
    return CATALOGUE_TS.read_text(encoding="utf-8")


def test_every_outcome_has_a_remedy_in_both_languages():
    cat = _catalogue_text()
    missing = []
    for code, outcome in failures.CATALOGUE.items():
        own = cat.count(f"'isync.remedy.{code}'") == 2
        fallback = cat.count(f"'isync.remedy.category.{outcome.category}'") == 2
        if not (own or fallback):
            missing.append(code)
    assert not missing, missing
    for cat_name in failures.HEALTH_OF:
        assert cat.count(f"'isync.remedy.category.{cat_name}'") == 2


def test_every_code_the_sync_can_raise_is_catalogued():
    """Grep the inventory sync and data-source code for raised codes: each
    must be in the catalogue (or be a validation refusal answered at once,
    never a run outcome)."""
    root = ROOT / "backend"
    raised = set()
    for path in list((root / "inventory_sync").glob("*.py")) + [root / "datasources" / "errors.py",
                                                                 root / "datasources" / "mongo.py"]:
        text = path.read_text(encoding="utf-8")
        raised |= set(re.findall(r'_c\("([a-z_]+)"', text))
        raised |= {"brake_" + c for c in re.findall(r'_Brake\("([a-z_]+)"', text)}
    validation_only = {c for c in raised if c.startswith(("inventory_sync_ingest", "inventory_sync_document"))}
    unknown = sorted(c for c in raised - validation_only if c not in failures.CATALOGUE)
    assert not unknown, unknown


def test_documented_limits_have_reasons():
    assert len(failures.LIMITS) >= 8
    assert all(len(v) > 60 for v in failures.LIMITS.values())
