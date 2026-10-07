"""Plan data for the paid-only features (API, MCP, WhatsApp bot) and the
corporate tier. Pure: no database, no fixtures — it runs with --noconftest.
"""

from dataclasses import fields

from backend.entitlements.plans import (
    CORPORATE, DEMO, FEATURE_FIELDS, FEATURE_REQUIRED_PLAN, FREE, PAID, PLANS, PlanDef,
)
from backend.entitlements.service import (
    feature_locked_error, tenant_features, tenant_limits, tenant_tier,
)

_NUMERIC = [f.name for f in fields(PlanDef) if f.name not in FEATURE_FIELDS.values()]


def test_every_tier_defines_every_field():
    assert set(PLANS) == {FREE, PAID, CORPORATE, DEMO}
    for tier, plan in PLANS.items():
        for name in _NUMERIC:
            assert hasattr(plan, name), (tier, name)
        for field in FEATURE_FIELDS.values():
            assert isinstance(getattr(plan, field), bool), (tier, field)


def test_features_by_tier():
    expected = {
        FREE: {"api": False, "mcp": False, "whatsapp_bot": False},
        # The demo has the API and MCP (small ceilings) but never the bot.
        DEMO: {"api": True, "mcp": True, "whatsapp_bot": False},
        PAID: {"api": True, "mcp": True, "whatsapp_bot": True},
        CORPORATE: {"api": True, "mcp": True, "whatsapp_bot": True},
    }
    for tier, want in expected.items():
        assert tenant_features({"tier": tier}) == want, tier


def test_free_has_no_api_ceilings_to_spend_and_demo_has_small_ones():
    assert PLANS[FREE].max_api_keys == 0
    assert PLANS[FREE].max_api_calls_per_day == 0
    assert PLANS[DEMO].max_api_keys == 1
    assert PLANS[DEMO].max_api_calls_per_day == 200


def test_corporate_lifts_every_commercial_ceiling_but_not_infrastructure():
    c = PLANS[CORPORATE]
    for name in ("max_skus", "max_users", "max_locations", "max_sessions",
                 "max_api_keys", "max_api_calls_per_day", "max_trainings_per_day"):
        assert getattr(c, name) is None, name
    assert c.max_concurrent_jobs == PLANS[PAID].max_concurrent_jobs
    assert c.max_dataset_size_mb == 2000


def test_paid_is_limited_and_above_free():
    paid, free = PLANS[PAID], PLANS[FREE]
    for name in ("max_skus", "max_users", "max_locations", "max_sessions",
                 "max_api_keys", "max_api_calls_per_day", "max_dataset_size_mb",
                 "max_trainings_per_day"):
        assert getattr(paid, name) is not None, name
        assert getattr(paid, name) > getattr(free, name), name


def test_limits_map_does_not_carry_the_feature_booleans():
    limits = tenant_limits({"tier": PAID})
    assert set(limits) == set(_NUMERIC)
    for field in FEATURE_FIELDS.values():
        assert field not in limits


def test_unknown_tier_is_free_and_has_no_features():
    assert tenant_tier({"tier": "enterprise"}) == FREE
    assert tenant_features({"tier": "enterprise"}) == {
        "api": False, "mcp": False, "whatsapp_bot": False}
    assert tenant_features({}) == {"api": False, "mcp": False, "whatsapp_bot": False}


def test_quota_override_can_grant_or_revoke_one_feature():
    granted = tenant_features({"tier": FREE, "quota": {"api_access": True}})
    assert granted == {"api": True, "mcp": False, "whatsapp_bot": False}
    revoked = tenant_features({"tier": PAID, "quota": {"whatsapp_bot": False}})
    assert revoked == {"api": True, "mcp": True, "whatsapp_bot": False}


def test_locked_error_is_the_structured_403():
    err = feature_locked_error("mcp", "Old keys stop working.")
    assert err.code == "plan_feature_locked"
    assert err.status_code == 403
    assert err.params == {"feature": "mcp", "required_plan": FEATURE_REQUIRED_PLAN}
    assert FEATURE_REQUIRED_PLAN == PAID
    assert "Old keys stop working." in err.message


def test_full_plan_numbers_of_2026_10_05():
    """Owner decision: the Full plan fits 1,000 products, 5 users and 3
    warehouses. A drift here is a price-list change, so it must be deliberate."""
    paid = PLANS[PAID]
    assert (paid.max_skus, paid.max_users, paid.max_locations) == (1000, 5, 3)


def test_training_ceiling_per_day_by_tier():
    assert PLANS[FREE].max_trainings_per_day == 1
    assert PLANS[DEMO].max_trainings_per_day == 1
    assert PLANS[PAID].max_trainings_per_day == 10
    assert PLANS[CORPORATE].max_trainings_per_day is None
    assert tenant_limits({"tier": PAID})["max_trainings_per_day"] == 10
    # A per-tenant agreement wins, in both directions, like every other ceiling.
    assert tenant_limits(
        {"tier": FREE, "quota": {"max_trainings_per_day": 3}})["max_trainings_per_day"] == 3


def test_the_training_ceiling_has_a_catalogue_key():
    from backend.error_codes import all_bridge_codes
    assert "plan_limit_max_trainings_per_day" in all_bridge_codes()
