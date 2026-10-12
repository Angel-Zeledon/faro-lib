"""Reading a profile's source in bounded batches.

Every read goes through the existing read-only doors — SQL through
`datasources.service._read_only_rows` (sql_guard + `client.open_session`),
MongoDB through `datasources.mongo.open_mongo` (validated find/aggregate) —
so the sync inherits the SSRF check, TLS, timeouts, the per-tenant connection
gate and the secret scrubbing instead of re-implementing any of them.

The incremental watermark is the only value that ever becomes part of a
statement, and it is never text a person typed: it is the largest value of the
watermark column the previous successful run READ, kept as ``{"type", "value"}``
and rendered here only as a validated number or timestamp literal.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator, Optional

from backend.errors import AppError

BATCH = 1000
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")
_NUM = re.compile(r"^-?\d{1,30}(\.\d{1,12})?$")


def _cfg(tenant_id: str, data_source_id: str) -> dict:
    from backend.datasources.service import get_source
    src = get_source(tenant_id, data_source_id)
    if not src:
        raise AppError("data_source_not_found", "Data source not found", status_code=404,
                       params={"source_id": data_source_id})
    if src.get("connection_status") != "connected":
        raise AppError("data_source_not_connected",
                       "This data source is not connected. Test the connection first.",
                       status_code=400)
    return src.get("sql_config") or {}


# ── Watermarks ────────────────────────────────────────────────────────────────

def watermark_of(value: Any) -> Optional[dict]:
    """A cell of the watermark column as a comparable ``{"type", "value"}``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.replace(microsecond=0).isoformat()}
    if isinstance(value, date):
        return {"type": "datetime", "value": datetime(value.year, value.month, value.day).isoformat()}
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return {"type": "number", "value": str(value)}
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return {"type": "datetime", "value": parsed.replace(microsecond=0).isoformat()}
    except ValueError:
        pass
    if _NUM.match(text):
        return {"type": "number", "value": text}
    return {"type": "text", "value": text}


def _key(wm: dict) -> Any:
    if wm["type"] == "number":
        return float(wm["value"])
    if wm["type"] == "datetime":
        dt = datetime.fromisoformat(wm["value"])
        return dt.replace(tzinfo=None) if dt.tzinfo is None else dt.astimezone(timezone.utc).replace(tzinfo=None)
    return wm["value"]


def max_watermark(a: Optional[dict], b: Optional[dict]) -> Optional[dict]:
    if a is None:
        return b
    if b is None or a["type"] != b["type"]:
        return a
    return b if _key(b) > _key(a) else a


def _start_point(wm: dict, overlap_minutes: int) -> dict:
    if wm["type"] == "datetime":
        dt = datetime.fromisoformat(wm["value"]) - timedelta(minutes=overlap_minutes)
        return {"type": "datetime", "value": dt.isoformat()}
    return wm


def _sql_literal(wm: dict, engine: str) -> str:
    if wm["type"] == "number":
        if not _NUM.match(wm["value"]):
            raise AppError("inventory_sync_watermark_unsupported",
                           "The watermark value cannot be used in a query.", status_code=422)
        return wm["value"]
    if wm["type"] == "datetime":
        dt = datetime.fromisoformat(wm["value"])
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        text = dt.strftime("%Y-%m-%d %H:%M:%S")
        if not _TS.match(text):
            raise AppError("inventory_sync_watermark_unsupported",
                           "The watermark value cannot be used in a query.", status_code=422)
        return f"TIMESTAMP '{text}'" if engine in ("oracle", "postgresql") else f"'{text}'"
    raise AppError("inventory_sync_watermark_unsupported",
                   "An incremental sync needs a date/time or numeric column to follow.",
                   status_code=422, params={"type": wm["type"]})


# ── Statements ────────────────────────────────────────────────────────────────

def build_sql(profile: dict, engine: str, watermark: Optional[dict]) -> str:
    """The statement a run sends. Table sources quote the identifier; query
    sources wrap the saved query; the incremental predicate is appended only
    with a stored watermark."""
    from backend.datasources import catalog
    if profile["source_kind"] == "table":
        base = f"SELECT * FROM {catalog.qualified(engine, profile.get('source_schema'), profile['source_object'])}"
        alias = None
    else:
        base = f"SELECT * FROM ({profile['source_query'].strip().rstrip(';')}) stockai_src"
        alias = "stockai_src"
    if profile.get("mode") == "incremental" and watermark and profile.get("watermark_column"):
        col = catalog.quote_ident(engine, profile["watermark_column"])
        if alias:
            col = f"{alias}.{col}"
        start = _start_point(watermark, int(profile.get("overlap_minutes") or 0))
        base += f" WHERE {col} >= {_sql_literal(start, engine)}"
    return base


