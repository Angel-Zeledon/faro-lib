"""Guided upload through the API: the guide reads, asks, and applies recorded fixes.

What is asserted is state, read straight from the database and the disk:

* the ORIGINAL file is byte-for-byte untouched; the fixes land in a NEW dataset
  whose ``parent_id`` is the original;
* the transformation (fix list, answers, row counts) is stored with the session
  and reaches the lineage manifest;
* a half-answered conversation cannot change the data (409, nothing written);
* only analysts may apply, only the owning tenant may see the session at all;
* a clean file produces no questions and no fixes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import query, query_one

_CASES_PATH = (pathlib.Path(__file__).resolve().parents[2] / "ForecastingCore" / "tests"
               / "guidance_cases.py")
_spec = importlib.util.spec_from_file_location("guidance_cases", _CASES_PATH)
cases = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("guidance_cases", cases)
_spec.loader.exec_module(cases)
CASE = {c.id: c for c in cases.CASES}


# ── helpers ──────────────────────────────────────────────────────────────────

def _upload(client, headers, path: pathlib.Path) -> dict:
    mime = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if path.suffix == ".xlsx" else "text/csv")
    r = client.post("/api/v1/datasets",
                    files={"file": (path.name, path.read_bytes(), mime)}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _session(client, headers, dataset_id: str) -> str:
    s = client.post("/api/v1/sessions", json={"name": f"guided-{uuid4().hex[:6]}"},
                    headers=headers)
    sid = s.json()["data"]["id"]
    r = client.post(f"/api/v1/sessions/{sid}/dataset", json={"dataset_id": dataset_id},
                    headers=headers)
    assert r.status_code == 200, r.text
    return sid


def _setup(client, headers, tmp_path, case_id: str):
    path = CASE[case_id].build(tmp_path)
    ds = _upload(client, headers, path)
    return _session(client, headers, ds["id"]), ds, path


def _sha(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _guided(client, headers, sid, decisions=None, mapping=None):
    params = {}
    if decisions is not None:
        params["decisions"] = json.dumps(decisions)
    if mapping is not None:
        params["mapping"] = json.dumps(mapping)
    return client.get(f"/api/v1/sessions/{sid}/guided-reading", params=params, headers=headers)


def _apply(client, headers, sid, decisions=None, **extra):
    return client.post(f"/api/v1/sessions/{sid}/configure/guided-reading",
                       json={"decisions": decisions or {}, **extra}, headers=headers)


def _session_row(tenant_id, sid):
    return query_one("SELECT * FROM sessions WHERE id=%s AND tenant_id=%s", (sid, tenant_id))


def _datasets(tenant_id):
    return query("SELECT id, parent_id, file_path, row_count, tenant_id FROM datasets "
                 "WHERE tenant_id=%s ORDER BY uploaded_at", (tenant_id,))


# ── inspect carries the guide's reading ──────────────────────────────────────

class TestInspectCarriesTheGuidance:
    def test_a_messy_file_arrives_with_its_fixes_and_stored_in_the_inspection(
            self, client, auth_headers, test_tenant, tmp_path):
        sid, _, _ = _setup(client, auth_headers, tmp_path, "title_rows_merged_xlsx")
        r = client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)
        assert r.status_code == 200, r.text
        g = r.json()["data"]["guidance"]
        assert g["verdict"] == "ready"
        assert [f["code"] for f in g["fixes"]] == ["header_row"]
        assert g["mapping"] == {"sku": "sku", "date": "fecha", "demand": "cantidad"}
        # The state is in the database, not just in the response.
        stored = session_store.get_field(test_tenant["id"], sid, "inspection")
        assert stored["guidance"]["fixes"][0]["params"] == {"row": 3}
        assert stored["guidance_error"] is None

    def test_a_clean_file_has_no_questions_and_no_fixes(
            self, client, auth_headers, tmp_path):
        sid, _, _ = _setup(client, auth_headers, tmp_path, "clean_csv")
        g = client.get(f"/api/v1/sessions/{sid}/inspect",
                       headers=auth_headers).json()["data"]["guidance"]
        assert g["verdict"] == "ready" and g["questions"] == [] and g["fixes"] == []
        assert g["needs_apply"] is False and g["checks_done"] == g["checks_total"] == 6

    def test_an_ambiguous_file_stops_at_one_question_with_both_readings(
            self, client, auth_headers, tmp_path):
        sid, _, _ = _setup(client, auth_headers, tmp_path, "date_ambiguous_slash_monthly")
        g = client.get(f"/api/v1/sessions/{sid}/inspect",
                       headers=auth_headers).json()["data"]["guidance"]
        assert g["verdict"] == "ask"
        q = g["next_question"]
        assert q["code"] == "date_order_ambiguous"
        assert {o["id"] for o in q["options"]} == {"day_first", "month_first"}
        assert q["examples"][0]["day_first"] != q["examples"][0]["month_first"]


# ── preview ──────────────────────────────────────────────────────────────────

class TestPreview:
    def test_answers_move_the_conversation_forward(
            self, client, auth_headers, tmp_path):
        sid, _, _ = _setup(client, auth_headers, tmp_path, "date_ambiguous_slash_monthly")
        first = _guided(client, auth_headers, sid).json()["data"]["report"]
        assert first["next_question"]["code"] == "date_order_ambiguous"
        answer = next(o for o in first["next_question"]["options"] if o["id"] == "day_first")
        second = _guided(client, auth_headers, sid, answer["decision"]).json()["data"]["report"]
        assert second["verdict"] == "ready" and second["next_question"] is None
        assert [f["code"] for f in second["fixes"]] == ["read_dates"]
        assert second["summary"]["granularity"] == "monthly"

    def test_a_viewer_may_read_it(self, client, viewer_headers, auth_headers, test_tenant,
                                  tmp_path):
        sid, _, _ = _setup(client, auth_headers, tmp_path, "clean_csv")
        r = _guided(client, viewer_headers, sid)
        assert r.status_code == 200 and r.json()["data"]["report"]["verdict"] == "ready"

    def test_preview_writes_nothing(self, client, auth_headers, test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "title_rows_merged_xlsx")
        before = (_datasets(test_tenant["id"]), _session_row(test_tenant["id"], sid)["dataset_id"])
        assert _guided(client, auth_headers, sid).status_code == 200
        after = (_datasets(test_tenant["id"]), _session_row(test_tenant["id"], sid)["dataset_id"])
        assert before == after

    def test_malformed_or_oversized_answers_are_refused_not_ignored(
            self, client, auth_headers, tmp_path):
        sid, _, _ = _setup(client, auth_headers, tmp_path, "clean_csv")
        bad = client.get(f"/api/v1/sessions/{sid}/guided-reading",
                         params={"decisions": "{not json"}, headers=auth_headers)
        assert bad.status_code == 422 and bad.json()["error_code"] == "guided_reading_invalid"
        big = client.get(f"/api/v1/sessions/{sid}/guided-reading",
                         params={"decisions": json.dumps({"x": "a" * 9000})},
                         headers=auth_headers)
        assert big.status_code == 422 and big.json()["error_code"] == "guided_reading_invalid"
        listy = client.get(f"/api/v1/sessions/{sid}/guided-reading",
                           params={"decisions": "[1]"}, headers=auth_headers)
        assert listy.status_code == 422

    def test_a_mapping_the_user_chose_is_judged_not_replaced(
            self, client, auth_headers, tmp_path):
        sid, _, _ = _setup(client, auth_headers, tmp_path, "clean_csv")
        r = _guided(client, auth_headers, sid,
                    mapping={"sku": "sku", "date": "cantidad", "demand": "fecha"})
        rep = r.json()["data"]["report"]
        assert rep["mapping"]["date"] == "cantidad" and rep["verdict"] == "unusable"
        assert {c["slot"]: c["status"] for c in rep["columns"]}["date"] == "bad"


# ── apply ────────────────────────────────────────────────────────────────────

class TestApply:
    def test_viewer_is_denied_and_nothing_changes(self, client, viewer_headers, auth_headers,
                                                  test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "title_rows_merged_xlsx")
        before = _datasets(test_tenant["id"])
        r = _apply(client, viewer_headers, sid)
        assert r.status_code == 403
        assert _datasets(test_tenant["id"]) == before
        assert _session_row(test_tenant["id"], sid)["dataset_id"] == ds["id"]
        assert not (session_store.get_field(test_tenant["id"], sid, "dataset_ref") or {}).get(
            "guided_reading")

    def test_analyst_applies_fixes_into_a_new_dataset_and_the_original_is_untouched(
            self, client, analyst_headers, analyst_user, auth_headers, test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "title_rows_merged_xlsx")
        original_hash = _sha(ds["file_path"])

        r = _apply(client, analyst_headers, sid)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        new_id = data["dataset_id"]
        assert new_id != ds["id"]

        rows = {d["id"]: d for d in _datasets(test_tenant["id"])}
        assert rows[new_id]["parent_id"] == ds["id"]
        assert rows[new_id]["tenant_id"] == test_tenant["id"]
        assert rows[new_id]["row_count"] == 104
        assert _sha(rows[ds["id"]]["file_path"]) == original_hash, "the original was modified"
        assert _session_row(test_tenant["id"], sid)["dataset_id"] == new_id

        ref = session_store.get_field(test_tenant["id"], sid, "dataset_ref")
        record = ref["guided_reading"]
        assert ref["dataset_id"] == new_id
        assert record["source_dataset_id"] == ds["id"]
        assert record["derived_dataset_id"] == new_id
        assert record["fixes"] == [{"code": "header_row", "params": {"row": 3}}]
        assert record["rows_in"] == record["rows_out"] == 104
        assert record["applied_by"] == analyst_user["user"]["id"]
        assert session_store.get_field(test_tenant["id"], sid, "inspection") is None

    def test_the_cleaned_copy_has_the_same_canonical_table_as_the_clean_equivalent(
            self, client, auth_headers, test_tenant, tmp_path):
        # Read it the way training does: through the engine's loader.
        from forecasting_core.data.loader import DataLoader
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "everything_at_once")
        assert _apply(client, auth_headers, sid).status_code == 200
        new = next(d for d in _datasets(test_tenant["id"]) if d["parent_id"] == ds["id"])
        frame = DataLoader().load(new["file_path"])
        got = sorted((str(r.sku), str(r.fecha)[:10], float(r.cantidad))
                     for r in frame.itertuples(index=False))
        want = sorted((s, d, float(v)) for s, d, v in CASE["everything_at_once"].clean)
        assert got == want
        assert all(len(s) == 5 for s, _, _ in got), "leading zeros were lost"

    def test_an_open_question_blocks_the_apply_and_writes_nothing(
            self, client, auth_headers, test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "date_ambiguous_slash_monthly")
        before = _datasets(test_tenant["id"])
        r = _apply(client, auth_headers, sid)
        assert r.status_code == 409 and r.json()["error_code"] == "guided_reading_not_ready"
        assert "date_order_ambiguous" in r.json()["error_params"]["pending"]
        assert _datasets(test_tenant["id"]) == before
        assert _session_row(test_tenant["id"], sid)["dataset_id"] == ds["id"]

    def test_an_unusable_file_is_refused(self, client, auth_headers, test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "stock_snapshot_file")
        r = _apply(client, auth_headers, sid)
        assert r.status_code == 409 and r.json()["error_params"]["verdict"] == "unusable"
        assert _session_row(test_tenant["id"], sid)["dataset_id"] == ds["id"]

    def test_answered_then_applied_reads_the_date_the_user_chose(
            self, client, auth_headers, test_tenant, tmp_path):
        from backend.dataframes.io import read_rows
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "date_ambiguous_slash_monthly")
        r = _apply(client, auth_headers, sid, {"date_order": {"fecha": "day_first"}})
        assert r.status_code == 200, r.text
        new = next(d for d in _datasets(test_tenant["id"]) if d["parent_id"] == ds["id"])
        dates = {str(r_["fecha"])[:10] for r_ in read_rows(new["file_path"])}
        assert "2025-02-01" in dates and "2025-01-02" not in dates
        record = session_store.get_field(test_tenant["id"], sid, "dataset_ref")["guided_reading"]
        assert record["decisions"] == {"date_order": {"fecha": "day_first"}}

    def test_a_clean_file_creates_no_copy(self, client, auth_headers, test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "clean_csv")
        r = _apply(client, auth_headers, sid)
        assert r.status_code == 200 and r.json()["data"]["applied"] is None
        assert len(_datasets(test_tenant["id"])) == 1
        assert _session_row(test_tenant["id"], sid)["dataset_id"] == ds["id"]

    def test_apply_twice_replaces_the_copy_not_stacks_it(
            self, client, auth_headers, test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "totals_row_and_note_at_bottom")
        first = _apply(client, auth_headers, sid).json()["data"]
        second = _apply(client, auth_headers, sid).json()["data"]
        # Both copies descend from the ORIGINAL, never from each other.
        parents = {d["parent_id"] for d in _datasets(test_tenant["id"]) if d["parent_id"]}
        assert parents == {ds["id"]}
        assert second["applied"]["source_dataset_id"] == ds["id"]
        assert first["dataset_id"] != second["dataset_id"]

    def test_the_cleaned_session_goes_through_inspect_gate_and_mapping(
            self, client, auth_headers, test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "totals_row_and_note_at_bottom")
        assert _apply(client, auth_headers, sid).status_code == 200
        insp = client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers).json()["data"]
        assert insp["guidance"]["fixes"] == [] and insp["guidance"]["verdict"] == "ready"
        assert insp["guided_reading"]["fixes"][0]["code"] == "ignore_totals_row"
        assert insp["profile"]["stats"]["n_rows"] == 104
        m = {"sku": "sku", "date": "fecha", "demand": "cantidad"}
        r = client.post(f"/api/v1/sessions/{sid}/configure/columns",
                        json={"canonical_mapping": m, "defaults_override": {}},
                        headers=auth_headers)
        assert r.status_code == 200, r.text
        gate = client.get(f"/api/v1/sessions/{sid}/data-gate", headers=auth_headers)
        assert gate.status_code == 200
        assert gate.json()["data"]["evaluated_columns"]["target"] == "cantidad"

    def test_the_lineage_manifest_names_the_transformation(
            self, client, auth_headers, test_tenant, tmp_path):
        from backend.lineage.manifest import _guided_reading
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "totals_row_and_note_at_bottom")
        assert _guided_reading(test_tenant["id"], sid) is None
        _apply(client, auth_headers, sid)
        rec = _guided_reading(test_tenant["id"], sid)
        assert rec["source_dataset_id"] == ds["id"]
        assert rec["fixes"] == [{"code": "ignore_totals_row", "params": {}}]


class TestRevert:
    def test_revert_puts_the_original_back(self, client, analyst_headers, auth_headers,
                                           test_tenant, tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "totals_row_and_note_at_bottom")
        _apply(client, auth_headers, sid)
        assert _session_row(test_tenant["id"], sid)["dataset_id"] != ds["id"]
        r = _apply(client, analyst_headers, sid, revert=True)
        assert r.status_code == 200, r.text
        assert _session_row(test_tenant["id"], sid)["dataset_id"] == ds["id"]
        assert session_store.get_field(test_tenant["id"], sid, "inspection") is None
        assert session_store.get_field(test_tenant["id"], sid, "columns_cfg") is None

    def test_viewer_cannot_revert(self, client, viewer_headers, auth_headers, test_tenant,
                                  tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "totals_row_and_note_at_bottom")
        _apply(client, auth_headers, sid)
        current = _session_row(test_tenant["id"], sid)["dataset_id"]
        assert _apply(client, viewer_headers, sid, revert=True).status_code == 403
        assert _session_row(test_tenant["id"], sid)["dataset_id"] == current


class TestTenantIsolation:
    def _other_admin(self, client):
        from backend.tenants.service import create_tenant
        from backend.users import service as user_svc
        from backend.db.connection import execute
        tenant = create_tenant(f"pytest-other-{uuid4().hex[:8]}")
        email, pw = f"other-{uuid4().hex[:8]}@example.com", "TestPass123!"
        user = user_svc.create_user(tenant_id=tenant["id"], email=email, password=pw,
                                    role="admin", full_name="Other")
        user_svc.mark_verified(tenant["id"], user["id"])
        token = client.post("/api/v1/auth/login",
                            json={"email": email, "password": pw}).json()["data"]["access_token"]
        return tenant, {"Authorization": f"Bearer {token}"}, lambda: execute(
            "DELETE FROM tenants WHERE id=%s", (tenant["id"],))

    def test_another_tenant_can_neither_read_nor_apply(self, client, auth_headers, test_tenant,
                                                       tmp_path):
        sid, ds, _ = _setup(client, auth_headers, tmp_path, "title_rows_merged_xlsx")
        other, headers, cleanup = self._other_admin(client)
        try:
            before = _datasets(test_tenant["id"])
            assert _guided(client, headers, sid).status_code == 404
            r = _apply(client, headers, sid)
            assert r.status_code == 404 and r.json()["error_code"] == "session_not_found"
            assert _datasets(test_tenant["id"]) == before
            assert _datasets(other["id"]) == []
            assert _session_row(test_tenant["id"], sid)["dataset_id"] == ds["id"]
        finally:
            cleanup()
