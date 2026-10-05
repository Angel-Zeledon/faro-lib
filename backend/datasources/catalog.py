"""What a connection can SEE and DO on the customer's database.

Every statement in this module is fixed text written here, run inside the same
read-only session as everything else (`client.open_session`); the only values
that vary — a schema or table name — travel as BOUND parameters, never
concatenated into SQL. The one place an identifier has to become SQL text (the
"preview this table" statement handed to the editor) goes through
`quote_ident`, and the result is then run through `sql_guard` like any
statement a person typed.

Contents:

* `quote_ident` / `select_preview_sql` — per-engine identifier quoting.
* `list_tables` / `list_columns` — the schema browser, capped.
* `write_privileges` — does this login hold privileges that could CHANGE
  data? StockAI never writes, but a login that can is one leaked password
  away from a damaged ERP; the connection test says so and hands over the
  exact least-privilege statements (`least_privilege_sql`).
* `tls_state` / `read_only_state` — what the session actually negotiated.
"""

from __future__ import annotations

import re
from typing import Any, Optional

MAX_TABLES = 1000
MAX_COLUMNS = 1000

# ── Identifiers ───────────────────────────────────────────────────────────────


def quote_ident(engine: str, name: str) -> str:
    """Quote one identifier for `engine`, doubling the closing quote."""
    text = str(name)
    if "\x00" in text:
        raise ValueError("identifier contains NUL")
    if engine == "mysql":
        return "`" + text.replace("`", "``") + "`"
    if engine == "mssql":
        return "[" + text.replace("]", "]]") + "]"
    return '"' + text.replace('"', '""') + '"'


def qualified(engine: str, schema: Optional[str], table: str) -> str:
    if schema:
        return f"{quote_ident(engine, schema)}.{quote_ident(engine, table)}"
    return quote_ident(engine, table)


def select_preview_sql(engine: str, schema: Optional[str], table: str, limit: int = 100) -> Optional[str]:
    """``SELECT * FROM <table>`` with the engine's row limit, or None when the
    name cannot be expressed in a statement `sql_guard` accepts (a backslash
    in a MySQL identifier, say) — the browser then offers no preview rather
    than a statement that will be refused."""
    from backend.datasources.sql_guard import validate_read_only_sql
    from backend.errors import AppError

    target = qualified(engine, schema, table)
    n = int(limit)
    if engine == "mssql":
        sql = f"SELECT TOP {n} * FROM {target}"
    elif engine == "oracle":
        sql = f"SELECT * FROM {target} FETCH FIRST {n} ROWS ONLY"
    else:
        sql = f"SELECT * FROM {target} LIMIT {n}"
    try:
        validate_read_only_sql(sql, engine)
    except (AppError, ValueError):
        return None
    return sql


# ── Schema browser ────────────────────────────────────────────────────────────

_TABLES_SQL = {
    "postgresql": """
        SELECT n.nspname AS table_schema, c.relname AS table_name,
               CASE WHEN c.relkind IN ('v', 'm') THEN 'view' ELSE 'table' END AS kind,
               CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS row_estimate
          FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND NOT c.relispartition
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg\\_toast%' ESCAPE '\\'
           AND n.nspname NOT LIKE 'pg\\_temp%' ESCAPE '\\'
           AND has_schema_privilege(n.oid, 'USAGE')
           AND has_table_privilege(c.oid, 'SELECT')
         ORDER BY 1, 2
         LIMIT :cap""",
    "mysql": """
        SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name,
               CASE WHEN TABLE_TYPE = 'VIEW' THEN 'view' ELSE 'table' END AS kind,
               TABLE_ROWS AS row_estimate
          FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
         ORDER BY 1, 2
         LIMIT :cap""",
    "mssql": """
        SELECT TOP (:cap) s.name AS table_schema, o.name AS table_name,
               CASE WHEN o.type = 'V' THEN 'view' ELSE 'table' END AS kind,
               (SELECT SUM(p.rows) FROM sys.partitions p
                 WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)) AS row_estimate
          FROM sys.objects o
          JOIN sys.schemas s ON s.schema_id = o.schema_id
         WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
         ORDER BY s.name, o.name""",
    "oracle": """
        SELECT table_schema, table_name, kind, row_estimate FROM (
            SELECT owner AS table_schema, table_name, 'table' AS kind, num_rows AS row_estimate
              FROM all_tables
             WHERE owner IN (SELECT username FROM all_users WHERE oracle_maintained = 'N')
            UNION ALL
            SELECT owner, view_name, 'view', NULL
              FROM all_views
             WHERE owner IN (SELECT username FROM all_users WHERE oracle_maintained = 'N')
        ) ORDER BY 1, 2 FETCH FIRST :cap ROWS ONLY""",
}

