"""File-drop sources: a company drops a file in a directory or bucket and the
data it feeds follows, with no wizard and nobody uploading by hand.

What it does with a file, in order, and why each step exists:

1. **Finished uploading.** A file is read only once two looks agree on its size
   and modification time (and it is a minute old). A file still growing is not
   read half-written.
2. **Never twice.** Each (source, name, content hash) is decided once. The same
   bytes dropped again, or listed again because a move failed, are skipped.
3. **Plausible.** Before anything is replaced the file is compared with the data
   it replaces (`core.judge`): empty, different columns, or far fewer rows is
   HELD for a person instead of silently erasing the history a forecast reads.
4. **Applied through the existing replace path** (`datasources.replace_file_source`),
   so the plan's size ceiling, the saved column mapping, the move of the old file
   to `previous/` and the retrain-on-change schedule all behave exactly as for a
   manual upload. A refusal there rejects the file with its code.
5. **Moved, never deleted.** The file goes to `processed/` or `rejected/` beside
   it (with a `.reason.txt` note for a rejection). A move the account is not
   allowed to make is reported, not hidden: the ledger still prevents a re-apply.

Read and move are the only things done to the company's server. Credentials are
encrypted at rest and never returned.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from backend.activity.events import record_event
from backend.datasources import network
from backend.datasources import secrets as ds_secrets
from backend.db.connection import _json, execute, query, query_one
from backend.errors import AppError
from backend.file_drop import core
from backend.file_drop.transports import S3Transport, SftpTransport, Transport

log = logging.getLogger(__name__)

KINDS = ("sftp", "s3")
MAX_SOURCES_PER_TENANT = 10
LEASE_MINUTES = 20
MAX_NAME_LENGTH = 100
_STALE_APPLYING = timedelta(minutes=30)

STATUS_NEW, STATUS_OK, STATUS_ERROR, STATUS_BLOCKED = "new", "ok", "error", "blocked"
# Failures that retrying will not fix: the admin has to act, so the source stops
# (retrying a wrong password would lock the company's account).
_BLOCKING = {
    "file_drop_auth_failed", "file_drop_host_key_changed", "file_drop_host_key_unpinned",
    "file_drop_path_not_found", "file_drop_permission_denied", "file_drop_key_unreadable",
    "data_source_host_refused", "file_drop_target_missing",
}


def _bad(code: str, message: str, status: int = 400, **params) -> AppError:
    return AppError(code, message, status_code=status, params=params)


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


# ── Source CRUD ──────────────────────────────────────────────────────────────

def _clean_config(kind: str, cfg: dict) -> dict:
    cfg = cfg or {}
    if kind == "sftp":
        host = str(cfg.get("host") or "").strip()
        user = str(cfg.get("username") or "").strip()
        if not host or len(host) > 253 or not user:
            raise _bad("file_drop_config_invalid", "An SFTP source needs a host and a user",
                       field="host" if not host else "username")
        try:
            port = int(cfg.get("port") or 22)
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            raise _bad("file_drop_config_invalid", "The port is not valid", field="port")
        path = str(cfg.get("path") or ".").strip() or "."
        if ".." in path.split("/"):
            raise _bad("file_drop_config_invalid", "The directory cannot go up", field="path")
        network.check_literal(host, allow_private=network.allow_private_hosts())
        return {"host": host, "port": port, "username": user, "path": path}
    bucket = str(cfg.get("bucket") or "").strip()
    if not bucket or len(bucket) > 255 or "/" in bucket:
        raise _bad("file_drop_config_invalid", "An S3 source needs a bucket name", field="bucket")
    endpoint = str(cfg.get("endpoint") or "").strip().rstrip("/")
    if endpoint:
        from urllib.parse import urlsplit
        parts = urlsplit(endpoint)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise _bad("file_drop_config_invalid", "The endpoint must be an http(s) address",
                       field="endpoint")
        network.check_literal(parts.hostname, allow_private=network.allow_private_hosts())
    return {
        "bucket": bucket, "endpoint": endpoint or None,
        "region": str(cfg.get("region") or "us-east-1").strip(),
        "prefix": str(cfg.get("prefix") or "").strip().strip("/"),
    }


def _clean_secrets(kind: str, raw: Optional[dict], required: bool) -> Optional[dict]:
    raw = raw or {}
    if kind == "sftp":
        out = {k: raw[k] for k in ("password", "private_key", "passphrase") if raw.get(k)}
        if required and not (out.get("password") or out.get("private_key")):
            raise _bad("file_drop_config_invalid", "Give a password or a private key",
                       field="password")
    else:
        out = {k: raw[k] for k in ("access_key", "secret_key") if raw.get(k)}
        if required and len(out) < 2:
            raise _bad("file_drop_config_invalid", "Give the access key and the secret key",
                       field="access_key")
    return out or None


def _check_pattern(pattern: Optional[str]) -> str:
    err = core.pattern_error(pattern)
    if err:
        raise _bad("file_drop_pattern_invalid", "The file name pattern is not valid", reason=err)
    return (pattern or "*.csv").strip() or "*.csv"


def _check_target(tenant_id: str, dataset_id: Optional[str]) -> Optional[str]:
    if not dataset_id:
        return None
    row = query_one("SELECT source_type FROM datasets WHERE id = %s AND tenant_id = %s",
                    (dataset_id, tenant_id))
    if not row:
        raise _bad("file_drop_target_missing", "That dataset does not exist", 404)
    if row["source_type"] != "file":
        raise _bad("file_drop_target_not_file", "Only a file dataset can be fed by a file drop")
    return dataset_id


def public(row: dict) -> dict:
    cfg = row.get("config") or {}
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"], "config": cfg,
        "has_secret": bool(row.get("secrets")),
        "host_key_fingerprint": row.get("host_key_fingerprint"),
        "host_key_pinned_at": _iso(row.get("host_key_pinned_at")),
        "target_dataset_id": row.get("target_dataset_id"),
        "file_pattern": row["file_pattern"], "interval_minutes": row["interval_minutes"],
        "min_age_seconds": row["min_age_seconds"], "enabled": row["enabled"],
        "status": row["status"], "last_checked_at": _iso(row.get("last_checked_at")),
        "last_ok_at": _iso(row.get("last_ok_at")),
        "last_error_code": row.get("last_error_code"),
        "last_error_params": row.get("last_error_params"),
        "created_at": _iso(row.get("created_at")),
    }


def get_source(tenant_id: str, source_id: str) -> dict:
    row = query_one("SELECT * FROM file_drop_sources WHERE id = %s AND tenant_id = %s",
                    (source_id, tenant_id))
    if not row:
        raise _bad("file_drop_not_found", "File drop source not found", 404)
    return row


def list_sources(tenant_id: str) -> list[dict]:
    return [public(r) for r in query(
        "SELECT * FROM file_drop_sources WHERE tenant_id = %s ORDER BY created_at", (tenant_id,))]


def create_source(tenant_id: str, user_id: str, body: dict) -> dict:
    kind = body.get("kind")
    if kind not in KINDS:
        raise _bad("file_drop_config_invalid", "The kind must be sftp or s3", field="kind")
    name = str(body.get("name") or "").strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise _bad("file_drop_config_invalid", "Give the source a name", field="name")
    if query_one("SELECT COUNT(*) AS n FROM file_drop_sources WHERE tenant_id = %s",
                 (tenant_id,))["n"] >= MAX_SOURCES_PER_TENANT:
        raise _bad("file_drop_too_many_sources", "The limit of file drop sources was reached",
                   409, max=MAX_SOURCES_PER_TENANT)
    if query_one("SELECT 1 AS x FROM file_drop_sources WHERE tenant_id = %s AND name = %s",
                 (tenant_id, name)):
        raise _bad("file_drop_name_taken", "A source with that name already exists", 409)
    cfg = _clean_config(kind, body.get("config") or {})
    secrets = _clean_secrets(kind, body.get("secrets"), required=True)
    interval = int(body.get("interval_minutes") or 15)
    min_age = int(body.get("min_age_seconds") if body.get("min_age_seconds") is not None else 60)
    if interval < 5 or min_age < 0:
        raise _bad("file_drop_config_invalid", "The interval is at least 5 minutes",
                   field="interval_minutes")
    row = query_one(
        """INSERT INTO file_drop_sources
               (tenant_id, name, kind, config, secrets, target_dataset_id, file_pattern,
                interval_minutes, min_age_seconds, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (tenant_id, name, kind, _json(cfg),
         ds_secrets.encrypt(json.dumps(secrets)) if secrets else None,
         _check_target(tenant_id, body.get("target_dataset_id")),
         _check_pattern(body.get("file_pattern")), interval, min_age, user_id))
    return public(row)


