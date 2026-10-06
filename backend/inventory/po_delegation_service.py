"""Purchase-order approval delegation: a substitute approver for a date range.

An approver (a user an admin flagged, see `po_approval_service`) can name a
substitute while they are away. Creating, listing and revoking a delegation is
served by Rust only (`backend-rs/src/routes/po_delegations.rs`; there is no
Python route and no failover). This module is the other half: the Python
decision path (`po_approval_service.decide`, still Python) honours the rows
Rust writes, with the same rules:

* never to yourself, never to a viewer, never to somebody inactive or outside
  the tenant; only a current approver may delegate (nothing to hand over
  otherwise);
* the dates are inclusive UTC days. Whether a delegation is in force is decided
  from them AT DECISION TIME (`ACTIVE_SQL`); there is no job that expires
  anything, so an expired or revoked delegation stops working immediately;
* a delegate can never exceed the delegator. At decision time the delegator
  must still be an approver, the order's destination warehouse must be inside
  the delegator's warehouse scope (fail closed: an unreadable scope is an empty
  one), and when the delegator asked for the order themselves the same
  self-approval limit applies to the delegate as would to the delegator. A
  delegation is never transitive: it lends the delegator's authority, not the
  authority the delegator was lent.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from backend.db.connection import query, query_one

# "Today" is the UTC date, the same clock the Rust service reads, so the two
# never disagree about whether a delegation is in force.
_TODAY = "(NOW() AT TIME ZONE 'UTC')::date"
ACTIVE_SQL = f"d.revoked_at IS NULL AND d.starts_on <= {_TODAY} AND d.ends_on >= {_TODAY}"
_STATUS_SQL = f"""CASE WHEN d.revoked_at IS NOT NULL THEN 'revoked'
                       WHEN d.ends_on < {_TODAY} THEN 'expired'
                       WHEN d.starts_on > {_TODAY} THEN 'scheduled'
                       ELSE 'active' END"""


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _name_of(full_name: Optional[str], email: Optional[str]) -> Optional[str]:
    return full_name or (email or "").split("@")[0] or None


def _shape(row: dict) -> dict:
    return {
        "id": row["id"],
        "delegator_id": row["delegator_id"],
        "delegator_name": _name_of(row["delegator_full_name"], row["delegator_email"]),
        "delegate_id": row["delegate_id"],
        "delegate_name": _name_of(row["delegate_full_name"], row["delegate_email"]),
        "starts_on": _iso(row["starts_on"]),
        "ends_on": _iso(row["ends_on"]),
        "note": row["note"],
        "status": row["status"],
        "created_by": row["created_by"],
        "created_at": _iso(row["created_at"]),
        "revoked_at": _iso(row["revoked_at"]),
        "revoked_by": row["revoked_by"],
    }


_SELECT = f"""SELECT d.id, d.delegator_id, d.delegate_id, d.starts_on, d.ends_on, d.note,
                     d.created_by, d.created_at, d.revoked_at, d.revoked_by,
                     {_STATUS_SQL} AS status,
                     dr.full_name AS delegator_full_name, dr.email AS delegator_email,
                     de.full_name AS delegate_full_name, de.email AS delegate_email
                FROM po_approval_delegations d
                LEFT JOIN users dr ON dr.id = d.delegator_id AND dr.tenant_id = d.tenant_id
                LEFT JOIN users de ON de.id = d.delegate_id AND de.tenant_id = d.tenant_id"""


# ── Authority at decision time ───────────────────────────────────────────────

def _scope_allows(tenant_id: str, user_id: str, destination: Optional[str]) -> bool:
    """Whether the delegator's warehouse scope covers the order's destination.
    Mirrors `warehouse_scope.in_scope` for a user known only by id, and fails
    closed: no row, an unreadable value or a stale id all mean "not covered"."""
    row = query_one("SELECT warehouse_scope FROM users WHERE id = %s AND tenant_id = %s",
                    (user_id, tenant_id))
    if row is None:
        return False
    raw = row["warehouse_scope"]
    if raw is None:
        return True
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return False
    ids = [str(i) for i in raw] if isinstance(raw, list) else []
    if not ids:
        return False
    names = {str(r["name"]).strip().casefold() for r in query(
        "SELECT name FROM warehouses WHERE tenant_id = %s AND id = ANY(%s)",
        (tenant_id, ids))}
    target = (destination or "").strip()
    if not target:
        from backend.auth import warehouse_scope as wscope
        target = wscope._tenant_default(tenant_id)
    return target.strip().casefold() in names


def active_for_delegate(tenant_id: str, delegate_id: str) -> list[dict]:
    """Delegations in force today for this person, oldest first."""
    rows = query(
        f"{_SELECT} WHERE d.tenant_id = %s AND d.delegate_id = %s AND {ACTIVE_SQL} "
        "ORDER BY d.created_at, d.id", (tenant_id, delegate_id))
    return [_shape(dict(r)) for r in rows]


def authority_for(tenant_id: str, delegate_id: str, po: dict, request_row: dict,
                  req: dict, decision: str = "approved") -> tuple[Optional[dict], bool]:
    """(delegation that lets `delegate_id` decide this request, had_candidates).

    Walks the delegations in force; the first whose delegator is still an
    approver, whose warehouse scope covers the order, and who could themselves
    decide this request, wins. `had_candidates` tells the caller a refusal is
    "your delegation does not reach this order" rather than "you may not".
    """
    from backend.inventory import po_approval_service as approvals
    candidates = active_for_delegate(tenant_id, delegate_id)
    for d in candidates:
        delegator = d["delegator_id"]
        if not approvals.is_approver(tenant_id, delegator):
            continue
        if not _scope_allows(tenant_id, delegator, po.get("destination_warehouse")):
            continue
        if decision == "approved" and request_row["requested_by"] == delegator:
            below = req.get("self_approve_below")
            if not (below is not None and float(request_row["amount"]) < float(below)):
                continue
        return d, True
    return None, bool(candidates)

