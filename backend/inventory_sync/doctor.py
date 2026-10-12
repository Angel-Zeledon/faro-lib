"""The connection doctor: one button, staged checks, the first failure named
with its remedy, and a diagnostic bundle a developer can paste to their DBA.

Stages (each ok / warning / failed / skipped, with a code, params, timing and
a remedy key `isync.remedy.<code>`):

    source      the data source exists and was tested
    connection  dns, tcp, tls, auth, privileges, select, tables (the staged
                connection test of `datasources/probe.py`, or MongoDB's)
    object      the table / view / collection exists (or the saved query runs)
    columns     every mapped, filter, watermark and ledger column exists
    sample      a bounded sample read, with its timing
    estimate    the row-count estimate (from the engine's statistics)
    index       incremental reads filter on an indexed column (else: the exact
                CREATE INDEX to run)
    capture     which change-capture strategy this source supports, recommended

Nothing in the result or the bundle is a secret: hosts and users yes,
passwords and certificates never.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from backend.config import settings
from backend.db.connection import query_one
from backend.errors import AppError
from backend.inventory_sync import failures

OK, WARNING, FAILED, SKIPPED = "ok", "warning", "failed", "skipped"


def _stage(stages: list, name: str, status: str, started: Optional[float] = None, *,
           code: Optional[str] = None, params: Optional[dict] = None, detail: Any = None) -> None:
    entry: dict[str, Any] = {"stage": name, "status": status}
    if code:
        entry["code"] = code
        entry["remedy_key"] = f"isync.remedy.{code}"
        entry["category"] = failures.outcome_of(code).category
    if params:
        entry["params"] = failures.public_params(params)
    if detail is not None:
        entry["detail"] = detail
    if started is not None:
        entry["duration_ms"] = int((time.monotonic() - started) * 1000)
    stages.append(entry)


def _columns_needed(profile: dict) -> list[str]:
    cols = [m.get("column") for m in (profile.get("mapping") or {}).values() if m.get("column")]
    cols += [f["column"] for f in profile.get("filters") or []]
    if profile.get("watermark_column"):
        cols.append(profile["watermark_column"])
    cfg = profile.get("model_config") or {}
    if (profile.get("stock_model") or "") == "ledger":
        for k in ("id_column", "cursor_column", "status_column", "as_of_column", "factor_column"):
            if cfg.get(k):
                cols.append(cfg[k])
        sign = cfg.get("sign") or {}
        cols += [sign[k] for k in ("type_column", "in_column", "out_column") if sign.get(k)]
    return sorted({c for c in cols if c})


_INDEX_SQL = {
    "postgresql": ("SELECT COUNT(*) AS n FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid "
                   "JOIN pg_namespace s ON s.oid = t.relnamespace JOIN pg_attribute a ON a.attrelid = t.oid "
                   "AND a.attnum = i.indkey[0] WHERE s.nspname = {schema} AND t.relname = {table} "
                   "AND a.attname = {column}"),
    "mysql": ("SELECT COUNT(*) AS n FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = {schema} "
              "AND TABLE_NAME = {table} AND COLUMN_NAME = {column} AND SEQ_IN_INDEX = 1"),
    "mssql": ("SELECT COUNT(*) AS n FROM sys.index_columns ic JOIN sys.columns c ON c.object_id = ic.object_id "
              "AND c.column_id = ic.column_id WHERE ic.object_id = OBJECT_ID({schema} + '.' + {table}) "
              "AND c.name = {column} AND ic.key_ordinal = 1"),
    "oracle": ("SELECT COUNT(*) AS n FROM all_ind_columns WHERE table_owner = {schema} AND table_name = {table} "
               "AND column_name = {column} AND column_position = 1"),
}


def _index_check(tenant_id: str, profile: dict, engine: str, stages: list) -> None:
    from backend.datasources import catalog
    from backend.inventory_sync import ledger, strategies
    column = profile.get("watermark_column")
    if (profile.get("stock_model") or "") == "ledger":
        column = (profile.get("model_config") or {}).get("cursor_column")
    started = time.monotonic()
    if not column or profile.get("source_kind") != "table" or engine not in _INDEX_SQL:
        _stage(stages, "index", SKIPPED, started)
        return
    schema = profile.get("source_schema") or {"postgresql": "public", "mssql": "dbo"}.get(engine) \
        or (query_one("SELECT sql_config->>'database' AS d FROM datasets WHERE id = %s",
                      (profile["data_source_id"],)) or {}).get("d")
    lits = {k: strategies._literal(v) for k, v in
            {"schema": schema, "table": profile["source_object"], "column": column}.items()}
    if any(v is None for v in lits.values()):
        _stage(stages, "index", SKIPPED, started)
        return
    try:
        n = None
        for batch in ledger._rows(tenant_id, profile, _INDEX_SQL[engine].format(**lits)):
            n = int(next(iter(batch[0].values()))) if batch else 0
        if n:
            _stage(stages, "index", OK, started, detail={"column": column})
        else:
            target = catalog.qualified(engine, profile.get("source_schema"), profile["source_object"])
            _stage(stages, "index", WARNING, started, code="inventory_sync_filter_not_indexed",
                   params={"column": column},
                   detail={"suggestion": f"CREATE INDEX stockai_{column.lower()[:40]}_idx ON {target} "
                                         f"({catalog.quote_ident(engine, column)});"})
    except AppError as exc:
        _stage(stages, "index", WARNING, started, code="data_source_privileges_unknown", params=exc.params)


def _capture_check(tenant_id: str, profile: dict, engine: str, stages: list) -> None:
    """Which engine-native change capture this source supports."""
    from backend.inventory_sync import capture, ledger, strategies
    started = time.monotonic()
    recommended, available = "full", []
    if profile.get("watermark_column"):
        available.append("watermark")
        recommended = "watermark"
    if (profile.get("stock_model") or "") == "ledger":
        available.append("ledger_cursor")
        recommended = "ledger_cursor"
    try:
        if engine == "mssql" and profile.get("source_kind") == "table":
            lit_s = strategies._literal(profile.get("source_schema") or "dbo")
            lit_t = strategies._literal(profile["source_object"])
            v = None
            for b in ledger._rows(tenant_id, profile,
                                  f"SELECT CHANGE_TRACKING_MIN_VALID_VERSION(OBJECT_ID({lit_s} + '.' + {lit_t})) AS v"):
                v = next(iter(b[0].values())) if b else None
            if v is not None:
                available.append("change_tracking")
                recommended = "change_tracking"
        elif engine == "oracle" and profile.get("source_kind") == "table":
            available.append("oracle_rowscn")
            if recommended == "full":
                recommended = "oracle_rowscn"
        elif engine == "mongodb":
            from backend.datasources import mongo
            from backend.inventory_sync import reader
            with mongo.open_mongo(reader._cfg(tenant_id, profile["data_source_id"]), tenant_id=tenant_id) as s:
                hello = s.client.admin.command("hello")
            if hello.get("setName"):
                available.append("change_stream")
                recommended = "change_stream"
    except Exception:  # noqa: BLE001 - capability probing is advice, never a failure
        pass
    _stage(stages, "capture", OK, started, detail={"available": available, "recommended": recommended})


def run(tenant_id: str, profile: dict) -> dict:
    """The staged diagnosis of a profile (saved or unsaved configuration)."""
    from backend.datasources import probe, service as ds
    from backend.inventory_sync import reader, strategies
    stages: list[dict] = []
    started_all = time.monotonic()
    engine = None
    src = None
    if profile.get("source_kind") == "push":
        _stage(stages, "source", OK, detail={"kind": "push"})
    else:
        t0 = time.monotonic()
        src = ds.get_source(tenant_id, profile.get("data_source_id") or "")
        if not src:
            _stage(stages, "source", FAILED, t0, code="data_source_not_found")
        else:
            engine = (src.get("sql_config") or {}).get("engine")
            _stage(stages, "source", OK, t0, detail={"engine": engine, "status": src.get("connection_status")})
    if src:
        t0 = time.monotonic()
        result = probe.run_probe(src.get("sql_config") or {}, tenant_id=tenant_id)
        for s in result["stages"]:
            entry = dict(s)
            if entry.get("code"):
                entry["remedy_key"] = f"isync.remedy.{entry['code']}"
                entry["category"] = failures.outcome_of(entry["code"]).category
            entry["stage"] = f"connection.{entry['stage']}"
            stages.append(entry)
        if result["ok"] and src.get("connection_status") != "connected":
            from backend.db.connection import execute
            execute("UPDATE datasets SET connection_status = 'connected' WHERE id = %s AND tenant_id = %s",
                    (src["id"], tenant_id))
        if result["ok"]:
            prof = {**profile, "engine": engine}
            t0 = time.monotonic()
            sample: list[dict] = []
            try:
                for batch in reader.read_batches(tenant_id, {**prof, "stock_model": "snapshot", "mode": "full"},
                                                 limit=20, batch_size=20):
                    sample.extend(batch)
                _stage(stages, "object", OK, t0)
                _stage(stages, "sample", OK, t0, detail={"rows": len(sample)})
            except AppError as exc:
                code, params = failures.refine(exc.code, exc.params)
                _stage(stages, "object", FAILED, t0, code=code, params=params)
            if sample:
                t0 = time.monotonic()
                low = {k.lower() for r in sample for k in r}
                missing = [c for c in _columns_needed(prof) if c.lower() not in low]
                if missing:
                    _stage(stages, "columns", FAILED, t0, code="inventory_sync_column_missing",
                           params={"column": missing[0], "columns": ", ".join(missing)})
                else:
                    _stage(stages, "columns", OK, t0, detail={"checked": len(_columns_needed(prof))})
            t0 = time.monotonic()
            try:
                if profile.get("source_kind") in ("table", "collection"):
                    listing = ds.get_schema(tenant_id, src["id"])
                    est = next((t.get("row_estimate") for t in listing.get("tables") or []
                                if t.get("name") == profile.get("source_object")), None)
                    _stage(stages, "estimate", OK, t0, detail={"rows": est})
                else:
                    _stage(stages, "estimate", SKIPPED, t0)
            except AppError:
                _stage(stages, "estimate", SKIPPED, t0)
            _index_check(tenant_id, prof, engine, stages)
            _capture_check(tenant_id, prof, engine, stages)
    first = next((s for s in stages if s["status"] == FAILED), None)
    egress = [ip.strip() for ip in (getattr(settings, "inventory_sync_egress_ips", "") or "").split(",") if ip.strip()]
    return {
        "ok": first is None, "first_failure": first, "stages": stages,
        "elapsed_ms": int((time.monotonic() - started_all) * 1000),
        "egress_ips": egress,
        "diagnostic": bundle(tenant_id, profile, src, stages),
    }


def bundle(tenant_id: str, profile: dict, src: Optional[dict], stages: list) -> dict:
    """What a developer pastes to their DBA or to us. No secrets."""
    cfg = (src or {}).get("sql_config") or {}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": {k: profile.get(k) for k in ("id", "slug", "name", "source_kind", "source_schema",
                                                 "source_object", "stock_model", "mode", "freshness",
                                                 "strategy", "health", "health_reason", "last_error_code")},
        "source": {k: cfg.get(k) for k in ("engine", "host", "port", "database", "username", "ssl_mode",
                                            "connect_timeout_s", "statement_timeout_s")},
        "stages": [{k: s.get(k) for k in ("stage", "status", "code", "params", "duration_ms")} for s in stages],
    }
