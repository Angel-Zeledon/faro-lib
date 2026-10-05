"""`POST /feedback`: the in-app "Send feedback" report.

State is asserted straight from Postgres and from the disk, not from the
response. Every role may report (viewers included: it changes no business data),
the screenshot is a file under storage/feedback/<tenant>/ that nothing serves,
and erasing the tenant removes the file as well as the rows.
"""
import base64
import io
import json
import zipfile
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest

from backend.db.connection import query, query_one
from backend.feedback import validation as v
from backend.storage import paths

pytestmark = pytest.mark.integration

URL = "/api/v1/feedback"

# A real 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _body(**over):
    base = {
        "message": "The purchase screen showed an empty table after I saved.",
        "error_code": "HTTP 500",
        "page_path": "/pedidos?token=secret#x",
        "user_agent": "pytest-agent/1.0",
        "app_version": "1.0.0",
    }
    base.update(over)
    return base


def _shot(raw: bytes = PNG) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _rows(tenant_id):
    return query(
        "SELECT * FROM feedback_reports WHERE tenant_id = %s ORDER BY created_at", (tenant_id,)
    )


@pytest.fixture
def contact(monkeypatch):
    """An instance contact address, and a capture of what the mail transport got."""
    sent = []

    def fake_send(to, subject, html, attachment=None, tenant_id=None):
        sent.append({"to": to, "subject": subject, "html": html, "attachment": attachment})

    cfg = SimpleNamespace(contact_email="ops@example.test")
    monkeypatch.setattr("backend.service_config.resolver.effective", lambda tenant_id=None: cfg)
    monkeypatch.setattr("backend.notifications.email._send", fake_send)
    return sent


class TestWhoMayReport:

    def test_a_viewer_may_report_and_the_row_is_theirs(
        self, client, viewer_headers, viewer_user, test_tenant, contact,
    ):
        r = client.post(URL, json=_body(), headers=viewer_headers)
        assert r.status_code == 201, r.text
        row = query_one("SELECT * FROM feedback_reports WHERE id = %s", (r.json()["data"]["id"],))
        assert row["tenant_id"] == test_tenant["id"]
        assert row["user_id"] == viewer_user["user"]["id"]
        assert row["account_email"] == viewer_user["email"]
        assert row["message"].startswith("The purchase screen")

    def test_an_analyst_and_an_admin_may_report(self, client, analyst_headers, auth_headers, test_tenant, contact):
        for headers in (analyst_headers, auth_headers):
            assert client.post(URL, json=_body(), headers=headers).status_code == 201
        assert len(_rows(test_tenant["id"])) == 2

    def test_no_token_is_refused_and_nothing_is_stored(self, client, test_tenant):
        r = client.post(URL, json=_body())
        assert r.status_code in (401, 403)
        assert _rows(test_tenant["id"]) == []

    def test_there_is_no_route_that_reads_reports_back(self, client, auth_headers, test_tenant, contact):
        rid = client.post(URL, json=_body(screenshot=_shot()), headers=auth_headers).json()["data"]["id"]
        for path in (URL, f"{URL}/{rid}", f"{URL}/{rid}/screenshot"):
            assert client.get(path, headers=auth_headers).status_code in (404, 405)

    def test_an_api_key_cannot_file_a_report(self):
        # The router tag is INTERNAL, so a key is refused before the handler runs.
        from backend.api import public_surface
        assert "feedback" in public_surface.INTERNAL_TAGS
        assert "feedback" not in public_surface.EXPOSED_TAGS


class TestWhatIsStored:

    def test_metadata_is_cut_and_the_page_path_loses_its_query_string(
        self, client, auth_headers, test_tenant, contact,
    ):
        client.post(URL, json=_body(user_agent="U" * 900), headers=auth_headers)
        row = _rows(test_tenant["id"])[0]
        assert row["page_path"] == "/pedidos"
        assert len(row["user_agent"]) == v.MAX_USER_AGENT_CHARS
        assert row["error_code"] == "HTTP 500"
        assert row["app_version"] == "1.0.0"

    def test_a_too_short_message_is_refused_and_stores_nothing(
        self, client, auth_headers, test_tenant, contact,
    ):
        r = client.post(URL, json=_body(message="  "), headers=auth_headers)
        assert r.status_code == 422
        assert r.json()["error_code"] == "feedback_message_required"
        assert _rows(test_tenant["id"]) == []


