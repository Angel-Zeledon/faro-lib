"""Deleting a tenant must not leave its data behind — enforced by the database.

Of the 45 tables carrying `tenant_id`, only four declared a foreign key to
`tenants`. Every other table kept its rows after the tenant was gone:
unreachable, invisible and permanent. Measured on the dev database before the
`cascade_tenant_id_foreign_keys` migration ran: 1.3M orphan rows belonging to
24,794 tenants that no longer existed, 596 MB, and a backend suite that had
slowed from 26 minutes to 4h21 dragging them around.

`data_export.delete_tenant()` compensated by deleting each table explicitly, and
`test_tenant_data.py` now schema-checks that list — but that only covers the
product's own erasure path. A script, a fixture or a psql session that removed a
tenant still orphaned everything. These tests pin the guarantee one level down,
where nothing can route around it.
"""

from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one


def _tenant_scoped_tables() -> set[str]:
    return {
        r["table_name"] for r in query(
            "SELECT c.table_name FROM information_schema.columns c "
            "JOIN information_schema.tables x "
            "  ON x.table_name = c.table_name AND x.table_schema = c.table_schema "
            " AND x.table_type = 'BASE TABLE' "
            "WHERE c.column_name = 'tenant_id' AND c.table_schema = 'public' "
            "  AND c.table_name <> 'tenants'"
        )
    }


def _cascading_tables() -> set[str]:
    """Tables whose tenant_id FK to tenants is ON DELETE CASCADE ('c')."""
    return {
        r["table_name"] for r in query(
            "SELECT pc.conrelid::regclass::text AS table_name "
            "FROM pg_constraint pc "
            "JOIN pg_attribute a ON a.attrelid = pc.conrelid "
            "                   AND a.attnum = ANY (pc.conkey) "
            "WHERE pc.contype = 'f' "
            "  AND pc.confrelid = 'public.tenants'::regclass "
            "  AND pc.confdeltype = 'c' "
            "  AND a.attname = 'tenant_id'"
        )
    }


class TestTheDatabaseEnforcesIt:

    def test_every_tenant_scoped_table_cascades_from_tenants(self):
        """The guard that cannot rot: read from the live catalog, not a roster.

        A tenant-scoped table added tomorrow fails here on the day it is added,
        instead of silently orphaning its rows for as long as nobody notices.
        """
        missing = _tenant_scoped_tables() - _cascading_tables()
        assert not missing, (
            f"{len(missing)} table(s) carry tenant_id with no ON DELETE CASCADE "
            f"to tenants: {sorted(missing)} — deleting a tenant orphans them")

    def test_the_migration_left_no_orphans_anywhere(self):
        """Adding the constraint required purging what was already stranded; a
        surviving orphan would mean a table slipped past the migration."""
        stranded = {}
        for table in sorted(_tenant_scoped_tables()):
            n = query_one(
                f"SELECT COUNT(*) AS c FROM {table} x WHERE NOT EXISTS "
                "(SELECT 1 FROM tenants t WHERE t.id = x.tenant_id)"
            )["c"]
            if n:
                stranded[table] = n
        assert not stranded, f"orphan rows survive in: {stranded}"


class TestABareDeleteIsEnough:

    @pytest.fixture
    def doomed_tenant(self):
        tid = f"ten_{uuid4().hex[:12]}"
        execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
            (tid, f"cascade-{tid}", f"cascade-{tid}"),
        )
        yield tid
        execute("DELETE FROM tenants WHERE id = %s", (tid,))

    def test_deleting_the_row_takes_the_childrens_rows_with_it(self, doomed_tenant):
        """The behaviour, not the catalog: a plain `DELETE FROM tenants` — the
        one a script or a psql session would write — has to be sufficient."""
        tid = doomed_tenant
        execute(
            "INSERT INTO inventory_stock (tenant_id, sku, current_stock) "
            "VALUES (%s, %s, %s)", (tid, f"SKU-{uuid4().hex[:6]}", 5),
        )
        execute("INSERT INTO warehouses (tenant_id, name) VALUES (%s, %s)",
                (tid, "Bodega"))
        execute(
            "INSERT INTO activity_logs (id, tenant_id, user_id, action) "
            "VALUES (%s, %s, %s, %s)",
            (f"act_{uuid4().hex[:8]}", tid, f"usr_{uuid4().hex[:8]}", "login"),
        )
        seeded = ("inventory_stock", "warehouses", "activity_logs")
        for table in seeded:
            assert query_one(
                f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s", (tid,)
            )["c"] == 1, f"{table} was not seeded — the test would prove nothing"

        execute("DELETE FROM tenants WHERE id = %s", (tid,))

        left = {}
        for table in sorted(_tenant_scoped_tables()):
            n = query_one(
                f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s", (tid,)
            )["c"]
            if n:
                left[table] = n
        assert not left, f"a bare DELETE FROM tenants stranded rows in: {left}"

    def test_another_tenants_rows_are_untouched(self, doomed_tenant, test_tenant):
        """A cascade that reached past its own tenant would be far worse than
        the leak it replaces."""
        tid, other = doomed_tenant, test_tenant["id"]
        sku = f"KEEP-{uuid4().hex[:6]}"
        execute(
            "INSERT INTO inventory_stock (tenant_id, sku, current_stock) "
            "VALUES (%s, %s, %s)", (other, sku, 9),
        )

        execute("DELETE FROM tenants WHERE id = %s", (tid,))

        assert query_one(
            "SELECT current_stock FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s", (other, sku),
        ) is not None, "the cascade deleted a different tenant's rows"
