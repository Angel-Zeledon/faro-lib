"""Guided upload, session side: preview the guide's report, apply its fixes.

Two rules carry this module:

* The ORIGINAL file is never modified. Applying the fixes writes a NEW dataset
  (``parent_id`` = the original, like the dataset editor's save-as-new) and
  re-attaches the session to it. Reverting re-attaches the original.
* The transformation is part of the session's record. The exact fix list, the
  answers that produced it and the row counts are stored in
  ``dataset_ref.guided_reading`` and copied into the lineage manifest of every
  run trained on the cleaned file, so "what was done to this file" is never a
  recollection.

Pure orchestration: every read of the file goes through ``backend.dataframes``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from backend.datasets.service import get_dataset
from backend.db import session_store
from backend.db.connection import execute
from backend.errors import AppError
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

MAX_DECISIONS_BYTES = 8_192


def parse_json_param(raw: Optional[str], name: str) -> Optional[dict]:
    """A JSON object passed in a query string. Bounded, and a bad value is an
    error the caller sees, never silently an empty answer."""
    if raw is None or raw == "":
        return None
    if len(raw) > MAX_DECISIONS_BYTES:
        raise AppError("guided_reading_invalid", f"'{name}' is too large.", status_code=422,
                       params={"field": name})
    try:
        value = json.loads(raw)
    except ValueError:
        raise AppError("guided_reading_invalid", f"'{name}' is not valid JSON.",
                       status_code=422, params={"field": name})
    if not isinstance(value, dict):
        raise AppError("guided_reading_invalid", f"'{name}' must be a JSON object.",
                       status_code=422, params={"field": name})
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dataset_path(tenant_id: str, dataset_id: str, session: dict) -> str:
    ds = get_dataset(tenant_id, dataset_id)
    if not ds or not os.path.exists(ds.get("file_path") or ""):
        raise AppError(
            "dataset_file_missing",
            f"The data file for session '{session['name']}' is no longer on disk. "
            "Re-upload it before continuing.",
            status_code=422, params={"session": session["name"]})
    return ds["file_path"]


def applied(tenant_id: str, session_id: str) -> Optional[dict]:
    """The stored record of a previous application, or None."""
    ref = session_store.get_field(tenant_id, session_id, "dataset_ref") or {}
    return ref.get("guided_reading")


def source_dataset_id(tenant_id: str, session: dict) -> str:
    """The file the person uploaded: the original behind a cleaned copy."""
    record = applied(tenant_id, session["id"])
    return (record or {}).get("source_dataset_id") or session["dataset_id"]


def preview(tenant_id: str, session: dict, decisions: Optional[dict],
            mapping: Optional[dict]) -> dict:
    """The guide's report over the ORIGINAL file with the answers so far."""
    from backend.dataframes.guided import GuidedReadError, analyze_file

    if not session.get("dataset_id"):
        raise AppError("session_no_dataset", "No dataset attached.", status_code=400)
    path = _dataset_path(tenant_id, source_dataset_id(tenant_id, session), session)
    try:
        return analyze_file(path, decisions or {}, mapping=mapping)
    except GuidedReadError as exc:
        raise AppError("guided_reading_unreadable",
                       f"The file could not be read as a table: {exc}",
                       status_code=422, params={"reason": str(exc)})


def _slim(findings: list[dict]) -> list[dict]:
    return [{"code": f["code"], "severity": f["severity"], "check": f["check"],
             "column": f.get("column"), "params": f.get("params") or {}}
            for f in findings if f["severity"] in ("auto", "info")]


def apply(tenant_id: str, user_id: str, session: dict, decisions: Optional[dict],
          mapping: Optional[dict], revert: bool = False) -> dict:
    """Apply the fixes (or revert them). Returns ``{"report", "applied", "dataset_id"}``."""
    from backend.dataframes.guided import GuidedReadError, write_cleaned
    from backend.sessions import service as session_svc
    from backend.storage import paths

    session_id = session["id"]
    if not session.get("dataset_id"):
        raise AppError("session_no_dataset", "No dataset attached.", status_code=400)
    source_id = source_dataset_id(tenant_id, session)

    if revert:
        if session["dataset_id"] != source_id:
            session_svc.attach_dataset(tenant_id, session_id, source_id)
            session_store.clear_field(tenant_id, session_id, "inspection")
            session_store.clear_field(tenant_id, session_id, "columns_cfg")
        report = preview(tenant_id, {**session, "dataset_id": source_id}, decisions, mapping)
        return {"report": report, "applied": None, "dataset_id": source_id}

    report = preview(tenant_id, session, decisions, mapping)
    if report["verdict"] != "ready":
        pending = [f["code"] for f in report["findings"] if f["severity"] in ("ask", "block")]
        raise AppError(
            "guided_reading_not_ready",
            "This file still has open questions or problems, so nothing was changed: "
            + ", ".join(pending) + ".",
            status_code=409, params={"verdict": report["verdict"], "pending": ", ".join(pending)})

    if not report["fixes"]:
        # Nothing to change. If an earlier application is still attached,
        # go back to the original rather than keep a now-pointless copy.
        if session["dataset_id"] != source_id:
            session_svc.attach_dataset(tenant_id, session_id, source_id)
            session_store.clear_field(tenant_id, session_id, "inspection")
            session_store.clear_field(tenant_id, session_id, "columns_cfg")
        return {"report": report, "applied": None, "dataset_id": source_id}

    src = get_dataset(tenant_id, source_id)
    path = _dataset_path(tenant_id, source_id, session)
    new_id = generate_id("ds")
    dst_dir = paths.dataset_dir(tenant_id, new_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "data.xlsx"
    try:
        counts = write_cleaned(path, report["fixes"], str(dst))
    except GuidedReadError as exc:
        raise AppError("guided_reading_unreadable",
                       f"The file could not be cleaned: {exc}", status_code=422,
                       params={"reason": str(exc)})

    name = f"{src.get('name')} (guided)"
    execute(
        """INSERT INTO datasets
           (id, tenant_id, name, original_filename, file_type, file_path, size_bytes,
            row_count, column_count, source_type, connection_status, parent_id,
            uploaded_by, uploaded_at, updated_at)
           VALUES (%s,%s,%s,%s,'xlsx',%s,%s,%s,%s,'file','connected',%s,%s,NOW(),NOW())""",
        (new_id, tenant_id, name, f"{name}.xlsx", str(dst), dst.stat().st_size,
         counts["rows"], counts["columns"], source_id, user_id),
    )
    session_svc.attach_dataset(tenant_id, session_id, new_id)

    record = {
        "version": report["version"],
        "source_dataset_id": source_id,
        "derived_dataset_id": new_id,
        "fixes": report["fixes"],
        "decisions": decisions or {},
        "mapping": report["mapping"],
        "findings": _slim(report["findings"]),
        "rows_in": counts["rows_in"],
        "rows_out": counts["rows"],
        "applied_at": _now(),
        "applied_by": user_id,
    }
    ref = dict(session_store.get_field(tenant_id, session_id, "dataset_ref") or {})
    ref["guided_reading"] = record
    session_store.set_field(tenant_id, session_id, "dataset_ref", ref)
    # The file under the session changed: the cached inspection and any mapping
    # confirmed against the old columns describe a different table.
    session_store.clear_field(tenant_id, session_id, "inspection")
    session_store.clear_field(tenant_id, session_id, "columns_cfg")
    return {"report": report, "applied": record, "dataset_id": new_id}