def update_source(tenant_id: str, source_id: str, body: dict) -> dict:
    row = get_source(tenant_id, source_id)
    sets: dict[str, Any] = {}
    if "name" in body:
        name = str(body["name"] or "").strip()
        if not name or len(name) > MAX_NAME_LENGTH:
            raise _bad("file_drop_config_invalid", "Give the source a name", field="name")
        if query_one("SELECT 1 AS x FROM file_drop_sources WHERE tenant_id = %s AND name = %s "
                     "AND id <> %s", (tenant_id, name, source_id)):
            raise _bad("file_drop_name_taken", "A source with that name already exists", 409)
        sets["name"] = name
    if "config" in body:
        new_cfg = _clean_config(row["kind"], {**(row["config"] or {}), **(body["config"] or {})})
        old = row["config"] or {}
        # Pointing the source at another server forgets the pinned key: trusting
        # the old server's key for a new host would defeat the pin.
        if row["kind"] == "sftp" and (new_cfg["host"], new_cfg["port"]) != (
                old.get("host"), old.get("port")):
            sets["host_key_fingerprint"] = None
            sets["host_key_pinned_at"] = None
        sets["config"] = _json(new_cfg)
    if body.get("secrets"):
        merged = {}
        if row.get("secrets"):
            merged = json.loads(ds_secrets.decrypt(row["secrets"]))
        merged.update(_clean_secrets(row["kind"], body["secrets"], required=False) or {})
        sets["secrets"] = ds_secrets.encrypt(json.dumps(merged))
    if "target_dataset_id" in body:
        sets["target_dataset_id"] = _check_target(tenant_id, body["target_dataset_id"])
    if "file_pattern" in body:
        sets["file_pattern"] = _check_pattern(body["file_pattern"])
    if "interval_minutes" in body:
        if int(body["interval_minutes"]) < 5:
            raise _bad("file_drop_config_invalid", "The interval is at least 5 minutes",
                       field="interval_minutes")
        sets["interval_minutes"] = int(body["interval_minutes"])
    if "enabled" in body:
        sets["enabled"] = bool(body["enabled"])
    if not sets:
        return public(row)
    # An edit is the admin acting on whatever stopped the source: try again soon.
    if row["status"] in (STATUS_BLOCKED, STATUS_ERROR):
        sets.update(status=STATUS_NEW, last_error_code=None, last_error_params=None)
    sets["next_run_at"] = datetime.now(timezone.utc)
    cols = ", ".join(f"{k} = %s" for k in sets)
    updated = query_one(
        f"UPDATE file_drop_sources SET {cols} WHERE id = %s AND tenant_id = %s RETURNING *",
        (*sets.values(), source_id, tenant_id))
    return public(updated)