def _mongo_value(wm: dict) -> Any:
    if wm["type"] == "number":
        v = float(wm["value"])
        return int(v) if v.is_integer() else v
    if wm["type"] == "datetime":
        dt = datetime.fromisoformat(wm["value"])
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return wm["value"]


# ── Reading ───────────────────────────────────────────────────────────────────

def read_batches(tenant_id: str, profile: dict, *, watermark: Optional[dict] = None,
                 limit: Optional[int] = None, batch_size: int = BATCH,
                 max_rows: Optional[int] = None) -> Iterator[list[dict]]:
    """Yield lists of raw row dicts from the source. `limit` stops early (the
    dry run's sample); `max_rows` makes reading past it a failure — the run
    never silently truncates a source."""
    cfg = _cfg(tenant_id, profile["data_source_id"])
    engine = cfg.get("engine") or "postgresql"
    read = 0

    def _over(n: int) -> None:
        if max_rows is not None and n > max_rows:
            raise AppError("inventory_sync_too_many_rows",
                           f"The source returned more than {max_rows} rows, the most one run "
                           "may read. Narrow the query.", status_code=422,
                           params={"max_rows": max_rows})

    if engine == "mongodb":
        from backend.datasources import mongo
        flt: Optional[dict] = None
        pipeline: Optional[list] = None
        if profile["source_kind"] == "pipeline":
            pipeline = list(json.loads(profile.get("source_query") or "[]"))
        elif profile.get("source_query"):
            flt = json.loads(profile["source_query"])
        if profile.get("mode") == "incremental" and watermark and profile.get("watermark_column"):
            start = _start_point(watermark, int(profile.get("overlap_minutes") or 0))
            cond = {profile["watermark_column"]: {"$gte": _mongo_value(start)}}
            if pipeline is not None:
                pipeline = [{"$match": cond}] + pipeline
            else:
                flt = {"$and": [flt, cond]} if flt else cond
        try:
            with mongo.open_mongo(cfg, tenant_id=tenant_id, bulk=True) as session:
                batch: list[dict] = []
                for row in mongo.iterate_documents(session, profile["source_object"], flt=flt,
                                                   pipeline=pipeline, batch_size=batch_size,
                                                   limit=limit):
                    batch.append(row)
                    read += 1
                    _over(read)
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
                if batch:
                    yield batch
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            from backend.datasources.service import _secrets_of
            raise mongo.classify_mongo(exc, secrets=_secrets_of(cfg)).to_app_error() from None
        return

    from backend.datasources.service import _classified, _read_only_rows
    sql = build_sql(profile, engine, watermark)
    try:
        with _read_only_rows(cfg, sql, stream=True, tenant_id=tenant_id, bulk=True) as result:
            columns = [str(c) for c in result.keys()]
            while True:
                want = batch_size if limit is None else min(batch_size, limit - read)
                if want <= 0:
                    break
                chunk = result.fetchmany(want)
                if not chunk:
                    break
                read += len(chunk)
                _over(read)
                yield [dict(zip(columns, r)) for r in chunk]
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _classified(exc, cfg) from None


def sample_columns(tenant_id: str, profile: dict, rows: int = 50) -> dict:
    """Columns of a source with sample values, inferred from its first rows —
    what the mapping assistant needs for a query or pipeline source."""
    sample: list[dict] = []
    for batch in read_batches(tenant_id, profile, limit=rows, batch_size=rows):
        sample.extend(batch)
    names: list[str] = []
    for r in sample:
        for k in r:
            if k not in names:
                names.append(k)
    columns = []
    for n in names:
        values = [r.get(n) for r in sample if r.get(n) not in (None, "")]
        kind = "number" if values and all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
                                          for v in values) else ""
        columns.append({"name": n, "type": kind,
                        "samples": [str(v)[:60] for v in values[:3]]})
    return {"columns": columns, "sampled": len(sample)}
