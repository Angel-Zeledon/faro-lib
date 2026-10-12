"""Engine-native change capture, read-only on the customer's side.

Three strategies read only what changed since a durable cursor stored on the
profile (`cursor_value`), each with the privileges a read-only login has:

* **change_tracking** (SQL Server): `CHANGETABLE(CHANGES t, @version)` joined
  back to the table on its primary key. Needs change tracking enabled on the
  database and the table (a DBA's one-time ALTER) and VIEW CHANGE TRACKING for
  the login. The cursor is `CHANGE_TRACKING_CURRENT_VERSION()` read BEFORE the
  changes, so nothing committed during the read is skipped. A cursor older than
  `CHANGE_TRACKING_MIN_VALID_VERSION` (retention passed) falls back to a full
  read, reported on the run.
* **oracle_rowscn** (Oracle): rows whose `ORA_ROWSCN` is above the stored
  SCN. ORA_ROWSCN is block-level unless the table has ROWDEPENDENCIES, so it
  over-reads neighbours — harmless, the apply is idempotent — and never
  under-reads: a later commit always has a higher SCN.
* **change_stream** (MongoDB replica sets / Atlas): the collection's change
  stream from the stored resume token, read for a bounded time, then closed
  (no lingering client). A token past the oplog window falls back to a full
  read. A standalone server has no change stream: the strategy reports
  itself unavailable instead of pretending.

Deletes: CT and change streams report them, but a deleted row no longer says
which SKU it held; they are counted on the run and resolved by the periodic
full reconcile (continuous mode), which is also what catches anything a
cursor could miss.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Iterator, Optional

from backend.errors import AppError
from backend.inventory_sync import reader

log = logging.getLogger(__name__)

CHANGE_STREAM_MAX_EVENTS = 50_000
CHANGE_STREAM_WAIT_MS = 1_000


def _unavailable(strategy: str, reason: str, **params: Any) -> AppError:
    return AppError("inventory_sync_strategy_unavailable",
                    f"The {strategy} strategy is not available on this source ({reason}).",
                    status_code=422, params={"strategy": strategy, "reason": reason, **params})


def _scalar(tenant_id: str, profile: dict, sql: str) -> Any:
    from backend.inventory_sync import ledger
    for batch in ledger._rows(tenant_id, profile, sql):
        if batch:
            return next(iter(batch[0].values()))
    return None


def read(tenant_id: str, profile: dict, strategy: str, *, run_id: Optional[str] = None,
         max_rows: Optional[int] = None, incremental: bool = True,
         report: Optional[dict] = None, scope: Optional[dict] = None) -> Iterator[list[dict]]:
    report = report if report is not None else {}
    if profile.get("source_kind") != "table" and strategy != "change_stream":
        raise _unavailable(strategy, "needs_table")
    if strategy == "change_tracking":
        yield from _change_tracking(tenant_id, profile, incremental, max_rows, report)
    elif strategy == "oracle_rowscn":
        yield from _rowscn(tenant_id, profile, incremental, max_rows, report)
    elif strategy == "change_stream":
        yield from _change_stream(tenant_id, profile, incremental, max_rows, report)
    else:
        raise _unavailable(strategy, "unknown")


# ── SQL Server change tracking ────────────────────────────────────────────────

def _ct_sql_failed(exc: AppError) -> AppError:
    reason = str((exc.params or {}).get("reason") or "").lower()
    if exc.code in ("sql_query_failed", "data_source_permission_denied") and \
            ("change tracking" in reason or "changetable" in reason or "view change tracking" in reason):
        return _unavailable("change_tracking", "not_enabled_or_no_permission")
    return exc


def _change_tracking(tenant_id, profile, incremental, max_rows, report):
    from backend.datasources import catalog
    from backend.inventory_sync import strategies
    q = lambda c: catalog.quote_ident("mssql", c)  # noqa: E731
    schema = profile.get("source_schema") or "dbo"
    table = profile["source_object"]
    lit_schema, lit_table = strategies._literal(schema), strategies._literal(table)
    if lit_schema is None or lit_table is None:
        raise _unavailable("change_tracking", "name")
    try:
        version = _scalar(tenant_id, profile, "SELECT CHANGE_TRACKING_CURRENT_VERSION() AS v")
        if version is None:
            raise _unavailable("change_tracking", "not_enabled_on_database")
        cursor = profile.get("cursor_value") or {}
        stored = cursor.get("value") if cursor.get("strategy") == "change_tracking" else None
        min_valid = _scalar(tenant_id, profile,
                            f"SELECT CHANGE_TRACKING_MIN_VALID_VERSION(OBJECT_ID({lit_schema} + '.' + {lit_table})) AS v")
        if min_valid is None:
            raise _unavailable("change_tracking", "not_enabled_on_table")
        report["cursor_after"] = {"type": "number", "value": str(version), "strategy": "change_tracking"}
        if not incremental or stored is None or int(min_valid) > int(stored):
            if incremental and stored is not None:
                report["capture_fallback"] = "change_tracking_retention_passed"
            yield from reader.read_batches(tenant_id, {**profile, "mode": "full"}, max_rows=max_rows)
            return
        pk = []
        for batch in __import__("backend.inventory_sync.ledger", fromlist=["_rows"])._rows(
                tenant_id, profile,
                "SELECT kcu.COLUMN_NAME AS c FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
                "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
                "AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY' "
                f"AND tc.TABLE_SCHEMA = {lit_schema} AND tc.TABLE_NAME = {lit_table} ORDER BY kcu.ORDINAL_POSITION"):
            pk += [r["c"] for r in batch]
        if not pk:
            raise _unavailable("change_tracking", "no_primary_key")
        join = " AND ".join(f"t.{q(c)} = ct.{q(c)}" for c in pk)
        sql = (f"SELECT ct.SYS_CHANGE_OPERATION AS stockai_op, t.* FROM CHANGETABLE(CHANGES "
               f"{q(schema)}.{q(table)}, {int(stored)}) AS ct LEFT JOIN {q(schema)}.{q(table)} AS t ON {join}")
        from backend.inventory_sync import ledger
        deletes = 0
        for batch in ledger._rows(tenant_id, profile, sql, max_rows=max_rows):
            live = [r for r in batch if r.get("stockai_op") != "D"]
            deletes += len(batch) - len(live)
            for r in live:
                r.pop("stockai_op", None)
            if live:
                yield live
        report["capture_deletes"] = deletes
    except AppError as exc:
        raise _ct_sql_failed(exc) from None


# ── Oracle ORA_ROWSCN ─────────────────────────────────────────────────────────

def _rowscn(tenant_id, profile, incremental, max_rows, report):
    from backend.datasources import catalog
    from backend.inventory_sync import ledger
    cursor = profile.get("cursor_value") or {}
    stored = cursor.get("value") if cursor.get("strategy") == "oracle_rowscn" else None
    target = catalog.qualified("oracle", profile.get("source_schema"), profile["source_object"])
    sql = f"SELECT t.*, ORA_ROWSCN AS STOCKAI_SCN FROM {target} t"
    if incremental and stored is not None:
        sql += f" WHERE ORA_ROWSCN > {int(stored)}"
    best = int(stored) if stored is not None else None
    for batch in ledger._rows(tenant_id, profile, sql, max_rows=max_rows):
        for r in batch:
            scn = r.pop("STOCKAI_SCN", None) if "STOCKAI_SCN" in r else r.pop("stockai_scn", None)
            if scn is not None and (best is None or int(scn) > best):
                best = int(scn)
        yield batch
    if best is not None:
        report["cursor_after"] = {"type": "number", "value": str(best), "strategy": "oracle_rowscn"}


# ── MongoDB change streams ────────────────────────────────────────────────────

def _token_out(token: Any) -> str:
    from bson import json_util
    return base64.b64encode(json_util.dumps(token).encode()).decode()


def _token_in(text: str) -> Any:
    from bson import json_util
    return json_util.loads(base64.b64decode(text.encode()).decode())


def _change_stream(tenant_id, profile, incremental, max_rows, report):
    from backend.datasources import mongo
    cfg = reader._cfg(tenant_id, profile["data_source_id"])
    cursor = profile.get("cursor_value") or {}
    stored = cursor.get("value") if cursor.get("strategy") == "change_stream" else None
    try:
        with mongo.open_mongo(cfg, tenant_id=tenant_id) as session:
            coll = session.db[profile["source_object"]]
            if not incremental or stored is None:
                # Position the stream BEFORE the full read: changes during the
                # read are replayed next time (the apply is idempotent).
                with coll.watch(max_await_time_ms=100) as stream:
                    token = stream.resume_token
                report["cursor_after"] = {"type": "text", "value": _token_out(token), "strategy": "change_stream"}
            else:
                rows, deletes, token = [], 0, _token_in(stored)
                with coll.watch(resume_after=token, full_document="updateLookup",
                                max_await_time_ms=CHANGE_STREAM_WAIT_MS) as stream:
                    while len(rows) < min(max_rows or CHANGE_STREAM_MAX_EVENTS, CHANGE_STREAM_MAX_EVENTS):
                        ev = stream.try_next()
                        if ev is None:
                            break
                        if ev.get("operationType") in ("insert", "update", "replace") and ev.get("fullDocument"):
                            rows.append(mongo.flatten(ev["fullDocument"]))
                        elif ev.get("operationType") == "delete":
                            deletes += 1
                    token = stream.resume_token
                report["cursor_after"] = {"type": "text", "value": _token_out(token), "strategy": "change_stream"}
                report["capture_deletes"] = deletes
                for i in range(0, len(rows), 1000):
                    yield rows[i:i + 1000]
                return
    except AppError as exc:
        reason = str((exc.params or {}).get("reason") or "").lower()
        if "replica set" in reason or "40573" in reason or "changestream" in reason.replace(" ", ""):
            raise _unavailable("change_stream", "needs_replica_set") from None
        if "resume" in reason or "history" in reason or "oplog" in reason:
            report["capture_fallback"] = "change_stream_history_lost"
            stored = None
        else:
            raise
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "replica set" in text or getattr(exc, "code", None) == 40573:
            raise _unavailable("change_stream", "needs_replica_set") from None
        if getattr(exc, "code", None) in (280, 286) or "resume" in text:
            report["capture_fallback"] = "change_stream_history_lost"
        else:
            raise mongo.classify_mongo(exc).to_app_error() from None
    yield from reader.read_batches(tenant_id, {**profile, "mode": "full", "stock_model": "snapshot"},
                                   max_rows=max_rows)
