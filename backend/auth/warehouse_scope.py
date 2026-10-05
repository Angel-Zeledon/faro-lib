"""Warehouse scopes: which warehouses a user (or an API key) may see and change.

Roles are tenant-wide (admin / analyst / viewer). A scope narrows WHERE a role
applies, never what it can do: a scoped analyst is an analyst inside their
warehouses and nobody outside them.

    users.warehouse_scope    JSONB, NULL = every warehouse (all existing users),
                             a JSON array of warehouse ids = only those,
                             [] = none at all
    api_keys.warehouse_scope the same, for an integration

**Fail closed.** The scope is stored as warehouse IDS and resolved to names on
every check, against the tenant's own `warehouses` rows:

  * deleting a warehouse can only shrink a scope - a stale id resolves to
    nothing, and `[]` means "none", never "all";
  * an id that is not a warehouse of the caller's tenant resolves to nothing;
  * an unreadable value is treated as an empty scope, not as unrestricted.

**Where it is enforced.** At the endpoints and in the code they hand off to, not
in the UI: every router function that touches warehouse-dimensioned data calls
one of the helpers below, and `test_warehouse_scope_coverage.py` fails when a new
one does not. MCP tools and the assistant call those same functions with the
caller's `CurrentUser`, so they inherit the scope.

**What a scoped user sees.** Only their warehouses' figures. Where a tenant-wide
total cannot be recomputed over a subset (a network-wide optimisation, the
company's ROI, an e-mailed alert, a PDF of every row) the endpoint refuses with
`warehouse_scope_company_totals` instead of showing a number that mixes in
warehouses the person may not see - see `company_wide`.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from fastapi import Depends

from backend.auth.guards import CurrentUser, get_current_user
from backend.db.connection import query, query_one
from backend.errors import AppError

DEFAULT_WAREHOUSE = "principal"  # mirrors inventory.warehouse_service.DEFAULT_WAREHOUSE


# ── Reading the scope ────────────────────────────────────────────────────────

def _raw_scope(user: CurrentUser) -> Any:
    """The stored value: None (unrestricted) or whatever else is on the row."""
    if user.is_machine:
        row = query_one(
            "SELECT warehouse_scope FROM api_keys WHERE id = %s AND tenant_id = %s",
            (user.api_key_id, user.tenant_id),
        )
    else:
        row = query_one(
            "SELECT warehouse_scope FROM users WHERE id = %s AND tenant_id = %s",
            (user.user_id, user.tenant_id),
        )
    if row is None:
        # A caller whose row cannot be read has no business being unrestricted.
        return []
    return row["warehouse_scope"]


def scope_ids(user: CurrentUser) -> list[str] | None:
    """The stored warehouse ids of the caller's scope (None = unrestricted).
    Unlike `scope_names`, ids that no longer resolve are still listed - this is
    for copying a scope onto something the caller creates."""
    raw = _raw_scope(user)
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(i) for i in raw] if isinstance(raw, list) else []


def scope_names(user: CurrentUser) -> frozenset[str] | None:
    """Warehouse NAMES the caller is limited to; None when unrestricted.

    Resolved once per request (cached on the `CurrentUser`).
    """
    cached = getattr(user, "scope_cache", _MISSING)
    if cached is not _MISSING:
        return cached

    raw = _raw_scope(user)
    if raw is None:
        names: frozenset[str] | None = None
    else:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = []
        ids = [str(i) for i in raw] if isinstance(raw, list) else []
        if not ids:
            names = frozenset()
        else:
            rows = query(
                "SELECT name FROM warehouses WHERE tenant_id = %s AND id = ANY(%s)",
                (user.tenant_id, ids),
            )
            names = frozenset(r["name"] for r in rows)
    try:
        user.scope_cache = names
    except AttributeError:  # pragma: no cover - slots are declared on CurrentUser
        pass
    return names


_MISSING = object()


def is_scoped(user: CurrentUser) -> bool:
    return scope_names(user) is not None


def scope_warehouse_ids(user: CurrentUser) -> frozenset[str] | None:
    """Warehouse IDS the caller is limited to, resolved against the tenant's
    own warehouses (stale or foreign ids drop out, as in `scope_names`); None
    when unrestricted. For rows that name a warehouse by id rather than by
    name, such as customer commitments."""
    ids = scope_ids(user)
    if ids is None:
        return None
    if not ids:
        return frozenset()
    rows = query(
        "SELECT id FROM warehouses WHERE tenant_id = %s AND id = ANY(%s)",
        (user.tenant_id, ids),
    )
    return frozenset(r["id"] for r in rows)


def _fold(name: str | None) -> str:
    return (name or "").strip().casefold()


def effective_name(name: str | None) -> str:
    """The warehouse a write with this (possibly missing) name lands on."""
    return (name or "").strip() or DEFAULT_WAREHOUSE


def in_scope(user: CurrentUser, warehouse: str | None) -> bool:
    """Whether `warehouse` is one of the caller's. A missing name means the
    tenant's default warehouse, because that is where such a row lives."""
    names = scope_names(user)
    if names is None:
        return True
    target = _fold(effective_name(warehouse))
    return any(_fold(n) == target for n in names)


