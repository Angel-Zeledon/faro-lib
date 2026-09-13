"""What a tenant may do: which tier it is on, its limits, and whether it may write.

Feature entitlements used to live here too — `has_feature`, `required_plans_for`
and a catalog to look them up in. They are not coming back: both tiers include
every feature, so a feature check could only ever answer True, and a permission
that cannot say no is worse than no permission at all — it reads like a gate
while gating nothing. The only thing a tier decides is *how much*.
"""

from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime, timezone

from backend.entitlements.plans import DEFAULT_TIER, PLANS, PlanDef

_LIMIT_FIELDS = tuple(f.name for f in fields(PlanDef))


def tenant_tier(tenant: dict) -> str:
    """The tier this tenant runs on: 'free' or 'paid'.

    Anything unrecognised — a NULL column on a row that predates the migration,
    a typo somebody typed into psql — resolves to free. Failing to the paid
    tier would silently hand out an unlimited account, which is the one failure
    mode nobody would notice.
    """
    tier = (tenant or {}).get("tier")
    return tier if tier in PLANS else DEFAULT_TIER


def get_plan_def(tenant: dict | None = None) -> PlanDef:
    return PLANS[tenant_tier(tenant or {})]


def tenant_limits(tenant: dict) -> dict:
    """The tier's limits, with any per-tenant override on top.

    `tenants.quota` is how we widen (or narrow) one account without a deploy —
    an agreement with one customer, not a tier anybody can buy. It wins over
    the tier, in both directions: a free tenant we promised 500 SKUs while they
    migrate gets 500 SKUs.
    """
    plan = get_plan_def(tenant)
    override = (tenant or {}).get("quota") or {}
    limits = {}
    for field in _LIMIT_FIELDS:
        limits[field] = override[field] if field in override else getattr(plan, field)
    return limits


def trial_state(tenant: dict) -> str:
    """Whether a time-boxed grant is still open.

    Nobody is put on one any more — the free tier is a permanent home, not a
    countdown, so signup leaves `trial_ends_at` NULL and this answers "active".
    The machinery stays for the one thing it is still good for: suspending a
    single account by hand, by writing a past date into that column.
    """
    ends = tenant.get("trial_ends_at")
    if ends is None:
        return "active"
    if isinstance(ends, str):
        ends = datetime.fromisoformat(ends)
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return "trialing" if ends >= datetime.now(timezone.utc) else "expired"


def is_read_only(tenant: dict) -> bool:
    return trial_state(tenant) == "expired"


@contextmanager
def limit_guard(tenant_id: str):
    """Serialize one tenant's limit-consuming writes, and yield the connection
    they must run on.

    Every ceiling in this product is a count followed by a write:

        current = count_stock(tenant)     # ← another request lands here
        enforce_limit(tenant, "max_skus", current)
        write()

    Single-threaded that is correct. Concurrently it is a check that has already
    expired by the time it is acted on, and the window is wide — a COUNT, a
    tenant lookup and a plan resolution all sit inside it. Measured on
    2026-08-22 before this existed: twelve simultaneous writes against a ceiling
    of five left **ten** rows, and eight simultaneous invitations against a
    ceiling of two left **nine** users. On the free tier that ceiling is the
    entire commercial boundary of the product, so "it only happens under load"
    is not a mitigation — bulk import in one tab and a manual entry in another
    is load.

    A transaction-scoped advisory lock is the fix that does not require a new
    column, a trigger, or a rewrite of every write path. It is keyed on the
    tenant, so tenants never wait on each other, and Postgres releases it at
    commit — which is also the moment the row becomes visible, so the next
    waiter's COUNT can no longer miss it.

    The caller MUST pass the yielded `conn` to both the count and the write. A
    count on a second connection would work (the previous writer has committed
    by the time the lock is granted) but would hold two pooled connections per
    request, and the pool is ten.
    """
    from backend.db.connection import transaction
    with transaction() as conn:
        take_tenant_lock(tenant_id, conn)
        yield conn


def take_tenant_lock(tenant_id: str, conn) -> None:
    """Serialize this tenant's limit-consuming writes, on a transaction the
    caller already owns.

    `limit_guard` is the version for code that has no transaction yet. This one
    is for the paths that do — a transfer reception, a PO reception, an
    integration sync — where opening a second transaction just to hold a lock
    would burn a pooled connection and put the lock on the wrong side of the
    commit. Call it FIRST inside the block, before the count.

    hashtext() maps the tenant id to the bigint the lock API wants. A collision
    between two tenant ids would only make them queue behind each other —
    slower, never wrong.
    """
    from backend.db.connection import query_one
    query_one("SELECT pg_advisory_xact_lock(hashtext(%s))", (tenant_id,), conn=conn)


def enforce_limit(tenant_id: str, limit_key: str, current: int, adding: int = 1,
                  conn=None) -> None:
    """Raise 403 PLAN_LIMIT_REACHED when `current + adding` would exceed the
    tenant's limit for `limit_key`. No-op in testing mode, and no-op when the
    limit is unbounded (None, which is every commercial limit on the paid tier).

    `conn` is the connection from `limit_guard`. Passing it is what makes the
    check and the write that follows one atomic unit; omitting it keeps the old
    non-atomic behaviour, which is still correct for a single caller and still
    wrong under concurrency.

    The `detail` dict is the wire contract: `backend/main.py` promotes its
    `code` to the envelope's `error_code` and the rest to `error_params`, so the
    frontend can show "you have 100 of 100 SKUs" in the user's language and
    offer the way to ask for more.
    """
    from fastapi import HTTPException, status
    from backend.config import settings
    from backend.db.connection import query_one
    if settings.testing_mode:
        return
    tenant = query_one(
        "SELECT * FROM tenants WHERE id = %s", (tenant_id,), conn=conn
    ) or {"quota": {}}
    max_allowed = tenant_limits(tenant)[limit_key]
    if max_allowed is not None and current + adding > max_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PLAN_LIMIT_REACHED", "limit": limit_key,
                    "current": current, "max": max_allowed,
                    "tier": tenant_tier(tenant)},
        )
