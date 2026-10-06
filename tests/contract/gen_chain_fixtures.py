"""Seeded differential fixtures for the approval-chain core.

    backend/.venv/Scripts/python.exe tests/contract/gen_chain_fixtures.py

rewrites tests/contract/chain_fixtures.json: random center trees and chains
(some inactive, some malformed, some cyclic, boundary amounts, unknown
amounts, escalation) with the answer the PYTHON core gives for each. The Rust
core must give the identical answer: `cargo test fixtures_agree_with_python`.
The file is checked in; regenerate it whenever the rules change on EITHER side,
then both suites say which side forgot.

Pure computation, deterministic for a seed: no database, no clock.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.inventory import cost_center_chain_core as core  # noqa: E402

SEED = 20261006
N_CASES = 900
OUT = os.path.join(os.path.dirname(__file__), "chain_fixtures.json")
USERS = [f"u{i}" for i in range(6)]


def rand_level(rng: random.Random, broken: bool):
    r = rng.random()
    if broken and r < 0.5:
        return rng.choice([
            {"kind": "role", "role": "viewer"}, {"kind": "users", "user_ids": []},
            {"kind": "users", "user_ids": [1]}, {"kind": "nope"}, "text", None,
            {"kind": "users", "user_ids": ["a"] * 21}, {"kind": "role"},
        ])
    if r < 0.55:
        return {"kind": "role", "role": rng.choice(["analyst", "admin"])}
    return {"kind": "users", "user_ids": rng.sample(USERS, rng.randint(1, 4))
            + (["u0"] if rng.random() < 0.2 else [])}


def rand_bands(rng: random.Random, broken: bool):
    n = rng.randint(1, 4)
    mins = rng.sample([0, 50, 100, 250, 500, 1000, 5000, 10000, 100000, 0.5, 99.99], n)
    bands = [{"min_amount": float(m), "levels": [rand_level(rng, broken)
                                                   for _ in range(rng.randint(1, 3))]}
             for m in mins]
    if broken and rng.random() < 0.3:
        bands.append({"min_amount": rng.choice([-5.0, "7", True, mins[0]]),
                      "levels": [{"kind": "role", "role": "admin"}]})
    if broken and rng.random() < 0.15:
        return rng.choice([[], "x", [1], [{"levels": []}]])
    return bands


def rand_tree(rng: random.Random):
    n = rng.randint(0, 8)
    centers = []
    for i in range(n):
        parent = None
        if i and rng.random() < 0.75:
            parent = f"c{rng.randrange(i)}"
        centers.append({"id": f"c{i}", "parent_id": parent, "active": rng.random() > 0.15})
    if n >= 2 and rng.random() < 0.08:                    # a cycle
        centers[0]["parent_id"] = f"c{n - 1}"
    if n and rng.random() < 0.05:                         # a dangling parent
        centers[rng.randrange(n)]["parent_id"] = "missing"
    return centers


def build_case(rng: random.Random, idx: int):
    centers = rand_tree(rng)
    chains, used_centers = [], set()
    for j in range(rng.randint(0, 4)):
        target = None
        if centers and rng.random() < 0.7:
            target = rng.choice(centers)["id"]
        if target in used_centers:                        # one chain per center (a DB index)
            continue
        used_centers.add(target)
        broken = rng.random() < 0.2
        chains.append({"id": f"ch{j}", "cost_center_id": target,
                       "active": rng.random() > 0.2, "bands": rand_bands(rng, broken)})
    ids = [c["id"] for c in centers]
    center = rng.choice(ids + [None, None, "ghost", ""]) if ids else rng.choice([None, "ghost"])
    mins = []
    for c in chains:
        if isinstance(c["bands"], list):
            for b in c["bands"]:
                if isinstance(b, dict) and isinstance(b.get("min_amount"), float):
                    mins.append(b["min_amount"])
    pool = [0.0, 1.0, 49.99, 5000.0, 123456.78]
    if mins:
        pool += [m for m in mins] + [m - 0.004 for m in mins] + [m - 0.006 for m in mins] + [m + 0.01 for m in mins]
    amount = None if rng.random() < 0.08 else round(rng.choice(pool), 4)
    escalate = rng.random() < 0.15
    expected = core.resolve(chains, centers, center, amount, escalate)
    return {"label": f"seed-case-{idx}", "centers": centers, "chains": chains,
            "center_id": center, "amount": amount, "escalate": escalate, "expected": expected}


def main() -> None:
    rng = random.Random(SEED)
    cases = [build_case(rng, i) for i in range(N_CASES)]
    states: dict[str, int] = {}
    for c in cases:
        key = c["expected"]["state"] + ":" + str(c["expected"]["reason"])
        states[key] = states.get(key, 0) + 1
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"seed": SEED, "cases": cases}, fh, indent=None, separators=(",", ":"))
        fh.write("\n")
    print(f"wrote {len(cases)} cases to {OUT}")
    for k in sorted(states):
        print(f"  {k}: {states[k]}")


if __name__ == "__main__":
    main()
