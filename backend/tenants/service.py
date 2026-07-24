import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2

from backend.db.connection import query_one, execute, _json
from backend.utils.ids import generate_id
from backend.config import settings

_TRIAL_DAYS = 14


def _slugify(name: str) -> str:
    """Lowercase, collapse non-alphanumerics to single hyphens, trim."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "tenant"


def create_tenant(name: str) -> dict:
    """
    Create a tenant with a unique slug. Two companies can share a display name,
    so on a slug collision we append a short random suffix and retry instead of
    returning a 500 (previously an unhandled UniqueViolation on duplicate names).

    New tenants start on a 14-day Starter trial with no quota override — limits
    come entirely from the plan catalog (backend.entitlements.plans) until the
    tenant is granted a custom override.
    """
    tenant_id = generate_id("ten")
    base = _slugify(name)
    trial_ends = datetime.now(timezone.utc) + timedelta(days=_TRIAL_DAYS)
    candidates = [base] + [f"{base}-{secrets.token_hex(2)}" for _ in range(4)]
    for slug in candidates:
        try:
            execute(
                """INSERT INTO tenants (id, name, slug, plan, status, quota, settings, trial_ends_at, created_at)
                   VALUES (%s, %s, %s, 'starter', 'active', '{}', '{}', %s, NOW())""",
                (tenant_id, name, slug, trial_ends),
            )
            return get_tenant(tenant_id)
        except psycopg2.errors.UniqueViolation:
            pass  # slug taken — try the next candidate
    # Extremely unlikely: fall back to a guaranteed-unique slug from the id.
    execute(
        """INSERT INTO tenants (id, name, slug, plan, status, quota, settings, trial_ends_at, created_at)
           VALUES (%s, %s, %s, 'starter', 'active', '{}', '{}', %s, NOW())""",
        (tenant_id, name, f"{base}-{tenant_id[-8:]}", trial_ends),
    )
    return get_tenant(tenant_id)


def get_tenant(tenant_id: str) -> Optional[dict]:
    return query_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,))


def get_quota(tenant_id: str) -> dict:
    from backend.entitlements.service import tenant_limits
    tenant = get_tenant(tenant_id)
    return tenant_limits(tenant) if tenant else tenant_limits({"plan": "starter", "quota": {}})


def check_session_quota(tenant_id: str) -> bool:
    if settings.testing_mode:
        return True  # testing mode: no plan quota enforcement
    from backend.sessions import service as session_svc
    max_sessions = get_quota(tenant_id)["max_sessions"]
    if max_sessions is None:
        return True  # unlimited (e.g. enterprise plan)
    return session_svc.count_sessions(tenant_id) < max_sessions


def get_settings(tenant_id: str) -> dict:
    """The tenant's `settings` JSONB, already parsed (RealDictCursor). Empty
    dict when the tenant is missing or has no settings yet."""
    row = query_one("SELECT settings FROM tenants WHERE id = %s", (tenant_id,))
    if not row or not row.get("settings"):
        return {}
    return dict(row["settings"])


def update_settings(tenant_id: str, patch: dict) -> dict:
    """Shallow-merge `patch` into the tenant's settings and persist. Returns the
    merged dict. Other top-level keys are preserved (never clobbered)."""
    merged = {**get_settings(tenant_id), **patch}
    execute("UPDATE tenants SET settings = %s WHERE id = %s", (_json(merged), tenant_id))
    return merged
