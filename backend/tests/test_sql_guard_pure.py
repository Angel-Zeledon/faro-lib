"""The statement check that stands in front of every customer-database query.

Pure: no database, no app. The engine-level read-only transaction is the
second layer and is tested against a real server in
test_datasource_sql_guard.py.
"""

import pytest

from backend.datasources.sql_guard import (
    MAX_SQL_CHARS, statement_hash, validate_read_only_sql,
)
from backend.errors import AppError


def _refused(sql: str, engine: str = "postgresql") -> AppError:
    with pytest.raises(AppError) as exc:
        validate_read_only_sql(sql, engine)
    assert exc.value.status_code == 400
    return exc.value


class TestPlainReadsPass:
    @pytest.mark.parametrize("sql, engine", [
        ("SELECT 1", "postgresql"),
        ("select sku, sum(qty) from sales group by sku", "postgresql"),
        ("WITH m AS (SELECT * FROM s) SELECT * FROM m", "postgresql"),
        ("(SELECT 1) UNION (SELECT 2)", "postgresql"),
        ("SELECT date_trunc('month', fecha), SUM(ventas) FROM s GROUP BY 1", "postgresql"),
        ("SELECT 'delete; drop table x' AS note", "postgresql"),
        ("SELECT 'it''s' AS x", "postgresql"),
        ('SELECT "update", "Delete" FROM t', "postgresql"),
        ("SELECT comment, load, lock FROM t", "postgresql"),
        ("SELECT DEL/**/ETE FROM t", "postgresql"),
        ("SELECT 1 -- trailing note", "postgresql"),
        ("SELECT [Order Date], [Update] FROM t", "mssql"),
        ("SELECT `update` FROM t", "mysql"),
        ("SELECT CAST(x AS CHAR CHARACTER SET utf8mb4) FROM t", "mysql"),
        ("SELECT * FROM v$session", "oracle"),
        ("SELECT a$b$c FROM t", "postgresql"),
    ])
    def test_accepted(self, sql, engine):
        assert validate_read_only_sql(sql, engine) == sql

    def test_one_trailing_semicolon_is_removed(self):
        assert validate_read_only_sql("SELECT 1;  ", "postgresql") == "SELECT 1"
        assert validate_read_only_sql("SELECT 1; -- end", "postgresql") == "SELECT 1"


class TestOneStatementOnly:
    @pytest.mark.parametrize("sql", [
        "SELECT 1; COMMIT",
        "SELECT 1; DELETE FROM t",
        "SELECT 1;;",
        "SELECT 1 /* x */ ; SELECT 2",
        # "--1" is not a comment on MySQL, so its ';' is real code: refused.
        "SELECT 1 --1; DROP TABLE t",
    ])
    def test_multiple_statements(self, sql):
        assert _refused(sql).code == "sql_multiple_statements"

    def test_sql_server_batch_without_semicolon(self):
        """T-SQL runs `SELECT 1 DROP TABLE t` as two statements."""
        err = _refused("SELECT 1 DROP TABLE t", "mssql")
        assert err.code == "sql_forbidden_keyword"
        assert err.params == {"keyword": "DROP"}

    def test_sql_server_statement_words(self):
        for sql in ("SELECT 1 SET ROWCOUNT 0", "SELECT 1 DECLARE @x INT",
                    "SELECT 1 WAITFOR DELAY '00:01'", "SELECT 1 USE master"):
            assert _refused(sql, "mssql").code == "sql_forbidden_keyword", sql


