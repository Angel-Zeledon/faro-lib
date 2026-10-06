"""Generate `backend-rs/tests/fixtures/allocation_cases.json`: seeded random
allocation cases solved by the Python reference. The Rust unit test
`allocation::core::tests::matches_the_python_reference_exactly` replays every
case against the Rust core and demands identical integers.

    python tests/contract/gen_allocation_fixtures.py          # rewrite the file
    python tests/contract/gen_allocation_fixtures.py --check  # fail if stale

Only integers cross the file (micro-units and day numbers), so there is no
float round-trip to argue about.
"""

from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from allocation_reference import MICRO, allocate  # noqa: E402

SEED = 20261006
N_CASES = 2600
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend-rs", "tests",
                   "fixtures", "allocation_cases.json")


def make_case(rng: random.Random) -> dict:
    today = rng.randint(700_000, 740_000)
    n = rng.choice([1, 2, 3, 4, 5, 6, 8, 12, 20, 40])
    scale = rng.choice([1, 10, 1000, 1_000_000])  # up to 1e6 units of 1e6 micro
    claims = []
    for k in range(n):
        offset = rng.choice([-40, -3, 0, 0, 1, 5, 10, 30, 90, 365])
        claims.append({
            "id": f"c{rng.randint(0, 99):02d}-{k}",   # ids collide in prefix, never fully
            "tier": rng.choice([1, 1, 2, 3, 5, 5, 9]),
            "day": today + offset + rng.randint(0, 3),
            "units": max(1, rng.randint(1, 50) * MICRO * scale // rng.choice([1, 2, 3, 7])),
        })
    total = sum(c["units"] for c in claims)
    stock = int(total * rng.choice([0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0, 1.3]))
    if rng.random() < 0.05:
        stock = -rng.randint(1, 5 * MICRO)
    arrivals = []
    for _ in range(rng.choice([0, 0, 1, 2, 3, 6])):
        day = rng.choice([None, today - 5, today, today + rng.randint(0, 120)])
        qty = int(total * rng.choice([0.05, 0.2, 0.5])) + rng.randint(0, 999)
        if rng.random() < 0.05:
            qty = -qty
        arrivals.append({"day": day, "qty": qty})
    fair = sorted(rng.sample([1, 2, 3, 5, 9], rng.choice([0, 0, 1, 2, 5])))
    case = {"today": today, "stock": stock, "claims": claims, "arrivals": arrivals, "fair_tiers": fair}
    case["expected"] = allocate(claims, today, stock, arrivals, fair)
    return case


def build() -> str:
    rng = random.Random(SEED)
    cases = [make_case(rng) for _ in range(N_CASES)]
    return json.dumps({"seed": SEED, "cases": cases}, separators=(",", ":")) + "\n"


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        with open(OUT, encoding="utf-8", newline="") as f:
            current = f.read()
        if current != text:
            print("allocation_cases.json is stale: run tests/contract/gen_allocation_fixtures.py")
            return 1
        print("allocation_cases.json is current")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"wrote {N_CASES} cases to {os.path.normpath(OUT)} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
