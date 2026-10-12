"""Push ingest: the customer's side POSTs stock (or movements) to us.

For a customer who cannot open database access to StockAI, a nightly job — or
a small change-data-capture forwarder (a trigger writing an outbox table, a
Debezium consumer, a few lines of script) — sends rows to
`POST /inventory-sync/profiles/{id}/ingest`. The rows go through the SAME
pipeline as a pulled source: the profile's column mapping and transforms, the
row validation, staging, the safety brake, the set-based apply and the run
log. There is no second validation path.

Contract (JSON body, or NDJSON with the same fields as query parameters):

    batch_id     the sender's id for this batch, [A-Za-z0-9._:-]{1,128}. A retry
                 with the same id and the same rows is answered from the first
                 run (`replayed: true`) and applies nothing twice; the same id
                 with DIFFERENT rows is refused (`inventory_sync_batch_id_reused`).
    rows         objects named like the mapping's columns (nested objects are
                 flattened to dotted paths, as for MongoDB).
    snapshot_id  optional: this batch is one part of a FULL catalogue snapshot
                 sent in several parts; parts are staged, and the part with
                 `last: true` runs the full-snapshot brake (vanished SKUs, row
                 drop) and applies the whole snapshot at once.
    dry_run      preview what the rows would do; writes nothing. A dry run of a
                 push profile is what allows enabling it.

Without `snapshot_id` a batch is partial: it updates the SKUs it names and
never treats an absent SKU as gone. For a ledger profile the rows are
MOVEMENTS: applied as deltas, each movement id once, in any order.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from backend.config import settings
from backend.db.connection import _json, execute, query, query_one
from backend.errors import AppError

_BATCH_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _reject(code: str, message: str, status: int = 422, **params: Any) -> AppError:
    return AppError(code, message, status_code=status, params=params)


def max_rows() -> int:
    return max(1, int(getattr(settings, "inventory_sync_max_ingest_rows", 50_000) or 50_000))


def _clean_rows(rows: Any) -> list[dict]:
    from backend.datasources.mongo import flatten
    if not isinstance(rows, list):
        raise _reject("inventory_sync_ingest_invalid", "rows must be a JSON array of objects.", reason="not_array")
    if len(rows) > max_rows():
        raise _reject("inventory_sync_ingest_too_large",
                      f"A batch may carry at most {max_rows()} rows; send several batches.", 413, max=max_rows())
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise _reject("inventory_sync_ingest_invalid", f"rows[{i}] is not an object.", reason="row_not_object",
                          path=f"rows[{i}]")
        out.append(flatten(r))
    return out


def _fingerprint(rows: list[dict], snapshot_id: Optional[str], last: bool) -> str:
    raw = json.dumps({"rows": rows, "snapshot_id": snapshot_id, "last": last}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _run_out(run: Optional[dict]) -> Optional[dict]:
    if not run:
        return None
    keep = ("id", "status", "run_kind", "rows_read", "rows_valid", "created", "updated", "unchanged",
            "rejected", "rejected_sample", "error_code", "error_params", "duration_ms", "warnings")
    return {k: run.get(k) for k in keep}


def ingest(tenant_id: str, user_id: str, profile_id: str, *, batch_id: str, rows: Any,
           snapshot_id: Optional[str] = None, last: bool = True, dry_run: bool = False) -> dict:
    from backend.inventory_sync import engine, profiles, scheduler
    profile = profiles.require(tenant_id, profile_id)
    if profile.get("source_kind") != "push":
        raise _reject("inventory_sync_not_push", "This sync reads a database; it does not accept pushed rows.", 409)
    if not _BATCH_ID.match(str(batch_id or "")):
        raise _reject("inventory_sync_ingest_invalid", "batch_id is required: [A-Za-z0-9._:-]{1,128}.",
                      reason="batch_id", path="batch_id")
    if snapshot_id is not None and not _BATCH_ID.match(str(snapshot_id)):
        raise _reject("inventory_sync_ingest_invalid", "snapshot_id: [A-Za-z0-9._:-]{1,128}.",
                      reason="snapshot_id", path="snapshot_id")
    clean = _clean_rows(rows)
    model = profile.get("stock_model") or "snapshot"

    if dry_run:
        prof = {**profile, "engine": "push"}
        result = engine.dry_run(tenant_id, prof, sample=len(clean) or 1,
                                batches=_batches_for(tenant_id, profile, clean, None))
        profiles.mark_dry_run(tenant_id, profile_id, profile["config_hash"])
        return {"batch_id": batch_id, "dry_run": True, "result": result}

    if not profile.get("enabled"):
        raise _reject("inventory_sync_dry_run_required",
                      "Preview a batch (dry_run) and turn this sync on before sending data.", 409)
    if profile.get("health") == "paused":
        raise _reject("inventory_sync_paused", "This sync is paused; see its health for the reason.", 409,
                      reason=profile.get("health_reason"))

    fp = _fingerprint(clean, snapshot_id, last)
    seen = query_one("SELECT run_id, fingerprint FROM inventory_sync_ingest_batches WHERE profile_id = %s "
                     "AND batch_id = %s", (profile_id, batch_id))
    if seen:
        if seen["fingerprint"] != fp:
            raise _reject("inventory_sync_batch_id_reused",
                          "This batch_id was already used for different rows.", 409, batch_id=batch_id)
        run = query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (seen["run_id"],))
        return {"batch_id": batch_id, "replayed": True, "run": _run_out(run)}

    if snapshot_id and model != "ledger":
        return _snapshot_part(tenant_id, user_id, profile, batch_id, snapshot_id, clean, last, fp)

    owner = f"push:{batch_id}"
    scheduler.claim_one(tenant_id, profile_id, owner)
    try:
        run_id = engine.start_run(tenant_id, profile_id, "push", user_id=user_id, worker=owner,
                                  kind="incremental" if model == "ledger" else "push")
        execute("INSERT INTO inventory_sync_ingest_batches (tenant_id, profile_id, batch_id, run_id, rows, "
                "fingerprint) VALUES (%s,%s,%s,%s,%s,%s)", (tenant_id, profile_id, batch_id, run_id, len(clean), fp))
        run = engine.execute_run(tenant_id, profile_id, run_id, lease_owner=owner, user_id=user_id,
                                 batches=_batches_for(tenant_id, profile, clean, run_id))
    finally:
        scheduler.release(profile_id, owner)
    return {"batch_id": batch_id, "replayed": False, "run": _run_out(run)}


def _batches_for(tenant_id: str, profile: dict, rows: list[dict], run_id: Optional[str]):
    """The rows as staging batches; for a ledger, a factory turning movements
    into balances (needs the run's report for the cursor and counts)."""
    chunks = [rows[i:i + 1000] for i in range(0, len(rows), 1000)]
    if (profile.get("stock_model") or "snapshot") != "ledger":
        return chunks
    from backend.inventory_sync import ledger
    return lambda report: ledger.apply_movements(tenant_id, profile, iter(chunks), run_id, report)


def _snapshot_part(tenant_id, user_id, profile, batch_id, snapshot_id, rows, last, fp) -> dict:
    """One part of a multi-part full snapshot: staged now, applied on `last`."""
    from backend.inventory_sync import engine, scheduler
    pid = profile["id"]
    run = query_one("SELECT * FROM inventory_sync_runs WHERE profile_id = %s AND status = 'receiving' "
                    "AND scope->>'snapshot_id' = %s", (pid, snapshot_id))
    if not run:
        run_id = engine.start_run(tenant_id, pid, "push", user_id=user_id, kind="full",
                                  scope={"snapshot_id": snapshot_id})
        execute("UPDATE inventory_sync_runs SET status = 'receiving' WHERE id = %s", (run_id,))
    else:
        run_id = run["id"]
    execute("INSERT INTO inventory_sync_ingest_batches (tenant_id, profile_id, batch_id, run_id, rows, "
            "fingerprint) VALUES (%s,%s,%s,%s,%s,%s)", (tenant_id, pid, batch_id, run_id, len(rows), fp))
    engine.stage_part(tenant_id, {**profile, "engine": "push"}, run_id, rows)
    if not last:
        return {"batch_id": batch_id, "replayed": False, "snapshot_id": snapshot_id, "receiving": True,
                "run": _run_out(query_one("SELECT * FROM inventory_sync_runs WHERE id = %s", (run_id,)))}
    owner = f"push:{snapshot_id}"
    scheduler.claim_one(tenant_id, pid, owner)
    try:
        execute("UPDATE inventory_sync_runs SET status = 'running' WHERE id = %s", (run_id,))
        result = engine.execute_run(tenant_id, pid, run_id, lease_owner=owner, user_id=user_id, staged=True)
    finally:
        scheduler.release(pid, owner)
    return {"batch_id": batch_id, "replayed": False, "snapshot_id": snapshot_id, "run": _run_out(result)}
