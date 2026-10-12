"""MongoDB as a read-only data source — the document-engine twin of `client.py`.

A customer's inventory may live in MongoDB instead of a SQL database. This
module is the only place that opens a connection to one, and it keeps every
guarantee `client.open_session` gives the SQL engines:

1. **One slot per call, bounded per tenant** — the same `tenant_slot` gate, so
   SQL and MongoDB connections of one tenant share one ceiling.
2. **The address is checked, then used.** The host is resolved once, every
   address goes through the SSRF policy (`network.py`), and the client connects
   to the checked ADDRESS with `directConnection=True` — no replica-set
   discovery, so the server cannot hand us other hosts to connect to that were
   never checked. (`verify-full` TLS needs the host NAME for certificate
   matching and connects by name in that one mode, as MySQL and Oracle do.)
   `mongodb+srv://` is refused at parse time for the same reason.
3. **TLS as configured**: disable / require (encrypted, certificate not
   checked) / verify-ca / verify-full; an uploaded CA is written to a private
   temp file for the life of the session and deleted.
4. **Timeouts on every layer**: server selection, connect, socket, and
   `maxTimeMS` on every operation.
5. **Read-only by construction.** MongoDB has no read-only session, so the
   module exposes exactly two operations — `find` and `aggregate` — and both
   are validated first: an aggregation may only use the stages in
   `ALLOWED_STAGES` (never `$out` / `$merge`), and no filter or pipeline may
   carry server-side JavaScript (`$where`, `$function`, `$accumulator`).
6. **Nothing lingers**: one client per session, `maxPoolSize=1`, closed on
   every path.
7. **Nothing leaks**: failures leave as coded `AppError`s (`classify_mongo`),
   scrubbed of the password, never as a driver message that could quote the
   connection URI.

Documents are FLATTENED for the column mapper: nested fields become dotted
paths (``stock.on_hand``), arrays of scalars are kept as a list value, and
BSON types (ObjectId, Decimal128, dates) become plain Python values.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator, Optional

from backend.datasources import network
from backend.datasources.client import APP_NAME, resolve_checked, spec_from_stored, tenant_slot
from backend.datasources.connection import ConnectionSpec
from backend.datasources.errors import (
    STAGE_AUTH, STAGE_DNS, STAGE_QUERY, STAGE_TCP, STAGE_TLS, Classified, scrub,
)
from backend.errors import AppError

log = logging.getLogger(__name__)

MAX_COLLECTIONS = 1000
SAMPLE_DOCUMENTS = 200
MAX_FIELDS = 500
MAX_DEPTH = 6

# Aggregation stages that only READ. Anything else — `$out` and `$merge` above
# all — is refused before a connection is opened.
ALLOWED_STAGES = frozenset({
    "$match", "$project", "$addFields", "$set", "$unset", "$lookup", "$unwind",
    "$group", "$sort", "$limit", "$skip", "$count", "$replaceRoot", "$replaceWith",
    "$facet", "$sortByCount", "$bucket", "$bucketAuto",
})
# Operators that run JavaScript on the customer's server.
FORBIDDEN_OPERATORS = frozenset({"$where", "$function", "$accumulator"})
MAX_PIPELINE_STAGES = 50

# Privileges that could CHANGE data. A login holding any is reported by the
# connection test (StockAI never uses them).
_WRITE_ACTIONS = frozenset({
    "insert", "update", "remove", "dropCollection", "dropDatabase", "createCollection",
    "createIndex", "dropIndex", "renameCollectionSameDB", "collMod", "convertToCapped",
    "emptycapped", "bypassDocumentValidation", "dropUser", "createUser", "grantRole",
})


def _reject(code: str, message: str, **params: Any) -> AppError:
    return AppError(code, message, status_code=400, params=params)


# ── Read-only guard ───────────────────────────────────────────────────────────

def _check_no_js(value: Any, depth: int = 0) -> None:
    if depth > 40:
        raise _reject("mongo_query_invalid", "The query is nested too deeply.", reason="too_deep")
    if isinstance(value, dict):
        for key, inner in value.items():
            if not isinstance(key, str):
                raise _reject("mongo_query_invalid", "Field names must be text.", reason="bad_key")
            if "\x00" in key:
                raise _reject("mongo_query_invalid", "A field name contains a NUL byte.",
                              reason="nul")
            if key in FORBIDDEN_OPERATORS:
                raise _reject("mongo_query_forbidden_operator",
                              f"{key} runs code on the server and is not allowed.", operator=key)
            _check_no_js(inner, depth + 1)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            _check_no_js(inner, depth + 1)


def validate_filter(flt: Any) -> dict:
    """A `find` filter: a plain document, no server-side JavaScript."""
    if flt in (None, ""):
        return {}
    if not isinstance(flt, dict):
        raise _reject("mongo_query_invalid", "The filter must be a JSON object.", reason="not_object")
    _check_no_js(flt)
    return flt


def validate_pipeline(pipeline: Any) -> list[dict]:
    """An aggregation pipeline made only of read stages (recursively, so a
    `$facet` or `$lookup` sub-pipeline cannot smuggle a `$merge`)."""
    if not isinstance(pipeline, list) or not pipeline:
        raise _reject("mongo_query_invalid", "The pipeline must be a non-empty JSON array.",
                      reason="not_array")
    if len(pipeline) > MAX_PIPELINE_STAGES:
        raise _reject("mongo_query_invalid", "The pipeline has too many stages.",
                      reason="too_many_stages", max=MAX_PIPELINE_STAGES)
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise _reject("mongo_query_invalid", "Each stage must be an object with one operator.",
                          reason="bad_stage")
        name, body = next(iter(stage.items()))
        if name not in ALLOWED_STAGES:
            raise _reject("mongo_stage_not_allowed",
                          f"The stage {name} is not allowed: only read stages are.",
                          stage=str(name)[:40], allowed=", ".join(sorted(ALLOWED_STAGES)))
        if name == "$lookup" and isinstance(body, dict) and "pipeline" in body:
            validate_pipeline(body["pipeline"]) if body["pipeline"] else None
        if name == "$facet" and isinstance(body, dict):
            for sub in body.values():
                validate_pipeline(sub)
        _check_no_js(body)
    return pipeline


# ── Errors ────────────────────────────────────────────────────────────────────

def classify_mongo(exc: BaseException, *, secrets: tuple[str, ...] = (), host: str = "",
                   database: str = "", during: str = STAGE_QUERY) -> Classified:
    """Any pymongo failure as a coded `Classified` (same codes as the SQL
    engines, so the frontend's sentences apply unchanged)."""
    if isinstance(exc, AppError):
        return Classified(exc.code, during, False, exc.status_code, dict(exc.params))
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return Classified("data_source_driver_missing", "driver", False, 503, {"engine": "mongodb"})
    text = str(exc)
    low = text.lower()
    reason = scrub(text, secrets)
    name = type(exc).__name__
    code = getattr(exc, "code", None)

    if name == "ConfigurationError":
        return Classified("data_source_connect_failed", during, False, 400, {"reason": reason})
    if code in (18, 13) and ("auth" in low or code == 18):
        return Classified("data_source_auth_failed", STAGE_AUTH, False, 400, {})
    if code == 13 or "not authorized" in low or "unauthorized" in low:
        return Classified("data_source_permission_denied",
                          STAGE_AUTH if during == STAGE_AUTH else STAGE_QUERY, False, 400,
                          {"reason": reason})
    if code == 50 or "exceeded time limit" in low or name == "ExecutionTimeout":
        return Classified("data_source_query_timeout", STAGE_QUERY, False, 504, {})
    if "authentication failed" in low or name == "OperationFailure" and "auth" in low:
        return Classified("data_source_auth_failed", STAGE_AUTH, False, 400, {})
    if "ssl" in low or "tls" in low or "certificate" in low:
        return Classified("data_source_tls_failed", STAGE_TLS, False, 400, {"reason": reason})
    if name in ("ServerSelectionTimeoutError", "NetworkTimeout") or "timed out" in low:
        if "refused" in low:
            return Classified("data_source_host_unreachable", STAGE_TCP, False, 400,
                              {"host": host, "reason": "refused"})
        if during == STAGE_QUERY and name == "NetworkTimeout":
            return Classified("data_source_query_timeout", STAGE_QUERY, False, 504, {})
        return Classified("data_source_connect_timeout", STAGE_TCP, True, 400, {"host": host})
    if name == "AutoReconnect" or name == "ConnectionFailure" or "connection reset" in low:
        return Classified("data_source_connection_lost", STAGE_QUERY, True, 503, {})
    if "getaddrinfo" in low or "name or service not known" in low:
        return Classified("data_source_dns_failed", STAGE_DNS, False, 400,
                          {"host": host, "reason": "not_found"})
    if during == STAGE_QUERY:
        return Classified("mongo_query_failed", STAGE_QUERY, False, 400, {"reason": reason})
    return Classified("data_source_connect_failed", during, False, 400, {"reason": reason})


# ── Sessions ──────────────────────────────────────────────────────────────────

@dataclass
class MongoSession:
    client: Any
    db: Any
    spec: ConnectionSpec
    timeout_ms: int


@contextmanager
def _ca_temp(spec: ConnectionSpec) -> Iterator[Optional[str]]:
    if not spec.ssl_ca:
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="stockai-mongo-ca-", suffix=".pem")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(spec.ssl_ca)
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - Windows ignores POSIX modes
            pass
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            log.warning("Mongo source: could not delete the temporary CA file")


