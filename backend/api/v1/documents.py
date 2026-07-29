"""
Documents API — upload, list, delete, and status for the document RAG system.

Endpoints:
  POST   /documents                — Upload PDF/DOCX/TXT; starts background indexing
  GET    /documents                — List all documents for the authenticated tenant
  GET    /documents/{doc_id}       — Get single document metadata
  DELETE /documents/{doc_id}       — Delete document + Pinecone vectors
  GET    /documents/{doc_id}/status — Polling endpoint for indexing status

Security:
  - All routes require a valid JWT (get_current_user)
  - Documents are filtered by tenant_id — no cross-tenant access
  - Uploaded files are stored in storage/documents/{tenant_id}/{doc_id}_{filename}
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from backend.auth.guards import (
    CurrentUser, get_current_user, require_analyst_or_above,
)
from backend.db.connection import execute, query, query_one
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.schemas.common import ok

router = APIRouter(
    tags=["documents"],
    dependencies=[Depends(require_feature(Feature.DOCUMENTS_RAG))],
)
log = logging.getLogger(__name__)

ALLOWED_TYPES = {"pdf", "docx", "doc", "txt"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _create_doc(
    doc_id: str, tenant_id: str, user_id: str,
    name: str, original_name: str, file_path: str,
    file_type: str, file_size: int,
) -> None:
    execute(
        """INSERT INTO documents
           (id, tenant_id, uploaded_by, name, original_name, file_path,
            file_type, file_size, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING')""",
        (doc_id, tenant_id, user_id, name, original_name, file_path,
         file_type, file_size),
    )


def _set_status(
    doc_id: str, tenant_id: str, status: str,
    error: str | None = None,
    chunk_count: int | None = None,
    page_count: int | None = None,
) -> None:
    if status == "INDEXED":
        execute(
            """UPDATE documents
               SET status=%s, chunk_count=%s, page_count=%s,
                   indexed_at=NOW(), error=NULL
               WHERE id=%s AND tenant_id=%s""",
            (status, chunk_count, page_count, doc_id, tenant_id),
        )
    elif status == "FAILED":
        execute(
            "UPDATE documents SET status=%s, error=%s WHERE id=%s AND tenant_id=%s",
            (status, error, doc_id, tenant_id),
        )
    else:
        execute(
            "UPDATE documents SET status=%s WHERE id=%s AND tenant_id=%s",
            (status, doc_id, tenant_id),
        )


def _get_doc(doc_id: str, tenant_id: str) -> dict | None:
    return query_one(
        "SELECT * FROM documents WHERE id=%s AND tenant_id=%s", (doc_id, tenant_id)
    )


def _list_docs(tenant_id: str) -> list[dict]:
    return query(
        "SELECT * FROM documents WHERE tenant_id=%s ORDER BY uploaded_at DESC",
        (tenant_id,),
    )


# ── Background indexing ────────────────────────────────────────────────────────

