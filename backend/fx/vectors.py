"""Seeded test cases for the conversion core, and the Python evaluator that
answers them.

Used three ways:

* `python -m backend.fx.vectors --out backend-rs/tests/fx_vectors.json` writes
  the committed golden vectors the Rust unit test replays
  (`fx::tests::the_python_reference_vectors_are_reproduced_exactly`);
* `tests/test_fx_reference.py` checks the committed file is exactly what the
  reference produces today (so changing a rule without regenerating is red);
* `tests/contract/fx_differential.py` generates FRESH seeds and compares the
  reference with the `stockai-api fx-eval` binary, line for line.

Case encoding (shared with `backend-rs/src/fx_eval.rs`): a number is a JSON
string of decimal text, a JSON integer, or `{"f": "<text>"}` for a float parsed
from text (the Rust side uses Rust's correctly rounded parser).
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from backend.fx import reference as ref

CURRENCIES = ["USD", "EUR", "MXN", "GTQ", "COP", "CRC", "PEN", "CLP"]


def _value(x: Any) -> Any:
    if isinstance(x, dict):
        return float(x["f"])
    return x


def _ok(d: Decimal) -> dict:
    return {"ok": ref.money_text(d)}


def _rate_text(d: Decimal) -> str:
    return format(d.normalize(), "f")


def evaluate(case: dict) -> dict:
    """The reference answer to one case: {"ok": ...} or {"error": true}."""
    try:
        op = case["op"]
        if op == "convert":
            return _ok(ref.convert(_value(case["amount"]), _value(case["rate"])))
        if op == "convert_line":
            return _ok(ref.convert_line(_value(case["qty"]), _value(case["cost"]),
                                        _value(case["rate"])))
        if op == "base_line":
            return _ok(ref.base_line_value(_value(case["qty"]), _value(case["cost"])))
        if op == "total_lines":
            vals = []
            for ln in case["lines"]:
                if ln.get("rate") is None:
                    vals.append(ref.base_line_value(_value(ln["qty"]), _value(ln["cost"])))
                else:
                    vals.append(ref.convert_line(_value(ln["qty"]), _value(ln["cost"]),
                                                 _value(ln["rate"])))
            return _ok(ref.total(vals))
        if op == "parse_rate":
            return {"ok": _rate_text(ref.parse_rate(_value(case["value"])))}
        if op == "resolve":
            rows = [{**r, "effective_date": date.fromisoformat(r["effective_date"])}
                    for r in case["rates"]]
            found = ref.resolve_rate(rows, case["currency"], case["base"],
                                     date.fromisoformat(case["as_of"]))
            return {"ok": None if found is None else found["id"]}
    except ref.FxError:
        return {"error": True}
    return {"error": True}


# ── generation ───────────────────────────────────────────────────────────────

def _dec_text(rng: random.Random, max_int_digits: int, max_dec: int) -> str:
    ints = rng.randint(0, max_int_digits)
    int_part = "".join(rng.choice("0123456789") for _ in range(ints)).lstrip("0") or "0"
    n_dec = rng.randint(0, max_dec)
    if n_dec == 0:
        return int_part
    return int_part + "." + "".join(rng.choice("0123456789") for _ in range(n_dec))


def _tieish(rng: random.Random) -> tuple[str, str]:
    """An amount and a rate whose product lands on or next to a half cent."""
    mant = rng.randint(1, 9999)
    a = f"{mant}.{rng.choice(['5', '25', '75', '125', '375', '625', '875'])}"
    r = rng.choice(["1", "2", "0.5", "4", "8", "0.1", "10", "100", "0.01"])
    return a, r


def _number(rng: random.Random, max_int: int = 9, max_dec: int = 8) -> Any:
    kind = rng.random()
    if kind < 0.55:
        return _dec_text(rng, max_int, max_dec)
    if kind < 0.7:
        return rng.randint(0, 10 ** rng.randint(1, 12))
    # a float, as the text Python's repr gives for it
    f = rng.uniform(0, 10 ** rng.randint(0, 9))
    if rng.random() < 0.3:
        f = round(f, rng.randint(0, 4))
    return {"f": repr(f)}


def _rate(rng: random.Random) -> Any:
    kind = rng.random()
    if kind < 0.7:
        return _dec_text(rng, rng.randint(0, 4), rng.randint(0, 10))
    if kind < 0.85:
        return {"f": repr(round(rng.uniform(0.0001, 5000), rng.randint(0, 6)))}
    return str(rng.randint(1, 5000))


_BAD = ["", ".", "-1", "-0.5", "1_000", "NaN", "nan", "Infinity", "1e", "e5", "--1",
        "1.2.3", "0x10", "abc", "1e31", "1e30", "123456789012345678901234567890123",
        "1e-41", "0." + "0" * 40 + "1", "  ", "1 2", "+-1", "٣"]


def generate(seed: int, n: int) -> list[dict]:
    rng = random.Random(seed)
    cases: list[dict] = []

    def add(case: dict) -> None:
        cases.append({**case, "expect": evaluate(case)})

    for _ in range(n):
        pick = rng.random()
        if pick < 0.28:
            add({"op": "convert", "amount": _number(rng), "rate": _rate(rng)})
        elif pick < 0.55:
            add({"op": "convert_line", "qty": _number(rng, 6, 4), "cost": _number(rng, 7, 6),
                 "rate": _rate(rng)})
        elif pick < 0.62:
            add({"op": "base_line", "qty": _number(rng, 6, 4), "cost": _number(rng, 7, 6)})
        elif pick < 0.72:
            a, r = _tieish(rng)
            add({"op": "convert", "amount": a, "rate": r})
        elif pick < 0.82:
            lines = []
            for _ in range(rng.randint(0, 8)):
                lines.append({"qty": _number(rng, 5, 3), "cost": _number(rng, 6, 4),
                              "rate": None if rng.random() < 0.4 else _rate(rng)})
            add({"op": "total_lines", "lines": lines})
        elif pick < 0.9:
            add({"op": "parse_rate", "value": _rate(rng) if rng.random() < 0.8
                 else _dec_text(rng, 10, 12)})
        elif pick < 0.97:
            base = rng.choice(CURRENCIES)
            rows = []
            for i in range(rng.randint(0, 9)):
                rows.append({
                    "id": f"r{i}", "currency": rng.choice(CURRENCIES),
                    "base_currency": base if rng.random() < 0.85 else rng.choice(CURRENCIES),
                    "effective_date": (date(2026, 1, 1) + timedelta(days=rng.randint(0, 400))).isoformat(),
                })
            # unique dates per (currency, base) as the table's key guarantees
            seen = set()
            uniq = []
            for r in rows:
                k = (r["currency"], r["base_currency"], r["effective_date"])
                if k not in seen:
                    seen.add(k)
                    uniq.append(r)
            add({"op": "resolve", "rates": uniq, "currency": rng.choice(CURRENCIES), "base": base,
                 "as_of": (date(2026, 1, 1) + timedelta(days=rng.randint(-30, 430))).isoformat()})
        else:
            bad = rng.choice(_BAD)
            op = rng.choice(["convert", "convert_line", "base_line", "parse_rate"])
            if op == "convert":
                add({"op": op, "amount": bad, "rate": "1.5"})
            elif op == "convert_line":
                add({"op": op, "qty": "2", "cost": bad, "rate": "1.5"})
            elif op == "base_line":
                add({"op": op, "qty": bad, "cost": "1"})
            else:
                add({"op": op, "value": bad})
    # Fixed edge cases every run carries, whatever the seed.
    for case in [
        {"op": "convert", "amount": "2.675", "rate": "1"},
        {"op": "convert", "amount": "0.005", "rate": "1"},
        {"op": "convert", "amount": {"f": "0.1"}, "rate": "3"},
        {"op": "convert", "amount": {"f": "2.675"}, "rate": "1"},
        {"op": "convert", "amount": "100", "rate": "520.5"},
        {"op": "convert_line", "qty": "3", "cost": "0.335", "rate": "520.5"},
        {"op": "convert_line", "qty": {"f": "-0.0"}, "cost": "5", "rate": "2"},
        {"op": "parse_rate", "value": "1.0000000000"},
        {"op": "parse_rate", "value": "1.00000000001"},
        {"op": "parse_rate", "value": "0"},
        {"op": "total_lines", "lines": []},
    ]:
        add(case)
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--n", type=int, default=3000)
    args = ap.parse_args()
    doc = {"seed": args.seed, "generator": "backend/fx/vectors.py", "cases": generate(args.seed, args.n)}
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=0, sort_keys=True)
        fh.write("\n")


if __name__ == "__main__":
    main()
