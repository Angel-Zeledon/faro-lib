"""
AI Insights API — narrative intelligence layer.
Generates natural language narratives from structured business data.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth.guards import CurrentUser, get_current_user
from backend.schemas.common import ok

router = APIRouter(prefix="/ai", tags=["ai-insights"])
log = logging.getLogger(__name__)


# ── Request models ─────────────────────────────────────────────────────────────

class MorningNarrativeRequest(BaseModel):
    session_id:  str
    profile:     str = 'distributor'


class InventoryInsightRequest(BaseModel):
    session_id: str
    profile:    str = 'distributor'


class ForecastExplanationRequest(BaseModel):
    sku:        str
    session_id: str
    profile:    str = 'distributor'


class SuggestedQuestionsRequest(BaseModel):
    profile:         str  = 'distributor'
    has_inventory:   bool = True
    has_production:  bool = False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/narrative/morning")
def morning_narrative(
    body: MorningNarrativeRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Generates an executive morning briefing narrative from the inventory briefing data.
    Adapts language and focus to the business profile.
    """
    from backend.inventory.service import get_morning_briefing
    from backend.ai.narrative_service import generate_morning_narrative

    try:
        briefing = get_morning_briefing(user.tenant_id, body.session_id)
        result   = generate_morning_narrative(briefing, body.profile)
        return ok(result)
    except Exception as e:
        log.error("Morning narrative error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/narrative/inventory")
def inventory_insight(
    body: InventoryInsightRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Generates a concise insight about the current inventory state.
    """
    from backend.inventory.service import get_inventory_status
    from backend.ai.narrative_service import generate_inventory_insight

    try:
        items  = get_inventory_status(user.tenant_id, body.session_id)
        result = generate_inventory_insight(items, body.profile)
        return ok(result)
    except Exception as e:
        log.error("Inventory insight error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/narrative/forecast-explanation")
def forecast_explanation(
    body: ForecastExplanationRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """
    Explains a specific SKU's inventory signal and recommendation in plain language.
    """
    from backend.inventory.service import get_inventory_status
    from backend.ai.narrative_service import generate_forecast_explanation

    try:
        items    = get_inventory_status(user.tenant_id, body.session_id)
        sku_data = next((i for i in items if i['sku'] == body.sku), None)
        if not sku_data:
            raise HTTPException(status_code=404, detail=f"SKU '{body.sku}' not found in inventory status")
        result = generate_forecast_explanation(body.sku, sku_data, body.profile)
        return ok(result)
    except HTTPException:
        raise
    except Exception as e:
        log.error("Forecast explanation error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/suggested-questions")
def suggested_questions(
    body: SuggestedQuestionsRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Returns context-aware suggested questions for the AI Analyst."""
    from backend.ai.narrative_service import get_suggested_questions
    return ok(get_suggested_questions(body.profile, body.has_inventory, body.has_production))
