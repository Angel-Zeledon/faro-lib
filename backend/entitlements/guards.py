"""FastAPI dependency guards that enforce plan entitlements."""

from fastapi import Depends, HTTPException, status

from backend.auth.guards import CurrentUser, get_current_user, require_role
from backend.config import settings
from backend.entitlements.plans import Feature
from backend.entitlements.service import has_feature, is_read_only, required_plans_for
from backend.tenants.service import get_tenant


def require_active_analyst(
    user: CurrentUser = Depends(require_role("admin", "analyst")),
) -> CurrentUser:
    """Role check (admin/analyst) plus trial read-only enforcement.

    Delegates the role check to ``require_role`` and additionally blocks
    mutations for tenants whose trial has expired, unless testing_mode
    bypasses entitlement checks entirely.
    """
    if settings.testing_mode:
        return user
    tenant = get_tenant(user.tenant_id) or {}
    if is_read_only(tenant):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "TRIAL_EXPIRED",
                "current_plan": tenant.get("plan", "starter"),
                "trial_ends_at": (
                    tenant["trial_ends_at"].isoformat()
                    if tenant.get("trial_ends_at") else None
                ),
            },
        )
    return user


def require_feature(feature: Feature):
    def guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if settings.testing_mode:
            return user
        tenant = get_tenant(user.tenant_id) or {}
        if not has_feature(tenant, feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PLAN_UPGRADE_REQUIRED",
                    "feature": feature.value,
                    "current_plan": tenant.get("plan", "starter"),
                    "required_plans": required_plans_for(feature),
                },
            )
        return user

    return guard