def delete_source(tenant_id: str, source_id: str) -> None:
    get_source(tenant_id, source_id)
    execute("DELETE FROM file_drop_sources WHERE id = %s AND tenant_id = %s",
            (source_id, tenant_id))


# ── Connecting ───────────────────────────────────────────────────────────────

def open_transport(row: dict, *, trust_new_key: bool = False) -> Transport:
    """Open the source. An SFTP server whose key is not pinned yet is refused
    (`file_drop_host_key_unpinned`) unless `trust_new_key` (the admin's test)."""
    secrets = json.loads(ds_secrets.decrypt(row["secrets"])) if row.get("secrets") else {}
    cfg = row.get("config") or {}
    if row["kind"] == "s3":
        return S3Transport(cfg, secrets)
    pinned = row.get("host_key_fingerprint")
    transport = SftpTransport(cfg, secrets, pinned)
    if not pinned and not trust_new_key:
        transport.close()
        raise _bad("file_drop_host_key_unpinned",
                   "Test the connection first so an admin can confirm the server's key", 409)
    return transport


def test_connection(tenant_id: str, source_id: str) -> dict:
    """List the watched location without reading or changing anything, pin the
    SFTP host key on first use, and say what the account is allowed to do."""
    row = get_source(tenant_id, source_id)
    transport = open_transport(row, trust_new_key=True)
    try:
        newly_pinned = False
        fp = getattr(transport, "observed_fingerprint", None)
        if row["kind"] == "sftp" and fp and not row.get("host_key_fingerprint"):
            execute("UPDATE file_drop_sources SET host_key_fingerprint = %s, "
                    "host_key_pinned_at = NOW() WHERE id = %s", (fp, source_id))
            newly_pinned = True
        files = transport.list_files()
    finally:
        transport.close()
    patterns = core.parse_patterns(row["file_pattern"])
    matched = [f.name for f in files if core.selects(patterns, f.name)]
    execute("UPDATE file_drop_sources SET status = CASE WHEN status IN ('blocked','error') "
            "THEN 'new' ELSE status END, last_error_code = NULL, last_error_params = NULL, "
            "next_run_at = NOW() WHERE id = %s", (source_id,))
    return {
        "ok": True, "files_seen": len(files), "files_matching": len(matched),
        "sample": sorted(matched)[:20],
        "host_key": {"fingerprint": fp, "newly_pinned": newly_pinned} if fp else None,
        "kind": row["kind"],
        # Rendered by the screen through i18n; the permissions the poller needs.
        "least_privilege": ["list", "read", "move"] if row["kind"] == "sftp"
        else ["list", "get", "put", "delete"],
    }


