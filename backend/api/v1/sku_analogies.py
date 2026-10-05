"""
Forecast by analogy: "this new product will sell like these" (see
`inventory/analogy_service.py`).

Its own router and tag: the statement is a person's judgement, recorded under
their name, so an API key does not make it (the tag is INTERNAL in
`api/public_surface.py`). Defining one trains nothing: the status screen reads
the ledger and says, on the row, that the number is an analogy.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.inventory import analogy_service as svc
from backend.schemas.common import ok

router = APIRouter(tags=["sku-analogies"])


class AnalogyBody(BaseModel):
    new_sku: str = Field(min_length=1, max_length=svc.MAX_SKU_LENGTH)
    reference_skus: list[str] = Field(min_length=1, max_length=5)
    scale_factor: float = Field(default=1.0)
    start_date: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


def _details(row: dict) -> dict:
    # Language-neutral: the activity feed needs no copy.
    return {"sku": row["new_sku"], "references": ",".join(row["reference_skus"])}


@router.get("/sku-analogies")
def list_analogies(
    sku: Optional[str] = Query(default=None, max_length=svc.MAX_SKU_LENGTH),
    include_reverted: bool = Query(default=False),
    user: CurrentUser = Depends(get_current_user),
):
    lo, hi, smin, smax = svc._limits()
    return ok({
        "limits": {"min_references": lo, "max_references": hi,
                   "min_scale": smin, "max_scale": smax},
        "items": svc.list_for_tenant(user.tenant_id, sku, include_reverted),
    })


@router.post("/sku-analogies", status_code=201)
def create_analogy(body: AnalogyBody, user: CurrentUser = Depends(require_analyst_or_above)):
    row = svc.create(
        user.tenant_id, user.user_id, new_sku=body.new_sku,
        reference_skus=body.reference_skus, scale_factor=body.scale_factor,
        start_date=body.start_date, note=body.note)
    record_event(user.tenant_id, user.user_id, "forecast.analogy_defined",
                 resource=row["id"], details=_details(row))
    return ok(row)


@router.post("/sku-analogies/{analogy_id}/revert")
def revert_analogy(analogy_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    row = svc.revert(user.tenant_id, analogy_id, user.user_id)
    record_event(user.tenant_id, user.user_id, "forecast.analogy_reverted",
                 resource=row["id"], details=_details(row))
    return ok(row)
