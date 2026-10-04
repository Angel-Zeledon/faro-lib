"""A scheduled hour belongs to somebody's clock.

The frequency picker names an hour ("cada lunes a las 6am") and the cron behind
it was read in UTC, so on the automation screen a Costa Rican admin picked 6am
and the page answered "Próxima ejecución: 12:00 a.m." — six hours off, with no
timezone named anywhere on it.

The stored `next_run` is still a UTC instant; that is what the scheduler compares
against and it must not change. What these pin is the READING, on both sides:
the API computes the first run, and the worker computes every run after it. Fixing
only the API would have been undone on the very first firing.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.api.v1.schedule import _next_run
from backend.db.connection import query_one
from backend.api.v1.timezone import DEFAULT_TZ, SUPPORTED, timezone_for_country
from backend.tenants.service import update_settings
from backend.workers.worker import _next_cron_run

MONDAY_6AM = "0 6 * * 1"


def _local_hour(instant: datetime, tz_name: str) -> int:
    return instant.astimezone(ZoneInfo(tz_name)).hour


class TestTheCronIsReadInTheTenantsZone:
    def test_six_am_means_six_am_where_the_company_is(self, test_tenant):
        """The whole point: the label and the firing hour have to agree."""
        tid = test_tenant["id"]
        update_settings(tid, {"timezone": "America/Costa_Rica"})

        nxt = _next_run(MONDAY_6AM, tid)

        assert nxt.tzinfo is not None, "next_run must be stored as an instant"
        assert _local_hour(nxt, "America/Costa_Rica") == 6
        # And it is NOT 6am UTC any more, which is what produced "12:00 a.m.".
        assert nxt.astimezone(timezone.utc).hour == 12

    def test_a_different_zone_moves_the_instant(self, test_tenant):
        tid = test_tenant["id"]
        update_settings(tid, {"timezone": "America/Costa_Rica"})
        cr = _next_run(MONDAY_6AM, tid)
        update_settings(tid, {"timezone": "Europe/Madrid"})
        es = _next_run(MONDAY_6AM, tid)

        assert cr != es, "the same cron fired at the same instant in two zones"
        assert _local_hour(cr, "America/Costa_Rica") == 6
        assert _local_hour(es, "Europe/Madrid") == 6

    def test_an_unsupported_zone_falls_back_instead_of_raising(self, test_tenant):
        """A bad stored zone must not take a 3am scheduled job down."""
        tid = test_tenant["id"]
        update_settings(tid, {"timezone": "Mars/Olympus_Mons"})
        nxt = _next_run(MONDAY_6AM, tid)
        assert _local_hour(nxt, DEFAULT_TZ) == 6

    def test_a_tenant_that_never_chose_gets_the_anchor_market(self, test_tenant):
        tid = test_tenant["id"]
        update_settings(tid, {"timezone": None})
        assert _local_hour(_next_run(MONDAY_6AM, tid), DEFAULT_TZ) == 6


class TestTheWorkerRescheduesInTheSameZone:
    def test_the_second_run_lands_on_the_same_hour_as_the_first(self, test_tenant):
        """Rescheduling in UTC would have quietly undone the fix after one firing."""
        tid = test_tenant["id"]
        update_settings(tid, {"timezone": "America/Costa_Rica"})

        first = _next_run(MONDAY_6AM, tid)
        # The worker reschedules from the moment it fired.
        second = _next_cron_run(MONDAY_6AM, first + timedelta(minutes=1), tid)

        assert _local_hour(second, "America/Costa_Rica") == 6
        assert second > first
        assert (second - first) == timedelta(days=7)

    def test_without_a_tenant_it_still_behaves_as_before(self):
        """The parameter is optional; existing callers must not change meaning."""
        now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        nxt = _next_cron_run(MONDAY_6AM, now)
        assert nxt.astimezone(timezone.utc).hour == 6

    def test_an_invalid_cron_still_moves_forward(self, test_tenant):
        """An unparseable expression must not leave next_run in the past — that
        turned the scheduler poll into a hot loop retrying every 60s."""
        now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        nxt = _next_cron_run("not a cron", now, test_tenant["id"])
        assert nxt > now


class TestTheZoneSetting:
    def test_every_supported_zone_is_a_real_zone(self):
        """A typo here surfaces as a crash inside croniter, at 3am, unattended."""
        for tz_name in SUPPORTED:
            ZoneInfo(tz_name)   # raises if the zone does not exist

    def test_wizard_countries_map_to_a_zone(self):
        """The onboarding wizard already asks for the country; these are the ones
        it offers, so each has to resolve."""
        for country in ("CR", "CO", "MX", "PE", "CL", "AR", "EC", "GT", "PA", "DO", "ES"):
            assert timezone_for_country(country), country

    def test_an_unknown_country_maps_to_nothing_rather_than_guessing(self):
        assert timezone_for_country("ZZ") is None
        assert timezone_for_country(None) is None

    def test_viewer_can_read_but_not_change_the_zone(self, client, viewer_headers,
                                                    auth_headers):
        """It moves when every armed retrain fires, so writing it is admin-only —
        but any screen showing a scheduled hour needs to read it."""
        assert client.get("/api/v1/tenant/timezone", headers=viewer_headers).status_code == 200

        denied = client.patch("/api/v1/tenant/timezone", headers=viewer_headers,
                              json={"timezone": "Europe/Madrid"})
        assert denied.status_code == 403
        after = client.get("/api/v1/tenant/timezone", headers=auth_headers).json()
        assert after["data"]["current"]["timezone"] != "Europe/Madrid"

    def test_changing_the_zone_moves_the_schedules_that_are_already_armed(
        self, client, auth_headers, completed_session,
    ):
        """A schedule stores an instant, so the zone change has to move it.

        Measured in the browser: with the tenant on Madrid, "cada domingo a las
        8am" was stored as 06:00Z. Switching to Costa Rica left that instant
        alone, so the screen showed "cada domingo a las 8am · Próxima ejecución:
        9/8/26, 0:00" — the same row contradicting itself. The admin is also told
        the change affects when their retrains fire, so it must be true on the
        FIRST firing, not the second.
        """
        sid = completed_session["id"]
        client.patch("/api/v1/tenant/timezone", headers=auth_headers,
                     json={"timezone": "Europe/Madrid"})
        assert client.post(f"/api/v1/sessions/{sid}/schedule", headers=auth_headers,
                           json={"cron_expr": "0 8 * * 0"}).status_code == 200
        madrid_instant = query_one(
            "SELECT next_run FROM scheduled_jobs WHERE session_id = %s", (sid,))["next_run"]
        assert _local_hour(madrid_instant, "Europe/Madrid") == 8

        r = client.patch("/api/v1/tenant/timezone", headers=auth_headers,
                         json={"timezone": "America/Costa_Rica"})
        assert r.status_code == 200
        assert r.json()["data"]["schedules_rescheduled"] == 1

        moved = query_one(
            "SELECT next_run FROM scheduled_jobs WHERE session_id = %s", (sid,))["next_run"]
        assert moved != madrid_instant, "the armed schedule kept the old zone's instant"
        assert _local_hour(moved, "America/Costa_Rica") == 8

    def test_a_paused_schedule_is_left_alone(self, client, auth_headers, completed_session):
        """Nothing fires from it, and saving it again recomputes next_run anyway."""
        sid = completed_session["id"]
        client.patch("/api/v1/tenant/timezone", headers=auth_headers,
                     json={"timezone": "Europe/Madrid"})
        client.post(f"/api/v1/sessions/{sid}/schedule", headers=auth_headers,
                    json={"cron_expr": "0 8 * * 0", "enabled": False})
        before = query_one("SELECT next_run FROM scheduled_jobs WHERE session_id = %s",
                           (sid,))["next_run"]

        r = client.patch("/api/v1/tenant/timezone", headers=auth_headers,
                         json={"timezone": "America/Costa_Rica"})
        assert r.json()["data"]["schedules_rescheduled"] == 0
        after = query_one("SELECT next_run FROM scheduled_jobs WHERE session_id = %s",
                          (sid,))["next_run"]
        assert after == before

    def test_a_cron_with_five_invalid_fields_is_rejected_not_turned_into_tomorrow(
        self, client, auth_headers, completed_session,
    ):
        """Five fields is not five VALID fields.

        '0 99 * * 1' passed the count check, croniter raised inside _next_run and
        the fallback there quietly returned "24 hours from now": the API answered
        200, the screen showed a next run, and the schedule fired at an hour
        nobody chose. A rejected request is the honest answer.
        """
        sid = completed_session["id"]
        r = client.post(f"/api/v1/sessions/{sid}/schedule", headers=auth_headers,
                        json={"cron_expr": "0 99 * * 1", "enabled": True})
        assert r.status_code == 422, r.text

        stored = query_one(
            "SELECT id FROM scheduled_jobs WHERE session_id = %s", (sid,))
        assert stored is None, "the invalid schedule was saved anyway"

    def test_a_valid_preset_still_saves(self, client, auth_headers, completed_session):
        """The guard above must not reject what the picker actually offers."""
        sid = completed_session["id"]
        r = client.post(f"/api/v1/sessions/{sid}/schedule", headers=auth_headers,
                        json={"cron_expr": MONDAY_6AM, "enabled": True})
        assert r.status_code == 200, r.text
        assert query_one("SELECT cron_expr FROM scheduled_jobs WHERE session_id = %s",
                         (sid,))["cron_expr"] == MONDAY_6AM

    def test_an_unsupported_zone_is_rejected_with_a_code(self, client, auth_headers):
        r = client.patch("/api/v1/tenant/timezone", headers=auth_headers,
                         json={"timezone": "Mars/Olympus_Mons"})
        assert r.status_code == 400
        assert r.json()["error_code"] == "timezone_not_supported"
