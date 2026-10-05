"""
Commercial calendar for the extra LatAm countries (MX, PE, CL, AR, EC, GT, PA, DO)
and a cross-check of the public-holiday dates the engine relies on.

Every expected date below was derived by hand from the country's own rule
(statute, or "Nth Sunday of month") — never copied from the code under test.
"""
from datetime import date, timedelta

import pytest

from backend.db.connection import query
from backend.inventory import calendar_catalog as cat

NEW_COUNTRIES = ["MX", "PE", "CL", "AR", "EC", "GT", "PA", "DO"]


def _occ(country: str, key_suffix: str, year: int):
    ev = next(e for e in cat.catalog_for(country) if e.key == f"{country.lower()}_{key_suffix}")
    return ev.occurrences(year)


# ── Dates ────────────────────────────────────────────────────────────────────

class TestGiftDates:
    @pytest.mark.offline
    @pytest.mark.parametrize("country,suffix,year,expected", [
        # Mexico and Guatemala: 10 May, fixed.
        ("MX", "dia_madre", 2025, date(2025, 5, 10)),
        ("MX", "dia_madre", 2027, date(2027, 5, 10)),
        ("GT", "dia_madre", 2026, date(2026, 5, 10)),
        # Peru, Chile, Ecuador: second Sunday of May.
        ("PE", "dia_madre", 2025, date(2025, 5, 11)),
        ("PE", "dia_madre", 2026, date(2026, 5, 10)),
        ("CL", "dia_madre", 2027, date(2027, 5, 9)),
        ("EC", "dia_madre", 2028, date(2028, 5, 14)),
        # Argentina: third Sunday of October.
        ("AR", "dia_madre", 2025, date(2025, 10, 19)),
        ("AR", "dia_madre", 2026, date(2026, 10, 18)),
        ("AR", "dia_madre", 2027, date(2027, 10, 17)),
        ("AR", "dia_madre", 2028, date(2028, 10, 15)),
        # Panama: 8 December, fixed.
        ("PA", "dia_madre", 2026, date(2026, 12, 8)),
        # Dominican Republic: last Sunday of May.
        ("DO", "dia_madre", 2026, date(2026, 5, 31)),
        ("DO", "dia_madre", 2027, date(2027, 5, 30)),
        ("DO", "dia_madre", 2025, date(2025, 5, 25)),
        # Father's Day: third Sunday of June (MX, PE, CL, AR, EC).
        ("MX", "dia_padre", 2025, date(2025, 6, 15)),
        ("PE", "dia_padre", 2026, date(2026, 6, 21)),
        ("CL", "dia_padre", 2027, date(2027, 6, 20)),
        ("AR", "dia_padre", 2028, date(2028, 6, 18)),
        ("EC", "dia_padre", 2026, date(2026, 6, 21)),
        # Guatemala: 17 June, fixed. Dominican Republic: last Sunday of July.
        ("GT", "dia_padre", 2027, date(2027, 6, 17)),
        ("DO", "dia_padre", 2026, date(2026, 7, 26)),
        ("DO", "dia_padre", 2027, date(2027, 7, 25)),
        # Argentina Children's Day: third Sunday of August.
        ("AR", "dia_nino", 2025, date(2025, 8, 17)),
        ("AR", "dia_nino", 2026, date(2026, 8, 16)),
    ])
    def test_gift_day_ends_on_the_celebration_date(self, country, suffix, year, expected):
        (occ,) = _occ(country, suffix, year)
        assert occ.end_date == expected
        assert occ.start_date < occ.end_date

    @pytest.mark.offline
    @pytest.mark.parametrize("year,easter", [
        (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)), (2028, date(2028, 4, 16)),
    ])
    def test_semana_santa_ends_on_easter_in_every_country(self, year, easter):
        for country in NEW_COUNTRIES:
            (occ,) = _occ(country, "semana_santa", year)
            assert occ.end_date == easter, country
            assert occ.start_date == easter - timedelta(days=7), country

    @pytest.mark.offline
    @pytest.mark.parametrize("year,monday,tuesday", [
        (2025, date(2025, 3, 3), date(2025, 3, 4)),
        (2026, date(2026, 2, 16), date(2026, 2, 17)),
        (2027, date(2027, 2, 8), date(2027, 2, 9)),
        (2028, date(2028, 2, 28), date(2028, 2, 29)),
    ])
    def test_carnival_window_covers_monday_and_tuesday(self, year, monday, tuesday):
        for country in ("AR", "EC", "PA"):
            (occ,) = _occ(country, "carnaval", year)
            assert occ.end_date == tuesday, country
            assert occ.start_date <= monday, country
            assert occ.start_date.weekday() == 5, "starts on the Saturday before"

    @pytest.mark.offline
    @pytest.mark.parametrize("year,friday", [
        (2025, date(2025, 11, 28)), (2026, date(2026, 11, 27)),
        (2027, date(2027, 11, 26)), (2028, date(2028, 11, 24)),
    ])
    def test_black_friday_in_new_countries(self, year, friday):
        for country in NEW_COUNTRIES:
            (occ,) = _occ(country, "black_friday", year)
            assert occ.start_date == friday, country

    @pytest.mark.offline
    def test_statutory_bonus_windows_end_on_the_legal_deadline(self):
        assert _occ("MX", "aguinaldo", 2026)[0].end_date == date(2026, 12, 20)
        assert _occ("PE", "gratificacion_julio", 2026)[0].end_date == date(2026, 7, 15)
        assert _occ("PE", "gratificacion_diciembre", 2026)[0].end_date == date(2026, 12, 15)
        assert _occ("AR", "aguinaldo_junio", 2026)[0].end_date == date(2026, 6, 30)
        assert _occ("AR", "aguinaldo_diciembre", 2026)[0].end_date == date(2026, 12, 18)
        assert _occ("EC", "decimotercero", 2026)[0].end_date == date(2026, 12, 24)
        assert _occ("EC", "decimocuarto_costa", 2026)[0].end_date == date(2026, 3, 15)
        assert _occ("EC", "decimocuarto_sierra", 2026)[0].end_date == date(2026, 8, 15)
        assert _occ("GT", "bono_14", 2026)[0].end_date == date(2026, 7, 15)
        assert _occ("DO", "regalia_pascual", 2026)[0].end_date == date(2026, 12, 20)

    @pytest.mark.offline
    def test_panama_decimotercer_mes_has_three_installments(self):
        occs = _occ("PA", "decimotercer_mes", 2026)
        assert [o.start_date for o in occs] == [
            date(2026, 4, 15), date(2026, 8, 15), date(2026, 12, 15)]
        assert len({o.name for o in occs}) == 3

    @pytest.mark.offline
    def test_last_weekday_of_month(self):
        assert cat.last_weekday_of_month(2026, 5, 6) == date(2026, 5, 31)
        assert cat.last_weekday_of_month(2026, 2, 6) == date(2026, 2, 22)
        assert cat.last_weekday_of_month(2028, 2, 6) == date(2028, 2, 27)  # leap year


