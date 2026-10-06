"""The money-at-risk arithmetic: the plain-Python spec is held to the facts it
claims, and the differential fixtures the Rust core replays must not go stale.
No database, no conftest.
"""
import random
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "contract"))
import money_reference as ref  # noqa: E402


def test_amount_is_exact_decimal_arithmetic_not_float():
    # 0.1 + 0.2 style traps: 3 units at 0.1 must be exactly 0.30
    m = ref.money("will_miss", 3.0, False, 0.1, None)
    assert ref.fmt_cents(m["amount_cents"]) == "0.30"
    # 1.005 is stored below 1.005, so a float round(x, 2) and exact decimals differ
    assert ref.scaled(1.005, 2, 1e12) == 100
    assert ref.scaled(2.675, 2, 1e12) == 267


def test_missing_price_is_never_a_zero_in_the_totals():
    rows = [ref.money("will_miss", 10.0, False, None, 1.0),
            ref.money("will_miss", 10.0, False, 0.0, 1.0),
            ref.money("at_risk", 10.0, False, 2.0, None)]
    t = ref.rollup(rows)
    assert t["excluded_no_price"] == 2 and t["computed"] == 1
    assert t["amount_cents"] == 2000 and t["margin_rows"] == 0 and t["margin_excluded"] == 3


def test_on_track_and_insufficient_rows_never_enter_a_total():
    rows = [ref.money(v, 10.0, False, 2.0, 1.0) for v in ("on_track", "insufficient_data")]
    t = ref.rollup(rows)
    assert t["eligible"] == 0 and t["amount_cents"] == 0


def test_the_reference_agrees_with_an_independent_decimal_formula():
    rnd = random.Random(5)
    for _ in range(2000):
        units = rnd.randint(1, 100000) / 100.0
        price = rnd.randint(1, 10 ** 6) / 10000.0
        m = ref.money("will_miss", units, False, price, None)
        exact = (Decimal(str(units)) * Decimal(str(price)))
        want = int((exact * 100).to_integral_value(rounding="ROUND_HALF_UP"))
        assert m["amount_cents"] == want


def test_the_differential_fixtures_are_current():
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "contract" / "gen_money_fixtures.py"),
                        "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
