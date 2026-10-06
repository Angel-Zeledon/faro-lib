"""
Purchase budgets: a cap on purchasing spend and what to order first under it
(see `inventory/purchase_budget_service.py`).

Its own router and tag, INTERNAL in `api/public_surface.py`: setting a cap, or
lifting a hard one, is a person's decision about company money.

Every route is warehouse-dimensioned: a user limited to some warehouses sees and
edits only budgets scoped to THEIR warehouses, and the plan lists only their
warehouses' lines (`wscope`). Nothing here creates an order or changes a
recommended quantity: the plan ANNOTATES, the order flow is untouched.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.activity.events import record_event
from backend.api.v1.currency import currency_of
from backend.auth import warehouse_scope as wscope
from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.errors import AppError
from backend.inventory import purchase_budget_service as svc
from backend.schemas.common import ok

router = APIRouter(tags=["purchase-budgets"])


class BudgetBody(BaseModel):
    period_type: str = Field(pattern="^(month|quarter|custom)$")
    # month / quarter: any day inside the period; custom: the first day.
    period_start: str
    period_end: Optional[str] = None
    amount: float = Field(ge=0, le=svc.MAX_AMOUNT)
    currency: Optional[str] = Field(default=None, max_length=8)
    scope_type: str = Field(default="company", pattern="^(company|warehouse|supplier|category)$")
    scope_value: Optional[str] = Field(default=None, max_length=200)
    parent_root_id: Optional[str] = Field(default=None, max_length=64)
    hard_cap: bool = False
    active: bool = True
    note: Optional[str] = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


class BudgetPatch(BaseModel):
    expected_revision: int = Field(ge=1)
    period_type: Optional[str] = Field(default=None, pattern="^(month|quarter|custom)$")
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0, le=svc.MAX_AMOUNT)
    scope_type: Optional[str] = Field(default=None, pattern="^(company|warehouse|supplier|category)$")
    scope_value: Optional[str] = Field(default=None, max_length=200)
    parent_root_id: Optional[str] = Field(default=None, max_length=64)
    hard_cap: Optional[bool] = None
    active: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


class PlanBody(BaseModel):
    budget_id: Optional[str] = Field(default=None, max_length=64)
    session_id: Optional[str] = Field(default=None, max_length=64)


class CheckLine(BaseModel):
    sku: str = Field(min_length=1, max_length=200)
    qty: float = Field(ge=0, le=1e9)
    unit_cost: Optional[float] = Field(default=None, ge=0, le=1e9)
    # ISO 4217 code of `unit_cost` when it is not the company's own currency.
    currency: Optional[str] = Field(default=None, max_length=8)
    supplier: Optional[str] = Field(default=None, max_length=200)
    supplier_id: Optional[str] = Field(default=None, max_length=64)
    warehouse: Optional[str] = Field(default=None, max_length=200)


class CheckBody(BaseModel):
    lines: list[CheckLine] = Field(min_length=1, max_length=2000)
    destination_warehouse: Optional[str] = Field(default=None, max_length=200)


def _scope_label(row: dict) -> str:
    return f"{row['scope_type']}:{row.get('scope_label') or ''}".rstrip(":")


def _period_label(row: dict) -> str:
    return f"{row['period_start']}..{row['period_end']}"


@router.get("/inventory/budgets")
def list_budgets(include_inactive: bool = Query(default=False),
                 user: CurrentUser = Depends(get_current_user)):
    allowed = wscope.scope_warehouse_ids(user)
    return ok({
        "items": svc.list_budgets(user.tenant_id, allowed, include_inactive=include_inactive),
        # Says which view this is, so a screen never presents a scoped user's
        # partial list as the company's.
        "scope": "company" if allowed is None else "warehouses",
        "currency": currency_of(user.tenant_id)["code"],
    })


@router.post("/inventory/budgets", status_code=201)
def create_budget(body: BudgetBody, user: CurrentUser = Depends(require_analyst_or_above)):
    allowed = wscope.scope_warehouse_ids(user)
    raw = body.model_dump()
    raw["currency"] = (raw.get("currency") or currency_of(user.tenant_id)["code"])
    row = svc.create(user.tenant_id, user.user_id, allowed, raw)
    record_event(user.tenant_id, user.user_id, "purchase_budget.created",
                 resource=row["root_id"],
                 details={"budget_scope": _scope_label(row), "amount": row["amount"],
                          "period": _period_label(row)})
    return ok(row)


@router.patch("/inventory/budgets/{root_id}")
def revise_budget(root_id: str, body: BudgetPatch,
                  user: CurrentUser = Depends(require_analyst_or_above)):
    allowed = wscope.scope_warehouse_ids(user)
    changes = body.model_dump(exclude_unset=True)
    expected = changes.pop("expected_revision")
    row = svc.revise(user.tenant_id, user.user_id, allowed, root_id, expected, changes)
    record_event(user.tenant_id, user.user_id, "purchase_budget.revised",
                 resource=row["root_id"],
                 details={"budget_scope": _scope_label(row), "amount": row["amount"],
                          "period": _period_label(row)})
    return ok(row)


@router.get("/inventory/budgets/{root_id}/history")
def budget_history(root_id: str, user: CurrentUser = Depends(get_current_user)):
    allowed = wscope.scope_warehouse_ids(user)
    return ok({"items": svc.history(user.tenant_id, allowed, root_id)})


@router.get("/inventory/budget/status")
def budget_status(budget_id: Optional[str] = Query(default=None, max_length=64),
                  user: CurrentUser = Depends(get_current_user)):
    """The budget in force today (or the one asked for): amount, spent (received),
    committed (open orders), remaining, burn against elapsed time and projected
    overrun. `budget: null` when no budget is set: the product then behaves
    exactly as it did before budgets existed."""
    allowed = wscope.scope_warehouse_ids(user)
    return ok(svc.status(user.tenant_id, allowed, date.today(), budget_id))


@router.post("/inventory/budget/plan")
def budget_plan(body: PlanBody, user: CurrentUser = Depends(get_current_user)):
    """What a budget funds first among the CURRENT recommendations. Read-only:
    creates no order and changes no recommended quantity. A POST because of the
    body, so it needs no write role."""
    allowed = wscope.scope_warehouse_ids(user)
    return ok(svc.plan(user, allowed, date.today(), body.budget_id, body.session_id))


@router.post("/inventory/budget/check")
def budget_check(body: CheckBody, user: CurrentUser = Depends(get_current_user)):
    """Would this order go past what a budget has left? Read-only preview of the
    check the order flow runs, so a cart can warn before it is submitted."""
    allowed = wscope.scope_warehouse_ids(user)
    lines = [ln.model_dump() for ln in body.lines]
    return ok({"exceeded": svc.check_order(user, allowed, lines, body.destination_warehouse,
                                           date.today())})


# ── The order hook ───────────────────────────────────────────────────────────

def enforce_on_order(user: CurrentUser, lines: list[dict], destination: Optional[str],
                     override_reason: Optional[str]) -> tuple[list[dict], bool]:
    """Run before an order is written. Returns (exceeded budgets, hard cap
    overridden).

    * No budget set / the order fits: ([], False) - nothing changes.
    * A soft budget is exceeded: the order proceeds with the warning.
    * A HARD cap is exceeded: refused (409) unless an administrator (a person,
      never an API key) passes a reason.
    """
    allowed = wscope.scope_warehouse_ids(user)
    exceeded = svc.check_order(user, allowed, lines, destination, date.today())
    reason = (override_reason or "").strip() or None
    hard = [e for e in exceeded if e["hard_cap"]]
    if not hard:
        return exceeded, False
    first = hard[0]
    params = {k: first[k] for k in ("over_by", "remaining", "order_value") if k in first}
    # A hard cap that is flagged only because part of the order could not be
    # converted (no exchange rate) is not "over the cap": its own code says what
    # is missing, so the buyer enters the rate instead of hunting for an excess.
    over = [e for e in hard if e.get("exceeds", True)]
    if not over:
        params = {"unconverted_lines": first.get("unconverted_lines", 0)}
        if user.role == "admin" and not user.is_machine:
            if reason:
                return exceeded, True
            raise AppError("purchase_budget_fx_rate_missing",
                           "Part of this order is priced in a currency with no exchange rate, "
                           "so it cannot be checked against a hard purchasing budget. Enter the "
                           "rate, or an administrator can override with a reason.",
                           status_code=409, params={**params, "override_possible": True})
        if reason:
            raise AppError("purchase_budget_override_requires_admin",
                           "Only an administrator can override a hard purchasing budget.",
                           status_code=403, params=params)
        raise AppError("purchase_budget_fx_rate_missing",
                       "Part of this order is priced in a currency with no exchange rate, "
                       "so it cannot be checked against a hard purchasing budget. Enter the "
                       "rate, or ask an administrator to override.",
                       status_code=409, params={**params, "override_possible": False})
    first = over[0]
    params = {k: first[k] for k in ("over_by", "remaining", "order_value") if k in first}
    if user.role == "admin" and not user.is_machine:
        if reason:
            return exceeded, True
        raise AppError("purchase_budget_hard_cap",
                       "This order exceeds a hard purchasing budget; an administrator can "
                       "override it with a reason.", status_code=409,
                       params={**params, "override_possible": True})
    if reason:
        raise AppError("purchase_budget_override_requires_admin",
                       "Only an administrator can override a hard purchasing budget.",
                       status_code=403, params=params)
    raise AppError("purchase_budget_hard_cap",
                   "This order exceeds a hard purchasing budget; ask an administrator to "
                   "override it.", status_code=409,
                   params={**params, "override_possible": False})


def record_order_budget_events(user: CurrentUser, exceeded: list[dict], overridden: bool,
                               reference: str, override_reason: Optional[str]) -> None:
    """One audited row per exceeded budget, written once the order exists."""
    reason = (override_reason or "").strip() or None
    for e in exceeded:
        if not e.get("exceeds", True) and not (e["hard_cap"] and overridden):
            continue  # flagged only for an unconverted line: a warning, not an excess
        record_event(
            user.tenant_id, user.user_id,
            "purchase_budget.override" if (e["hard_cap"] and overridden) else "purchase_budget.exceeded",
            resource=e.get("root_id") or "",
            details={"budget_scope": e["scope_type"], "over_by": e.get("over_by"),
                     "override_reason": reason or "", "reference": reference})
