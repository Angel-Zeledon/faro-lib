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


from backend.sessions import family_service as fam


def _daily_dates(n):
    import datetime
    d0 = datetime.date(2025, 1, 1)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


class TestPlanFamily:
    def test_long_daily_data_yields_three_grains(self):
        specs = fam.plan_family(_daily_dates(900))  # ~30 months
        grains = [s["granularity"] for s in specs]
        assert grains == ["daily", "weekly", "monthly"]
        base = specs[0]
        assert base["is_base"] is True and base["target_freq"] is None
        assert base["horizon"] == 90
        monthly = specs[-1]
        assert monthly["target_freq"] == "MS" and monthly["horizon"] == 12
        assert monthly["is_base"] is False

    def test_short_daily_data_yields_only_base(self):
        specs = fam.plan_family(_daily_dates(10))
        assert [s["granularity"] for s in specs] == ["daily"]
        assert specs[0]["is_base"] is True
