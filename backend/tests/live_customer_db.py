"""Real "customer databases" for the SQL data-source integration tests.

Three engines, reached on the ports below (SSH tunnels on the development
machine; any reachable server works). Each target is described by an admin
login (used ONLY by the fixtures, to build the test tables and the read-only
login) and is skipped — not failed — when its port does not answer, so the
suite still runs on a machine without them.

Override with environment variables:

    STOCKAI_LIVE_PG     host:port:admin_user:admin_password     (default 127.0.0.1:5546:postgres:postgres)
    STOCKAI_LIVE_MYSQL  host:port:admin_user:admin_password     (default 127.0.0.1:3307:root:rootpass)
    STOCKAI_LIVE_MARIA  host:port:admin_user:admin_password     (default 127.0.0.1:3308:root:rootpass)

What the fixtures create (idempotent, only inside the disposable test servers):
PostgreSQL database `customer_pg` (never the `forecasting` database that
shares the server); in it and in MySQL/MariaDB database `customer`, tables
prefixed `stockai_`, a table with a hostile name, and a read-only login
`stockai_test_ro`.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

RO_USER = "stockai_test_ro"
# Non-ASCII and URL-hostile on purpose: it has to survive every layer.
RO_PASSWORD = "ro-pässwörd€;@/:%"
BIG_ROWS = 200_000
HOSTILE_TABLE = 'Ventas 2024 "Ñ" `x` ]y['
UNICODE_TEXT = "ñandú — 漢字 — 🚀 — Ωmega"


@dataclass(frozen=True)
class Target:
    key: str             # "pg" | "mysql" | "maria"
    engine: str          # stockai engine name
    host: str
    port: int
    admin_user: str
    admin_password: str
    database: str


def _parse(env: str, default: str) -> tuple[str, int, str, str]:
    host, port, user, password = os.environ.get(env, default).split(":", 3)
    return host, int(port), user, password


def targets() -> list[Target]:
    pg = _parse("STOCKAI_LIVE_PG", "127.0.0.1:5546:postgres:postgres")
    my = _parse("STOCKAI_LIVE_MYSQL", "127.0.0.1:3307:root:rootpass")
    ma = _parse("STOCKAI_LIVE_MARIA", "127.0.0.1:3308:root:rootpass")
    return [
        Target("pg", "postgresql", pg[0], pg[1], pg[2], pg[3], "customer_pg"),
        Target("mysql", "mysql", my[0], my[1], my[2], my[3], "customer"),
        Target("maria", "mysql", ma[0], ma[1], ma[2], ma[3], "customer"),
    ]


@lru_cache(maxsize=None)
def reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


# ── admin connections (fixtures only) ─────────────────────────────────────────

def admin_connect(t: Target, database: Optional[str] = None):
    if t.engine == "postgresql":
        import psycopg2
        conn = psycopg2.connect(host=t.host, port=t.port, user=t.admin_user,
                                password=t.admin_password, dbname=database or t.database,
                                connect_timeout=10)
        conn.autocommit = True
        return conn
    import pymysql
    return pymysql.connect(host=t.host, port=t.port, user=t.admin_user,
                           password=t.admin_password, database=database or t.database,
                           charset="utf8mb4", autocommit=True, connect_timeout=10)


def admin_scalar(t: Target, sql: str, params=None):
    conn = admin_connect(t)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _q(t: Target, name: str) -> str:
    if t.engine == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def setup_target(t: Target) -> None:
    """Create the test objects if missing. Safe to run every time."""
    if t.engine == "postgresql":
        _setup_pg(t)
    else:
        _setup_mysql(t)


def _setup_pg(t: Target) -> None:
    import psycopg2
    root = admin_connect(t, database="postgres")
    try:
        with root.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (t.database,))
            if not cur.fetchone():
                cur.execute(f"CREATE DATABASE {t.database}")
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_USER,))
            if cur.fetchone():
                cur.execute(f"ALTER ROLE {RO_USER} LOGIN PASSWORD %s", (RO_PASSWORD,))
            else:
                cur.execute(f"CREATE ROLE {RO_USER} LOGIN PASSWORD %s", (RO_PASSWORD,))
    finally:
        root.close()

    conn = admin_connect(t)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stockai_types (
                    id int PRIMARY KEY, dec numeric(38,10), big bigint, flt double precision,
                    d date, ts timestamp, tstz timestamptz, b bytea, j jsonb, txt text,
                    flag boolean, nul text)""")
            cur.execute("DELETE FROM stockai_types")
            cur.execute("""
                INSERT INTO stockai_types VALUES
                (1, 12345678901234567890.0123456789, 9223372036854775807, 1.5,
                 '2026-01-31', '2026-01-31 13:45:06.123456', '2026-01-31 13:45:06+02',
                 '\\x00ff10'::bytea, '{"a": [1, 2], "b": "ü"}', %s, true, NULL),
                (2, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)""",
                        (UNICODE_TEXT,))
            cur.execute("CREATE TABLE IF NOT EXISTS stockai_big (id int PRIMARY KEY, sku text, qty int)")
            cur.execute("SELECT count(*) FROM stockai_big")
            if cur.fetchone()[0] != BIG_ROWS:
                cur.execute("TRUNCATE stockai_big")
                cur.execute(f"INSERT INTO stockai_big SELECT g, 'SKU-' || (g % 500), g % 37 "
                            f"FROM generate_series(1, {BIG_ROWS}) g")
                cur.execute("ANALYZE stockai_big")
            cur.execute("CREATE TABLE IF NOT EXISTS stockai_write_target (id int)")
            cur.execute("DELETE FROM stockai_write_target")
            cur.execute("INSERT INTO stockai_write_target VALUES (1), (2), (3)")
            cur.execute(f"CREATE TABLE IF NOT EXISTS {_q(t, HOSTILE_TABLE)} (\"order\" int, \"select\" text)")
            cur.execute(f"DELETE FROM {_q(t, HOSTILE_TABLE)}")
            cur.execute(f"INSERT INTO {_q(t, HOSTILE_TABLE)} VALUES (7, %s)", (UNICODE_TEXT,))
            cur.execute(f"GRANT CONNECT ON DATABASE {t.database} TO {RO_USER}")
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {RO_USER}")
            cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {RO_USER}")
            # PostgreSQL 15+ already denies CREATE on public; older servers do not.
            cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    except psycopg2.Error:
        raise
    finally:
        conn.close()


