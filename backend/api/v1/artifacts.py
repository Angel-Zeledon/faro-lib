from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.auth.guards import CurrentUser, get_current_user
from backend.schemas.common import ok
from backend.sessions import service as session_svc
from backend.storage import paths

router = APIRouter(tags=["artifacts"])

_TYPE_MAP = {
    ".pkl": "model", ".joblib": "model",
    ".png": "plot", ".jpg": "plot", ".svg": "plot",
    ".xlsx": "report", ".xls": "report",
    ".pdf": "report",
    ".log": "log",
    ".json": "metrics",
    ".csv": "data",
}


@router.get("/sessions/{session_id}/artifacts")
def list_artifacts(session_id: str, user: CurrentUser = Depends(get_current_user)):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    artifact_dir = paths.artifacts_dir(user.tenant_id, session_id)
    items = []
    if artifact_dir.exists():
        for p in artifact_dir.rglob("*"):
            if p.is_file():
                items.append({
                    "name": p.name,
                    "relative_path": str(p.relative_to(artifact_dir)).replace("\\", "/"),
                    "size_bytes": p.stat().st_size,
                    "type": _TYPE_MAP.get(p.suffix.lower(), "other"),
                })
    return ok({"artifacts": items, "total": len(items)})


@router.get("/sessions/{session_id}/artifacts/download/{artifact_path:path}")
def download_artifact(
    session_id: str,
    artifact_path: str,
    user: CurrentUser = Depends(get_current_user),
):
    s = session_svc.get_session(user.tenant_id, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    base = paths.artifacts_dir(user.tenant_id, session_id).resolve()
    full = (base / artifact_path).resolve()

    # Path traversal guard
    if not str(full).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Access denied")

    if not full.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(full, filename=full.name)
