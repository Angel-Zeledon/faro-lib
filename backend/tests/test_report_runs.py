"""
Report generation must leave a durable, retrievable status.

Report building runs as a FastAPI background task. Before this, a failure —
including the early return when the session has no stored training result —
wrote nothing anywhere, and the later download answered
404 "No pdf report found. Generate one first via POST /reports/generate":
it told the user to redo exactly what had just failed.

Each attempt now writes a `report_runs` row (running → completed/failed) and
the download reports what actually happened, as a structured English
error_code + params (CLAUDE.md: no Spanish literals in backend logic).
"""

import shutil

import pytest

from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.storage import paths


def _force_completed(session_id: str, tenant_id: str) -> None:
    """Reports require a COMPLETED session; training itself is out of scope
    here, so the terminal state is set directly."""
    execute(
        "UPDATE sessions SET status = 'COMPLETED', pipeline_step = 'completed' "
        "WHERE id = %s AND tenant_id = %s",
        (session_id, tenant_id),
    )


def _runs(session_id: str) -> list[dict]:
    return [dict(r) for r in query(
        "SELECT status, error_code, error_detail, report_type, formats, finished_at "
        "FROM report_runs WHERE session_id = %s ORDER BY created_at",
        (session_id,),
    )]


@pytest.fixture
def resultless_session(test_session, registered_user):
    """COMPLETED session with NO training result — the exact case that used to
    fail silently in the background task."""
    tenant_id = registered_user["tenant"]["id"]
    _force_completed(test_session["id"], tenant_id)
    yield {"id": test_session["id"], "tenant_id": tenant_id}
    shutil.rmtree(paths.reports_artifact_dir(tenant_id, test_session["id"]), ignore_errors=True)


@pytest.fixture
def result_session(completed_session, registered_user):
    """COMPLETED session WITH a stored training result — reports can be built."""
    tenant_id = registered_user["tenant"]["id"]
    sid = completed_session["id"]
    _force_completed(sid, tenant_id)
    assert session_store.get_training_result(tenant_id, sid), "fixture must seed a training result"
    yield {"id": sid, "tenant_id": tenant_id}
    shutil.rmtree(paths.reports_artifact_dir(tenant_id, sid), ignore_errors=True)


