"""Purchase budget arithmetic that needs no database (purchase_budget_math.py)
and the term validation of the service."""
from datetime import date

import pytest

from backend.errors import AppError
from backend.inventory import purchase_budget_math as bm
from backend.inventory import purchase_budget_service as svc


def test_month_bounds_including_leap_february():
    assert bm.period_bounds("month", date(2028, 2, 14)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert bm.period_bounds("month", date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_quarter_bounds():
    assert bm.period_bounds("quarter", date(2026, 5, 20)) == (date(2026, 4, 1), date(2026, 6, 30))
    assert bm.period_bounds("quarter", date(2026, 1, 1)) == (date(2026, 1, 1), date(2026, 3, 31))
    assert bm.period_bounds("quarter", date(2026, 12, 31)) == (date(2026, 10, 1), date(2026, 12, 31))


def test_custom_bounds_validated():
    assert bm.period_bounds("custom", date(2026, 1, 5), date(2026, 3, 9)) == (date(2026, 1, 5), date(2026, 3, 9))
    with pytest.raises(ValueError):
        bm.period_bounds("custom", date(2026, 3, 9), date(2026, 1, 5))
    with pytest.raises(ValueError):
        bm.period_bounds("custom", date(2026, 3, 9))
    with pytest.raises(ValueError):
        bm.period_bounds("week", date(2026, 3, 9))


def test_elapsed_states():
    s, e = date(2026, 10, 1), date(2026, 10, 30)
    assert bm.elapsed(s, e, date(2026, 9, 30))["state"] == "upcoming"
    assert bm.elapsed(s, e, date(2026, 9, 30))["elapsed_days"] == 0
    mid = bm.elapsed(s, e, date(2026, 10, 15))
    assert mid["state"] == "running" and mid["elapsed_days"] == 15 and mid["elapsed_fraction"] == 0.5
    done = bm.elapsed(s, e, date(2026, 11, 2))
    assert done["state"] == "closed" and done["elapsed_fraction"] == 1.0 and done["days_left"] == 0


def test_burn_projects_straight_line_and_flags_overrun():
    s, e = date(2026, 10, 1), date(2026, 10, 30)
    b = bm.burn(1000, 600, s, e, date(2026, 10, 15))  # 600 in half the month
    assert b["projected_total"] == 1200.0 and b["projected_overrun"] == 200.0
    assert b["pace"] == bm.PACE_AHEAD and b["projection_reliable"]
    ok = bm.burn(1000, 400, s, e, date(2026, 10, 15))
    assert ok["projected_overrun"] == 0.0 and ok["pace"] == bm.PACE_ON_TRACK
    over = bm.burn(1000, 1200, s, e, date(2026, 10, 15))
    assert over["pace"] == bm.PACE_OVER


def test_burn_makes_no_projection_too_early_or_before_the_period():
    s, e = date(2026, 10, 1), date(2026, 10, 30)
    early = bm.burn(1000, 900, s, e, date(2026, 10, 2))
    assert early["projected_overrun"] is None and early["projection_reliable"] is False
    before = bm.burn(1000, 0, s, e, date(2026, 9, 1))
    assert before["projected_overrun"] is None


def test_closed_period_projects_what_was_spent():
    s, e = date(2026, 9, 1), date(2026, 9, 30)
    b = bm.burn(1000, 1300, s, e, date(2026, 10, 5))
    assert b["projected_total"] == 1300.0 and b["projected_overrun"] == 300.0


def test_zero_amount_has_no_fraction():
    b = bm.burn(0, 0, date(2026, 10, 1), date(2026, 10, 30), date(2026, 10, 15))
    assert b["used_fraction"] is None


def test_remaining_may_go_negative_and_effective_floors_it():
    assert bm.remaining(1000, 700, 400) == -100.0
    assert bm.effective_remaining(-100.0, None) == (0.0, "self")
    assert bm.effective_remaining(500.0, 300.0) == (300.0, "parent")
    assert bm.effective_remaining(200.0, 300.0) == (200.0, "self")


ROWS = [
    {"warehouse": "Norte", "supplier": "Acme", "supplier_id": "s1", "category": "Tools",
     "value": 100.0, "open": False},
    {"warehouse": "Sur", "supplier": "Acme", "supplier_id": "s1", "category": "Paint",
     "value": 50.0, "open": True},
    {"warehouse": "norte", "supplier": "Beta", "supplier_id": "s2", "category": "tools",
     "value": None, "open": True},
]


def test_ordered_value_splits_spent_committed_and_counts_unknown_cost():
    allv = bm.ordered_value(ROWS, bm.scope_matcher("company", {}))
    assert allv == {"spent": 100.0, "committed": 50.0, "unknown_cost_lines": 1, "lines": 3}


def test_scope_matchers():
    wh = bm.ordered_value(ROWS, bm.scope_matcher("warehouse", {"warehouse": "Norte"}))
    assert wh["lines"] == 2 and wh["spent"] == 100.0 and wh["unknown_cost_lines"] == 1
    sup = bm.ordered_value(ROWS, bm.scope_matcher("supplier", {"supplier_id": "s1", "supplier": "Acme"}))
    assert sup["spent"] == 100.0 and sup["committed"] == 50.0
    cat = bm.ordered_value(ROWS, bm.scope_matcher("category", {"category": "TOOLS"}))
    assert cat["lines"] == 2
    none = bm.ordered_value(ROWS, bm.scope_matcher("warehouse", {"warehouse": None}))
    assert none["lines"] == 0  # an unresolvable scope matches nothing, never everything


def test_order_check():
    assert bm.order_check(120, 100)["over_by"] == 20.0
    assert bm.order_check(120, 100)["exceeds"] is True
    assert bm.order_check(100, 100)["exceeds"] is False
    assert bm.order_check(50, -30)["remaining"] == 0.0 and bm.order_check(50, -30)["over_by"] == 50.0


# ── Term validation ──────────────────────────────────────────────────────────

BASE = {"period_type": "month", "period_start": "2026-10-17", "amount": 5000, "currency": "usd"}


def test_clean_terms_normalises_a_month():
    t = svc.clean_terms(BASE)
    assert t["period_start"] == date(2026, 10, 1) and t["period_end"] == date(2026, 10, 31)
    assert t["currency"] == "USD" and t["scope_type"] == "company" and t["scope_value"] is None
    assert t["hard_cap"] is False and t["active"] is True


@pytest.mark.parametrize("patch", [
    {"amount": -1}, {"amount": "abc"}, {"amount": float("nan")}, {"amount": 1e13},
    {"period_type": "week"}, {"period_start": "not-a-date"}, {"currency": ""},
    {"scope_type": "region"}, {"scope_type": "warehouse"},
    {"period_type": "custom"},
    {"period_type": "custom", "period_end": "2026-09-01"},
])
def test_clean_terms_rejects(patch):
    with pytest.raises(AppError) as err:
        svc.clean_terms({**BASE, **patch})
    assert err.value.code == "purchase_budget_invalid" and err.value.status_code == 422


def test_company_scope_drops_a_stray_value_and_revision_merges_onto_current():
    t = svc.clean_terms({**BASE, "scope_value": "x"})
    assert t["scope_value"] is None
    cur = svc.clean_terms({**BASE, "scope_type": "supplier", "scope_value": "s1", "hard_cap": True})
    merged = svc.clean_terms({"amount": 9000}, current=cur)
    assert merged["amount"] == 9000.0 and merged["hard_cap"] is True
    assert merged["scope_type"] == "supplier" and merged["scope_value"] == "s1"


def test_visibility_for_scoped_users():
    wh = {"scope_type": "warehouse", "scope_value": "w1"}
    assert svc.visible(frozenset({"w1"}), wh)
    assert not svc.visible(frozenset({"w2"}), wh)
    assert not svc.visible(frozenset({"w1"}), {"scope_type": "company", "scope_value": None})
    assert not svc.visible(frozenset({"w1"}), {"scope_type": "supplier", "scope_value": "w1"})
    assert svc.visible(None, {"scope_type": "company", "scope_value": None})
    with pytest.raises(AppError) as err:
        svc.require_writable(frozenset(), wh)
    assert err.value.status_code == 403


def test_ledger_is_in_the_tenant_export_and_erase_lists():
    from backend.tenants import data_export
    assert ("purchase_budgets", "purchase_budgets", "*") in data_export._EXPORT_SPECS
    assert "purchase_budgets" in data_export._DELETE_ORDER


def test_internal_tag_and_audit_catalogue_cover_the_feature():
    from backend.activity.events import EVENTS
    from backend.api.public_surface import INTERNAL_TAGS
    from backend.audit.catalog import LEGACY
    assert "purchase-budgets" in INTERNAL_TAGS
    for action in ("created", "revised", "exceeded", "override"):
        assert f"purchase_budget.{action}" in EVENTS and f"purchase_budget.{action}" in LEGACY
