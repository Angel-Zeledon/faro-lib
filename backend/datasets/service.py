import math
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from backend.config import settings
from backend.db.connection import query_one, query, execute
from backend.entitlements.service import enforce_limit, tenant_limits
from backend.tenants.service import get_tenant
from backend.utils.ids import generate_id

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".parquet", ".xls", ".json"}


def _enforce_dataset_size(tenant_id: str, size_bytes: int) -> None:
    """
    The tenant's plan (or per-tenant quota override) max_dataset_size_mb is the
    authoritative upload cap — an enterprise plan's 2000 MB must not be silently
    truncated by the global settings.max_upload_size_mb (200 MB). The global
    value only acts as an infra ceiling when the plan defines no limit (None).
    """
    size_mb = size_bytes / (1024 * 1024)
    tenant = get_tenant(tenant_id)
    plan_max_mb = tenant_limits(tenant)["max_dataset_size_mb"] if tenant else None
    if plan_max_mb is not None:
        # Raises 403 PLAN_LIMIT_REACHED; no-op in testing mode.
        enforce_limit(
            tenant_id, "max_dataset_size_mb", current=math.ceil(size_mb), adding=0
        )
    elif not settings.testing_mode and size_bytes > settings.max_upload_size_mb * 1024 * 1024:
        raise ValueError(
            f"File size {size_mb:.1f} MB exceeds "
            f"limit of {settings.max_upload_size_mb} MB"
        )


async def upload_dataset(tenant_id: str, user_id: str, file: UploadFile) -> dict:
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type '{suffix}' not supported. Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )

    content = await file.read()
    size_bytes = len(content)
    _enforce_dataset_size(tenant_id, size_bytes)

    dataset_id = generate_id("ds")

    # Binary file stays on disk
    from backend.storage import paths
    dst_dir = paths.dataset_dir(tenant_id, dataset_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    file_path = dst_dir / f"data{suffix}"
    with open(file_path, "wb") as f:
        f.write(content)

    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, original_filename, file_type, file_path,
            size_bytes, uploaded_by, uploaded_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
        (
            dataset_id,
            tenant_id,
            Path(file.filename).stem,
            file.filename,
            suffix.lstrip("."),
            str(file_path),
            size_bytes,
            user_id,
        ),
    )
    return get_dataset(tenant_id, dataset_id)


def get_dataset(tenant_id: str, dataset_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM datasets WHERE id = %s AND tenant_id = %s",
        (dataset_id, tenant_id),
    )


def list_datasets(tenant_id: str, skip: int = 0, limit: int = 50) -> list[dict]:
    return query(
        "SELECT * FROM datasets WHERE tenant_id = %s ORDER BY uploaded_at DESC LIMIT %s OFFSET %s",
        (tenant_id, limit, skip),
    )


def update_stats(tenant_id: str, dataset_id: str, row_count: int, column_count: int) -> None:
    execute(
        "UPDATE datasets SET row_count = %s, column_count = %s WHERE id = %s AND tenant_id = %s",
        (row_count, column_count, dataset_id, tenant_id),
    )
