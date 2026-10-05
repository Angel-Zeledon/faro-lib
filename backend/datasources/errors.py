"""Turn a driver's failure into something a person can act on.

Four drivers (psycopg2, PyMySQL, pyodbc, python-oracledb), wrapped by
SQLAlchemy, raise four different shapes of exception whose text can carry the
connection string, the statement, a SQLAlchemy documentation link or — with
some drivers and some failures — the password. None of that may reach a user,
a log line the tenant can read, the audit trail or a schedule's `last_error`.

`classify(exc, ...)` maps an exception to:

* a stable **code** the frontend translates (`errors.<code>`) — wrong
  password, host unreachable, timeout, TLS failure, missing database,
  permission denied, missing driver, read-only violation, or the user's own
  SQL being wrong (`sql_query_failed`, the only one that carries the driver's
  text, because "column x does not exist" is exactly what the author needs);
* the **stage** of the staged connection test it belongs to;
* whether it is **transient** — the only failures worth retrying (a server
  that is starting up, out of connection slots, or a dropped connection), so
  a wrong password is never hammered against the customer's server.

`scrub(text, secrets)` removes the given secrets (raw and URL-encoded), any
``password=...``-shaped pair and any ``scheme://user:pass@`` credentials, and
keeps only the first meaningful line, capped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import quote, quote_plus

from backend.errors import AppError

MAX_REASON_CHARS = 300

STAGE_DNS = "dns"
STAGE_TCP = "tcp"
STAGE_TLS = "tls"
STAGE_AUTH = "auth"
STAGE_QUERY = "query"
STAGE_DRIVER = "driver"


@dataclass(frozen=True)
class Classified:
    code: str
    stage: str
    transient: bool = False
    status_code: int = 400
    params: dict = field(default_factory=dict)

    def to_app_error(self) -> AppError:
        reason = self.params.get("reason")
        message = f"{self.code}: {reason}" if reason else self.code
        return AppError(self.code, message, status_code=self.status_code, params=dict(self.params))


_SECRET_PAIR = re.compile(
    r"(?i)\b(password|passwd|pwd|pass|secret|token|apikey|api_key)\s*[=:]\s*(\{[^}]*\}|'[^']*'|\"[^\"]*\"|[^\s;&,)]+)"
)
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]*)@")
_SQLA_NOISE = re.compile(r"\(Background on this error at:[^)]*\)|\[SQL:.*?\]|\[parameters:.*?\]", re.S)


def scrub(text: Any, secrets: Iterable[str] = ()) -> str:
    """The first meaningful line of `text`, without secrets, capped."""
    s = str(text or "")
    for secret in secrets:
        if not secret:
            continue
        for variant in {secret, quote(secret, safe=""), quote_plus(secret)}:
            if variant:
                s = s.replace(variant, "***")
    s = _SQLA_NOISE.sub("", s)
    s = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}=***", s)
    s = _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{m.group(2)}:***@", s)
    # SQLAlchemy prefixes "(psycopg2.errors.UndefinedColumn) " — keep the text.
    s = re.sub(r"^\([\w.]+\)\s*", "", s.strip())
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    # psycopg2 puts the useful pointer on a LINE n: / HINT: line; keep a HINT.
    hint = next((ln for ln in lines[1:] if ln.upper().startswith(("HINT:", "DETAIL:"))), "")
    out = f"{first} {hint}".strip() if hint else first
    return out[:MAX_REASON_CHARS]


def _orig(exc: BaseException) -> BaseException:
    """The DBAPI exception under a SQLAlchemy wrapper, if any."""
    inner = getattr(exc, "orig", None)
    return inner if isinstance(inner, BaseException) else exc


def _mysql_errno(exc: BaseException) -> Optional[int]:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _pg_code(exc: BaseException) -> Optional[str]:
    return getattr(exc, "pgcode", None)


def _odbc_state(exc: BaseException) -> Optional[str]:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], str) and re.fullmatch(r"[0-9A-Z]{5}", args[0]):
        return args[0]
    return None


def _ora_code(exc: BaseException, text: str) -> Optional[str]:
    code = getattr(exc, "full_code", None)
    if code:
        return code
    if exc.args and hasattr(exc.args[0], "full_code"):
        return exc.args[0].full_code
    m = re.search(r"\b(ORA-\d{5}|DPY-\d{4}|TNS-\d{5})\b", text)
    return m.group(1) if m else None


def _c(code: str, stage: str, *, transient: bool = False, status: int = 400, **params) -> Classified:
    return Classified(code, stage, transient, status, params)


_TLS_WORDS = re.compile(r"(?i)\b(ssl|tls|certificate|cert verify|handshake|x509|self[- ]signed)\b")


def classify(exc: BaseException, *, engine: str = "", secrets: Iterable[str] = (),
             host: str = "", database: str = "", during: str = STAGE_QUERY) -> Classified:
    """Map any exception from connecting or querying to a `Classified`.

    `during` is what the caller was doing ("auth" while opening the
    connection, "query" while running a statement); it decides where an
    unrecognised failure lands."""
    if isinstance(exc, AppError):
        return Classified(exc.code, during, False, exc.status_code, dict(exc.params))

    secrets = tuple(secrets)
    inner = _orig(exc)
    text = str(inner) if str(inner) else str(exc)
    low = text.lower()
    reason = scrub(text, secrets)

    if isinstance(inner, (ImportError, ModuleNotFoundError)):
        return _c("data_source_driver_missing", STAGE_DRIVER, status=503, engine=engine)
    if engine == "mssql" and (_odbc_state(inner) in ("IM002", "01000") and
                              ("can't open lib" in low or "driver" in low)):
        return _c("data_source_driver_missing", STAGE_DRIVER, status=503, engine=engine)

    # ── PostgreSQL (psycopg2) ────────────────────────────────────────────────
    pg = _pg_code(inner)
    if engine == "postgresql" or pg:
        if pg == "57014" or "statement timeout" in low or "canceling statement" in low:
            return _c("data_source_query_timeout", STAGE_QUERY, status=504)
        if pg == "25006" or "read-only transaction" in low:
            return _c("data_source_read_only_violation", STAGE_QUERY)
        if pg == "42501" or "permission denied" in low:
            return _c("data_source_permission_denied",
                      STAGE_AUTH if during == STAGE_AUTH else STAGE_QUERY, reason=reason)
        if pg in ("53300", "57P03", "53400") or "too many connections" in low \
                or "remaining connection slots" in low or "starting up" in low \
                or "shutting down" in low:
            return _c("data_source_server_busy", STAGE_AUTH, transient=True, status=503)
        if pg == "3D000" or re.search(r'database ".*" does not exist', low):
            return _c("data_source_database_not_found", STAGE_AUTH, database=database)
        if pg in ("28P01", "28000") or "password authentication failed" in low \
                or "no password supplied" in low or re.search(r'role ".*" does not exist', low):
            if "pg_hba.conf" in low:
                return _c("data_source_host_rejected", STAGE_AUTH, host=host)
            return _c("data_source_auth_failed", STAGE_AUTH)
        if "pg_hba.conf" in low:
            return _c("data_source_host_rejected", STAGE_AUTH, host=host)
        if "could not translate host name" in low or "name or service not known" in low:
            return _c("data_source_dns_failed", STAGE_DNS, host=host, reason="not_found")
        if "server does not support ssl" in low or _TLS_WORDS.search(low):
            return _c("data_source_tls_failed", STAGE_TLS, reason=reason)
        if "timeout expired" in low or "timed out" in low:
            return _c("data_source_connect_timeout", STAGE_TCP, transient=True, host=host)
        if "connection refused" in low:
            return _c("data_source_host_unreachable", STAGE_TCP, host=host, reason="refused")
        if "could not connect to server" in low or "no route to host" in low \
                or "network is unreachable" in low or "connection to server at" in low and "failed" in low:
            return _c("data_source_host_unreachable", STAGE_TCP, host=host, reason="unreachable")
        if "server closed the connection unexpectedly" in low or "connection already closed" in low \
                or "ssl syscall error" in low or "terminating connection" in low:
            return _c("data_source_connection_lost", STAGE_QUERY, transient=True, status=503)
        if "unsupported startup parameter" in low:
            return _c("data_source_startup_options_refused", STAGE_AUTH)

    # ── MySQL / MariaDB (PyMySQL) ────────────────────────────────────────────
    errno = _mysql_errno(inner)
    if engine == "mysql" or (errno and 1000 <= errno < 4000):
        if errno in (3024, 1969, 1317) or "max_execution_time" in low or "max_statement_time" in low:
            return _c("data_source_query_timeout", STAGE_QUERY, status=504)
        if errno == 1792 or "read only transaction" in low:
            return _c("data_source_read_only_violation", STAGE_QUERY)
        if errno == 1045:
            return _c("data_source_auth_failed", STAGE_AUTH)
        if errno == 1130 or "is not allowed to connect" in low:
            return _c("data_source_host_rejected", STAGE_AUTH, host=host)
        if errno == 1049 or "unknown database" in low:
            return _c("data_source_database_not_found", STAGE_AUTH, database=database)
        if errno == 1044:
            # Access denied to the DATABASE (the login itself worked).
            return _c("data_source_permission_denied", STAGE_AUTH, reason=reason)
        if errno in (1142, 1143, 1227, 1370):
            return _c("data_source_permission_denied", STAGE_QUERY, reason=reason)
        if errno in (1040, 1203, 1226) or "too many connections" in low:
            return _c("data_source_server_busy", STAGE_AUTH, transient=True, status=503)
        if errno in (2026,) or (errno and "ssl" in low) or _TLS_WORDS.search(low):
            return _c("data_source_tls_failed", STAGE_TLS, reason=reason)
        if errno == 2005 or "unknown mysql server host" in low or "getaddrinfo failed" in low:
            return _c("data_source_dns_failed", STAGE_DNS, host=host, reason="not_found")
        if errno == 2003:
            if "timed out" in low or "timeout" in low:
                return _c("data_source_connect_timeout", STAGE_TCP, transient=True, host=host)
            return _c("data_source_host_unreachable", STAGE_TCP, host=host,
                      reason="refused" if re.search(r"refused|errno 111|10061", low) else "unreachable")
        if errno in (2013, 2006, 2055):
            if "timed out" in low:
                # The client-side read timeout fired: the statement outlived it.
                return _c("data_source_query_timeout", STAGE_QUERY, status=504)
            return _c("data_source_connection_lost", STAGE_QUERY, transient=True, status=503)

    # ── SQL Server (pyodbc) ──────────────────────────────────────────────────
    state = _odbc_state(inner)
    if engine == "mssql" or state:
        if state == "HYT00" or "query timeout expired" in low:
            return _c("data_source_query_timeout", STAGE_QUERY, status=504)
        if state == "HYT01" or "login timeout expired" in low:
            return _c("data_source_connect_timeout", STAGE_TCP, transient=True, host=host)
        if "18456" in low or "login failed" in low:
            if "cannot open database" in low or "4060" in low:
                return _c("data_source_database_not_found", STAGE_AUTH, database=database)
            return _c("data_source_auth_failed", STAGE_AUTH)
        if "4060" in low or "cannot open database" in low:
            return _c("data_source_database_not_found", STAGE_AUTH, database=database)
        if "(229)" in low or "permission was denied" in low:
            return _c("data_source_permission_denied", STAGE_QUERY, reason=reason)
        if "certificate" in low or "ssl provider" in low or "encryption" in low and "not supported" in low:
            return _c("data_source_tls_failed", STAGE_TLS, reason=reason)
        if state == "08001" or "named pipes provider" in low or "tcp provider" in low:
            if "timeout" in low:
                return _c("data_source_connect_timeout", STAGE_TCP, transient=True, host=host)
            return _c("data_source_host_unreachable", STAGE_TCP, host=host, reason="unreachable")
        if state in ("08S01", "08003") or "communication link failure" in low:
            return _c("data_source_connection_lost", STAGE_QUERY, transient=True, status=503)

    # ── Oracle (python-oracledb) ─────────────────────────────────────────────
    ora = _ora_code(inner, text) if engine == "oracle" or "ORA-" in text or "DPY-" in text else None
    if ora:
        if ora in ("ORA-01017", "ORA-01005", "ORA-28000"):
            return _c("data_source_auth_failed", STAGE_AUTH)
        if ora in ("ORA-12514", "ORA-12505", "DPY-6001", "DPY-6003"):
            return _c("data_source_database_not_found", STAGE_AUTH, database=database)
        if ora in ("ORA-01031", "ORA-00942", "ORA-01039"):
            return _c("data_source_permission_denied", STAGE_QUERY, reason=reason)
        if ora in ("DPY-4024", "ORA-03156", "DPY-4011") and "timeout" in low or ora == "ORA-01013":
            return _c("data_source_query_timeout", STAGE_QUERY, status=504)
        if ora in ("ORA-01456",):
            return _c("data_source_read_only_violation", STAGE_QUERY)
        if ora in ("ORA-12541", "DPY-6005", "TNS-12541"):
            if "timed out" in low or "timeout" in low:
                return _c("data_source_connect_timeout", STAGE_TCP, transient=True, host=host)
            return _c("data_source_host_unreachable", STAGE_TCP, host=host, reason="refused")
        if ora in ("ORA-12170", "DPY-6000"):
            return _c("data_source_connect_timeout", STAGE_TCP, transient=True, host=host)
        if ora in ("ORA-12516", "ORA-12519", "ORA-12520", "ORA-00018", "ORA-01033"):
            return _c("data_source_server_busy", STAGE_AUTH, transient=True, status=503)
        if ora in ("ORA-29024", "ORA-28860", "DPY-6004") or _TLS_WORDS.search(low):
            return _c("data_source_tls_failed", STAGE_TLS, reason=reason)
        if ora in ("ORA-03113", "ORA-03114", "ORA-03135", "DPY-4011"):
            return _c("data_source_connection_lost", STAGE_QUERY, transient=True, status=503)

    # ── Generic socket-level failures (any driver) ───────────────────────────
    if isinstance(inner, TimeoutError) or "timed out" in low:
        if during in (STAGE_AUTH, STAGE_TCP, STAGE_TLS, STAGE_DNS):
            return _c("data_source_connect_timeout", STAGE_TCP, transient=True, host=host)
        return _c("data_source_query_timeout", STAGE_QUERY, status=504)
    if isinstance(inner, ConnectionRefusedError) or "connection refused" in low:
        return _c("data_source_host_unreachable", STAGE_TCP, host=host, reason="refused")
    if isinstance(inner, ConnectionResetError):
        return _c("data_source_connection_lost", STAGE_QUERY, transient=True, status=503)

    if during == STAGE_QUERY:
        # The statement itself is wrong (syntax, unknown column...). Its text is
        # what the author needs, scrubbed.
        return _c("sql_query_failed", STAGE_QUERY, reason=reason)
    return _c("data_source_connect_failed", during, reason=reason)


def as_app_error(exc: BaseException, **kwargs) -> AppError:
    """`classify` straight to the `AppError` an endpoint raises."""
    return classify(exc, **kwargs).to_app_error()