_COLUMNS_SQL = {
    "postgresql": """
        SELECT a.attname AS column_name,
               pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
               NOT a.attnotnull AS nullable
          FROM pg_catalog.pg_attribute a
          JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = :schema AND c.relname = :table
           AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY a.attnum
         LIMIT :cap""",
    "mysql": """
        SELECT COLUMN_NAME AS column_name, COLUMN_TYPE AS data_type,
               IS_NULLABLE = 'YES' AS nullable
          FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table
         ORDER BY ORDINAL_POSITION
         LIMIT :cap""",
    "mssql": """
        SELECT TOP (:cap) COLUMN_NAME AS column_name, DATA_TYPE AS data_type,
               CASE WHEN IS_NULLABLE = 'YES' THEN 1 ELSE 0 END AS nullable
          FROM INFORMATION_SCHEMA.COLUMNS
         WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table
         ORDER BY ORDINAL_POSITION""",
    "oracle": """
        SELECT column_name, data_type, CASE WHEN nullable = 'Y' THEN 1 ELSE 0 END AS nullable
          FROM all_tab_columns
         WHERE owner = :schema AND table_name = :table
         ORDER BY column_id FETCH FIRST :cap ROWS ONLY""",
}


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def list_tables(conn, engine: str, cap: int = MAX_TABLES) -> dict:
    """Tables and views the login can read, with row ESTIMATES (from the
    engine's statistics — never a COUNT(*) on a customer's big table)."""
    import sqlalchemy
    rows = conn.execute(sqlalchemy.text(_TABLES_SQL[engine]), {"cap": cap + 1}).fetchall()
    truncated = len(rows) > cap
    items = []
    for r in rows[:cap]:
        schema, name, kind, estimate = r[0], r[1], r[2], r[3]
        items.append({
            "schema": schema, "name": name, "kind": kind,
            "row_estimate": _int_or_none(estimate),
            "select_sql": select_preview_sql(engine, schema, name),
        })
    return {"tables": items, "truncated": truncated, "cap": cap}


def list_columns(conn, engine: str, schema: str, table: str, cap: int = MAX_COLUMNS) -> dict:
    import sqlalchemy
    rows = conn.execute(sqlalchemy.text(_COLUMNS_SQL[engine]),
                        {"schema": schema, "table": table, "cap": cap + 1}).fetchall()
    truncated = len(rows) > cap
    return {
        "schema": schema, "table": table, "truncated": truncated,
        "columns": [{"name": r[0], "type": str(r[1]), "nullable": bool(r[2])} for r in rows[:cap]],
    }


# ── Privileges ────────────────────────────────────────────────────────────────

_WRITE_WORDS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE", "DROP", "ALTER",
                "ALL PRIVILEGES", "REFERENCES", "INDEX", "TRIGGER", "EXECUTE", "GRANT OPTION",
                "SUPER", "FILE", "SHUTDOWN", "PROCESS")

# Grants that only let a login READ (or connect). Anything else is reported.
_MYSQL_READ_PRIVS = {"SELECT", "USAGE", "SHOW VIEW", "SHOW DATABASES", "LOCK TABLES"}


