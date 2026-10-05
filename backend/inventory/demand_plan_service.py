"""Demand plan versions with sign-off: a small S&OP consensus record.

Once a month a planner saves the current plan (statistical forecast + manual
adjustments + committed customer orders) as a VERSION, submits it, and somebody
with approval authority approves or rejects it. When the periods pass, the
approved plan is measured against what sold, next to the plain statistical
forecast over the same points: did the consensus beat the model?

Rules:

1. **A version is frozen.** Its numbers are written once (`demand_plan_math.
   build_snapshot`) and a database trigger refuses any UPDATE. Status changes and
   comments are new rows in `demand_plan_version_events`; the current status is
   the latest status event.
2. **It changes nothing else.** No purchase recommendation, semaphore or
   optimizer reads these tables. With no version saved, every number in the
   product is exactly what it was.
3. **Approval is a person with the authority.** An active admin, or an analyst
   flagged as an approver (the purchase-order approvers, `po_approval_service`).
   The person who submitted a version cannot approve it while somebody else could.
4. **Company-wide.** A version sums every warehouse, so it is created, read and
   decided only by users who see the whole company (the router enforces it).
5. **Permanent.** Nothing here deletes a version; only whole-tenant erasure does.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from backend.db.connection import query, query_one, transaction
from backend.errors import AppError
from backend.inventory import demand_plan_math as plan_math

log = logging.getLogger(__name__)

STATUSES = ("draft", "submitted", "approved", "rejected", "superseded")
# What a person may move a version to, from each status. `superseded` is never
# chosen by hand: approving a newer version does it.
TRANSITIONS = {
    "draft": ("submitted",),
    "submitted": ("approved", "rejected"),
}
MAX_NAME_LENGTH = 120
MAX_COMMENT_LENGTH = 1000
MIN_REJECT_COMMENT_LENGTH = 3
# Bytes of one frozen snapshot (JSON). With MAX_CELLS this is never the binding
# limit for ordinary SKU codes; it exists for very long ones.
MAX_SNAPSHOT_BYTES = 8_000_000
# Versions one tenant may keep. Monthly reviews for decades fit; a script
# creating one per minute does not fill the database.
MAX_VERSIONS = 1000


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _name_sql(alias: str) -> str:
    return f"COALESCE(NULLIF({alias}.full_name, ''), split_part({alias}.email, '@', 1))"


def _lock(conn, tenant_id: str) -> None:
    """Serialise every write of one tenant's plan ledger, so two approvals can
    never both leave a version `approved` and the version count cannot be
    overrun by parallel saves."""
    query_one("SELECT pg_advisory_xact_lock(hashtext(%s))",
              ("demand_plan:" + tenant_id,), conn=conn)


# ── Who may approve ──────────────────────────────────────────────────────────

def approvers(tenant_id: str) -> list[dict]:
    """Active admins, plus analysts flagged as purchase-order approvers."""
    rows = query(
        """SELECT id, email, full_name, role FROM users
            WHERE tenant_id = %s AND status = 'active'
              AND (role = 'admin' OR (role = 'analyst' AND can_approve_po))
            ORDER BY full_name NULLS LAST, email""",
        (tenant_id,))
    return [dict(r) for r in rows]


def can_approve(tenant_id: str, user_id: str) -> bool:
    return any(a["id"] == user_id for a in approvers(tenant_id))


# ── The session a version is built from ──────────────────────────────────────

def _usable_session(tenant_id: str, session_id: Optional[str]) -> dict:
    if not session_id:
        from backend.sessions.planning_service import resolve_active_session
        session_id = resolve_active_session(tenant_id)
        if not session_id:
            raise AppError("demand_plan_no_session",
                           "Train a forecast before saving a demand plan", status_code=409)
    row = query_one(
        """SELECT id, name, status, granularity, archived_at, is_backtest
             FROM sessions WHERE id = %s AND tenant_id = %s""",
        (session_id, tenant_id))
    if not row:
        raise AppError("demand_plan_session_not_found", "Forecast not found", status_code=404)
    reason = None
    if row["status"] != "COMPLETED":
        reason = "not_completed"
    elif row.get("archived_at") is not None:
        reason = "archived"
    elif row.get("is_backtest"):
        reason = "backtest"
    if reason:
        raise AppError("demand_plan_session_not_usable",
                       "A demand plan is built from a completed, active forecast",
                       status_code=409, params={"reason": reason})
    return dict(row)


# ── Creating a version ───────────────────────────────────────────────────────

def create_version(tenant_id: str, user_id: str, *, name: str,
                   session_id: Optional[str] = None,
                   horizon_periods: Optional[int] = None,
                   note: Optional[str] = None,
                   today: Optional[date] = None) -> dict:
    from backend.forecast_check.service import _champion_forecasts
    from backend.inventory import committed_demand_service as cd_svc
    from backend.inventory import forecast_adjustment_service as adj_svc

    name = (name or "").strip()
    if not name:
        raise AppError("demand_plan_name_required", "Give the version a name")
    name = name[:MAX_NAME_LENGTH]
    note = (note or "").strip()[:MAX_COMMENT_LENGTH] or None
    today = today or date.today()
    session = _usable_session(tenant_id, session_id)

    # The same three inputs the purchase recommendation reads. Adjustments are
    # read without the "not yet over" cut (date.min): a period that started
    # before today still carries the adjustment that covered its first days, and
    # demand_multiplier only ever counts the overlap with each period.
    snapshot = plan_math.build_snapshot(
        _champion_forecasts(tenant_id, session["id"]),
        adj_svc.active_by_sku(tenant_id, session["id"], today=date.min),
        cd_svc.active_by_sku(tenant_id),
        today=today, horizon_periods=horizon_periods,
        granularity=session.get("granularity"))
    blob = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
    size = len(blob.encode("utf-8"))
    if size > MAX_SNAPSHOT_BYTES:
        raise AppError("demand_plan_too_large",
                       "This plan is too large to store as one version; choose a shorter horizon",
                       params={"skus": snapshot["totals"]["sku_count"],
                               "periods": len(snapshot["periods"]),
                               "bytes": size, "max_bytes": MAX_SNAPSHOT_BYTES})
    totals = snapshot["totals"]

    with transaction() as conn:
        _lock(conn, tenant_id)
        n = query_one("SELECT COUNT(*) AS n FROM demand_plan_versions WHERE tenant_id = %s",
                      (tenant_id,), conn=conn)["n"]
        if n >= MAX_VERSIONS:
            raise AppError("demand_plan_version_limit",
                           "This company already keeps the maximum number of plan versions",
                           status_code=409, params={"max": MAX_VERSIONS})
        row = query_one(
            """INSERT INTO demand_plan_versions
                   (tenant_id, name, session_id, granularity, anchor_date, first_period,
                    last_period, horizon_periods, sku_count, snapshot_bytes, totals,
                    snapshot, note, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
               RETURNING id""",
            (tenant_id, name, session["id"], session.get("granularity"), today,
             snapshot["periods"][0], snapshot["periods"][-1], len(snapshot["periods"]),
             totals["sku_count"], size, json.dumps(totals), blob, note, user_id),
            conn=conn)
        query_one(
            """INSERT INTO demand_plan_version_events
                   (tenant_id, version_id, kind, from_status, to_status, actor_id, comment)
               VALUES (%s, %s, 'status', NULL, 'draft', %s, %s) RETURNING id""",
            (tenant_id, row["id"], user_id, note), conn=conn)
    return get_version(tenant_id, row["id"])


# ── Reading ──────────────────────────────────────────────────────────────────

_STATUS_SQL = """(SELECT e.to_status FROM demand_plan_version_events e
                   WHERE e.version_id = v.id AND e.tenant_id = v.tenant_id
                     AND e.kind = 'status'
                   ORDER BY e.id DESC LIMIT 1)"""

_COLS = f"""v.id, v.name, v.session_id, v.granularity, v.anchor_date, v.first_period,
            v.last_period, v.horizon_periods, v.sku_count, v.snapshot_bytes, v.totals,
            v.note, v.created_by, v.created_at, {_name_sql('u')} AS created_by_name,
            s.name AS session_name, {_STATUS_SQL} AS status"""


def _fmt(row: dict) -> dict:
    d = dict(row)
    for k in ("anchor_date", "first_period", "last_period", "created_at"):
        d[k] = _iso(d.get(k))
    totals = d.get("totals") or {}
    # The list carries the grand totals only; the per-period series is on the detail.
    d["totals"] = {k: v for k, v in totals.items() if k != "by_period"}
    return d


def list_versions(tenant_id: str, limit: int = 200) -> list[dict]:
    rows = query(
        f"""SELECT {_COLS} FROM demand_plan_versions v
              LEFT JOIN users u ON u.id = v.created_by AND u.tenant_id = v.tenant_id
              LEFT JOIN sessions s ON s.id = v.session_id AND s.tenant_id = v.tenant_id
             WHERE v.tenant_id = %s
             ORDER BY v.created_at DESC LIMIT %s""",
        (tenant_id, max(1, min(int(limit), MAX_VERSIONS))))
    return [_fmt(r) for r in rows]


def _row(tenant_id: str, version_id: str, conn=None) -> dict:
    row = query_one(
        f"""SELECT {_COLS} FROM demand_plan_versions v
              LEFT JOIN users u ON u.id = v.created_by AND u.tenant_id = v.tenant_id
              LEFT JOIN sessions s ON s.id = v.session_id AND s.tenant_id = v.tenant_id
             WHERE v.id = %s AND v.tenant_id = %s""",
        (version_id, tenant_id), conn=conn)
    if not row:
        raise AppError("demand_plan_not_found", "Plan version not found", status_code=404)
    return row


def events(tenant_id: str, version_id: str) -> list[dict]:
    rows = query(
        f"""SELECT e.id, e.kind, e.from_status, e.to_status, e.actor_id, e.comment,
                   e.details, e.created_at, {_name_sql('u')} AS actor_name
              FROM demand_plan_version_events e
              LEFT JOIN users u ON u.id = e.actor_id AND u.tenant_id = e.tenant_id
             WHERE e.tenant_id = %s AND e.version_id = %s
             ORDER BY e.id""",
        (tenant_id, version_id))
    return [{**dict(r), "created_at": _iso(r["created_at"])} for r in rows]


def get_version(tenant_id: str, version_id: str) -> dict:
    row = _row(tenant_id, version_id)
    totals = row.get("totals") or {}
    out = _fmt(row)
    out["by_period"] = totals.get("by_period") or []
    out["events"] = events(tenant_id, version_id)
    submitted = [e for e in out["events"] if e["kind"] == "status" and e["to_status"] == "submitted"]
    out["submitted_by"] = submitted[-1]["actor_id"] if submitted else None
    return out


def snapshot_of(tenant_id: str, version_id: str) -> dict:
    row = query_one(
        "SELECT snapshot FROM demand_plan_versions WHERE id = %s AND tenant_id = %s",
        (version_id, tenant_id))
    if not row:
        raise AppError("demand_plan_not_found", "Plan version not found", status_code=404)
    return row["snapshot"] or {}


def lines(tenant_id: str, version_id: str, *, q: Optional[str] = None,
          offset: int = 0, limit: int = 50) -> dict:
    return plan_math.line_rows(snapshot_of(tenant_id, version_id), q=q, offset=offset, limit=limit)


def diff(tenant_id: str, version_a: str, version_b: str, limit: int = 50) -> dict:
    out = plan_math.diff_versions(snapshot_of(tenant_id, version_a),
                                  snapshot_of(tenant_id, version_b), limit)
    return {"version_a": version_a, "version_b": version_b, **out}


# ── Status changes and comments ──────────────────────────────────────────────

def _insert_event(conn, tenant_id: str, version_id: str, *, kind: str, actor_id: str,
                  from_status: Optional[str] = None, to_status: Optional[str] = None,
                  comment: Optional[str] = None, details: Optional[dict] = None) -> None:
    query_one(
        """INSERT INTO demand_plan_version_events
               (tenant_id, version_id, kind, from_status, to_status, actor_id, comment, details)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id""",
        (tenant_id, version_id, kind, from_status, to_status, actor_id, comment,
         json.dumps(details or {})), conn=conn)


def transition(tenant_id: str, version_id: str, actor_id: str, to_status: str,
               comment: Optional[str] = None) -> dict:
    """Move a version to `submitted`, `approved` or `rejected`.

    Returns the version plus `superseded` (ids of the versions this approval
    replaced) and `self_approved`.
    """
    if to_status not in ("submitted", "approved", "rejected"):
        raise AppError("demand_plan_status_invalid", "Unknown status",
                       params={"status": to_status})
    comment = (comment or "").strip()[:MAX_COMMENT_LENGTH] or None
    if to_status == "rejected" and len(comment or "") < MIN_REJECT_COMMENT_LENGTH:
        raise AppError("demand_plan_reject_reason_required",
                       "Say why the plan is rejected",
                       params={"min": MIN_REJECT_COMMENT_LENGTH})

    superseded: list[str] = []
    self_approved = False
    with transaction() as conn:
        _lock(conn, tenant_id)
        current = _row(tenant_id, version_id, conn=conn)["status"]
        if to_status not in TRANSITIONS.get(current, ()):
            raise AppError("demand_plan_transition_invalid",
                           "This version cannot move to that status from where it is",
                           status_code=409, params={"from": current, "to": to_status})
        if to_status in ("approved", "rejected"):
            people = approvers(tenant_id)
            if not any(a["id"] == actor_id for a in people):
                raise AppError("demand_plan_not_approver",
                               "Only an administrator or a flagged approver can decide on a plan",
                               status_code=403)
            sub = query_one(
                """SELECT actor_id FROM demand_plan_version_events
                    WHERE tenant_id = %s AND version_id = %s AND kind = 'status'
                      AND to_status = 'submitted'
                    ORDER BY id DESC LIMIT 1""",
                (tenant_id, version_id), conn=conn)
            submitter = sub["actor_id"] if sub else None
            if to_status == "approved" and submitter == actor_id:
                if len(people) > 1:
                    raise AppError("demand_plan_self_approval",
                                   "Somebody other than the person who submitted it must approve it",
                                   status_code=403,
                                   params={"approvers": len(people)})
                self_approved = True     # the only approver there is; recorded as such
        details = {"self_approved": True} if self_approved else {}
        _insert_event(conn, tenant_id, version_id, kind="status", actor_id=actor_id,
                      from_status=current, to_status=to_status, comment=comment,
                      details=details)
        if to_status == "approved":
            # One approved plan at a time: the one it replaces stays, marked.
            older = query(
                f"""SELECT v.id FROM demand_plan_versions v
                     WHERE v.tenant_id = %s AND v.id <> %s AND {_STATUS_SQL} = 'approved'""",
                (tenant_id, version_id), conn=conn)
            for r in older:
                _insert_event(conn, tenant_id, r["id"], kind="status", actor_id=actor_id,
                              from_status="approved", to_status="superseded",
                              details={"superseded_by": version_id})
                superseded.append(r["id"])
    out = get_version(tenant_id, version_id)
    out["superseded"] = superseded
    out["self_approved"] = self_approved
    return out


def add_comment(tenant_id: str, version_id: str, actor_id: str, comment: str) -> dict:
    text = (comment or "").strip()[:MAX_COMMENT_LENGTH]
    if not text:
        raise AppError("demand_plan_comment_required", "Write a comment")
    with transaction() as conn:
        _row(tenant_id, version_id, conn=conn)
        _insert_event(conn, tenant_id, version_id, kind="comment", actor_id=actor_id,
                      comment=text)
    return get_version(tenant_id, version_id)


# ── Measuring a version against what sold ────────────────────────────────────

def _pick_actuals(tenant_id: str, session: dict, cols: dict, target_freq,
                  snapshot: dict, today: date, dataset_id: Optional[str]):
    """(dataset, {sku: {date: units}}, reason). When no dataset is named, the one
    giving the most comparable points wins (the same picker the forecast check
    and the adjustment grading use)."""
    from backend.dataframes.actuals import load_actual_series
    from backend.datasets.service import get_dataset
    from backend.forecast_check import service as fc_service

    if dataset_id:
        ds = get_dataset(tenant_id, dataset_id)
        if not ds:
            return None, None, "dataset_not_found"
        to_try = [ds]
    else:
        periods = snapshot.get("periods") or []
        candidates = fc_service._candidates(
            tenant_id, session, cols["date"],
            periods[0] if periods else None, periods[-1] if periods else None)
        ranked = fc_service._rank_for_auto_pick(candidates, session.get("backtest_source_dataset_id"))
        to_try = [d for d in (get_dataset(tenant_id, c["dataset_id"])
                              for c in ranked[:fc_service.MAX_CANDIDATES_TRIED]) if d]
        if not to_try:
            return None, None, "no_data_yet"
    best, best_n, reason = None, 0, "no_data_yet"
    for ds in to_try:
        try:
            loaded = load_actual_series(ds["file_path"], cols["date"], cols["target"],
                                        list(cols["group_keys"]), target_freq)
        except Exception as exc:  # an unreadable file is a reason, not a 500
            log.warning("demand plan accuracy: could not read dataset %s: %s", ds["id"], exc)
            reason = "unreadable"
            continue
        if "error" in loaded:
            reason = loaded["error"]
            continue
        actuals = plan_math.actuals_by_sku(loaded["series"])
        n = len(plan_math.compare_points(snapshot, actuals, today)[0])
        if n > best_n:
            best, best_n = (ds, actuals), n
    if best is None:
        return None, None, reason
    return best[0], best[1], "ok"


def accuracy(tenant_id: str, version_id: str, dataset_id: Optional[str] = None,
             today: Optional[date] = None) -> dict:
    """The version (and the statistical forecast frozen beside it) against real
    sales, over the periods that have fully passed.

    status: ok | periods_not_passed | no_actuals | no_data_yet | dataset_not_found
    | unreadable | <the loader's own reasons>
    """
    from backend.sessions.service import get_session
    from backend.workers.runner import build_engine_config

    today = today or date.today()
    meta = _row(tenant_id, version_id)
    snapshot = snapshot_of(tenant_id, version_id)
    head = {"version_id": version_id, "version_status": meta["status"], "source": None}
    if not plan_math.passed_period_indexes(snapshot, today):
        empty = plan_math.compare_with_actuals(snapshot, {}, today)
        return {**head, **empty}
    session = get_session(tenant_id, meta["session_id"])
    if not session:
        return {**head, **plan_math.compare_with_actuals(snapshot, {}, today),
                "status": "session_missing"}
    cfg = build_engine_config(tenant_id, session["id"])
    ds, actuals, reason = _pick_actuals(
        tenant_id, session, cfg["columns"], (cfg.get("granularity") or {}).get("target_freq"),
        snapshot, today, dataset_id)
    if ds is None:
        return {**head, **plan_math.compare_with_actuals(snapshot, {}, today), "status": reason}
    return {**head, "source": {"dataset_id": ds["id"], "name": ds["name"]},
            **plan_math.compare_with_actuals(snapshot, actuals, today)}