class TestOnlyReads:
    @pytest.mark.parametrize("sql, keyword", [
        ("DELETE FROM t", "DELETE"),
        ("EXPLAIN ANALYZE DELETE FROM t", "EXPLAIN"),
        ("COMMIT", "COMMIT"),
        ("CREATE TABLE x (a int)", "CREATE"),
        ("VALUES (1)", "VALUES"),
    ])
    def test_must_start_with_select_or_with(self, sql, keyword):
        err = _refused(sql)
        assert err.code == "sql_not_a_select"
        assert err.params == {"keyword": keyword}

    @pytest.mark.parametrize("sql, keyword", [
        ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "DELETE"),
        ("WITH u AS (UPDATE t SET a = 1 RETURNING *) SELECT * FROM u", "UPDATE"),
        ("WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM i", "INSERT"),
        ("SELECT * INTO copy FROM t", "INTO"),
        ("SELECT * FROM t INTO OUTFILE '/tmp/x'", "INTO"),
        ("SELECT * FROM t FOR UPDATE", "UPDATE"),
        ("SELECT * FROM t FOR SHARE", "FOR SHARE"),
        ("SELECT * FROM t LOCK IN SHARE MODE", "LOCK IN SHARE MODE"),
        ("SELECT * FROM OPENROWSET('x', 'y', 'z')", "OPENROWSET"),
    ])
    def test_forbidden_keyword_anywhere(self, sql, keyword):
        err = _refused(sql)
        assert err.code == "sql_forbidden_keyword"
        assert err.params == {"keyword": keyword}

    def test_sql_server_lock_hint(self):
        assert _refused("SELECT * FROM t WITH (UPDLOCK)", "mssql").code == "sql_forbidden_keyword"

    @pytest.mark.parametrize("sql, function, engine", [
        ("SELECT dblink_exec('db', 'DELETE FROM t')", "dblink_exec", "postgresql"),
        ("SELECT * FROM dblink('db', 'SELECT 1') AS x(a int)", "dblink", "postgresql"),
        ("SELECT pg_terminate_backend(42)", "pg_terminate_backend", "postgresql"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file", "postgresql"),
        ("SELECT set_config('default_transaction_read_only', 'off', false)", "set_config", "postgresql"),
        ("SELECT nextval('seq')", "nextval", "postgresql"),
        ("SELECT query_to_xml('DELETE FROM t', true, false, '')", "query_to_xml", "postgresql"),
        ("SELECT lo_export(1, '/tmp/x')", "lo_export", "postgresql"),
        ("SELECT LOAD_FILE('/etc/passwd')", "load_file", "mysql"),
        ("SELECT dbms_xmlgen.getxml('DELETE FROM t') FROM dual", "dbms_xmlgen", "oracle"),
        ("SELECT public.pg_sleep(100)", "pg_sleep", "postgresql"),
    ])
    def test_forbidden_function(self, sql, function, engine):
        err = _refused(sql, engine)
        assert err.code == "sql_forbidden_function"
        assert err.params == {"function": function}


class TestAmbiguousSyntaxIsRefused:
    """Where engines disagree about where a string ends, guessing is how a
    string on one engine becomes code on another."""

    @pytest.mark.parametrize("sql, construct, engine", [
        ("SELECT $$'$$; DROP TABLE t; --'", "$$", "postgresql"),
        ("SELECT $tag$x$tag$", "$tag$", "postgresql"),
        ("SELECT 'a\\'; DROP TABLE t; -- '", "\\", "mysql"),
        ("SELECT E'a\\'' FROM t", "\\", "postgresql"),
        ("SELECT /*!50000 DROP */ 1", "/*!", "mysql"),
        ("SELECT 1 # comment", "#", "mysql"),
        ("SELECT q'[x]' FROM dual", "q'", "oracle"),
        ("SELECT 'unterminated", "'", "postgresql"),
        ("SELECT 1 /* never closed", "/*", "postgresql"),
        ("SELECT /* a /* nested */ */ 1", "/* /*", "postgresql"),
        ("SELECT 1\x00", "NUL", "postgresql"),
    ])
    def test_refused(self, sql, construct, engine):
        err = _refused(sql, engine)
        assert err.code == "sql_unsupported_syntax"
        assert err.params == {"construct": construct}

    def test_too_long(self):
        err = _refused("SELECT " + "1" * MAX_SQL_CHARS)
        assert err.code == "sql_too_long"
        assert err.params == {"max_chars": MAX_SQL_CHARS}


class TestStatementHash:
    def test_whitespace_does_not_change_it_but_content_does(self):
        assert statement_hash("SELECT  1\n FROM t") == statement_hash("SELECT 1 FROM t")
        assert statement_hash("SELECT 1 FROM t") != statement_hash("SELECT 2 FROM t")
        assert len(statement_hash("SELECT 1")) == 16
