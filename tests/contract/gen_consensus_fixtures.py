"""Cases for the differential test of the Rust consensus math.

    python tests/contract/gen_consensus_fixtures.py          # rewrite the fixture
    python tests/contract/gen_consensus_fixtures.py --check  # fail if it is stale

Writes backend-rs/src/consensus/consensus_cases.json: random inputs with what the
Python reference (`consensus_reference.py`) answers. The Rust unit test
`consensus::math::tests::matches_the_python_reference` loads it and demands exact
equality; `backend/tests/test_consensus_reference.py` demands the file is current.
Deterministic (fixed seed) and stdlib only.
"""

import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import consensus_reference as ref  # noqa: E402

OUT = os.path.join(HERE, "..", "..", "backend-rs", "src", "consensus", "consensus_cases.json")
SEED = 20261006
CONSENSUS_CASES = 200
FVA_CASES = 120


def _config(rng: random.Random) -> dict:
    priority = list(ref.FUNCTIONS)
    rng.shuffle(priority)
    weights = {f: rng.choice([0, 1, 1, 2, 3, 5, 10, 100, 333, 1000]) for f in ref.FUNCTIONS}
    return {"rule": rng.choice(["priority", "weighted"]), "priority": priority, "weights": weights,
            "cap_down_bp": rng.choice([-10000, -5000, -2000, -1, 0]),
            "cap_up_bp": rng.choice([100000, 20000, 5000, 1, 0])}


def _submissions(rng: random.Random) -> list[dict]:
    subs, n = [], 0
    for sku in rng.sample(["A", "B", "C", "D-1", "sku with space"], rng.randint(1, 3)):
        taken: dict[str, list[tuple[int, int]]] = {f: [] for f in ref.FUNCTIONS}
        for _ in range(rng.randint(1, 7)):
            f = rng.choice(ref.FUNCTIONS)
            start = rng.randint(0, 40)
            end = start + rng.randint(0, 25)
            if any(start <= hi and end >= lo for lo, hi in taken[f]):
                continue   # one function never overlaps itself on a SKU (the route refuses it)
            taken[f].append((start, end))
            n += 1
            bp = rng.choice([rng.randint(-10000, 100000), rng.randint(-3000, 3000), 0, 1, -1, 5, 15, -15])
            subs.append({"id": f"s{n}", "sku": sku, "function": f, "start": start, "end": end,
                         "pct_bp": bp})
    return subs


def _points(rng: random.Random) -> list[dict]:
    out = []
    for _ in range(rng.choice([0, 1, 3, 4, 5, 6, 12, 40])):
        base = rng.choice([rng.uniform(0, 500), float(rng.randint(0, 300)), 0.0, 0.1, 1e-3])
        actual = rng.choice([rng.uniform(0, 500), float(rng.randint(0, 300)), 0.0, base])
        out.append({"base": base, "pct_bp": rng.choice([0, 500, -500, 1234, -2500, 100000, -10000, 7]),
                    "actual": actual, "function": rng.choice(ref.FUNCTIONS),
                    "reason": rng.choice(["promotion", "price_change", "other"])})
    return out


def build() -> dict:
    rng = random.Random(SEED)
    consensus = []
    for _ in range(CONSENSUS_CASES):
        config, subs = _config(rng), _submissions(rng)
        consensus.append({"config": config, "submissions": subs,
                          "lines": ref.consensus_lines(config, subs)})
    fva = []
    for _ in range(FVA_CASES):
        pts = _points(rng)
        fva.append({"points": pts, "aggregate": ref.forecast_value_added(pts),
                    "by_function": ref.forecast_value_added_by(pts, "function"),
                    "by_reason": ref.forecast_value_added_by(pts, "reason")})
    return {"seed": SEED, "consensus": consensus, "fva": fva}


def render() -> str:
    return json.dumps(build(), separators=(",", ":"), allow_nan=False) + "\n"


if __name__ == "__main__":
    text = render()
    path = os.path.normpath(OUT)
    if "--check" in sys.argv:
        current = open(path, encoding="utf-8", newline="").read() if os.path.exists(path) else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{path} is stale: run python tests/contract/gen_consensus_fixtures.py")
            sys.exit(1)
        print("fixture is current")
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"wrote {path} ({len(text)} bytes)")
