"""SQL data sources: who may run SQL on the customer's database, and what SQL.

Before this, `execute-query` and `export-query` only asked for a logged-in user
(a viewer, or a `read` API key), passed the caller's text straight to the
customer's database, and opened the connection read-write. A string carrying
its own `; DELETE ...` or `COMMIT` would have run.

Real end-to-end, like test_sql_materialize.py: the "customer database" is the
test Postgres itself, so the read-only transaction is exercised by a real
server. Every refusal asserts the customer's table still holds its rows.
"""

from urllib.parse import urlparse
from uuid import uuid4

import pytest

from backend.auth.api_key_auth import hash_key
from backend.config import settings
from backend.datasources.sql_guard import statement_hash
from backend.db.connection import execute, query, query_one


def _own_db_config():
    u = urlparse(settings.database_url)
    return {
        "host": u.hostname or "127.0.0.1",
        "port": u.port or 5432,
        "database": (u.path or "/postgres").lstrip("/"),
        "username": u.username or "postgres",
        "password": u.password or "",
        "engine": "postgresql",
    }


@pytest.fixture
def erp_table():
    table = f"pytest_erp_guard_{uuid4().hex[:8]}"
    execute(f"CREATE TABLE {table} (sku TEXT NOT NULL, qty INT NOT NULL)")
    execute(f"INSERT INTO {table} VALUES ('A', 1), ('B', 2), ('C', 3)")
    yield table
    execute(f"DROP TABLE IF EXISTS {table}")


@pytest.fixture
def sql_source(client, auth_headers, erp_table):
    resp = client.post(
        "/api/v1/data-sources/sql",
        json={"name": f"erp-{uuid4().hex[:6]}", **_own_db_config()},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    src = resp.json()["data"]
    resp = client.post(f"/api/v1/data-sources/{src['id']}/test-connection", headers=auth_headers)
    assert resp.json()["data"]["ok"] is True, resp.text
    return src


def _rows_left(table: str) -> int:
    return query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]


def _audit_rows(tenant_id: str, source_id: str, action: str) -> list[dict]:
    return query(
        "SELECT user_id, context FROM activity_logs WHERE tenant_id = %s "
        "AND action = %s AND resource = %s",
        (tenant_id, action, source_id),
    )


