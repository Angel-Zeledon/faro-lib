"""Chained purchase-order approval: the Python decision path.

Cost centers and approval chains are MANAGED by Rust
(`backend-rs/src/routes/cost_centers.rs`, no Python route and no failover).
Everything that DECIDES an order is still Python (`po_approval_service`), so
this module is the Python half: it reads the rows Rust writes and applies the
same rules (`cost_center_chain_core.py`, differential-tested against
`backend-rs/src/chain.rs`).

How it plugs into `po_approval_service`, each in one line:

* `requirement()` -> `overlay()`: with no active chain it returns the rule
  result untouched (no new key). With one, an order the chain covers is
  `required`, or `chain_unresolved` (fail closed) when its center or chain
  cannot be resolved; it can never come out "not required" by being unclear.
* `assert_sendable()` raises `po_approval_chain_unresolved` for that status.
* `request_approval()` -> `on_request()`: snapshots the chain (levels and a
  fingerprint) on the request and opens one pending step per level.
* `decide()` -> `route_decision()`: a chained request is decided level by level,
  in order. The request is approved only when the LAST level approves; any
  level's rejection rejects the request.
* `describe()` -> `decorate()`: steps, and `can_decide` for the level now open.

Rules of a chained request (the fail-closed ones):

* the requester never approves a level; nobody approves two levels of one
  request; a level is decided by somebody who fits it (role or named person);
* a request keeps the levels it was asked under (snapshot). If the order, its
  center or the chain changes so the fingerprint differs, the open request can
  no longer be decided (`po_approval_chain_changed`) and asking again replaces
  it. An approval made under an older fingerprint no longer counts either;
* a chain with a level nobody else can fill is refused at request time
  (`po_approval_no_approver`), never left to freeze the order.

DELEGATION (feat/approval-delegation) composes through ONE predicate:
`level_eligible(level, user)` in the core. A substitute may decide a level iff
the delegator would be eligible for it (`user` = the delegator row) and the
delegation's own limits hold (date range, warehouse scope, self-approval); a
delegation never skips a level, never lets one person decide two levels
(compare the substitute AND the delegator against earlier deciders), and the
step records `decided_by` = substitute with the delegator in `delegation_id`
columns added by that branch. See docs/rust-migration.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError
from backend.inventory import cost_center_chain_core as core

log = logging.getLogger(__name__)

STATUS_UNRESOLVED = "chain_unresolved"
SUPERSEDED_COMMENT = "superseded:chain_changed"


# ── Loading ──────────────────────────────────────────────────────────────────

def chains_active(tenant_id: str) -> bool:
    return query_one("SELECT 1 AS x FROM approval_chains WHERE tenant_id = %s AND active LIMIT 1",
                     (tenant_id,)) is not None


def load_centers(tenant_id: str) -> list[dict]:
    return [dict(r) for r in query(
        "SELECT id, parent_id, active FROM cost_centers WHERE tenant_id = %s", (tenant_id,))]


def load_chains(tenant_id: str) -> list[dict]:
    chains = [dict(r) for r in query(
        "SELECT id, cost_center_id, active FROM approval_chains WHERE tenant_id = %s",
        (tenant_id,))]
    bands: dict[str, list[dict]] = {}
    for b in query("SELECT chain_id, min_amount, levels FROM approval_chain_bands "
                   "WHERE tenant_id = %s", (tenant_id,)):
        levels = b["levels"]
        if isinstance(levels, str):
            try:
                levels = json.loads(levels)
            except ValueError:
                levels = None
        bands.setdefault(b["chain_id"], []).append(
            {"min_amount": float(b["min_amount"]), "levels": levels})
    for c in chains:
        c["bands"] = bands.get(c["id"], [])
    return chains


def context(tenant_id: str) -> Optional[dict]:
    """Preloaded chains and centers for a caller walking a list; None when the
    tenant has no active chain (the cheap, common case)."""
    if not chains_active(tenant_id):
        return None
    return {"chains": load_chains(tenant_id), "centers": load_centers(tenant_id)}


def resolve_order(tenant_id: str, po: dict, amount: Optional[float],
                  ctx: Optional[dict] = None) -> dict:
    ctx = ctx or {"chains": load_chains(tenant_id), "centers": load_centers(tenant_id)}
    return core.resolve(ctx["chains"], ctx["centers"], po.get("cost_center_id"), amount,
                        bool(po.get("chain_escalate")))


def _chain_view(res: dict) -> dict:
    return {k: res[k] for k in ("state", "reason", "chain_id", "min_amount", "levels",
                                "fingerprint", "escalated", "cost_center_id")}


def _approved_under(po_log_id: str, fingerprint: str) -> bool:
    """Was the order's latest approval granted under THIS chain fingerprint?"""
    row = query_one(
        "SELECT status, chain_fingerprint FROM po_approvals WHERE po_log_id = %s "
        "ORDER BY requested_at DESC, id DESC LIMIT 1", (po_log_id,))
    return bool(row and row["status"] == "approved" and row["chain_fingerprint"] == fingerprint)


