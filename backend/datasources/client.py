"""Opening connections to a customer's database — the only place that does.

Every path that touches a customer database (the connection test, the schema
browser, the query editor, export, materialize, analysis, the scheduled
refresh) goes through `open_session`, which guarantees, in this order:

1. **One slot per call, bounded per tenant.** At most
   `SQL_SOURCES_MAX_CONCURRENT_PER_TENANT` connections from one tenant to its
   databases at a time; the next caller waits briefly and is then refused
   with `data_source_busy` (429). A burst of clicks cannot open fifty
   connections against a customer's ERP.
2. **The address is checked, then used.** The host is resolved once, every
   address is checked against the SSRF policy (`network.py`), and the driver
   connects to the checked ADDRESS — a DNS answer that changes between check
   and connect cannot redirect it. (TLS ``verify-full`` must present the host
   NAME to the server for certificate matching: PostgreSQL does that through
   ``hostaddr`` and SQL Server through ``HostNameInCertificate``, so both still
   connect to the checked address; MySQL and Oracle connect by name in that
   one mode.)
3. **TLS as configured** (`connection.SSL_MODES`), with an uploaded CA
   certificate written to a private temp file only for PostgreSQL (libpq
   needs a path) and deleted when the session ends; the other drivers take
   an in-memory SSL context.
4. **Timeouts on every layer**: connect, statement (server-side where the
   engine has one) and a client-side read backstop.
5. **Read-only**: a read-only session or transaction where the engine has one,
   and ALWAYS a rollback at the end (`sql_guard` already refused any
   statement that is not one plain read).
6. **Retries only for transient failures** (server starting up, out of
   connection slots, connection dropped), with jittered backoff — a wrong
   password is never retried against the customer's server.
7. **Nothing leaks**: NullPool (no idle pooled connections), the engine is
   disposed and the connection closed on every path, and a failure inside the
   session invalidates the connection instead of politely draining a
   streaming result of ten million rows first.

Errors leave as `AppError`s with a code (`errors.classify`), never as a raw
driver message that could quote the connection string.
"""

from __future__ import annotations

import logging
import os
import random
import ssl
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from backend.datasources import network
from backend.datasources import secrets as ds_secrets
from backend.datasources.connection import (
    DEFAULT_CONNECT_TIMEOUT_S, DEFAULT_PORTS, DEFAULT_STATEMENT_TIMEOUT_S,
    LEGACY_SSL_MODE, ConnectionSpec,
)
from backend.datasources.errors import STAGE_AUTH, STAGE_TCP, classify
from backend.errors import AppError

log = logging.getLogger(__name__)

APP_NAME = "StockAI"
RETRY_BACKOFF_S = (0.4, 1.2)
GATE_WAIT_S = 10.0
DEFAULT_MAX_CONCURRENT_PER_TENANT = 4

DIALECTS = {
    "postgresql": "postgresql+psycopg2://",
    "mysql": "mysql+pymysql://",
    "mssql": "mssql+pyodbc://",
    "oracle": "oracle+oracledb://",
}


# ── Stored config -> spec ─────────────────────────────────────────────────────

def spec_from_stored(cfg: dict) -> ConnectionSpec:
    """Build the spec from a stored `sql_config`, decrypting its secrets.

    A config written before TLS modes and timeouts existed gets the values
    that reproduce its old behaviour (`LEGACY_SSL_MODE`)."""
    engine = cfg.get("engine") or "postgresql"
    if engine not in DEFAULT_PORTS:
        raise AppError("data_source_engine_unsupported", f"Unsupported engine {engine!r}.",
                       status_code=400, params={"engine": engine, "allowed": ", ".join(DEFAULT_PORTS)})
    password = ds_secrets.decrypt(cfg.get("password_enc") or "")
    ca = ds_secrets.decrypt(cfg["ssl_ca_enc"]) if cfg.get("ssl_ca_enc") else None
    return ConnectionSpec(
        engine=engine,
        host=str(cfg.get("host") or "localhost"),
        port=int(cfg.get("port") or DEFAULT_PORTS[engine]),
        database=str(cfg.get("database") or ""),
        username=str(cfg.get("username") or ""),
        password=password,
        ssl_mode=cfg.get("ssl_mode") or LEGACY_SSL_MODE[engine],
        ssl_ca=ca,
        connect_timeout_s=int(cfg.get("connect_timeout_s") or DEFAULT_CONNECT_TIMEOUT_S),
        statement_timeout_s=int(cfg.get("statement_timeout_s") or DEFAULT_STATEMENT_TIMEOUT_S),
    )


