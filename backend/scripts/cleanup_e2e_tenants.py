"""
Delete tenants created by e2e browser tests.

`Frontend/tests/critical_flows.mjs` signs up a fresh tenant every run (the
running-stockai skill's convention: test accounts use the @stockai-e2e.io domain,
which has no MX, so nothing real is ever reached). Nothing deleted them, so a
dev database accumulates one throwaway tenant per run. This finds every user
whose email ends in @stockai-e2e.io and deletes their tenant — `ON DELETE
CASCADE` on tenant_id takes the rest (sessions, datasets, stock, etc.).

Run:
    backend/.venv/Scripts/python.exe -m backend.scripts.cleanup_e2e_tenants
    backend/.venv/Scripts/python.exe -m backend.scripts.cleanup_e2e_tenants --dry-run
"""
from __future__ import annotations

import argparse
import logging

log = logging.getLogger("cleanup_e2e_tenants")


def cleanup(dry_run: bool = False) -> list[str]:
    from backend.config import settings
    from backend.db.connection import init_pool, query, execute
    from backend.db import migrations

    init_pool(settings.database_url)
    migrations.run_all()

    # @faro-e2e.io is the domain the tests used before the rename to StockAI;
    # a dev database still holds tenants from those runs.
    rows = query(
        "SELECT DISTINCT tenant_id FROM users WHERE email LIKE %s OR email LIKE %s",
        ("%@stockai-e2e.io", "%@faro-e2e.io"),
    )
    tenant_ids = [r["tenant_id"] for r in rows]

    if not tenant_ids:
        log.info("No @stockai-e2e.io tenants found.")
        return []

    log.info("Found %d @stockai-e2e.io tenant(s): %s", len(tenant_ids), tenant_ids)
    if dry_run:
        log.info("--dry-run: not deleting.")
        return tenant_ids

    for tid in tenant_ids:
        execute("DELETE FROM tenants WHERE id = %s", (tid,))
        log.info("Deleted tenant %s", tid)

    return tenant_ids


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List without deleting")
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run)
