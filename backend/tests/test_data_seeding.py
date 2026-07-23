"""
PHASE 1 — Data preparation validation.
Verifies that synthetic data generators produce valid, well-formed datasets
before any endpoint tests run.
"""

import csv
import io
from datetime import date

import pytest
from tests.fixtures.synthetic_data import (
    generate_csv_bytes,
    generate_large_csv_bytes,
    generate_malformed_csv_bytes,
    generate_minimal_csv_bytes,
    generate_sparse_csv_bytes,
    generate_wrong_schema_csv_bytes,
    seed_completed_session,
)


# ── CSV generator tests ───────────────────────────────────────────────────────

class TestSyntheticCSVGenerators:
    def test_default_csv_has_correct_columns(self):
        data = generate_csv_bytes(n_skus=2, n_days=10)
        reader = csv.DictReader(io.StringIO(data.decode()))
        assert set(reader.fieldnames) == {"date", "sku", "sales"}

    def test_default_csv_row_count(self):
        data = generate_csv_bytes(n_skus=3, n_days=20)
        reader = list(csv.DictReader(io.StringIO(data.decode())))
        assert len(reader) == 3 * 20

    def test_sku_names_are_distinct_and_consistent(self):
        data = generate_csv_bytes(n_skus=5, n_days=10)
        reader = list(csv.DictReader(io.StringIO(data.decode())))
        skus = {row["sku"] for row in reader}
        assert len(skus) == 5
        for sku in skus:
            assert sku.startswith("SKU_")

    def test_dates_are_consecutive_per_sku(self):
        data = generate_csv_bytes(n_skus=2, n_days=7)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        by_sku: dict = {}
        for row in rows:
            by_sku.setdefault(row["sku"], []).append(date.fromisoformat(row["date"]))
        for sku, dates in by_sku.items():
            for i in range(1, len(dates)):
                delta = (dates[i] - dates[i - 1]).days
                assert delta == 1, f"Gap in dates for {sku} at position {i}"

    def test_sales_values_are_non_negative(self):
        data = generate_csv_bytes(n_skus=5, n_days=30)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        for row in rows:
            assert float(row["sales"]) >= 0.0

    def test_same_seed_is_deterministic(self):
        data1 = generate_csv_bytes(n_skus=3, n_days=10, seed=123)
        data2 = generate_csv_bytes(n_skus=3, n_days=10, seed=123)
        assert data1 == data2

    def test_different_seeds_produce_different_data(self):
        data1 = generate_csv_bytes(n_skus=3, n_days=10, seed=1)
        data2 = generate_csv_bytes(n_skus=3, n_days=10, seed=2)
        assert data1 != data2

    def test_minimal_csv_is_valid(self):
        data = generate_minimal_csv_bytes()
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        assert len(rows) == 2 * 30

    def test_sparse_csv_contains_zeros(self):
        data = generate_sparse_csv_bytes()
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        values = [float(r["sales"]) for r in rows]
        assert any(v == 0.0 for v in values), "Sparse CSV should have zero values"

    def test_large_csv_size(self):
        data = generate_large_csv_bytes(n_skus=50, n_days=365)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        assert len(rows) == 50 * 365

    def test_wrong_schema_csv_missing_expected_columns(self):
        data = generate_wrong_schema_csv_bytes()
        reader = csv.DictReader(io.StringIO(data.decode()))
        assert "date" not in reader.fieldnames
        assert "sales" not in reader.fieldnames

    def test_malformed_csv_bytes_are_non_empty(self):
        data = generate_malformed_csv_bytes()
        assert len(data) > 0

    def test_returns_bytes_not_string(self):
        data = generate_csv_bytes()
        assert isinstance(data, bytes)

    def test_custom_column_names(self):
        data = generate_csv_bytes(
            n_skus=2, n_days=5,
            date_col="timestamp", sku_col="product_id", target_col="demand"
        )
        reader = csv.DictReader(io.StringIO(data.decode()))
        assert set(reader.fieldnames) == {"timestamp", "product_id", "demand"}


# ── DB seeding test ───────────────────────────────────────────────────────────

@pytest.mark.integration
class TestDBSeeding:
    def test_seed_completed_session_stores_result(self, test_tenant, test_session):
        from backend.db import session_store

        # Transition session to MODELS_CONFIGURED so it can be force-set to COMPLETED
        tid = test_tenant["id"]
        sid = test_session["id"]

        result = seed_completed_session(tid, sid, n_skus=2)
        assert result["metrics"]["n_skus"] == 2

        stored = session_store.get_training_result(tid, sid)
        assert stored is not None
        assert stored["job_id"] == "job_test_seed"
        assert "metrics" in stored

    def test_seed_stores_forecasts(self, test_tenant, test_session):
        from backend.db import session_store
        tid = test_tenant["id"]
        sid = test_session["id"]

        seed_completed_session(tid, sid, n_skus=2)
        forecasts = session_store.get_forecasts(tid, sid)
        assert forecasts is not None
        assert len(forecasts) == 2
        for sku, models in forecasts.items():
            assert "lightgbm" in models
            assert "forecast" in models["lightgbm"]
            assert "historical" in models["lightgbm"]

    def test_seed_transitions_session_to_completed(self, test_tenant, test_session):
        from backend.sessions.service import get_session
        tid = test_tenant["id"]
        sid = test_session["id"]

        seed_completed_session(tid, sid, n_skus=1)
        s = get_session(tid, sid)
        assert s["status"] == "COMPLETED"