# ── The requirement ──────────────────────────────────────────────────────────

def overlay(tenant_id: str, po: dict, base: dict, facts: Optional[dict] = None,
            ctx: Optional[dict] = None) -> dict:
    """Merge what the chain says into the rule-based `requirement()`."""
    from backend.inventory import po_approval_service as approvals
    if ctx is None:
        ctx = context(tenant_id)
    if ctx is None:
        return base
    if "cost_center_id" not in po or "chain_escalate" not in po:
        # A partial row (an explicit-column listing) must not read as "no
        # center": that would turn every such order into "unresolved".
        full = query_one("SELECT * FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
                         (po.get("id"), tenant_id))
        po = {**po, **(full or {})}
    facts = approvals._order_facts(tenant_id, po) if facts is None else facts
    amount = facts["amount"]
    res = resolve_order(tenant_id, po, amount, ctx)
    chain = _chain_view(res)
    if res["state"] == core.NOT_REQUIRED:
        return base
    shell = {"amount": amount, "amount_known": amount is not None, "rule_id": None,
             "self_approve_below": None, "chain": chain}
    if res["state"] == core.UNRESOLVED:
        return {**shell, "required": True, "status": STATUS_UNRESOLVED}
    state, approved_amount = po.get("approval_status"), po.get("approved_amount")
    if state == approvals.APPROVED and approved_amount is not None \
            and amount <= float(approved_amount) + approvals._EPS \
            and _approved_under(po["id"], res["fingerprint"]):
        return {**shell, "required": False, "status": approvals.APPROVED}
    if state == approvals.PENDING:
        status = approvals.PENDING
    elif state == approvals.REJECTED:
        status = approvals.REJECTED
    else:
        status = "approval_needed"      # never asked, grew, or approved under other terms
    return {**shell, "required": True, "status": status}


def unresolved_error(req: dict) -> AppError:
    return AppError(
        "po_approval_chain_unresolved",
        "This order cannot be approved yet: its cost center or approval chain is not resolved",
        status_code=409,
        params={"reason": (req.get("chain") or {}).get("reason"),
                "approval_status": STATUS_UNRESOLVED})


# ── Requesting ───────────────────────────────────────────────────────────────

def _people(tenant_id: str, conn=None) -> list[dict]:
    return [dict(r) for r in query(
        "SELECT id, email, full_name, role, status FROM users "
        "WHERE tenant_id = %s AND status = 'active' AND role IN ('admin', 'analyst')",
        (tenant_id,), conn=conn)]


def _is_stale(open_row: dict, chain: Optional[dict]) -> bool:
    """Is an open request no longer the request the order's rules call for?"""
    if open_row.get("chain_levels") is None:
        return chain is not None             # asked the old way, chain applies now
    return chain is None or chain["state"] != core.REQUIRED \
        or chain["fingerprint"] != open_row["chain_fingerprint"]


def _supersede(tenant_id: str, open_row: dict) -> None:
    with transaction() as conn:
        won = query_one(
            """UPDATE po_approvals SET status = 'rejected', decided_at = NOW(), comment = %s
                WHERE id = %s AND tenant_id = %s AND status = 'requested' RETURNING id""",
            (SUPERSEDED_COMMENT, open_row["id"], tenant_id), conn=conn)
        if won is not None:
            execute(
                """UPDATE inventory_po_log SET approval_status = NULL, approved_amount = NULL
                    WHERE id = %s AND tenant_id = %s AND approval_status = 'pending_approval'""",
                (open_row["po_log_id"], tenant_id), conn=conn)
    log.info("[po-chain] SUPERSEDED tenant=%s po=%s approval=%s",
             tenant_id, open_row["po_log_id"], open_row["id"])


