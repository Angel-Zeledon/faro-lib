"""The registry of persisted models: which files a session's models live in.

The engine (`forecasting_core.reforecast`) decides WHAT is stored and in which
format; this module decides WHERE and keeps the books. No pandas, no ML here.

Layout, under the tenant's storage root and never in git::

    storage/artifacts/<tenant>/<session>/models/<family>.<hash16>.json.gz

and one row per file in `model_artifacts`, scoped by `tenant_id` in every query.

**Integrity.** Every file is registered with the SHA-256 of its bytes, and the
same digests are written into the immutable `session_manifests` row of the
training that produced them. `load_verified` re-hashes each file and compares it
with BOTH records before handing the bytes to the engine, so a file that was
edited on disk, or swapped for another model, is refused as
`artifact_integrity_failed` instead of being parsed. See the engine's
`reforecast.codec` for the format (JSON, no pickle) and what trust remains.

**Inheritance.** A re-forecast session does not copy its parent's models; it
registers the same files under its own id with `inherited_from_session_id`
pointing at the session whose training produced them. A chain of daily
re-forecasts therefore keeps one set of models on disk, and every session in the
chain can be re-forecast from. Archived sessions keep their rows (sessions are
permanent).
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Iterable, Optional

from backend.db.connection import _json, execute, query, query_one
from backend.errors import AppError
from backend.storage import paths
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

CONTEXT_FAMILY = "context"


def _tenant_root(tenant_id: str) -> Path:
    return paths.artifacts_dir(tenant_id, "x").parent.resolve()


def save_artifact_set(tenant_id: str, session_id: str, files: Iterable[Any]) -> list[dict]:
    """Write each file of an engine `ArtifactSet` and register it.

    `files` are objects with `family`, `kind`, `data`, `sha256`, `size_bytes` and
    `metadata`. Re-registering a family for the same session (a session trained
    again) replaces the row and bumps `version`. Returns the summaries that go
    into the lineage manifest."""
    base = paths.models_artifact_dir(tenant_id, session_id)
    base.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for f in files:
        name = f"{f.family}.{f.sha256[:16]}.json.gz"
        target = base / name
        tmp = base / (name + ".tmp")
        with open(tmp, "wb") as handle:
            handle.write(f.data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        row = query_one(
            """INSERT INTO model_artifacts
                 (id, tenant_id, session_id, family, kind, version, format,
                  storage_path, content_hash, size_bytes, metadata)
               VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, session_id, family) DO UPDATE SET
                  kind = EXCLUDED.kind, version = model_artifacts.version + 1,
                  format = EXCLUDED.format, storage_path = EXCLUDED.storage_path,
                  content_hash = EXCLUDED.content_hash, size_bytes = EXCLUDED.size_bytes,
                  metadata = EXCLUDED.metadata, inherited_from_session_id = NULL,
                  created_at = NOW()
               RETURNING version""",
            (generate_id("art"), tenant_id, session_id, f.family, f.kind,
             (f.metadata or {}).get("format") or "stockai-artifact/json+gzip/v1",
             str(target), f.sha256, int(f.size_bytes), _json(f.metadata or {})),
        )
        out.append({
            "family": f.family, "kind": f.kind, "sha256": f.sha256,
            "size_bytes": int(f.size_bytes), "version": (row or {}).get("version", 1),
        })
    return out


def list_artifacts(tenant_id: str, session_id: str) -> list[dict]:
    rows = query(
        "SELECT * FROM model_artifacts WHERE tenant_id = %s AND session_id = %s "
        "ORDER BY (family = 'context') DESC, family",
        (tenant_id, session_id),
    ) or []
    return [dict(r) for r in rows]


def has_artifacts(tenant_id: str, session_id: str) -> bool:
    rows = list_artifacts(tenant_id, session_id)
    return any(r["family"] == CONTEXT_FAMILY for r in rows) and len(rows) > 1


def inherit_artifacts(tenant_id: str, child_session_id: str, parent_session_id: str) -> int:
    """Register the parent's files under the child. Returns how many."""
    n = 0
    for row in list_artifacts(tenant_id, parent_session_id):
        origin = row.get("inherited_from_session_id") or parent_session_id
        execute(
            """INSERT INTO model_artifacts
                 (id, tenant_id, session_id, family, kind, version, format,
                  storage_path, content_hash, size_bytes, metadata,
                  inherited_from_session_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, session_id, family) DO NOTHING""",
            (generate_id("art"), tenant_id, child_session_id, row["family"], row["kind"],
             row["version"], row["format"], row["storage_path"], row["content_hash"],
             row["size_bytes"], _json(row["metadata"] or {}), origin),
        )
        n += 1
    return n