class TestConsent:

    def test_both_default_to_refused_with_no_timestamp(self, client, auth_headers, test_tenant, contact):
        client.post(URL, json=_body(), headers=auth_headers)
        row = _rows(test_tenant["id"])[0]
        assert (row["consent_reply"], row["consent_news"]) == (False, False)
        assert row["consent_reply_at"] is None and row["consent_news_at"] is None

    def test_the_two_are_independent(self, client, auth_headers, test_tenant, contact):
        client.post(URL, json=_body(consent_reply=True), headers=auth_headers)
        client.post(URL, json=_body(consent_news=True), headers=auth_headers)
        reply_only, news_only = _rows(test_tenant["id"])
        assert reply_only["consent_reply"] is True and reply_only["consent_reply_at"] is not None
        assert reply_only["consent_news"] is False and reply_only["consent_news_at"] is None
        assert news_only["consent_news"] is True and news_only["consent_news_at"] is not None
        assert news_only["consent_reply"] is False and news_only["consent_reply_at"] is None

    def test_a_non_boolean_does_not_count_as_consent(self, client, auth_headers, test_tenant, contact):
        r = client.post(URL, json=_body(consent_news="maybe"), headers=auth_headers)
        assert r.status_code == 422
        assert _rows(test_tenant["id"]) == []


class TestScreenshotFile:

    def test_it_is_a_file_under_the_tenant_folder_never_a_column(
        self, client, auth_headers, test_tenant, contact,
    ):
        r = client.post(URL, json=_body(screenshot=_shot()), headers=auth_headers)
        assert r.status_code == 201, r.text
        rid = r.json()["data"]["id"]
        row = query_one("SELECT * FROM feedback_reports WHERE id = %s", (rid,))
        assert row["screenshot_path"] == f"{rid}.png"
        stored = paths.feedback_dir(test_tenant["id"]) / row["screenshot_path"]
        assert stored.is_file() and stored.read_bytes() == PNG
        assert not any("screenshot" in c and c != "screenshot_path" for c in row)

    def test_no_screenshot_means_no_file_and_a_null_path(self, client, auth_headers, test_tenant, contact):
        client.post(URL, json=_body(), headers=auth_headers)
        assert _rows(test_tenant["id"])[0]["screenshot_path"] is None
        assert not paths.feedback_dir(test_tenant["id"]).exists()

    def test_a_gif_is_refused_and_leaves_no_row_and_no_file(self, client, auth_headers, test_tenant, contact):
        gif = b"GIF89a" + b"\x00" * 60
        r = client.post(URL, json=_body(screenshot=_shot(gif)), headers=auth_headers)
        assert r.status_code == 422
        assert r.json()["error_code"] == "feedback_screenshot_invalid"
        assert _rows(test_tenant["id"]) == []
        assert not paths.feedback_dir(test_tenant["id"]).exists()

    def test_an_oversize_screenshot_is_refused_with_413(self, client, auth_headers, test_tenant, contact):
        big = PNG + b"\x00" * (v.MAX_SCREENSHOT_BYTES + 10)
        r = client.post(URL, json=_body(screenshot=_shot(big)), headers=auth_headers)
        assert r.status_code == 413
        assert r.json()["error_code"] == "feedback_screenshot_too_large"
        assert _rows(test_tenant["id"]) == []

    def test_a_body_over_the_cap_is_refused_before_it_is_parsed(self, client, auth_headers, test_tenant, contact):
        r = client.post(
            URL, content=b"{" + b" " * (v.MAX_BODY_BYTES + 100) + b"}",
            headers={**auth_headers, "Content-Type": "application/json"},
        )
        assert r.status_code == 413
        assert r.json()["error_code"] == "feedback_too_large"
        assert _rows(test_tenant["id"]) == []

    def test_a_failed_insert_does_not_leave_an_orphan_file(self, client, auth_headers, test_tenant, contact, monkeypatch):
        """The file is written before the INSERT inside one transaction; if the
        INSERT fails, nothing may be left on disk that no row points at."""
        from contextlib import contextmanager
        from backend.feedback import service

        real_get_conn = service.get_conn

        class FailingCursor:
            def __init__(self, cur):
                self._cur = cur

            def __enter__(self):
                self._cur.__enter__()
                return self

            def __exit__(self, *exc):
                return self._cur.__exit__(*exc)

            def execute(self, sql, *a, **k):
                if "INSERT INTO feedback_reports" in sql:
                    raise RuntimeError("insert failed")
                return self._cur.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._cur, name)

        class ConnProxy:
            # psycopg2 connections do not allow attribute assignment, so wrap.
            def __init__(self, conn):
                self._conn = conn

            def cursor(self, *a, **k):
                return FailingCursor(self._conn.cursor(*a, **k))

            def __getattr__(self, name):
                return getattr(self._conn, name)

        @contextmanager
        def failing_conn():
            with real_get_conn() as conn:
                yield ConnProxy(conn)

        monkeypatch.setattr(service, "get_conn", failing_conn)
        try:
            r = client.post(URL, json=_body(screenshot=_shot()), headers=auth_headers)
            assert r.status_code >= 500
        except RuntimeError:
            pass  # the test client re-raises server errors
        folder = paths.feedback_dir(test_tenant["id"])
        assert not folder.exists() or not any(folder.iterdir())
        assert _rows(test_tenant["id"]) == []


