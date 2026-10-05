"""Route one parsed e-mail to a tenant and ingest its attachments.

Every attachment goes through the SAME code as `POST /datasets`
(`datasets.service.upload_dataset`), so the plan's file-size ceiling and the
global upload cap apply exactly as for a person, and a NUL byte is refused
before anything is stored.

What is deliberately NOT done here:

* No reply is ever sent to the sender. Answering arbitrary senders is how a
  mailbox becomes a backscatter source; the outcome is recorded for the
  tenant's admin to read in the app, and a teammate (a sender the tenant
  knows) is told through the tenant's own notification feed.
* A new column mapping is never guessed. A file whose columns do not match the
  tenant's last confirmed mapping is stored as `needs_review` and the user is
  asked, in the app, to confirm the columns.
* No training is started on its own. Only a tenant that already has an
  ENABLED schedule whose dataset has the same columns gets that schedule's
  data refreshed and its retrain launched, through the schedule's own code.
"""
from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

from backend.dataframes.io import dataset_preview, peek_columns
from backend.datasets import service as ds_svc
from backend.db.connection import query, query_one
from backend.inbound_email import service as svc
from backend.inbound_email.parse import ParsedEmail, token_from_recipients

log = logging.getLogger(__name__)

# Past this many unknown-sender rows in an hour we stop WRITING them (they are
# still logged): somebody who learned an address must not be able to fill the
# tenant's table.
_UNKNOWN_SENDER_ROWS_PER_HOUR = 50


# Row reason code -> the event reason the feed has copy for. The row keeps the
# precise code; the feed speaks in the fewer causes a person can act on.
_EVENT_REASON = {
    "empty_file": "inbound_unreadable_file",
    "unreadable_file": "inbound_unreadable_file",
    "file_has_nul_byte": "inbound_unreadable_file",
    "file_too_large": "inbound_file_too_large",
    "plan_limit_reached": "plan_limit_reached",
    "duplicate_attachment": "inbound_duplicate_file",
    "unsupported_type": "inbound_no_usable_attachment",
    "no_attachment": "inbound_no_usable_attachment",
}


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _clean_name(name: str) -> str:
    base = Path((name or "").replace("\x00", "").replace("\\", "/")).name
    return base[:200]


def _confirmed_mapping_columns(tenant_id: str) -> Optional[dict]:
    """The date / demand / product columns of the tenant's latest confirmed
    mapping, or None when nobody has confirmed one yet."""
    from backend.sessions.data_gate import resolve_columns
    rows = query(
        "SELECT sc.columns_cfg FROM session_configs sc "
        "JOIN sessions s ON s.id = sc.session_id AND s.tenant_id = sc.tenant_id "
        "WHERE sc.tenant_id = %s AND sc.columns_cfg IS NOT NULL "
        "AND s.archived_at IS NULL ORDER BY sc.updated_at DESC LIMIT 5",
        (tenant_id,),
    )
    for row in rows:
        cols = resolve_columns(row["columns_cfg"])
        if cols.get("date") and cols.get("target") and cols.get("group"):
            return cols
    return None


def _mapping_verdict(tenant_id: str, columns: list[str]) -> tuple[bool, str, dict]:
    """(matches, reason, params). Exact header names: a renamed column is a
    different file until a person says otherwise."""
    mapping = _confirmed_mapping_columns(tenant_id)
    if mapping is None:
        return False, "no_confirmed_mapping", {}
    needed = [mapping["date"], mapping["target"], mapping["group"]]
    missing = [c for c in needed if c not in columns]
    if missing:
        return False, "columns_changed", {"missing": ", ".join(missing)}
    return True, "", {}


async def _upload_async(tenant_id: str, filename: str, content: bytes, *, track: bool) -> dict:
    upload = UploadFile(file=io.BytesIO(content), filename=filename)
    return await ds_svc.upload_dataset(tenant_id, "inbound_email", upload, track=track)


