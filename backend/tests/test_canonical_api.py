"""
backend/tests/test_canonical_api.py

Offline tests for Task 10: canonical column mapping wired into the API.
- GET  /sessions/{id}/inspect   → response includes canonical_suggestions
- POST /sessions/{id}/configure/columns
    - accepts CanonicalColumnsRequest (body has canonical_mapping key)
    - calls validate_required before saving; returns 422 on failure
    - still accepts old ColumnsConfigRequest body (backward-compat)

Marked @pytest.mark.offline — no database connection required.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Stub forecasting_core.data.canonical so that CanonicalColumnsRequest
# .validate_required() can import REQUIRED_FIELDS even when the full
# forecasting_core package is not installed.
# ──────────────────────────────────────────────────────────────────────────────

_CANON_FQN = "forecasting_core.data.canonical"
if _CANON_FQN not in sys.modules:
    _canon_mod = types.ModuleType(_CANON_FQN)
    _canon_mod.REQUIRED_FIELDS = frozenset({"sku", "date", "demand"})
    _canon_mod.FIELD_DEFAULTS = {
        "sku": None, "date": None, "demand": None,
        "store": "Tienda única", "region": "Sin región",
        "inventory": 0, "lead_time": 7,
        "price": None, "cost": None,
        "regular_price": None, "promo_price": None,
        "promo": False, "promo_type": "Sin promoción", "discount": 0.0,
    }
    sys.modules[_CANON_FQN] = _canon_mod


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

TENANT_ID  = "ten_can_001"
ADMIN_ID   = "usr_admin_can"
SESSION_ID = "sess_can_001"
DATASET_ID = "ds_can_001"

MOCK_SESSION = {
    "id": SESSION_ID, "tenant_id": TENANT_ID, "name": "Canonical Test",
    "status": "INSPECTED", "pipeline_step": "columns",
    "dataset_id": DATASET_ID, "created_by": ADMIN_ID,
    "description": None, "tags": [], "version": 1,
    "created_at": "2024-01-01T00:00:00", "updated_at": "2024-03-01T00:00:00",
    "last_job_id": None,
}

# Three columns that satisfy the required canonical fields
_TEST_COLUMNS = [
    {"name": "sku",    "dtype": "object",  "nulls": 0, "unique": 5},
    {"name": "date",   "dtype": "object",  "nulls": 0, "unique": 60},
    {"name": "demand", "dtype": "float64", "nulls": 0, "unique": 195},
]

# Full inspection payload already including canonical_suggestions (cached scenario)
MOCK_INSPECTION_WITH_CANONICAL = {
    "profile": {"n_rows": 200, "columns": _TEST_COLUMNS, "recommended": {}},
    "column_options": {
        "date_candidates":   ["date"],
        "target_candidates": ["demand"],
        "group_candidates":  ["sku"],
    },
    "canonical_suggestions": {
        "sku":    {"top": "sku",    "candidates": ["sku"],    "confidence": 1.0, "can_use_default": False},
        "date":   {"top": "date",   "candidates": ["date"],   "confidence": 1.0, "can_use_default": False},
        "demand": {"top": "demand", "candidates": ["demand"], "confidence": 0.9, "can_use_default": False},
    },
    "config_schema": {},
    "inspected_at": "2024-01-01T00:00:00",
}

# Minimal inspection for configure/columns tests (just needs a profile.columns list)
MOCK_INSPECTION_FOR_CFG = {
    "profile": {"columns": _TEST_COLUMNS},
}


# ──────────────────────────────────────────────────────────────────────────────
# App / client fixtures (module-scoped, no DB)
# ──────────────────────────────────────────────────────────────────────────────

_STARTUP_PATCHES = [
    mock.patch("backend.db.connection.init_pool"),
    mock.patch("backend.main._recover_running_jobs"),
    mock.patch("backend.workers.worker.start"),
    mock.patch("backend.auth.blocklist.is_revoked",    return_value=False),
    mock.patch("backend.auth.blocklist.ensure_table"),
    mock.patch("backend.auth.blocklist.revoke"),
    mock.patch("backend.notifications.email._send"),
    mock.patch("backend.training.queue.peek",          return_value=[]),
    mock.patch("backend.db.connection.execute"),
    mock.patch("backend.db.connection.query",          return_value=[]),
    mock.patch("backend.db.connection.query_one",      return_value=None),
]


@pytest.fixture(scope="module")
def can_app():
    for p in _STARTUP_PATCHES:
        p.start()
    try:
        from backend.main import app as _app
        yield _app
    finally:
        for p in _STARTUP_PATCHES:
            try:
                p.stop()
            except RuntimeError:
                pass


@pytest.fixture(scope="module")
def oc(can_app):
    from fastapi.testclient import TestClient
    with TestClient(can_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def tokens():
    from backend.auth.jwt_handler import create_access_token
    return {
        "admin":  {"Authorization": f"Bearer {create_access_token(ADMIN_ID, TENANT_ID, 'admin')}"},
        "viewer": {"Authorization": f"Bearer {create_access_token('usr_viewer_can', TENANT_ID, 'viewer')}"},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

pytestmark = pytest.mark.offline


class TestCanonicalAPI:

    # ── inspect ──────────────────────────────────────────────────────────────

    def test_inspect_response_includes_canonical_suggestions(self, oc, tokens):
        """
        When the inspection is already cached (with canonical_suggestions), the
        endpoint returns the field as part of the response payload.
        """
        session_with_ds = {**MOCK_SESSION, "dataset_id": DATASET_ID}
        with mock.patch("backend.sessions.service.get_session",  return_value=session_with_ds), \
             mock.patch("backend.db.session_store.get_field",    return_value=MOCK_INSPECTION_WITH_CANONICAL):
            r = oc.get(f"/api/v1/sessions/{SESSION_ID}/inspect", headers=tokens["admin"])

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert "canonical_suggestions" in data, (
            "inspect response must include 'canonical_suggestions'"
        )
        cs = data["canonical_suggestions"]
        assert "sku"    in cs
        assert "date"   in cs
        assert "demand" in cs
        assert cs["sku"]["top"]    == "sku"
        assert cs["date"]["top"]   == "date"
        assert cs["demand"]["top"] == "demand"

    # ── configure/columns — canonical schema ─────────────────────────────────

    def test_configure_columns_accepts_canonical_mapping(self, oc, tokens):
        """
        POST configure/columns with a canonical_mapping body returns 200 and
        saves config with schema_version = 'canonical_v1'.
        """
        with mock.patch("backend.sessions.service.get_session",    return_value=MOCK_SESSION), \
             mock.patch("backend.db.session_store.get_field",      return_value=MOCK_INSPECTION_FOR_CFG), \
             mock.patch("backend.db.session_store.set_field"), \
             mock.patch("backend.sessions.service.transition",     return_value=MOCK_SESSION):
            r = oc.post(
                f"/api/v1/sessions/{SESSION_ID}/configure/columns",
                json={"canonical_mapping": {
                    "sku":    "sku",
                    "date":   "date",
                    "demand": "demand",
                }},
                headers=tokens["admin"],
            )

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert "canonical_mapping" in data, "saved config must echo canonical_mapping"
        assert data["schema_version"] == "canonical_v1"
        assert data["canonical_mapping"]["sku"]    == "sku"
        assert data["canonical_mapping"]["date"]   == "date"
        assert data["canonical_mapping"]["demand"] == "demand"
        assert "configured_at" in data
        assert "configured_by" in data

    def test_configure_columns_canonical_validates_required_fields(self, oc, tokens):
        """
        POST configure/columns with empty canonical_mapping (required fields sku,
        date, demand all unmapped) returns 422 after calling validate_required.
        """
        with mock.patch("backend.sessions.service.get_session",  return_value=MOCK_SESSION), \
             mock.patch("backend.db.session_store.get_field",    return_value=MOCK_INSPECTION_FOR_CFG):
            r = oc.post(
                f"/api/v1/sessions/{SESSION_ID}/configure/columns",
                json={"canonical_mapping": {}},
                headers=tokens["admin"],
            )

        assert r.status_code == 422, (
            f"Expected 422 for empty canonical_mapping (required fields unmapped), "
            f"got {r.status_code}: {r.text}"
        )

    def test_configure_columns_canonical_rejects_nonexistent_column(self, oc, tokens):
        """
        POST configure/columns with a canonical_mapping that references a column
        not present in the file returns 422.
        """
        with mock.patch("backend.sessions.service.get_session",  return_value=MOCK_SESSION), \
             mock.patch("backend.db.session_store.get_field",    return_value=MOCK_INSPECTION_FOR_CFG):
            r = oc.post(
                f"/api/v1/sessions/{SESSION_ID}/configure/columns",
                json={"canonical_mapping": {
                    "sku":    "nonexistent_column",
                    "date":   "date",
                    "demand": "demand",
                }},
                headers=tokens["admin"],
            )

        assert r.status_code == 422, (
            f"Expected 422 when canonical_mapping references a nonexistent column, "
            f"got {r.status_code}: {r.text}"
        )

    def test_configure_columns_canonical_saves_defaults_override(self, oc, tokens):
        """
        POST configure/columns with canonical_mapping and defaults_override saves both
        fields in the config response.
        """
        with mock.patch("backend.sessions.service.get_session",    return_value=MOCK_SESSION), \
             mock.patch("backend.db.session_store.get_field",      return_value=MOCK_INSPECTION_FOR_CFG), \
             mock.patch("backend.db.session_store.set_field"), \
             mock.patch("backend.sessions.service.transition",     return_value=MOCK_SESSION):
            r = oc.post(
                f"/api/v1/sessions/{SESSION_ID}/configure/columns",
                json={
                    "canonical_mapping": {
                        "sku":    "sku",
                        "date":   "date",
                        "demand": "demand",
                    },
                    "defaults_override": {"lead_time": 14},
                },
                headers=tokens["admin"],
            )

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["defaults_override"] == {"lead_time": 14}

    # ── configure/columns — old style (backward-compat) ──────────────────────

    def test_configure_columns_old_style_still_works(self, oc, tokens):
        """
        POST configure/columns with the classic ColumnsConfigRequest body
        (no canonical_mapping key) continues to return 200 with the same shape.
        """
        with mock.patch("backend.sessions.service.get_session",    return_value=MOCK_SESSION), \
             mock.patch("backend.db.session_store.get_field",      return_value=None), \
             mock.patch("backend.db.session_store.set_field"), \
             mock.patch("backend.sessions.service.transition",     return_value=MOCK_SESSION):
            r = oc.post(
                f"/api/v1/sessions/{SESSION_ID}/configure/columns",
                json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
                headers=tokens["admin"],
            )

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["date_column"]   == "date"
        assert data["target_column"] == "sales"
        assert data["sku_column"]    == "sku"
        assert "schema_version" not in data   # old-style config has no schema_version

    def test_configure_columns_old_style_missing_target_returns_422(self, oc, tokens):
        """
        POST configure/columns without target_column (old style) returns 422
        from the early Pydantic validation, before even hitting the session lookup.
        """
        r = oc.post(
            f"/api/v1/sessions/{SESSION_ID}/configure/columns",
            json={"date_column": "date"},   # missing target_column
            headers=tokens["admin"],
        )
        assert r.status_code == 422

    def test_configure_columns_viewer_cannot_use_canonical_path(self, oc, tokens):
        """RBAC: viewer role is rejected on POST configure/columns (canonical body)."""
        r = oc.post(
            f"/api/v1/sessions/{SESSION_ID}/configure/columns",
            json={"canonical_mapping": {"sku": "sku", "date": "date", "demand": "demand"}},
            headers=tokens["viewer"],
        )
        assert r.status_code == 403