def accept_host_key(tenant_id: str, source_id: str, fingerprint: str) -> dict:
    row = get_source(tenant_id, source_id)
    if row["kind"] != "sftp":
        raise _bad("file_drop_config_invalid", "Only SFTP sources have a host key")
    if not fingerprint.startswith("SHA256:"):
        raise _bad("file_drop_config_invalid", "That is not a key fingerprint", field="fingerprint")
    updated = query_one(
        """UPDATE file_drop_sources SET host_key_fingerprint = %s, host_key_pinned_at = NOW(),
                  status = 'new', last_error_code = NULL, last_error_params = NULL,
                  next_run_at = NOW()
            WHERE id = %s AND tenant_id = %s RETURNING *""",
        (fingerprint, source_id, tenant_id))
    return public(updated)


# ── Inspecting a file ────────────────────────────────────────────────────────

def inspect_content(suffix: str, content: bytes, *, header_only: bool = False) -> core.Snapshot:
    """Rows and column names of a table without loading it into a DataFrame."""
    try:
        if suffix == ".csv":
            text = content.decode("utf-8-sig") if _is_utf8(content) else content.decode("latin-1")
            reader = csv.reader(io.StringIO(text))
            header = next(reader, None)
            if header is None:
                return core.Snapshot(0, ())
            cols = tuple(c.strip() for c in header)
            if header_only:
                return core.Snapshot(None, cols)
            return core.Snapshot(sum(1 for r in reader if any(c.strip() for c in r)), cols)
        if suffix == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                ws = wb.worksheets[0]
                head = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
                cols = tuple(str(c).strip() for c in (head or ()) if c is not None)
                rows = None if header_only else max(0, (ws.max_row or 1) - 1)
                return core.Snapshot(rows, cols)
            finally:
                wb.close()
        if suffix == ".parquet":
            import pyarrow.parquet as pq
            meta = pq.read_metadata(io.BytesIO(content))
            names = tuple(meta.schema.names)
            return core.Snapshot(None if header_only else meta.num_rows, names)
        if suffix == ".json":
            data = json.loads(content)
            if isinstance(data, list):
                cols = tuple(data[0].keys()) if data and isinstance(data[0], dict) else ()
                return core.Snapshot(len(data), cols)
        return core.Snapshot(None, ())
    except Exception as exc:
        raise _bad("file_drop_file_unreadable", "The file could not be read as a table",
                   detail=type(exc).__name__) from exc


def _is_utf8(content: bytes) -> bool:
    try:
        content.decode("utf-8-sig")
        return True
    except UnicodeDecodeError:
        return False


