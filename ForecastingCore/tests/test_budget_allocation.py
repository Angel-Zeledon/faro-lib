"""Purchase budget allocation (forecasting_core/business/budget_allocation.py)."""
import random

import pytest

from forecasting_core.business.budget_allocation import (
    BudgetLine, allocate_budget, line_from_mapping,
)


def L(key, qty, cost, signal="PEDIR_YA", risk=None, abc="B", moq=None, **kw):
    return BudgetLine(key=key, sku=key, quantity=qty, unit_cost=cost, signal=signal,
                      money_at_risk=risk, abc=abc, moq=moq, **kw)


def by_key(res):
    return {ln.key: ln for ln in res.lines}


def random_lines(rng, n):
    out = []
    for i in range(n):
        out.append(L(
            f"s{i}", qty=rng.randint(1, 200),
            cost=rng.choice([None, 0, rng.uniform(0.5, 90)]),
            signal=rng.choice(["PEDIR_YA", "PEDIR_PRONTO", "OK"]),
            risk=rng.choice([None, rng.uniform(0, 5000)]),
            abc=rng.choice(["A", "B", "C", None]),
            moq=rng.choice([None, 1, 5, 24, 0.5])))
    return out


def test_never_exceeds_remaining_budget():
    rng = random.Random(7)
    for _ in range(300):
        lines = random_lines(rng, rng.randint(0, 25))
        budget = rng.choice([0, rng.uniform(0, 3000), rng.uniform(0, 100000)])
        res = allocate_budget(lines, budget)
        assert res.summary["funded_cost"] <= budget + 1e-6
        assert sum(ln.funded_cost for ln in res.lines) <= budget + 0.01 * len(lines)
        assert res.summary["budget_remaining_after"] >= 0


def test_deterministic_under_any_input_order():
    rng = random.Random(11)
    for _ in range(80):
        lines = random_lines(rng, 15)
        budget = rng.uniform(100, 4000)
        base = allocate_budget(lines, budget).as_dict()
        shuffled = lines[:]
        rng.shuffle(shuffled)
        assert allocate_budget(shuffled, budget).as_dict() == base


def test_funded_quantity_respects_moq():
    rng = random.Random(3)
    for _ in range(200):
        lines = random_lines(rng, 20)
        res = allocate_budget(lines, rng.uniform(0, 3000))
        for ln in res.lines:
            if ln.status == "partial":
                assert ln.funded_qty >= (ln.moq or 0) - 1e-9
                assert 0 < ln.funded_qty < ln.recommended_qty
            if ln.status in ("deferred", "cost_unknown", "ignored"):
                assert ln.funded_qty == 0
            if ln.status == "funded":
                assert ln.funded_qty == ln.recommended_qty


def test_budget_at_least_the_need_equals_no_budget():
    rng = random.Random(5)
    for _ in range(100):
        lines = random_lines(rng, 15)
        free = allocate_budget(lines, None)
        need = free.summary["funded_cost"]
        rich = allocate_budget(lines, need + 1000)
        assert [(l.key, l.status, l.funded_qty, l.reason) for l in rich.lines] == \
               [(l.key, l.status, l.funded_qty, l.reason) for l in free.lines]
        exact = allocate_budget(lines, need + 0.01)  # summary is rounded to cents
        assert [(l.key, l.status, l.funded_qty) for l in exact.lines] == \
               [(l.key, l.status, l.funded_qty) for l in free.lines]


def test_no_budget_funds_every_costed_candidate_in_full():
    res = allocate_budget([L("a", 10, 5, risk=1), L("b", 3, 2, "PEDIR_PRONTO"),
                           L("c", 4, 2, "OK")], None)
    st = {k: v.status for k, v in by_key(res).items()}
    assert st == {"a": "funded", "b": "funded", "c": "ignored"}
    assert res.summary["capped"] is False


def test_empty_budget_defers_everything_with_a_reason():
    res = allocate_budget([L("a", 10, 5, risk=100), L("b", 3, 2, risk=40)], 0)
    for ln in res.lines:
        assert ln.status == "deferred" and ln.reason == "budget_exhausted"
        assert ln.uncovered_risk == ln.money_at_risk
    assert res.summary["money_at_risk_uncovered"] == 140.0
    assert res.summary["funded_cost"] == 0


def test_negative_or_nan_budget_is_zero():
    for bad in (-50, float("nan")):
        res = allocate_budget([L("a", 1, 5, risk=1)], bad)
        assert res.lines[0].status == "deferred"


def test_empty_candidates():
    res = allocate_budget([], 100)
    assert res.lines == [] and res.summary["funded_cost"] == 0 and res.warnings == []


def test_ya_before_pronto_even_when_pronto_is_denser():
    ya = L("ya", 10, 10, "PEDIR_YA", risk=50)          # density 0.5
    pronto = L("pr", 10, 10, "PEDIR_PRONTO", risk=5000)  # density 50
    res = allocate_budget([pronto, ya], 100)
    st = by_key(res)
    assert st["ya"].status == "funded"
    assert st["pr"].status == "deferred"


