"""
Committed demand: customer orders placed ahead of time
(see `inventory/committed_demand_service.py`).

Its own router and tag, INTERNAL in `api/public_surface.py`: a commitment moves
purchase decisions and is recorded under a person's name, so an API key does not
enter them yet.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.db.connection import query_one
from backend.errors import AppError
from backend.inventory import committed_demand_service as svc
from backend.schemas.common import ok

router = APIRouter(tags=["committed-demand"])


class CommitmentBody(BaseModel):
    sku: str = Field(min_length=1, max_length=200)
    delivery_date: str
    quantity: float = Field(gt=0, le=svc.MAX_QUANTITY)
    customer: Optional[str] = Field(default=None, max_length=svc.MAX_CUSTOMER_LENGTH)
    # 1 = firm. A promise that may not happen carries its likelihood.
    probability: float = Field(default=1.0, gt=0, le=1)
    warehouse_id: Optional[str] = Field(default=None, max_length=64)
    # False when the history already contains this customer's demand.
    on_top_of_base: bool = True
    note: Optional[str] = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


class CommitmentPatch(BaseModel):
    sku: Optional[str] = Field(default=None, min_length=1, max_length=200)
    delivery_date: Optional[str] = None
    quantity: Optional[float] = Field(default=None, gt=0, le=svc.MAX_QUANTITY)
    customer: Optional[str] = Field(default=None, max_length=svc.MAX_CUSTOMER_LENGTH)
    probability: Optional[float] = Field(default=None, gt=0, le=1)
    warehouse_id: Optional[str] = Field(default=None, max_length=64)
    on_top_of_base: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


class BulkBody(BaseModel):
    rows: list[CommitmentBody] = Field(min_length=1, max_length=svc.MAX_BULK_ROWS)


class StatusBody(BaseModel):
    status: str = Field(pattern="^(open|fulfilled|cancelled)$")


# ── Warehouse scope ──────────────────────────────────────────────────────────
# A commitment names its warehouse by ID (or none: a company-wide promise). A
# user limited to some warehouses (`wscope`) sees, enters and changes only the
# commitments that name one of THEIR warehouses; an unassigned commitment is a
# company-wide figure and stays with company-wide users. The at-risk verdict a
# scoped user reads is computed against their warehouses' stock and arrivals
# only, never the company's. The assistant's `list_committed_demand` tool calls
# `list_commitments` with the caller's user, so it inherits all of this.

def _visible(allowed: frozenset[str] | None, row: dict) -> bool:
    return allowed is None or (row.get("warehouse_id") or None) in allowed


def _require_writable_warehouse(user: CurrentUser, allowed: frozenset[str] | None,
                                warehouse_id: Optional[str]) -> None:
    """A scoped caller must name one of their own warehouses."""
    if allowed is None:
        return
    wid = (warehouse_id or "").strip() or None
    if wid is None:
        raise AppError(
            "committed_demand_warehouse_required",
            "A user limited to some warehouses must assign the commitment to one of them.",
            status_code=422,
        )
    if wid not in allowed:
        row = query_one("SELECT name FROM warehouses WHERE id = %s AND tenant_id = %s",
                        (wid, user.tenant_id))
        raise wscope.denied(row["name"] if row else wid)


def _get_visible(user: CurrentUser, allowed: frozenset[str] | None, commitment_id: str) -> dict:
    """The commitment, or the same 404 a missing one gets: a scoped user is not
    told that a commitment exists in a warehouse they cannot see."""
    row = svc.get(user.tenant_id, commitment_id)
    if not _visible(allowed, row):
        raise AppError("committed_demand_not_found", "Commitment not found", status_code=404)
    return row


@router.get("/committed-demand")
def list_commitments(
    sku: Optional[str] = Query(default=None, max_length=200),
    status: Optional[str] = Query(default=None, max_length=20),
    limit: int = Query(default=500, ge=1, le=2000),
    user: CurrentUser = Depends(get_current_user),
):
    allowed = wscope.scope_warehouse_ids(user)
    items = svc.list_for_tenant(user.tenant_id, sku=sku, status=status, limit=limit,
                                warehouse_ids=allowed)
    # Each open item carries its at-risk verdict (stock + incoming against all
    # open commitments due up to its date); `by_customer` rolls it up. For a
    # scoped caller both sides of that comparison are their warehouses only.
    svc.annotate_risk(user.tenant_id, items,
                      warehouse_names=wscope.scope_names(user) if allowed is not None else None)
    return ok({
        "statuses": list(svc.STATUSES),
        "items": items,
        "by_customer": svc.summarize_by_customer(items),
        # Says which verdict this is, so a screen never presents a scoped
        # user's partial figure as the company's.
        "scope": "company" if allowed is None else "warehouses",
    })


@router.post("/committed-demand", status_code=201)
def create_commitment(body: CommitmentBody,
                      user: CurrentUser = Depends(require_analyst_or_above)):
    _require_writable_warehouse(user, wscope.scope_warehouse_ids(user), body.warehouse_id)
    row = svc.create(user.tenant_id, user.user_id, **body.model_dump())
    record_event(user.tenant_id, user.user_id, "committed_demand.created",
                 resource=row["id"],
                 details={"sku": row["sku"], "quantity": row["quantity"],
                          "delivery_date": row["delivery_date"],
                          "customer": row["customer"] or ""})
    return ok(row)


@router.post("/committed-demand/bulk", status_code=201)
def create_commitments_bulk(body: BulkBody,
                            user: CurrentUser = Depends(require_analyst_or_above)):
    """All rows or none: a refusal names every bad row and nothing is written."""
    allowed = wscope.scope_warehouse_ids(user)
    for r in body.rows:  # every row checked BEFORE anything is written
        _require_writable_warehouse(user, allowed, r.warehouse_id)
    result = svc.create_many(user.tenant_id, user.user_id,
                             [r.model_dump() for r in body.rows])
    record_event(user.tenant_id, user.user_id, "committed_demand.imported",
                 resource="bulk", details={"rows": result["created"]})
    return ok(result)


@router.patch("/committed-demand/{commitment_id}")
def update_commitment(commitment_id: str, body: CommitmentPatch,
                      user: CurrentUser = Depends(require_analyst_or_above)):
    allowed = wscope.scope_warehouse_ids(user)
    _get_visible(user, allowed, commitment_id)
    fields = body.model_dump(exclude_unset=True)
    if "warehouse_id" in fields:  # moving it: the destination must be theirs too
        _require_writable_warehouse(user, allowed, fields["warehouse_id"])
    row = svc.update(user.tenant_id, commitment_id, user.user_id, **fields)
    record_event(user.tenant_id, user.user_id, "committed_demand.changed",
                 resource=row["id"], details={"sku": row["sku"], "status": row["status"]})
    return ok(row)


@router.post("/committed-demand/{commitment_id}/status")
def set_commitment_status(commitment_id: str, body: StatusBody,
                          user: CurrentUser = Depends(require_analyst_or_above)):
    _get_visible(user, wscope.scope_warehouse_ids(user), commitment_id)
    row = svc.set_status(user.tenant_id, commitment_id, user.user_id, body.status)
    record_event(user.tenant_id, user.user_id, "committed_demand.changed",
                 resource=row["id"],
                 details={"sku": row["sku"], "status": row["status"]})
    return ok(row)
