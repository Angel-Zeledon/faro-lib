"""Session family: schema, planning, fan-out (multi-period Phase A)."""

from backend.db.connection import query


def _columns(table: str) -> set[str]:
    rows = query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,))
    return {r["column_name"] for r in rows}


class TestFamilySchema:
    def test_sessions_have_family_columns(self, client):
        cols = _columns("sessions")
        assert "family_id" in cols
        assert "granularity" in cols
