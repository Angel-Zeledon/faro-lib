"""Fixtures for the differential test of the Rust recurring-delivery dates.

    backend/.venv/Scripts/python.exe tests/contract/gen_recurring_fixtures.py OUT_DIR

writes OUT_DIR/recurring_fixtures.jsonl, then:

    RECURRING_FIXTURES=OUT_DIR/recurring_fixtures.jsonl \
        cargo test --manifest-path backend-rs/Cargo.toml -- --ignored recurring_dates_match_python

One line per (spec, window): what the Python reference
(`backend/inventory/recurring_delivery_dates.py`) answers, as
[nominal, delivery] ISO pairs. Pure computation: no database, no backend.
"""
import json
import os
import random
import sys
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from backend.inventory.recurring_delivery_dates import occurrences  # noqa: E402

FREQS = ["weekly", "fortnightly", "semimonthly", "monthly"]
RULES = ["skip", "before", "after"]


def iso(d: date) -> str:
    return d.isoformat()


def build(rng: random.Random) -> dict:
    freq = rng.choice(FREQS)
    start = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 1500))
    end = start + timedelta(days=rng.choice([0, 1, 6, 13, 30, 59, 120, 400, 1200]))
    holidays = sorted({start + timedelta(days=rng.randrange(-5, (end - start).days + 40))
                       for _ in range(rng.choice([0, 0, 1, 3, 10, 40]))})
    spec = {
        "frequency": freq,
        "weekday": rng.randrange(7) if freq in ("weekly", "fortnightly") else None,
        "day_of_month": (rng.choice([1, 15, 28, 29, 30, 31, rng.randrange(1, 32)])
                         if freq == "monthly" else None),
        "start_date": start, "end_date": end,
        "holiday_dates": holidays,
        "avoid_weekends": rng.random() < 0.4,
        "shift_rule": rng.choice(RULES),
    }
    lo = start + timedelta(days=rng.randrange(-60, max(1, (end - start).days + 60)))
    hi = lo + timedelta(days=rng.choice([0, 3, 30, 180, 730]))
    return {"spec": spec, "lo": lo, "hi": hi}


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(20261006)
    n = 0
    with open(os.path.join(out_dir, "recurring_fixtures.jsonl"), "w", encoding="utf-8") as f:
        for _ in range(4000):
            c = build(rng)
            res = occurrences(c["spec"], c["lo"], c["hi"])
            row = {
                "spec": {**c["spec"], "start_date": iso(c["spec"]["start_date"]),
                         "end_date": iso(c["spec"]["end_date"]),
                         "holiday_dates": [iso(h) for h in c["spec"]["holiday_dates"]]},
                "lo": iso(c["lo"]), "hi": iso(c["hi"]),
                "result": [[iso(a), iso(b)] for a, b in res],
            }
            f.write(json.dumps(row) + "\n")
            n += 1
    print(f"{n} fixtures written")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "recurring_fixtures_out")
