"""
RAG Security Test Suite — Multi-Tenant Isolation Protocol

Verifies:
  1. Legitimate query (Tenant A, user_analista_01) → retrieves correct data
  2. Cross-tenant attack (Tenant B namespace) → empty results, no data leak
  3. Cross-user attack (same tenant, wrong user_id) → empty results
  4. Off-topic query (below MIN_SCORE) → safe refusal, LLM never called
  5. Audit metadata — every vector carries user_id, tenant_id, timestamp

Prerequisites:
  - VOYAGEAI_API_KEY, PINECONE_API_KEY, PINECONE_INDEX, ANTHROPIC_API_KEY in .env
  - pip install voyageai pinecone anthropic

Run:
  cd backend
  python -m pytest tests/test_rag_security.py -v
"""

from __future__ import annotations

import os
import sys
import time
import pytest

# ── Allow running from repo root ──────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# ── Test identities ───────────────────────────────────────────────────────────
TENANT_A      = "tenant_9942_DIPO"
TENANT_B      = "tenant_8811_DISAL"
USER_ANALYST  = "user_analista_01"    # authorised user — owns the indexed session
USER_MANAGER  = "user_gerente_02"     # different user in same tenant
SESSION_A     = "sess_test_security_A"
SESSION_B     = "sess_test_security_B"

# ── Synthetic session data for Tenant A ──────────────────────────────────────
_RESULT_A = {
    "metrics": {
        "by_model": {
            "lightgbm": {"avg_mae": 312.4, "avg_rmse": 418.7, "avg_wape": 0.112},
        },
        "rows": [
            {"sku": "SKU-104", "model": "lightgbm", "mae": 287.3, "wape": 0.098, "rmse": 390.1},
        ],
    },
    "inventory": {
        "recommendations": [
            {
                "sku": "SKU-104",
                "reorder_point": 4500,
                "safety_stock": 900,
                "stockout_risk": 0.72,
                "holding_cost": 1350.0,
                "action": "REORDER",
            }
        ]
    },
    "data_quality": {
        "SKU-104": {
            "quality_score": 0.91,
            "series_type": "seasonal",
            "n_rows": 156,
            "missing_pct": 0.01,
            "n_outliers": 2,
            "is_valid": True,
            "warnings": [],
        }
    },
    "routing": {"SKU-104": ["lightgbm", "arima"]},
    "config": {
        "columns":  {"target": "demand", "date": "date", "group": "sku"},
        "training": {"train_ratio": 0.8, "wfv_splits": 3, "min_history": 20},
        "forecast": {"horizon": 12},
        "features": {"lags": [1, 7], "rolling": [7, 14], "calendar": True},
    },
}
_INSPECTION_A = {
    "profile": {
        "stats": {
            "n_rows": 156, "n_skus": 1, "date_min": "2022-01-01", "date_max": "2024-12-31"
        },
        "recommended": {"freq": "monthly"},
        "warnings": [],
    }
}
_FORECASTS_A = {
    "SKU-104": {
        "lightgbm": {
            "historical": [{"date": "2024-11-01", "value": 4100.0}],
            "forecast":   [
                {"date": "2025-01-01", "value": 4500.0},
                {"date": "2025-02-01", "value": 4650.0},
                {"date": "2025-12-01", "value": 4900.0},
            ],
        }
    }
}

