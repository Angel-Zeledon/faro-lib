from typing import Optional

from backend.db.connection import query_one, execute, _json
from backend.utils.ids import generate_id
from backend.config import settings

_DEFAULT_QUOTA = {
    "max_sessions": settings.default_max_sessions,
    "max_skus_per_session": settings.default_max_skus,
    "max_concurrent_jobs": settings.max_concurrent_jobs,
    "max_dataset_size_mb": settings.max_upload_size_mb,
}


def create_tenant(name: str) -> dict:
    tenant_id = generate_id("ten")
    execute(
        """INSERT INTO tenants (id, name, slug, plan, status, quota, settings, created_at)
           VALUES (%s, %s, %s, 'free', 'active', %s, '{}', NOW())""",
        (tenant_id, name, name.lower().replace(" ", "-"), _json(_DEFAULT_QUOTA)),
    )
    return get_tenant(tenant_id)


def get_tenant(tenant_id: str) -> Optional[dict]:
    return query_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,))


def get_quota(tenant_id: str) -> dict:
    tenant = get_tenant(tenant_id)
    return tenant.get("quota", _DEFAULT_QUOTA) if tenant else _DEFAULT_QUOTA


def check_session_quota(tenant_id: str) -> bool:
    if settings.testing_mode:
        return True  # testing mode: no plan quota enforcement
    from backend.sessions import service as session_svc
    quota = get_quota(tenant_id)
    current = session_svc.count_sessions(tenant_id)
    return current < quota["max_sessions"]
