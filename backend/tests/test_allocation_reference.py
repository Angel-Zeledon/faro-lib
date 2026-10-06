"""The Python side of the allocation differential test.

`backend-rs/src/allocation/core.rs` is checked against an independent Python
reference (`tests/contract/allocation_reference.py`) over thousands of seeded
cases: the reference solves them, `backend-rs/tests/fixtures/allocation_cases.json`
holds the answers, and the Rust unit test demands exactly the same integers.

What guards that arrangement from this side:

* the committed fixture is what the generator produces today (a stale fixture
  would let the Rust test pass against answers nobody can reproduce);
* the reference itself obeys the properties the feature promises, on cases
  that are NOT in the fixture (another seed): never more than a claim wants,
  never more than the supply on any day, higher priority never loses units to
  a lower one it could have served instead of, and fair-share is order-free.
"""

import importlib.util
import json
import pathlib
import random

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "tests" / "contract"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, CONTRACT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ref = _load("allocation_reference")
gen = _load("gen_allocation_fixtures")


def test_the_committed_fixture_is_what_the_generator_produces():
    current = pathlib.Path(gen.OUT).read_bytes().decode("utf-8")
    assert current.replace("\r\n", "\n") == gen.build()


def test_the_fixture_is_large_and_actually_contested():
    doc = json.loads(pathlib.Path(gen.OUT).read_text(encoding="utf-8"))
    cases = doc["cases"]
    assert len(cases) >= 2000
    short = sum(1 for c in cases if any(a < cl["units"] for a, cl in zip(c["expected"], c["claims"])))
    fair = sum(1 for c in cases if c["fair_tiers"])
    assert short > len(cases) // 4 and fair > len(cases) // 4


def _supply(case, t):
    today = case["today"]
    total = max(0, case["stock"])
    for a in case["arrivals"]:
        if a["qty"] > 0 and (today if a["day"] is None else max(a["day"], today)) <= t:
            total += a["qty"]
    return total


@pytest.mark.parametrize("seed", range(12))
def test_reference_properties_on_fresh_cases(seed):
    rng = random.Random(9_000 + seed)
    for _ in range(120):
        case = gen.make_case(rng)
        got = ref.allocate(case["claims"], case["today"], case["stock"], case["arrivals"], case["fair_tiers"])
        assert got == case["expected"]
        eff = [max(c["day"], case["today"]) for c in case["claims"]]
        for g, c in zip(got, case["claims"]):
            assert 0 <= g <= c["units"]
        # No day is over-committed: units owed by day t never exceed the supply by t.
        for t in sorted(set(eff)):
            assert sum(g for g, e in zip(got, eff) if e <= t) <= _supply(case, t)


def test_fair_share_does_not_depend_on_the_order_of_the_claims():
    rng = random.Random(77)
    for _ in range(300):
        case = gen.make_case(rng)
        fair = case["fair_tiers"]
        if not fair:
            continue
        claims = case["claims"]
        shuffled = list(range(len(claims)))
        rng.shuffle(shuffled)
        a = ref.allocate(claims, case["today"], case["stock"], case["arrivals"], fair)
        b = ref.allocate([claims[i] for i in shuffled], case["today"], case["stock"], case["arrivals"], fair)
        by_id_a = {c["id"]: x for c, x in zip(claims, a)}
        by_id_b = {claims[i]["id"]: x for i, x in zip(shuffled, b)}
        for c in claims:
            if c["tier"] in fair:
                assert by_id_a[c["id"]] == by_id_b[c["id"]]


def test_a_higher_priority_is_served_before_a_lower_one():
    # 10 units, two customers wanting 8 each on the same day: tier 1 gets 8, tier 2 gets 2.
    M = ref.MICRO
    claims = [
        {"id": "low", "tier": 2, "day": 100, "units": 8 * M},
        {"id": "high", "tier": 1, "day": 100, "units": 8 * M},
    ]
    assert ref.allocate(claims, 50, 10 * M, [], []) == [2 * M, 8 * M]
    # Fair share inside one tier: 5 and 5.
    claims = [{"id": "a", "tier": 1, "day": 100, "units": 8 * M}, {"id": "b", "tier": 1, "day": 100, "units": 8 * M}]
    assert ref.allocate(claims, 50, 10 * M, [], [1]) == [5 * M, 5 * M]
    # An arrival after a claim's date never serves that claim.
    claims = [{"id": "x", "tier": 1, "day": 60, "units": 8 * M}]
    assert ref.allocate(claims, 50, 5 * M, [{"day": 70, "qty": 20 * M}], []) == [5 * M]
