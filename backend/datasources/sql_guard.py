"""Which SQL a customer database connection is allowed to run.

A SQL data source points StockAI at the customer's own production database.
Whatever text reaches it runs with the credentials the customer gave us, so
the text is checked here BEFORE a connection is opened, and the connection is
then opened read-only where the driver allows it (`service._readonly_session`).
Neither layer is trusted alone: this check is a parser, not a proof, and a
read-only transaction does not exist on every engine.

The check is an ALLOW-list on the shape of the statement, then a deny-list on
what may appear inside it:

1. Exactly one statement. A trailing ``;`` is tolerated; any other ``;`` is
   refused (psycopg2 runs every statement of a string, ``COMMIT`` included).
2. The statement starts with ``SELECT`` or ``WITH`` (optionally after ``(``).
3. No keyword that writes, changes the session or the transaction, or calls a
   procedure — anywhere, not only at the start: Postgres runs a ``DELETE``
   inside a CTE, ``SELECT ... INTO`` creates a table, and SQL Server runs a
   batch of statements with no ``;`` between them at all.
4. No function known to write, read the server's files, reach another server
   or execute a string as SQL.
5. Any construct whose boundaries differ between engines (dollar quoting,
   backslash escapes, MySQL executable comments, Oracle q-quotes) is refused
   rather than guessed at — a guess is how a string on one engine becomes code
   on another.

Words inside string literals, quoted identifiers and comments are not
keywords, so ``WHERE note = 'delete me'`` and a column named ``"update"`` pass.
An unquoted column that collides with a refused word (``set``) must be quoted;
the error names the word so the user knows which.
"""

from __future__ import annotations

import hashlib
import re

from backend.errors import AppError

MAX_SQL_CHARS = 100_000

_REJECTED = 400

# Words refused on every engine, matched as whole words outside literals,
# quoted identifiers and comments, case-insensitively. Only words that can
# appear INSIDE a single statement need to be here — the statement itself must
# start with SELECT/WITH — so a column named `comment` or `load` still works.
FORBIDDEN_KEYWORDS = frozenset({
    # data and schema changes (a Postgres CTE may hold a DELETE; SELECT ... INTO
    # creates a table on SQL Server and Postgres, writes a file on MySQL)
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "TRUNCATE", "INTO",
    "CREATE", "ALTER", "DROP", "GRANT", "REVOKE",
    # transactions
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT",
    # procedures and dynamic SQL
    "CALL", "EXEC", "EXECUTE",
    # SQL Server: a whole other query or another server
    "OPENROWSET", "OPENQUERY", "OPENDATASOURCE",
})

# SQL Server runs a BATCH: `SELECT 1 DROP TABLE t` is two statements with no
# ';' between them, so every statement keyword T-SQL has must be refused there.
# (Postgres, MySQL and Oracle need the ';' that is already refused.)
FORBIDDEN_KEYWORDS_MSSQL = frozenset({
    "SET", "DECLARE", "USE", "DENY", "SAVE", "SHUTDOWN", "KILL", "DBCC",
    "BACKUP", "RESTORE", "BULK", "RECONFIGURE", "WAITFOR", "CHECKPOINT",
    "WRITETEXT", "UPDATETEXT", "OPENXML", "ENABLE", "DISABLE", "GOTO",
})

# Row-lock clauses: a read that takes locks can stall the customer's ERP.
# (FOR UPDATE is already caught by UPDATE.)
_LOCK_CLAUSE = re.compile(
    r"\bFOR\s+(?:KEY\s+)?SHARE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b|\bWITH\s*\(\s*(?:UPDLOCK|XLOCK|TABLOCKX?|HOLDLOCK)\b",
    re.IGNORECASE,
)

# Function names (exact, case-insensitive) that write, read server files, run
# a string as SQL, or reach outside the database.
FORBIDDEN_FUNCTIONS = frozenset({
    # Postgres
    "SET_CONFIG", "NEXTVAL", "SETVAL", "QUERY_TO_XML", "QUERY_TO_XMLSCHEMA",
    "QUERY_TO_XML_AND_XMLSCHEMA", "CURSOR_TO_XML", "CURSOR_TO_XMLSCHEMA",
    "LO_IMPORT", "LO_EXPORT", "LO_UNLINK", "LO_CREATE", "LO_FROM_BYTEA", "LO_PUT",
    "TXID_CURRENT", "PG_CURRENT_XACT_ID",
    # MySQL
    "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS", "BENCHMARK",
    "SLEEP", "MASTER_POS_WAIT", "SOURCE_POS_WAIT",
    # SQL Server
    "XP_CMDSHELL",
})

