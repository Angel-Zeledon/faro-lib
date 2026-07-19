"""
Tests for the preloaded LatAm commercial calendar (feature 3.4).

Two layers:
  * Pure date math (movable feasts) — verifiable against published calendars.
  * Seeding + toggling through the API, asserted with direct DB queries and
    the mandatory viewer-denied / analyst-allowed permission pairs.
"""
from datetime import date

import pytest

from backend.db.connection import query, query_one
from backend.inventory import calendar_catalog as cat
from backend.inventory import service as inv_svc


# ── Movable date math ────────────────────────────────────────────────────────

class TestMovableDates:
    @pytest.mark.offline
    @pytest.mark.parametrize("year,expected", [
        (2020, date(2020, 4, 12)),
        (2021, date(2021, 4, 4)),
        (2022, date(2022, 4, 17)),
        (2023, date(2023, 4, 9)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2028, date(2028, 4, 16)),
        (2030, date(2030, 4, 21)),
    ])
    def test_easter_sunday_matches_published_calendar(self, year, expected):
        assert cat.easter_sunday(year) == expected

    @pytest.mark.offline
    def test_easter_is_always_a_sunday(self):
        for year in range(2000, 2101):
            assert cat.easter_sunday(year).weekday() == 6, year

    @pytest.mark.offline
    def test_semana_santa_runs_palm_sunday_to_easter(self):
        # 2026: Pascua 5-abr → Domingo de Ramos 29-mar.
        entry = next(e for e in cat.CATALOG if e.key == "co_semana_santa")
        occ = entry.occurrences(2026)[0]
        assert occ.start_date == date(2026, 3, 29)
        assert occ.end_date == date(2026, 4, 5)
        assert occ.catalog_key == "co_semana_santa:2026"

    @pytest.mark.offline
    @pytest.mark.parametrize("year,expected", [
        (2024, date(2024, 5, 12)),
        (2025, date(2025, 5, 11)),
        (2026, date(2026, 5, 10)),
        (2027, date(2027, 5,  9)),
    ])
    def test_mothers_day_is_second_sunday_of_may(self, year, expected):
        got = cat.mothers_day_co(year)
        assert got == expected
        assert got.weekday() == 6

    @pytest.mark.offline
    @pytest.mark.parametrize("year,expected", [
        (2024, date(2024, 11, 29)),
        (2025, date(2025, 11, 28)),
        (2026, date(2026, 11, 27)),
        (2027, date(2027, 11, 26)),
    ])
    def test_black_friday_follows_fourth_thursday_of_november(self, year, expected):
        got = cat.black_friday(year)
        assert got == expected
        assert got.weekday() == 4  # viernes

    @pytest.mark.offline
    def test_quincena_fin_de_mes_clamps_february(self):
        entry = next(e for e in cat.CATALOG if e.key == "co_quincena_30")
        feb = [o for o in entry.occurrences(2026) if o.start_date.month == 2][0]
        # 2026 no es bisiesto → el "30" cae al 28.
        assert feb.start_date == date(2026, 2, 28)
        assert cat.easter_sunday(2026).year == 2026  # sanity: same-year build

    @pytest.mark.offline
    def test_leap_year_february_quincena(self):
        entry = next(e for e in cat.CATALOG if e.key == "co_quincena_30")
        feb = [o for o in entry.occurrences(2028) if o.start_date.month == 2][0]
        assert feb.start_date == date(2028, 2, 29)


class TestCatalogShape:
    @pytest.mark.offline
    def test_catalog_covers_the_required_events(self):
        keys = {e.key for e in cat.catalog_for("CO")}
        assert keys == {
            "co_quincena_15", "co_quincena_30",
            "co_prima_junio", "co_prima_diciembre",
            "co_dia_madre", "co_semana_santa",
            "co_temporada_escolar", "co_navidad", "co_black_friday",
        }

    @pytest.mark.offline
    def test_occurrence_keys_are_unique_and_dates_ordered(self):
        occ = cat.build_occurrences("CO", [2026, 2027])
        keys = [o.catalog_key for o in occ]
        assert len(keys) == len(set(keys)), "catalog_key debe ser único por ocurrencia"
        for o in occ:
            assert o.end_date >= o.start_date, o.catalog_key
            assert o.multiplier > 1.0, o.catalog_key

    @pytest.mark.offline
    def test_quincenas_produce_twelve_occurrences_per_year(self):
        occ = cat.build_occurrences("CO", [2026])
        q15 = [o for o in occ if o.catalog_key.startswith("co_quincena_15:")]
        q30 = [o for o in occ if o.catalog_key.startswith("co_quincena_30:")]
        assert len(q15) == 12
        assert len(q30) == 12

    @pytest.mark.offline
    def test_unknown_country_has_no_catalog(self):
        assert cat.catalog_for("XX") == []