def client_kwargs(spec: ConnectionSpec, address: Optional[str], timeout_s: int,
                  ca_path: Optional[str]) -> dict:
    """What `MongoClient` is constructed with. Pure, so the TLS / address
    choices are unit-testable without a server."""
    by_name = spec.ssl_mode == "verify-full" or not address
    kwargs: dict[str, Any] = dict(
        host=spec.host if by_name else address, port=spec.port,
        directConnection=True,
        appname=APP_NAME,
        connect=False,
        maxPoolSize=1, minPoolSize=0,
        retryWrites=False, retryReads=True,
        readPreference="primaryPreferred",
        serverSelectionTimeoutMS=spec.connect_timeout_s * 1000,
        connectTimeoutMS=spec.connect_timeout_s * 1000,
        socketTimeoutMS=(timeout_s + 15) * 1000,
        uuidRepresentation="standard",
    )
    if spec.username:
        kwargs.update(username=spec.username, password=spec.password,
                      authSource=spec.auth_source or "admin")
    if spec.ssl_mode == "disable":
        kwargs["tls"] = False
    else:
        kwargs["tls"] = True
        if spec.ssl_mode == "require":
            kwargs["tlsInsecure"] = True
        elif spec.ssl_mode == "verify-ca":
            kwargs["tlsAllowInvalidHostnames"] = True
        if ca_path:
            kwargs["tlsCAFile"] = ca_path
    return kwargs


