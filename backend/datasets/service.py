from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from backend.config import settings
from backend.db.connection import query_one, query, execute
from backend.utils.ids import generate_id

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".parquet", ".xls", ".json"}


async def upload_dataset(tenant_id: str, user_id: str, file: UploadFile) -> dict:
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type '{suffix}' not supported. Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )

    content = await file.read()
    size_bytes = len(content)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if not settings.testing_mode and size_bytes > max_bytes:
        raise ValueError(
            f"File size {size_bytes / 1024 / 1024:.1f} MB exceeds "
            f"limit of {settings.max_upload_size_mb} MB"
        )

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
