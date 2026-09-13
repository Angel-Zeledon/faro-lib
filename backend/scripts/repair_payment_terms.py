"""Repair `suppliers.payment_terms_days` rows stored by the old, wrong parser.

Until 2026-08-23 `parse_payment_terms_days` matched the stem `anticipad` but not
the far commoner `anticipo`, so "50% anticipo" fell through to the generic
number extractor and was stored as **50 days of credit** for a payment that is
due immediately. Same shape for `2x30` -> 2 and `30/60/90` -> 30. Rows written
before the fix keep those numbers: the corrected backfill migration only fills
`payment_terms_days IS NULL`, so it never overwrites them.

**Why this is a script and not a migration.** `backend/db/migrations.py` has no
applied-ledger — every statement in it re-runs on every single boot. A
value-based correction there would re-apply itself forever, so the moment a user
legitimately typed a number this script disagrees with, the next restart would
silently take it away again. A correction that fights the user once a day is
worse than the wrong number it fixes.

So: run by hand, once, and look at it first.

    # show what WOULD change, touch nothing (default):
    backend/.venv/Scripts/python.exe -m backend.scripts.repair_payment_terms

    # actually write:
    backend/.venv/Scripts/python.exe -m backend.scripts.repair_payment_terms --apply

    # one tenant only:
    backend/.venv/Scripts/python.exe -m backend.scripts.repair_payment_terms --tenant ten_xxx --apply

Only rows where the stored number DISAGREES with what the corrected parser now
reads from that row's own `payment_terms` text are touched. A row whose text is
unreadable becomes NULL, which is what "we do not know your terms" is spelled as
everywhere else in this module — `terms_known` then reports False instead of
claiming a number nobody supplied.
"""

from __future__ import annotations

import argparse
import logging

log = logging.getLogger("repair_payment_terms")


def _rows(tenant_id: str | None) -> list[dict]:
    from backend.db.connection import query
    sql = ("SELECT id, tenant_id, name, payment_terms, payment_terms_days "
           "FROM suppliers WHERE payment_terms IS NOT NULL AND payment_terms <> ''")
    params: tuple = ()
    if tenant_id:
        sql += " AND tenant_id = %s"
        params = (tenant_id,)
    return query(sql + " ORDER BY tenant_id, name", params)


def plan(tenant_id: str | None = None) -> list[dict]:
    """Rows whose stored day count disagrees with the corrected parser."""
    from backend.inventory.cash_service import parse_payment_terms_days

    changes = []
    for r in _rows(tenant_id):
        correct = parse_payment_terms_days(r.get("payment_terms"))
        stored = r.get("payment_terms_days")
        if correct != stored:
            changes.append({
                "id": r["id"], "tenant_id": r["tenant_id"], "name": r.get("name"),
                "terms": r.get("payment_terms"), "stored": stored, "correct": correct,
            })
    return changes


def apply(changes: list[dict]) -> int:
    from backend.db.connection import execute
    for c in changes:
        execute(
            "UPDATE suppliers SET payment_terms_days = %s WHERE id = %s AND tenant_id = %s",
            (c["correct"], c["id"], c["tenant_id"]),
        )
    return len(changes)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    ap.add_argument("--tenant", default=None, help="limit to one tenant id")
    args = ap.parse_args()

    from backend.config import settings
    from backend.db.connection import init_pool, pool_is_initialized
    if not pool_is_initialized():
        init_pool(settings.database_url)

    changes = plan(args.tenant)
    if not changes:
        log.info("Nothing to repair: every stored payment_terms_days already "
                 "agrees with the parser.")
        return

    log.info("%d supplier row(s) disagree with the corrected parser:\n", len(changes))
    for c in changes:
        log.info("  %-28s %-24r stored=%-6s -> %s",
                 (c["name"] or "")[:28], c["terms"], c["stored"], c["correct"])

    if not args.apply:
        log.info("\nDry run — nothing was written. Re-run with --apply to commit.")
        return

    log.info("\nWrote %d row(s).", apply(changes))


if __name__ == "__main__":
    main()