def on_request(tenant_id: str, po_log_id: str, po: dict, req: dict, user_id: str,
               note: Optional[str]) -> Optional[dict]:
    """Called by `request_approval` after the order is known and not cancelled.
    Returns the finished answer when the chain handles the request; None when
    the legacy path should continue (the caller re-reads the requirement,
    because a stale open request may have been replaced here)."""
    from backend.inventory import po_approval_service as approvals
    chain = req.get("chain")
    latest = approvals._latest(po_log_id)
    if latest and latest["status"] == "requested" and _is_stale(latest, chain):
        _supersede(tenant_id, latest)
        po = approvals._get_po(tenant_id, po_log_id)
        req = approvals.requirement(tenant_id, po)
        chain = req.get("chain")
    if chain is None:
        return None
    if req["status"] == STATUS_UNRESOLVED:
        raise unresolved_error(req)
    if req["status"] in (approvals.APPROVED, approvals.PENDING):
        return {**approvals.describe(tenant_id, po_log_id, user_id), "changed": False, "notified": 0}

    levels = chain["levels"]
    amount = float(req["amount"])
    people = _people(tenant_id)
    short = core.staffing(levels, people, user_id)
    if short is not None:
        raise AppError(
            "po_approval_no_approver",
            "Nobody else can approve this order; an admin must flag an approver",
            status_code=409, params={"level": short})

    clean_note = (note or "").strip()[:approvals.MAX_COMMENT_LENGTH] or None
    try:
        with transaction() as conn:
            row = query_one(
                """INSERT INTO po_approvals
                       (tenant_id, po_log_id, status, amount, requested_by, request_note,
                        chain_id, chain_fingerprint, chain_levels, cost_center_id)
                   VALUES (%s, %s, 'requested', %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id""",
                (tenant_id, po_log_id, amount, user_id, clean_note, chain["chain_id"],
                 chain["fingerprint"], json.dumps(levels), po.get("cost_center_id")), conn=conn)
            for n, level in enumerate(levels, start=1):
                execute(
                    """INSERT INTO po_approval_steps (tenant_id, approval_id, level_no, level)
                       VALUES (%s, %s, %s, %s::jsonb)""",
                    (tenant_id, row["id"], n, json.dumps(level)), conn=conn)
            execute(
                """UPDATE inventory_po_log SET approval_status = %s, approved_amount = NULL
                    WHERE id = %s AND tenant_id = %s""",
                (approvals.PENDING, po_log_id, tenant_id), conn=conn)
    except Exception as exc:
        if getattr(exc, "pgcode", "") != "23505":
            raise
        return {**approvals.describe(tenant_id, po_log_id, user_id), "changed": False, "notified": 0}

    log.info("[po-chain] REQUEST tenant=%s po=%s by=%s amount=%s levels=%d",
             tenant_id, po_log_id, user_id, amount, len(levels))
    first = [p for p in people if core.level_eligible(levels[0], p) and p["id"] != user_id]
    notified = approvals._notify_approvers(tenant_id, po, amount, user_id, first)
    return {**approvals.describe(tenant_id, po_log_id, user_id), "changed": True,
            "notified": notified}


# ── Deciding ─────────────────────────────────────────────────────────────────

def _steps(approval_id: str, conn=None, lock: bool = False) -> list[dict]:
    return [dict(r) for r in query(
        "SELECT * FROM po_approval_steps WHERE approval_id = %s ORDER BY level_no"
        + (" FOR UPDATE" if lock else ""), (approval_id,), conn=conn)]


def _level_of(step: dict) -> dict:
    lvl = step["level"]
    if isinstance(lvl, str):
        lvl = json.loads(lvl)
    return lvl


def _may_decide_step(user: dict, requester_id: str, steps: list[dict], decision: str) -> bool:
    """May `user` decide the first pending step of `steps`?"""
    current = next((s for s in steps if s["status"] == "pending"), None)
    if current is None or not core.level_eligible(_level_of(current), user):
        return False
    if decision == "approved":
        if user["id"] == requester_id:
            return False
        if any(s["decided_by"] == user["id"] for s in steps if s["status"] == "approved"):
            return False
    return True