# ── Per-tenant concurrency ────────────────────────────────────────────────────

_gates: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_gates_lock = threading.Lock()


def _max_concurrent() -> int:
    from backend.config import settings
    value = int(getattr(settings, "sql_sources_max_concurrent_per_tenant",
                        DEFAULT_MAX_CONCURRENT_PER_TENANT) or DEFAULT_MAX_CONCURRENT_PER_TENANT)
    return max(1, value)


@contextmanager
def tenant_slot(tenant_id: Optional[str], wait_s: Optional[float] = None) -> Iterator[None]:
    wait_s = GATE_WAIT_S if wait_s is None else wait_s
    limit = _max_concurrent()
    key = (tenant_id or "_internal", limit)
    with _gates_lock:
        sem = _gates.get(key)
        if sem is None:
            sem = _gates[key] = threading.BoundedSemaphore(limit)
    if not sem.acquire(timeout=wait_s):
        raise AppError(
            "data_source_busy",
            f"{limit} queries to your databases are already running. Try again "
            "when one finishes.",
            status_code=429, params={"max": limit},
        )
    try:
        yield
    finally:
        sem.release()


# ── TLS material ──────────────────────────────────────────────────────────────

def ssl_context(mode: str, ca_pem: Optional[str]) -> Optional[ssl.SSLContext]:
    """The context for a mode, or None for ``disable`` / ``prefer`` (where the
    driver's own opportunistic TLS is used)."""
    if mode in ("disable", "prefer"):
        return None
    ctx = ssl.create_default_context(cadata=ca_pem) if ca_pem else ssl.create_default_context()
    # Python 3.13 turns on strict X.509 checks that reject the self-signed
    # certificates MySQL generates for itself; the chain is still verified.
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    if mode == "require":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:  # verify-full
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


@contextmanager
def _ca_file(spec: ConnectionSpec) -> Iterator[Optional[str]]:
    """libpq reads the CA from a file: write it to a private temp file for the
    life of the session, then delete it."""
    if not (spec.engine == "postgresql" and spec.ssl_ca):
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="stockai-ca-", suffix=".pem")
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
            log.warning("SQL source: could not delete the temporary CA file")


# ── Driver connections ────────────────────────────────────────────────────────

def _pg_options(timeout_s: int) -> str:
    ms = timeout_s * 1000
    return (f"-c statement_timeout={ms} -c default_transaction_read_only=on "
            f"-c lock_timeout={min(ms, 10_000)} "
            f"-c idle_in_transaction_session_timeout={max(ms * 2, 300_000)}")


def _connect_postgresql(spec: ConnectionSpec, address: Optional[str], timeout_s: int,
                        ca_path: Optional[str]):
    import psycopg2

    kwargs: dict[str, Any] = dict(
        host=spec.host, port=spec.port, dbname=spec.database, user=spec.username,
        password=spec.password, connect_timeout=spec.connect_timeout_s,
        sslmode=spec.ssl_mode, application_name=APP_NAME, client_encoding="UTF8",
        # GSSAPI encryption is tried first by default; on a machine with a
        # Kerberos setup that costs a round trip nobody asked for.
        gssencmode="disable",
    )
    if address:
        kwargs["hostaddr"] = address
    if ca_path:
        kwargs["sslrootcert"] = ca_path
    elif spec.ssl_mode == "verify-full":
        kwargs["sslrootcert"] = "system"
    try:
        return psycopg2.connect(options=_pg_options(timeout_s), **kwargs)
    except psycopg2.OperationalError as exc:
        # A pooler (pgbouncer) may refuse startup options. Connect without
        # them and apply the same settings as plain SET statements.
        if "unsupported startup parameter" not in str(exc).lower():
            raise
    conn = psycopg2.connect(**kwargs)
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (timeout_s * 1000,))
            cur.execute("SET default_transaction_read_only = on")
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def _connect_mysql(spec: ConnectionSpec, address: Optional[str], timeout_s: int):
    import pymysql

    host = spec.host if (spec.ssl_mode == "verify-full" or not address) else address
    kwargs: dict[str, Any] = dict(
        host=host, port=spec.port, user=spec.username,
        # PyMySQL encodes a str password as latin-1; a password with "ñ" or
        # "€" would fail before reaching the server. Bytes go as they are.
        password=spec.password.encode("utf-8"),
        database=spec.database, charset="utf8mb4",
        connect_timeout=spec.connect_timeout_s,
        read_timeout=timeout_s + 15, write_timeout=60,
        init_command="SET SESSION TRANSACTION READ ONLY",
        program_name=APP_NAME, autocommit=False,
    )
    if spec.ssl_mode == "disable":
        kwargs["ssl_disabled"] = True
    elif spec.ssl_mode != "prefer":
        kwargs["ssl"] = ssl_context(spec.ssl_mode, spec.ssl_ca)
    # ("prefer" is PyMySQL's own default: TLS when the server offers it.)
    conn = pymysql.connect(**kwargs)
    try:
        with conn.cursor() as cur:
            if "mariadb" in (conn.get_server_info() or "").lower():
                cur.execute("SET SESSION max_statement_time = %s", (timeout_s,))
            else:
                cur.execute("SET SESSION max_execution_time = %s", (timeout_s * 1000,))
    except Exception:
        conn.close()
        raise
    return conn


