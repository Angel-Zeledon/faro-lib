"""The fulfillment outlook must not become a second authority on risk.

`tests/contract/fulfillment_reference.py` is the plain-Python spec the Rust core
(`backend-rs/src/fulfillment/core.rs`) is held to. These tests pin that spec to
the authority that already exists, `committed_demand_service.allocate_risk`,
and keep the differential fixtures from going stale. No database, no conftest.
"""
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

from backend.inventory import committed_demand_service as svc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "contract"))
import fulfillment_reference as ref  # noqa: E402


def _case(rnd: random.Random):
    today = 739900 + rnd.randint(-200, 200)
    commitments = [
        {"id": f"c{i}", "delivery_day": today + rnd.randint(-10, 90),
         "quantity": rnd.randint(1, 400) / 4.0, "probability": rnd.choice([1.0, 0.5, 0.25])}
        for i in range(rnd.randint(1, 6))]
    stock = rnd.randint(0, 800) / 4.0
    arrivals = [{"day": today + rnd.randint(-2, 100), "qty": rnd.randint(1, 300) / 4.0,
                 "kind": rnd.choice(["po", "transfer"])} for _ in range(rnd.randint(0, 4))]
    return today, commitments, stock, rnd.choice([None, rnd.randint(1, 45)]), arrivals


def test_shortfall_equals_allocate_risk_when_every_arrival_is_dated():
    """Same units (quantity x probability), same earliest-first allocation, same
    supply at the delivery date: the shortfall must be the number the
    committed-demand list already shows, for every dated case."""
    rnd = random.Random(11)
    checked = 0
    for _ in range(1500):
        today, commitments, stock, lead, arrivals = _case(rnd)
        mine = {r["id"]: r for r in ref.evaluate_sku(commitments, stock, lead, arrivals, today)}
        theirs = svc.allocate_risk(
            [{"id": c["id"], "delivery_date": date.fromordinal(c["delivery_day"]),
              "quantity": c["quantity"], "probability": c["probability"]} for c in commitments],
            stock, [(date.fromordinal(a["day"]), a["qty"]) for a in arrivals],
            lead or 0, date.fromordinal(today))
        for cid, v in theirs.items():
            assert round(mine[cid]["shortfall_units"], 2) == v["shortfall"], (cid, mine[cid], v)
            # Their at_risk is "any shortfall"; the outlook may add a tight cover
            # (at_risk with zero shortfall) but never contradicts a real shortfall.
            if v["at_risk"]:
                assert mine[cid]["verdict"] in (ref.AT_RISK, ref.WILL_MISS, ref.INSUFFICIENT)
            else:
                assert mine[cid]["verdict"] in (ref.ON_TRACK, ref.AT_RISK)
            checked += 1
    assert checked > 3000


def test_missing_stock_is_insufficient_data_in_both():
    out = ref.evaluate_sku([{"id": "a", "delivery_day": 10, "quantity": 5.0, "probability": 1.0}],
                           None, 10, [], 1)
    assert out[0]["verdict"] == ref.INSUFFICIENT and out[0]["shortfall_units"] is None
    theirs = svc.allocate_risk(
        [{"id": "a", "delivery_date": date.fromordinal(10), "quantity": 5.0, "probability": 1.0}],
        None, [], 10, date.fromordinal(1))
    assert theirs["a"]["at_risk"] is None


def test_the_differential_fixtures_are_current():
    """A rule changed in the reference without regenerating would leave the Rust
    test replaying yesterday's spec."""
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "contract" / "gen_fulfillment_fixtures.py"),
                        "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