# ── Seeding through the API, asserted in the DB ──────────────────────────────

def _seeded_rows(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM inventory_events "
        "WHERE tenant_id = %s AND catalog_key IS NOT NULL ORDER BY start_date",
        (tenant_id,),
    )


class TestSeedEndpoint:
    def test_viewer_cannot_seed_and_db_stays_empty(
        self, client, viewer_headers, test_tenant
    ):
        before = len(_seeded_rows(test_tenant["id"]))

        resp = client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=viewer_headers,
        )

        assert resp.status_code == 403, resp.text
        assert len(_seeded_rows(test_tenant["id"])) == before, \
            "un viewer rechazado no debe haber sembrado nada"

    def test_analyst_seeds_catalog_into_db(
        self, client, analyst_headers, test_tenant
    ):
        resp = client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        summary = resp.json()["data"]

        rows = _seeded_rows(test_tenant["id"])
        expected = len(cat.build_occurrences("CO", [2026]))
        assert summary["inserted"] == expected
        assert len(rows) == expected

        by_key = {r["catalog_key"]: r for r in rows}
        # Semana Santa 2026 landed on the computed dates, not on a hardcoded guess.
        ss = by_key["co_semana_santa:2026"]
        assert ss["start_date"] == date(2026, 3, 29)
        assert ss["end_date"] == date(2026, 4, 5)
        assert ss["source"] == "catalog"
        assert ss["country"] == "CO"
        assert ss["active"] is True
        assert ss["multiplier"] == 1.5

        # Día de la Madre 2026: semana previa al segundo domingo de mayo.
        madre = by_key["co_dia_madre:2026"]
        assert madre["end_date"] == date(2026, 5, 10)
        assert madre["start_date"] == date(2026, 5, 4)

        assert by_key["co_black_friday:2026"]["start_date"] == date(2026, 11, 27)
        assert by_key["co_prima_diciembre:2026"]["start_date"] == date(2026, 12, 1)

    def test_seeding_twice_is_idempotent(
        self, client, analyst_headers, test_tenant
    ):
        first = client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=analyst_headers,
        )
        assert first.status_code == 200
        count_after_first = len(_seeded_rows(test_tenant["id"]))
        assert count_after_first > 0

        second = client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=analyst_headers,
        )
        assert second.status_code == 200
        assert second.json()["data"]["inserted"] == 0
        assert second.json()["data"]["already_present"] == count_after_first
        assert len(_seeded_rows(test_tenant["id"])) == count_after_first, \
            "resembrar no debe duplicar filas"

    def test_reseeding_does_not_resurrect_a_disabled_event(
        self, client, analyst_headers, test_tenant
    ):
        client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=analyst_headers,
        )
        off = client.patch(
            "/api/v1/inventory/events/catalog/co_navidad",
            json={"active": False},
            headers=analyst_headers,
        )
        assert off.status_code == 200, off.text

        client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=analyst_headers,
        )

        row = query_one(
            "SELECT active FROM inventory_events "
            "WHERE tenant_id = %s AND catalog_key = %s",
            (test_tenant["id"], "co_navidad:2026"),
        )
        assert row["active"] is False, "el evento apagado revivió al resembrar"

    def test_unknown_country_rejected(self, client, analyst_headers):
        resp = client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "XX"},
            headers=analyst_headers,
        )
        assert resp.status_code == 422