def _odbc_value(value: str) -> str:
    return "{" + str(value).replace("}", "}}") + "}"


def best_mssql_odbc_driver() -> str:
    """Newest SQL Server ODBC driver installed on this host."""
    try:
        import pyodbc
        installed = [d for d in pyodbc.drivers() if "SQL Server" in d]
        for preferred in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"):
            if preferred in installed:
                return preferred
        if installed:
            return installed[-1]
    except Exception:  # noqa: BLE001 - no pyodbc: the connect call reports it
        pass
    return "ODBC Driver 18 for SQL Server"


def mssql_connection_string(spec: ConnectionSpec, address: Optional[str]) -> str:
    server = address or spec.host_name
    if ":" in server and not server.startswith("["):
        server = f"[{server}]"
    if spec.instance:
        server_part = f"{server}\\{spec.instance}"
    else:
        server_part = f"tcp:{server},{spec.port}"
    parts = [
        ("DRIVER", _odbc_value(best_mssql_odbc_driver())),
        ("SERVER", server_part),
        ("DATABASE", _odbc_value(spec.database)),
        ("UID", _odbc_value(spec.username)),
        ("PWD", _odbc_value(spec.password)),
        ("APP", APP_NAME),
    ]
    if spec.ssl_mode in ("disable", "prefer"):
        parts.append(("Encrypt", "no"))
    elif spec.ssl_mode == "require":
        parts += [("Encrypt", "yes"), ("TrustServerCertificate", "yes")]
    else:  # verify-full
        parts += [("Encrypt", "yes"), ("TrustServerCertificate", "no")]
        if address:
            parts.append(("HostNameInCertificate", _odbc_value(spec.host_name)))
    return ";".join(f"{k}={v}" for k, v in parts) + ";"


def _connect_mssql(spec: ConnectionSpec, address: Optional[str], timeout_s: int):
    import pyodbc

    conn = pyodbc.connect(mssql_connection_string(spec, address),
                          timeout=spec.connect_timeout_s, autocommit=False, readonly=True)
    conn.timeout = timeout_s     # query timeout, seconds
    return conn


def _connect_oracle(spec: ConnectionSpec, address: Optional[str], timeout_s: int):
    import oracledb

    by_name = spec.ssl_mode == "verify-full" or not address
    kwargs: dict[str, Any] = dict(
        user=spec.username, password=spec.password,
        host=spec.host if by_name else address, port=spec.port,
        service_name=spec.database, tcp_connect_timeout=float(spec.connect_timeout_s),
    )
    if spec.ssl_mode != "disable":
        kwargs.update(protocol="tcps", ssl_context=ssl_context(spec.ssl_mode, spec.ssl_ca),
                      ssl_server_dn_match=spec.ssl_mode == "verify-full")
    conn = oracledb.connect(**kwargs)
    conn.call_timeout = timeout_s * 1000
    return conn


