"""The schema itself must be English.

CLAUDE.md allows Spanish in exactly four places: app routes, persisted signal
values, vocabulary that matches real user data (CSV aliases, payment terms,
calendar keys), and end-user copy in the locale layers. Database object names
are not among them.

The Spanish sweep renamed the tables and columns, but object names that
Postgres carries independently of the table — index names, constraint names —
survive an `ALTER TABLE ... RENAME`. `inventory_mermas_pkey` sat on
`inventory_shrinkage` for exactly that reason: the migration renamed the two
secondary indexes explicitly and never listed the primary key.

These assert the live schema, not the migration source, so a rename that is
written but never applied still fails.
"""
import pytest

from backend.db.connection import query

# Whole words that are unambiguously Spanish and would only appear in an object
# name left over from before the sweep. Deliberately not a general Spanish
# detector: the point is to catch regressions of the known vocabulary, and a
# fuzzy matcher would fire on English names that merely look Latin.
_SPANISH_FRAGMENTS = (
    "merma", "bodega", "cantidad", "proveedor", "categoria", "fecha",
    "costo", "precio", "existencia", "ventas", "usuario", "pedido",
)


def _offenders(rows: list[dict], key: str) -> list[str]:
    return [
        r[key] for r in rows
        if any(frag in str(r[key]).lower() for frag in _SPANISH_FRAGMENTS)
    ]


class TestSchemaObjectNamesAreEnglish:
    """`client` is requested only for its side effect: app startup is what
    initialises the connection pool and runs migrations, so these assert the
    schema as a booted server actually leaves it."""

    @pytest.fixture(autouse=True)
    def _booted(self, client):
        return client

    def test_no_spanish_table_names(self):
        rows = query("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        assert _offenders(rows, "tablename") == []

    def test_no_spanish_column_names(self):
        rows = query(
            "SELECT table_name || '.' || column_name AS name "
            "FROM information_schema.columns WHERE table_schema = 'public'"
        )
        assert _offenders(rows, "name") == []

    def test_no_spanish_index_names(self):
        """The one that regressed: index names outlive a table rename."""
        rows = query("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        assert _offenders(rows, "indexname") == []

    def test_no_spanish_constraint_names(self):
        rows = query(
            "SELECT conname FROM pg_constraint c "
            "JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'public'"
        )
        assert _offenders(rows, "conname") == []


class TestTheDetectorCanActuallyFail:
    """A test suite that cannot fail is decoration. This proves the matcher
    fires on the exact name that regressed."""

    def test_matcher_flags_the_name_that_regressed(self):
        rows = [{"indexname": "inventory_mermas_pkey"}]
        assert _offenders(rows, "indexname") == ["inventory_mermas_pkey"]

    def test_matcher_leaves_english_names_alone(self):
        rows = [{"indexname": "inventory_shrinkage_pkey"},
                {"indexname": "sessions_tenant_idx"}]
        assert _offenders(rows, "indexname") == []