def _mysql_grant_privileges(grant: str) -> tuple[set[str], str]:
    """Privilege names and the object of one `SHOW GRANTS` line. The line
    itself is never returned: MariaDB prints the password hash in it."""
    m = re.match(r"(?is)^\s*GRANT\s+(.*?)\s+ON\s+(\S+)\s+TO\s", grant)
    if not m:
        return set(), ""
    privs_text, target = m.group(1), m.group(2)
    if privs_text.strip().upper().startswith("PROXY"):
        return {"PROXY"}, target
    privs = set()
    for part in re.split(r",(?![^(]*\))", privs_text):
        name = re.sub(r"\(.*?\)", "", part).strip().upper()
        if name:
            privs.add(name)
    if re.search(r"(?i)WITH\s+GRANT\s+OPTION", grant):
        privs.add("GRANT OPTION")
    return privs, target


def write_privileges(conn, engine: str) -> dict:
    """``{"can_write": bool, "privileges": [...], "superuser": bool}``.

    Raises when the catalogue cannot be read; the caller reports "unknown"."""
    import sqlalchemy
    t = sqlalchemy.text
    found: set[str] = set()
    superuser = False

    if engine == "postgresql":
        superuser = bool(conn.execute(t(
            "SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_catalog.pg_roles "
            "WHERE rolname = current_user")).scalar())
        row = conn.execute(t("""
            SELECT
              bool_or(has_table_privilege(c.oid, 'INSERT')),
              bool_or(has_table_privilege(c.oid, 'UPDATE')),
              bool_or(has_table_privilege(c.oid, 'DELETE')),
              bool_or(has_table_privilege(c.oid, 'TRUNCATE'))
              FROM pg_catalog.pg_class c
              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p')
               AND n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'""")).fetchone()
        for name, flag in zip(("INSERT", "UPDATE", "DELETE", "TRUNCATE"), row or ()):
            if flag:
                found.add(name)
        create = conn.execute(t("""
            SELECT bool_or(has_schema_privilege(n.oid, 'CREATE'))
              FROM pg_catalog.pg_namespace n
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'""")).scalar()
        if create:
            found.add("CREATE")
        if superuser:
            found.add("SUPERUSER")

    elif engine == "mysql":
        for (grant,) in conn.execute(t("SHOW GRANTS FOR CURRENT_USER()")).fetchall():
            privs, _target = _mysql_grant_privileges(str(grant))
            for p in privs:
                if p not in _MYSQL_READ_PRIVS:
                    found.add(p)
        superuser = any(p in found for p in ("ALL PRIVILEGES", "SUPER", "GRANT OPTION"))

    elif engine == "mssql":
        row = conn.execute(t(
            "SELECT IS_SRVROLEMEMBER('sysadmin'), IS_ROLEMEMBER('db_owner'), "
            "IS_ROLEMEMBER('db_datawriter'), IS_ROLEMEMBER('db_ddladmin'), "
            "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT'), "
            "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE'), "
            "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE'), "
            "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER')")).fetchone()
        names = ("SYSADMIN", "DB_OWNER", "DB_DATAWRITER", "DB_DDLADMIN",
                 "INSERT", "UPDATE", "DELETE", "ALTER")
        for name, flag in zip(names, row or ()):
            if flag == 1:
                found.add(name)
        superuser = "SYSADMIN" in found or "DB_OWNER" in found

    elif engine == "oracle":
        for (priv,) in conn.execute(t(
                "SELECT privilege FROM session_privs WHERE privilege LIKE '%ANY%' "
                "OR privilege IN ('CREATE TABLE', 'ALTER SYSTEM', 'DROP USER')")).fetchall():
            p = str(priv).upper()
            if not p.startswith(("SELECT ANY", "READ ANY")):
                found.add(p)
        owned = conn.execute(t("SELECT COUNT(*) FROM user_tables")).scalar() or 0
        if owned:
            found.add("OWNS_TABLES")
        superuser = any(p.startswith(("ALTER SYSTEM", "DROP ANY", "CREATE ANY", "GRANT ANY")) for p in found)

    return {"can_write": bool(found), "privileges": sorted(found)[:20], "superuser": superuser}


