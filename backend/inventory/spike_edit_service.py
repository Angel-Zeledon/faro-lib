"""Spike exclusions: a person marks a past period of a product as a one-off.

The number-one complaint about competing tools is that a past spike cannot be
edited, so the forecast keeps believing a one-time bulk order is demand. Here an
analyst marks the period ("exclude from the baseline") with a reason from a fixed
list and an optional note. Three rules keep it honest:

1. **The upload is never touched.** The mark lives in `spike_edits`, beside the
   data. Training applies it to its in-memory copy (the engine's
   `forecasting_core.data.spike_edits`), replacing the excluded observations by
   the typical neighbouring value.
2. **Append-only.** A mark is never edited or deleted. Undo stamps
   `reverted_by`/`reverted_at`; marking again is a new row. Who excluded what,
   when and why, and who restored it, can always be answered.
3. **Nothing is retrained for you.** A mark changes the NEXT training or
   re-forecast of any session on that dataset; the runner records, per run, how
   many points each mark treated (`spike_edit_applications`) and says so in the
   run's warnings, so a forecast's lineage names the people's edits behind it.
   Existing results do not move.

Keyed by dataset, not session: "March 2025 was a one-off" is a fact about the
history, so it applies to every later training on that data.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError
from backend.sessions.service import get_session

log = logging.getLogger(__name__)

# The list the analyst chooses from. Stable codes: the frontend renders the label.
REASONS = (
    "one_off_order", "promotion", "backlog_catch_up", "data_error",
    "external_event", "other",
)
MAX_NOTE_LENGTH = 300
# Longest period one mark may cover.
MAX_SPAN_DAYS = 366


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _as_date(value, field: str) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except ValueError:
        raise AppError("date_invalid_iso", f"{field} must be an ISO date (YYYY-MM-DD)",
                       params={"field": field})


_COLS = """e.id, e.dataset_id, e.sku, e.start_date, e.end_date, e.reason_code,
           e.reason_note, e.created_by, e.created_at, e.reverted_by, e.reverted_at,
           COALESCE(NULLIF(u.full_name, ''), split_part(u.email, '@', 1)) AS created_by_name"""


def _fmt(row: dict) -> dict:
    d = dict(row)
    for k in ("start_date", "end_date", "created_at", "reverted_at"):
        d[k] = _iso(d.get(k))
    return d


def _dataset_of(tenant_id: str, session_id: str) -> str:
    session = get_session(tenant_id, session_id)
    if not session:
        raise AppError("session_not_found", "Session not found", status_code=404)
    dataset_id = session.get("dataset_id")
    if not dataset_id:
        raise AppError("spike_edit_no_dataset",
                       "This session has no dataset to mark a period in", status_code=409)
    return dataset_id


def create(tenant_id: str, session_id: str, user_id: str, *, sku: str, start_date, end_date,
           reason_code: str, reason_note: Optional[str] = None) -> dict:
    sku = (sku or "").strip()
    if not sku:
        raise AppError("spike_edit_sku_required", "Choose a product")
    if reason_code not in REASONS:
        raise AppError("spike_edit_reason_invalid", "Choose a reason from the list",
                       params={"reason": reason_code})
    note = (reason_note or "").strip()[:MAX_NOTE_LENGTH] or None
    if reason_code == "other" and not note:
        raise AppError("spike_edit_note_required",
                       "Say what the reason is when you choose 'other'")
    start, end = _as_date(start_date, "start_date"), _as_date(end_date, "end_date")
    if end < start:
        raise AppError("spike_edit_dates_invalid", "The end date is before the start",
                       params={"start_date": start.isoformat(), "end_date": end.isoformat()})
    if (end - start).days > MAX_SPAN_DAYS:
        raise AppError("spike_edit_dates_invalid", "That period is too long to exclude",
                       params={"start_date": start.isoformat(), "end_date": end.isoformat()})
    if end > date.today():
        raise AppError("spike_edit_dates_invalid",
                       "Only a period that already happened can be excluded",
                       params={"start_date": start.isoformat(), "end_date": end.isoformat()})
    dataset_id = _dataset_of(tenant_id, session_id)

    with transaction() as conn:
        clash = query_one(
            """SELECT id FROM spike_edits
                WHERE tenant_id = %s AND dataset_id = %s AND sku = %s
                  AND reverted_at IS NULL AND start_date <= %s AND end_date >= %s
                LIMIT 1""",
            (tenant_id, dataset_id, sku, end, start), conn=conn)
        if clash:
            raise AppError("spike_edit_overlaps",
                           "That period overlaps one already excluded for this product; "
                           "undo it first to change it", status_code=409,
                           params={"sku": sku, "spike_edit_id": clash["id"]})
        row = query_one(
            """INSERT INTO spike_edits
                   (tenant_id, dataset_id, sku, start_date, end_date,
                    reason_code, reason_note, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (tenant_id, dataset_id, sku, start, end, reason_code, note, user_id), conn=conn)
    return get(tenant_id, row["id"])


