"""Flat, versioned datasets for BI tools (Power BI, Excel, Looker, any HTTP client).

The product's JSON is built for screens: nested rows, one call per order for its
lines, an envelope per page. A BI tool wants a table. These endpoints are that
table, one URL each:

    GET /api/v1/datasets/schema                  every dataset's columns and types
    GET /api/v1/datasets/inventory-status
    GET /api/v1/datasets/purchase-order-lines
    GET /api/v1/datasets/receptions
    GET /api/v1/datasets/forecast-points
    GET /api/v1/datasets/accuracy
    GET /api/v1/datasets/committed-demand

`?format=json` (default) or `?format=csv`; `?page=` / `?limit=` (cap 10 000) over
a stable, unique sort; `?updated_since=` where the dataset has a timestamp. The
columns are a CONTRACT (`backend/bi_datasets/spec.py`): adding one is allowed,
renaming one is not. Every response carries `X-Schema-Version`.

Access: API access is a paid feature, so the plan check applies to a signed-in
person as well as to a key (a key is also checked in the auth guard, on every
call). Authentication is `Authorization: Bearer sk_live_...` or `X-API-Key:
sk_live_...`; a key in the URL is never accepted (it would be written to every
proxy and browser history).

Tagged `bi-datasets` (not `datasets`, which is the sales-file upload area) so
the existing upload routes are untouched. This router is included BEFORE the
upload router: `/datasets/{dataset_id}` would otherwise swallow these paths.
"""

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request, Response

from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user
from backend.bi_datasets import render, service
from backend.bi_datasets.spec import (
    DATASETS, SCHEMA_VERSION, Dataset, describe, unknown_parameters,
)
from backend.entitlements.service import ensure_feature
from backend.errors import AppError
from backend.schemas.common import ok

router = APIRouter(prefix="/datasets", tags=["bi-datasets"])


def bi_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """The caller, once their plan includes API access.

    An `sk_live_*` key was already checked in the auth guard (before the rate
    window and the meter, so a refused call is neither counted nor billed), so it
    is not asked twice. A signed-in person has no key to check; they are asked
    here, so these feeds are paid for the same whoever calls them.
    """
    if not user.is_machine:
        ensure_feature(
            user.tenant_id, "api",
            "The flat data feeds for BI tools come with the plan that includes API access.",
        )
    return user


def _reject_unknown_parameters(dataset: Dataset, request: Request) -> None:
    unknown = unknown_parameters(dataset, request.query_params.keys())
    if unknown:
        raise AppError(
            "dataset_parameter_unsupported",
            f"The {dataset.name} dataset does not accept the parameter '{unknown[0]}'. "
            f"Accepted: {', '.join(sorted(dataset.accepted_parameters))}.",
            status_code=422,
            params={"dataset": dataset.name, "parameter": unknown[0],
                    "accepted": ", ".join(sorted(dataset.accepted_parameters))},
        )


def _serve(
    dataset: Dataset, request: Request, response: Response, user: CurrentUser,
    fmt: str, page: int, limit: int, params: dict, scope,
):
    # `scope` is the caller's warehouse scope (None = every warehouse). The
    # loaders in backend/bi_datasets/service.py apply it to the rows (or refuse
    # with warehouse_scope_company_totals); here it is DISCLOSED, so somebody
    # building a report on a scoped key sees that the table is not the company.
    _reject_unknown_parameters(dataset, request)
    rows, info = service.load(dataset, user, params, page, limit)
    shaped = render.shape_rows(dataset, rows)
    headers = {
        "X-Schema-Version": dataset.version,
        "X-Dataset": dataset.name,
        # A person's refresh must never be served a copy somebody cached.
        "Cache-Control": "no-store",
        **render.page_headers(info),
        **({"X-Warehouse-Scope": "limited"} if scope is not None else {}),
    }
    if fmt == "csv":
        return Response(
            content=render.to_csv(dataset, shaped).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={**headers,
                     "Content-Disposition": f'inline; filename="{dataset.name}.csv"'},
        )
    for name, value in headers.items():
        response.headers[name] = value
    return ok({
        "dataset": dataset.name,
        "schema_version": dataset.version,
        "columns": list(dataset.column_names),
        "page": info["page"], "limit": info["limit"], "total": info["total"],
        "pages": info["pages"], "next_page": info["next_page"],
        # Present only for a caller limited to some warehouses.
        **({"scope": {"warehouses": sorted(scope)}} if scope is not None else {}),
        "items": shaped,
    })


_FORMAT = Query(default="json", pattern="^(json|csv)$",
                description="json (default) or csv: UTF-8 with BOM, invariant numbers and ISO dates")
_PAGE = Query(default=1, ge=1, description="1-based page number")
_LIMIT = Query(default=render.DEFAULT_LIMIT, ge=1, le=render.MAX_LIMIT,
               description="Rows per page")