def denied(warehouse: str | None) -> AppError:
    return AppError(
        "warehouse_out_of_scope",
        "This user is not allowed to work with that warehouse.",
        status_code=403,
        params={"warehouse": effective_name(warehouse)},
    )


# ── Enforcing it ─────────────────────────────────────────────────────────────

def require_in_scope(user: CurrentUser, warehouse: str | None) -> None:
    """Refuse (403 `warehouse_out_of_scope`) a read or write on a warehouse the
    caller may not touch. A no-op for an unrestricted caller."""
    if not in_scope(user, warehouse):
        raise denied(warehouse)


def require_all_in_scope(user: CurrentUser, warehouses: Iterable[str | None]) -> None:
    for w in warehouses:
        require_in_scope(user, w)


def require_any_in_scope(user: CurrentUser, warehouses: Iterable[str | None]) -> None:
    """For things that touch two warehouses and are visible from either end."""
    items = list(warehouses)
    if scope_names(user) is None:
        return
    if not any(in_scope(user, w) for w in items):
        raise denied(items[0] if items else None)


def filter_rows(user: CurrentUser, rows: list[dict], key: str = "warehouse") -> list[dict]:
    """The rows whose warehouse the caller may see (all of them if unrestricted)."""
    names = scope_names(user)
    if names is None:
        return rows
    allowed = {_fold(n) for n in names}
    return [r for r in rows if _fold(effective_name(r.get(key))) in allowed]


