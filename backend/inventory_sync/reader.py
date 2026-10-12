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


FUTURE_ALLOWANCE = timedelta(days=1)


def cap_future(wm: Optional[dict]) -> Optional[dict]:
    """A datetime watermark more than a day ahead of now is capped at now (a
    day absorbs any time-zone offset of a naive source clock)."""
    if not wm or wm.get("type") != "datetime":
        return wm
    dt = datetime.fromisoformat(wm["value"])
    now = datetime.now(timezone.utc) if dt.tzinfo else datetime.utcnow()
    if dt > now + FUTURE_ALLOWANCE:
        return {"type": "datetime", "value": now.replace(microsecond=0).isoformat()}
    return wm


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

def _from(profile: dict, engine: str) -> str:
    from backend.datasources import catalog
    if profile["source_kind"] == "table":
        return catalog.qualified(engine, profile.get("source_schema"), profile["source_object"])
    # Newlines around the query: a saved query that ends in a `-- comment`
    # would otherwise comment out the closing parenthesis.
    return f"(\n{profile['source_query'].strip().rstrip(';')}\n) stockai_q"


def raw_select(profile: dict, engine: str) -> str:
    """The profile's table, view or saved query, as a SELECT.

    Projected to the columns the profile uses once `prepare_projection` has
    run (a run / a dry run): a 200-column ERP table with a BLOB or a 1 MB
    notes column never has those columns pulled over the wire. Without it
    (inspection, the mapping assistant) it is SELECT * — those screens need
    every column."""
    from backend.datasources import catalog
    cols = profile.get("_projection")
    select = ", ".join(catalog.quote_ident(engine, c) for c in cols) if cols else "*"
    return f"SELECT {select} FROM {_from(profile, engine)}"


def needed_columns(profile: dict) -> list[str]:
    """Every source column the profile reads: mapped fields, filters, the
    watermark, and the stock model's own columns (ledger id / cursor / sign /
    status / as-of / factor, aggregate group-by)."""
    out: list[str] = []

    def add(c: Any) -> None:
        if isinstance(c, str) and c and c not in out:
            out.append(c)
    for m in (profile.get("mapping") or {}).values():
        if isinstance(m, dict):
            add(m.get("column"))
    for f in profile.get("filters") or []:
        if isinstance(f, dict):
            add(f.get("column"))
    add(profile.get("watermark_column"))
    cfg = profile.get("model_config") or {}
    for k in ("id_column", "cursor_column", "status_column", "as_of_column", "factor_column"):
        add(cfg.get(k))
    sign = cfg.get("sign") or {}
    if isinstance(sign, dict):
        for k in ("type_column", "in_column", "out_column"):
            add(sign.get(k))
    for c in cfg.get("group_by") or []:
        add(c)
    return out


def prepare_projection(tenant_id: str, profile: dict) -> None:
    """Resolve `needed_columns` against the source's real column names (a
    zero-row SELECT: no data read) and store them on the profile dict for
    `raw_select`. Matching is case-insensitive, as the row mapper is; a
    needed column the source lacks is simply not projected, so the run
    reports it exactly as before (schema drift -> paused, column named).
    Any failure here leaves SELECT * in place: the real read then reports
    the real error."""
    profile.pop("_projection", None)
    if profile.get("source_kind") not in ("table", "query") or not profile.get("data_source_id"):
        return
    wanted = needed_columns(profile)
    mapping = profile.get("mapping") or {}
    if not wanted or not (mapping.get("sku") or {}).get("column"):
        return
    try:
        cfg = _cfg(tenant_id, profile["data_source_id"])
        engine = cfg.get("engine") or "postgresql"
        if engine == "mongodb":
            return
        from backend.datasources.service import _read_only_rows
        with _read_only_rows(cfg, f"SELECT * FROM {_from(profile, engine)} WHERE 1 = 0",
                             tenant_id=tenant_id) as result:
            names = [str(c) for c in result.keys()]
    except Exception:  # noqa: BLE001 - see docstring: never a new failure mode
        return
    profile["_source_columns"] = names       # what the dry run's advice inspects
    low = {w.lower() for w in wanted}
    cols = [n for n in names if n.lower() in low]
    if cols and len(cols) < len(names):
        profile["_projection"] = cols


def build_sql(profile: dict, engine: str, watermark: Optional[dict],
              scope: Optional[dict] = None) -> str:
    """The statement a run sends.

    * snapshot: the rows, filtered by the watermark (incremental) and the scope;
    * aggregate: GROUP BY the mapped keys (see `strategies.aggregate_sql`). An
      incremental aggregate re-sums EVERY row of each key that has at least one
      changed row (an EXISTS over the changed rows), never just the changed
      rows: summing only the changed lots would write a partial total."""
    from backend.datasources import catalog
    from backend.inventory_sync import strategies
    q = lambda c: catalog.quote_ident(engine, c)  # noqa: E731
    base = raw_select(profile, engine)
    preds: list[str] = []
    scoped = strategies.scope_predicate(profile, engine, scope)
    if scoped:
        preds.append("src." + scoped)
    model = profile.get("stock_model") or "snapshot"
    if profile.get("mode") == "incremental" and watermark and profile.get("watermark_column"):
        start = _start_point(watermark, int(profile.get("overlap_minutes") or 0))
        lit = _sql_literal(start, engine)
        wm = q(profile["watermark_column"])
        if model == "aggregate":
            mapping = profile.get("mapping") or {}
            keys = [mapping[f]["column"] for f in ("sku", "warehouse") if mapping.get(f, {}).get("column")]
            join = " AND ".join(f"chg.{q(k)} = src.{q(k)}" for k in keys)
            preds.append(f"EXISTS (SELECT 1 FROM ({base}) chg WHERE {join} AND chg.{wm} >= {lit})")
        else:
            preds.append(f"src.{wm} >= {lit}")
    sql = f"SELECT src.* FROM ({base}) src" + (f" WHERE {' AND '.join(preds)}" if preds else "")
    if model == "aggregate":
        sql = strategies.aggregate_sql(profile, engine, sql)
    return sql


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
                 max_rows: Optional[int] = None, scope: Optional[dict] = None) -> Iterator[list[dict]]:
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
    sql = build_sql(profile, engine, watermark, scope)
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