def _existing_snapshot(tenant_id: str, dataset_id: Optional[str]) -> Optional[core.Snapshot]:
    """What the target holds now: its stored row count and its file's columns."""
    if not dataset_id:
        return None
    row = query_one("SELECT file_path, file_type, row_count FROM datasets "
                    "WHERE id = %s AND tenant_id = %s", (dataset_id, tenant_id))
    if not row or not row.get("file_path") or not Path(row["file_path"]).exists():
        return None
    try:
        cols = inspect_content("." + (row["file_type"] or ""),
                               Path(row["file_path"]).read_bytes(), header_only=True).columns
    except AppError:
        cols = ()
    return core.Snapshot(row.get("row_count"), cols)


# ── Applying ─────────────────────────────────────────────────────────────────

def _plan_limit_bytes() -> int:
    from backend.datasources.service import MAX_FILE_MB
    return MAX_FILE_MB * 1024 * 1024


def _apply(tenant_id: str, row: dict, name: str, content: bytes) -> str:
    """Hand the bytes to the existing replace/create path. Returns the dataset id."""
    from fastapi import UploadFile
    from backend.datasources import service as ds

    user_id = row.get("created_by") or "system"
    upload = UploadFile(file=io.BytesIO(content), filename=name)
    target = row.get("target_dataset_id")
    if target:
        if not query_one("SELECT 1 AS x FROM datasets WHERE id = %s AND tenant_id = %s",
                         (target, tenant_id)):
            raise _bad("file_drop_target_missing", "The dataset this source feeds was deleted", 409)
        asyncio.run(ds.replace_file_source(tenant_id, user_id, target, upload))
        return target
    created = asyncio.run(ds.create_file_source(
        tenant_id, user_id, upload, name=row["name"],
        description="Created by file drop"))
    execute("UPDATE file_drop_sources SET target_dataset_id = %s WHERE id = %s",
            (created["id"], row["id"]))
    row["target_dataset_id"] = created["id"]
    return created["id"]


def _try_move(transport: Transport, name: str, folder: str, note: Optional[str] = None) -> bool:
    try:
        if note:
            transport.put_text(folder, f"{name}.reason.txt", note)
        transport.move(name, folder)
        return True
    except Exception as exc:
        log.warning("file drop: could not move %s to %s/: %s", name, folder, type(exc).__name__)
        return False


def _event(row: dict, action: str, reason: Optional[str], details: dict,
           reason_params: Optional[dict] = None) -> None:
    record_event(row["tenant_id"], row.get("created_by") or "system", action,
                 resource=row["id"], details={"source_name": row["name"], **details},
                 reason=reason, reason_params=reason_params)


def _record(row: dict, name: str, size: int, digest: str, status: str, *,
            code: Optional[str] = None, params: Optional[dict] = None,
            rows: Optional[int] = None, moved: bool = False,
            dataset_id: Optional[str] = None, decided_by: Optional[str] = None) -> None:
    execute(
        """UPDATE file_drop_files SET status = %s, reason_code = %s, reason_params = %s,
                  rows_count = %s, moved = %s, dataset_id = %s, decided_at = NOW(),
                  decided_by = COALESCE(%s, decided_by)
            WHERE source_id = %s AND remote_name = %s AND content_hash = %s""",
        (status, code, _json(params or {}), rows, moved, dataset_id, decided_by,
         row["id"], name, digest))


def _claim_file(row: dict, name: str, size: int, digest: str) -> Optional[dict]:
    """Insert the ledger row that says "I am applying this". None when the same
    content already has a decision (or another worker holds it)."""
    existing = query_one(
        "SELECT * FROM file_drop_files WHERE source_id = %s AND remote_name = %s "
        "AND content_hash = %s", (row["id"], name, digest))
    if existing:
        stale = existing["status"] == "applying" and existing["seen_at"] < \
            datetime.now(timezone.utc) - _STALE_APPLYING
        if existing["status"] == "failed" or stale:
            if core.can_retry("failed", existing["attempts"]):
                execute("UPDATE file_drop_files SET status = 'applying', attempts = attempts + 1, "
                        "seen_at = NOW() WHERE id = %s", (existing["id"],))
                return existing
            execute("UPDATE file_drop_files SET status = 'rejected', reason_code = "
                    "'file_drop_gave_up', decided_at = NOW() WHERE id = %s", (existing["id"],))
            return {**existing, "gave_up": True}
        return {**existing, "known": True}
    inserted = query_one(
        """INSERT INTO file_drop_files (tenant_id, source_id, remote_name, size_bytes,
                                        content_hash, status)
           VALUES (%s, %s, %s, %s, %s, 'applying')
           ON CONFLICT (source_id, remote_name, content_hash) DO NOTHING RETURNING *""",
        (row["tenant_id"], row["id"], name, size, digest))
    return inserted


