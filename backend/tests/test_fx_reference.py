"""The conversion rules (backend/fx/reference.py), checked three ways:

* by hand on the cases where float arithmetic or early rounding gets it wrong;
* against an independent oracle (`fractions.Fraction`, which shares no code with
  the module under test) over seeded cases;
* the committed golden vectors the Rust core replays are exactly what the
  reference produces today, so changing a rule without regenerating them is red.
"""
from __future__ import annotations

import json
import random
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from backend.fx import reference as ref
from backend.fx import vectors

VECTORS = Path(__file__).resolve().parents[2] / "backend-rs" / "tests" / "fx_vectors.json"


def test_a_float_is_the_decimal_it_spells_not_its_binary_value():
    assert ref.to_decimal(0.1) == Decimal("0.1")
    assert ref.convert(0.1, 3) == Decimal("0.30")          # 0.1*3 in floats is 0.30000000000000004
    assert ref.money_text(ref.convert(2.675, 1)) == "2.68"  # round(2.675, 2) in floats is 2.67


def test_one_rounding_half_up_at_the_end_only():
    # 3 x 0.335 x 520.5 = 523.1025 -> 523.10. Rounding the unit value first
    # (174.3675 -> 174.37) and then multiplying gives 523.11: that is the bug.
    assert ref.money_text(ref.convert_line(3, "0.335", "520.5")) == "523.10"
    early = ref.convert("0.335", "520.5") * 3
    assert ref.money_text(early) == "523.11"
    for text, want in [("0.005", "0.01"), ("0.004999", "0.00"), ("9.995", "10.00"), ("2.665", "2.67")]:
        assert ref.money_text(ref.quantize_money(Decimal(text))) == want


def test_totals_are_exact_sums_of_two_decimal_values():
    parts = [ref.quantize_money(Decimal(x)) for x in ("0.10", "0.20", "0.30")]
    assert ref.money_text(ref.total(parts)) == "0.60"
    assert 0.1 + 0.2 != 0.3  # the float sum is the thing being avoided
    assert ref.money_text(ref.total([ref.quantize_money(Decimal("0.1")),
                                     ref.quantize_money(Decimal("0.2"))])) == "0.30"


@pytest.mark.parametrize("bad", ["", ".", "-1", "1_000", "NaN", "Infinity", "1e", "abc", "1e31",
                                 "٣", float("nan"), float("inf"), -1.0, True, None, [1]])
def test_inputs_the_rules_refuse(bad):
    with pytest.raises(ref.FxError):
        ref.to_decimal(bad)


def test_negative_zero_never_prints_a_minus_sign():
    assert ref.money_text(ref.convert(-0.0, 5)) == "0.00"


def test_a_rate_is_validated_never_rounded():
    assert ref.parse_rate("520.5") == Decimal("520.5")
    assert ref.parse_rate("1.0000000000") == Decimal(1)
    for bad in ("0", "0.00000000001", "1000000000.0000000001", "1.00000000001"):
        with pytest.raises(ref.FxError):
            ref.parse_rate(bad)


def _rows(*triples):
    return [{"id": i, "currency": c, "base_currency": b, "effective_date": date.fromisoformat(d)}
            for i, c, b, d in triples]


def test_the_rate_in_force_is_the_latest_not_after_the_date_and_never_a_substitute():
    rows = _rows(("a", "USD", "CRC", "2026-01-01"), ("b", "USD", "CRC", "2026-06-01"),
                 ("c", "EUR", "CRC", "2026-01-01"), ("d", "USD", "MXN", "2026-01-01"))
    on = date.fromisoformat
    assert ref.resolve_rate(rows, "USD", "CRC", on("2026-06-01"))["id"] == "b"   # the day itself counts
    assert ref.resolve_rate(rows, "USD", "CRC", on("2026-05-31"))["id"] == "a"
    assert ref.resolve_rate(rows, "USD", "CRC", on("2025-12-31")) is None        # not the nearest FUTURE rate
    assert ref.resolve_rate(rows, "GBP", "CRC", on("2026-12-31")) is None        # no rate: None, never 1.0
    assert ref.resolve_rate(rows, "EUR", "MXN", on("2026-12-31")) is None        # another base never leaks in


def _oracle_text(value: Fraction) -> str:
    """Half-up to 2 decimals from exact rational arithmetic."""
    cents = (value * 100 + Fraction(1, 2)).__floor__()
    return f"{cents // 100}.{cents % 100:02d}"


def test_agrees_with_an_independent_exact_oracle_over_seeded_cases():
    rng = random.Random(77)
    for _ in range(4000):
        qty = Fraction(rng.randint(0, 10**6), 10 ** rng.randint(0, 4))
        cost = Fraction(rng.randint(0, 10**7), 10 ** rng.randint(0, 6))
        rate = Fraction(rng.randint(1, 10**9), 10 ** rng.randint(0, 6))
        text = lambda f: format(Decimal(f.numerator) / Decimal(f.denominator), "f")  # noqa: E731
        # the Fractions above have terminating decimal expansions, so the text is exact
        got = ref.money_text(ref.convert_line(text(qty), text(cost), text(rate)))
        assert got == _oracle_text(qty * cost * rate), (qty, cost, rate)


def test_the_golden_vectors_are_what_the_reference_produces_today():
    doc = json.loads(VECTORS.read_text(encoding="utf-8"))
    fresh = vectors.generate(doc["seed"], len(doc["cases"]) - 11)  # 11 fixed edge cases are appended
    assert doc["cases"] == json.loads(json.dumps(fresh)), (
        "backend-rs/tests/fx_vectors.json is stale: run "
        "python -m backend.fx.vectors --out backend-rs/tests/fx_vectors.json")
    assert any(c["expect"].get("error") for c in doc["cases"])
    assert sum(1 for c in doc["cases"] if c["op"] == "resolve") > 100
