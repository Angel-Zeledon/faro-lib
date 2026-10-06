"""Live differential test of the approval-chain evaluator, Rust against Python.

    SECRET_KEY=... DATABASE_URL=postgresql://... \
    backend/.venv/Scripts/python.exe tests/contract/chain_differential.py \
        --python http://127.0.0.1:8011 --rust http://127.0.0.1:8021 [--configs 80] [--seed 7]

For each of N seeded configurations (center trees with inactive centers and
cycles, chains that are inactive or malformed, default chains, boundary and
unknown amounts, escalation) the harness WRITES the rows to a throwaway
tenant's tables, then asks both implementations about every (center, amount,
escalate) combination:

* Rust: `POST /approval-chains/evaluate` (the route's loader reads the rows
  and `crate::chain::resolve` decides);
* Python: `po_chain_service.load_chains / load_centers` + `core.resolve`,
  imported in this process against the SAME database.

Any difference in the JSON is a failure, and the harness prints the config.
It exercises the two loaders as well as the two evaluators, which the
fixtures file (tests/contract/chain_fixtures.json, pure computation) cannot.

Needs the backend venv (psycopg2, the backend package), a running Python API
(for the throwaway tenant's signup) and the Rust API, and the same SECRET_KEY
and DATABASE_URL the services use. Erases the tenant at the end.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import contract_test as ct  # noqa: E402
import gen_chain_fixtures as gen  # noqa: E402


def storable_bands(bands):
    """The part of a generated `bands` value the table can hold: a list of
    objects with a distinct non-negative float `min_amount`. The levels stay as
    generated (malformed or not): the table does not judge them."""
    if not isinstance(bands, list):
        return []
    out, seen = [], set()
    for b in bands:
        if not isinstance(b, dict) or not isinstance(b.get("min_amount"), float):
            continue
        m = b["min_amount"]
        if m < 0 or m in seen or "levels" not in b:
            continue
        seen.add(m)
        out.append({"min_amount": m, "levels": b["levels"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default="http://127.0.0.1:8011")
    ap.add_argument("--rust", default="http://127.0.0.1:8021")
    ap.add_argument("--configs", type=int, default=80)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    secret = os.environ.get("SECRET_KEY")
    dsn = os.environ.get("DATABASE_URL")
    if not secret or not dsn:
        raise SystemExit("set SECRET_KEY and DATABASE_URL to what the services use")

    import psycopg2
    from backend.db.connection import init_pool
    from backend.inventory import cost_center_chain_core as core
    from backend.inventory import po_chain_service as svc

    init_pool(dsn)
    db = psycopg2.connect(dsn)
    db.autocommit = True
    cur = db.cursor()

    fx = ct.make_fixture(args.python, secret)
    tid = fx.tenant_id
    print(f"throwaway tenant {tid}")
    rng = random.Random(args.seed)
    compared = mismatches = required = 0
    states: dict[str, int] = {}
    try:
        for n in range(args.configs):
            tag = uuid.uuid4().hex[:8]
            case = gen.build_case(rng, n)
            cur.execute("DELETE FROM approval_chain_bands WHERE tenant_id = %s", (tid,))
            cur.execute("DELETE FROM approval_chains WHERE tenant_id = %s", (tid,))
            cur.execute("UPDATE cost_centers SET parent_id = NULL WHERE tenant_id = %s", (tid,))
            cur.execute("DELETE FROM cost_centers WHERE tenant_id = %s", (tid,))
            ident = lambda x: f"{tag}-{x}" if x else x
            centers = [c for c in case["centers"]]
            for c in centers:
                cur.execute("""INSERT INTO cost_centers (id, tenant_id, code, name, active, created_by)
                               VALUES (%s, %s, %s, %s, %s, 'diff')""",
                            (ident(c["id"]), tid, ident(c["id"]), c["id"], c["active"]))
            for c in centers:
                if c["parent_id"] and c["parent_id"] != "missing":
                    cur.execute("UPDATE cost_centers SET parent_id = %s WHERE id = %s",
                                (ident(c["parent_id"]), ident(c["id"])))
            for ch in case["chains"]:
                cur.execute("""INSERT INTO approval_chains (id, tenant_id, name, cost_center_id, active, created_by)
                               VALUES (%s, %s, 'diff', %s, %s, 'diff')""",
                            (ident(ch["id"]), tid, ident(ch["cost_center_id"]) if ch["cost_center_id"] else None,
                             ch["active"]))
                for b in storable_bands(ch["bands"]):
                    cur.execute("""INSERT INTO approval_chain_bands (tenant_id, chain_id, min_amount, levels)
                                   VALUES (%s, %s, %s, %s::jsonb)""",
                                (tid, ident(ch["id"]), b["min_amount"], json.dumps(b["levels"])))
            mins = [b["min_amount"] for ch in case["chains"] for b in storable_bands(ch["bands"])]
            amounts = [None, 0.0, 1.0, 5000.0]
            for m in mins[:6]:
                amounts += [m, m - 0.004, m - 0.006, m + 0.01]
            ids = [ident(c["id"]) for c in centers] + [None, "ghost"]
            token = ct.mint_access_token(secret, fx.admin_id, tid, "admin")
            py_chains, py_centers = svc.load_chains(tid), svc.load_centers(tid)
            for center in ids:
                for amount in amounts:
                    for escalate in (False, True) if (amount in (None, 1.0) or rng.random() < 0.15) else (False,):
                        body = {"escalate": escalate}
                        if center is not None:
                            body["cost_center_id"] = center
                        if amount is not None:
                            body["amount"] = amount
                        r = ct.http(args.rust, "POST", f"{ct.API}/approval-chains/evaluate", token=token, body=body)
                        want = json.loads(json.dumps(core.resolve(py_chains, py_centers, center, amount, escalate)))
                        compared += 1
                        got = (r.body or {}).get("data") if r.status == 200 else {"http": r.status, "body": r.body}
                        key = f"{want['state']}:{want['reason']}"
                        states[key] = states.get(key, 0) + 1
                        required += want["state"] == "required"
                        if got != want:
                            mismatches += 1
                            if mismatches <= 5:
                                print(f"\nMISMATCH config {n} center={center} amount={amount} escalate={escalate}")
                                print("  python:", json.dumps(want))
                                print("  rust:  ", json.dumps(got))
        print(f"\ncompared {compared} resolutions over {args.configs} configurations "
              f"({required} required): {mismatches} mismatches")
        for k in sorted(states):
            print(f"  {k}: {states[k]}")
    finally:
        if not args.keep:
            cur.execute("UPDATE cost_centers SET parent_id = NULL WHERE tenant_id = %s", (tid,))
            ct.erase_fixture(args.python, fx)
    return 1 if mismatches or compared == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