class TestCatalogToggle:
    def test_viewer_cannot_toggle_and_state_unchanged(
        self, client, analyst_headers, viewer_headers, test_tenant
    ):
        client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=analyst_headers,
        )

        resp = client.patch(
            "/api/v1/inventory/events/catalog/co_semana_santa",
            json={"active": False},
            headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text

        row = query_one(
            "SELECT active FROM inventory_events "
            "WHERE tenant_id = %s AND catalog_key = %s",
            (test_tenant["id"], "co_semana_santa:2026"),
        )
        assert row["active"] is True, "el viewer no debió poder apagar el evento"

    def test_analyst_toggles_whole_group_off_and_on(
        self, client, analyst_headers, test_tenant
    ):
        client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=analyst_headers,
        )

        def active_count() -> int:
            return query_one(
                "SELECT COUNT(*) FILTER (WHERE active IS TRUE) AS n "
                "FROM inventory_events "
                "WHERE tenant_id = %s AND catalog_key LIKE 'co_quincena_15:%%'",
                (test_tenant["id"],),
            )["n"]

        assert active_count() == 12

        off = client.patch(
            "/api/v1/inventory/events/catalog/co_quincena_15",
            json={"active": False},
            headers=analyst_headers,
        )
        assert off.status_code == 200, off.text
        assert off.json()["data"]["updated"] == 12
        assert active_count() == 0, "las 12 quincenas debieron quedar apagadas"

        on = client.patch(
            "/api/v1/inventory/events/catalog/co_quincena_15",
            json={"active": True},
            headers=analyst_headers,
        )
        assert on.status_code == 200
        assert active_count() == 12

    def test_toggling_unknown_catalog_key_404(self, client, analyst_headers):
        resp = client.patch(
            "/api/v1/inventory/events/catalog/no_existe",
            json={"active": False},
            headers=analyst_headers,
        )
        assert resp.status_code == 404

    def test_disabled_events_disappear_from_upcoming(
        self, client, analyst_headers, test_tenant
    ):
        """An event switched off must stop raising the dashboard alert."""
        today = date.today()
        ev = client.post(
            "/api/v1/inventory/events",
            json={
                "name": "Promo interna",
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
                "multiplier": 2.0,
            },
            headers=analyst_headers,
        )
        assert ev.status_code == 201
        event_id = ev.json()["data"]["id"]

        upcoming = inv_svc.get_upcoming_events(test_tenant["id"], days_ahead=30)
        assert any(e["id"] == event_id for e in upcoming)

        patched = client.patch(
            f"/api/v1/inventory/events/{event_id}",
            json={"active": False},
            headers=analyst_headers,
        )
        assert patched.status_code == 200, patched.text

        row = query_one(
            "SELECT active FROM inventory_events WHERE id = %s", (event_id,)
        )
        assert row["active"] is False

        upcoming = inv_svc.get_upcoming_events(test_tenant["id"], days_ahead=30)
        assert not any(e["id"] == event_id for e in upcoming), \
            "un evento apagado no debe aparecer en próximos eventos"


class TestCatalogListing:
    def test_catalog_reports_seeded_and_active_state(
        self, client, auth_headers, analyst_headers
    ):
        before = client.get("/api/v1/inventory/events/catalog", headers=auth_headers)
        assert before.status_code == 200, before.text
        entries = {e["key"]: e for e in before.json()["data"]["entries"]}
        assert len(entries) == 9
        assert entries["co_navidad"]["seeded"] is False
        assert entries["co_navidad"]["occurrences"] == 0

        client.post(
            "/api/v1/inventory/events/catalog/seed",
            json={"country": "CO", "years": [2026]},
            headers=analyst_headers,
        )

        after = client.get("/api/v1/inventory/events/catalog", headers=auth_headers)
        entries = {e["key"]: e for e in after.json()["data"]["entries"]}
        assert entries["co_navidad"]["seeded"] is True
        assert entries["co_navidad"]["active"] is True
        assert entries["co_quincena_15"]["occurrences"] == 12

        client.patch(
            "/api/v1/inventory/events/catalog/co_navidad",
            json={"active": False},
            headers=analyst_headers,
        )
        after = client.get("/api/v1/inventory/events/catalog", headers=auth_headers)
        entries = {e["key"]: e for e in after.json()["data"]["entries"]}
        assert entries["co_navidad"]["active"] is False
        assert entries["co_navidad"]["seeded"] is True, \
            "apagar no es desembrar: las ocurrencias siguen existiendo"

    def test_unknown_country_rejected(self, client, auth_headers):
        resp = client.get(
            "/api/v1/inventory/events/catalog?country=ZZ", headers=auth_headers
        )
        assert resp.status_code == 422
