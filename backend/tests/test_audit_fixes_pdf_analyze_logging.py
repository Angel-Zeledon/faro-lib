"""Regression tests for the 2026-10-05 audit: PDF markup, analyze column names,
API-key event/logging, virgin-database migrations and the framework error codes.
"""

import io
import logging
from uuid import uuid4

import pytest

from backend.auth.api_key_auth import hash_key
from backend.db.connection import execute, query_one
from backend.inventory import service as inv_svc

CSV = (
    b"fecha,sku,cantidad\n"
    + b"".join(
        f"2026-01-{d:02d},A-1,{5 + d % 4}\n".encode() for d in range(1, 29)
    )
)


def _upload(client, headers) -> str:
    r = client.post(
        "/api/v1/data-sources/file", headers=headers,
        files={"file": ("ventas.csv", io.BytesIO(CSV), "text/csv")},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


class TestInventoryPdfEscapesUserText:

    @pytest.mark.parametrize("session_id", ["<b>x", "x</para>", "a&b<c>d"])
    def test_markup_in_the_session_id_still_yields_a_pdf(
        self, client, auth_headers, session_id,
    ):
        resp = client.get("/api/v1/inventory/report/pdf",
                          params={"session_id": session_id}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_markup_in_names_suppliers_and_notes_still_yields_a_pdf(
        self, client, auth_headers, monkeypatch,
    ):
        def _items(tenant_id, session_id, service_level=0.95, period="daily"):
            base = {
                "sku": "T<8>&1", "display_name": "Tuerca <M8> & arandela </b>",
                "supplier": "Ferreteria <Sur> & Hijos", "abc_xyz": "A<X",
                "current_stock": 3, "coverage_days": 2, "recommended_qty": 10,
                "inventory_value": 30.0,
            }
            return [
                dict(base, signal="PEDIR_YA"),
                dict(base, sku="T<8>&2", signal="OK"),
            ]

        monkeypatch.setattr(inv_svc, "get_inventory_status", _items)
        resp = client.get("/api/v1/inventory/report/pdf",
                          params={"session_id": "s<1>"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.content.startswith(b"%PDF")


class TestAnalyzeRejectsUnknownColumns:

    @pytest.mark.parametrize("param", ["date_col", "target_col", "sku_col"])
    def test_a_name_that_is_not_a_column_is_a_400_naming_it(
        self, client, analyst_headers, param,
    ):
        source = _upload(client, analyst_headers)
        q = {"date_col": "fecha", "target_col": "cantidad", "sku_col": "sku"}
        q[param] = "2026-10-01"
        resp = client.get(f"/api/v1/data-sources/{source}/analyze/A-1",
                          params=q, headers=analyst_headers)
        body = resp.json()
        assert resp.status_code == 400, resp.text
        assert body["error_code"] == "upload_column_missing"
        assert body["error_params"]["column"] == "2026-10-01"

    def test_a_malformed_date_filter_is_a_422_not_a_500(self, client, analyst_headers):
        source = _upload(client, analyst_headers)
        resp = client.get(
            f"/api/v1/data-sources/{source}/analyze/A-1",
            params={"date_col": "fecha", "target_col": "cantidad", "sku_col": "sku",
                    "date_from": "garbage"},
            headers=analyst_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "date_invalid_iso"

    def test_a_text_column_as_target_is_a_422_not_a_500(self, client, analyst_headers):
        source = _upload(client, analyst_headers)
        resp = client.get(
            f"/api/v1/data-sources/{source}/analyze/A-1",
            params={"date_col": "fecha", "target_col": "sku", "sku_col": "sku"},
            headers=analyst_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "analysis_failed"

    def test_valid_columns_still_analyse(self, client, analyst_headers):
        source = _upload(client, analyst_headers)
        resp = client.get(
            f"/api/v1/data-sources/{source}/analyze/A-1",
            params={"date_col": "fecha", "target_col": "cantidad", "sku_col": "sku"},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["report"]["n_observations"] == 28


class TestApiKeyEventAndLog:

    def test_minting_a_key_declares_every_detail_it_passes(
        self, client, auth_headers, test_tenant, caplog,
    ):
        with caplog.at_level(logging.WARNING, logger="backend.activity.events"):
            resp = client.post("/api/v1/api-keys",
                               json={"name": "k-scope", "scope": "write"},
                               headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert "undeclared detail keys" not in caplog.text
        row = query_one(
            "SELECT context FROM activity_logs "
            "WHERE tenant_id=%s AND action='account.api_key_created'",
            (test_tenant["id"],),
        )
        assert row["context"]["key_name"] == "k-scope"
        assert row["context"]["role"] == "analyst"

    def test_a_key_call_is_logged_with_its_tenant_and_key(
        self, client, test_tenant, caplog,
    ):
        raw = f"sk_live_{uuid4().hex}{uuid4().hex}"
        key_id = str(uuid4())
        execute(
            """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4)
               VALUES (%s, %s, 'log-key', %s, 'viewer', 'usr_test', %s)""",
            (key_id, test_tenant["id"], hash_key(raw), raw[-4:]),
        )
        with caplog.at_level(logging.INFO, logger="access"):
            resp = client.get("/api/v1/inventory/stock",
                              headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200, resp.text
        lines = [r.getMessage() for r in caplog.records
                 if r.name == "access" and "/inventory/stock" in r.getMessage()]
        assert lines, caplog.text
        assert f"tenant={test_tenant['id']}" in lines[-1]
        assert f"key={key_id}" in lines[-1]

    def test_an_unauthenticated_call_is_still_logged_with_a_dash(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="access"):
            client.get("/api/v1/inventory/stock")
        lines = [r.getMessage() for r in caplog.records if r.name == "access"]
        assert lines and "tenant=-" in lines[-1] and "key=" not in lines[-1]


class TestFrameworkErrorsCarryACode:

    def test_missing_credential(self, client):
        r = client.get("/api/v1/inventory/stock")
        assert (r.status_code, r.json()["error_code"]) == (401, "unauthenticated")
        assert r.json()["detail"] == "Not authenticated"

    def test_unknown_route(self, client):
        r = client.get("/api/v1/definitely-not-a-route")
        assert (r.status_code, r.json()["error_code"]) == (404, "not_found")
        assert r.json()["detail"] == "Not Found"

    def test_wrong_method(self, client):
        r = client.delete("/api/v1/auth/login")
        assert (r.status_code, r.json()["error_code"]) == (405, "method_not_allowed")

    def test_the_key_rate_limit_sentence_maps_to_a_code(self):
        from backend.error_codes import describe_http_error
        sentence = ("Rate limit exceeded: 120 requests per minute per API key, and the "
                    "daily ceiling of this tenant's plan.")
        assert describe_http_error(sentence) == ("rate_limited", {})


class TestJobLogsTailIsBounded:

    def test_a_negative_tail_is_a_422_not_a_500(self, client, auth_headers):
        r = client.get("/api/v1/jobs/job_nope/logs", params={"tail": -1},
                       headers=auth_headers)
        assert r.status_code == 422, r.text


class TestVirginDatabaseMigrations:

    def test_run_all_creates_the_tables_a_migration_alters(self):
        """`add_user_preferences_dm_sms_enabled` ALTERs a table that only the
        server's startup used to create. Drop it, run the migrations the way
        seed_demo does, and it must come back."""
        from backend.db import migrations
        execute("DROP TABLE IF EXISTS user_preferences")
        migrations.run_all()
        row = query_one(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'user_preferences' AND column_name = 'dm_sms_enabled'"
        )
        assert row is not None
