"""The one entitlement guard left: an expired trial cannot write.

`require_feature` used to live here and gated about forty routes by plan. With
one plan it has nothing to decide, so it is gone rather than left as a
dependency that always says yes.
"""

from fastapi import Depends, HTTPException, status

from backend.auth.guards import CurrentUser, require_role
from backend.config import settings
from backend.entitlements.service import is_read_only
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
                "trial_ends_at": (
                    tenant["trial_ends_at"].isoformat()
                    if tenant.get("trial_ends_at") else None
                ),
            },
        )
    return user
