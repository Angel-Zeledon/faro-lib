import secrets
import logging
import re
from calendar import monthrange
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator, model_validator

from backend.activity.events import record_event
from backend.api.public_surface import ROLE_SCOPE, SCOPE_ROLE
from backend.auth.api_key_auth import KEY_PREFIX, RATE_MAX_PER_MINUTE, hash_key
from backend.auth.guards import (
    CurrentUser, get_current_user, require_admin, require_analyst_or_above,
)
from backend.errors import AppError
from backend.db.connection import execute, query, query_one
from backend.schemas.common import ok

router = APIRouter(
    prefix="/api-keys", tags=["api-keys"],
)
log = logging.getLogger(__name__)


class CreateKeyRequest(BaseModel):
    name: str
    # What the key may do, as the public API names it: 'read' (acts as a
    # viewer) or 'write' (acts as an analyst) — the same two roles a person can
    # hold, enforced by the same guards, so a key can never reach past what the
    # product already knows how to refuse.
    scope: str | None = None
    # The older spelling of the same choice ('viewer' | 'analyst'), still
    # accepted so a client written against it keeps working. Sending both is
    # fine only when they agree.
    role: str | None = None
    # Days until the key stops working. None = never, which is what an
    # unattended nightly sync usually wants.
    expires_in_days: int | None = None

    @field_validator("scope")
    @classmethod
    def _known_scope(cls, v: str | None) -> str | None:
        if v is not None and v not in SCOPE_ROLE:
            raise ValueError("scope must be 'read' or 'write'")
        return v

    @model_validator(mode="after")
    def _scope_and_role_agree(self):
        if self.scope and self.role and SCOPE_ROLE[self.scope] != self.role:
            raise ValueError("scope and role disagree; send only scope")
        if self.role is None:
            self.role = SCOPE_ROLE[self.scope or "read"]
        if self.scope is None:
            self.scope = ROLE_SCOPE[self.role]
        return self

    @field_validator("name")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ("viewer", "analyst"):
            raise ValueError("role must be 'viewer' or 'analyst'")
        return v

    @field_validator("expires_in_days")
    @classmethod
    def _positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("expires_in_days must be at least 1")
        return v


@router.post("")
def create_api_key(body: CreateKeyRequest, user: CurrentUser = Depends(require_analyst_or_above)):
    # How many machine credentials this tenant may hold. The free tier gets one
    # — enough for the nightly ERP push it is meant to run — and minting a
    # second is the moment to talk to us, not a silent extra.
    from backend.entitlements.service import enforce_limit, limit_guard

    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    # No role check beyond the guard on this endpoint, deliberately: 'analyst'
    # is the strongest role a key can hold, and `require_analyst_or_above` has
    # already refused anyone weaker than that. A key can never outrank the
    # person who minted it because there is no rank above the one they hold.
    # Counted and minted under one per-tenant lock. Two clicks on "create key"
    # otherwise both read "0 keys" and the free tier's single credential
    # becomes two.
    with limit_guard(user.tenant_id) as conn:
        existing = query_one(
            "SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = %s",
            (user.tenant_id,), conn=conn,
        )
        enforce_limit(user.tenant_id, "max_api_keys",
                      int(existing["n"]) if existing else 0, conn=conn)
        execute(
            """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4, expires_at)
               VALUES (gen_random_uuid()::text, %s, %s, %s, %s, %s, %s,
                       CASE WHEN %s IS NULL THEN NULL
                            ELSE NOW() + (%s || ' days')::INTERVAL END)""",
            (user.tenant_id, body.name, hash_key(raw), body.role, user.user_id, raw[-4:],
             body.expires_in_days, body.expires_in_days),
            conn=conn,
        )
    # The name and role are safe to log; the key itself never is, not even
    # truncated, and not even at DEBUG.
    log.info("[api-keys] created name=%s scope=%s tenant=%s", body.name, body.scope, user.tenant_id)
    # A credential that runs unattended now exists. Every other admin should be
    # able to see that it was minted without reading a log file — this is the
    # one event in the product that a stranger would create if they got in.
    record_event(
        user.tenant_id, user.user_id, "account.api_key_created",
        resource=body.name, reason="changed_by_an_account_admin",
        details={"key_name": body.name, "role": body.role, "scope": body.scope},
    )
    # The raw key is returned exactly once. Nothing stores it — not this
    # process, not the database — so a customer who loses it mints a new one.
    return ok({"key": raw, "name": body.name, "role": body.role, "scope": body.scope})