@contextmanager
def open_mongo(cfg_or_spec: Any, *, tenant_id: Optional[str],
               statement_timeout_s: Optional[int] = None, bulk: bool = False) -> Iterator[MongoSession]:
    """A session on the customer's MongoDB database. See the module doc."""
    spec = cfg_or_spec if isinstance(cfg_or_spec, ConnectionSpec) else spec_from_stored(cfg_or_spec)
    if spec.engine != "mongodb":
        raise _reject("data_source_engine_unsupported", "Not a MongoDB source.",
                      engine=spec.engine, allowed="mongodb")
    timeout_s = statement_timeout_s or (spec.bulk_timeout_s if bulk else spec.statement_timeout_s)
    with tenant_slot(tenant_id):
        addresses = resolve_checked(spec)
        with _ca_temp(spec) as ca_path:
            try:
                from pymongo import MongoClient
                client = MongoClient(**client_kwargs(spec, addresses[0] if addresses else None,
                                                     timeout_s, ca_path))
            except Exception as exc:  # noqa: BLE001
                raise classify_mongo(exc, secrets=spec.secrets, host=spec.host,
                                     during=STAGE_AUTH).to_app_error() from None
            try:
                try:
                    client.admin.command("ping")
                    db = client[spec.database]
                    # Authentication is lazy: touch the database once so a wrong
                    # password fails HERE (as auth), not halfway through a read.
                    db.list_collection_names(filter={"name": "__stockai_probe__"})
                except Exception as exc:  # noqa: BLE001
                    raise classify_mongo(exc, secrets=spec.secrets, host=spec.host,
                                         database=spec.database,
                                         during=STAGE_AUTH).to_app_error() from None
                yield MongoSession(client, db, spec, timeout_s * 1000)
            finally:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass


# ── Values and flattening ─────────────────────────────────────────────────────