class TestRateLimit:

    def test_the_eleventh_report_in_an_hour_is_refused(self, client, auth_headers, test_tenant, contact):
        for i in range(v.RATE_MAX_PER_HOUR):
            r = client.post(URL, json=_body(message=f"report number {i}"), headers=auth_headers)
            assert r.status_code == 201, (i, r.text)
        r = client.post(URL, json=_body(message="one too many"), headers=auth_headers)
        assert r.status_code == 429
        assert r.json()["error_code"] == "feedback_rate_limited"
        assert len(_rows(test_tenant["id"])) == v.RATE_MAX_PER_HOUR

    def test_the_limit_is_per_person_not_per_tenant(
        self, client, auth_headers, analyst_headers, test_tenant, contact,
    ):
        for i in range(v.RATE_MAX_PER_HOUR):
            client.post(URL, json=_body(message=f"admin report {i}"), headers=auth_headers)
        assert client.post(URL, json=_body(), headers=auth_headers).status_code == 429
        assert client.post(URL, json=_body(), headers=analyst_headers).status_code == 201

    def test_old_reports_leave_the_window(self, client, auth_headers, test_tenant, contact):
        from backend.db.connection import execute
        for i in range(v.RATE_MAX_PER_HOUR):
            client.post(URL, json=_body(message=f"report {i}"), headers=auth_headers)
        execute(
            "UPDATE feedback_reports SET created_at = NOW() - INTERVAL '2 hours' WHERE tenant_id = %s",
            (test_tenant["id"],),
        )
        assert client.post(URL, json=_body(), headers=auth_headers).status_code == 201