def route_decision(tenant_id: str, po: dict, latest: dict, user_id: str, decision: str,
                   comment: Optional[str]) -> Optional[dict]:
    """Called by `decide` once the request is known to be open. None = a legacy
    request, the old path continues. Raises when the request is stale."""
    from backend.inventory import po_approval_service as approvals
    req = approvals.requirement(tenant_id, po)
    chain = req.get("chain")
    if latest.get("chain_levels") is None:
        if chain is not None:
            raise AppError("po_approval_chain_changed",
                           "The order's approval chain changed; request approval again",
                           status_code=409)
        return None
    if chain is None or chain["state"] != core.REQUIRED \
            or chain["fingerprint"] != latest["chain_fingerprint"]:
        raise AppError("po_approval_chain_changed",
                       "The order's approval chain changed; request approval again",
                       status_code=409)
    return _decide_chained(tenant_id, po, latest, user_id, decision, comment, req)


def _decide_chained(tenant_id: str, po: dict, latest: dict, user_id: str, decision: str,
                    comment: Optional[str], req: dict) -> dict:
    from backend.inventory import po_approval_service as approvals
    user = query_one("SELECT id, role, status FROM users WHERE id = %s AND tenant_id = %s",
                     (user_id, tenant_id))
    if user is None:
        raise AppError("po_approval_not_approver", "You are not allowed to approve orders",
                       status_code=403)
    po_log_id = po["id"]
    final = False
    with transaction() as conn:
        row = query_one("SELECT * FROM po_approvals WHERE id = %s AND tenant_id = %s FOR UPDATE",
                        (latest["id"], tenant_id), conn=conn)
        steps = _steps(latest["id"], conn=conn, lock=True)
        if row is None:
            raise AppError("po_approval_chain_changed",
                           "The order's approval chain changed; request approval again",
                           status_code=409)
        if row["status"] != "requested":
            outcome = row["status"]
            replay = outcome == decision
        else:
            outcome, replay = None, False
            mine = [s for s in steps if s["decided_by"] == user_id and s["status"] == decision]
            if mine:
                replay = True            # this person already took this decision
        if not replay and outcome is None:
            current = next((s for s in steps if s["status"] == "pending"), None)
            if current is None:
                raise AppError("po_approval_chain_changed",
                               "The order's approval chain changed; request approval again",
                               status_code=409)
            level = _level_of(current)
            if decision == "approved" and user_id == row["requested_by"]:
                raise AppError("po_approval_self_approval",
                               "You cannot approve your own order at this value", status_code=403)
            if not core.level_eligible(level, user):
                raise AppError("po_approval_chain_wrong_level",
                               "This level of the approval chain is not yours to decide",
                               status_code=403, params={"level": current["level_no"]})
            # "Nobody approves two levels": a person who already approved an earlier
            # level is caught by the replay check above (their approval is on record,
            # so repeating it is a no-op, never a second signature), and
            # `describe` tells them `can_decide: false` for the level now open.
            won = query_one(
                """UPDATE po_approval_steps SET status = %s, decided_by = %s,
                          decided_at = NOW(), comment = %s
                    WHERE id = %s AND tenant_id = %s AND status = 'pending' RETURNING id""",
                (decision, user_id, comment, current["id"], tenant_id), conn=conn)
            if won is None:     # unreachable while the row lock is held; fail closed
                raise AppError("po_approval_already_decided", "This request was already decided",
                               status_code=409, params={"decision": None})
            final = decision == "rejected" or current["level_no"] == len(steps)
            if final:
                amount_now = float(req["amount"]) if req.get("amount") is not None \
                    else float(row["amount"])
                query_one(
                    """UPDATE po_approvals SET status = %s, decided_by = %s, decided_at = NOW(),
                              comment = %s WHERE id = %s AND tenant_id = %s
                          AND status = 'requested' RETURNING id""",
                    (decision, user_id, comment, row["id"], tenant_id), conn=conn)
                execute(
                    """UPDATE inventory_po_log SET approval_status = %s, approved_amount = %s
                        WHERE id = %s AND tenant_id = %s""",
                    (approvals.APPROVED if decision == "approved" else approvals.REJECTED,
                     amount_now if decision == "approved" else None, po_log_id, tenant_id),
                    conn=conn)
            decided_level = current["level_no"]
    if replay:
        return {**approvals.describe(tenant_id, po_log_id, user_id), "changed": False}
    if outcome is not None:
        raise AppError("po_approval_already_decided", "This request was already decided",
                       status_code=409, params={"decision": outcome})

    log.info("[po-chain] %s tenant=%s po=%s by=%s level=%s final=%s",
             decision.upper(), tenant_id, po_log_id, user_id, decided_level, final)
    amount = float(latest["amount"])
    if final:
        from backend.webhooks.service import emit_po_event
        emit_po_event(tenant_id, f"purchase_order.{decision}", po_log_id, decided_by=user_id)
        approvals._notify_requester(tenant_id, po, latest["requested_by"], decision, comment,
                                    amount, user_id)
    else:
        levels = [_level_of(s) for s in steps]
        nxt = levels[decided_level]
        earlier = {s["decided_by"] for s in steps if s["decided_by"]} | {user_id}
        eligible = [p for p in _people(tenant_id)
                    if core.level_eligible(nxt, p) and p["id"] != latest["requested_by"]
                    and p["id"] not in earlier]
        approvals._notify_approvers(tenant_id, po, amount, latest["requested_by"], eligible)
    return {**approvals.describe(tenant_id, po_log_id, user_id), "changed": True,
            "po_number": po.get("po_number"), "amount": amount, "comment": comment,
            "level": decided_level, "levels_total": len(steps),
            "level_progress": not final}


