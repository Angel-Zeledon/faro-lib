"""Differential test of the conversion core: the Python reference
(`backend/fx/reference.py`) against the Rust binary (`stockai-api fx-eval`), on
seeded cases, with exact equality of the answers.

    python tests/contract/fx_differential.py --bin path/to/stockai-api --seeds 1 2 3 --n 5000

Each seed generates `n` cases (`backend/fx/vectors.py`): decimals of every shape,
floats parsed from text, half-cent ties, malformed inputs, rate validation,
totals over mixed lines and the as-of rate lookup. Every case is answered by the
reference and by the binary; any difference is printed and the exit code is 1.
The same generator, with a fixed seed, is what `backend-rs/tests/fx_vectors.json`
holds (checked by `cargo test`), so this run adds fresh seeds on every call.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.fx import vectors  # noqa: E402


def find_binary(explicit: str | None = None) -> str | None:
    cands = [explicit, os.environ.get("STOCKAI_RS_BIN")]
    exe = "stockai-api.exe" if os.name == "nt" else "stockai-api"
    for sub in ("release", "debug"):
        cands.append(str(ROOT / "backend-rs" / "target" / sub / exe))
    target_dir = os.environ.get("CARGO_TARGET_DIR")
    if target_dir:
        for sub in ("release", "debug"):
            cands.append(str(Path(target_dir) / sub / exe))
    for c in cands:
        if c and Path(c).is_file():
            return c
    return None


def check(binary: str, seeds: list[int], n: int) -> tuple[int, list[str]]:
    """Returns (cases compared, problems)."""
    problems: list[str] = []
    total = 0
    for seed in seeds:
        cases = [{k: v for k, v in c.items() if k != "expect"} for c in vectors.generate(seed, n)]
        expected = [vectors.evaluate(c) for c in cases]
        stdin = "".join(json.dumps(c) + "\n" for c in cases)
        done = subprocess.run([binary, "fx-eval"], input=stdin, capture_output=True, text=True,
                              encoding="utf-8", timeout=300)
        if done.returncode != 0:
            problems.append(f"seed {seed}: fx-eval exited {done.returncode}: {done.stderr[:200]}")
            continue
        got = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
        if len(got) != len(cases):
            problems.append(f"seed {seed}: {len(got)} answers for {len(cases)} cases")
            continue
        for case, want, have in zip(cases, expected, got):
            total += 1
            if want != have:
                problems.append(f"seed {seed}: {json.dumps(case)[:300]} -> python {want} / rust {have}")
                if len(problems) > 20:
                    return total, problems
    return total, problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=None)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--n", type=int, default=4000)
    args = ap.parse_args()
    binary = find_binary(args.bin)
    if not binary:
        raise SystemExit("no stockai-api binary found (pass --bin or set STOCKAI_RS_BIN)")
    total, problems = check(binary, args.seeds, args.n)
    for p in problems:
        print("DIFF", p)
    print(f"{total} cases compared across seeds {args.seeds}: {'FAIL' if problems else 'identical'}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