def _process(row: dict, transport: Transport, name: str, content: bytes,
             summary: dict, *, force: bool = False, decided_by: Optional[str] = None) -> None:
    tenant_id = row["tenant_id"]
    digest = core.content_hash(content)
    claimed = _claim_file(row, name, len(content), digest)
    if claimed is None or claimed.get("known"):
        if claimed and claimed["status"] in ("applied", "rejected") and not claimed["moved"]:
            folder = core.PROCESSED_DIR if claimed["status"] == "applied" else core.REJECTED_DIR
            if _try_move(transport, name, folder):
                execute("UPDATE file_drop_files SET moved = TRUE WHERE id = %s", (claimed["id"],))
        summary["already_known"] += 1
        return
    if claimed.get("gave_up"):
        summary["rejected"] += 1
        _try_move(transport, name, core.REJECTED_DIR,
                  core.sidecar_text("file_drop_gave_up", {}, datetime.now(timezone.utc)))
        return

    suffix = Path(name).suffix.lower()
    try:
        incoming = inspect_content(suffix, content)
        if not force:
            verdict = core.judge(_existing_snapshot(tenant_id, row.get("target_dataset_id")),
                                 incoming)
            if not verdict.ok:
                _record(row, name, len(content), digest, "held", code=verdict.code,
                        params=verdict.params, rows=incoming.rows)
                _event(row, "file_drop.file_held", "file_drop_file_held",
                       {"file_name": name}, {"code": verdict.code, **verdict.params})
                summary["held"] += 1
                return
        dataset_id = _apply(tenant_id, row, name, content)
    except AppError as exc:
        params = dict(exc.params or {})
        _record(row, name, len(content), digest, "rejected", code=exc.code, params=params,
                decided_by=decided_by)
        moved = _try_move(transport, name, core.REJECTED_DIR,
                          core.sidecar_text(exc.code, params, datetime.now(timezone.utc)))
        execute("UPDATE file_drop_files SET moved = %s WHERE source_id = %s AND remote_name = %s "
                "AND content_hash = %s", (moved, row["id"], name, digest))
        _event(row, "file_drop.file_rejected", "file_drop_file_rejected", {"file_name": name},
               {"code": exc.code, **params})
        summary["rejected"] += 1
        return
    except Exception as exc:
        log.exception("file drop: applying %s failed", name)
        _record(row, name, len(content), digest, "failed", code="file_drop_apply_failed",
                params={"detail": type(exc).__name__})
        summary["failed"] += 1
        return

    moved = _try_move(transport, name, core.PROCESSED_DIR)
    _record(row, name, len(content), digest, "applied", rows=incoming.rows, moved=moved,
            dataset_id=dataset_id, decided_by=decided_by)
    _event(row, "file_drop.file_applied", None,
           {"file_name": name, "rows": incoming.rows})
    summary["applied"] += 1
    if not moved:
        summary["move_failed"] += 1


# ── Polling ──────────────────────────────────────────────────────────────────