def least_privilege_sql(engine: str, database: str, schema: Optional[str] = None,
                        login: str = "stockai_reader") -> list[str]:
    """The statements a DBA runs to create a login that can only read.

    Placeholders are never filled with the real password: the DBA chooses
    it. Names are quoted for the engine."""
    q = lambda name: quote_ident(engine, name)  # noqa: E731
    if engine == "postgresql":
        sch = schema or "public"
        return [
            f"CREATE ROLE {q(login)} LOGIN PASSWORD '<choose-a-password>';",
            f"GRANT CONNECT ON DATABASE {q(database)} TO {q(login)};",
            f"GRANT USAGE ON SCHEMA {q(sch)} TO {q(login)};",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {q(sch)} TO {q(login)};",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {q(sch)} GRANT SELECT ON TABLES TO {q(login)};",
            f"ALTER ROLE {q(login)} SET default_transaction_read_only = on;",
        ]
    if engine == "mysql":
        return [
            f"CREATE USER '{login}'@'%' IDENTIFIED BY '<choose-a-password>';",
            f"GRANT SELECT, SHOW VIEW ON {q(database)}.* TO '{login}'@'%';",
        ]
    if engine == "mssql":
        return [
            f"CREATE LOGIN {q(login)} WITH PASSWORD = '<choose-a-password>';",
            f"USE {q(database)};",
            f"CREATE USER {q(login)} FOR LOGIN {q(login)};",
            f"ALTER ROLE db_datareader ADD MEMBER {q(login)};",
        ]
    owner = (schema or "<SCHEMA_OWNER>").upper()
    return [
        f'CREATE USER {login.upper()} IDENTIFIED BY "<choose-a-password>";',
        f"GRANT CREATE SESSION TO {login.upper()};",
        f"-- one per table StockAI should read, e.g.: GRANT SELECT ON {owner}.<TABLE> TO {login.upper()};",
    ]


# ── Session state ─────────────────────────────────────────────────────────────

def tls_state(conn, engine: str) -> dict:
    """``{"encrypted": bool|None, "detail": str|None}`` for THIS session."""
    import sqlalchemy
    t = sqlalchemy.text
    if engine == "postgresql":
        row = conn.execute(t(
            "SELECT ssl, version, cipher FROM pg_catalog.pg_stat_ssl "
            "WHERE pid = pg_catalog.pg_backend_pid()")).fetchone()
        if row is None:
            return {"encrypted": None, "detail": None}
        return {"encrypted": bool(row[0]), "detail": f"{row[1]} {row[2]}" if row[0] else None}
    if engine == "mysql":
        row = conn.execute(t("SHOW SESSION STATUS LIKE 'Ssl_cipher'")).fetchone()
        cipher = (row[1] if row else "") or ""
        return {"encrypted": bool(cipher), "detail": cipher or None}
    if engine == "mssql":
        row = conn.execute(t(
            "SELECT encrypt_option FROM sys.dm_exec_connections WHERE session_id = @@SPID")).fetchone()
        if row is None:
            return {"encrypted": None, "detail": None}
        return {"encrypted": str(row[0]).upper() == "TRUE", "detail": None}
    row = conn.execute(t("SELECT sys_context('USERENV', 'NETWORK_PROTOCOL') FROM dual")).fetchone()
    proto = str(row[0] or "").lower() if row else ""
    return {"encrypted": proto == "tcps" if proto else None, "detail": proto or None}


def read_only_state(conn, engine: str) -> Optional[bool]:
    """Whether the server itself will refuse a write in this session; None
    when the engine has no such mode (SQL Server)."""
    import sqlalchemy
    t = sqlalchemy.text
    if engine == "postgresql":
        return str(conn.execute(t("SHOW transaction_read_only")).scalar()).lower() == "on"
    if engine == "mysql":
        for var in ("@@session.transaction_read_only", "@@session.tx_read_only"):
            try:
                return bool(int(conn.execute(t(f"SELECT {var}")).scalar()))
            except Exception:  # noqa: BLE001 - the other spelling
                continue
        return None
    if engine == "oracle":
        return True   # SET TRANSACTION READ ONLY is issued on every session
    return None