@router.get("")
def list_api_keys(user: CurrentUser = Depends(get_current_user)):
    rows = query(
        """SELECT id, name, role, scope, last4, last_used, expires_at, created_at
           FROM api_keys WHERE tenant_id = %s ORDER BY created_at DESC""",
        (user.tenant_id,),
    )
    return ok([dict(r) for r in rows])


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("/usage")
def api_key_usage(
    month: str | None = Query(
        None, description="Calendar month as YYYY-MM (UTC). Defaults to the current month.",
    ),
    user: CurrentUser = Depends(require_admin),
):
    """API-key calls this tenant made in one calendar month, by day and by key.

    The numbers a call-based bill is computed from. Days are UTC. A call is
    counted when it reached an endpoint — a refused one (bad key, a route keys
    cannot call, a read key on a write, over the rate limit) is not. Keys that
    were revoked still appear, flagged `active: false`, because the calls they
    made before revocation are still part of the month.
    """
    today = datetime.now(timezone.utc).date()
    if month is None:
        year, mon = today.year, today.month
    else:
        if not _MONTH_RE.match(month):
            raise AppError(
                "invalid_month", "month must be YYYY-MM",
                status_code=422, params={"month": month},
            )
        year, mon = int(month[:4]), int(month[5:7])
    first = date(year, mon, 1)
    last = date(year, mon, monthrange(year, mon)[1])

    rows = query(
        """SELECT u.day, u.api_key_id, u.key_name, u.calls,
                  k.scope, (k.id IS NOT NULL) AS active
             FROM api_usage_daily u
             LEFT JOIN api_keys k ON k.id = u.api_key_id AND k.tenant_id = u.tenant_id
            WHERE u.tenant_id = %s AND u.day BETWEEN %s AND %s
            ORDER BY u.day""",
        (user.tenant_id, first, last),
    )

    per_day: dict[date, int] = {}
    per_key: dict[str, dict] = {}
    for r in rows:
        calls = int(r["calls"])
        per_day[r["day"]] = per_day.get(r["day"], 0) + calls
        entry = per_key.setdefault(r["api_key_id"], {
            "api_key_id": r["api_key_id"], "name": r["key_name"],
            "scope": r["scope"], "active": bool(r["active"]), "calls": 0,
        })
        entry["calls"] += calls
        # The newest name the key was metered under is the one to show.
        entry["name"] = r["key_name"]

    # Every day of the month up to today (or the whole month, for a past one),
    # zeros included: a chart with gaps reads as "no data", not as "no calls".
    # A future month has no days yet.
    by_day = []
    d, end = first, min(last, today)
    while d <= end:
        by_day.append({"day": d.isoformat(), "calls": per_day.get(d, 0)})
        d = date.fromordinal(d.toordinal() + 1)

    from backend.auth.api_key_auth import _daily_ceiling
    return ok({
        "month": f"{year:04d}-{mon:02d}",
        "timezone": "UTC",
        "total": sum(per_day.values()),
        "today": per_day.get(today, 0) if (year, mon) == (today.year, today.month) else None,
        "by_day": by_day,
        "by_key": sorted(per_key.values(), key=lambda e: (-e["calls"], e["name"])),
        "limits": {
            "per_minute_per_key": RATE_MAX_PER_MINUTE,
            # None = no daily ceiling on this tenant's tier.
            "per_day_per_key": _daily_ceiling(user.tenant_id),
        },
    })


@router.delete("/{key_id}")
def revoke_api_key(key_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    row = query_one(
        "SELECT id, name FROM api_keys WHERE id = %s AND tenant_id = %s",
        (key_id, user.tenant_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
    record_event(
        user.tenant_id, user.user_id, "account.api_key_revoked",
        resource=key_id, reason="changed_by_an_account_admin",
        details={"key_name": row.get("name")},
    )
    return ok({"revoked": key_id})