def poll(row: dict) -> dict:
    """One look at one source. Never raises: a failure becomes the source's state."""
    summary = {"seen": 0, "waiting": 0, "applied": 0, "held": 0, "rejected": 0,
               "failed": 0, "already_known": 0, "move_failed": 0, "error": None}
    now = time.time()
    observations: dict[str, list] = {}
    try:
        transport = open_transport(row)
    except AppError as exc:
        _fail(row, exc)
        summary["error"] = exc.code
        return summary
    try:
        patterns = core.parse_patterns(row["file_pattern"])
        previous = row.get("observations") or {}
        limit = _plan_limit_bytes()
        files = sorted((f for f in transport.list_files() if core.selects(patterns, f.name)),
                       key=lambda f: (f.mtime, f.name))
        for f in files:
            summary["seen"] += 1
            observations[f.name] = [f.size, f.mtime]
            if not core.is_stable(previous.get(f.name), f.size, f.mtime, now=now,
                                  min_age_seconds=row["min_age_seconds"]):
                summary["waiting"] += 1
                continue
            if f.size > limit:
                _reject_unread(row, transport, f.name, f.size, "file_drop_file_too_large",
                               {"max_mb": round(limit / 1024 / 1024, 1)}, summary)
                continue
            content = transport.read(f.name, limit)
            if len(content) != f.size:
                # It changed between the listing and the read: look again next time.
                observations[f.name] = [len(content), f.mtime]
                summary["waiting"] += 1
                continue
            _process(row, transport, f.name, content, summary)
        _succeed(row, observations)
    except AppError as exc:
        _fail(row, exc)
        summary["error"] = exc.code
    except Exception as exc:
        log.exception("file drop poll failed for %s", row["id"])
        _fail(row, AppError("file_drop_connection_failed", "The poll failed", status_code=500,
                            params={"detail": type(exc).__name__}))
        summary["error"] = "file_drop_connection_failed"
    finally:
        transport.close()
    return summary


def _reject_unread(row: dict, transport: Transport, name: str, size: int, code: str,
                   params: dict, summary: dict) -> None:
    digest = f"unread:{size}"
    claimed = _claim_file(row, name, size, digest)
    if claimed is None or claimed.get("known"):
        summary["already_known"] += 1
        return
    _record(row, name, size, digest, "rejected", code=code, params=params)
    moved = _try_move(transport, name, core.REJECTED_DIR,
                      core.sidecar_text(code, params, datetime.now(timezone.utc)))
    execute("UPDATE file_drop_files SET moved = %s WHERE source_id = %s AND remote_name = %s "
            "AND content_hash = %s", (moved, row["id"], name, digest))
    _event(row, "file_drop.file_rejected", "file_drop_file_rejected", {"file_name": name},
           {"code": code, **params})
    summary["rejected"] += 1


def _delay_seconds(row: dict) -> float:
    return core.jittered_delay_seconds(row["interval_minutes"], random.random())


def _succeed(row: dict, observations: dict) -> None:
    execute(
        """UPDATE file_drop_sources SET status = 'ok', last_checked_at = NOW(), last_ok_at = NOW(),
                  last_error_code = NULL, last_error_params = NULL, observations = %s,
                  lease_until = NULL, next_run_at = NOW() + make_interval(secs => %s)
            WHERE id = %s""",
        (_json(observations), _delay_seconds(row), row["id"]))


def _fail(row: dict, exc: AppError) -> None:
    blocked = exc.code in _BLOCKING
    execute(
        """UPDATE file_drop_sources SET status = %s, last_checked_at = NOW(),
                  last_error_code = %s, last_error_params = %s, lease_until = NULL,
                  next_run_at = NOW() + make_interval(secs => %s)
            WHERE id = %s""",
        (STATUS_BLOCKED if blocked else STATUS_ERROR, exc.code, _json(exc.params or {}),
         _delay_seconds(row), row["id"]))
    # Once per change of state: a source already stopped for this reason says nothing more.
    if row.get("last_error_code") != exc.code:
        _event(row, "file_drop.source_blocked" if blocked else "file_drop.source_failed",
               "file_drop_source_stopped" if blocked else "file_drop_source_failing",
               {}, {"code": exc.code, **(exc.params or {})})


def claim_due(limit: int = 5) -> list[dict]:
    """Lease the sources due for a look. Blocked sources are not claimed: retrying
    a wrong password locks the company's account."""
    return query(
        """UPDATE file_drop_sources SET lease_until = NOW() + make_interval(mins => %s)
            WHERE id IN (
                SELECT id FROM file_drop_sources
                 WHERE enabled AND status <> 'blocked' AND next_run_at <= NOW()
                   AND (lease_until IS NULL OR lease_until < NOW())
                 ORDER BY next_run_at LIMIT %s FOR UPDATE SKIP LOCKED)
        RETURNING *""",
        (LEASE_MINUTES, limit))