def _index_in_background(
    doc_id: str, tenant_id: str, user_id: str,
    file_path: str, file_type: str, doc_name: str,
) -> None:
    """Runs in a daemon thread — update DB status after indexing completes."""
    try:
        _set_status(doc_id, tenant_id, "INDEXING")

        from backend.ai.document_indexer import doc_indexer
        page_cnt = doc_indexer.page_count(file_path, file_type)
        chunk_cnt = doc_indexer.index_document(
            doc_id=doc_id,
            tenant_id=tenant_id,
            user_id=user_id,
            file_path=file_path,
            file_type=file_type,
            doc_name=doc_name,
        )
        _set_status(doc_id, tenant_id, "INDEXED",
                    chunk_count=chunk_cnt, page_count=page_cnt)
        log.info("Document indexed: doc=%s chunks=%d pages=%d", doc_id, chunk_cnt, page_cnt)

    except Exception as exc:
        log.error("Document indexing failed (doc=%s): %s", doc_id, exc, exc_info=True)
        try:
            _set_status(doc_id, tenant_id, "FAILED", error=str(exc)[:500])
        except Exception:
            pass


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/documents")
async def upload_document(
    file: UploadFile = File(...),
    # A read-only account must not be able to put a 50 MB file into the
    # tenant's storage, or feed the analyst a document nobody vetted.
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """
    Upload a document (PDF, DOCX, TXT up to 50 MB).
    File is saved to storage and indexed asynchronously.
    Returns doc metadata with status=PENDING immediately.
    """
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    ext = Path(file.filename).suffix.lstrip(".").lower()
    if ext not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported file type '.{ext}'. Allowed: {sorted(ALLOWED_TYPES)}")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(400, "File is empty — nothing to index.")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File too large ({len(content)//1024//1024} MB). Max 50 MB.")

    doc_id     = str(uuid.uuid4())
    safe_name  = file.filename.replace("/", "_").replace("\\", "_")
    store_dir  = Path("storage") / "documents" / user.tenant_id
    store_dir.mkdir(parents=True, exist_ok=True)
    file_path  = str(store_dir / f"{doc_id}_{safe_name}")

    Path(file_path).write_bytes(content)

    _create_doc(
        doc_id=doc_id, tenant_id=user.tenant_id, user_id=user.user_id,
        name=file.filename, original_name=file.filename,
        file_path=file_path, file_type=ext, file_size=len(content),
    )

    # Kick off indexing in a background thread
    t = threading.Thread(
        target=_index_in_background,
        args=(doc_id, user.tenant_id, user.user_id, file_path, ext, file.filename),
        daemon=True,
    )
    t.start()

    doc = _get_doc(doc_id, user.tenant_id)
    return ok(_doc_response(doc))


@router.get("/documents")
def list_documents(user: CurrentUser = Depends(get_current_user)):
    docs = _list_docs(user.tenant_id)
    return ok([_doc_response(d) for d in docs])


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    doc = _get_doc(doc_id, user.tenant_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return ok(_doc_response(doc))


@router.get("/documents/{doc_id}/content")
def view_document(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    """Serve the raw file for in-browser viewing or download."""
    doc = _get_doc(doc_id, user.tenant_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    fp = doc.get("file_path")
    if not fp or not Path(fp).exists():
        raise HTTPException(404, "File not found on disk")
    file_type = doc.get("file_type", "").lower()
    if file_type == "txt":
        text = Path(fp).read_text(encoding="utf-8", errors="replace")
        return PlainTextResponse(text)
    media_types = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "doc": "application/msword"}
    media_type = media_types.get(file_type, "application/octet-stream")
    return FileResponse(fp, media_type=media_type, filename=doc.get("name", Path(fp).name))


@router.get("/documents/{doc_id}/status")
def get_document_status(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    doc = _get_doc(doc_id, user.tenant_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return ok({
        "doc_id":      doc_id,
        "status":      doc["status"],
        "chunk_count": doc.get("chunk_count"),
        "page_count":  doc.get("page_count"),
        "error":       doc.get("error"),
    })


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    doc = _get_doc(doc_id, user.tenant_id)
    if not doc:
        raise HTTPException(404, "Document not found")

    # Remove Pinecone vectors
    from backend.ai.document_indexer import doc_indexer
    doc_indexer.delete_document(doc_id, user.tenant_id)

    # Remove file from disk
    try:
        fp = doc.get("file_path")
        if fp and Path(fp).exists():
            Path(fp).unlink()
    except Exception as exc:
        log.warning("Could not delete file for doc %s: %s", doc_id, exc)

    execute(
        "DELETE FROM documents WHERE id=%s AND tenant_id=%s",
        (doc_id, user.tenant_id),
    )
    return ok({"deleted": doc_id})


# ── Serialiser ─────────────────────────────────────────────────────────────────

def _doc_response(doc: dict) -> dict:
    if not doc:
        return {}
    return {
        "id":            doc["id"],
        "name":          doc["name"],
        "original_name": doc.get("original_name") or doc["name"],
        "file_type":     doc["file_type"],
        "file_size":     doc["file_size"],
        "status":        doc["status"],
        "chunk_count":   doc.get("chunk_count"),
        "page_count":    doc.get("page_count"),
        "error":         doc.get("error"),
        "uploaded_by":   doc.get("uploaded_by"),
        "uploaded_at":   doc["uploaded_at"].isoformat() if doc.get("uploaded_at") else None,
        "indexed_at":    doc["indexed_at"].isoformat() if doc.get("indexed_at") else None,
    }
