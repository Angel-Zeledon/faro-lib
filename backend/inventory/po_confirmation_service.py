"""
Supplier confirmation link: the database side (rules live in
`po_confirmation_core.py`, schema in `po_confirmation_migrations.py`).

Two audiences, kept apart on purpose:

* the SUPPLIER, who holds a link and nothing else. `resolve_active` is the only
  door: any link that is malformed, unknown, revoked, expired, or whose order was
  cancelled raises the SAME `supplier_portal_not_found` 404, so nobody can tell
  those cases apart or probe for which orders exist. What the supplier sees is a
  whitelist (`portal_view`): product names, SKU codes, ordered quantity, unit,
  requested date. Never a price, cost, stock figure, another supplier's lines or
  another order.
* the BUYER, signed in, who sees the answers (`list_for_po`, `summary`), accepts a
  proposed change (`accept`), reopens a locked page or revokes a link.

Nothing the supplier says changes what drives purchasing by itself. A proposed
date or quantity is recorded; the order's expected arrival moves only for a line
whose proposal a person accepted (`accepted_promises`, read by
`reception_service.get_overdue_receptions`). With no confirmations on file every
reader behaves exactly as it did before this feature existed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from backend.config import settings
from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError
from backend.inventory import po_confirmation_core as core

log = logging.getLogger(__name__)

_ORDERED = ("approved", "modified")
_USER_AGENT_MAX = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else value


def not_found() -> AppError:
    """The one answer for every bad link. Same status, code and text whether the
    token is malformed, unknown, revoked, expired or its order was cancelled."""
    return AppError("supplier_portal_not_found",
                    "This link is not valid or is no longer available",
                    status_code=404)


def portal_url(token: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}/proveedor/{token}"


# ── Lines ────────────────────────────────────────────────────────────────────

def ordered_lines(tenant_id: str, po_log_id: str, supplier: str,
                  conn: Optional[Any] = None) -> list[dict]:
    """The lines one supplier is asked to answer: ordered lines of this order
    whose supplier name is exactly the one the send flow grouped them by."""
    from backend.inventory import reception_service as rec
    items = rec.get_po_items(tenant_id, po_log_id, conn=conn)
    return [
        i for i in items
        if i["status"] in _ORDERED and (i.get("supplier") or "").strip() == supplier
    ]


def _units_by_sku(tenant_id: str, skus: list[str]) -> dict[str, str]:
    if not skus:
        return {}
    rows = query(
        """SELECT sku, MAX(unit_of_measure) AS unit
             FROM inventory_stock
            WHERE tenant_id = %s AND sku = ANY(%s)
            GROUP BY sku""",
        (tenant_id, skus),
    )
    return {r["sku"]: r["unit"] for r in rows if r.get("unit")}


# ── Issuing a link (buyer side, called by the send flow) ─────────────────────

def expected_date_for(tenant_id: str, po: dict, supplier: str) -> Optional[date]:
    """The date StockAI expects this supplier's goods: order date plus the
    supplier's lead time. It is shown to the supplier as the requested date."""
    from backend.inventory import reception_service as rec
    generated = po.get("generated_at")
    if generated is None:
        return None
    lead_time, _source = rec._effective_lead_time(tenant_id, supplier)
    return (generated + timedelta(days=float(lead_time))).date()


def issue_link(
    tenant_id: str, po: dict, supplier: str, created_by: str, language: str,
) -> tuple[str, dict]:
    """Create (or re-issue) the one link for this order and supplier.

    Returns (raw token, request row). The raw token exists only in the return
    value: what is stored is its hash, so re-sending an order rotates the token
    (the previous link stops working) instead of reading an old one back. A
    re-issue never reopens a locked answer and never drops the answers already
    on file; it does clear a revocation, because a buyer sending the order again
    is deciding the supplier should be able to answer.
    """
    now = _now()
    requested = expected_date_for(tenant_id, po, supplier)
    expires = core.compute_expiry(now, requested)
    token = core.new_token()
    lang = language if language in ("es", "en") else "es"
    rows = query(
        """INSERT INTO po_confirmation_requests
               (tenant_id, po_log_id, supplier, token_hash, requested_date,
                language, expires_at, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, po_log_id, supplier) DO UPDATE
              SET token_hash      = EXCLUDED.token_hash,
                  token_issued_at = NOW(),
                  expires_at      = GREATEST(po_confirmation_requests.expires_at,
                                             EXCLUDED.expires_at),
                  revoked_at      = NULL,
                  revoked_by      = NULL,
                  language        = EXCLUDED.language,
                  requested_date  = COALESCE(po_confirmation_requests.requested_date,
                                             EXCLUDED.requested_date)
           RETURNING *""",
        (tenant_id, po["id"], supplier, core.hash_token(token), requested,
         lang, expires, created_by),
    )
    return token, rows[0]