# ── Reading ──────────────────────────────────────────────────────────────────

def _names(tenant_id: str, user_ids: set[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    out = {}
    for r in query("SELECT id, full_name, email FROM users WHERE tenant_id = %s AND id = ANY(%s)",
                   (tenant_id, list(user_ids))):
        out[r["id"]] = r["full_name"] or (r["email"] or "").split("@")[0] or None
    return out


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def steps_view(tenant_id: str, approval_ids: list[str]) -> dict[str, list[dict]]:
    if not approval_ids:
        return {}
    rows = [dict(r) for r in query(
        "SELECT * FROM po_approval_steps WHERE tenant_id = %s AND approval_id = ANY(%s) "
        "ORDER BY approval_id, level_no", (tenant_id, approval_ids))]
    ids: set[str] = set()
    for r in rows:
        r["level"] = _level_of(r)
        if r["level"]["kind"] == "users":
            ids.update(r["level"]["user_ids"])
        if r["decided_by"]:
            ids.add(r["decided_by"])
    names = _names(tenant_id, ids)
    out: dict[str, list[dict]] = {}
    for r in rows:
        lvl = dict(r["level"])
        if lvl["kind"] == "users":
            lvl["users"] = [{"id": i, "name": names.get(i)} for i in lvl["user_ids"]]
        out.setdefault(r["approval_id"], []).append({
            "level_no": r["level_no"], "level": lvl, "status": r["status"],
            "decided_by": r["decided_by"], "decided_by_name": names.get(r["decided_by"]),
            "decided_at": _iso(r["decided_at"]), "comment": r["comment"]})
    return out


def decorate(tenant_id: str, po: dict, user_id: Optional[str], result: dict) -> dict:
    """Add steps to a `describe()` answer, and decide `can_decide` for a chained
    open request from the level now open (the rule-based flag does not apply)."""
    hist = result.get("history") or []
    chained_ids = [h["id"] for h in hist if h.get("id")]
    if not chained_ids or not chains_exist(tenant_id):
        return result
    by_approval = steps_view(tenant_id, chained_ids)
    if not by_approval:
        return result
    for h in hist:
        if h["id"] in by_approval:
            h["steps"] = by_approval[h["id"]]
    open_request = result.get("open_request")
    if open_request and open_request["id"] in by_approval:
        steps = by_approval[open_request["id"]]
        can = False
        chain = result.get("chain")
        fresh = bool(chain and chain.get("state") == core.REQUIRED)
        if user_id and fresh:
            row = query_one("SELECT id, role, status FROM users WHERE id = %s AND tenant_id = %s",
                            (user_id, tenant_id))
            raw = [{"status": s["status"], "decided_by": s["decided_by"],
                    "level": s["level"]} for s in steps]
            can = bool(row) and _may_decide_step(row, open_request["requested_by"], raw, "approved")
        result["can_decide"] = can
        result["open_request"] = {**open_request, "steps": steps}
    return result


def chains_exist(tenant_id: str) -> bool:
    """Any chained request ever (even if chains were switched off since)."""
    return query_one("SELECT 1 AS x FROM po_approvals WHERE tenant_id = %s "
                     "AND chain_levels IS NOT NULL LIMIT 1", (tenant_id,)) is not None


def merge_inbox(tenant_id: str, user_id: str, inbox: dict) -> dict:
    """The approver's inbox with chained requests replaced by the ones whose
    OPEN level this person can decide."""
    if not chains_exist(tenant_id):
        return inbox
    from backend.inventory import po_approval_service as approvals
    from backend.inventory.roi_service import format_po_number
    rows = query(
        """SELECT a.id AS approval_id, a.po_log_id, a.amount, a.requested_by, a.requested_at,
                  a.request_note, a.chain_fingerprint, l.po_number, l.sku_count,
                  l.destination_warehouse, rq.full_name AS requested_by_name,
                  rq.email AS requested_by_email,
                  (SELECT string_agg(DISTINCT i.supplier, ', ') FROM inventory_po_items i
                    WHERE i.po_log_id = l.id AND i.status IN ('approved', 'modified')
                      AND i.supplier IS NOT NULL) AS suppliers
             FROM po_approvals a
             JOIN inventory_po_log l ON l.id = a.po_log_id
             LEFT JOIN users rq ON rq.id = a.requested_by
            WHERE a.tenant_id = %s AND a.status = 'requested' AND a.chain_levels IS NOT NULL
              AND l.cancelled_at IS NULL
            ORDER BY a.requested_at""", (tenant_id,))
    chained_ids = {r["approval_id"] for r in rows}
    items = [i for i in inbox["items"] if i["approval_id"] not in chained_ids]
    user = query_one("SELECT id, role, status FROM users WHERE id = %s AND tenant_id = %s",
                     (user_id, tenant_id))
    mine: list[dict] = []
    if user and rows:
        steps = steps_view(tenant_id, [r["approval_id"] for r in rows])
        for r in rows:
            po = approvals._get_po(tenant_id, r["po_log_id"])
            chain = approvals.requirement(tenant_id, po).get("chain")
            if not chain or chain["state"] != core.REQUIRED \
                    or chain["fingerprint"] != r["chain_fingerprint"]:
                continue
            raw = steps.get(r["approval_id"], [])
            if not _may_decide_step(user, r["requested_by"], raw, "approved"):
                continue
            current = next((s for s in raw if s["status"] == "pending"), None)
            mine.append({
                "approval_id": r["approval_id"], "po_log_id": r["po_log_id"],
                "reference": format_po_number(r["po_number"], r["po_log_id"]),
                "amount": float(r["amount"]), "sku_count": r["sku_count"],
                "suppliers": r["suppliers"], "warehouse": r["destination_warehouse"],
                "requested_by_name": approvals._name_of(
                    {"full_name": r["requested_by_name"], "email": r["requested_by_email"]}),
                "requested_at": _iso(r["requested_at"]), "note": r["request_note"],
                "can_decide": True, "level_no": current["level_no"] if current else None,
                "levels_total": len(raw)})
    if not mine:
        return {**inbox, "items": items}
    return {**inbox, "items": items + mine,
            "is_approver": True, "is_chain_approver": True}


# ── Attribution ──────────────────────────────────────────────────────────────

def require_active_center(tenant_id: str, center_id: str) -> dict:
    """The tenant's ACTIVE cost center, or the error that names what is wrong."""
    row = query_one("SELECT id, code, name, active FROM cost_centers WHERE id = %s AND tenant_id = %s",
                    (center_id, tenant_id))
    if row is None:
        raise AppError("cost_center_not_found", "Cost center not found", status_code=404)
    if not row["active"]:
        raise AppError("cost_center_inactive", "That cost center is not active", status_code=409)
    return dict(row)