@router.get("/schema")
def datasets_schema(response: Response, user: CurrentUser = Depends(bi_user)):
    """Every dataset's columns, types, sort and parameters: the contract a report binds to."""
    response.headers["X-Schema-Version"] = SCHEMA_VERSION
    return ok({
        "schema_version": SCHEMA_VERSION,
        "contract": "Adding columns is allowed; renaming, removing or retyping one "
                    "needs a new dataset version.",
        "formats": {
            "csv": "UTF-8 with a byte-order mark, CRLF, '.' decimal separator, no "
                   "thousands separator, dates YYYY-MM-DD, instants in UTC "
                   "(YYYY-MM-DDTHH:MM:SSZ), booleans true/false, null as an empty cell. "
                   "Text starting with = + - @ gets a leading apostrophe.",
            "paging": f"page (1-based) and limit (default {render.DEFAULT_LIMIT}, "
                      f"max {render.MAX_LIMIT}); X-Total-Count / X-Page-Count / "
                      "X-Next-Page headers, and next_page in the JSON.",
        },
        "datasets": [describe(d) for d in DATASETS.values()],
    })


@router.get("/inventory-status")
def inventory_status_dataset(
    request: Request, response: Response,
    format: str = _FORMAT, page: int = _PAGE, limit: int = _LIMIT,
    session_id: Optional[str] = Query(
        default=None, max_length=100,
        description="Completed session; defaults to the active one"),
    user: CurrentUser = Depends(bi_user),
):
    """One row per SKU and warehouse: signal, stock, incoming, coverage, reorder point,
    recommended quantity, forecast source and money at risk."""
    return _serve(DATASETS["inventory-status"], request, response, user, format, page,
                  limit, {"session_id": session_id}, wscope.scope_names(user))


@router.get("/purchase-order-lines")
def purchase_order_lines_dataset(
    request: Request, response: Response,
    format: str = _FORMAT, page: int = _PAGE, limit: int = _LIMIT,
    updated_since: Optional[datetime] = Query(
        default=None, description="ISO 8601 (URL-encode a +offset, or use Z); rows whose order changed at or after it"),
    user: CurrentUser = Depends(bi_user),
):
    """Every purchase order line with order, supplier, state, dates, quantity, cost and received quantity."""
    return _serve(DATASETS["purchase-order-lines"], request, response, user, format, page,
                  limit, {"updated_since": updated_since}, wscope.scope_names(user))


@router.get("/receptions")
def receptions_dataset(
    request: Request, response: Response,
    format: str = _FORMAT, page: int = _PAGE, limit: int = _LIMIT,
    updated_since: Optional[datetime] = Query(
        default=None, description="ISO 8601 (URL-encode a +offset, or use Z); lines received at or after it"),
    user: CurrentUser = Depends(bi_user),
):
    """Purchase order lines with units received, and when."""
    return _serve(DATASETS["receptions"], request, response, user, format, page,
                  limit, {"updated_since": updated_since}, wscope.scope_names(user))


@router.get("/forecast-points")
def forecast_points_dataset(
    request: Request, response: Response,
    format: str = _FORMAT, page: int = _PAGE, limit: int = _LIMIT,
    session_id: Optional[str] = Query(
        default=None, max_length=100,
        description="Completed session; defaults to the active one"),
    user: CurrentUser = Depends(bi_user),
):
    """The champion forecast per SKU per period, with its band."""
    return _serve(DATASETS["forecast-points"], request, response, user, format, page,
                  limit, {"session_id": session_id}, wscope.scope_names(user))


@router.get("/accuracy")
def accuracy_dataset(
    request: Request, response: Response,
    format: str = _FORMAT, page: int = _PAGE, limit: int = _LIMIT,
    session_id: Optional[str] = Query(
        default=None, max_length=100,
        description="Completed session; defaults to the active one"),
    user: CurrentUser = Depends(bi_user),
):
    """WAPE and bias of each SKU's champion model."""
    return _serve(DATASETS["accuracy"], request, response, user, format, page,
                  limit, {"session_id": session_id}, wscope.scope_names(user))


@router.get("/committed-demand")
def committed_demand_dataset(
    request: Request, response: Response,
    format: str = _FORMAT, page: int = _PAGE, limit: int = _LIMIT,
    updated_since: Optional[datetime] = Query(
        default=None, description="ISO 8601 (URL-encode a +offset, or use Z); commitments changed at or after it"),
    user: CurrentUser = Depends(bi_user),
):
    """Customer orders placed ahead of time, with their delivery date, probability, status and risk."""
    return _serve(DATASETS["committed-demand"], request, response, user, format, page,
                  limit, {"updated_since": updated_since}, wscope.scope_names(user))
