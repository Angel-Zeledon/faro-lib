"""
Blanket supply contracts ("contratos marco"): a customer's volume agreement
called off in releases (see `inventory/supply_contract_service.py`).

Its own router and tag, INTERNAL in `api/public_surface.py`: a contract moves
purchase decisions through the commitments it materialises and is recorded
under a person's name, the same reason committed demand is internal.

Warehouse scope: every route passes the caller's `wscope.scope_names(user)`
to the service, which shows and writes only contracts naming one of the
caller's warehouses; a contract with no warehouse (company-wide) is for
company-wide users only.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.inventory import supply_contract_service as svc
from backend.schemas.common import ok

router = APIRouter(tags=["supply-contracts"])


class LineBody(BaseModel):
    sku: str = Field(min_length=1, max_length=svc.MAX_SKU_LENGTH)
    # Optional only for an explicit schedule, whose releases then define it.
    total_quantity: Optional[float] = Field(default=None, gt=0, le=svc.MAX_QUANTITY)
    unit_price: Optional[float] = Field(default=None, ge=0, le=svc.MAX_PRICE)


class ReleaseBody(BaseModel):
    # Everything optional HERE on purpose: the service validates each pasted
    # row and names every bad one at once (row number + reason), which a
    # pydantic 422 on the first bad field cannot do.
    sku: Optional[str] = Field(default=None, max_length=svc.MAX_SKU_LENGTH)
    date: Optional[str] = Field(default=None, max_length=40)
    quantity: Optional[float] = None


class TermsBody(BaseModel):
    customer: str = Field(min_length=1, max_length=svc.MAX_CUSTOMER_LENGTH)
    reference: Optional[str] = Field(default=None, max_length=svc.MAX_REFERENCE_LENGTH)
    lines: list[LineBody] = Field(min_length=1, max_length=svc.MAX_LINES)
    period_start: str
    period_end: str
    schedule_kind: Literal["monthly", "weekly", "explicit"]
    releases: Optional[list[ReleaseBody]] = Field(default=None, max_length=svc.MAX_RELEASES)
    tolerance_pct: float = Field(default=0.0, ge=0, le=100)
    warehouse_id: Optional[str] = Field(default=None, max_length=64)
    # False when the sales history already contains this customer's demand.
    on_top_of_base: bool = True
    note: Optional[str] = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


class CreateBody(TermsBody):
    status: Literal["draft", "active"] = "draft"


class ReviseBody(TermsBody):
    # The revision the person was looking at: a stale form is refused (409)
    # instead of silently overwriting somebody else's revision.
    expected_revision: int = Field(ge=1)


class StatusBody(BaseModel):
    status: Literal["active", "closed", "cancelled"]
    expected_revision: int = Field(ge=1)


def _terms(body: TermsBody) -> dict:
    d = body.model_dump(exclude={"status", "expected_revision"})
    d["lines"] = [ln.model_dump() for ln in body.lines]
    d["releases"] = None if body.releases is None else [r.model_dump() for r in body.releases]
    return d


def _details(c: dict) -> dict:
    return {"customer": c["customer"], "lines": len(c.get("lines") or []),
            "revision": c["revision"], "status": c["status"]}


@router.get("/supply-contracts")
def list_supply_contracts(
    status: Optional[str] = Query(default=None, max_length=20),
    user: CurrentUser = Depends(get_current_user),
):
    items = svc.list_contracts(user.tenant_id, wscope.scope_names(user), status=status)
    return ok({"statuses": list(svc.STATUSES), "items": items})


@router.post("/supply-contracts/preview")
def preview_supply_contract(body: TermsBody, user: CurrentUser = Depends(get_current_user)):
    """The releases these terms produce. Writes nothing and reads no stored
    row; the form shows the table before anybody saves."""
    return ok(svc.preview(_terms(body)))


@router.get("/supply-contracts/{root_id}")
def get_supply_contract(root_id: str, user: CurrentUser = Depends(get_current_user)):
    return ok(svc.get(user.tenant_id, wscope.scope_names(user), root_id))


@router.post("/supply-contracts", status_code=201)
def create_supply_contract(body: CreateBody,
                           user: CurrentUser = Depends(require_analyst_or_above)):
    c = svc.create(user.tenant_id, user.user_id, wscope.scope_names(user), _terms(body),
                   status=body.status)
    record_event(user.tenant_id, user.user_id, "supply_contract.created",
                 resource=c["root_id"], details=_details(c))
    return ok(c)


@router.post("/supply-contracts/{root_id}/revisions")
def revise_supply_contract(root_id: str, body: ReviseBody,
                           user: CurrentUser = Depends(require_analyst_or_above)):
    c = svc.revise(user.tenant_id, user.user_id, wscope.scope_names(user), root_id,
                   body.expected_revision, _terms(body))
    record_event(user.tenant_id, user.user_id, "supply_contract.revised",
                 resource=root_id, details=_details(c))
    return ok(c)


@router.post("/supply-contracts/{root_id}/status")
def set_supply_contract_status(root_id: str, body: StatusBody,
                               user: CurrentUser = Depends(require_analyst_or_above)):
    c = svc.set_status(user.tenant_id, user.user_id, wscope.scope_names(user), root_id,
                       body.expected_revision, body.status)
    record_event(user.tenant_id, user.user_id, "supply_contract.status_changed",
                 resource=root_id, details=_details(c))
    return ok(c)