# ── The supplier's door ──────────────────────────────────────────────────────

def resolve_active(token: object, conn: Optional[Any] = None,
                   for_update: bool = False) -> dict:
    """The request a token opens, or the one generic 404."""
    if not core.token_is_wellformed(token):
        raise not_found()
    sql = "SELECT * FROM po_confirmation_requests WHERE token_hash = %s"
    if for_update:
        sql += " FOR UPDATE"
    row = query_one(sql, (core.hash_token(token),), conn=conn)
    if not row or not core.verify_token(token, row["token_hash"]):
        raise not_found()
    if not core.link_is_usable(expires_at=row["expires_at"],
                               revoked_at=row["revoked_at"], now=_now()):
        raise not_found()
    po = query_one(
        "SELECT id, po_number, generated_at, cancelled_at FROM inventory_po_log "
        "WHERE id = %s AND tenant_id = %s",
        (row["po_log_id"], row["tenant_id"]), conn=conn,
    )
    # A cancelled order must not collect promises: the buyer abandoned it.
    if not po or po.get("cancelled_at") is not None:
        raise not_found()
    row["_po"] = po
    return row


def _latest_responses(request_id: str, conn: Optional[Any] = None) -> dict[str, dict]:
    rows = query(
        """SELECT DISTINCT ON (c.po_item_id)
                  c.id, c.po_item_id, c.revision, c.confirmed_qty, c.promised_date,
                  c.status, c.note, c.submitted_at,
                  a.accepted_at, a.accepted_by
             FROM po_line_confirmations c
             LEFT JOIN po_confirmation_acceptances a ON a.confirmation_id = c.id
            WHERE c.request_id = %s
            ORDER BY c.po_item_id, c.revision DESC""",
        (request_id,), conn=conn,
    )
    return {r["po_item_id"]: r for r in rows}


def portal_view(token: object) -> dict:
    """What a supplier may read. A WHITELIST: each field below is chosen, nothing
    is copied from the order wholesale, so a column added to the order tables
    tomorrow cannot leak through here."""
    req = resolve_active(token)
    tenant_id = req["tenant_id"]
    lines = ordered_lines(tenant_id, req["po_log_id"], req["supplier"])
    if not lines:
        raise not_found()
    from backend.inventory.roi_service import format_po_number
    units = _units_by_sku(tenant_id, [l["sku"] for l in lines])
    responses = _latest_responses(req["id"])
    tenant = query_one("SELECT name FROM tenants WHERE id = %s", (tenant_id,))
    execute("UPDATE po_confirmation_requests SET last_viewed_at = NOW() WHERE id = %s",
            (req["id"],))
    locked = core.is_locked(req["submitted_at"], req["reopened_at"])
    out_lines = []
    for l in lines:
        resp = responses.get(l["id"])
        out_lines.append({
            "line_id": l["id"],
            "sku": l["sku"],
            "name": l.get("display_name") or l["sku"],
            "quantity": float(l.get("final_qty") or 0),
            "unit": units.get(l["sku"]),
            "requested_date": _iso(req["requested_date"]),
            "response": None if resp is None else {
                "status": resp["status"],
                "confirmed_qty": resp["confirmed_qty"],
                "promised_date": _iso(resp["promised_date"]),
                "note": resp["note"],
            },
        })
    return {
        "reference": format_po_number(req["_po"].get("po_number"), req["po_log_id"]),
        "buyer": (tenant or {}).get("name"),
        "supplier": req["supplier"],
        "language": req["language"],
        "requested_date": _iso(req["requested_date"]),
        "expires_at": _iso(req["expires_at"]),
        "locked": locked,
        "submitted_at": _iso(req["submitted_at"]),
        "lines": out_lines,
    }