# ── Catalog structure ────────────────────────────────────────────────────────

class TestCatalogStructure:
    @pytest.mark.offline
    def test_every_new_country_is_supported(self):
        for country in NEW_COUNTRIES + ["CR", "CO"]:
            assert country in cat.SUPPORTED_COUNTRIES

    @pytest.mark.offline
    def test_new_entries_are_neutral(self):
        for country in NEW_COUNTRIES:
            events = cat.catalog_for(country)
            assert events
            assert {e.multiplier for e in events} == {1.0}, country

    @pytest.mark.offline
    def test_cr_and_co_keep_their_original_estimates(self):
        by_key = {e.key: e.multiplier for e in cat.CATALOG}
        assert by_key["co_dia_madre"] == 2.0
        assert by_key["cr_aguinaldo"] == 1.9

    @pytest.mark.offline
    def test_entry_keys_are_unique_and_prefixed_by_country(self):
        keys = [e.key for e in cat.CATALOG]
        assert len(keys) == len(set(keys))
        for e in cat.CATALOG:
            assert e.key.startswith(e.country.lower() + "_")

    @pytest.mark.offline
    @pytest.mark.parametrize("country", NEW_COUNTRIES)
    def test_no_duplicate_occurrences_and_valid_ranges(self, country):
        occs = cat.build_occurrences(country, [2026, 2027])
        keys = [o.catalog_key for o in occs]
        assert len(keys) == len(set(keys))
        assert all(o.start_date <= o.end_date for o in occs)
        assert all(o.name.strip() for o in occs)

    @pytest.mark.offline
    @pytest.mark.parametrize("country", NEW_COUNTRIES)
    def test_output_is_deterministic(self, country):
        assert cat.build_occurrences(country, [2026]) == cat.build_occurrences(country, [2026])

    @pytest.mark.offline
    def test_quincena_names_are_not_double_suffixed(self):
        names = [o.name for o in _occ("MX", "quincena_15", 2026)]
        assert names[0] == "Quincena (pago 15 de ene)"
        assert len(set(names)) == 12

    @pytest.mark.offline
    def test_countries_without_a_computable_rule_are_not_invented(self):
        all_keys = {e.key for e in cat.CATALOG}
        assert not any("hot_sale" in k or "buen_fin" in k or "cyber" in k for k in all_keys)
        assert "UY" not in cat.SUPPORTED_COUNTRIES


