"""The staged connection test behind the "Test connection" button.

"Connection failed: <driver text>" told a person that something, somewhere,
was wrong. This runs the connection one layer at a time and reports which
layer stopped it, so the answer to "why" is in the result:

    dns         the host name resolves, and every address it resolves to is
                one this installation may connect to (SSRF policy)
    tcp         something accepts a TCP connection on that address and port
    tls         TLS negotiated as the configured mode requires
    auth        the server accepted the user, the password and the database
    privileges  the login can NOT write (a warning, with the least-privilege
                GRANT statements, when it can)
    select      a read-only session runs SELECT 1
    tables      the tables and views the login can read (count and a sample)

Each stage is ``ok``, ``warning`` (works, but tell the person), ``failed`` or
``skipped`` (an earlier stage failed). A failed stage carries a `code` and
`params` the frontend renders as a sentence; nothing in the result is a raw
driver message, and nothing in it is a secret. The probe never retries — a
test should report the first failure, not hide it — and never raises except
`data_source_busy` when the tenant's connection slots are all in use.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from backend.datasources import catalog, network
from backend.datasources.client import (
    _ca_file, make_engine, spec_from_stored, tenant_slot,
)
from backend.datasources.errors import STAGE_TLS, classify
from backend.errors import AppError

STAGES = ("dns", "tcp", "tls", "auth", "privileges", "select", "tables")
OK, WARNING, FAILED, SKIPPED = "ok", "warning", "failed", "skipped"
TABLE_SAMPLE = 10


class _Report:
    def __init__(self) -> None:
        self.stages: dict[str, dict] = {}

    def set(self, stage: str, status: str, started: Optional[float] = None, *,
            code: Optional[str] = None, params: Optional[dict] = None,
            detail: Any = None) -> None:
        entry: dict[str, Any] = {"stage": stage, "status": status}
        if code:
            entry["code"] = code
        if params:
            entry["params"] = params
        if detail is not None:
            entry["detail"] = detail
        if started is not None:
            entry["duration_ms"] = int((time.monotonic() - started) * 1000)
        self.stages[stage] = entry

    def failed(self) -> Optional[dict]:
        return next((self.stages[s] for s in STAGES
                     if self.stages.get(s, {}).get("status") == FAILED), None)

    def finish(self, **extra) -> dict:
        for s in STAGES:
            self.stages.setdefault(s, {"stage": s, "status": SKIPPED})
        failed = self.failed()
        out = {
            "ok": failed is None,
            "status": "connected" if failed is None else "error",
            "stages": [self.stages[s] for s in STAGES],
            "failed_stage": failed["stage"] if failed else None,
            # `error` kept for older API clients: a CODE now, never driver text.
            "error": failed.get("code") if failed else None,
            "tested_at": datetime.now(timezone.utc).isoformat(),
        }
        out.update(extra)
        return out


@contextmanager
def _begin_read_only(conn, engine: str):
    """One read-only transaction per stage, ALWAYS rolled back — so a stage
    whose catalogue query fails (an aborted transaction on PostgreSQL) does
    not poison the next one, and nothing is ever committed."""
    import sqlalchemy
    trans = conn.begin()
    try:
        if engine in ("postgresql", "oracle"):
            conn.execute(sqlalchemy.text("SET TRANSACTION READ ONLY"))
        yield
    finally:
        try:
            trans.rollback()
        except Exception:  # noqa: BLE001 - the connection is closed right after
            pass


def run_probe(cfg: dict, *, tenant_id: Optional[str]) -> dict:
    report = _Report()
    extra: dict[str, Any] = {"write_access": None, "grant_sql": None,
                             "tables_sample": [], "table_count": None,
                             "server_version": None, "read_only_session": None}

    started = time.monotonic()
    try:
        spec = spec_from_stored(cfg)
    except AppError as exc:
        # Credentials that cannot be decrypted, an engine no longer supported.
        report.set("auth", FAILED, started, code=exc.code, params=exc.params)
        return report.finish(**extra)

    with tenant_slot(tenant_id):
        # ── dns ──────────────────────────────────────────────────────────────
        started = time.monotonic()
        try:
            addresses = network.resolve(spec.host_name, spec.port)
            for ip in addresses:
                verdict = network.classify(ip, allow_private=network.allow_private_hosts())
                if not verdict.allowed:
                    raise network._refuse(spec.host_name, ip.compressed, verdict.category)
        except AppError as exc:
            report.set("dns", FAILED, started, code=exc.code, params=exc.params)
            return report.finish(**extra)
        report.set("dns", OK, started, detail={"addresses": [ip.compressed for ip in addresses][:4]})

        # ── tcp ──────────────────────────────────────────────────────────────
        started = time.monotonic()
        reached: list[str]
        if spec.instance:
            # A named instance's port is announced by SQL Browser over UDP;
            # there is no fixed TCP port to knock on. The driver finds it.
            report.set("tcp", SKIPPED, started, code="data_source_tcp_named_instance")
            reached = [ip.compressed for ip in addresses]
        else:
            ip, failure = network.tcp_reachable(addresses, spec.port,
                                                timeout_s=min(float(spec.connect_timeout_s), 8.0))
            if ip is None:
                code = "data_source_connect_timeout" if failure == "timeout" else "data_source_host_unreachable"
                report.set("tcp", FAILED, started, code=code,
                           params={"host": spec.host_name, "port": spec.port, "reason": failure})
                return report.finish(**extra)
            report.set("tcp", OK, started, detail={"address": ip.compressed, "port": spec.port})
            reached = [ip.compressed]

        # ── tls + auth ───────────────────────────────────────────────────────
        started = time.monotonic()
        with _ca_file(spec) as ca_path:
            engine = make_engine(spec, reached, timeout_s=min(spec.statement_timeout_s, 15),
                                 ca_path=ca_path)
            try:
                try:
                    conn = engine.connect()
                except Exception as exc:  # noqa: BLE001
                    kind = classify(exc, engine=spec.engine, secrets=spec.secrets,
                                    host=spec.host_name, database=spec.database, during="auth")
                    if kind.stage == STAGE_TLS:
                        report.set("tls", FAILED, started, code=kind.code, params=kind.params)
                    else:
                        if spec.ssl_mode in ("require", "verify-ca", "verify-full"):
                            # The handshake precedes authentication on every
                            # engine: reaching auth means TLS held.
                            report.set("tls", OK, None)
                        else:
                            report.set("tls", SKIPPED, None, code="data_source_tls_not_checked")
                        stage = kind.stage if kind.stage in ("dns", "tcp") else "auth"
                        report.set(stage, FAILED, started, code=kind.code, params=kind.params)
                    return report.finish(**extra)

                try:
                    version = conn.dialect.server_version_info
                    if version:
                        extra["server_version"] = ".".join(str(p) for p in version[:3])
                    _probe_session(conn, spec, report, extra, started)
                finally:
                    conn.close()
            finally:
                engine.dispose()
    return report.finish(**extra)


def _probe_session(conn, spec, report: _Report, extra: dict, auth_started: float) -> None:
    import sqlalchemy
    engine = spec.engine

    # tls
    started = time.monotonic()
    try:
        with _begin_read_only(conn, engine):
            tls = catalog.tls_state(conn, engine)
    except Exception:  # noqa: BLE001 - visibility of the TLS state is not a failure
        tls = {"encrypted": None, "detail": None}
    mode = spec.ssl_mode
    if tls["encrypted"] is True:
        report.set("tls", OK, started, detail={"mode": mode, "cipher": tls["detail"]})
    elif tls["encrypted"] is False and mode in ("require", "verify-ca", "verify-full"):
        report.set("tls", FAILED, started, code="data_source_tls_failed",
                   params={"reason": "not_negotiated"})
    elif tls["encrypted"] is False and mode == "prefer":
        report.set("tls", WARNING, started, code="data_source_tls_not_used", detail={"mode": mode})
    elif tls["encrypted"] is False:
        report.set("tls", WARNING, started, code="data_source_tls_disabled", detail={"mode": mode})
    else:
        report.set("tls", OK if mode in ("require", "verify-ca", "verify-full") else SKIPPED,
                   started, code=None if mode != "disable" else "data_source_tls_disabled",
                   detail={"mode": mode})
    report.set("auth", OK, auth_started,
               detail={"user": spec.username, "database": spec.database})
    if report.failed():
        return

    # privileges
    started = time.monotonic()
    try:
        with _begin_read_only(conn, engine):
            access = catalog.write_privileges(conn, engine)
        extra["write_access"] = access
        if access["can_write"]:
            report.set("privileges", WARNING, started, code="data_source_login_can_write",
                       params={"privileges": ", ".join(access["privileges"])})
            extra["grant_sql"] = catalog.least_privilege_sql(engine, spec.database)
        else:
            report.set("privileges", OK, started)
    except Exception:  # noqa: BLE001 - some logins cannot read the catalogue
        report.set("privileges", WARNING, started, code="data_source_privileges_unknown")
        extra["grant_sql"] = catalog.least_privilege_sql(engine, spec.database)

    # select
    started = time.monotonic()
    try:
        with _begin_read_only(conn, engine):
            read_only = catalog.read_only_state(conn, engine)
            one = "SELECT 1 FROM dual" if engine == "oracle" else "SELECT 1"
            conn.execute(sqlalchemy.text(one)).scalar()
        extra["read_only_session"] = read_only
        if read_only is False:
            report.set("select", WARNING, started, code="data_source_read_only_not_enforced")
        elif read_only is None and engine == "mssql":
            report.set("select", OK, started, code="data_source_read_only_by_login",
                       detail={"read_only_session": None})
        else:
            report.set("select", OK, started, detail={"read_only_session": read_only})
    except Exception as exc:  # noqa: BLE001
        kind = classify(exc, engine=engine, secrets=spec.secrets, during="query")
        report.set("select", FAILED, started, code=kind.code, params=kind.params)
        return

    # tables
    started = time.monotonic()
    try:
        with _begin_read_only(conn, engine):
            listing = catalog.list_tables(conn, engine)
        tables = listing["tables"]
        extra["table_count"] = len(tables)
        extra["tables_truncated"] = listing["truncated"]
        extra["tables_sample"] = [
            f"{t['schema']}.{t['name']}" if t.get("schema") else t["name"]
            for t in tables[:TABLE_SAMPLE]
        ]
        if not tables:
            report.set("tables", WARNING, started, code="data_source_no_tables_visible")
        else:
            report.set("tables", OK, started, params={"count": len(tables)})
    except Exception as exc:  # noqa: BLE001
        kind = classify(exc, engine=engine, secrets=spec.secrets, during="query")
        report.set("tables", WARNING, started, code=kind.code, params=kind.params)
