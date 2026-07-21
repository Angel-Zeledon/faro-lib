"""Entitlement queries over a tenant row + the plan catalog."""

from datetime import datetime, timezone

from backend.entitlements.plans import Feature, PlanDef, PLAN_CATALOG

_LIMIT_FIELDS = (
    "max_skus", "max_users", "max_locations",
    "max_sessions", "max_concurrent_jobs", "max_dataset_size_mb",
)


def get_plan_def(plan: str) -> PlanDef:
    return PLAN_CATALOG.get(plan or "", PLAN_CATALOG["starter"])


def has_feature(tenant: dict, feature: Feature) -> bool:
    return feature in get_plan_def(tenant.get("plan", "")).features


def tenant_limits(tenant: dict) -> dict:
    plan = get_plan_def(tenant.get("plan", ""))
    override = tenant.get("quota") or {}
    limits = {}
    for field in _LIMIT_FIELDS:
        limits[field] = override[field] if field in override else getattr(plan, field)
    return limits


def trial_state(tenant: dict) -> str:
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


def required_plans_for(feature: Feature) -> list[str]:
    return [name for name, d in PLAN_CATALOG.items() if feature in d.features]
