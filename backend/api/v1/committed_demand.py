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
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
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


@router.get("/committed-demand")
def list_commitments(
    sku: Optional[str] = Query(default=None, max_length=200),
    status: Optional[str] = Query(default=None, max_length=20),
    limit: int = Query(default=500, ge=1, le=2000),
    user: CurrentUser = Depends(get_current_user),
):
    items = svc.list_for_tenant(user.tenant_id, sku=sku, status=status, limit=limit)
    # Each open item carries its at-risk verdict (stock + incoming against all
    # open commitments due up to its date); `by_customer` rolls it up.
    svc.annotate_risk(user.tenant_id, items)
    return ok({
        "statuses": list(svc.STATUSES),
        "items": items,
        "by_customer": svc.summarize_by_customer(items),
    })


@router.post("/committed-demand", status_code=201)
def create_commitment(body: CommitmentBody,
                      user: CurrentUser = Depends(require_analyst_or_above)):
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
    result = svc.create_many(user.tenant_id, user.user_id,
                             [r.model_dump() for r in body.rows])
    record_event(user.tenant_id, user.user_id, "committed_demand.imported",
                 resource="bulk", details={"rows": result["created"]})
    return ok(result)


@router.patch("/committed-demand/{commitment_id}")
def update_commitment(commitment_id: str, body: CommitmentPatch,
                      user: CurrentUser = Depends(require_analyst_or_above)):
    row = svc.update(user.tenant_id, commitment_id, user.user_id,
                     **body.model_dump(exclude_unset=True))
    record_event(user.tenant_id, user.user_id, "committed_demand.changed",
                 resource=row["id"], details={"sku": row["sku"], "status": row["status"]})
    return ok(row)


@router.post("/committed-demand/{commitment_id}/status")
def set_commitment_status(commitment_id: str, body: StatusBody,
                          user: CurrentUser = Depends(require_analyst_or_above)):
    row = svc.set_status(user.tenant_id, commitment_id, user.user_id, body.status)
    record_event(user.tenant_id, user.user_id, "committed_demand.changed",
                 resource=row["id"],
                 details={"sku": row["sku"], "status": row["status"]})
    return ok(row)
