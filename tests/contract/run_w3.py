"""Run only the wave-3 (inventory hub) contract cases.

    python tests/contract/run_w3.py --python http://127.0.0.1:8052 --rust http://127.0.0.1:8051 \
        --env-file backend/.env --db postgresql://postgres@127.0.0.1:5560/rust_w3 [--only NAME] [--dump]

The full harness (`contract_test.py`) runs these cases last; this entry point
skips the rest of the suite so a change to one wave-3 route can be re-checked
in seconds. It creates and erases its own throwaway tenants (three), exactly as
`w3_cases.run_w3` documents.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract_test as h  # noqa: E402
import w3_cases  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default="http://127.0.0.1:8011")
    ap.add_argument("--rust", default="http://127.0.0.1:8021")
    ap.add_argument("--env-file", default="backend/.env")
    ap.add_argument("--db", required=True)
    ap.add_argument("--only", default=None)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    import psycopg2  # noqa: PLC0415

    db = psycopg2.connect(args.db)
    db.autocommit = True
    env = h.read_env_file(args.env_file)
    secret = os.environ.get("SECRET_KEY") or env.get("SECRET_KEY")
    if not secret:
        raise SystemExit("SECRET_KEY not found")
    fx = h.make_fixture(args.python, secret)
    try:
        results = w3_cases.run_w3(args, fx, db, h)
    finally:
        if not args.keep:
            h.erase_fixture(args.python, fx)

    width = max((len(c.name) for c, _, _ in results), default=10)
    print()
    for case, verdict, problems in results:
        print(f"{verdict:4}  {case.name:<{width}}  {case.route}")
        for p in problems:
            print(f"        - {p}")
    by_route: dict[str, list[str]] = {}
    for case, verdict, _ in results:
        by_route.setdefault(case.route, []).append(verdict)
    print("\nper route:")
    for route, verdicts in by_route.items():
        extra = "".join(f", {verdicts.count(v)} {v.lower()}" for v in ("STALE", "SKIP") if v in verdicts)
        print(f"  {route:<52} {verdicts.count('PASS')}/{len(verdicts)} pass{extra}")
    failed = sum(1 for _, v, _ in results if v == "FAIL")
    print(f"\n{len(results) - failed}/{len(results)} cases pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
