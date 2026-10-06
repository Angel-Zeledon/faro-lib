"""Purchase-order approval: an opt-in workflow that appears only when configured.

A tenant admin defines RULES ("an order worth 5,000 or more needs approval",
optionally narrowed to one warehouse or one supplier) and flags the people who
may approve. Until a rule exists none of this runs: `requirement()` answers
"not required", `assert_sendable()` returns, and every PO behaves exactly as it
did before.

The model, deliberately small:

* The rules are evaluated against the order AS IT IS NOW, never against a stored
  verdict. An order does not "become" approval-bound when somebody clicks a
  button; it is approval-bound the moment its value matches an active rule. So
  an order can never skip approval by never asking for it.
* `inventory_po_log.approval_status` is the current state (NULL / pending_approval
  / approved / rejected) and `approved_amount` the value the approver saw: an
  order that grew past it needs asking again. `po_approvals` keeps one row per
  request (who asked, who decided, when, why, and the amount at that moment).
* An order needing approval cannot leave the building. `assert_sendable` is
  called from every send path AND from the two lowest-level steps they share
  (`mark_po_sent`, `generate_po_pdf`), so a new caller that forgets it still
  cannot deliver an unapproved order.
* Self-approval: the requester may approve their own order only while it is worth
  LESS than the rule's `self_approve_below`; above it somebody else must.

An order whose value cannot be computed (no line carries a unit cost) matches no
threshold: there is no number to compare. The check says so (`amount_known`)
instead of pretending it passed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.db.connection import execute, query, query_one, transaction
from backend.errors import AppError

log = logging.getLogger(__name__)

PENDING = "pending_approval"
APPROVED = "approved"
REJECTED = "rejected"

MAX_COMMENT_LENGTH = 500
MIN_REJECT_REASON_LENGTH = 3
_UNIQUE_VIOLATION = "23505"
_EPS = 0.005


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


# ── Rules ────────────────────────────────────────────────────────────────────

def list_rules(tenant_id: str) -> list[dict]:
    rows = query(
        """SELECT r.id, r.threshold, r.warehouse, r.supplier_id,
                  s.name AS supplier_name, r.self_approve_below, r.active,
                  r.created_by, r.created_at, r.updated_at
             FROM po_approval_rules r
             LEFT JOIN suppliers s ON s.id = r.supplier_id AND s.tenant_id = r.tenant_id
            WHERE r.tenant_id = %s
            ORDER BY r.threshold, r.created_at""",
        (tenant_id,),
    )
    return [{**dict(r), "created_at": _iso(r["created_at"]),
             "updated_at": _iso(r["updated_at"])} for r in rows]


def _clean_rule(tenant_id: str, data: dict) -> dict:
    threshold = float(data["threshold"])
    if not threshold > 0:
        raise AppError("po_approval_rule_invalid", "The threshold must be above zero",
                       params={"field": "threshold"})
    below = data.get("self_approve_below")
    if below is not None:
        below = float(below)
        if not below > threshold:
            raise AppError(
                "po_approval_rule_invalid",
                "The self-approval limit must be above the threshold",
                params={"field": "self_approve_below"})
    warehouse = (data.get("warehouse") or "").strip() or None
    if warehouse:
        from backend.inventory import warehouse_service as wh_svc
        warehouse = wh_svc.resolve_canonical_name(tenant_id, warehouse)
    supplier_id = (data.get("supplier_id") or "").strip() or None
    if supplier_id and not query_one(
            "SELECT 1 AS x FROM suppliers WHERE id = %s AND tenant_id = %s",
            (supplier_id, tenant_id)):
        raise AppError("supplier_not_found", "Supplier not found", status_code=404)
    return {"threshold": threshold, "self_approve_below": below,
            "warehouse": warehouse, "supplier_id": supplier_id}


def create_rule(tenant_id: str, user_id: str, data: dict) -> dict:
    clean = _clean_rule(tenant_id, data)
    # A rule with nobody to approve would freeze every order it matches.
    if not list_approvers(tenant_id):
        raise AppError(
            "po_approval_no_approver",
            "Flag at least one person who can approve before creating a rule",
            status_code=409)
    row = query_one(
        """INSERT INTO po_approval_rules
               (tenant_id, threshold, warehouse, supplier_id, self_approve_below, created_by)
           VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id""",
        (tenant_id, clean["threshold"], clean["warehouse"], clean["supplier_id"],
         clean["self_approve_below"], user_id),
    )
    return get_rule(tenant_id, row["id"])


def get_rule(tenant_id: str, rule_id: str) -> dict:
    for r in list_rules(tenant_id):
        if r["id"] == rule_id:
            return r
    raise AppError("po_approval_rule_not_found", "Approval rule not found", status_code=404)


def update_rule(tenant_id: str, rule_id: str, data: dict) -> dict:
    current = get_rule(tenant_id, rule_id)
    merged = {**current, **{k: v for k, v in data.items() if k in (
        "threshold", "warehouse", "supplier_id", "self_approve_below")}}
    clean = _clean_rule(tenant_id, merged)
    active = current["active"] if data.get("active") is None else bool(data["active"])
    if active and not list_approvers(tenant_id):
        raise AppError(
            "po_approval_no_approver",
            "Flag at least one person who can approve before creating a rule",
            status_code=409)
    execute(
        """UPDATE po_approval_rules
              SET threshold = %s, warehouse = %s, supplier_id = %s,
                  self_approve_below = %s, active = %s, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s""",
        (clean["threshold"], clean["warehouse"], clean["supplier_id"],
         clean["self_approve_below"], active, rule_id, tenant_id),
    )
    return get_rule(tenant_id, rule_id)


def delete_rule(tenant_id: str, rule_id: str) -> None:
    get_rule(tenant_id, rule_id)   # 404 for a rule that is not this tenant's
    execute("DELETE FROM po_approval_rules WHERE id = %s AND tenant_id = %s",
            (rule_id, tenant_id))


def has_active_rules(tenant_id: str) -> bool:
    return query_one(
        "SELECT 1 AS x FROM po_approval_rules WHERE tenant_id = %s AND active LIMIT 1",
        (tenant_id,)) is not None


# ── Approvers ────────────────────────────────────────────────────────────────

def list_approvers(tenant_id: str) -> list[dict]:
    """People who may decide: flagged by an admin, still active, and able to act
    (a viewer who was flagged and later demoted cannot)."""
    rows = query(
        """SELECT id, email, full_name, role FROM users
            WHERE tenant_id = %s AND can_approve_po AND status = 'active'
              AND role IN ('admin', 'analyst')
            ORDER BY full_name NULLS LAST, email""",
        (tenant_id,),
    )
    return [dict(r) for r in rows]


def is_approver(tenant_id: str, user_id: str) -> bool:
    return any(a["id"] == user_id for a in list_approvers(tenant_id))


def set_approver(tenant_id: str, user_id: str, can_approve: bool) -> dict:
    user = query_one(
        "SELECT id, role, status, can_approve_po FROM users WHERE id = %s AND tenant_id = %s",
        (user_id, tenant_id))
    if not user:
        raise AppError("user_not_found", "User not found", status_code=404)
    if can_approve and user["role"] not in ("admin", "analyst"):
        raise AppError(
            "po_approval_approver_role", "Only an admin or an analyst can be an approver",
            status_code=409)
    if not can_approve and has_active_rules(tenant_id):
        remaining = [a for a in list_approvers(tenant_id) if a["id"] != user_id]
        if not remaining:
            raise AppError(
                "po_approval_last_approver",
                "Approval rules are active; keep at least one approver or turn the rules off first",
                status_code=409)
    execute("UPDATE users SET can_approve_po = %s WHERE id = %s AND tenant_id = %s",
            (bool(can_approve), user_id, tenant_id))
    return {"user_id": user_id, "can_approve": bool(can_approve)}


# ── The requirement: does THIS order need approval, and is it approved? ──────

def _order_facts(tenant_id: str, po: dict) -> dict:
    """Value, warehouses and suppliers of the ordered lines."""
    lines = query(
        """SELECT i.supplier, i.supplier_id, i.warehouse, i.final_qty, i.unit_cost
             FROM inventory_po_items i
            WHERE i.po_log_id = %s AND i.tenant_id = %s
              AND i.status IN ('approved', 'modified')""",
        (po["id"], tenant_id),
    )
    priced = [float(l["final_qty"] or 0) * float(l["unit_cost"])
              for l in lines if l.get("unit_cost") is not None]
    warehouses = {(l.get("warehouse") or "").strip().lower() for l in lines}
    if po.get("destination_warehouse"):
        warehouses.add(str(po["destination_warehouse"]).strip().lower())
    return {
        "amount": sum(priced) if priced else None,
        "warehouses": {w for w in warehouses if w},
        "supplier_ids": {l["supplier_id"] for l in lines if l.get("supplier_id")},
        "supplier_names": {(l.get("supplier") or "").strip().lower()
                           for l in lines if (l.get("supplier") or "").strip()},
    }


def _rule_matches(rule: dict, amount: float, facts: dict) -> bool:
    if not rule["active"] or amount + _EPS < float(rule["threshold"]):
        return False
    if rule.get("warehouse") and rule["warehouse"].strip().lower() not in facts["warehouses"]:
        return False
    if rule.get("supplier_id"):
        name = (rule.get("supplier_name") or "").strip().lower()
        if rule["supplier_id"] not in facts["supplier_ids"] and name not in facts["supplier_names"]:
            return False
    return True


def requirement(tenant_id: str, po: dict, rules: Optional[list[dict]] = None,
                facts: Optional[dict] = None) -> dict:
    """What approval means for this order right now.

    `status`: not_required | approval_needed (never asked) | pending_approval |
    approved | rejected. `required` is True for every status but not_required
    and approved. `rules`/`facts` may be preloaded by a caller walking a list.
    """
    rules = list_rules(tenant_id) if rules is None else rules
    if not any(r["active"] for r in rules):
        return {"required": False, "status": "not_required", "amount": None,
                "amount_known": None, "rule_id": None, "self_approve_below": None}
    facts = _order_facts(tenant_id, po) if facts is None else facts
    amount = facts["amount"]
    if amount is None:
        return {"required": False, "status": "not_required", "amount": None,
                "amount_known": False, "rule_id": None, "self_approve_below": None}
    matching = [r for r in rules if _rule_matches(r, amount, facts)]
    if not matching:
        return {"required": False, "status": "not_required", "amount": amount,
                "amount_known": True, "rule_id": None, "self_approve_below": None}
    # The strictest matching rule decides: highest threshold, and a missing
    # self-approval limit (never) beats a present one.
    rule = sorted(matching, key=lambda r: (
        r["self_approve_below"] is not None, -float(r["threshold"])))[0]
    state = po.get("approval_status")
    approved_amount = po.get("approved_amount")
    if state == APPROVED and approved_amount is not None \
            and amount <= float(approved_amount) + _EPS:
        status, required = APPROVED, False
    elif state == PENDING:
        status, required = PENDING, True
    elif state == REJECTED:
        status, required = REJECTED, True
    else:
        # Never asked — or approved for a smaller amount than the order is now.
        status, required = "approval_needed", True
    return {"required": required, "status": status, "amount": amount,
            "amount_known": True, "rule_id": rule["id"],
            "self_approve_below": rule["self_approve_below"]}


def assert_sendable(tenant_id: str, po_log_id: Optional[str] = None,
                    po: Optional[dict] = None) -> None:
    """Raise 409 `po_approval_required` when this order may not leave yet.

    The single enforcement point. Cheap when no rule exists (one indexed read),
    which is every tenant that has not opted in.
    """
    if not has_active_rules(tenant_id):
        return
    if po is None:
        po = query_one("SELECT * FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
                       (po_log_id, tenant_id))
        if po is None:
            return            # the caller reports "not found" in its own words
    req = requirement(tenant_id, po)
    if req["required"]:
        raise AppError(
            "po_approval_required",
            "This order needs approval before it can be sent",
            status_code=409,
            params={"approval_status": req["status"]})


# ── Requests and decisions ───────────────────────────────────────────────────

def _get_po(tenant_id: str, po_log_id: str) -> dict:
    po = query_one("SELECT * FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
                   (po_log_id, tenant_id))
    if not po:
        raise AppError("po_not_found", "Purchase order not found", status_code=404)
    return po


def _name_of(row: Optional[dict]) -> Optional[str]:
    if not row:
        return None
    return row.get("full_name") or (row.get("email") or "").split("@")[0] or None


def _latest(po_log_id: str) -> Optional[dict]:
    return query_one(
        "SELECT * FROM po_approvals WHERE po_log_id = %s ORDER BY requested_at DESC, id DESC LIMIT 1",
        (po_log_id,))


def history(tenant_id: str, po_log_id: str) -> list[dict]:
    rows = query(
        """SELECT a.id, a.status, a.amount, a.requested_by, a.requested_at, a.request_note,
                  a.decided_by, a.decided_at, a.comment,
                  a.decided_on_behalf_of, a.delegation_id,
                  rq.full_name AS requested_by_name, rq.email AS requested_by_email,
                  dc.full_name AS decided_by_name, dc.email AS decided_by_email,
                  ob.full_name AS on_behalf_full_name, ob.email AS on_behalf_email
             FROM po_approvals a
             LEFT JOIN users rq ON rq.id = a.requested_by
             LEFT JOIN users dc ON dc.id = a.decided_by
             LEFT JOIN users ob ON ob.id = a.decided_on_behalf_of
            WHERE a.po_log_id = %s AND a.tenant_id = %s
            ORDER BY a.requested_at DESC, a.id DESC""",
        (po_log_id, tenant_id),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["requested_by_name"] = _name_of({"full_name": d.pop("requested_by_name"),
                                           "email": d.pop("requested_by_email")})
        d["decided_by_name"] = _name_of({"full_name": d.pop("decided_by_name"),
                                         "email": d.pop("decided_by_email")})
        d["decided_on_behalf_of_name"] = _name_of({"full_name": d.pop("on_behalf_full_name"),
                                                   "email": d.pop("on_behalf_email")})
        d["requested_at"] = _iso(d["requested_at"])
        d["decided_at"] = _iso(d["decided_at"])
        out.append(d)
    return out


def describe(tenant_id: str, po_log_id: str, user_id: Optional[str] = None) -> dict:
    """Everything the PO's approval panel needs: the requirement, the history,
    and whether THIS user may decide the open request."""
    po = _get_po(tenant_id, po_log_id)
    req = requirement(tenant_id, po)
    hist = history(tenant_id, po_log_id)
    open_request = next((h for h in hist if h["status"] == "requested"), None)
    can_decide = False
    if open_request and user_id:
        can_decide = _may_decide(tenant_id, user_id, open_request, req, po)
    return {"po_log_id": po_log_id, **req, "history": hist,
            "open_request": open_request, "can_decide": can_decide}


def _may_decide(tenant_id: str, user_id: str, request_row: dict, req: dict,
                po: Optional[dict] = None) -> bool:
    if not is_approver(tenant_id, user_id):
        # A substitute may decide only what their delegation reaches, and never
        # their own request above the self-approval limit.
        if po is None:
            return False
        from backend.inventory import po_delegation_service as delegations
        via, _ = delegations.authority_for(tenant_id, user_id, po, request_row, req)
        if via is None:
            return False
    if request_row["requested_by"] != user_id:
        return True
    below = req.get("self_approve_below")
    return below is not None and float(request_row["amount"]) < float(below)


def request_approval(tenant_id: str, po_log_id: str, user_id: str,
                     note: Optional[str] = None) -> dict:
    """Ask for approval. Idempotent: an open request is returned, not doubled."""
    po = _get_po(tenant_id, po_log_id)
    if po.get("cancelled_at") is not None:
        raise AppError("po_cancelled",
                       "This order was cancelled; reopen it before sending it",
                       status_code=409)
    req = requirement(tenant_id, po)
    if req["status"] == APPROVED:
        return {**describe(tenant_id, po_log_id, user_id), "changed": False, "notified": 0}
    if req["status"] == PENDING:
        return {**describe(tenant_id, po_log_id, user_id), "changed": False, "notified": 0}
    if not req["required"]:
        raise AppError("po_approval_not_required",
                       "This order does not need approval", status_code=409)

    below = req.get("self_approve_below")
    amount = float(req["amount"])
    eligible = [a for a in list_approvers(tenant_id)
                if a["id"] != user_id or (below is not None and amount < float(below))]
    if not eligible:
        raise AppError(
            "po_approval_no_approver",
            "Nobody else can approve this order; an admin must flag an approver",
            status_code=409)

    clean_note = (note or "").strip()[:MAX_COMMENT_LENGTH] or None
    try:
        with transaction() as conn:
            query_one(
                """INSERT INTO po_approvals
                       (tenant_id, po_log_id, status, amount, rule_id, requested_by, request_note)
                   VALUES (%s, %s, 'requested', %s, %s, %s, %s) RETURNING id""",
                (tenant_id, po_log_id, amount, req["rule_id"], user_id, clean_note),
                conn=conn)
            execute(
                """UPDATE inventory_po_log
                      SET approval_status = %s, approved_amount = NULL
                    WHERE id = %s AND tenant_id = %s""",
                (PENDING, po_log_id, tenant_id), conn=conn)
    except Exception as exc:
        if getattr(exc, "pgcode", "") != _UNIQUE_VIOLATION:
            raise
        # Lost a race with another request for the same order: the open one
        # stands, nothing was written by this call.
        return {**describe(tenant_id, po_log_id, user_id), "changed": False, "notified": 0}

    log.info("[po-approval] REQUEST tenant=%s po=%s by=%s amount=%s",
             tenant_id, po_log_id, user_id, amount)
    notified = _notify_approvers(tenant_id, po, amount, user_id, eligible)
    return {**describe(tenant_id, po_log_id, user_id), "changed": True, "notified": notified}


def decide(tenant_id: str, po_log_id: str, user_id: str, decision: str,
           comment: Optional[str] = None) -> dict:
    """Approve or reject the open request. Idempotent: repeating the decision
    that was already taken changes nothing; the opposite one is refused."""
    if decision not in ("approved", "rejected"):
        raise ValueError(decision)
    po = _get_po(tenant_id, po_log_id)
    clean_comment = (comment or "").strip()[:MAX_COMMENT_LENGTH] or None
    if decision == "rejected" and (clean_comment is None
                                   or len(clean_comment) < MIN_REJECT_REASON_LENGTH):
        raise AppError("po_approval_reason_required",
                       "Say why you are rejecting this order", status_code=422)

    latest = _latest(po_log_id)
    if latest is None:
        raise AppError("po_approval_not_requested",
                       "Nobody has asked for approval on this order", status_code=409)
    if latest["status"] != "requested":
        if latest["status"] == decision:
            return {**describe(tenant_id, po_log_id, user_id), "changed": False}
        raise AppError("po_approval_already_decided",
                       "This request was already decided", status_code=409,
                       params={"decision": latest["status"]})

    req = requirement(tenant_id, po)
    via = None   # the delegation this decision stands on, when the person is a substitute
    if not is_approver(tenant_id, user_id):
        from backend.inventory import po_delegation_service as delegations
        via, had_delegation = delegations.authority_for(
            tenant_id, user_id, po, latest, req, decision)
        if via is None and had_delegation:
            # A delegation is in force but does not reach THIS order (the
            # delegator's warehouses, their own order, no longer an approver).
            raise AppError("po_approval_delegation_not_permitted",
                           "Your delegation does not allow you to decide this order",
                           status_code=403)
        if via is None:
            raise AppError("po_approval_not_approver",
                           "You are not allowed to approve orders", status_code=403)
    own = latest["requested_by"] == user_id
    below = req.get("self_approve_below")
    if own and decision == "approved" and not (
            below is not None and float(latest["amount"]) < float(below)):
        raise AppError(
            "po_approval_self_approval",
            "You cannot approve your own order at this value",
            status_code=403)

    amount_now = req["amount"] if req.get("amount") is not None else latest["amount"]
    with transaction() as conn:
        won = query_one(
            """UPDATE po_approvals
                  SET status = %s, decided_by = %s, decided_at = NOW(), comment = %s,
                      decided_on_behalf_of = %s, delegation_id = %s
                WHERE id = %s AND tenant_id = %s AND status = 'requested'
            RETURNING id""",
            (decision, user_id, clean_comment,
             via["delegator_id"] if via else None, via["id"] if via else None,
             latest["id"], tenant_id), conn=conn)
        if won is not None:
            execute(
                """UPDATE inventory_po_log
                      SET approval_status = %s, approved_amount = %s
                    WHERE id = %s AND tenant_id = %s""",
                (APPROVED if decision == "approved" else REJECTED,
                 float(amount_now) if decision == "approved" else None,
                 po_log_id, tenant_id), conn=conn)
    if won is None:
        # Another decision landed between the read and the write.
        latest = _latest(po_log_id) or {}
        if latest.get("status") == decision:
            return {**describe(tenant_id, po_log_id, user_id), "changed": False}
        raise AppError("po_approval_already_decided",
                       "This request was already decided", status_code=409,
                       params={"decision": latest.get("status")})

    log.info("[po-approval] %s tenant=%s po=%s by=%s", decision.upper(), tenant_id,
             po_log_id, user_id)
    # Only the decision that WON the race reaches here, so one decision is one
    # event, however many times the call is repeated.
    from backend.webhooks.service import emit_po_event
    emit_po_event(tenant_id, f"purchase_order.{decision}", po_log_id, decided_by=user_id)
    _notify_requester(tenant_id, po, latest["requested_by"], decision, clean_comment,
                      float(latest["amount"]), user_id)
    return {**describe(tenant_id, po_log_id, user_id), "changed": True,
            "po_number": po.get("po_number"), "amount": float(latest["amount"]),
            "comment": clean_comment,
            "on_behalf_of_name": via["delegator_name"] if via else None}


def list_pending(tenant_id: str, user_id: str) -> dict:
    """The approver's inbox: open requests on orders that can still be acted on.
    A person who cannot approve gets an empty list, not an error: the same call
    feeds the attention rows for everyone."""
    from backend.inventory import po_delegation_service as delegations
    own = is_approver(tenant_id, user_id)
    if not own and not delegations.active_for_delegate(tenant_id, user_id):
        return {"is_approver": False, "items": []}
    rows = query(
        """SELECT a.id AS approval_id, a.po_log_id, a.amount, a.requested_by,
                  a.requested_at, a.request_note, l.po_number, l.sku_count,
                  l.destination_warehouse, rq.full_name AS requested_by_name,
                  rq.email AS requested_by_email,
                  (SELECT string_agg(DISTINCT i.supplier, ', ')
                     FROM inventory_po_items i
                    WHERE i.po_log_id = l.id AND i.status IN ('approved', 'modified')
                      AND i.supplier IS NOT NULL) AS suppliers
             FROM po_approvals a
             JOIN inventory_po_log l ON l.id = a.po_log_id
             LEFT JOIN users rq ON rq.id = a.requested_by
            WHERE a.tenant_id = %s AND a.status = 'requested'
              AND l.cancelled_at IS NULL
            ORDER BY a.requested_at""",
        (tenant_id,),
    )
    from backend.inventory.roi_service import format_po_number
    items = []
    for r in rows:
        req_row = {"requested_by": r["requested_by"], "amount": r["amount"]}
        po = _get_po(tenant_id, r["po_log_id"])
        req = requirement(tenant_id, po)
        items.append({
            "approval_id": r["approval_id"], "po_log_id": r["po_log_id"],
            "reference": format_po_number(r["po_number"], r["po_log_id"]),
            "amount": float(r["amount"]), "sku_count": r["sku_count"],
            "suppliers": r["suppliers"], "warehouse": r["destination_warehouse"],
            "requested_by_name": _name_of({"full_name": r["requested_by_name"],
                                           "email": r["requested_by_email"]}),
            "requested_at": _iso(r["requested_at"]), "note": r["request_note"],
            "can_decide": _may_decide(tenant_id, user_id, req_row, req, po),
        })
    if not own:
        # A substitute sees only the orders their delegation lets them decide.
        items = [i for i in items if i["can_decide"]]
        return {"is_approver": False, "is_delegate": True, "items": items}
    return {"is_approver": True, "items": items}


def annotate_orders(tenant_id: str, rows: list[dict]) -> list[dict]:
    """Add `approval` ({required, status}) to PO history rows. No rule, no work:
    the rows come back untouched and carry no `approval` key at all."""
    rules = list_rules(tenant_id)
    if not any(r["active"] for r in rules) or not rows:
        return rows
    ids = [r["id"] for r in rows]
    lines = query(
        """SELECT po_log_id, supplier, supplier_id, warehouse, final_qty, unit_cost
             FROM inventory_po_items
            WHERE tenant_id = %s AND po_log_id = ANY(%s)
              AND status IN ('approved', 'modified')""",
        (tenant_id, ids))
    by_po: dict[str, list[dict]] = {}
    for l in lines:
        by_po.setdefault(l["po_log_id"], []).append(l)
    out = []
    for r in rows:
        po_lines = by_po.get(r["id"], [])
        priced = [float(l["final_qty"] or 0) * float(l["unit_cost"])
                  for l in po_lines if l.get("unit_cost") is not None]
        whs = {(l.get("warehouse") or "").strip().lower() for l in po_lines}
        if r.get("destination_warehouse"):
            whs.add(str(r["destination_warehouse"]).strip().lower())
        facts = {
            "amount": sum(priced) if priced else None,
            "warehouses": {w for w in whs if w},
            "supplier_ids": {l["supplier_id"] for l in po_lines if l.get("supplier_id")},
            "supplier_names": {(l.get("supplier") or "").strip().lower()
                               for l in po_lines if (l.get("supplier") or "").strip()},
        }
        req = requirement(tenant_id, r, rules=rules, facts=facts)
        out.append({**r, "approval": {"required": req["required"], "status": req["status"]}})
    return out


# ── Notifications ────────────────────────────────────────────────────────────

def _order_link(po_log_id: str) -> str:
    from backend.config import settings
    return f"{settings.frontend_url}/pedidos?view=approvals&po={po_log_id}"


def _amount_text(tenant_id: str, amount: float) -> str:
    from backend.api.v1.currency import currency_of
    from backend.formatting import money
    return money(amount, currency=currency_of(tenant_id))


def _notify_approvers(tenant_id: str, po: dict, amount: float, requester_id: str,
                      eligible: list[dict]) -> int:
    """Email every approver who can decide this order, through the existing
    transport. The bell reaches them through the attention rows (the pending
    list is derived live), so this is the push for somebody not looking at the
    app. Returns how many mails left; a failure is logged, never raised: the
    request itself is already written."""
    from backend.inventory.roi_service import format_po_number
    from backend.notifications import email as email_mod
    requester = query_one("SELECT full_name, email FROM users WHERE id = %s", (requester_id,))
    ref = format_po_number(po.get("po_number"), po["id"])
    sent = 0
    for a in eligible:
        if a["id"] == requester_id:
            continue
        try:
            if email_mod.send_po_approval_request_email(
                    to=a["email"], approver_name=_name_of(a) or "", requester_name=_name_of(requester) or "",
                    po_ref=ref, amount_text=_amount_text(tenant_id, amount),
                    url=_order_link(po["id"]), tenant_id=tenant_id):
                sent += 1
        except Exception:   # noqa: BLE001 — see the docstring
            log.exception("[po-approval] approver email failed tenant=%s po=%s", tenant_id, po["id"])
    return sent


def _notify_requester(tenant_id: str, po: dict, requester_id: str, decision: str,
                      comment: Optional[str], amount: float, decider_id: str) -> None:
    if requester_id == decider_id:
        return
    from backend.inventory.roi_service import format_po_number
    from backend.notifications import email as email_mod
    requester = query_one("SELECT full_name, email FROM users WHERE id = %s AND tenant_id = %s",
                          (requester_id, tenant_id))
    decider = query_one("SELECT full_name, email FROM users WHERE id = %s", (decider_id,))
    if not requester or not requester.get("email"):
        return
    try:
        email_mod.send_po_approval_decision_email(
            to=requester["email"], requester_name=_name_of(requester) or "",
            decider_name=_name_of(decider) or "", approved=(decision == "approved"),
            po_ref=format_po_number(po.get("po_number"), po["id"]),
            amount_text=_amount_text(tenant_id, amount), comment=comment,
            url=_order_link(po["id"]), tenant_id=tenant_id)
    except Exception:   # noqa: BLE001
        log.exception("[po-approval] requester email failed tenant=%s po=%s", tenant_id, po["id"])