# Function-name PREFIXES refused (exact names would miss the family).
FORBIDDEN_FUNCTION_PREFIXES = (
    "DBLINK",                 # Postgres: a second connection, outside our read-only transaction
    "PG_TERMINATE", "PG_CANCEL", "PG_RELOAD", "PG_ROTATE", "PG_SWITCH",
    "PG_PROMOTE", "PG_CREATE_", "PG_DROP_", "PG_LOGICAL_", "PG_REPLICATION_",
    "PG_READ_", "PG_LS_", "PG_STAT_FILE", "PG_FILE_", "PG_ADVISORY",
    "PG_START_BACKUP", "PG_STOP_BACKUP", "PG_BACKUP_", "PG_SLEEP",
    "PG_WAL_REPLAY_", "PG_IMPORT_", "PG_EXPORT_",
    "LO_",                    # Postgres large objects
    "DBMS_", "UTL_",          # Oracle packages (dbms_xmlgen runs a string as SQL)
    "XP_", "SP_",             # SQL Server extended / system procedures
)

ALLOWED_FIRST_KEYWORDS = ("SELECT", "WITH")

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$#@]*")
_FUNCTION_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$#@]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_$#@]*)*)\s*\(")


def _reject(code: str, message: str, **params) -> AppError:
    return AppError(code, message, status_code=_REJECTED, params=params)


def _unsupported(construct: str) -> AppError:
    return _reject(
        "sql_unsupported_syntax",
        f"The query uses {construct!r}, which is not accepted on a data-source "
        "query. Rewrite it without that construct.",
        construct=construct,
    )