def plain_value(value: Any) -> Any:
    """A BSON value as plain Python (what the mapper and JSON can hold)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    kind = type(value).__name__
    if kind == "Decimal128":
        return value.to_decimal()
    if kind == "Int64":
        return int(value)
    if kind in ("ObjectId", "UUID", "Timestamp", "Regex", "Code", "DBRef", "MinKey", "MaxKey"):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def flatten(doc: dict, prefix: str = "", depth: int = 0, out: Optional[dict] = None) -> dict:
    """``{"a": {"b": 1}, "tags": ["x"]}`` -> ``{"a.b": 1, "tags": ["x"]}``."""
    out = {} if out is None else out
    for key, value in doc.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and depth < MAX_DEPTH:
            flatten(value, path + ".", depth + 1, out)
        elif isinstance(value, (list, tuple)):
            if all(not isinstance(v, (dict, list, tuple)) for v in value):
                out[path] = [plain_value(v) for v in value]
            else:
                # An array of sub-documents is not a column; `$unwind` it in a
                # pipeline to read it row by row.
                out[path] = f"[{len(value)} items]"
        else:
            out[path] = plain_value(value)
    return out


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, (float, Decimal)):
        return "decimal"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, list):
        return "array"
    return "string"


# ── Catalogue ─────────────────────────────────────────────────────────────────

def list_collections(session: MongoSession, cap: int = MAX_COLLECTIONS) -> dict:
    names = sorted(n for n in session.db.list_collection_names() if not n.startswith("system."))
    truncated = len(names) > cap
    items = []
    for name in names[:cap]:
        try:
            estimate = int(session.db[name].estimated_document_count(maxTimeMS=session.timeout_ms))
        except Exception:  # noqa: BLE001 - a view has no count; the name is still listed
            estimate = None
        items.append({"schema": session.spec.database, "name": name, "kind": "collection",
                      "row_estimate": estimate, "select_sql": None})
    return {"tables": items, "truncated": truncated, "cap": cap}


def sample_fields(session: MongoSession, collection: str, sample: int = SAMPLE_DOCUMENTS) -> dict:
    """Infer the columns of a collection from a sample of its documents:
    every dotted path seen, its dominant type, and how often it is present."""
    docs = list(session.db[collection].find({}, limit=sample, max_time_ms=session.timeout_ms))
    seen: dict[str, dict] = {}
    for doc in docs:
        for path, value in flatten(doc).items():
            entry = seen.setdefault(path, {"count": 0, "types": {}, "samples": []})
            entry["count"] += 1
            t = _type_name(value)
            entry["types"][t] = entry["types"].get(t, 0) + 1
            if value is not None and len(entry["samples"]) < 3 and not isinstance(value, list):
                entry["samples"].append(str(value)[:60])
    columns = []
    for path, entry in list(seen.items())[:MAX_FIELDS]:
        dominant = max(entry["types"].items(), key=lambda kv: kv[1])[0]
        columns.append({
            "name": path, "type": dominant,
            "nullable": entry["count"] < len(docs),
            "presence": round(entry["count"] / len(docs), 3) if docs else 0.0,
            "samples": entry["samples"],
        })
    return {"schema": session.spec.database, "table": collection, "truncated": len(seen) > MAX_FIELDS,
            "sampled": len(docs), "columns": columns}


def iterate_documents(session: MongoSession, collection: str, *, flt: Optional[dict] = None,
                      pipeline: Optional[list] = None, batch_size: int = 1000,
                      limit: Optional[int] = None) -> Iterator[dict]:
    """Stream flattened rows from `find` (a filter) or `aggregate` (a
    pipeline), never holding the whole result."""
    coll = session.db[collection]
    if pipeline:
        stages = list(validate_pipeline(pipeline))
        if limit:
            stages.append({"$limit": int(limit)})
        cursor = coll.aggregate(stages, batchSize=batch_size, maxTimeMS=session.timeout_ms,
                                allowDiskUse=False)
    else:
        cursor = coll.find(validate_filter(flt), batch_size=batch_size,
                           max_time_ms=session.timeout_ms, limit=int(limit or 0))
    try:
        for doc in cursor:
            yield flatten(doc)
    finally:
        cursor.close()


# ── Connection test ───────────────────────────────────────────────────────────

def run_mongo_probe(cfg: dict, *, tenant_id: Optional[str]) -> dict:
    """The staged connection test for a MongoDB source, in the same shape as
    `probe.run_probe` so the screen renders it unchanged."""
    from backend.datasources.probe import FAILED, OK, SKIPPED, WARNING, _Report

    report = _Report()
    extra: dict[str, Any] = {"write_access": None, "grant_sql": None, "tables_sample": [],
                             "table_count": None, "server_version": None,
                             "read_only_session": False}
    started = time.monotonic()
    try:
        spec = spec_from_stored(cfg)
    except AppError as exc:
        report.set("auth", FAILED, started, code=exc.code, params=exc.params)
        return report.finish(**extra)

    with tenant_slot(tenant_id):
        started = time.monotonic()
        try:
            addresses = network.resolve_allowed(spec.host_name, spec.port,
                                                allow_private=network.allow_private_hosts())
        except AppError as exc:
            report.set("dns", FAILED, started, code=exc.code, params=exc.params)
            return report.finish(**extra)
        report.set("dns", OK, started, detail={"addresses": [ip.compressed for ip in addresses][:4]})

        started = time.monotonic()
        ip, failure = network.tcp_reachable(addresses, spec.port,
                                            timeout_s=min(float(spec.connect_timeout_s), 8.0))
        if ip is None:
            code = "data_source_connect_timeout" if failure == "timeout" else "data_source_host_unreachable"
            report.set("tcp", FAILED, started, code=code,
                       params={"host": spec.host_name, "port": spec.port, "reason": failure})
            return report.finish(**extra)
        report.set("tcp", OK, started, detail={"address": ip.compressed, "port": spec.port})

        started = time.monotonic()
        with _ca_temp(spec) as ca_path:
            client = None
            try:
                from pymongo import MongoClient
                client = MongoClient(**client_kwargs(spec, ip.compressed,
                                                     min(spec.statement_timeout_s, 15), ca_path))
                info = client.admin.command("buildInfo")
                extra["server_version"] = str(info.get("version") or "")[:20] or None
                db = client[spec.database]
                names = sorted(n for n in db.list_collection_names() if not n.startswith("system."))
            except Exception as exc:  # noqa: BLE001
                kind = classify_mongo(exc, secrets=spec.secrets, host=spec.host_name,
                                      database=spec.database, during=STAGE_AUTH)
                if kind.stage == STAGE_TLS:
                    report.set("tls", FAILED, started, code=kind.code, params=kind.params)
                else:
                    if spec.ssl_mode != "disable":
                        report.set("tls", OK, None)
                    else:
                        report.set("tls", WARNING, None, code="data_source_tls_disabled")
                    stage = kind.stage if kind.stage in ("dns", "tcp") else "auth"
                    report.set(stage, FAILED, started, code=kind.code, params=kind.params)
                if client is not None:
                    client.close()
                return report.finish(**extra)
            try:
                if spec.ssl_mode != "disable":
                    report.set("tls", OK, None, detail={"mode": spec.ssl_mode})
                else:
                    report.set("tls", WARNING, None, code="data_source_tls_disabled",
                               detail={"mode": spec.ssl_mode})
                report.set("auth", OK, started)

                started = time.monotonic()
                try:
                    status = db.command("connectionStatus", showPrivileges=True)
                    actions = {a for p in (status.get("authInfo") or {}).get("authenticatedUserPrivileges") or []
                               for a in p.get("actions") or []}
                    users = (status.get("authInfo") or {}).get("authenticatedUsers") or []
                    can_write = bool(actions & _WRITE_ACTIONS) or not users
                    extra["write_access"] = {"can_write": can_write,
                                             "privileges": sorted(actions & _WRITE_ACTIONS)[:20]}
                    if can_write:
                        # Least privilege for a MongoDB login is the built-in
                        # `read` role on the one database.
                        extra["grant_sql"] = (
                            f'db.getSiblingDB("{spec.auth_source or "admin"}").createUser('
                            f'{{user: "stockai_ro", pwd: "<password>", roles: '
                            f'[{{role: "read", db: "{spec.database}"}}]}})')
                        report.set("privileges", WARNING, started, code="data_source_login_can_write",
                                   params={"privileges": ", ".join(sorted(actions & _WRITE_ACTIONS)[:10])
                                           or "unauthenticated"})
                    else:
                        report.set("privileges", OK, started)
                except Exception:  # noqa: BLE001 - not every server exposes it
                    report.set("privileges", WARNING, started, code="data_source_privileges_unknown")

                started = time.monotonic()
                # MongoDB has no read-only session: reads are guaranteed by
                # this module only ever calling find/aggregate (validated).
                report.set("select", OK, started, code="data_source_read_only_by_client",
                           detail={"read_only_session": None})
                extra["table_count"] = len(names)
                extra["tables_sample"] = names[:10]
                if names:
                    report.set("tables", OK, started, params={"count": len(names)})
                else:
                    report.set("tables", WARNING, started, code="data_source_no_tables_visible")
            finally:
                client.close()
    return report.finish(**extra)
