"""Stock allocation: what Python owns (the schema) and what it must keep true.

The allocation routes are written in Rust (`backend-rs/src/routes/
stock_allocation.rs`). Python has no twin of them, so what is checked here is
the part Python is responsible for:

* the schema the Rust routes write (constraints, the one-active-reservation
  guarantee, cascade from the tenant, whole-tenant erasure);
* the promise that makes the feature safe: it is ADVISORY. No allocation table
  is an input of the semaforo, and no Python module reads one, so the purchase
  recommendation cannot be skewed by a reservation;
* the three events exist in the activity registry and the audit catalogue.
"""

import pathlib
import re
from uuid import uuid4

import psycopg2
import pytest

from backend.db.connection import execute, query, query_one

ALLOCATION_TABLES = (
    "allocation_customer_priorities",
    "allocation_tier_policy",
    "allocation_runs",
    "stock_reservations",
)

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _reservation(tenant_id: str, commitment_id: str, *, status: str = "active", units: int = 5_000_000):
    execute(
        "INSERT INTO stock_reservations (id, tenant_id, run_id, sku, commitment_id, customer, tier,"
        " units_micro, reserved_micro, short_micro, commitment_quantity, commitment_probability,"
        " commitment_delivery_date, status, created_by)"
        " VALUES (%s, %s, 'alr_x', 'SKU-1', %s, 'Acme', 1, %s, %s, 0, 5, 1, '2026-12-01', %s, 'u')",
        (f"res_{uuid4().hex[:12]}", tenant_id, commitment_id, units, units, status),
    )