def _setup_mysql(t: Target) -> None:
    conn = admin_connect(t)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stockai_types (
                    id int PRIMARY KEY, `dec` decimal(38,10), big bigint, ubig bigint unsigned,
                    flt double, d date, ts datetime(6), b varbinary(16), j json,
                    txt varchar(200) CHARACTER SET utf8mb4, flag tinyint(1), nul varchar(10))
                    CHARACTER SET utf8mb4""")
            cur.execute("DELETE FROM stockai_types")
            cur.execute("""
                INSERT INTO stockai_types VALUES
                (1, 1234567890123456789012345678.0123456789, 9223372036854775807,
                 18446744073709551615, 1.5, '2026-01-31', '2026-01-31 13:45:06.123456',
                 UNHEX('00FF10'), '{"a": [1, 2], "b": "ü"}', %s, 1, NULL),
                (2, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)""",
                        (UNICODE_TEXT,))
            cur.execute("CREATE TABLE IF NOT EXISTS stockai_big (id int PRIMARY KEY, sku varchar(20), qty int)")
            cur.execute("SELECT count(*) FROM stockai_big")
            if cur.fetchone()[0] != BIG_ROWS:
                cur.execute("DELETE FROM stockai_big")
                # Doubling insert: 1, 2, 4 ... rows, then trimmed — no recursion limits.
                cur.execute("INSERT INTO stockai_big VALUES (1, 'SKU-1', 1)")
                n = 1
                while n < BIG_ROWS:
                    cur.execute(f"INSERT INTO stockai_big SELECT id + {n}, CONCAT('SKU-', (id + {n}) % 500), "
                                f"(id + {n}) % 37 FROM stockai_big WHERE id + {n} <= {BIG_ROWS}")
                    n *= 2
                cur.execute("ANALYZE TABLE stockai_big")
                cur.fetchall()
            cur.execute("CREATE TABLE IF NOT EXISTS stockai_write_target (id int)")
            cur.execute("DELETE FROM stockai_write_target")
            cur.execute("INSERT INTO stockai_write_target VALUES (1), (2), (3)")
            cur.execute(f"CREATE TABLE IF NOT EXISTS {_q(t, HOSTILE_TABLE)} (`order` int, `select` varchar(100)) "
                        "CHARACTER SET utf8mb4")
            cur.execute(f"DELETE FROM {_q(t, HOSTILE_TABLE)}")
            cur.execute(f"INSERT INTO {_q(t, HOSTILE_TABLE)} VALUES (7, %s)", (UNICODE_TEXT,))
            cur.execute("SELECT COUNT(*) FROM mysql.user WHERE user = %s AND host = '%%'", (RO_USER,))
            if cur.fetchone()[0]:
                cur.execute(f"ALTER USER '{RO_USER}'@'%%' IDENTIFIED BY %s", (RO_PASSWORD,))
            else:
                cur.execute(f"CREATE USER '{RO_USER}'@'%%' IDENTIFIED BY %s", (RO_PASSWORD,))
            cur.execute(f"GRANT SELECT, SHOW VIEW ON `{t.database}`.* TO '{RO_USER}'@'%'")
    finally:
        conn.close()


def server_connections(t: Target, user: str) -> int:
    """Connections the server currently holds for `user` (ours only: the
    admin's own counting connection is a different user)."""
    if t.engine == "postgresql":
        return int(admin_scalar(t, "SELECT count(*) FROM pg_stat_activity WHERE usename = %s", (user,)))
    return int(admin_scalar(t, "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE USER = %s", (user,)))


def running_statements(t: Target, user: str, needle: str) -> int:
    """Statements by `user` whose text contains `needle` that are still running."""
    if t.engine == "postgresql":
        return int(admin_scalar(
            t, "SELECT count(*) FROM pg_stat_activity WHERE usename = %s AND state = 'active' "
               "AND query LIKE %s AND pid <> pg_backend_pid()", (user, f"%{needle}%")))
    return int(admin_scalar(
        t, "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE USER = %s "
           "AND COMMAND = 'Query' AND INFO LIKE %s", (user, f"%{needle}%")))