async def _refresh_schedules(tenant_id: str, columns: list[str], filename: str,
                             content: bytes) -> str:
    """Refresh the data of every ENABLED schedule whose dataset has these exact
    columns, then run that schedule once. Returns 'none' when the tenant has no
    such schedule (the usual case), else 'launched' / 'skipped' / 'failed'.
    Nothing here can start a training for a tenant without a schedule."""
    from backend.datasources import service as src_svc
    from backend.sessions import retrain_service

    jobs = query(
        "SELECT id, session_id FROM scheduled_jobs WHERE tenant_id = %s AND enabled = TRUE",
        (tenant_id,),
    )
    result = "none"
    for job in jobs:
        try:
            session = query_one(
                "SELECT dataset_id FROM sessions WHERE id = %s AND tenant_id = %s",
                (job["session_id"], tenant_id),
            )
            ds = src_svc.get_source(tenant_id, session["dataset_id"]) if session and session["dataset_id"] else None
            if not ds or ds.get("source_type") != "file" or not ds.get("file_path"):
                continue
            if list(dataset_preview(ds["file_path"], 1)["columns"]) != columns:
                continue
            await src_svc.replace_file_source(
                tenant_id, "inbound_email", ds["id"],
                UploadFile(file=io.BytesIO(content), filename=filename),
            )
            launched = retrain_service.launch_scheduled_retrain(
                tenant_id, job["id"], job["session_id"])
            outcome = "launched" if launched else "skipped"
        except Exception:  # noqa: BLE001 - the ingest itself already succeeded
            log.exception("inbound e-mail: schedule refresh failed tenant=%s job=%s",
                          tenant_id, job["id"])
            outcome = "failed"
        if outcome == "launched" or result == "none" or (outcome == "failed" and result == "skipped"):
            result = outcome
    return result


def _event(tenant_id: str, action: str, filename: str, sender: str,
           reason: Optional[str] = None, params: Optional[dict] = None,
           resource: Optional[str] = None) -> None:
    from backend.activity.events import record_event
    record_event(tenant_id, "system", action, resource=resource,
                 details={"filename": filename, "email": sender},
                 reason=reason, reason_params=params)


def _audit(tenant_id: str, dataset_id: str, filename: str, sender: str,
           outcome: str) -> None:
    """The audit trail row for data that entered the tenant by e-mail."""
    try:
        from backend.activity.service import log_action
        from backend.audit.catalog import audit_action
        log_action(
            tenant_id, "system", audit_action("inbound_email.received"),
            resource=dataset_id,
            context={"target_type": "dataset", "target_id": dataset_id,
                     "target_label": filename, "actor_kind": "system",
                     "after": {"outcome": outcome, "sender": sender}},
        )
    except Exception:  # noqa: BLE001
        log.exception("inbound e-mail: audit row not written tenant=%s", tenant_id)


async def _ingest_attachment(tenant_id: str, row_id: str, sender: str, known: bool,
                             filename: str, content: bytes) -> dict:
    def reject(reason: str, params: Optional[dict] = None) -> dict:
        svc.settle(row_id, svc.OUTCOME_REJECTED, reason, params)
        if known:
            _event(tenant_id, "inbound_email.rejected", filename, sender,
                   reason=_EVENT_REASON.get(reason, "unknown"), params=params)
        return {"outcome": svc.OUTCOME_REJECTED, "reason": reason}

    if not content:
        return reject("empty_file")

    suffix = Path(filename).suffix.lower()
    try:
        columns = peek_columns(content, suffix)
    except ValueError as exc:
        # The NUL refusal carries its own sentence; anything else unreadable
        # is reported as such.
        if "NUL byte" in str(exc):
            return reject("file_has_nul_byte")
        return reject("unreadable_file")
    except Exception:  # noqa: BLE001 - a corrupt file is the sender's problem
        return reject("unreadable_file")
    if not columns:
        return reject("unreadable_file")

    matches, why, why_params = _mapping_verdict(tenant_id, columns)

    try:
        meta = await _upload_async(tenant_id, filename, content, track=matches)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return reject("plan_limit_reached", {
            k: detail[k] for k in ("limit", "current", "max") if k in detail})
    except ValueError:
        return reject("file_too_large", {
            "size_mb": round(len(content) / 1024 / 1024, 1)})

    dataset_id = meta["id"]
    if not matches:
        svc.settle(row_id, svc.OUTCOME_NEEDS_REVIEW, why, why_params, dataset_id)
        _audit(tenant_id, dataset_id, filename, sender, svc.OUTCOME_NEEDS_REVIEW)
        _event(tenant_id, "inbound_email.needs_review", filename, sender,
               reason="inbound_columns_unconfirmed",
               params={"cause": why, **why_params}, resource=dataset_id)
        return {"outcome": svc.OUTCOME_NEEDS_REVIEW, "reason": why,
                "dataset_id": dataset_id}

    retrain = await _refresh_schedules(tenant_id, columns, filename, content)
    svc.settle(row_id, svc.OUTCOME_INGESTED, None, None, dataset_id, retrain)
    _audit(tenant_id, dataset_id, filename, sender, svc.OUTCOME_INGESTED)
    _event(tenant_id, "inbound_email.ingested", filename, sender, resource=dataset_id)
    return {"outcome": svc.OUTCOME_INGESTED, "dataset_id": dataset_id, "retrain": retrain}


