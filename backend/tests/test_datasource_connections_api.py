"""The data-source connection endpoints, end to end through the app database.

The "customer database" is the test Postgres itself (as in
test_sql_materialize.py). Every mutating endpoint has its permission pair
(viewer refused with the state unchanged; analyst succeeds with the change
read back from the database), and every test that stores or reports anything
about a connection checks that no secret reached the row, the response or the
audit trail.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest

from backend.config import settings
from backend.datasources import secrets as ds_secrets
from backend.db.connection import execute, query, query_one
from backend.tests.test_sql_materialize import _own_db_config

SECRET_MARK = "never-in-a-response"


@pytest.fixture
def erp_table():
    table = f"pytest_conn_{uuid4().hex[:8]}"
    execute(f"CREATE TABLE {table} (id int, sku text, qty int)")
    execute(f"INSERT INTO {table} SELECT g, 'SKU-' || g, g FROM generate_series(1, 30) g")
    yield table
    execute(f"DROP TABLE IF EXISTS {table}")


def _row(source_id):
    return query_one("SELECT connection_status, sql_config, updated_at FROM datasets WHERE id = %s",
                     (source_id,))


def _audit(tenant_id, source_id, action):
    return query("SELECT user_id, context FROM activity_logs WHERE tenant_id = %s AND action = %s "
                 "AND resource = %s", (tenant_id, action, source_id))


def _create(client, headers, **overrides):
    body = {"name": f"erp-{uuid4().hex[:6]}", **_own_db_config(), **overrides}
    r = client.post("/api/v1/data-sources/sql", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _no_secret(*blobs, secret):
    for blob in blobs:
        assert secret not in json.dumps(blob, default=str)


# ── Parse ─────────────────────────────────────────────────────────────────────

class TestParse:
    def test_viewer_refused(self, client, viewer_headers):
        r = client.post("/api/v1/data-sources/sql/parse", headers=viewer_headers,
                        json={"connection_string": "postgresql://u:p@h/db"})
        assert r.status_code == 403

    def test_analyst_gets_fields_but_never_the_password(self, client, analyst_headers):
        r = client.post("/api/v1/data-sources/sql/parse", headers=analyst_headers,
                        json={"connection_string": f"mysql://reader:{SECRET_MARK}@DB.Example.com:3307/shop"
                                                   "?ssl-mode=REQUIRED"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data == {"engine": "mysql", "host": "db.example.com", "port": 3307, "database": "shop",
                        "username": "reader", "ssl_mode": "require", "has_password": True}
        assert SECRET_MARK not in r.text

    def test_garbage_is_a_coded_400(self, client, analyst_headers):
        r = client.post("/api/v1/data-sources/sql/parse", headers=analyst_headers,
                        json={"connection_string": "not a connection string"})
        assert r.status_code == 400 and r.json()["error_code"] == "data_source_connection_string_invalid"


# ── Create ────────────────────────────────────────────────────────────────────

class TestCreate:
    def test_viewer_refused_and_nothing_stored(self, client, viewer_headers, test_tenant):
        before = query_one("SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s", (test_tenant["id"],))["n"]
        r = client.post("/api/v1/data-sources/sql", headers=viewer_headers,
                        json={"name": "x", **_own_db_config()})
        assert r.status_code == 403
        assert query_one("SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s",
                         (test_tenant["id"],))["n"] == before

    def test_from_a_connection_string_with_secrets_encrypted(self, client, analyst_headers, test_tenant):
        own = _own_db_config()
        cs = (f"postgresql://{own['username']}:{quote(own['password'], safe='')}@{own['host']}:"
              f"{own['port']}/{own['database']}?sslmode=prefer")
        r = client.post("/api/v1/data-sources/sql", headers=analyst_headers,
                        json={"name": "From string", "connection_string": cs})
        assert r.status_code == 200, r.text
        src = r.json()["data"]
        stored = _row(src["id"])["sql_config"]
        assert stored["host"] == own["host"] and stored["port"] == own["port"]
        assert stored["ssl_mode"] == "prefer"
        assert ds_secrets.decrypt(stored["password_enc"]) == own["password"]
        assert "password_enc" not in src["sql_config"] and src["sql_config"]["has_password"] is True
        if own["password"]:
            # Exact-value check, not a substring one: a test database whose password is
            # a common word ('postgres') legitimately appears inside 'postgresql'.
            def _strings(x):
                if isinstance(x, dict):
                    for v in x.values():
                        yield from _strings(v)
                elif isinstance(x, list):
                    for v in x:
                        yield from _strings(v)
                elif isinstance(x, str):
                    yield x
            assert own["password"] not in set(_strings(r.json()))
            _no_secret(_audit(test_tenant["id"], src["id"], "audit.dataset.created"), secret=own["password"])

    def test_private_literal_refused_when_the_installation_does_not_allow_it(
        self, client, analyst_headers, test_tenant, monkeypatch,
    ):
        monkeypatch.setattr(settings, "sql_sources_allow_private_hosts", False)
        before = query_one("SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s", (test_tenant["id"],))["n"]
        r = client.post("/api/v1/data-sources/sql", headers=analyst_headers,
                        json={"name": "x", **_own_db_config(), "host": "10.0.0.5"})
        assert r.status_code == 400 and r.json()["error_code"] == "data_source_host_not_allowed"
        r = client.post("/api/v1/data-sources/sql", headers=analyst_headers,
                        json={"name": "x", **_own_db_config(), "host": "169.254.169.254"})
        assert r.status_code == 400 and r.json()["error_code"] == "data_source_host_forbidden"
        assert query_one("SELECT COUNT(*) AS n FROM datasets WHERE tenant_id = %s",
                         (test_tenant["id"],))["n"] == before


# ── Edit ──────────────────────────────────────────────────────────────────────

class TestEdit:
    def test_viewer_refused_and_config_unchanged(self, client, auth_headers, viewer_headers):
        src = _create(client, auth_headers)
        before = _row(src["id"])
        r = client.patch(f"/api/v1/data-sources/{src['id']}/sql-config", headers=viewer_headers,
                         json={"database": "other"})
        assert r.status_code == 403
        assert _row(src["id"]) == before

    def test_analyst_changes_one_field_and_the_password_is_kept(
        self, client, auth_headers, analyst_headers, test_tenant,
    ):
        src = _create(client, auth_headers)
        old_enc = _row(src["id"])["sql_config"]["password_enc"]
        r = client.patch(f"/api/v1/data-sources/{src['id']}/sql-config", headers=analyst_headers,
                         json={"statement_timeout_s": 45})
        assert r.status_code == 200, r.text
        row = _row(src["id"])
        assert row["sql_config"]["statement_timeout_s"] == 45
        assert row["sql_config"]["password_enc"] == old_enc
        assert row["connection_status"] == "pending"
        audit = _audit(test_tenant["id"], src["id"], "audit.dataset.updated")
        assert audit and audit[-1]["context"]["after"]["password_changed"] is False
        assert "password_enc" not in json.dumps(audit, default=str)

    def test_moving_to_another_host_requires_the_password(self, client, auth_headers, analyst_headers):
        src = _create(client, auth_headers)
        before = _row(src["id"])
        r = client.patch(f"/api/v1/data-sources/{src['id']}/sql-config", headers=analyst_headers,
                         json={"host": "replica.example.com"})
        assert r.status_code == 400 and r.json()["error_code"] == "data_source_password_required"
        assert _row(src["id"]) == before

    def test_ca_certificate_is_stored_encrypted(self, client, auth_headers, analyst_headers):
        from backend.tests.test_datasource_connection_pure import _self_signed
        pem, _ = _self_signed("Corp CA")
        src = _create(client, auth_headers)
        r = client.patch(f"/api/v1/data-sources/{src['id']}/sql-config", headers=analyst_headers,
                         json={"ssl_mode": "verify-full", "ssl_ca": pem})
        assert r.status_code == 200, r.text
        cfg = _row(src["id"])["sql_config"]
        assert ds_secrets.decrypt(cfg["ssl_ca_enc"]) == pem.strip()
        assert "BEGIN CERTIFICATE" not in json.dumps({k: v for k, v in cfg.items() if k != "ssl_ca_enc"})
        assert "BEGIN CERTIFICATE" not in r.text and r.json()["data"]["sql_config"]["has_ssl_ca"] is True


# ── Staged test ───────────────────────────────────────────────────────────────

class TestStagedTest:
    def test_success_is_stored_with_its_stages_and_audited(self, client, auth_headers, analyst_headers, test_tenant):
        src = _create(client, auth_headers)
        r = client.post(f"/api/v1/data-sources/{src['id']}/test-connection", headers=analyst_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["ok"] is True
        assert [s["stage"] for s in data["stages"]] == ["dns", "tcp", "tls", "auth", "privileges", "select", "tables"]
        row = _row(src["id"])
        assert row["connection_status"] == "connected"
        assert row["sql_config"]["last_test"]["ok"] is True
        assert _audit(test_tenant["id"], src["id"], "audit.dataset.connection_tested")

    def test_a_wrong_password_is_stored_as_an_auth_failure_without_the_password(
        self, client, auth_headers, analyst_headers, test_tenant,
    ):
        wrong = f"wrong-{SECRET_MARK}"
        src = _create(client, auth_headers, password=wrong)
        r = client.post(f"/api/v1/data-sources/{src['id']}/test-connection", headers=analyst_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["ok"] is False and data["failed_stage"] == "auth" and data["error"] == "data_source_auth_failed"
        row = _row(src["id"])
        assert row["connection_status"] == "error"
        assert row["sql_config"]["last_test"]["failed_stage"] == "auth"
        _no_secret(data, row["sql_config"]["last_test"],
                   _audit(test_tenant["id"], src["id"], "audit.dataset.connection_tested"), secret=wrong)


# ── Schema browser, paging, export ────────────────────────────────────────────

@pytest.fixture
def connected(client, auth_headers, erp_table):
    src = _create(client, auth_headers)
    r = client.post(f"/api/v1/data-sources/{src['id']}/test-connection", headers=auth_headers)
    assert r.json()["data"]["ok"] is True, r.text
    return src


class TestBrowseAndRead:
    def test_viewer_cannot_open_the_customer_schema(self, client, viewer_headers, connected):
        assert client.get(f"/api/v1/data-sources/{connected['id']}/schema", headers=viewer_headers).status_code == 403
        assert client.get(f"/api/v1/data-sources/{connected['id']}/schema/columns?table=x",
                          headers=viewer_headers).status_code == 403

    def test_analyst_lists_tables_columns_and_a_runnable_preview(
        self, client, analyst_headers, connected, erp_table,
    ):
        r = client.get(f"/api/v1/data-sources/{connected['id']}/schema", headers=analyst_headers)
        assert r.status_code == 200, r.text
        table = next(t for t in r.json()["data"]["tables"] if t["name"] == erp_table)
        cols = client.get(f"/api/v1/data-sources/{connected['id']}/schema/columns",
                          params={"schema": table["schema"], "table": erp_table}, headers=analyst_headers)
        assert [c["name"] for c in cols.json()["data"]["columns"]] == ["id", "sku", "qty"]
        run = client.post(f"/api/v1/data-sources/{connected['id']}/execute-query", headers=analyst_headers,
                          json={"sql": table["select_sql"], "limit": 5})
        assert run.status_code == 200 and run.json()["data"]["row_count"] == 5

    def test_a_pending_source_has_no_schema(self, client, auth_headers, analyst_headers):
        src = _create(client, auth_headers)
        r = client.get(f"/api/v1/data-sources/{src['id']}/schema", headers=analyst_headers)
        assert r.status_code == 400 and r.json()["error_code"] == "data_source_not_connected"

    def test_paging(self, client, analyst_headers, connected, erp_table):
        url = f"/api/v1/data-sources/{connected['id']}/execute-query"
        sql = f"SELECT id FROM {erp_table} ORDER BY id"
        first = client.post(url, headers=analyst_headers, json={"sql": sql, "limit": 20}).json()["data"]
        assert first["has_more"] is True and first["rows"][-1]["id"] == 20
        second = client.post(url, headers=analyst_headers, json={"sql": sql, "limit": 20, "offset": 20}).json()["data"]
        assert second["has_more"] is False and [r["id"] for r in second["rows"]] == list(range(21, 31))

    def test_csv_export(self, client, analyst_headers, connected, erp_table):
        r = client.post(f"/api/v1/data-sources/{connected['id']}/export-query", headers=analyst_headers,
                        json={"sql": f"SELECT id, sku FROM {erp_table} ORDER BY id", "format": "csv"})
        assert r.status_code == 200, r.text
        assert r.headers["x-row-count"] == "30" and "query-result.csv" in r.headers["content-disposition"]
        lines = r.content.decode("utf-8-sig").splitlines()
        assert lines[0] == "id,sku" and len(lines) == 31


# ── Delete keeps what was materialized ────────────────────────────────────────

class TestDelete:
    def test_materialized_datasets_survive_the_source(self, client, analyst_headers, connected, erp_table, test_tenant):
        r = client.post(f"/api/v1/data-sources/{connected['id']}/materialize", headers=analyst_headers,
                        json={"sql": f"SELECT * FROM {erp_table}"})
        assert r.status_code == 200, r.text
        child = r.json()["data"]
        r = client.delete(f"/api/v1/data-sources/{connected['id']}", headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert query_one("SELECT id FROM datasets WHERE id = %s", (connected["id"],)) is None
        kept = query_one("SELECT id, file_path, parent_id FROM datasets WHERE id = %s", (child["id"],))
        assert kept is not None and Path(kept["file_path"]).exists()

    def test_a_source_feeding_a_schedule_cannot_be_deleted(
        self, client, analyst_headers, connected, erp_table, test_tenant,
    ):
        from backend.tests.test_scheduled_retrain_freshness import _schedule, _template
        child = client.post(f"/api/v1/data-sources/{connected['id']}/materialize", headers=analyst_headers,
                            json={"sql": f"SELECT * FROM {erp_table}"}).json()["data"]
        _schedule(test_tenant["id"], _template(test_tenant["id"], child["id"]))
        r = client.delete(f"/api/v1/data-sources/{connected['id']}", headers=analyst_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "data_source_feeds_schedule"
        assert query_one("SELECT id FROM datasets WHERE id = %s", (connected["id"],)) is not None


# ── A scheduled refresh that fails is loud ────────────────────────────────────

def test_failed_scheduled_refresh_rings_the_bell_and_marks_the_source(
    client, auth_headers, test_tenant, tmp_path, monkeypatch,
):
    from backend.datasources import service as ds_svc
    from backend.errors import AppError
    from backend.sessions import retrain_service
    from backend.tests.test_scheduled_retrain_freshness import _dataset, _schedule, _template

    tid = test_tenant["id"]
    src = _create(client, auth_headers)
    execute("UPDATE datasets SET connection_status = 'connected' WHERE id = %s", (src["id"],))
    snap = tmp_path / "snap.csv"
    snap.write_text("sku,date,qty\na,2026-01-01,1\n")
    template = _template(tid, _dataset(tid, snap, parent_id=src["id"], name="ERP (SQL)"))
    sched = _schedule(tid, template)

    def _down(*a, **k):
        raise AppError("data_source_auth_failed", "data_source_auth_failed", status_code=400)

    monkeypatch.setattr(ds_svc, "materialize_sql_source", _down)
    with pytest.raises(AppError):
        retrain_service.launch_scheduled_retrain(tid, sched, template)

    events = query("SELECT context, status FROM activity_logs WHERE tenant_id = %s "
                   "AND action = 'data.sql_refresh_failed' AND resource = %s", (tid, src["id"]))
    assert len(events) == 1
    ctx = events[0]["context"]
    assert ctx["severity"] == "critical" and ctx["reason"] == "sql_source_refresh_failed"
    assert ctx["reason_params"]["error_code"] == "data_source_auth_failed"
    assert _row(src["id"])["connection_status"] == "error"