class TestGeneratePermissions:
    def test_viewer_denied_and_no_run_row_created(
        self, client, viewer_headers, resultless_session,
    ):
        resp = client.post(
            f"/api/v1/sessions/{resultless_session['id']}/reports/generate",
            json={"type": "operational", "formats": ["pdf"]},
            headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert _runs(resultless_session["id"]) == []

    def test_analyst_can_generate_and_a_run_row_is_written(
        self, client, analyst_headers, result_session,
    ):
        resp = client.post(
            f"/api/v1/sessions/{result_session['id']}/reports/generate",
            json={"type": "operational", "formats": ["excel"]},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["run_id"]

        rows = _runs(result_session["id"])
        assert len(rows) == 1
        assert rows[0]["report_type"] == "operational"
        assert rows[0]["formats"] == ["excel"]

    def test_unknown_format_rejected_before_any_run_is_opened(
        self, client, analyst_headers, result_session,
    ):
        resp = client.post(
            f"/api/v1/sessions/{result_session['id']}/reports/generate",
            json={"type": "operational", "formats": ["docx"]},
            headers=analyst_headers,
        )
        assert resp.status_code == 400, resp.text
        assert _runs(result_session["id"]) == []


class TestFailedGenerationIsRetrievable:
    def test_missing_training_result_is_recorded_as_failed(
        self, client, analyst_headers, resultless_session,
    ):
        sid = resultless_session["id"]
        resp = client.post(
            f"/api/v1/sessions/{sid}/reports/generate",
            json={"type": "operational", "formats": ["pdf"]},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text

        # TestClient runs background tasks before returning, so the outcome is
        # already durable here.
        rows = _runs(sid)
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["error_code"] == "no_training_result"
        assert rows[0]["error_detail"]
        assert rows[0]["finished_at"] is not None

    def test_download_after_failure_explains_the_failure(
        self, client, analyst_headers, resultless_session,
    ):
        sid = resultless_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/reports/generate",
            json={"type": "operational", "formats": ["pdf"]},
            headers=analyst_headers,
        )

        resp = client.get(f"/api/v1/sessions/{sid}/reports/pdf", headers=analyst_headers)
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error_code"] == "report_generation_failed"
        assert body["error_params"]["reason"] == "no_training_result"
        assert body["error_params"]["format"] == "pdf"
        # English fallback only — the Spanish wording is the frontend's job.
        assert "Generate one first" not in body["detail"]

    def test_status_endpoint_returns_the_failed_run(
        self, client, analyst_headers, resultless_session,
    ):
        sid = resultless_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/reports/generate",
            json={"type": "technical", "formats": ["pdf", "excel"]},
            headers=analyst_headers,
        )
        resp = client.get(f"/api/v1/sessions/{sid}/reports/status", headers=analyst_headers)
        assert resp.status_code == 200, resp.text
        runs = resp.json()["data"]["runs"]
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"
        assert runs[0]["error_code"] == "no_training_result"
        assert sorted(runs[0]["formats"]) == ["excel", "pdf"]

    def test_never_generated_still_returns_the_plain_404(
        self, client, analyst_headers, resultless_session,
    ):
        """No attempt at all is its own outcome, distinct from a failure.

        It carries its own code rather than the English prose it used to: all
        four download outcomes are structured, so the frontend localizes them
        the same way instead of rendering English for this one.
        """
        resp = client.get(
            f"/api/v1/sessions/{resultless_session['id']}/reports/pdf", headers=analyst_headers,
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["error_code"] == "report_not_generated"
        assert body["error_params"]["format"] == "pdf"


class TestSuccessfulGeneration:
    def test_run_is_completed_and_the_file_downloads(
        self, client, analyst_headers, result_session,
    ):
        sid = result_session["id"]
        gen = client.post(
            f"/api/v1/sessions/{sid}/reports/generate",
            json={"type": "operational", "formats": ["excel"]},
            headers=analyst_headers,
        )
        assert gen.status_code == 200, gen.text

        row = query_one(
            "SELECT status, error_code FROM report_runs WHERE id = %s",
            (gen.json()["data"]["run_id"],),
        )
        assert row["status"] == "completed"
        assert row["error_code"] is None

        dl = client.get(f"/api/v1/sessions/{sid}/reports/excel", headers=analyst_headers)
        assert dl.status_code == 200, dl.text
        assert dl.content

    def test_format_not_requested_is_not_reported_as_failed(
        self, client, analyst_headers, result_session,
    ):
        """An excel-only run says nothing about pdf: the pdf download must fall
        back to "never generated", not claim a failure that never happened."""
        sid = result_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/reports/generate",
            json={"type": "operational", "formats": ["excel"]},
            headers=analyst_headers,
        )
        resp = client.get(f"/api/v1/sessions/{sid}/reports/pdf", headers=analyst_headers)
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "report_not_generated"


class TestReportRunIsolation:
    def test_other_tenant_cannot_read_report_status(
        self, client, analyst_headers, resultless_session, make_tenant_user_headers,
    ):
        sid = resultless_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/reports/generate",
            json={"type": "operational", "formats": ["pdf"]},
            headers=analyst_headers,
        )
        other = make_tenant_user_headers(role="analyst")
        resp = client.get(f"/api/v1/sessions/{sid}/reports/status", headers=other)
        assert resp.status_code == 404

    def test_run_rows_die_with_the_tenant(self, client, analyst_headers, resultless_session):
        """report_runs.tenant_id CASCADEs, so a deleted tenant leaves no rows."""
        sid = resultless_session["id"]
        client.post(
            f"/api/v1/sessions/{sid}/reports/generate",
            json={"type": "operational", "formats": ["pdf"]},
            headers=analyst_headers,
        )
        assert _runs(sid), "precondition: a run row exists"
        execute("DELETE FROM tenants WHERE id = %s", (resultless_session["tenant_id"],))
        assert _runs(sid) == []


def test_report_run_ids_are_unique_per_attempt(client, analyst_headers, resultless_session):
    sid = resultless_session["id"]
    ids = set()
    for _ in range(2):
        r = client.post(
            f"/api/v1/sessions/{sid}/reports/generate",
            json={"type": "operational", "formats": ["pdf"]},
            headers=analyst_headers,
        )
        ids.add(r.json()["data"]["run_id"])
    assert len(ids) == 2, "each attempt must get its own run row"
    assert len(_runs(sid)) == 2
