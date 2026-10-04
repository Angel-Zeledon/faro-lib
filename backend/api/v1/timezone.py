"""The tenant's clock.

Scheduled retrains are stored as UTC instants — which is right — but the cron
that produces them was also INTERPRETED in UTC while the frequency labels name a
clock hour ("cada lunes a las 6am"). Driving the automation screen as a Costa
Rican admin: picking 6am produced "Próxima ejecución: 12:00 a.m.", six hours off,
with no mention of a timezone anywhere on the page.

Reading the cron in the tenant's own timezone is what makes the label true. The
stored `next_run` stays a UTC instant, so the worker's due check is untouched.

Mirrors `currency.py` deliberately: same JSONB settings blob, same shape, same
admin-only write, so there is one way to hold a tenant preference rather than two.
"""

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.auth.guards import CurrentUser, get_current_user, require_role
from backend.db.connection import execute, query
from backend.errors import AppError
from backend.schemas.common import ok
from backend.tenants.service import get_settings, update_settings

router = APIRouter(prefix="/tenant/timezone", tags=["timezone"])
log = logging.getLogger(__name__)

# The zones this product's market actually spans, each paired with the country
# the onboarding wizard already asks about for holidays. Deliberately short: an
# unvalidated tz string would surface as a crash inside croniter, at 3am, in a
# scheduled job nobody is watching.
#
# `label` is ENGLISH and is a fallback, not the copy: the frontend renders
# `timezone.zone.<iana>` from its own catalogue (CLAUDE.md, Language — backend
# logic never holds a Spanish string, and "México" here failed that guard). It
# ships at all so an unmapped zone shows a country name instead of a raw key.
SUPPORTED = {
    "America/Costa_Rica":  {"label": "Costa Rica",           "country": "CR"},
    "America/Bogota":      {"label": "Colombia",             "country": "CO"},
    "America/Mexico_City": {"label": "Mexico",               "country": "MX"},
    "America/Lima":        {"label": "Peru",                 "country": "PE"},
    "America/Santiago":    {"label": "Chile",                "country": "CL"},
    "America/Argentina/Buenos_Aires": {"label": "Argentina", "country": "AR"},
    "America/Guayaquil":   {"label": "Ecuador",              "country": "EC"},
    "America/Guatemala":   {"label": "Guatemala",            "country": "GT"},
    "America/Panama":      {"label": "Panama",               "country": "PA"},
    "America/Santo_Domingo": {"label": "Dominican Republic", "country": "DO"},
    "Europe/Madrid":       {"label": "Spain",                "country": "ES"},
    "America/New_York":    {"label": "United States (east)", "country": "US"},
    "UTC":                 {"label": "UTC",                  "country": None},
}

# Costa Rica is the anchor market. A tenant that never chose keeps the behaviour
# it had before this setting existed only in the sense that its schedules are
# unambiguous; the label now matches the hour it runs at.
DEFAULT_TZ = "America/Costa_Rica"

_BY_COUNTRY = {v["country"]: k for k, v in SUPPORTED.items() if v["country"]}


class TimezoneUpdate(BaseModel):
    timezone: str


def timezone_of(tenant_id: str) -> str:
    """The tenant's IANA zone, always a value `ZoneInfo` accepts."""
    tz = (get_settings(tenant_id) or {}).get("timezone") or DEFAULT_TZ
    if tz not in SUPPORTED:
        log.warning("[timezone] tenant %s has unsupported zone %s", tenant_id, tz)
        return DEFAULT_TZ
    return tz


def zoneinfo_of(tenant_id: str) -> ZoneInfo:
    """`timezone_of` as a tzinfo, never raising: a bad zone must not take a
    scheduled job down, it must fall back to the default and say so."""
    try:
        return ZoneInfo(timezone_of(tenant_id))
    except ZoneInfoNotFoundError:
        log.warning("[timezone] tzdata missing for tenant %s — using UTC", tenant_id)
        return ZoneInfo("UTC")


def timezone_for_country(country: str | None) -> str | None:
    """The zone that goes with a wizard country code, if we support one."""
    return _BY_COUNTRY.get((country or "").upper()) if country else None


@router.get("")
def get_timezone(user: CurrentUser = Depends(get_current_user)):
    """Readable by every role: any screen showing a scheduled time needs it."""
    current = timezone_of(user.tenant_id)
    return ok({
        "current": {"timezone": current, **SUPPORTED[current]},
        "supported": [{"timezone": tz, **meta} for tz, meta in SUPPORTED.items()],
    })


@router.patch("")
def set_timezone(
    body: TimezoneUpdate,
    # Admin only: it moves when every scheduled retrain in the company fires.
    user: CurrentUser = Depends(require_role("admin")),
):
    tz = body.timezone.strip()
    if tz not in SUPPORTED:
        raise AppError(
            "timezone_not_supported",
            f"Timezone '{tz}' is not supported.",
            status_code=400,
            params={"timezone": tz, "supported": sorted(SUPPORTED)},
        )
    update_settings(user.tenant_id, {"timezone": tz})
    log.info("[timezone] tenant %s -> %s (by %s)", user.tenant_id, tz, user.user_id)
    return ok({
        "current": {"timezone": tz, **SUPPORTED[tz]},
        "schedules_rescheduled": _reanchor_schedules(user.tenant_id),
    })


def _reanchor_schedules(tenant_id: str) -> int:
    """Recompute `next_run` for the tenant's armed schedules in the NEW zone.

    A schedule stores a UTC instant, not an hour, so changing the zone left
    every armed retrain pointing at the OLD local hour for one more cycle.
    Measured on screen: a schedule reading "cada domingo a las 8am" showed
    "Próxima ejecución: 9/8/26, 0:00" right after the zone moved from Madrid to
    Costa Rica — the same screen contradicting itself, which is the exact defect
    this whole setting exists to remove. The change is also announced to the
    admin as affecting when their retrains fire, so it has to be true now and
    not from the second firing onwards.
    """
    from backend.api.v1.schedule import _next_run   # local: schedule.py imports this module

    rows = query(
        "SELECT id, cron_expr FROM scheduled_jobs WHERE tenant_id = %s AND enabled = true",
        (tenant_id,),
    )
    moved = 0
    for row in rows:
        try:
            execute(
                "UPDATE scheduled_jobs SET next_run = %s WHERE id = %s",
                (_next_run(row["cron_expr"], tenant_id), row["id"]),
            )
            moved += 1
        except Exception as exc:
            # One unparseable cron must not block the zone change (already saved)
            # nor the remaining schedules.
            log.error("[timezone] tenant %s: could not re-anchor schedule %s (%r): %s",
                      tenant_id, row["id"], row["cron_expr"], exc)
    if moved:
        log.info("[timezone] tenant %s: re-anchored %d schedule(s)", tenant_id, moved)
    return moved