async def process(email: ParsedEmail, body_sha: str) -> dict:
    """Ingest one message. Returns a small, tenant-neutral summary the webhook
    echoes back: it must not reveal whether an address or a sender exists."""
    token = token_from_recipients(email.recipients, svc.inbound_domain())
    tenant_id = svc.tenant_for_token(token) if token else None
    if not tenant_id:
        log.info("inbound e-mail: no tenant for the recipient address; dropped")
        return {"outcome": svc.OUTCOME_REJECTED, "reason": "unknown_address"}

    message_id = email.message_id or f"sha:{body_sha}"
    sender = (email.sender or "").lower()[:254]
    address = svc.get_or_create(tenant_id)

    if not svc.sender_is_allowed(tenant_id, sender, address["allowed_senders"] or []):
        recent = query_one(
            "SELECT COUNT(*) AS n FROM inbound_email_messages WHERE tenant_id = %s "
            "AND reason = 'unknown_sender' AND received_at > NOW() - INTERVAL '1 hour'",
            (tenant_id,),
        )
        if recent["n"] < _UNKNOWN_SENDER_ROWS_PER_HOUR:
            row_id = svc.claim(tenant_id, message_id, sender, None, "", None)
            if row_id:
                svc.settle(row_id, svc.OUTCOME_REJECTED, "unknown_sender")
        log.info("inbound e-mail: unknown sender rejected tenant=%s (no reply sent)", tenant_id)
        return {"outcome": svc.OUTCOME_REJECTED, "reason": "unknown_sender"}

    candidates = [a for a in email.attachments
                  if Path(a.filename).suffix.lower() in ds_svc.ALLOWED_EXTENSIONS]
    results: list[dict] = []

    if not candidates:
        # Nothing readable: say why, once per message (signature images and
        # URL-only attachments land here too).
        reason = "unsupported_type" if (email.attachments or email.skipped_attachments) else "no_attachment"
        first = email.attachments[0] if email.attachments else None
        row_id = svc.claim(tenant_id, message_id, sender,
                           _clean_name(first.filename) if first else None,
                           _sha(first.content) if first else "",
                           len(first.content) if first else None)
        if row_id:
            svc.settle(row_id, svc.OUTCOME_REJECTED, reason,
                       {"skipped": email.skipped_attachments} if email.skipped_attachments else None)
            _event(tenant_id, "inbound_email.rejected",
                   _clean_name(first.filename) if first else "", sender,
                   reason=_EVENT_REASON[reason])
        return {"outcome": svc.OUTCOME_REJECTED, "reason": reason}

    for att in candidates:
        name = _clean_name(att.filename)
        sha = _sha(att.content)
        row_id = svc.claim(tenant_id, message_id, sender, name, sha, len(att.content))
        if row_id is None:
            results.append({"outcome": "duplicate"})       # the provider retried
            continue
        try:
            seen = query_one(
                "SELECT 1 AS hit FROM inbound_email_messages WHERE tenant_id = %s "
                "AND attachment_sha256 = %s AND id <> %s "
                "AND outcome IN ('ingested', 'needs_review')",
                (tenant_id, sha, row_id),
            )
            if seen:
                svc.settle(row_id, svc.OUTCOME_REJECTED, "duplicate_attachment")
                results.append({"outcome": svc.OUTCOME_REJECTED,
                                "reason": "duplicate_attachment"})
                continue
            results.append(await _ingest_attachment(
                tenant_id, row_id, sender, True, name, att.content))
        except Exception:  # noqa: BLE001 - a row must never stay 'processing'
            log.exception("inbound e-mail: attachment failed tenant=%s", tenant_id)
            svc.settle(row_id, svc.OUTCOME_REJECTED, "internal_error")
            results.append({"outcome": svc.OUTCOME_REJECTED, "reason": "internal_error"})

    return {"outcome": results[0]["outcome"], "attachments": len(results),
            "results": [{k: v for k, v in r.items() if k in ("outcome", "reason", "retrain")}
                        for r in results]}
