"""Fixtures for the differential test of the Rust money-at-risk core.

    python tests/contract/gen_money_fixtures.py            # (re)write
    python tests/contract/gen_money_fixtures.py --check    # fail if stale

Writes `backend-rs/tests/fixtures/money_cases.json`: seeded random cases and what
`money_reference.py` (exact `decimal` arithmetic, the spec) answers for each.
`cargo test` (backend-rs/src/fulfillment/money.rs, `differential_against_python`)
replays every case through the Rust core and demands EXACT equality.

Floats are stored as strings (`repr`, shortest round-trip) so no JSON parser can
move a last digit. Pure computation, standard library only.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import money_reference as ref  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "backend-rs" / "tests" / "fixtures" / "money_cases.json"
SEED = 20261007

VERDICTS = ["at_risk", "will_miss", "at_risk", "will_miss", "on_track", "insufficient_data"]
# Values that sit on or next to a rounding edge, plus the ones that must be refused.
EDGE_UNITS = [0.0, 0.004, 0.005, 0.015, 0.125, 0.375, 0.995, 1.005, 2.675, 1e12, 1e12 + 1.0, 1e15,
              float("nan"), float("inf"), -1.0, -0.0]
EDGE_PRICES = [0.0, -2.5, 0.00004, 0.00005, 0.00015, 0.00025, 0.5, 1.00005, 99.99995, 1e9, 1e9 + 1.0,
               float("nan"), float("inf"), 12.34, 1234567.8901]


def f(x):
    return None if x is None else repr(float(x))


def _units(rnd):
    r = rnd.random()
    if r < 0.08:
        return None
    if r < 0.2:
        return rnd.choice(EDGE_UNITS)
    if r < 0.5:
        return rnd.randint(0, 4000) / 4.0
    if r < 0.8:
        return rnd.uniform(0, 5000)
    return rnd.randint(1, 10 ** 7) / 1000.0


def _price(rnd):
    r = rnd.random()
    if r < 0.12:
        return None
    if r < 0.28:
        return rnd.choice(EDGE_PRICES)
    if r < 0.55:
        return rnd.randint(1, 100000) / 100.0
    if r < 0.85:
        return rnd.uniform(0.0001, 3000)
    return rnd.randint(1, 10 ** 8) / 10000.0


def _case(rnd):
    verdict = rnd.choice(VERDICTS)
    units, minimum = _units(rnd), rnd.random() < 0.3
    price = _price(rnd)
    cost = _price(rnd)
    if price is not None and cost is not None and rnd.random() < 0.7:
        cost = price * rnd.uniform(0.3, 1.3)  # mostly a believable cost, sometimes above the price
    return {"verdict": verdict, "shortfall": f(units), "minimum": minimum,
            "price": f(price), "cost": f(cost)}


def _expected(c):
    def p(s):
        return None if s is None else float(s)
    m = ref.money(c["verdict"], p(c["shortfall"]), c["minimum"], p(c["price"]), p(c["cost"]))
    return {"status": m["status"], "margin_status": m["margin_status"], "is_minimum": m["is_minimum"],
            "amount": ref.fmt_cents(m["amount_cents"]), "margin": ref.fmt_cents(m["margin_cents"])}, m


def build() -> dict:
    rnd = random.Random(SEED)
    cases = []
    for _ in range(6000):
        c = _case(rnd)
        c["expected"], _ = _expected(c)
        cases.append(c)

    rollups = []
    for _ in range(1200):
        rows = [_case(rnd) for _ in range(rnd.randint(0, 9))]
        ms = [_expected(r)[1] for r in rows]
        t = ref.rollup(ms)
        rollups.append({"rows": rows, "expected": {
            "eligible": t["eligible"], "computed": t["computed"],
            "excluded_no_price": t["excluded_no_price"],
            "excluded_no_shortfall": t["excluded_no_shortfall"],
            "amount": ref.fmt_cents(t["amount_cents"]), "has_minimum": t["has_minimum"],
            "margin_rows": t["margin_rows"], "margin": ref.fmt_cents(t["margin_cents"]),
            "margin_excluded": t["margin_excluded"]}})
    return {"seed": SEED, "units_places": ref.UNITS_PLACES, "price_places": ref.PRICE_PLACES,
            "cases": cases, "rollups": rollups}


def render() -> str:
    return json.dumps(build(), separators=(",", ":")) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is stale: run tests/contract/gen_money_fixtures.py")
            return 1
        print("money fixtures are current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
