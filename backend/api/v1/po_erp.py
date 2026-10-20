"""Purchase orders out to the ERP: the feed, the import files and the ERP's own
document number written back. Rules and reasons in `backend/po_erp/service.py`.

Every read here is a pure read (it records nothing), so a read-scoped API key can
poll the feed; writing the ERP reference needs an analyst, or a write key.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend import audit
from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.errors import AppError
from backend.po_erp import core, formats, service
from backend.schemas.common import ok

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders-erp"])


class ErpReferenceBody(BaseModel):
    reference: str = Field(min_length=1, max_length=core.MAX_REFERENCE_LENGTH)
    # Which supplier's document the number belongs to. Optional when the order has
    # a single supplier, which is the common case.
    supplier_id: Optional[str] = Field(default=None, max_length=100)


@router.get("/export")
def export_feed(
    since: Optional[str] = Query(default=None, description="Cursor from the previous page"),
    limit: Optional[int] = Query(default=None, ge=1, le=core.MAX_PAGE_SIZE),
    user: CurrentUser = Depends(get_current_user),
):
    """Approved orders changed after `since`, oldest change first, with a cursor to
    resume from. A cancelled order comes back with `state = "cancelled"`. An order
    still waiting for approval is not here until it is approved."""
    return ok(service.read_feed(user, since, limit))


@router.get("/export/formats")
def export_formats(user: CurrentUser = Depends(get_current_user)):
    """The ERP file layouts, and which of them have been run against a real system."""
    return ok([{
        "key": f.key, "label": f.label, "extension": f.extension,
        "verified": f.verified, "unmapped_fields": list(f.unmapped_fields),
    } for f in formats.FORMATS.values()])


@router.get("/export/file")
def export_file(
    request: Request,
    po_ids: str = Query(..., description="Comma-separated purchase order ids"),
    format: str = Query(default="csv"),
    user: CurrentUser = Depends(get_current_user),
):
    """The chosen orders as the file the ERP imports. One document per supplier."""
    fmt = formats.FORMATS.get(format)
    if fmt is None:
        raise AppError("erp_format_unknown", "Unknown ERP file format", status_code=400,
                       params={"format": format, "known": sorted(formats.FORMATS)})
    ids = list(dict.fromkeys(p.strip() for p in po_ids.split(",") if p.strip()))
    docs = service.documents_for_export(user, ids)
    audit.note(request, after={"orders": len(ids), "documents": len(docs),
                               "format": fmt.key, "verified": fmt.verified})
    return Response(
        content=fmt.render(docs),
        media_type=fmt.media_type,
        headers={"Content-Disposition":
                 f"attachment; filename=purchase_orders_{fmt.key}.{fmt.extension}"},
    )


@router.get("/unacknowledged")
def unacknowledged(user: CurrentUser = Depends(get_current_user)):
    """Open approved orders whose ERP document number nobody has written back."""
    return ok(service.unacknowledged(user))


@router.post("/{po_log_id}/erp-reference")
def set_erp_reference(
    po_log_id: str,
    body: ErpReferenceBody,
    user: CurrentUser = Depends(require_analyst_or_above),
    _scope: None = Depends(wscope.po_guard),
):
    """Store the ERP's own document number for an order. Idempotent on
    (order, reference); a different number for the same document is refused."""
    return ok(service.acknowledge(user.tenant_id, user.user_id, po_log_id,
                                  body.reference, body.supplier_id))
