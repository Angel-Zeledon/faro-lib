"""How an endpoint is retired without surprising the integrations that call it.

The policy is written in `docs/public-api.md` ("Versioning and deprecation"). This
module is the mechanism: a route listed in `DEPRECATIONS` answers every call with

    Deprecation: @<unix time the deprecation was announced>      (RFC 9745)
    Sunset: <HTTP date after which the route may be removed>     (RFC 8594)
    Link: <successor>; rel="successor-version"                   (when there is one)
    Link: <policy>; rel="deprecation"

and keeps working until the sunset date, so a client that logs response headers
learns about the change from the call itself. The registry is EMPTY today: nothing
is deprecated, so every response is byte-for-byte what it was.

A route is removed only after its sunset date has passed, and
`test_api_deprecation.py` fails when a route listed here is removed before then or
listed without a successor window of at least `MIN_NOTICE_DAYS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from email.utils import format_datetime
from typing import Optional

# Notice an endpoint gets between the day it is announced and the day it may go.
MIN_NOTICE_DAYS = 180
POLICY_URL = "https://app.stockai.es/desarrolladores#versionado"


@dataclass(frozen=True)
class Deprecation:
    announced: date
    sunset: date
    # Path of the replacement under the same prefix (e.g. "/v2/alerts"), if any.
    successor: Optional[str] = None


# (METHOD, route template as declared, without the /api/v1 prefix) -> Deprecation
DEPRECATIONS: dict[tuple[str, str], Deprecation] = {}


def notice_days(d: Deprecation) -> int:
    return (d.sunset - d.announced).days


def _stamp(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def headers_for(method: str, route_template: str,
                registry: Optional[dict] = None) -> dict[str, str]:
    """The deprecation headers for a call, or {} when the route is not deprecated."""
    reg = DEPRECATIONS if registry is None else registry
    entry = reg.get((method.upper(), route_template))
    if entry is None:
        return {}
    headers = {
        "Deprecation": f"@{int(_stamp(entry.announced).timestamp())}",
        "Sunset": format_datetime(_stamp(entry.sunset), usegmt=True),
    }
    links = [f'<{POLICY_URL}>; rel="deprecation"']
    if entry.successor:
        links.insert(0, f'<{entry.successor}>; rel="successor-version"')
    headers["Link"] = ", ".join(links)
    return headers
