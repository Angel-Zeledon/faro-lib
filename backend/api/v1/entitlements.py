from fastapi import APIRouter, Depends

from backend.auth.guards import CurrentUser, get_current_user
from backend.entitlements.plans import Feature, PLAN_CATALOG
from backend.entitlements.service import (
    get_plan_def, is_read_only, tenant_limits, trial_state,
)
from backend.schemas.common import ok
from backend.tenants.service import get_tenant

router = APIRouter(prefix="/entitlements", tags=["entitlements"])


@router.get("")
def get_entitlements(user: CurrentUser = Depends(get_current_user)):
    tenant = get_tenant(user.tenant_id) or {"plan": "starter", "quota": {}}
    plan_features = get_plan_def(tenant.get("plan", "starter")).features
    ends = tenant.get("trial_ends_at")
    return ok({
        "plan": tenant.get("plan", "starter"),
        "trial": {
            "state": trial_state(tenant),
            "ends_at": ends.isoformat() if ends else None,
        },
        "limits": tenant_limits(tenant),
        "features": {f.value: (f in plan_features) for f in Feature},
        "read_only": is_read_only(tenant),
    })