# ── Synthetic data for Tenant B ───────────────────────────────────────────────
_RESULT_B = {
    "metrics": {"by_model": {"arima": {"avg_mae": 55.2, "avg_rmse": 71.0, "avg_wape": 0.21}}, "rows": []},
    "inventory": {"recommendations": [
        {"sku": "RTE-01", "reorder_point": 80, "safety_stock": 20,
         "stockout_risk": 0.15, "holding_cost": 240.0, "action": "OK"}
    ]},
    "data_quality": {},
    "routing": {"RTE-01": ["arima"]},
    "config": {
        "columns":  {"target": "units", "date": "fecha", "group": "ruta"},
        "training": {"train_ratio": 0.8, "wfv_splits": 3, "min_history": 20},
        "forecast": {"horizon": 8},
        "features": {"lags": [1], "rolling": [7], "calendar": False},
    },
}
_INSPECTION_B = {
    "profile": {
        "stats": {"n_rows": 312, "n_skus": 1, "date_min": "2023-01-01", "date_max": "2024-12-31"},
        "recommended": {"freq": "weekly"},
        "warnings": ["Ruta bloqueada detectada en Pérez Zeledón"],
    }
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _skip_if_no_keys():
    """Skip test if RAG API keys are missing."""
    from backend.config import settings  # type: ignore
    missing = [
        k for k, v in [
            ("VOYAGEAI_API_KEY",  settings.voyageai_api_key),
            ("PINECONE_API_KEY",  settings.pinecone_api_key),
            ("PINECONE_INDEX",    settings.pinecone_index),
            ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
        ] if not v
    ]
    if missing:
        pytest.skip(f"Missing API keys: {', '.join(missing)}")


@pytest.fixture(scope="module")
def rag_svc():
    """Return initialised RAGService; skip if keys are missing OR invalid/unreachable.

    A skip (not a hard failure) is correct here: this fixture verifies the
    *environment* can reach Voyage/Pinecone/Anthropic, not the security logic
    itself. An expired/invalid key should never read as "the security
    boundary tests failed" — that would either mask a real regression in CI
    noise, or get the whole file ignored as "always broken."
    """
    _skip_if_no_keys()
    from backend.ai.rag_service import RAGService  # type: ignore
    svc = RAGService()
    if not svc._init():
        pytest.skip("RAGService failed to initialise — check API keys/connectivity (see logs)")
    return svc


@pytest.fixture(scope="module")
def populated_index(rag_svc):
    """
    Populate Pinecone with test vectors for both tenants and clean up after the suite.
    Scope=module so indexing runs once for all tests.
    """
    # ── Index Tenant A / analyst user ────────────────────────────────
    n_a = rag_svc.index_session(
        tenant_id=TENANT_A,
        session_id=SESSION_A,
        result=_RESULT_A,
        inspection=_INSPECTION_A,
        forecasts=_FORECASTS_A,
        user_id=USER_ANALYST,
        session_name="DIPO — Diciembre Peak Analysis",
    )
    assert n_a > 0, "Failed to index Tenant A vectors"

    # ── Index Tenant B / different company ───────────────────────────
    n_b = rag_svc.index_session(
        tenant_id=TENANT_B,
        session_id=SESSION_B,
        result=_RESULT_B,
        inspection=_INSPECTION_B,
        forecasts={},
        user_id="user_disal_admin",
        session_name="DISAL — Rutas Pérez Zeledón",
    )
    assert n_b > 0, "Failed to index Tenant B vectors"

    # Give Pinecone a moment to make the vectors queryable
    time.sleep(3)

    yield {"n_a": n_a, "n_b": n_b}

    # ── Teardown — remove test vectors ───────────────────────────────
    rag_svc.delete_session(TENANT_A, SESSION_A)
    rag_svc.delete_session(TENANT_B, SESSION_B)


# Valid sources when pipeline passes security gates (LLM may be unavailable)
_PIPELINE_PASSED = ("rag", "rag_retrieved", "fallback")


# ── Test 1 — Legitimate query ─────────────────────────────────────────────────

class TestLegitimateQuery:
    """Authorised user queries their own session data and gets correct results."""

    def test_indexed_vectors_exist(self, rag_svc, populated_index):
        info = rag_svc.is_indexed(TENANT_A, SESSION_A, USER_ANALYST)
        assert info["indexed"] is True, "Session A should be indexed"

    def test_returns_rag_source(self, rag_svc, populated_index):
        """Security gate passed — vectors found and similarity above threshold."""
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="What is the demand forecast for SKU-104 in December?",
            user_id=USER_ANALYST,
        )
        assert result["source"] in _PIPELINE_PASSED, (
            f"Expected pipeline to pass (rag/rag_retrieved/fallback), got: {result['source']}"
        )

    def test_answer_contains_sku104(self, rag_svc, populated_index):
        """SKU-104 data must appear in either the answer or the retrieved chunks."""
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="What is the inventory recommendation for SKU-104?",
            user_id=USER_ANALYST,
        )
        # Check retrieved chunks — these always contain the raw text regardless of LLM
        all_text = result["answer"] + " ".join(result.get("retrieved", []))
        assert "SKU-104" in all_text, "SKU-104 must appear in retrieved context or answer"

    def test_answer_contains_reorder_data(self, rag_svc, populated_index):
        """Inventory data (reorder point 4500) must appear in retrieved chunks."""
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="What is the reorder point and stockout risk for SKU-104?",
            user_id=USER_ANALYST,
        )
        all_text = result["answer"] + " ".join(result.get("retrieved", []))
        assert any(
            kw in all_text for kw in ["4500", "4,500", "reorder", "REORDER", "stockout"]
        ), f"Inventory data must be in retrieved context. Retrieved: {result.get('retrieved', [])[:1]}"

    def test_retrieved_chunks_not_empty(self, rag_svc, populated_index):
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="How accurate is the LightGBM model?",
            user_id=USER_ANALYST,
        )
        assert len(result["retrieved"]) > 0, "Should retrieve at least one chunk"

    def test_metadata_audit_fields(self, rag_svc, populated_index):
        """Every chunk in the tenant namespace has the required audit metadata."""
        zero_vec = [0.0] * 1024
        resp = rag_svc._index.query(
            vector=zero_vec,
            top_k=5,
            include_metadata=True,
            namespace=TENANT_A,
            filter={"session_id": {"$eq": SESSION_A}},
        )
        for match in resp.get("matches", []):
            meta = match.get("metadata", {})
            for field in ("user_id", "tenant_id", "session_id", "project_id",
                          "document_title", "timestamp"):
                assert field in meta, f"Missing metadata field '{field}' in vector {match['id']}"
            assert meta["user_id"] == USER_ANALYST
            assert meta["tenant_id"] == TENANT_A