def dbapi_connect(spec: ConnectionSpec, address: Optional[str], timeout_s: int,
                  ca_path: Optional[str] = None):
    """One raw DBAPI connection to one address."""
    if spec.engine == "postgresql":
        return _connect_postgresql(spec, address, timeout_s, ca_path)
    if spec.engine == "mysql":
        return _connect_mysql(spec, address, timeout_s)
    if spec.engine == "mssql":
        return _connect_mssql(spec, address, timeout_s)
    return _connect_oracle(spec, address, timeout_s)


def _connector(spec: ConnectionSpec, addresses: list[str], timeout_s: int,
               ca_path: Optional[str]) -> Callable[[], Any]:
    """A SQLAlchemy `creator`: try each checked address in order, moving on
    only when that address cannot be reached at all."""
    def connect():
        last: Optional[BaseException] = None
        for address in addresses or [None]:
            try:
                return dbapi_connect(spec, address, timeout_s, ca_path)
            except Exception as exc:  # noqa: BLE001 - classified by the caller
                last = exc
                kind = classify(exc, engine=spec.engine, secrets=spec.secrets, during=STAGE_AUTH)
                if kind.stage != STAGE_TCP:
                    raise
        assert last is not None
        raise last
    return connect


def make_engine(spec: ConnectionSpec, addresses: list[str], timeout_s: int,
                ca_path: Optional[str] = None):
    import sqlalchemy
    from sqlalchemy.pool import NullPool

    return sqlalchemy.create_engine(
        DIALECTS[spec.engine],
        creator=_connector(spec, addresses, timeout_s, ca_path),
        poolclass=NullPool,
    )


# ── Sessions ──────────────────────────────────────────────────────────────────

@dataclass
class Session:
    conn: Any                 # sqlalchemy Connection, inside a read-only transaction
    spec: ConnectionSpec
    address: Optional[str]    # the first checked address (what was connected to)


def resolve_checked(spec: ConnectionSpec) -> list[str]:
    """The checked addresses of the spec's host (SSRF policy applied)."""
    ips = network.resolve_allowed(spec.host_name, spec.port,
                                  allow_private=network.allow_private_hosts())
    return [ip.compressed for ip in ips]


def _connect_with_retry(engine, spec: ConnectionSpec, retries: int):
    for attempt in range(retries + 1):
        try:
            return engine.connect()
        except Exception as exc:  # noqa: BLE001
            kind = classify(exc, engine=spec.engine, secrets=spec.secrets,
                            host=spec.host, database=spec.database, during=STAGE_AUTH)
            if not kind.transient or attempt >= retries:
                # `from None`: the driver's exception (which may quote the
                # connection string) must not ride along in a traceback.
                raise kind.to_app_error() from None
            delay = RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)]
            log.info("SQL source: transient %s, retrying in %.1fs", kind.code, delay)
            time.sleep(delay * (1 + random.random() * 0.25))
    raise AssertionError("unreachable")  # pragma: no cover


@contextmanager
def open_session(cfg_or_spec: Any, *, tenant_id: Optional[str],
                 statement_timeout_s: Optional[int] = None, bulk: bool = False,
                 retries: int = 2) -> Iterator[Session]:
    """A read-only session on the customer's database. See the module doc."""
    import sqlalchemy

    spec = cfg_or_spec if isinstance(cfg_or_spec, ConnectionSpec) else spec_from_stored(cfg_or_spec)
    timeout_s = statement_timeout_s or (spec.bulk_timeout_s if bulk else spec.statement_timeout_s)
    with tenant_slot(tenant_id):
        addresses = resolve_checked(spec)
        with _ca_file(spec) as ca_path:
            engine = make_engine(spec, addresses, timeout_s, ca_path)
            try:
                conn = _connect_with_retry(engine, spec, retries)
                failed = False
                try:
                    trans = conn.begin()
                    if spec.engine in ("postgresql", "oracle"):
                        conn.execute(sqlalchemy.text("SET TRANSACTION READ ONLY"))
                    yield Session(conn, spec, addresses[0] if addresses else None)
                except BaseException:
                    failed = True
                    raise
                finally:
                    if failed:
                        # Do not drain an unread streaming result or wait on a
                        # rollback over a broken socket: drop the connection.
                        # The server rolls back an abandoned transaction.
                        try:
                            conn.invalidate()
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        try:
                            trans.rollback()
                        except Exception as exc:  # noqa: BLE001
                            log.warning("SQL source: rollback failed (%s)", type(exc).__name__)
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                engine.dispose()
