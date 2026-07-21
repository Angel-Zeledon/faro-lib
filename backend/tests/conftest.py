"""
Shared fixtures for all backend tests.

Isolation strategy:
  - session-scoped `client` — one FastAPI TestClient for the entire run
    (starts lifespan once: DB pool init + worker mocked out)
  - function-scoped `test_tenant` — unique tenant per test, CASCADE-deleted on teardown
  - All auth fixtures depend on `test_tenant`, so every test runs in isolation

Requires:
  - DATABASE_URL set in Backend/.env pointing to Supabase
  - forecasting_core is optional — mocked automatically if not installed
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest import mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


def _inject_forecasting_core_mocks() -> None:
    """
    If forecasting_core is not installed, inject lightweight mock modules so that
    import-inside-function calls in runner.py and configuration.py don't ImportError.

    When the real package IS importable (this repo ships it as a sibling dir), do
    nothing — a blanket MagicMock would shadow real submodules like
    forecasting_core.data.profiler, making the inspect endpoint 503 and blocking
    the wizard state machine (DATASET_LOADED never advances → training 409).
    """
    try:
        import forecasting_core  # noqa: F401  — real package available
        return
    except Exception:
        pass

    if "forecasting_core" not in sys.modules:
        import types
        fc = mock.MagicMock()
        for mod in (
            "forecasting_core",
            "forecasting_core.engine",
            "forecasting_core.models",
            "forecasting_core.models.factory",
            "forecasting_core.config",
            "forecasting_core.config.config",
            "forecasting_core.monitoring",
            "forecasting_core.monitoring.drift",
        ):
            sys.modules[mod] = fc

        # forecasting_core.data.canonical needs real values — a plain MagicMock
        # breaks CanonicalColumnsRequest.validate_required() which does
        # `column in REQUIRED_FIELDS` and `FIELD_DEFAULTS.get(field)`.
        _canon = types.ModuleType("forecasting_core.data.canonical")
        _canon.REQUIRED_FIELDS = frozenset({"sku", "date", "demand"})
        _canon.FIELD_DEFAULTS = {
            "sku": None, "date": None, "demand": None,
            "store": "Tienda única", "region": "Sin región",
            "inventory": 0, "lead_time": 7,
            "price": None, "cost": None,
            "regular_price": None, "promo_price": None,
            "promo": None, "promo_type": None, "discount": None,
        }
        sys.modules["forecasting_core.data"] = mock.MagicMock()
        sys.modules["forecasting_core.data.canonical"] = _canon

_inject_forecasting_core_mocks()


def _check_db_reachable() -> str | None:
    """
    Returns None if the DB is reachable, or an error string if not.
    Called once at session start to gate all DB-dependent tests.
    """
    try:
        from backend.config import settings
        import psycopg2
        conn = psycopg2.connect(settings.database_url, connect_timeout=5)
        conn.close()
        return None
    except Exception as e:
        return str(e)


# Lazily evaluated once per test session
_DB_ERROR: str | None = "not_checked"


def _db_available() -> bool:
    global _DB_ERROR
    if _DB_ERROR == "not_checked":
        _DB_ERROR = _check_db_reachable()
    return _DB_ERROR is None


def pytest_collection_modifyitems(config, items):
    """Skip all tests that need DB if the DB is unreachable."""
    if _db_available():
        return
    skip_db = pytest.mark.skip(reason=f"DB unreachable: {_DB_ERROR}")
    for item in items:
        # Never skip: pure-Python generators, offline-marked tests, DB-free RAG tests
        if "TestSyntheticCSVGenerators" in str(item.nodeid):
            continue
        if "test_rag_security" in str(item.nodeid):
            continue
        if item.get_closest_marker("offline"):
            continue
        item.add_marker(skip_db)


# ── App / client ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """FastAPI app with background worker suppressed."""
    with mock.patch("backend.workers.worker.start"):
        from backend.main import app as _app
        yield _app


@pytest.fixture(scope="session")
def client(app):
    """Single TestClient shared across the test session."""
    with mock.patch("backend.notifications.email._send"):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


@pytest.fixture(autouse=True, scope="session")
def _force_local_llm_in_tests():
    """
    backend/ai/local_llm.py::get_local_llm_client() returns a real
    Anthropic-backed client whenever ANTHROPIC_API_KEY is set in .env — the
    same .env the dev server reads. Without this, any test that reaches an
    AI call site fires a real, billed request, and stalls for a long time if
    that key's account has no credit (observed directly: a full-suite run
    died silently mid-test after several such calls each burned the
    client's retry/timeout budget). Autouse + session-scoped so every test
    is protected regardless of whether it uses the `client` fixture.
    """
    from backend.ai.local_llm import LocalLLMClient

    with mock.patch("backend.ai.local_llm.get_local_llm_client", side_effect=LocalLLMClient):
        yield


# ── Database helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def test_tenant():
    """
    Creates a unique tenant before each test.
    Deletes it (CASCADE → users, sessions, jobs, datasets) on teardown.
    """
    from backend.tenants.service import create_tenant
    from backend.db.connection import execute
    name = f"pytest-{uuid4().hex[:10]}"
    tenant = create_tenant(name)
    yield tenant
    execute("DELETE FROM tenants WHERE id = %s", (tenant["id"],))


@pytest.fixture
def registered_user(test_tenant):
    """Admin user in test_tenant with verified email."""
    from backend.users import service as user_svc
    email = f"admin-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(
        tenant_id=test_tenant["id"],
        email=email,
        password=password,
        role="admin",
        full_name="Test Admin",
    )
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def auth_headers(client, registered_user):
    """Bearer auth headers for the test admin user."""
    resp = client.post("/api/v1/auth/login", json={
        "email": registered_user["email"],
        "password": registered_user["password"],
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def analyst_user(test_tenant):
    """Analyst-role user in test_tenant."""
    from backend.users import service as user_svc
    email = f"analyst-{uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    user = user_svc.create_user(
        tenant_id=test_tenant["id"],
        email=email,
        password=password,
        role="analyst",
        full_name="Test Analyst",
    )
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def analyst_headers(client, analyst_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": analyst_user["email"],
        "password": analyst_user["password"],
    })
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def viewer_user(test_tenant):
    """Viewer-role user in test_tenant."""
    from backend.users import service as user_svc
    email = f"viewer-{uuid4().hex[:8]}@pytest.example.com"
    password = "TestPass123!"
    user = user_svc.create_user(
        tenant_id=test_tenant["id"],
        email=email,
        password=password,
        role="viewer",
        full_name="Test Viewer",
    )
    user_svc.mark_verified(test_tenant["id"], user["id"])
    return {"user": user, "tenant": test_tenant, "password": password, "email": email}


@pytest.fixture
def viewer_headers(client, viewer_user):
    resp = client.post("/api/v1/auth/login", json={
        "email": viewer_user["email"],
        "password": viewer_user["password"],
    })
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_tenant_user_headers(client):
    """
    Factory fixture for entitlements tests: creates a fresh tenant on a given
    plan/trial state, plus a verified user with a given role, and returns
    login headers for that user.

    make_tenant_user_headers(plan="starter", role="analyst",
                              expired_trial=False, return_tenant_id=False)

    Every tenant created through the factory is tracked and CASCADE-deleted
    on teardown, mirroring `test_tenant`.
    """
    from backend.tenants.service import create_tenant
    from backend.db.connection import execute
    from backend.users import service as user_svc

    created_tenant_ids: list[str] = []

    def _make(plan="starter", role="analyst", expired_trial=False, return_tenant_id=False):
        tenant = create_tenant(f"pytest-{uuid4().hex[:10]}")
        tenant_id = tenant["id"]
        created_tenant_ids.append(tenant_id)

        if expired_trial:
            trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
        else:
            trial_ends_at = None
        execute(
            "UPDATE tenants SET plan=%s, trial_ends_at=%s WHERE id=%s",
            (plan, trial_ends_at, tenant_id),
        )

        email = f"{role}-{uuid4().hex[:8]}@example.com"
        password = "TestPass123!"
        user = user_svc.create_user(
            tenant_id=tenant_id,
            email=email,
            password=password,
            role=role,
            full_name="Test User",
        )
        user_svc.mark_verified(tenant_id, user["id"])

        resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        token = resp.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        if return_tenant_id:
            return headers, tenant_id
        return headers

    yield _make

    for tid in created_tenant_ids:
        execute("DELETE FROM tenants WHERE id = %s", (tid,))


# ── Resource helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def test_session(client, auth_headers):
    """DRAFT session owned by registered_user. Cleaned up by test_tenant CASCADE."""
    resp = client.post(
        "/api/v1/sessions",
        json={"name": f"pytest-session-{uuid4().hex[:6]}", "description": "automated"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["data"]


@pytest.fixture
def csv_bytes():
    """Synthetic 5-SKU × 60-day CSV as bytes."""
    from tests.fixtures.synthetic_data import generate_csv_bytes
    return generate_csv_bytes(n_skus=5, n_days=60)


@pytest.fixture
def uploaded_dataset(client, auth_headers, csv_bytes):
    """Uploads a CSV and returns the dataset metadata dict."""
    resp = client.post(
        "/api/v1/datasets",
        files={"file": ("test_sales.csv", csv_bytes, "text/csv")},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


@pytest.fixture
def configured_session(client, auth_headers, test_session, uploaded_dataset):
    """
    Session with dataset attached and all wizard steps completed
    (columns + features + models configured).
    Status: MODELS_CONFIGURED — ready for training trigger.
    """
    sid = test_session["id"]

    # Attach dataset
    client.post(
        f"/api/v1/sessions/{sid}/dataset",
        json={"dataset_id": uploaded_dataset["id"]},
        headers=auth_headers,
    )

    # Inspect — required to advance DATASET_LOADED → INSPECTED before columns
    # config can transition the wizard state machine.
    client.get(f"/api/v1/sessions/{sid}/inspect", headers=auth_headers)

    # Columns
    client.post(
        f"/api/v1/sessions/{sid}/configure/columns",
        json={"date_column": "date", "target_column": "sales", "sku_column": "sku"},
        headers=auth_headers,
    )

    # Features
    client.post(
        f"/api/v1/sessions/{sid}/configure/features",
        json={"lags": [1, 7], "rolling": [7], "diffs": [1], "calendar": True},
        headers=auth_headers,
    )

    # Models
    client.post(
        f"/api/v1/sessions/{sid}/configure/models",
        json={"mode": "selected", "selected_models": ["lightgbm"]},
        headers=auth_headers,
    )

    resp = client.get(f"/api/v1/sessions/{sid}", headers=auth_headers)
    return resp.json()["data"]


@pytest.fixture
def completed_session(configured_session, registered_user):
    """
    Session with synthetic training results seeded directly into the DB.
    Status: COMPLETED — use to test results/forecast endpoints.
    """
    from tests.fixtures.synthetic_data import seed_completed_session
    tid = registered_user["tenant"]["id"]
    sid = configured_session["id"]
    seed_completed_session(tid, sid, n_skus=3)
    return {**configured_session, "status": "COMPLETED"}
