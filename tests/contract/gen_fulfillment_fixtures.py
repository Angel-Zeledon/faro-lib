"""Fixtures for the differential test of the Rust fulfillment core.

    python tests/contract/gen_fulfillment_fixtures.py            # (re)write
    python tests/contract/gen_fulfillment_fixtures.py --check    # fail if stale

Writes `backend-rs/tests/fixtures/fulfillment_cases.json`: seeded random cases
and what `fulfillment_reference.py` (the plain-Python spec) answers for each.
`cargo test` (backend-rs/src/fulfillment/core.rs, `differential_against_python`)
replays every case through the Rust core and demands EXACT equality.

Floats are stored as strings (`repr`, shortest round-trip) so no JSON parser can
move a last digit; the Rust side parses them with `str::parse::<f64>`.

Pure computation, standard library only.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fulfillment_reference as ref  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "backend-rs" / "tests" / "fixtures" / "fulfillment_cases.json"
SEED = 20261006
TODAY0 = 739900  # an arbitrary day ordinal (late 2026), the cases move around it

PROBS = [1.0, 1.0, 1.0, 0.9, 0.75, 0.5, 0.3, 0.25, 0.05]


def f(x):
    return None if x is None else repr(float(x))


def _qty(rnd, hi):
    return rnd.randint(1, hi * 4) / 4.0


def sku_case(rnd: random.Random) -> dict:
    today = TODAY0 + rnd.randint(-400, 400)
    n = rnd.randint(1, 6)
    commitments = []
    for i in range(n):
        commitments.append({
            "id": f"c{i}", "delivery_day": today + rnd.randint(-10, 120),
            "quantity": _qty(rnd, 300), "probability": rnd.choice(PROBS)})
    stock = None if rnd.random() < 0.08 else (0.0 if rnd.random() < 0.1 else _qty(rnd, 600))
    lead = None if rnd.random() < 0.25 else rnd.randint(1, 60)
    arrivals = []
    for _ in range(rnd.randint(0, 5)):
        day = None if rnd.random() < 0.25 else today + rnd.randint(-3, 130)
        if day is not None and rnd.random() < 0.15:
            day = today  # a transfer in transit lands today
        arrivals.append({"day": day, "qty": 0.0 if rnd.random() < 0.05 else _qty(rnd, 300),
                         "kind": rnd.choice(["po", "po", "transfer"])})
    return {"today": today, "stock": stock, "lead": lead, "commitments": commitments,
            "arrivals": arrivals}


def boundary_case(rnd: random.Random) -> dict:
    """Cases built to sit ON a rule's edge (slack days, lead time vs delivery,
    exact cover, delivery today), where a random case almost never lands."""
    today = TODAY0 + rnd.randint(-50, 50)
    kind = rnd.choice(["slack", "lead", "exact", "today", "tie", "late_po"])
    qty = _qty(rnd, 200)
    delivery = today + rnd.randint(5, 60)
    lead = rnd.randint(1, 40)
    stock = rnd.choice([0.0, qty / 4, qty / 2])
    arrivals = []
    if kind == "slack":
        arrivals = [{"day": delivery - rnd.randint(0, 6), "qty": qty - stock, "kind": "po"}]
    elif kind == "lead":
        delivery = today + lead + rnd.choice([-1, 0, 1])
        stock = rnd.choice([0.0, qty / 2])
    elif kind == "exact":
        stock = qty
        if rnd.random() < 0.5:
            stock = qty - 1e-10  # inside the float-dust tolerance
        elif rnd.random() < 0.5:
            stock = qty - 1e-6   # outside it
    elif kind == "today":
        delivery = today + rnd.choice([-1, 0, 0, 1])
        arrivals = [{"day": today, "qty": rnd.choice([0.0, qty - stock]), "kind": "transfer"}]
    elif kind == "tie":
        cs = [{"id": f"c{i}", "delivery_day": delivery, "quantity": _qty(rnd, 50), "probability": 1.0}
              for i in range(rnd.randint(2, 4))]
        return {"today": today, "stock": _qty(rnd, 100), "lead": lead, "commitments": cs,
                "arrivals": [{"day": delivery - 3, "qty": _qty(rnd, 60), "kind": "po"}]}
    elif kind == "late_po":
        arrivals = [{"day": delivery + rnd.choice([0, 1, 5]), "qty": qty, "kind": "po"},
                    {"day": None, "qty": rnd.choice([qty / 3, qty]), "kind": "po"}]
    return {"today": today, "stock": stock, "lead": rnd.choice([lead, lead, None]),
            "commitments": [{"id": "c0", "delivery_day": delivery, "quantity": qty,
                             "probability": rnd.choice([1.0, 0.5])}],
            "arrivals": arrivals}


def encode_sku(case: dict, expected: list) -> dict:
    return {
        "today": case["today"], "stock": f(case["stock"]), "lead": case["lead"],
        "commitments": [{"id": c["id"], "delivery_day": c["delivery_day"],
                         "quantity": f(c["quantity"]), "probability": f(c["probability"])}
                        for c in case["commitments"]],
        "arrivals": [{"day": a["day"], "qty": f(a["qty"]), "kind": a["kind"]}
                     for a in case["arrivals"]],
        "expected": [{
            "id": r["id"], "verdict": r["verdict"], "reason": r["reason"],
            "units": f(r["units"]), "cumulative_units": f(r["cumulative_units"]),
            "supply_units": f(r["supply_units"]), "shortfall_units": f(r["shortfall_units"]),
            "shortfall_is_minimum": r["shortfall_is_minimum"],
            "shortfall_worst_case": f(r["shortfall_worst_case"]),
            "undated_units": f(r["undated_units"]), "cover_day": r["cover_day"],
            "cover_source": r["cover_source"], "slack_days": r["slack_days"],
            "late_days": r["late_days"], "latest_safe_order_day": r["latest_safe_order_day"],
            "order_date_passed": r["order_date_passed"],
            "driver_index": r["driver_index"]} for r in expected],
    }


def build() -> dict:
    rnd = random.Random(SEED)
    sku_cases, summary_cases = [], []
    for i in range(1500):
        case = sku_case(rnd) if i < 900 else boundary_case(rnd)
        out = ref.evaluate_sku(case["commitments"], case["stock"], case["lead"],
                               case["arrivals"], case["today"])
        sku_cases.append(encode_sku(case, out))
        if rnd.random() < 0.15:
            by_id = {c["id"]: c for c in case["commitments"]}
            rows = [{"verdict": r["verdict"], "units": r["units"],
                     "shortfall_units": r["shortfall_units"],
                     "shortfall_is_minimum": r["shortfall_is_minimum"],
                     "delivery_day": by_id[r["id"]]["delivery_day"]} for r in out]
            exp = ref.summarize(rows)
            summary_cases.append({
                "rows": [{"verdict": r["verdict"], "units": f(r["units"]),
                          "shortfall_units": f(r["shortfall_units"]),
                          "shortfall_is_minimum": r["shortfall_is_minimum"],
                          "delivery_day": r["delivery_day"]} for r in rows],
                "expected": {**exp, "units": f(exp["units"]),
                             "shortfall_units": f(exp["shortfall_units"])}})
    summary_cases.append({"rows": [], "expected": {**ref.summarize([]), "units": "0.0",
                                                   "shortfall_units": "0.0"}})

    arrival_cases = []
    for _ in range(600):
        today = TODAY0 + rnd.randint(-400, 400)
        gen = today + rnd.randint(-90, 20)
        lead = None if rnd.random() < 0.3 else rnd.randint(1, 80)
        promise = None if rnd.random() < 0.6 else today + rnd.randint(-20, 90)
        arrival_cases.append({"generated_day": gen, "lead": lead, "promise_day": promise,
                              "today": today,
                              "expected": ref.po_line_arrival(gen, lead, promise, today)})

    po_lead_cases = []
    for _ in range(400):
        n = rnd.choice([0, 1, 2, 3, 3, 4, 10])
        avg = None if n == 0 else rnd.choice([0.0, 0.4, 1.0, 6.5, 7.2, 14.0, 21.01, 30.5])
        card = None if rnd.random() < 0.3 else rnd.randint(1, 60)
        declared = rnd.random() < 0.6
        po_lead_cases.append({"avg": f(avg), "n": n, "card": card, "declared": declared,
                              "expected": ref.po_lead_days(avg, n, card, declared)})

    sku_lead_cases = []
    for _ in range(400):
        avg = None if rnd.random() < 0.4 else rnd.choice([0.0, 0.4, 0.5, 1.5, 2.5, 6.5, 7.5, 12.49, 12.5, 30.0])
        row = None if rnd.random() < 0.2 else rnd.randint(1, 90)
        rule = None if rnd.random() < 0.5 else rnd.randint(1, 90)
        declared = rnd.random() < 0.5
        r = ref.sku_lead_days(row, declared, rule, avg)
        sku_lead_cases.append({"avg": f(avg), "row": row, "declared": declared, "rule": rule,
                               "expected": None if r is None else {"days": r[0], "source": r[1]}})

    return {"seed": SEED, "at_risk_slack_days": ref.AT_RISK_SLACK_DAYS,
            "sku_cases": sku_cases, "summary_cases": summary_cases,
            "arrival_cases": arrival_cases, "po_lead_cases": po_lead_cases,
            "sku_lead_cases": sku_lead_cases}


def render() -> str:
    return json.dumps(build(), separators=(",", ":")) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is stale: run tests/contract/gen_fulfillment_fixtures.py")
            return 1
        print("fulfillment fixtures are current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
