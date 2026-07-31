"""
The two silent outcomes of inventory generation must reach the user.

Both were previously invisible. The second one is the dangerous one: a product
with no purchase recommendation renders on the semáforo exactly like a product
that is well stocked, so the failure mode is the buyer not ordering something
they needed to order — and nothing anywhere told them.
"""

import pandas as pd
import pytest

from forecasting_core.config.config import SessionConfig
from forecasting_core.pipelines.pipeline import Pipeline


def _pipeline():
    return Pipeline(SessionConfig.from_dict({
        "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
        "models": {"lightgbm": {}},
    }))


class TestFindingShape:
    """`_collect_run_warnings` groups on these fields; a typo makes them vanish."""

    def test_no_finding_when_nothing_happened(self):
        assert _pipeline()._inventory_findings() == []

    def test_baseline_finding_carries_the_grouping_fields(self):
        pipeline = _pipeline()
        pipeline._outperformed_by_baseline = [
            {"sku": "A", "model": "lightgbm", "baseline": "naive"},
        ]
        finding = pipeline._inventory_findings()[0]
        assert finding["error_id"] == "NO_MODEL_BEAT_BASELINE"
        assert finding["severity"] == "warning"
        assert finding["layer"] == "inventory"
        assert "A" in finding["message"]
        assert finding["context"]["baseline"] == "naive"

    def test_missing_recommendation_is_an_error_not_a_warning(self):
        """A product that silently drops off the buy list outranks a caveat."""
        pipeline = _pipeline()
        pipeline._skipped_no_forecast = [{"sku": "B", "model": "prophet"}]
        finding = pipeline._inventory_findings()[0]
        assert finding["error_id"] == "SKU_WITHOUT_RECOMMENDATION"
        assert finding["severity"] == "error"
        assert "B" in finding["message"]

    def test_one_finding_per_sku_so_the_panel_can_count_them(self):
        pipeline = _pipeline()
        pipeline._outperformed_by_baseline = [
            {"sku": f"SKU_{i}", "model": "lightgbm", "baseline": "naive"}
            for i in range(4)
        ]
        assert len(pipeline._inventory_findings()) == 4


# The other half of this contract — that these findings survive the backend's
# payload mapper and reach the panel — is asserted in
# `backend/tests/test_run_corrections_payload.py`. It lives there because this
# library must not import `backend`: the dependency runs one way only.


class TestEndToEndThroughARun:

    def test_a_real_run_reports_no_phantom_findings(self):
        """A healthy run must not manufacture warnings out of nothing."""
        import numpy as np

        n = 200
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        rng = np.random.default_rng(4)
        df = pd.concat([
            pd.DataFrame({
                "date": dates, "sku": f"SKU_{i}",
                "demand": np.clip(
                    100.0 * (1 + 0.4 * np.sin(2 * np.pi * np.arange(n) / 7.0))
                    * rng.normal(1, 0.05, n), 0, None),
            })
            for i in range(3)
        ], ignore_index=True)

        cfg = SessionConfig.from_dict({
            "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
            "features": {"lags": [1, 7], "rolling": [7], "diffs": [1]},
            "models": {"lightgbm": {"n_estimators": 60}},
            "training": {"train_ratio": 0.8, "wfv_splits": 2},
            "forecast": {"horizon": 7},
        })
        results = Pipeline(cfg, df=df).run()

        codes = {f.get("error_id") for f in results.metadata["validation_findings"]}
        assert "SKU_WITHOUT_RECOMMENDATION" not in codes, (
            "a healthy run dropped a SKU from the recommendations"
        )
        assert "outperformed_by_baseline" in results.metadata
        assert "skipped_no_forecast" in results.metadata
