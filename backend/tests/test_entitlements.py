import pytest
from datetime import datetime, timedelta, timezone

from backend.entitlements.plans import Feature, PLAN_CATALOG
from backend.entitlements import service as ent


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


def _tenant(plan="starter", trial_ends_at=None, quota=None):
    return {"id": "ten_x", "plan": plan, "trial_ends_at": trial_ends_at,
            "quota": quota or {}}


@pytest.mark.offline
def test_has_feature_by_plan():
    assert ent.has_feature(_tenant("professional"), Feature.WHATSAPP_ALERTS)
    assert not ent.has_feature(_tenant("starter"), Feature.WHATSAPP_ALERTS)


@pytest.mark.offline
def test_unknown_plan_falls_back_to_starter():
    assert ent.get_plan_def("garbage").max_skus == 500


@pytest.mark.offline
def test_tenant_limits_merge_override():
    limits = ent.tenant_limits(_tenant("starter", quota={"max_skus": 999}))
    assert limits["max_skus"] == 999          # override wins
    assert limits["max_users"] == 2           # catalog default preserved


@pytest.mark.offline
def test_trial_state_and_read_only():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert ent.trial_state(_tenant(trial_ends_at=None)) == "active"
    assert ent.trial_state(_tenant(trial_ends_at=future)) == "trialing"
    assert ent.trial_state(_tenant(trial_ends_at=past)) == "expired"
    assert ent.is_read_only(_tenant(trial_ends_at=past)) is True
    assert ent.is_read_only(_tenant(trial_ends_at=future)) is False


@pytest.mark.offline
def test_required_plans_for():
    assert ent.required_plans_for(Feature.WHATSAPP_ALERTS) == ["professional", "enterprise"]
    assert ent.required_plans_for(Feature.API_ACCESS) == ["enterprise"]