def _mask(sql: str, engine: str) -> str:
    """Return `sql` with every literal, quoted identifier and comment replaced
    by spaces, so what remains is only code. Raises on anything ambiguous.

    Lengths are preserved (a masked span becomes spaces), so a comment can
    never glue two halves of a word together: ``DEL/**/ETE`` stays two words,
    exactly as the database reads it.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    # Brackets and backticks quote identifiers only on the engines that say so;
    # elsewhere their content is scanned as code (the conservative reading).
    bracket_quotes = engine == "mssql"
    backtick_quotes = engine == "mysql"

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # -- comment: only when followed by whitespace or end. MySQL needs the
        # space; Postgres does not. Not treating "--x" as a comment means we
        # scan MORE text than Postgres runs, never less.
        if ch == "-" and nxt == "-" and (i + 2 >= n or sql[i + 2] in " \t\r\n"):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
            continue

        if ch == "/" and nxt == "*":
            if i + 2 < n and sql[i + 2] in "!+":
                # /*! ... */ is EXECUTED by MySQL; /*+ ... */ is a hint.
                raise _unsupported("/*" + sql[i + 2])
            end = sql.find("*/", i + 2)
            if end == -1:
                raise _unsupported("/*")
            inner = sql[i + 2:end]
            if "/*" in inner:
                # Postgres nests block comments; MySQL does not. Refuse.
                raise _unsupported("/* /*")
            out.append(" " * (end + 2 - i))
            i = end + 2
            continue

        if ch == "#" and engine == "mysql":
            raise _unsupported("#")

        if ch == "$":
            # Postgres dollar quoting ($$...$$ or $tag$...$tag$): its body
            # would be scanned as code here and as a string there.
            # Inside an identifier ("v$session", "a$b$") a '$' is just a
            # character on every engine, Postgres included.
            prev = sql[i - 1] if i > 0 else ""
            in_identifier = prev.isalnum() or prev in "_$#@"
            m = None if in_identifier else re.match(r"\$[A-Za-z_][A-Za-z_0-9]*\$|\$\$", sql[i:i + 66])
            if m:
                raise _unsupported(m.group(0))
            out.append(ch)
            i += 1
            continue

        if ch == "'":
            # Oracle q'[...]' / nq'[...]' strings end at "]'", not at the
            # first quote: refuse the prefix instead of guessing the end.
            prefix = re.search(r"(?:^|[^A-Za-z0-9_$#@])([nN]?[qQ])$", sql[max(0, i - 3):i])
            if prefix:
                raise _unsupported(prefix.group(1) + "'")
            j = i + 1
            while True:
                if j >= n:
                    raise _unsupported("'")
                if sql[j] == "\\":
                    # MySQL and Postgres E'' strings escape with a backslash;
                    # standard strings do not. The two readings end the string
                    # at different places, so refuse.
                    raise _unsupported("\\")
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(" " * (j + 1 - i))
            i = j + 1
            continue

        closing = None
        if ch == '"':
            closing = '"'
        elif ch == "[" and bracket_quotes:
            closing = "]"
        elif ch == "`" and backtick_quotes:
            closing = "`"
        if closing:
            j = i + 1
            while True:
                if j >= n:
                    raise _unsupported(ch)
                if sql[j] == "\\" and engine == "mysql":
                    raise _unsupported("\\")
                if sql[j] == closing:
                    if j + 1 < n and sql[j + 1] == closing:
                        j += 2
                        continue
                    break
                j += 1
            out.append(" " * (j + 1 - i))
            i = j + 1
            continue

        if ch == "`":
            raise _unsupported("`")

        out.append(ch)
        i += 1
    return "".join(out)


def validate_read_only_sql(sql: str, engine: str = "postgresql") -> str:
    """Return the single read-only statement in `sql`, trailing ``;`` removed,
    or raise an ``AppError`` naming what was refused.

    The returned text is what must be sent to the database — never the
    original, which may carry the trailing semicolon.
    """
    if sql is None:
        raise _reject("sql_not_a_select", "The query is empty.", keyword="")
    if "\x00" in sql:
        raise _unsupported("NUL")
    if len(sql) > MAX_SQL_CHARS:
        raise _reject(
            "sql_too_long",
            f"The query is longer than {MAX_SQL_CHARS} characters.",
            max_chars=MAX_SQL_CHARS,
        )

    statement = sql.strip()
    masked = _mask(statement, engine)

    # One trailing semicolon (plus whitespace) is the usual way to end a query;
    # anything after it, or any semicolon before it, is a second statement.
    code = masked.rstrip()
    if code.endswith(";"):
        cut = len(code) - 1
        code = code[:cut].rstrip()
        statement = statement[:cut].rstrip()
        masked = masked[:cut]
    if ";" in code:
        raise _reject(
            "sql_multiple_statements",
            "Only one statement can run at a time. Remove the extra ';'.",
        )

    words = [m.group(0) for m in _WORD.finditer(code)]
    first_code = code.lstrip("( \t\r\n")
    first = _WORD.match(first_code)
    first_word = first.group(0).upper() if first else ""
    if first_word not in ALLOWED_FIRST_KEYWORDS:
        raise _reject(
            "sql_not_a_select",
            "Only a SELECT query (optionally starting with WITH) can run on a "
            "data source.",
            keyword=first_word or first_code[:20],
        )

    forbidden = FORBIDDEN_KEYWORDS | (FORBIDDEN_KEYWORDS_MSSQL if engine == "mssql" else frozenset())
    for word in words:
        upper = word.upper()
        if upper in forbidden:
            raise _reject(
                "sql_forbidden_keyword",
                f"The query contains {upper}, which can change data or the "
                "session. Only reads are allowed. If it is a column name, put "
                "it in double quotes.",
                keyword=upper,
            )

    lock = _LOCK_CLAUSE.search(code)
    if lock:
        clause = " ".join(lock.group(0).split()).upper()
        raise _reject(
            "sql_forbidden_keyword",
            f"The query contains {clause}, which locks rows. Only plain reads "
            "are allowed.",
            keyword=clause,
        )

    for match in _FUNCTION_CALL.finditer(code):
        dotted = re.sub(r"\s+", "", match.group(1)).upper()
        # Check every dotted part: schema.function and package.function
        # (Oracle dbms_xmlgen.getxml) both have to be caught.
        for part in dotted.split("."):
            if part in FORBIDDEN_FUNCTIONS or part.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise _reject(
                    "sql_forbidden_function",
                    f"The query calls {part.lower()}, which is not allowed on a "
                    "data-source query.",
                    function=part.lower(),
                )

    # Package-qualified references without parentheses (Oracle allows a
    # parameterless call with no "()") still name a refused family.
    for word in words:
        upper = word.upper()
        if upper.startswith(("DBMS_", "UTL_", "DBLINK")):
            raise _reject(
                "sql_forbidden_function",
                f"The query references {word.lower()}, which is not allowed on "
                "a data-source query.",
                function=word.lower(),
            )

    return statement


def statement_hash(sql: str) -> str:
    """Short, stable fingerprint of a statement for the audit trail — the trail
    says WHICH query ran without storing a query that may name customers."""
    normalized = " ".join((sql or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
