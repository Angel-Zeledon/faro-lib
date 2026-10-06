"""The pure rules of approval chains: which chain and band govern an order, and
who may decide a level. No I/O, no clock.

Rust owns the management routes (`backend-rs/src/routes/cost_centers.rs`) and
carries a second implementation of `resolve` (`backend-rs/src/chain.rs`);
Python keeps this one because the approval DECISION path is still served by
Python and must apply exactly the same rules. The two are held together by a
differential test (`tests/contract/chain_differential.py`) over seeded cases
and by a fixtures file both sides read (`tests/contract/chain_fixtures.json`,
written by `tests/contract/gen_chain_fixtures.py` from THIS module).

The model, deliberately small:

* A COST CENTER sits in a tree (`parent_id`). An order is attributed to one.
* A CHAIN belongs to one center (it then also covers the center's descendants
  that have no chain of their own) or to no center (the DEFAULT chain, which
  covers every other order, including orders with no center). At most one
  active chain per center.
* A chain has BANDS: from `min_amount` up, the order needs the band's LEVELS,
  in order. Each level is a role ("admin" or "analyst": that role or above)
  or a list of named people.
* FAIL CLOSED: once a tenant has an active chain, an order whose center is
  unknown or inactive, that no chain covers, whose chain is malformed or whose
  value is unknown is `unresolved`: it is NOT auto-approved and cannot be sent,
  and the reason says what to fix. Only an order below the chain's lowest band
  is `not_required`.
* ESCALATION: an order that went past a purchasing budget when it was created
  needs the chain's HIGHEST band whatever its value.
"""

from __future__ import annotations

import math
from typing import Any, Optional

EPS = 0.005                    # same tolerance as po_approval_service._EPS
MAX_LEVELS = 5
MAX_USERS_PER_LEVEL = 20
MAX_BANDS = 10
MAX_DEPTH = 32
ROLE_RANK = {"analyst": 1, "admin": 2}

NOT_REQUIRED = "not_required"
REQUIRED = "required"
UNRESOLVED = "unresolved"