class TestNotification:

    def test_the_contact_is_e_mailed_with_the_screenshot_attached(
        self, client, auth_headers, registered_user, test_tenant, contact,
    ):
        r = client.post(
            URL, json=_body(screenshot=_shot(), consent_reply=True), headers=auth_headers,
        )
        data = r.json()["data"]
        assert data["notified"] is True
        mail = contact[0]
        assert mail["to"] == "ops@example.test"
        assert mail["attachment"]["content_bytes"] == PNG
        assert mail["attachment"]["filename"] == f"feedback-{data['id']}.png"
        assert registered_user["email"] in mail["html"]
        assert "HTTP 500" in mail["html"]
        assert query_one("SELECT notified FROM feedback_reports WHERE id = %s", (data["id"],))["notified"] is True

    def test_what_a_person_typed_is_escaped_in_the_mail(self, client, auth_headers, contact):
        client.post(URL, json=_body(message="<script>alert(1)</script> broke"), headers=auth_headers)
        assert "<script>" not in contact[0]["html"]
        assert "&lt;script&gt;" in contact[0]["html"]

    def test_no_contact_address_still_stores_and_says_it_did_not_notify(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        cfg = SimpleNamespace(contact_email="")
        monkeypatch.setattr("backend.service_config.resolver.effective", lambda tenant_id=None: cfg)
        r = client.post(URL, json=_body(), headers=auth_headers)
        assert r.status_code == 201
        assert r.json()["data"]["notified"] is False
        assert query_one(
            "SELECT notified FROM feedback_reports WHERE id = %s", (r.json()["data"]["id"],)
        )["notified"] is False

    def test_a_transport_failure_is_reported_not_swallowed(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        cfg = SimpleNamespace(contact_email="ops@example.test")
        monkeypatch.setattr("backend.service_config.resolver.effective", lambda tenant_id=None: cfg)

        def boom(*a, **k):
            raise RuntimeError("smtp down")

        monkeypatch.setattr("backend.notifications.email._send", boom)
        r = client.post(URL, json=_body(), headers=auth_headers)
        assert r.status_code == 201
        assert r.json()["data"]["notified"] is False
        assert len(_rows(test_tenant["id"])) == 1


class TestAudit:

    def test_the_audit_row_names_the_report_and_never_carries_the_text(
        self, client, auth_headers, registered_user, test_tenant, contact,
    ):
        r = client.post(URL, json=_body(screenshot=_shot()), headers=auth_headers)
        rid = r.json()["data"]["id"]
        rows = query(
            "SELECT user_id, resource, context FROM activity_logs "
            "WHERE tenant_id = %s AND action = 'audit.feedback.sent'", (test_tenant["id"],),
        )
        assert len(rows) == 1
        assert rows[0]["user_id"] == registered_user["user"]["id"]
        assert rows[0]["resource"] == rid
        assert rows[0]["context"]["after"]["has_screenshot"] is True
        assert "purchase screen" not in json.dumps(rows[0]["context"])


class TestExportAndErasure:

    def test_the_export_carries_this_tenants_reports_and_screenshots_only(
        self, client, auth_headers, test_tenant, contact,
    ):
        from backend.feedback import service
        rid = client.post(URL, json=_body(screenshot=_shot()), headers=auth_headers).json()["data"]["id"]
        # Another tenant's report, written straight through the service.
        from backend.tenants.service import create_tenant
        other = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        try:
            service.create_report(
                tenant_id=other["id"], user_id="usr_other", message="other tenant message",
                error_code=None, page_path="/x", user_agent=None, app_version=None,
                screenshot=v.decode_screenshot(_shot()), consent_reply=False, consent_news=False,
            )
            resp = client.get("/api/v1/tenant/export", headers=auth_headers)
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            reports = json.loads(zf.read("feedback_reports.json"))
            assert [r["id"] for r in reports] == [rid]
            assert f"feedback_screenshots/{rid}.png" in zf.namelist()
            assert len([n for n in zf.namelist() if n.startswith("feedback_screenshots/")]) == 1
            assert json.loads(zf.read("manifest.json"))["feedback_screenshots"] == 1
        finally:
            from backend.db.connection import execute
            execute("DELETE FROM feedback_reports WHERE tenant_id = %s", (other["id"],))
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))
            import shutil
            shutil.rmtree(paths.feedback_dir(other["id"]), ignore_errors=True)

    def test_erasing_the_tenant_removes_the_rows_and_the_screenshot_file(
        self, client, auth_headers, test_tenant, contact,
    ):
        rid = client.post(URL, json=_body(screenshot=_shot()), headers=auth_headers).json()["data"]["id"]
        stored = paths.feedback_dir(test_tenant["id"]) / f"{rid}.png"
        assert stored.is_file(), "the test would prove nothing without a file to erase"
        resp = client.request("DELETE", "/api/v1/tenant", headers=auth_headers, json={"confirm": "DELETE"})
        assert resp.status_code == 200, resp.text
        assert _rows(test_tenant["id"]) == []
        assert not stored.exists()
        assert not paths.feedback_dir(test_tenant["id"]).exists()

    def test_the_screenshot_path_cannot_escape_the_tenant_folder(self, test_tenant):
        from backend.feedback.service import screenshot_abspath
        with pytest.raises(ValueError):
            screenshot_abspath(test_tenant["id"], "../../../etc/passwd")
        with pytest.raises(ValueError):
            screenshot_abspath(test_tenant["id"], "sub/dir.png")
