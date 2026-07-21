"""FastAPI dependency guards that enforce plan entitlements."""

from fastapi import Depends, HTTPException, status

from backend.auth.guards import CurrentUser, get_current_user
from backend.config import settings
from backend.entitlements.plans import Feature
from backend.entitlements.service import has_feature, required_plans_for
from backend.tenants.service import get_tenant


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