def normalize_level(raw: Any) -> Optional[dict]:
    """A level in canonical form, or None when it is malformed."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind == "role":
        role = raw.get("role")
        if isinstance(role, str) and role in ROLE_RANK:
            return {"kind": "role", "role": role}
        return None
    if kind == "users":
        ids = raw.get("user_ids")
        if not isinstance(ids, list) or not ids or len(ids) > MAX_USERS_PER_LEVEL:
            return None
        if not all(isinstance(i, str) and i for i in ids):
            return None
        return {"kind": "users", "user_ids": sorted(set(ids))}
    return None


def normalize_levels(raw: Any) -> Optional[list[dict]]:
    if not isinstance(raw, list) or not raw or len(raw) > MAX_LEVELS:
        return None
    out = []
    for item in raw:
        lvl = normalize_level(item)
        if lvl is None:
            return None
        out.append(lvl)
    return out


def level_text(level: dict) -> str:
    return f"role:{level['role']}" if level["kind"] == "role" else "users:" + ",".join(level["user_ids"])


def fingerprint(chain_id: str, min_amount: float, levels: list[dict]) -> str:
    """Identity of what an approval was granted under. An approval made under
    one fingerprint does not stand when the chain, the band or the levels
    change."""
    return f"{chain_id}|{min_amount:.2f}|" + ";".join(level_text(l) for l in levels)


def level_eligible(level: dict, user: dict) -> bool:
    """May `user` ({id, role, status}) decide `level`? Only an active admin or
    analyst can ever decide; a role level accepts that role or above."""
    if user.get("status") != "active" or user.get("role") not in ROLE_RANK:
        return False
    if level["kind"] == "role":
        return ROLE_RANK[user["role"]] >= ROLE_RANK[level["role"]]
    return user["id"] in level["user_ids"]


def _unresolved(reason: str, **extra: Any) -> dict:
    return {"state": UNRESOLVED, "reason": reason, "chain_id": None, "min_amount": None,
            "levels": None, "fingerprint": None, "escalated": False,
            "cost_center_id": extra.get("cost_center_id")}


def _chain_valid(chain: dict) -> Optional[list[dict]]:
    """The chain's bands as [{min_amount, levels}] sorted ascending, or None
    when ANY band is malformed (one broken band makes the whole chain untrusted:
    a gap in it could otherwise let an order through)."""
    bands_raw = chain.get("bands")
    if not isinstance(bands_raw, list) or not bands_raw or len(bands_raw) > MAX_BANDS:
        return None
    bands, seen = [], set()
    for b in bands_raw:
        if not isinstance(b, dict):
            return None
        m = b.get("min_amount")
        if isinstance(m, bool) or not isinstance(m, (int, float)):
            return None
        m = float(m)
        if not math.isfinite(m) or m < 0 or m in seen:
            return None
        seen.add(m)
        levels = normalize_levels(b.get("levels"))
        if levels is None:
            return None
        bands.append({"min_amount": m, "levels": levels})
    bands.sort(key=lambda b: b["min_amount"])
    return bands


def find_chain(chains: list[dict], centers: list[dict], center_id: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    """(chain, unresolved_reason). `centers`: [{id, parent_id, active}];
    `chains`: [{id, cost_center_id, active, bands}]."""
    active = [c for c in chains if c.get("active")]
    default = next((c for c in active if c.get("cost_center_id") is None), None)
    if center_id is None:
        return (default, None) if default else (None, "no_cost_center")
    by_id = {c["id"]: c for c in centers}
    node = by_id.get(center_id)
    if node is None or not node.get("active"):
        return None, "cost_center_invalid"
    visited: set[str] = set()
    depth = 0
    while node is not None:
        if node["id"] in visited or depth > MAX_DEPTH:
            return None, "cost_center_invalid"      # a cycle or an absurd depth
        visited.add(node["id"])
        for c in active:
            if c.get("cost_center_id") == node["id"]:
                return c, None
        parent = node.get("parent_id")
        node = by_id.get(parent) if parent else None
        depth += 1
        if parent and node is None:
            return None, "cost_center_invalid"      # a parent that does not exist
    return (default, None) if default else (None, "no_chain")


def resolve(chains: list[dict], centers: list[dict], center_id: Optional[str],
            amount: Optional[float], escalate: bool = False) -> dict:
    """What approval chain means for one order.

    Returns {state, reason, chain_id, min_amount, levels, fingerprint,
    escalated, cost_center_id}. `state` is not_required (no active chain at
    all, or the value is below the lowest band), required, or unresolved."""
    if not any(c.get("active") for c in chains):
        return {"state": NOT_REQUIRED, "reason": None, "chain_id": None, "min_amount": None,
                "levels": None, "fingerprint": None, "escalated": False,
                "cost_center_id": center_id}
    chain, why = find_chain(chains, centers, center_id)
    if chain is None:
        return _unresolved(why or "no_chain", cost_center_id=center_id)
    bands = _chain_valid(chain)
    if bands is None:
        return {**_unresolved("chain_invalid", cost_center_id=center_id), "chain_id": chain["id"]}
    if amount is None or isinstance(amount, bool) or not math.isfinite(float(amount)):
        return {**_unresolved("amount_unknown", cost_center_id=center_id), "chain_id": chain["id"]}
    amount = float(amount)
    if escalate:
        band = bands[-1]
    else:
        fitting = [b for b in bands if amount + EPS >= b["min_amount"]]
        if not fitting:
            return {"state": NOT_REQUIRED, "reason": None, "chain_id": chain["id"],
                    "min_amount": None, "levels": None, "fingerprint": None,
                    "escalated": False, "cost_center_id": center_id}
        band = fitting[-1]
    return {"state": REQUIRED, "reason": None, "chain_id": chain["id"],
            "min_amount": band["min_amount"], "levels": band["levels"],
            "fingerprint": fingerprint(chain["id"], band["min_amount"], band["levels"]),
            "escalated": bool(escalate), "cost_center_id": center_id}


def descendants(centers: list[dict], center_id: str) -> list[str]:
    """The center and every center below it (cycle-safe, depth-capped)."""
    kids: dict[str, list[str]] = {}
    for c in centers:
        if c.get("parent_id"):
            kids.setdefault(c["parent_id"], []).append(c["id"])
    out, stack, seen = [], [(center_id, 0)], set()
    while stack:
        cid, depth = stack.pop()
        if cid in seen or depth > MAX_DEPTH:
            continue
        seen.add(cid)
        out.append(cid)
        for k in sorted(kids.get(cid, [])):
            stack.append((k, depth + 1))
    return sorted(out)


def staffing(levels: list[dict], users: list[dict], requester_id: str) -> Optional[int]:
    """None when every level can be filled by a DIFFERENT eligible person other
    than the requester (a bipartite matching), else the 1-based number of the
    first level that cannot. `users`: [{id, role, status}]."""
    pool = [u for u in users if u["id"] != requester_id]
    cand = [[i for i, u in enumerate(pool) if level_eligible(l, u)] for l in levels]
    owner: dict[int, int] = {}

    def assign(li: int, seen: set[int]) -> bool:
        for ui in cand[li]:
            if ui in seen:
                continue
            seen.add(ui)
            if ui not in owner or assign(owner[ui], seen):
                owner[ui] = li
                return True
        return False

    for li in range(len(levels)):
        if not assign(li, set()):
            return li + 1
    return None
