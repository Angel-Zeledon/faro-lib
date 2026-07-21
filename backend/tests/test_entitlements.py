import pytest

from backend.entitlements.plans import Feature, PLAN_CATALOG


@pytest.mark.offline
def test_catalog_has_three_plans():
    assert set(PLAN_CATALOG) == {"starter", "professional", "enterprise"}


@pytest.mark.offline
def test_each_tier_is_a_superset_of_the_lower():
    starter = PLAN_CATALOG["starter"].features
    pro = PLAN_CATALOG["professional"].features
    ent = PLAN_CATALOG["enterprise"].features
    assert starter <= pro <= ent


@pytest.mark.offline
def test_starter_excludes_paid_features_but_includes_core():
    starter = PLAN_CATALOG["starter"].features
    assert Feature.SEMAPHORE in starter
    assert Feature.EMAIL_ALERTS in starter
    assert Feature.WHATSAPP_ALERTS not in starter
    assert Feature.API_ACCESS not in starter


@pytest.mark.offline
def test_enterprise_only_features():
    pro = PLAN_CATALOG["professional"].features
    assert Feature.API_ACCESS not in pro
    assert Feature.BOM not in pro
    assert Feature.WEBHOOKS not in pro
    ent = PLAN_CATALOG["enterprise"].features
    assert {Feature.API_ACCESS, Feature.BOM, Feature.WEBHOOKS} <= ent


@pytest.mark.offline
def test_numeric_limits():
    assert PLAN_CATALOG["starter"].max_skus == 500
    assert PLAN_CATALOG["professional"].max_skus == 5000
    assert PLAN_CATALOG["enterprise"].max_skus is None
    assert PLAN_CATALOG["starter"].max_users == 2
    assert PLAN_CATALOG["starter"].max_locations == 1