def company_wide(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Dependency for endpoints whose figures are company totals that cannot be
    recomputed over a subset of warehouses. A scoped caller is refused; an
    unrestricted one passes untouched.

    Using it is a statement: "this endpoint is warehouse-dimensioned and I have
    decided a scoped user does not get it". `test_warehouse_scope_coverage.py`
    accepts it as proof the function was considered.
    """
    if is_scoped(user):
        raise AppError(
            "warehouse_scope_company_totals",
            "This shows company-wide totals, which are not available to a user "
            "limited to some warehouses.",
            status_code=403,
        )
    return user


def require_company_wide(user: CurrentUser) -> None:
    """Same refusal, called from inside a function body."""
    if is_scoped(user):
        raise AppError(
            "warehouse_scope_company_totals",
            "This shows company-wide totals, which are not available to a user "
            "limited to some warehouses.",
            status_code=403,
        )


# ── Per-warehouse status rows ────────────────────────────────────────────────

def _named_in_scope(user: CurrentUser, warehouse: str | None) -> bool:
    """Like `in_scope`, but a missing name is NOT the default warehouse: it is
    nothing, and nothing the caller was not told about stays hidden."""
    return bool((warehouse or "").strip()) and in_scope(user, warehouse)


def scoped_status_rows(user: CurrentUser, rows: list[dict]) -> list[dict]:
    """Per-(sku, warehouse) status rows as a scoped caller may read them.

    The rows of their warehouses only, and with every pointer at a warehouse
    outside the scope removed: a transfer suggested FROM a warehouse they cannot
    see would name it and say how much it holds. Such a row falls back to the
    purchase the engine recommended anyway (`recommended_qty` is never changed
    by the transfer pass; only the label is).
    """
    rows = filter_rows(user, rows)
    if scope_names(user) is None:
        return rows
    out = []
    for r in rows:
        r = dict(r)
        sug = r.get("transfer_suggestion")
        if sug and not _named_in_scope(user, sug.get("from_warehouse")):
            r["transfer_suggestion"] = None
            if r.get("recommended_action") == "transfer":
                r["recommended_action"] = "order"
        partial = r.get("partial_transfer")
        if partial and not _named_in_scope(user, partial.get("from_warehouse")):
            r["partial_transfer"] = None
        rej = r.get("transfer_rejected_reason")
        if rej and not _named_in_scope(user, (rej.get("params") or {}).get("from_warehouse")):
            r["transfer_rejected_reason"] = None
        out.append(r)
    return out


# ── Purchase orders ──────────────────────────────────────────────────────────
# An order belongs to its DESTINATION warehouse; a NULL destination is the
# tenant's default warehouse, as everywhere else in the product.

def _tenant_default(tenant_id: str) -> str:
    from backend.inventory.warehouse_service import (
        DEFAULT_WAREHOUSE as FALLBACK, get_default_warehouse_name,
    )
    return get_default_warehouse_name(tenant_id) or FALLBACK


def _po_target(user: CurrentUser, destination: str | None) -> str:
    return (destination or "").strip() or _tenant_default(user.tenant_id)


def require_destination_in_scope(user: CurrentUser, destination: str | None) -> None:
    """For creating an order: where it will arrive must be one of the caller's."""
    if scope_names(user) is None:
        return
    target = _po_target(user, destination)
    if not in_scope(user, target):
        raise denied(target)


def scoped_destination(user: CurrentUser, destination: str | None) -> str | None:
    """The destination a new order is created with.

    Unchanged for an unrestricted caller. For a scoped one: the warehouse they
    named, if it is theirs (403 otherwise); with none named, the tenant default
    when that is theirs, else their only warehouse, else a 422 asking them to
    say - never a default outside their scope, which would put the order, and
    later its stock, somewhere they cannot see.
    """
    names = scope_names(user)
    if names is None:
        return destination
    named = (destination or "").strip()
    if named:
        from backend.inventory.warehouse_service import resolve_canonical_name
        require_in_scope(user, resolve_canonical_name(user.tenant_id, named))
        return destination
    default = _tenant_default(user.tenant_id)
    if in_scope(user, default):
        return destination
    if len(names) == 1:
        return next(iter(names))
    if not names:
        raise denied(default)
    raise AppError(
        "warehouse_destination_required",
        "Say which of your warehouses this order is for.",
        status_code=422,
    )


def po_destination(tenant_id: str, po_log_id: str) -> tuple[bool, str | None]:
    """(exists, destination warehouse) of a purchase order."""
    row = query_one(
        "SELECT destination_warehouse FROM inventory_po_log WHERE id = %s AND tenant_id = %s",
        (po_log_id, tenant_id),
    )
    if row is None:
        return False, None
    return True, row["destination_warehouse"]


def require_po_in_scope(user: CurrentUser, po_log_id: str) -> None:
    """403 when the order is destined to a warehouse outside the scope. An order
    that does not exist is left for the endpoint's own 404."""
    if scope_names(user) is None:
        return
    exists, dest = po_destination(user.tenant_id, po_log_id)
    if exists:
        require_destination_in_scope(user, dest)


def po_guard(po_log_id: str, user: CurrentUser = Depends(get_current_user)) -> None:
    """Dependency for any route with a `{po_log_id}` path parameter: the order's
    destination warehouse must be the caller's. Put it AFTER the role guard in
    the signature so a wrong role is still reported as a wrong role."""
    require_po_in_scope(user, po_log_id)


def allowed_po_ids(user: CurrentUser) -> set[str] | None:
    """Ids of the orders the caller may see; None when unrestricted."""
    if scope_names(user) is None:
        return None
    default = _tenant_default(user.tenant_id)
    rows = query(
        "SELECT id, destination_warehouse FROM inventory_po_log WHERE tenant_id = %s",
        (user.tenant_id,),
    )
    return {r["id"] for r in rows
            if in_scope(user, (r["destination_warehouse"] or "").strip() or default)}


def filter_po_rows(user: CurrentUser, rows: list[dict], key: str = "id") -> list[dict]:
    """Order rows (by their id under `key`) the caller may see."""
    allowed = allowed_po_ids(user)
    if allowed is None:
        return rows
    return [r for r in rows if r.get(key) in allowed]


# ── Validation of a scope being SET ──────────────────────────────────────────

def validate_scope_ids(tenant_id: str, ids: list[str] | None) -> list[str] | None:
    """The ids to store, after checking every one is a warehouse of THIS tenant.

    None passes through (unrestricted). An unknown or foreign id is refused
    rather than silently dropped: dropping it would store a narrower scope than
    the admin thinks they set.
    """
    if ids is None:
        return None
    clean = list(dict.fromkeys(str(i).strip() for i in ids if str(i).strip()))
    if len(clean) > 500:
        raise AppError("warehouse_scope_invalid", "Too many warehouses.", status_code=422)
    if clean:
        rows = query(
            "SELECT id FROM warehouses WHERE tenant_id = %s AND id = ANY(%s)",
            (tenant_id, clean),
        )
        known = {r["id"] for r in rows}
        unknown = [i for i in clean if i not in known]
        if unknown:
            raise AppError(
                "warehouse_scope_invalid",
                "One of the warehouses does not exist in this account.",
                status_code=422, params={"warehouse_id": unknown[0][:60]},
            )
    return clean