def test_orders_by_risk_density_within_a_tier():
    big = L("big", 10, 10, risk=300)      # 100 cost, density 3
    small = L("small", 5, 10, risk=250)   # 50 cost, density 5
    res = allocate_budget([big, small], 100)
    st = by_key(res)
    assert st["small"].status == "funded"
    # 50 left: big (cost 100) is funded partially (5 of 10 units = 50% >= 25%)
    assert st["big"].status == "partial" and st["big"].funded_qty == 5
    assert [l.key for l in res.lines][0] == "small"


def test_funded_density_never_below_a_deferred_one_that_fit():
    """If a higher-density line was deferred, no lower-density line of the same
    tier that is funded could have been funded instead of it: each funded line
    was visited after every denser line."""
    rng = random.Random(21)
    for _ in range(100):
        lines = [L(f"s{i}", rng.randint(1, 50), rng.uniform(1, 20), risk=rng.uniform(1, 900))
                 for i in range(12)]
        res = allocate_budget(lines, rng.uniform(50, 1500))
        order = [l for l in res.lines]
        dens = [l.risk_density for l in order]
        assert dens == sorted(dens, reverse=True)


def test_moq_larger_than_remainder_is_deferred_with_that_reason():
    # 100 units at 1.0, MOQ 60; only 40 affordable: below MOQ.
    res = allocate_budget([L("a", 100, 1.0, risk=500, moq=60)], 40)
    ln = res.lines[0]
    assert ln.status == "deferred" and ln.reason == "moq_exceeds_remainder"


def test_partial_never_below_moq_and_never_a_sliver():
    # affordable 20 units of 100: above MOQ 10 but below 25% -> useless order
    res = allocate_budget([L("a", 100, 1.0, risk=500, moq=10)], 20)
    assert res.lines[0].status == "deferred"
    assert res.lines[0].reason == "remainder_too_small"
    res = allocate_budget([L("a", 100, 1.0, risk=500, moq=10)], 30)
    assert res.lines[0].status == "partial" and res.lines[0].funded_qty == 30


def test_partial_uncovered_risk_is_proportional():
    res = allocate_budget([L("a", 100, 1.0, risk=1000)], 50)
    ln = res.lines[0]
    assert ln.status == "partial" and ln.funded_qty == 50
    assert ln.uncovered_risk == 500.0


def test_walk_continues_past_a_line_that_does_not_fit():
    big = L("big", 100, 10, risk=1000, moq=100)   # cost 1000, MOQ == qty
    cheap = L("cheap", 5, 2, risk=1)              # cost 10, tiny density
    res = allocate_budget([big, cheap], 500)
    st = by_key(res)
    assert st["big"].status == "deferred"
    assert st["cheap"].status == "funded"


def test_unknown_cost_is_never_free_and_is_warned():
    res = allocate_budget([L("a", 10, None, risk=900), L("b", 10, 0, risk=10),
                           L("c", 1, 5, risk=10)], 5)
    st = by_key(res)
    assert st["a"].status == "cost_unknown" and st["a"].reason == "no_cost"
    assert st["b"].status == "cost_unknown"
    assert st["c"].status == "funded"
    assert res.summary["funded_cost"] == 5
    assert res.summary["cost_unknown_lines"] == 2
    assert res.summary["cost_unknown_risk"] == 910.0
    assert {"code": "cost_unknown_lines", "params": {"count": 2}} in res.warnings
    # excluded from the cap maths: not counted in uncovered risk of costed lines
    assert res.summary["money_at_risk_uncovered"] == 0


def test_unknown_risk_sorts_after_valued_and_is_flagged():
    res = allocate_budget([L("norisk", 1, 1, risk=None), L("risk", 1, 1, risk=1)], 1)
    st = by_key(res)
    assert st["risk"].status == "funded" and st["norisk"].status == "deferred"
    assert res.summary["risk_unknown_lines"] == 1


def test_abc_breaks_ties():
    a = L("a_line", 1, 10, risk=100, abc="A")
    c = L("c_line", 1, 10, risk=100, abc="C")
    res = allocate_budget([c, a], 10)
    assert by_key(res)["a_line"].status == "funded"
    assert by_key(res)["c_line"].status == "deferred"


def test_fractional_moq_step():
    res = allocate_budget([L("kg", 10.0, 4.0, risk=100, moq=0.5)], 21)
    ln = res.lines[0]
    assert ln.status == "partial" and ln.funded_qty == 5.0   # 21/4 = 5.25 -> 5.0


def test_duplicate_keys_refused():
    with pytest.raises(ValueError):
        allocate_budget([L("a", 1, 1), L("a", 2, 1)], 10)


def test_line_from_mapping_reads_a_status_row():
    ln = line_from_mapping({"sku": "X", "warehouse": "Norte", "supplier": "S",
                            "recommended_qty": 12, "unit_cost": 3.5, "signal": "PEDIR_YA",
                            "money_at_risk": 99, "abc": "a", "moq": 6})
    assert (ln.sku, ln.quantity, ln.unit_cost, ln.abc, ln.moq) == ("X", 12.0, 3.5, "A", 6.0)
    assert line_from_mapping({"sku": "Y", "unit_cost": None}).unit_cost is None