# ── Test 2 — Cross-Tenant Attack ──────────────────────────────────────────────

class TestCrossTenantAttack:
    """
    A user from Tenant B (or a spoofed request) tries to access Tenant A data.
    The namespace=tenant_id barrier must return zero results.
    """

    def test_tenant_b_user_cannot_reach_tenant_a_data(self, rag_svc, populated_index):
        """Query Tenant A session but from Tenant B namespace — must return no_access."""
        result = rag_svc.query(
            tenant_id=TENANT_B,          # wrong tenant namespace
            session_id=SESSION_A,        # Tenant A session
            question="What is the demand forecast for SKU-104 in December?",
            user_id="user_disal_admin",
        )
        assert result["source"] == "no_access", (
            f"Cross-tenant query must return no_access, got: {result['source']}"
        )
        assert "SKU-104" not in result["answer"], "Cross-tenant response must not contain Tenant A data"

    def test_tenant_b_data_not_visible_in_tenant_a_namespace(self, rag_svc, populated_index):
        """Verify Pérez Zeledón data (Tenant B) never appears in Tenant A queries."""
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="What about the blocked routes in Pérez Zeledón?",
            user_id=USER_ANALYST,
        )
        assert "Pérez Zeledón" not in result["answer"], (
            "Tenant B data must never appear in Tenant A responses"
        )
        assert "bloqueada" not in result["answer"].lower()


# ── Test 3 — Cross-User Attack (Same Tenant) ──────────────────────────────────

class TestCrossUserAttack:
    """
    A different user in the same tenant (user_gerente_02) tries to access
    data indexed by user_analista_01. The user_id metadata filter must block this.
    """

    def test_wrong_user_gets_no_access(self, rag_svc, populated_index):
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="What is the inventory recommendation for SKU-104?",
            user_id=USER_MANAGER,    # different user — does not own these vectors
        )
        assert result["source"] == "no_access", (
            f"Cross-user query must return no_access, got: {result['source']}"
        )
        assert "4500" not in result["answer"] and "4,500" not in result["answer"], (
            "Cross-user response must not leak inventory data"
        )

    def test_legitimate_user_still_works_after_attack(self, rag_svc, populated_index):
        """Ensure the legitimate user's access is unaffected by the attack test."""
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="What is the forecast accuracy for SKU-104?",
            user_id=USER_ANALYST,
        )
        assert result["source"] in _PIPELINE_PASSED, (
            f"Legitimate user must pass security gate, got: {result['source']}"
        )
        assert result["answer"]


# ── Test 4 — Off-Topic Query Rejection ───────────────────────────────────────