# ── API: listing and selection by country ────────────────────────────────────

class TestCatalogApi:
    @pytest.mark.parametrize("country", NEW_COUNTRIES)
    def test_listing_returns_only_that_countrys_entries(self, client, auth_headers, country):
        resp = client.get(
            f"/api/v1/inventory/events/catalog?country={country}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["country"] == country
        assert set(data["countries"]) >= set(NEW_COUNTRIES)
        keys = [e["key"] for e in data["entries"]]
        assert keys and len(keys) == len(set(keys))
        assert all(k.startswith(country.lower() + "_") for k in keys)
        assert all(e["multiplier"] == 1.0 for e in data["entries"])
        assert all(not e["seeded"] for e in data["entries"])

    def test_seeding_a_country_writes_only_its_events_to_the_db(
        self, client, analyst_headers, test_tenant
    ):
        resp = client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "MX", "years": [2026]}, headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        rows = query(
            "SELECT catalog_key, country, multiplier, active, source FROM inventory_events "
            "WHERE tenant_id = %s", (test_tenant["id"],))
        assert rows
        assert all(r["country"] == "MX" and r["catalog_key"].startswith("mx_") for r in rows)
        assert all(float(r["multiplier"]) == 1.0 and r["active"] and r["source"] == "catalog"
                   for r in rows)
        assert len(rows) == len({r["catalog_key"] for r in rows})
        madre = [r for r in rows if r["catalog_key"].startswith("mx_dia_madre")]
        assert len(madre) == 1

    def test_viewer_cannot_seed_a_new_country(self, client, viewer_headers, test_tenant):
        resp = client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "PE"}, headers=viewer_headers)
        assert resp.status_code == 403
        assert query(
            "SELECT 1 FROM inventory_events WHERE tenant_id = %s", (test_tenant["id"],)) == []

    def test_user_can_untick_a_new_country_entry(
        self, client, analyst_headers, test_tenant
    ):
        client.post("/api/v1/inventory/events/catalog/seed",
                    json={"country": "CL", "years": [2026]}, headers=analyst_headers)
        resp = client.patch("/api/v1/inventory/events/catalog/cl_navidad",
                            json={"active": False}, headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        rows = query(
            "SELECT active FROM inventory_events WHERE tenant_id = %s "
            "AND catalog_key LIKE 'cl_navidad:%%'", (test_tenant["id"],))
        assert rows and all(r["active"] is False for r in rows)
        others = query(
            "SELECT active FROM inventory_events WHERE tenant_id = %s "
            "AND catalog_key NOT LIKE 'cl_navidad:%%'", (test_tenant["id"],))
        assert others and all(r["active"] is True for r in others)


# ── Public holidays the engine uses (cross-check of known dates) ─────────────

class TestPublicHolidayDates:
    """
    The engine reads public holidays from the `holidays` package, which computes
    them from each country's rules. These tests pin the movable ones to dates
    worked out by hand so a library upgrade that shifts one fails here.
    """

    @staticmethod
    def _h(country, year):
        holidays = pytest.importorskip("holidays")
        return holidays.country_holidays(country, years=[year])

    @pytest.mark.offline
    @pytest.mark.parametrize("year,good_friday", [
        (2025, date(2025, 4, 18)), (2026, date(2026, 4, 3)),
        (2027, date(2027, 3, 26)), (2028, date(2028, 4, 14)),
    ])
    def test_good_friday_everywhere_it_is_a_holiday(self, year, good_friday):
        for country in ("CO", "PE", "CL", "AR", "EC", "GT", "PA", "DO", "CR"):
            assert good_friday in self._h(country, year), country

    @pytest.mark.offline
    @pytest.mark.parametrize("year,epiphany,ascension,corpus", [
        # Ley Emiliani: Epiphany moved to Monday; Ascension (Easter+39) and
        # Corpus Christi (Easter+60) moved to the Monday after (Easter+43/+64).
        (2025, date(2025, 1, 6), date(2025, 6, 2), date(2025, 6, 23)),
        (2026, date(2026, 1, 12), date(2026, 5, 18), date(2026, 6, 8)),
        (2027, date(2027, 1, 11), date(2027, 5, 10), date(2027, 5, 31)),
        (2028, date(2028, 1, 10), date(2028, 5, 29), date(2028, 6, 19)),
    ])
    def test_colombia_ley_emiliani_moves_holidays_to_monday(
        self, year, epiphany, ascension, corpus
    ):
        h = self._h("CO", year)
        for d in (epiphany, ascension, corpus):
            assert d in h and d.weekday() == 0

    @pytest.mark.offline
    @pytest.mark.parametrize("year,assumption", [
        (2025, date(2025, 8, 18)), (2026, date(2026, 8, 17)), (2027, date(2027, 8, 16)),
    ])
    def test_colombia_assumption_moves_to_monday(self, year, assumption):
        assert assumption in self._h("CO", year)

    @pytest.mark.offline
    @pytest.mark.parametrize("year,constitution,benito,revolution", [
        # Mexico (LFT art. 74): first Monday of Feb, third Monday of March,
        # third Monday of November.
        (2025, date(2025, 2, 3), date(2025, 3, 17), date(2025, 11, 17)),
        (2026, date(2026, 2, 2), date(2026, 3, 16), date(2026, 11, 16)),
        (2027, date(2027, 2, 1), date(2027, 3, 15), date(2027, 11, 15)),
        (2028, date(2028, 2, 7), date(2028, 3, 20), date(2028, 11, 20)),
    ])
    def test_mexico_long_weekend_mondays(self, year, constitution, benito, revolution):
        h = self._h("MX", year)
        for d in (constitution, benito, revolution):
            assert d in h

    @pytest.mark.offline
    @pytest.mark.parametrize("year,carnival_mon,carnival_tue", [
        (2025, date(2025, 3, 3), date(2025, 3, 4)),
        (2026, date(2026, 2, 16), date(2026, 2, 17)),
        (2027, date(2027, 2, 8), date(2027, 2, 9)),
        (2028, date(2028, 2, 28), date(2028, 2, 29)),
    ])
    def test_carnival_is_a_holiday_in_ecuador_and_argentina(self, year, carnival_mon, carnival_tue):
        for country in ("EC", "AR"):
            h = self._h(country, year)
            assert carnival_mon in h and carnival_tue in h, country

    @pytest.mark.offline
    def test_fixed_national_days(self):
        assert date(2026, 9, 16) in self._h("MX", 2026)
        assert date(2026, 7, 28) in self._h("PE", 2026)
        assert date(2026, 9, 18) in self._h("CL", 2026)
        assert date(2026, 7, 9) in self._h("AR", 2026)
        assert date(2026, 8, 10) in self._h("EC", 2026)
        assert date(2026, 9, 15) in self._h("GT", 2026)
        assert date(2026, 11, 3) in self._h("PA", 2026)
        assert date(2026, 2, 27) in self._h("DO", 2026)