def origin_session_id(tenant_id: str, session_id: str) -> Optional[str]:
    """The session whose full training produced this session's models."""
    rows = list_artifacts(tenant_id, session_id)
    if not rows:
        return None
    return rows[0].get("inherited_from_session_id") or session_id


def _recorded_hashes(tenant_id: str, origin_session_id_: str) -> Optional[dict]:
    """{family: sha256} from the origin training's immutable manifest, or None
    when no manifest recorded artifacts (the manifest write is best-effort)."""
    row = query_one(
        """SELECT manifest->'model_artifacts' AS arts FROM session_manifests
            WHERE tenant_id = %s AND session_id = %s AND outcome = 'COMPLETED'
              AND manifest ? 'model_artifacts'
            ORDER BY created_at DESC LIMIT 1""",
        (tenant_id, origin_session_id_),
    )
    if not row or not row.get("arts"):
        return None
    return {a["family"]: a["sha256"] for a in row["arts"]}


def load_verified(tenant_id: str, session_id: str) -> list[tuple[str, bytes, str]]:
    """`(family, bytes, sha256)` for every file of the session, each one checked
    against the registry row AND the manifest before it is returned.

    Raises `AppError`: `artifacts_not_found` (nothing registered / file gone) or
    `artifact_integrity_failed` (bytes differ from a recorded digest, or the path
    leaves the tenant's storage)."""
    rows = list_artifacts(tenant_id, session_id)
    if not rows:
        raise AppError(
            "artifacts_not_found",
            "This session has no stored models to re-forecast from.",
            status_code=409, params={"session_id": session_id})
    origin = rows[0].get("inherited_from_session_id") or session_id
    recorded = _recorded_hashes(tenant_id, origin)
    root = _tenant_root(tenant_id)
    out: list[tuple[str, bytes, str]] = []
    for row in rows:
        path = Path(row["storage_path"]).resolve()
        if root not in path.parents:
            raise AppError(
                "artifact_integrity_failed", "A stored model is outside this tenant's storage.",
                status_code=422, params={"family": row["family"]})
        try:
            data = path.read_bytes()
        except OSError:
            raise AppError(
                "artifacts_not_found", "A stored model file is missing.",
                status_code=409, params={"session_id": session_id, "family": row["family"]})
        digest = hashlib.sha256(data).hexdigest()
        expected = {row["content_hash"]}
        if recorded and row["family"] in recorded:
            expected.add(recorded[row["family"]])
        if expected != {digest}:
            log.error("artifact %s of session %s failed its integrity check",
                      row["family"], session_id)
            raise AppError(
                "artifact_integrity_failed",
                "A stored model no longer matches its recorded content hash.",
                status_code=422, params={"family": row["family"]})
        out.append((row["family"], data, digest))
    return out


def manifest_summary(tenant_id: str, session_id: str) -> list[dict]:
    """What the lineage manifest records about a session's artifacts."""
    return [{
        "family": r["family"], "kind": r["kind"], "sha256": r["content_hash"],
        "size_bytes": r["size_bytes"], "version": r["version"],
        "origin_session_id": r.get("inherited_from_session_id") or session_id,
    } for r in list_artifacts(tenant_id, session_id)]