def submit(token: object, raw_lines: object, *, ip_hash: Optional[str],
           user_agent: Optional[str]) -> dict:
    """Record the supplier's answer: one new revision per line, then lock."""
    # Cheap pre-check outside the lock so a bad link never takes a row lock.
    resolve_active(token)
    submission_id = str(uuid.uuid4())
    ua = (user_agent or "")[:_USER_AGENT_MAX] or None
    with transaction() as conn:
        req = resolve_active(token, conn=conn, for_update=True)
        if core.is_locked(req["submitted_at"], req["reopened_at"]):
            raise AppError("supplier_portal_locked",
                           "This order was already answered; ask the buyer to reopen it",
                           status_code=409)
        tenant_id = req["tenant_id"]
        lines = ordered_lines(tenant_id, req["po_log_id"], req["supplier"], conn=conn)
        lines_by_id = {l["id"]: {"ordered_qty": float(l.get("final_qty") or 0)}
                       for l in lines}
        if not lines_by_id:
            raise not_found()
        try:
            clean = core.validate_submission(
                raw_lines, lines_by_id, today=_now().date(),
                requested_date=req["requested_date"],
            )
        except core.SubmissionInvalid as exc:
            raise AppError("supplier_portal_invalid_submission",
                           "The answer could not be accepted",
                           status_code=422,
                           params={"reason": exc.code, **exc.params})
        revisions = {
            r["po_item_id"]: int(r["rev"])
            for r in query(
                """SELECT po_item_id, MAX(revision) AS rev
                     FROM po_line_confirmations WHERE request_id = %s
                    GROUP BY po_item_id""",
                (req["id"],), conn=conn)
        }
        for row in clean:
            execute(
                """INSERT INTO po_line_confirmations
                       (tenant_id, request_id, po_log_id, po_item_id, revision,
                        confirmed_qty, promised_date, status, note,
                        submission_id, ip_hash, user_agent)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (tenant_id, req["id"], req["po_log_id"], row["po_item_id"],
                 revisions.get(row["po_item_id"], 0) + 1, row["confirmed_qty"],
                 row["promised_date"], row["status"], row["note"],
                 submission_id, ip_hash, ua),
                conn=conn,
            )
        execute("UPDATE po_confirmation_requests SET submitted_at = NOW() WHERE id = %s",
                (req["id"],), conn=conn)
    counts = {s: sum(1 for r in clean if r["status"] == s) for s in core.LINE_STATUSES}
    _notify_buyer(req, counts)
    return {"submitted": True, "counts": counts, "locked": True}


def _notify_buyer(req: dict, counts: dict[str, int]) -> None:
    """Activity event (and bell, when something needs a decision) plus an e-mail
    to the person who sent the order. Failures are logged, never raised: the
    supplier's answer is already stored and must not be reported as failed."""
    from backend.activity.events import record_event
    from backend.inventory.roi_service import format_po_number
    try:
        reference = format_po_number(req["_po"].get("po_number"), req["po_log_id"])
        details = {"reference": reference, "supplier": req["supplier"],
                   "confirmed": counts[core.CONFIRMED]}
        needs_decision = counts[core.CHANGED] + counts[core.DECLINED] > 0
        if needs_decision:
            record_event(
                req["tenant_id"], req["created_by"],
                "purchase.supplier_changes_proposed", resource=req["po_log_id"],
                details={**details, "changed": counts[core.CHANGED],
                         "declined": counts[core.DECLINED]},
                reason="supplier_proposed_changes",
            )
        else:
            record_event(req["tenant_id"], req["created_by"],
                         "purchase.supplier_confirmed", resource=req["po_log_id"],
                         details=details)
    except Exception:  # noqa: BLE001 — see the docstring
        log.exception("[po-confirmation] event failed tenant=%s po=%s",
                      req["tenant_id"], req["po_log_id"])
    try:
        buyer = query_one(
            "SELECT email FROM users WHERE id = %s AND tenant_id = %s",
            (req["created_by"], req["tenant_id"]),
        )
        if buyer and buyer.get("email"):
            from backend.notifications import email as email_mod
            email_mod.send_supplier_response_email(
                to=buyer["email"], supplier_name=req["supplier"],
                po_ref=format_po_number(req["_po"].get("po_number"), req["po_log_id"]),
                confirmed=counts[core.CONFIRMED], changed=counts[core.CHANGED],
                declined=counts[core.DECLINED],
                url=f"{settings.frontend_url.rstrip('/')}/pedidos?po={req['po_log_id']}",
                language=req.get("language") or "es", tenant_id=req["tenant_id"],
            )
    except Exception:  # noqa: BLE001
        log.exception("[po-confirmation] buyer e-mail failed tenant=%s po=%s",
                      req["tenant_id"], req["po_log_id"])


# ── The buyer's views ────────────────────────────────────────────────────────

def _request_state(req: dict, now: datetime) -> str:
    if req["revoked_at"] is not None:
        return "revoked"
    return "expired" if core.is_expired(req["expires_at"], now) else "active"


def list_for_po(tenant_id: str, po_log_id: str) -> list[dict]:
    """Every supplier's link for an order, with each line's latest answer."""
    now = _now()
    reqs = query(
        """SELECT id, supplier, requested_date, language, expires_at, revoked_at,
                  created_at, submitted_at, reopened_at
             FROM po_confirmation_requests
            WHERE tenant_id = %s AND po_log_id = %s
            ORDER BY supplier""",
        (tenant_id, po_log_id),
    )
    out = []
    for req in reqs:
        lines = ordered_lines(tenant_id, po_log_id, req["supplier"])
        responses = _latest_responses(req["id"])
        line_rows = []
        statuses = []
        for l in lines:
            resp = responses.get(l["id"])
            if resp:
                statuses.append(resp["status"])
            line_rows.append({
                "line_id": l["id"], "sku": l["sku"],
                "name": l.get("display_name") or l["sku"],
                "ordered_qty": float(l.get("final_qty") or 0),
                "response": None if resp is None else {
                    "confirmation_id": resp["id"],
                    "revision": int(resp["revision"]),
                    "status": resp["status"],
                    "confirmed_qty": resp["confirmed_qty"],
                    "promised_date": _iso(resp["promised_date"]),
                    "note": resp["note"],
                    "submitted_at": _iso(resp["submitted_at"]),
                    "accepted": resp["accepted_at"] is not None,
                    "accepted_at": _iso(resp["accepted_at"]),
                    "acceptable": (resp["status"] == core.CHANGED
                                   and resp["accepted_at"] is None),
                },
            })
        answered = req["submitted_at"] is not None
        out.append({
            "request_id": req["id"],
            "supplier": req["supplier"],
            "status": core.derive_overall(statuses) if answered and statuses else "pending",
            "state": _request_state(req, now),
            "locked": core.is_locked(req["submitted_at"], req["reopened_at"]),
            "submitted_at": _iso(req["submitted_at"]),
            "expires_at": _iso(req["expires_at"]),
            "requested_date": _iso(req["requested_date"]),
            "pending_acceptance": sum(
                1 for r in line_rows if r["response"] and r["response"]["acceptable"]),
            "lines": line_rows,
        })
    return out


def summary(tenant_id: str, limit: int = 500) -> list[dict]:
    """One row per order that has a confirmation link: the chip the history list
    shows. Computed from the latest answer of every line."""
    rows = query(
        """SELECT r.po_log_id, r.id AS request_id, r.supplier, r.submitted_at,
                  r.revoked_at, r.expires_at, l.status, (a.confirmation_id IS NOT NULL) AS accepted
             FROM po_confirmation_requests r
             LEFT JOIN LATERAL (
                  SELECT DISTINCT ON (c.po_item_id) c.id, c.status
                    FROM po_line_confirmations c
                   WHERE c.request_id = r.id
                   ORDER BY c.po_item_id, c.revision DESC
             ) l ON TRUE
             LEFT JOIN po_confirmation_acceptances a ON a.confirmation_id = l.id
            WHERE r.tenant_id = %s
              AND r.po_log_id IN (
                  SELECT po_log_id FROM po_confirmation_requests
                   WHERE tenant_id = %s
                   GROUP BY po_log_id ORDER BY MAX(created_at) DESC LIMIT %s)""",
        (tenant_id, tenant_id, limit),
    )
    by_po: dict[str, dict] = {}
    for r in rows:
        po = by_po.setdefault(r["po_log_id"], {"requests": set(), "statuses": [],
                                               "pending": 0, "answered": False})
        po["requests"].add(r["request_id"])
        if r["submitted_at"] is not None:
            po["answered"] = True
        if r["status"]:
            po["statuses"].append(r["status"])
            if r["status"] == core.CHANGED and not r["accepted"]:
                po["pending"] += 1
    return [
        {
            "po_log_id": po_id,
            "status": core.derive_overall(v["statuses"]) if v["answered"] and v["statuses"]
                      else "pending",
            "pending_acceptance": v["pending"],
            "suppliers": len(v["requests"]),
        }
        for po_id, v in by_po.items()
    ]


def _get_request(tenant_id: str, po_log_id: str, request_id: str) -> dict:
    req = query_one(
        "SELECT * FROM po_confirmation_requests WHERE id = %s AND tenant_id = %s "
        "AND po_log_id = %s",
        (request_id, tenant_id, po_log_id),
    )
    if not req:
        raise AppError("confirmation_not_found", "Confirmation link not found",
                       status_code=404)
    return req


def accept(tenant_id: str, po_log_id: str, confirmation_id: str, user_id: str) -> dict:
    """The buyer accepts a supplier's proposed change for one line. Only the
    LATEST answer to a line, and only a 'changed' one, can be accepted; the
    acceptance row is what lets the proposed date drive the order's expected
    arrival. Idempotent."""
    row = query_one(
        """SELECT c.id, c.po_item_id, c.request_id, c.revision, c.status,
                  c.promised_date, c.confirmed_qty, r.supplier
             FROM po_line_confirmations c
             JOIN po_confirmation_requests r ON r.id = c.request_id
            WHERE c.id = %s AND c.tenant_id = %s AND c.po_log_id = %s""",
        (confirmation_id, tenant_id, po_log_id),
    )
    if not row:
        raise AppError("confirmation_not_found", "Confirmation not found", status_code=404)
    latest = query_one(
        "SELECT MAX(revision) AS rev FROM po_line_confirmations "
        "WHERE request_id = %s AND po_item_id = %s",
        (row["request_id"], row["po_item_id"]),
    )
    if int(latest["rev"]) != int(row["revision"]):
        raise AppError("confirmation_not_acceptable",
                       "The supplier sent a newer answer for this line",
                       status_code=409, params={"reason": "superseded"})
    if row["status"] != core.CHANGED:
        raise AppError("confirmation_not_acceptable",
                       "Only a proposed change can be accepted",
                       status_code=409, params={"reason": "not_a_change"})
    inserted = query(
        """INSERT INTO po_confirmation_acceptances
               (confirmation_id, tenant_id, po_log_id, accepted_by)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (confirmation_id) DO NOTHING
           RETURNING accepted_at""",
        (confirmation_id, tenant_id, po_log_id, user_id),
    )
    line = query_one("SELECT sku FROM inventory_po_items WHERE id = %s AND tenant_id = %s",
                     (row["po_item_id"], tenant_id))
    return {
        "confirmation_id": confirmation_id, "supplier": row["supplier"],
        "sku": (line or {}).get("sku"), "promised_date": _iso(row["promised_date"]),
        "changed": bool(inserted),
    }


def reopen(tenant_id: str, po_log_id: str, request_id: str, user_id: str) -> dict:
    """Let the supplier answer again (a new revision, the old ones are kept).
    Also gives the link a week from now when it had less left."""
    req = _get_request(tenant_id, po_log_id, request_id)
    if req["revoked_at"] is not None:
        raise AppError("confirmation_link_inactive",
                       "This link was revoked; send the order again to issue a new one",
                       status_code=409)
    if not core.is_locked(req["submitted_at"], req["reopened_at"]):
        return {"request_id": request_id, "supplier": req["supplier"], "changed": False}
    execute(
        """UPDATE po_confirmation_requests
              SET reopened_at = NOW(), reopened_by = %s,
                  expires_at = GREATEST(expires_at, NOW() + make_interval(days => %s))
            WHERE id = %s AND tenant_id = %s""",
        (user_id, core.DAYS_AFTER_ARRIVAL, request_id, tenant_id),
    )
    return {"request_id": request_id, "supplier": req["supplier"], "changed": True}


def revoke(tenant_id: str, po_log_id: str, request_id: str, user_id: str) -> dict:
    """Kill the link now. The answers already on file stay."""
    req = _get_request(tenant_id, po_log_id, request_id)
    if req["revoked_at"] is not None:
        return {"request_id": request_id, "supplier": req["supplier"], "changed": False}
    execute(
        "UPDATE po_confirmation_requests SET revoked_at = NOW(), revoked_by = %s "
        "WHERE id = %s AND tenant_id = %s AND revoked_at IS NULL",
        (user_id, request_id, tenant_id),
    )
    return {"request_id": request_id, "supplier": req["supplier"], "changed": True}


# ── What the rest of the product reads ───────────────────────────────────────

def accepted_promises(tenant_id: str, po_log_ids: list[str]) -> dict[tuple[str, str], dict[str, date]]:
    """{(po_log_id, supplier): {line id: promised date}} for the lines whose
    LATEST answer is a proposed change a person accepted. Empty when no order has
    any, which is the whole behaviour of the product before this feature."""
    if not po_log_ids:
        return {}
    rows = query(
        """SELECT c.po_log_id, r.supplier, c.po_item_id, c.promised_date
             FROM po_confirmation_acceptances a
             JOIN po_line_confirmations c ON c.id = a.confirmation_id
             JOIN po_confirmation_requests r ON r.id = c.request_id
            WHERE a.tenant_id = %s
              AND c.po_log_id = ANY(%s)
              AND c.status = 'changed'
              AND c.promised_date IS NOT NULL
              AND c.revision = (SELECT MAX(c2.revision) FROM po_line_confirmations c2
                                 WHERE c2.request_id = c.request_id
                                   AND c2.po_item_id = c.po_item_id)""",
        (tenant_id, list(po_log_ids)),
    )
    out: dict[tuple[str, str], dict[str, date]] = {}
    for r in rows:
        out.setdefault((r["po_log_id"], r["supplier"]), {})[r["po_item_id"]] = r["promised_date"]
    return out


def promise_stats(tenant_id: str) -> dict[str, dict]:
    """{LOWER(supplier): {n, kept, avg_slip_days}}: accepted promised dates vs
    the day the order was really received (only fully received orders)."""
    rows = query(
        """SELECT LOWER(r.supplier) AS supplier_key,
                  COUNT(*)::int AS n,
                  SUM(CASE WHEN (pol.received_at AT TIME ZONE 'UTC')::date <= c.promised_date
                           THEN 1 ELSE 0 END)::int AS kept,
                  AVG((pol.received_at AT TIME ZONE 'UTC')::date - c.promised_date) AS avg_slip
             FROM po_confirmation_acceptances a
             JOIN po_line_confirmations c ON c.id = a.confirmation_id
             JOIN po_confirmation_requests r ON r.id = c.request_id
             JOIN inventory_po_log pol ON pol.id = c.po_log_id
            WHERE a.tenant_id = %s
              AND c.status = 'changed' AND c.promised_date IS NOT NULL
              AND pol.reception_status = 'received' AND pol.received_at IS NOT NULL
              AND c.revision = (SELECT MAX(c2.revision) FROM po_line_confirmations c2
                                 WHERE c2.request_id = c.request_id
                                   AND c2.po_item_id = c.po_item_id)
            GROUP BY LOWER(r.supplier)""",
        (tenant_id,),
    )
    return {
        r["supplier_key"]: {
            "n": int(r["n"]), "kept": int(r["kept"] or 0),
            "avg_slip_days": None if r["avg_slip"] is None else round(float(r["avg_slip"]), 1),
        }
        for r in rows
    }


def attach_promise_stats(tenant_id: str, scorecard: list[dict]) -> list[dict]:
    """Add the 'promised vs real' columns to the supplier scorecard rows. Rows of
    suppliers with no accepted promise carry None, never zero."""
    stats = promise_stats(tenant_id)
    for row in scorecard:
        s = stats.get((row.get("supplier") or "").lower())
        row["promises_measured"] = s["n"] if s else 0
        row["promise_kept_rate"] = round(s["kept"] / s["n"], 3) if s and s["n"] else None
        row["promise_avg_slip_days"] = s["avg_slip_days"] if s else None
    return scorecard