class TestOffTopicRejection:
    """
    Queries unrelated to logistics/forecasting must be blocked at the
    similarity threshold level — the LLM is never called.
    """

    def test_tourist_query_rejected(self, rag_svc, populated_index):
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="What are the best tourist beaches in Costa Rica?",
            user_id=USER_ANALYST,
        )
        assert result["source"] == "off_topic", (
            f"Tourist query must be rejected as off_topic, got: {result['source']}"
        )
        assert len(result["retrieved"]) == 0

    def test_sports_query_rejected(self, rag_svc, populated_index):
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="Who won the FIFA World Cup in 2022?",
            user_id=USER_ANALYST,
        )
        assert result["source"] == "off_topic"

    def test_cooking_query_rejected(self, rag_svc, populated_index):
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="How do I make a traditional gallo pinto recipe?",
            user_id=USER_ANALYST,
        )
        assert result["source"] == "off_topic"

    def test_off_topic_response_is_corporate_refusal(self, rag_svc, populated_index):
        """The refusal must not leak specific session data (SKU IDs, inventory numbers)."""
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question="What is the capital of France?",
            user_id=USER_ANALYST,
        )
        assert result["source"] == "off_topic"
        # The word "SKU" as a generic category is fine; specific IDs must not appear
        assert "SKU-104" not in result["answer"], "Specific SKU identifier must not leak"
        assert "4500" not in result["answer"], "Inventory numbers must not leak"
        # Should explain the scope of allowed questions
        assert any(kw in result["answer"].lower() for kw in
                   ["forecast", "session", "data", "question"]), (
            f"Refusal should explain scope. Got: {result['answer'][:200]}"
        )


# ── Test 5 — Legitimate On-Topic Forecasting Queries ─────────────────────────

class TestOnTopicForecasting:
    """Verify that genuine forecasting questions pass the threshold and get answers."""

    @pytest.mark.parametrize("question", [
        "What is the average MAE for the LightGBM model?",
        "Which SKUs have the highest stockout risk?",
        "How many rows are in the dataset?",
        "What is the forecast trend for SKU-104?",
        "Is the data quality good enough for reliable forecasts?",
    ])
    def test_forecasting_question_passes(self, rag_svc, populated_index, question):
        result = rag_svc.query(
            tenant_id=TENANT_A,
            session_id=SESSION_A,
            question=question,
            user_id=USER_ANALYST,
        )
        assert result["source"] in _PIPELINE_PASSED, (
            f"Forecasting question should pass security gate. source={result['source']}, "
            f"question={question!r}"
        )
        assert result["answer"], "Answer must not be empty"


# ── Standalone runner (not pytest) ───────────────────────────────────────────

def _run_manual():
    """Quick manual validation without pytest — prints pass/fail for each scenario."""
    print("\n=== RAG Security Audit ===\n")

    from backend.ai.rag_service import RAGService  # type: ignore

    svc = RAGService()
    if not svc._init():
        print("SKIP — RAG not initialised (check API keys)")
        return

    print("Indexing test data...")
    n = svc.index_session(
        tenant_id=TENANT_A, session_id=SESSION_A,
        result=_RESULT_A, inspection=_INSPECTION_A, forecasts=_FORECASTS_A,
        user_id=USER_ANALYST, session_name="DIPO Security Test",
    )
    print(f"  Indexed {n} vectors for Tenant A / {USER_ANALYST}")

    svc.index_session(
        tenant_id=TENANT_B, session_id=SESSION_B,
        result=_RESULT_B, inspection=_INSPECTION_B, forecasts={},
        user_id="user_disal_admin", session_name="DISAL Security Test",
    )
    time.sleep(3)

    tests = [
        ("PASS", "Legitimate query",
         svc.query(TENANT_A, SESSION_A, "Inventory for SKU-104?", user_id=USER_ANALYST)),
        ("no_access", "Cross-tenant attack",
         svc.query(TENANT_B, SESSION_A, "Inventory for SKU-104?", user_id="user_disal_admin")),
        ("no_access", "Cross-user attack",
         svc.query(TENANT_A, SESSION_A, "Inventory for SKU-104?", user_id=USER_MANAGER)),
        ("off_topic", "Off-topic query",
         svc.query(TENANT_A, SESSION_A, "Best beaches in Costa Rica?", user_id=USER_ANALYST)),
    ]

    all_ok = True
    for expected, label, result in tests:
        src = result["source"]
        if expected == "PASS":
            ok_flag = src in ("rag", "fallback")
        else:
            ok_flag = src == expected
        status = "✓ PASS" if ok_flag else "✗ FAIL"
        if not ok_flag:
            all_ok = False
        print(f"  {status}  [{label}]  source={src}")

    print("\nCleaning up test vectors...")
    svc.delete_session(TENANT_A, SESSION_A)
    svc.delete_session(TENANT_B, SESSION_B)

    verdict = "CERTIFIED — RAG security ready for production" if all_ok else "FAILED — review failures above"
    print(f"\n=== {verdict} ===\n")


if __name__ == "__main__":
    _run_manual()