def check_now(tenant_id: str, source_id: str) -> dict:
    """The admin's "check now". Does not wait for the interval, still honours the
    lease so two checks never overlap."""
    leased = query(
        """UPDATE file_drop_sources SET lease_until = NOW() + make_interval(mins => %s)
            WHERE id = %s AND tenant_id = %s AND (lease_until IS NULL OR lease_until < NOW())
        RETURNING *""", (LEASE_MINUTES, source_id, tenant_id))
    if not leased:
        get_source(tenant_id, source_id)
        raise _bad("file_drop_check_running", "A check of this source is already running", 409)
    # A file is finished when two looks agree, so the first press of "check now"
    # on a fresh file reports it as waiting; the next press reads it.
    return poll(leased[0])


# ── People deciding ──────────────────────────────────────────────────────────

def list_files(tenant_id: str, source_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    params: list[Any] = [tenant_id]
    where = "f.tenant_id = %s"
    if source_id:
        where += " AND f.source_id = %s"
        params.append(source_id)
    params.append(min(max(limit, 1), 500))
    rows = query(
        f"""SELECT f.*, s.name AS source_name FROM file_drop_files f
              JOIN file_drop_sources s ON s.id = f.source_id
             WHERE {where} ORDER BY f.seen_at DESC LIMIT %s""", tuple(params))
    return [{
        "id": r["id"], "source_id": r["source_id"], "source_name": r["source_name"],
        "remote_name": r["remote_name"], "size_bytes": r["size_bytes"], "status": r["status"],
        "reason_code": r["reason_code"], "reason_params": r["reason_params"],
        "rows": r["rows_count"], "moved": r["moved"], "dataset_id": r["dataset_id"],
        "seen_at": _iso(r["seen_at"]), "decided_at": _iso(r["decided_at"]),
    } for r in rows]


def decide(tenant_id: str, user_id: str, file_id: str, action: str) -> dict:
    """A person settles a held file: apply it anyway, or reject it."""
    rec = query_one("SELECT * FROM file_drop_files WHERE id = %s AND tenant_id = %s",
                    (file_id, tenant_id))
    if not rec:
        raise _bad("file_drop_file_not_found", "File not found", 404)
    if rec["status"] != "held":
        raise _bad("file_drop_file_not_held", "Only a held file can be decided", 409,
                   current_status=rec["status"])
    row = get_source(tenant_id, rec["source_id"])
    transport = open_transport(row)
    summary = {"seen": 0, "waiting": 0, "applied": 0, "held": 0, "rejected": 0,
               "failed": 0, "already_known": 0, "move_failed": 0, "error": None}
    try:
        name = rec["remote_name"]
        if action == "reject":
            _record(row, name, rec["size_bytes"], rec["content_hash"], "rejected",
                    code="file_drop_rejected_by_person", decided_by=user_id)
            moved = _try_move(transport, name, core.REJECTED_DIR, core.sidecar_text(
                "file_drop_rejected_by_person", {}, datetime.now(timezone.utc)))
            execute("UPDATE file_drop_files SET moved = %s WHERE id = %s", (moved, file_id))
            return {"status": "rejected", "moved": moved}
        content = transport.read(name, _plan_limit_bytes())
        if core.content_hash(content) != rec["content_hash"]:
            raise _bad("file_drop_file_changed",
                       "The file changed since it was held; the next look will judge the new one",
                       409)
        # `_claim_file` finds the held row; release it so the forced pass owns it.
        execute("DELETE FROM file_drop_files WHERE id = %s", (file_id,))
        _process(row, transport, name, content, summary, force=True, decided_by=user_id)
        result = query_one("SELECT status, reason_code FROM file_drop_files WHERE source_id = %s "
                           "AND remote_name = %s AND content_hash = %s",
                           (row["id"], name, rec["content_hash"]))
        return {"status": result["status"] if result else "failed",
                "reason_code": result["reason_code"] if result else None}
    finally:
        transport.close()
