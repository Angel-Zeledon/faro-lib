"""Run ONE section of the contract harness on its own throwaway tenant.

    python tests/contract/run_section.py --section run_cf --python URL --rust URL \
        --env-file backend/.env --db postgresql://... [--only NAME] [--dump]

The full harness (`contract_test.py`) takes ~20 minutes; when one section
fails, this re-checks just that section in seconds. Sections are looked up by
name in `contract_test` and in the per-feature case files it imports; each is
called through `_guarded`, exactly as the full run calls it.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract_test as h  # noqa: E402

# name -> (module, function, call shape)
SECTIONS = {
    "run_r3": ("contract_test", "run_r3", "afd"), "run_r4": ("contract_test", "run_r4", "afd"),
    "run_ip_allowlist": ("contract_test", "run_ip_allowlist", "afd"),
    "run_cd_resync": ("contract_test", "run_cd_resync", "afd"),
    "run_outbox": ("contract_test", "run_outbox", "afd"),
    "run_delegation": ("contract_test", "run_delegation", "afd"),
    "run_portal": ("contract_test", "run_portal", "afd"),
    "run_fx_section": ("contract_test", "run_fx_section", "afd"),
    "run_session_policy": ("contract_test", "run_session_policy", "afd"),
    "run_saml": ("contract_test", "run_saml", "afd"), "run_audit_stream": ("contract_test", "run_audit_stream", "afd"),
    "run_org": ("contract_test", "run_org", "afd"), "run_cost_centers": ("contract_test", "run_cost_centers", "afd"),
    "run_rd": ("contract_test", "run_rd", "afd"), "run_cr": ("contract_test", "run_cr", "afd"),
    "run_cf": ("cf_outlook_cases", "run_cf", "afdh"), "run_money": ("cf_money_cases", "run_money", "afdh"),
    "run_w3": ("w3_cases", "run_w3", "afdh"), "run_consensus": ("consensus_cases", "run_consensus", "afdh"),
    "run_custom_roles": ("custom_roles_cases", "run_custom_roles", "afd"),
    "run_mfa": ("mfa_cases", "run_mfa", "hafd"),
    "scheduled_reports": ("scheduled_reports_contract", "run", "afdh"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", required=True,
                    help="one name, or several comma-separated, run in order on ONE tenant "
                         "(state one section leaves is what the next one sees, as in the full run): "
                         + ", ".join(sorted(SECTIONS)))
    ap.add_argument("--python", default="http://127.0.0.1:8011")
    ap.add_argument("--rust", default="http://127.0.0.1:8021")
    ap.add_argument("--env-file", default="backend/.env")
    ap.add_argument("--db", required=True)
    ap.add_argument("--only", default=None)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--allow-stale", action="store_true")
    ap.add_argument("--rust-strict", default=None)
    args = ap.parse_args()

    import psycopg2  # noqa: PLC0415

    db = psycopg2.connect(args.db)
    db.autocommit = True
    env = h.read_env_file(args.env_file)
    secret = os.environ.get("SECRET_KEY") or env.get("SECRET_KEY")
    if not secret:
        raise SystemExit("SECRET_KEY not found")
    names = [n.strip() for n in args.section.split(",") if n.strip()]
    unknown = [n for n in names if n not in SECTIONS]
    if unknown:
        raise SystemExit(f"unknown section(s): {unknown}")
    fx = h.make_fixture(args.python, secret)
    results: list = []
    try:
        for name in names:
            mod_name, fn_name, shape = SECTIONS[name]
            fn = getattr(importlib.import_module(mod_name), fn_name)
            call = {"afd": (args, fx, db), "afdh": (args, fx, db, h), "hafd": (h, args, fx, db)}[shape]
            results += h._guarded(name, fn, *call)
    finally:
        if not args.keep:
            h.erase_fixture(args.python, fx)
    return h._report(args, fx, results)


if __name__ == "__main__":
    sys.exit(main())