def get(tenant_id: str, spike_edit_id: str) -> dict:
    row = query_one(
        f"""SELECT {_COLS} FROM spike_edits e
              LEFT JOIN users u ON u.id = e.created_by
             WHERE e.id = %s AND e.tenant_id = %s""", (spike_edit_id, tenant_id))
    if not row:
        raise AppError("spike_edit_not_found", "Exclusion not found", status_code=404)
    return _fmt(row)


def revert(tenant_id: str, spike_edit_id: str, user_id: str) -> dict:
    """Undo a mark. The row stays, stamped; the next training ignores it."""
    # Conditional update so two simultaneous undos cannot both claim the stamp.
    changed = query_one(
        """UPDATE spike_edits SET reverted_by = %s, reverted_at = NOW()
            WHERE id = %s AND tenant_id = %s AND reverted_at IS NULL
        RETURNING id""", (user_id, spike_edit_id, tenant_id))
    if not changed:
        existing = get(tenant_id, spike_edit_id)     # 404 when it is not this tenant's
        raise AppError("spike_edit_already_reverted", "That exclusion was already undone",
                       status_code=409, params={"spike_edit_id": existing["id"]})
    return get(tenant_id, spike_edit_id)


def list_for_session(tenant_id: str, session_id: str, sku: Optional[str] = None,
                     include_reverted: bool = False) -> list[dict]:
    """The marks on this session's dataset, each with how the session's own run
    used it (`applied`: points treated, or None when the run predates the mark)."""
    dataset_id = _dataset_of(tenant_id, session_id)
    clauses, params = ["e.tenant_id = %s", "e.dataset_id = %s"], [tenant_id, dataset_id]
    if sku:
        clauses.append("e.sku = %s")
        params.append(sku)
    if not include_reverted:
        clauses.append("e.reverted_at IS NULL")
    rows = query(
        f"""SELECT {_COLS} FROM spike_edits e
              LEFT JOIN users u ON u.id = e.created_by
             WHERE {' AND '.join(clauses)}
             ORDER BY e.start_date, e.created_at""", tuple(params))
    applied = {
        r["spike_edit_id"]: r for r in query(
            """SELECT spike_edit_id, status, points_treated, original_total,
                      replacement_total, applied_at
                 FROM spike_edit_applications
                WHERE tenant_id = %s AND session_id = %s""", (tenant_id, session_id))
    }
    out = []
    for r in rows:
        item = _fmt(r)
        a = applied.get(item["id"])
        item["applied"] = None if a is None else {
            "status": a["status"], "points_treated": a["points_treated"],
            "original_total": a["original_total"],
            "replacement_total": a["replacement_total"],
            "applied_at": _iso(a["applied_at"]),
        }
        out.append(item)
    return out


# ── Reaching the training run ────────────────────────────────────────────────

def active_for_dataset(tenant_id: str, dataset_id: str) -> list[dict]:
    """The marks a training of this dataset must apply: not undone, oldest first.
    Plain dicts shaped for `forecasting_core.data.spike_edits`."""
    rows = query(
        """SELECT id, sku, start_date, end_date FROM spike_edits
            WHERE tenant_id = %s AND dataset_id = %s AND reverted_at IS NULL
            ORDER BY created_at, id""", (tenant_id, dataset_id))
    return [{"id": r["id"], "sku": r["sku"],
             "start_date": _iso(r["start_date"]), "end_date": _iso(r["end_date"])}
            for r in rows]


def record_applications(tenant_id: str, session_id: str, report: list[dict]) -> None:
    """Write what one run did with each mark. Called by the runner."""
    if not report:
        return
    with transaction() as conn:
        for e in report:
            execute(
                """INSERT INTO spike_edit_applications
                       (tenant_id, session_id, spike_edit_id, sku, status,
                        points_treated, original_total, replacement_total)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (tenant_id, session_id, e["id"], e["sku"], e["status"],
                 int(e["points_treated"]), float(e["original_total"]),
                 float(e["replacement_total"])), conn=conn)