class TestSchema:

    def test_all_four_tables_exist(self):
        have = {r["table_name"] for r in query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
        assert set(ALLOCATION_TABLES) <= have

    def test_tier_is_bounded_one_to_nine(self, test_tenant):
        for bad in (0, 10, -1):
            with pytest.raises(psycopg2.errors.CheckViolation):
                execute(
                    "INSERT INTO allocation_customer_priorities (tenant_id, customer_key, customer, tier, updated_by)"
                    " VALUES (%s, %s, 'X', %s, 'u')", (test_tenant["id"], f"x{bad}", bad))
        assert query_one(
            "SELECT COUNT(*) AS n FROM allocation_customer_priorities WHERE tenant_id = %s",
            (test_tenant["id"],))["n"] == 0
        with pytest.raises(psycopg2.errors.CheckViolation):
            execute("INSERT INTO allocation_tier_policy (tenant_id, tier, fair_share, updated_by)"
                    " VALUES (%s, 12, TRUE, 'u')", (test_tenant["id"],))

    def test_a_customer_has_one_tier_per_tenant(self, test_tenant):
        sql = ("INSERT INTO allocation_customer_priorities (tenant_id, customer_key, customer, tier, updated_by)"
               " VALUES (%s, 'acme', 'Acme', 2, 'u')")
        execute(sql, (test_tenant["id"],))
        with pytest.raises(psycopg2.errors.UniqueViolation):
            execute(sql, (test_tenant["id"],))

    def test_a_commitment_holds_one_active_reservation_but_history_is_kept(self, test_tenant):
        tid = test_tenant["id"]
        _reservation(tid, "cd_1")
        with pytest.raises(psycopg2.errors.UniqueViolation):
            _reservation(tid, "cd_1")
        # Released rows are history: any number of them next to the active one.
        _reservation(tid, "cd_1", status="released")
        _reservation(tid, "cd_1", status="released")
        rows = query("SELECT status FROM stock_reservations WHERE tenant_id = %s AND commitment_id = 'cd_1'", (tid,))
        assert sorted(r["status"] for r in rows) == ["active", "released", "released"]

    def test_reservation_amounts_and_status_are_constrained(self, test_tenant):
        tid = test_tenant["id"]
        with pytest.raises(psycopg2.errors.CheckViolation):
            _reservation(tid, "cd_zero", units=0)
        with pytest.raises(psycopg2.errors.CheckViolation):
            _reservation(tid, "cd_bad", status="deleted")
        assert query_one("SELECT COUNT(*) AS n FROM stock_reservations WHERE tenant_id = %s", (tid,))["n"] == 0


class TestErasure:

    def test_deleting_the_tenant_row_cascades_to_every_allocation_table(self):
        from backend.tenants.service import create_tenant
        tid = create_tenant(f"pytest-{uuid4().hex[:10]}")["id"]
        execute("INSERT INTO allocation_customer_priorities (tenant_id, customer_key, customer, tier, updated_by)"
                " VALUES (%s, 'acme', 'Acme', 1, 'u')", (tid,))
        execute("INSERT INTO allocation_tier_policy (tenant_id, tier, fair_share, updated_by)"
                " VALUES (%s, 2, TRUE, 'u')", (tid,))
        execute("INSERT INTO allocation_runs (id, tenant_id, sku, result_hash, policy, supply, created_by)"
                " VALUES (%s, %s, 'S', 'h', '{}', '{}', 'u')", (f"alr_{uuid4().hex[:8]}", tid))
        _reservation(tid, "cd_1")
        for table in ALLOCATION_TABLES:
            assert query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id = %s", (tid,))["n"] == 1, table
        execute("DELETE FROM tenants WHERE id = %s", (tid,))
        for table in ALLOCATION_TABLES:
            assert query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id = %s", (tid,))["n"] == 0, table

    def test_the_product_erasure_path_and_the_export_both_name_the_tables(self):
        from backend.tenants import data_export
        for table in ALLOCATION_TABLES:
            assert table in data_export._DELETE_ORDER, table
            assert any(spec[1] == table for spec in data_export._EXPORT_SPECS), table
        # Children before parents is not needed (no FK between them), but the
        # reservations go before the runs that explain them, as a habit.
        order = data_export._DELETE_ORDER
        assert order.index("stock_reservations") < order.index("allocation_runs")

    def test_whole_tenant_erasure_removes_the_rows(self):
        from backend.tenants import data_export
        from backend.tenants.service import create_tenant
        tid = create_tenant(f"pytest-{uuid4().hex[:10]}")["id"]
        _reservation(tid, "cd_1")
        data_export.delete_tenant(tid)
        assert query_one("SELECT COUNT(*) AS n FROM stock_reservations WHERE tenant_id = %s", (tid,))["n"] == 0
        assert query_one("SELECT COUNT(*) AS n FROM tenants WHERE id = %s", (tid,)) is None or \
            query_one("SELECT COUNT(*) AS n FROM tenants WHERE id = %s", (tid,))["n"] == 0


class TestAdvisoryOnly:
    """A reservation must never reach the purchase recommendation."""

    def test_no_allocation_table_is_a_status_input(self):
        from backend.db.migrations import STATUS_INPUT_TABLES
        assert not set(ALLOCATION_TABLES) & set(STATUS_INPUT_TABLES)

    def test_no_allocation_table_has_a_status_trigger(self):
        have = {r["tgname"] for r in query(
            "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'status_bump_%%'")}
        for table in ALLOCATION_TABLES:
            assert not any(t.startswith(f"status_bump_{table}_") for t in have), table

    def test_no_python_module_reads_or_writes_an_allocation_table(self):
        """Python only creates, exports and erases them. A query against one
        anywhere else would be a second authority over committed demand."""
        allowed = {"stock_allocation_migrations.py", "data_export.py"}
        pattern = re.compile(r"\b(" + "|".join(ALLOCATION_TABLES) + r")\b")
        offenders = []
        for path in BACKEND.rglob("*.py"):
            if "tests" in path.parts or ".venv" in path.parts or path.name in allowed:
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(BACKEND)))
        assert offenders == []

    def test_the_rust_routes_never_write_stock_rows(self):
        src = (BACKEND.parent / "backend-rs" / "src").joinpath("routes", "stock_allocation.rs").read_text(encoding="utf-8")
        src += (BACKEND.parent / "backend-rs" / "src" / "allocation" / "supply.rs").read_text(encoding="utf-8")
        src += (BACKEND.parent / "backend-rs" / "src" / "allocation" / "engine.rs").read_text(encoding="utf-8")
        writes = re.findall(r"(?i)\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)", src)
        writes = [w for w in writes if w.upper() != "SET"]  # `DO UPDATE SET`
        assert set(writes) <= {
            "allocation_customer_priorities", "allocation_tier_policy", "allocation_runs", "stock_reservations",
        }, set(writes)


class TestEvents:

    @pytest.mark.parametrize("action,keys", [
        ("allocation.priorities_changed", ("customers",)),
        ("allocation.applied", ("sku", "reserved", "short")),
        ("allocation.released", ("sku", "reservations")),
    ])
    def test_event_is_registered_and_audited(self, action, keys):
        from backend.activity.events import EVENTS
        from backend.audit.catalog import LEGACY, all_stored_actions
        assert EVENTS[action].detail_keys == keys
        assert EVENTS[action].kind == "purchase"
        assert action in LEGACY
        assert LEGACY[action][0] == "stock_allocation"
        assert LEGACY[action][1] == action.replace("allocation.", "stock_allocation.")
        assert action in all_stored_actions()
