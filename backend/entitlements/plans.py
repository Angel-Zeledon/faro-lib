"""Plan catalog — the single source of truth for entitlements.

Each plan maps to numeric limits (None means unlimited) and a set of feature
keys. Tiers are composed by union so a higher tier can never lose a lower
tier's feature.
"""

from dataclasses import dataclass
from enum import Enum


class Feature(str, Enum):
    # Core — every plan
    SEMAPHORE = "semaphore"
    PO_GENERATION = "po_generation"
    RECEPTION = "reception"
    SUPPLIERS = "suppliers"
    REPORTS = "reports"
    EMAIL_ALERTS = "email_alerts"
    # Professional
    ABC_XYZ = "abc_xyz"
    WHATSAPP_ALERTS = "whatsapp_alerts"
    AI_ANALYST = "ai_analyst"
    DOCUMENTS_RAG = "documents_rag"
    EVENT_SIMULATOR = "event_simulator"
    MILP_OPTIMIZER = "milp_optimizer"
    SCHEDULED_REPORTS = "scheduled_reports"
    MULTI_LOCATION = "multi_location"
    # Enterprise
    BOM = "bom"
    API_ACCESS = "api_access"
    WEBHOOKS = "webhooks"
    INTEGRATIONS = "integrations"


@dataclass(frozen=True)
class PlanDef:
    max_skus: int | None
    max_users: int | None
    max_locations: int | None
    max_sessions: int | None
    max_concurrent_jobs: int | None
    max_dataset_size_mb: int | None
    features: frozenset[Feature]


_CORE = frozenset({
    Feature.SEMAPHORE, Feature.PO_GENERATION, Feature.RECEPTION,
    Feature.SUPPLIERS, Feature.REPORTS, Feature.EMAIL_ALERTS,
})

_PRO_EXTRA = frozenset({
    Feature.ABC_XYZ, Feature.WHATSAPP_ALERTS, Feature.AI_ANALYST,
    Feature.DOCUMENTS_RAG, Feature.EVENT_SIMULATOR, Feature.MILP_OPTIMIZER,
    Feature.SCHEDULED_REPORTS, Feature.MULTI_LOCATION,
})

_ENT_EXTRA = frozenset({Feature.BOM, Feature.API_ACCESS, Feature.WEBHOOKS, Feature.INTEGRATIONS})

PLAN_CATALOG: dict[str, PlanDef] = {
    "starter": PlanDef(
        max_skus=500, max_users=2, max_locations=1,
        max_sessions=20, max_concurrent_jobs=2, max_dataset_size_mb=200,
        features=_CORE,
    ),
    "professional": PlanDef(
        max_skus=5000, max_users=10, max_locations=5,
        max_sessions=100, max_concurrent_jobs=4, max_dataset_size_mb=500,
        features=_CORE | _PRO_EXTRA,
    ),
    "enterprise": PlanDef(
        max_skus=None, max_users=None, max_locations=None,
        max_sessions=None, max_concurrent_jobs=8, max_dataset_size_mb=2000,
        features=_CORE | _PRO_EXTRA | _ENT_EXTRA,
    ),
}
