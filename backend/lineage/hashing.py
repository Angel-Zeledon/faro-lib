"""Content hashes for the files and payloads a forecast is built from.

Pure standard library: the backend never reads a dataset into pandas just to
ask whether it changed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from backend.db.connection import query_one

_CHUNK = 1024 * 1024


def file_sha256(path: str | Path) -> str:
    """SHA-256 of a file's bytes, streamed so a 2 GB upload costs no memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def dataset_content_hash(tenant_id: str, dataset_id: str) -> Optional[str]:
    """Hash of the dataset's file as it is on disk right now, or None when the
    row or the file is gone (the caller decides what an unknown hash means)."""
    row = query_one(
        "SELECT file_path FROM datasets WHERE id = %s AND tenant_id = %s",
        (dataset_id, tenant_id),
    )
    if not row or not row.get("file_path"):
        return None
    path = Path(row["file_path"])
    if not path.is_file():
        return None
    try:
        return file_sha256(path)
    except OSError:
        return None


def json_sha256(payload: Any) -> str:
    """Stable hash of a JSON-able payload: keys sorted, no whitespace, floats
    rendered by `json` so the same numbers always hash the same."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
