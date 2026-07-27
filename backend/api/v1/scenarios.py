"""
What-if scenario API (PENDIENTES #7).

POST   /sessions/{session_id}/scenarios              — save a scenario
GET    /sessions/{session_id}/scenarios              — list saved scenarios
POST   /sessions/{session_id}/scenarios/preview      — run inline rules (no save)
POST   /sessions/{session_id}/scenarios/{id}/run     — run a saved scenario
GET    /scenarios/{id}                               — read one scenario
DELETE /scenarios/{id}                               — delete a scenario

Every route sits behind the EVENT_SIMULATOR entitlement (professional+), the
same gate the promo/event simulator uses. Running is a pure read — nothing is
persisted — so it only needs `get_current_user`.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from backend.auth.guards import CurrentUser, get_current_user, require_analyst_or_above
from backend.entitlements.guards import require_feature
from backend.entitlements.plans import Feature
from backend.errors import AppError
from backend.scenarios import service as svc
from backend.schemas.common import ok

router = APIRouter(tags=["scenarios"])

_GATE = Depends(require_feature(Feature.EVENT_SIMULATOR))

MAX_RULES = 50


class ScenarioRuleIn(BaseModel):
    """One typed what-if rule. Only the fields its `type` uses are kept."""

    type: Literal["demand_multiplier", "promo", "supplier_delay", "safety_stock"]
    label: Optional[str] = Field(default=None, max_length=120)

    # demand_multiplier / promo
    multiplier: Optional[float] = Field(default=None, ge=svc.MIN_MULTIPLIER, le=svc.MAX_MULTIPLIER)
    sku:        Optional[str]   = None
    category:   Optional[str]   = None
    date_from:  Optional[str]   = None
    date_to:    Optional[str]   = None

    # supplier_delay
    extra_days: Optional[int]   = Field(default=None, ge=0, le=svc.MAX_EXTRA_DAYS)
    supplier:   Optional[str]   = None

    # safety_stock
    service_level: Optional[float] = Field(
        default=None, ge=svc.MIN_SERVICE_LEVEL, le=svc.MAX_SERVICE_LEVEL
    )

    @model_validator(mode="after")
    def _check_required_per_type(self):
        # Delegated to the service so the API and the runner agree on exactly
        # one definition of a valid rule (the runner re-validates rules loaded
        # back from the DB).
        svc.normalize_rule(self.model_dump(exclude_none=True))
        return self


class ScenarioCreate(BaseModel):
    name:  str = Field(min_length=1, max_length=120)
    rules: list[ScenarioRuleIn] = Field(default_factory=list, max_length=MAX_RULES)


class ScenarioPreview(BaseModel):
    rules: list[ScenarioRuleIn] = Field(default_factory=list, max_length=MAX_RULES)
    name:  Optional[str] = Field(default=None, max_length=120)


def _serialize(row: dict) -> dict:
    return {
        "id":         row["id"],
        "session_id": row["session_id"],
        "name":       row["name"],
        "rules":      row.get("rules") or [],
        "created_by": row.get("created_by"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/scenarios", status_code=201, dependencies=[_GATE])
def create_scenario(
    session_id: str,
    body: ScenarioCreate,
    user: CurrentUser = Depends(require_analyst_or_above),
):
    """Save a named what-if scenario for this session."""
    row = svc.create_scenario(
        user.tenant_id, session_id, body.name,
        [r.model_dump(exclude_none=True) for r in body.rules],
        created_by=user.user_id,
    )
    return ok(_serialize(row))


@router.get("/sessions/{session_id}/scenarios", dependencies=[_GATE])
def list_scenarios(session_id: str, user: CurrentUser = Depends(get_current_user)):
    """Saved scenarios for this session, newest first."""
    return ok([_serialize(r) for r in svc.list_scenarios(user.tenant_id, session_id)])


@router.get("/scenarios/{scenario_id}", dependencies=[_GATE])
def get_scenario(scenario_id: str, user: CurrentUser = Depends(get_current_user)):
    row = svc.get_scenario(user.tenant_id, scenario_id)
    if not row:
        raise AppError("scenario_not_found", "Scenario not found", status_code=404)
    return ok(_serialize(row))


@router.delete("/scenarios/{scenario_id}", dependencies=[_GATE])
def delete_scenario(scenario_id: str, user: CurrentUser = Depends(require_analyst_or_above)):
    if not svc.delete_scenario(user.tenant_id, scenario_id):
        raise AppError("scenario_not_found", "Scenario not found", status_code=404)
    return ok({"deleted": scenario_id})


# ── Running ───────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/scenarios/preview", dependencies=[_GATE])
def preview_scenario(
    session_id: str,
    body: ScenarioPreview,
    top_changes: int = Query(default=50, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Run inline rules against the session WITHOUT saving them, so the builder can
    show base vs scenario before the user commits to a name.
    """
    return ok(svc.run_scenario(
        user.tenant_id, session_id,
        [r.model_dump(exclude_none=True) for r in body.rules],
        top_changes=top_changes, name=body.name,
    ))


@router.post("/sessions/{session_id}/scenarios/{scenario_id}/run", dependencies=[_GATE])
def run_scenario(
    session_id: str,
    scenario_id: str,
    top_changes: int = Query(default=50, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """Run a saved scenario and return the BASE vs SCENARIO comparison."""
    return ok(svc.run_saved_scenario(
        user.tenant_id, session_id, scenario_id, top_changes=top_changes,
    ))