def _mint_key(tenant_id: str, role: str) -> dict:
    raw = f"sk_live_{uuid4().hex}{uuid4().hex}"
    execute(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4)
           VALUES (gen_random_uuid()::text, %s, %s, %s, %s, 'usr_test', %s)""",
        (tenant_id, f"key-{role}", hash_key(raw), role, raw[-4:]),
    )
    return {"Authorization": f"Bearer {raw}"}


# ── Who ──────────────────────────────────────────────────────────────────────

class TestWhoMayRunSql:
    def test_viewer_cannot_execute_and_nothing_is_audited(
        self, client, viewer_headers, test_tenant, sql_source, erp_table,
    ):
        resp = client.post(
            f"/api/v1/data-sources/{sql_source['id']}/execute-query",
            json={"sql": f"SELECT * FROM {erp_table}"}, headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert _audit_rows(test_tenant["id"], sql_source["id"], "audit.dataset.query_run") == []

    def test_analyst_executes_and_the_run_is_audited(
        self, client, analyst_headers, analyst_user, test_tenant, sql_source, erp_table,
    ):
        sql = f"SELECT sku, qty FROM {erp_table} ORDER BY sku"
        resp = client.post(
            f"/api/v1/data-sources/{sql_source['id']}/execute-query",
            json={"sql": sql}, headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["row_count"] == 3

        rows = _audit_rows(test_tenant["id"], sql_source["id"], "audit.dataset.query_run")
        assert len(rows) == 1
        assert rows[0]["user_id"] == analyst_user["user"]["id"]
        after = rows[0]["context"]["after"]
        assert after == {"statement_sha256": statement_hash(sql), "rows": 3}
        # The statement text itself is never stored in the trail.
        assert erp_table not in str(rows[0]["context"])

    def test_viewer_cannot_export(self, client, viewer_headers, test_tenant, sql_source, erp_table):
        resp = client.post(
            f"/api/v1/data-sources/{sql_source['id']}/export-query",
            json={"sql": f"SELECT * FROM {erp_table}"}, headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert _audit_rows(test_tenant["id"], sql_source["id"], "audit.export.query") == []

    def test_read_key_cannot_export_but_write_key_can(
        self, client, test_tenant, sql_source, erp_table,
    ):
        body = {"sql": f"SELECT * FROM {erp_table}"}
        url = f"/api/v1/data-sources/{sql_source['id']}/export-query"

        resp = client.post(url, json=body, headers=_mint_key(test_tenant["id"], "viewer"))
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "api_key_scope_insufficient"
        assert _audit_rows(test_tenant["id"], sql_source["id"], "audit.export.query") == []

        resp = client.post(url, json=body, headers=_mint_key(test_tenant["id"], "analyst"))
        assert resp.status_code == 200, resp.text
        rows = _audit_rows(test_tenant["id"], sql_source["id"], "audit.export.query")
        assert len(rows) == 1
        assert rows[0]["context"]["after"]["rows"] == 3
        assert rows[0]["context"]["actor_kind"] == "api_key"


# ── What ─────────────────────────────────────────────────────────────────────

class TestOnlyOneReadRuns:
    @pytest.mark.parametrize("template, code", [
        ("SELECT 1; DELETE FROM {t}", "sql_multiple_statements"),
        ("SELECT 1; COMMIT; DELETE FROM {t}", "sql_multiple_statements"),
        ("WITH gone AS (DELETE FROM {t} RETURNING *) SELECT * FROM gone", "sql_forbidden_keyword"),
        ("DELETE FROM {t}", "sql_not_a_select"),
        ("SELECT * INTO pytest_copy_{t} FROM {t}", "sql_forbidden_keyword"),
        ("SELECT $$x$$; DELETE FROM {t}", "sql_unsupported_syntax"),
    ])
    def test_write_attempt_refused_and_table_intact(
        self, client, analyst_headers, test_tenant, sql_source, erp_table, template, code,
    ):
        resp = client.post(
            f"/api/v1/data-sources/{sql_source['id']}/execute-query",
            json={"sql": template.format(t=erp_table)}, headers=analyst_headers,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == code
        assert _rows_left(erp_table) == 3
        assert query_one("SELECT to_regclass(%s) AS r", (f"pytest_copy_{erp_table}",))["r"] is None
        assert _audit_rows(test_tenant["id"], sql_source["id"], "audit.dataset.query_run") == []

    def test_export_refuses_a_write_too(self, client, analyst_headers, sql_source, erp_table):
        resp = client.post(
            f"/api/v1/data-sources/{sql_source['id']}/export-query",
            json={"sql": f"SELECT 1; DELETE FROM {erp_table}"}, headers=analyst_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "sql_multiple_statements"
        assert _rows_left(erp_table) == 3

    def test_materialize_refuses_a_write_and_creates_no_dataset(
        self, client, analyst_headers, test_tenant, sql_source, erp_table,
    ):
        resp = client.post(
            f"/api/v1/data-sources/{sql_source['id']}/materialize",
            json={"sql": f"WITH g AS (DELETE FROM {erp_table} RETURNING *) SELECT * FROM g"},
            headers=analyst_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "sql_forbidden_keyword"
        assert _rows_left(erp_table) == 3
        assert query("SELECT id FROM datasets WHERE tenant_id = %s AND parent_id = %s",
                     (test_tenant["id"], sql_source["id"])) == []

    def test_saving_a_write_is_refused_and_nothing_is_stored(
        self, client, analyst_headers, sql_source, erp_table,
    ):
        """The saved query later runs for viewers (preview, analysis) and
        unattended (scheduled refresh) — it is checked when it is stored."""
        resp = client.patch(
            f"/api/v1/data-sources/{sql_source['id']}/query",
            json={"sql": f"SELECT 1; DROP TABLE {erp_table}"}, headers=analyst_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "sql_multiple_statements"
        row = query_one("SELECT saved_query FROM datasets WHERE id = %s", (sql_source["id"],))
        assert row["saved_query"] is None

    def test_a_legacy_saved_write_is_refused_at_preview(
        self, client, viewer_headers, sql_source, erp_table,
    ):
        """A destructive query stored before the check existed still never runs."""
        execute("UPDATE datasets SET saved_query = %s WHERE id = %s",
                (f"DELETE FROM {erp_table}", sql_source["id"]))
        resp = client.get(f"/api/v1/data-sources/{sql_source['id']}/preview", headers=viewer_headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "sql_not_a_select"
        assert _rows_left(erp_table) == 3


class TestTheConnectionItselfIsReadOnly:
    """Second layer: even if the statement check had a hole, Postgres refuses
    the write because every transaction on the connection is READ ONLY."""

    @pytest.mark.parametrize("template", [
        "WITH gone AS (DELETE FROM {t} RETURNING *) SELECT * FROM gone",
        "SELECT 1; DELETE FROM {t}",
        "SELECT 1; COMMIT; DELETE FROM {t}",
    ])
    def test_write_fails_on_the_server_with_the_guard_bypassed(
        self, client, analyst_headers, sql_source, erp_table, monkeypatch, template,
    ):
        from backend.datasources import sql_guard
        monkeypatch.setattr(sql_guard, "validate_read_only_sql", lambda sql, engine="postgresql": sql)
        resp = client.post(
            f"/api/v1/data-sources/{sql_source['id']}/execute-query",
            json={"sql": template.format(t=erp_table)}, headers=analyst_headers,
        )
        assert resp.status_code == 400, resp.text
        # The SERVER refuses the write. The connection layer now names that refusal
        # with its own code; the generic code carries the server's reason, which for a
        # data-modifying CTE is Postgres' cursor message rather than 'read-only'.
        code = resp.json()["error_code"]
        assert code in ("data_source_read_only_violation", "sql_query_failed"), code
        if code == "sql_query_failed":
            reason = resp.json()["error_params"]["reason"]
            assert "read-only" in reason or "data-modifying" in reason, reason
        assert _rows_left(erp_table) == 3
