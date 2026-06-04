"""Tests for forecasting_core.registry.ModelRegistry."""

import json
import pytest

from forecasting_core.registry.registry import ModelRegistry


def _sample_results():
    return {
        "lightgbm_A": {"mae": 3.2, "rmse": 4.1, "wape": 0.05},
        "lightgbm_B": {"mae": 5.0, "rmse": 6.2, "wape": 0.08},
    }


# ---------------------------------------------------------------------------
# Basic log_run / list_runs
# ---------------------------------------------------------------------------

class TestModelRegistryBasic:

    def test_log_run_returns_run_id(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        run_id = reg.log_run("sess1", "hash123", _sample_results())
        assert "sess1" in run_id
        assert "hash123" in run_id

    def test_log_run_persists_to_file(self, tmp_path):
        path = str(tmp_path / "reg.json")
        reg = ModelRegistry(path)
        reg.log_run("sess1", "abc", _sample_results())
        with open(path) as f:
            data = json.load(f)
        assert len(data["runs"]) == 1

    def test_list_runs_returns_all(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("sess1", "h1", _sample_results())
        reg.log_run("sess1", "h2", _sample_results())
        assert len(reg.list_runs()) == 2

    def test_list_runs_filtered_by_session(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("sess1", "h1", _sample_results())
        reg.log_run("sess2", "h2", _sample_results())
        assert len(reg.list_runs("sess1")) == 1
        assert len(reg.list_runs("sess2")) == 1

    def test_list_runs_sorted_newest_first(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("sess1", "h1", {"a": {"mae": 10.0}})
        reg.log_run("sess1", "h2", {"a": {"mae": 5.0}})
        runs = reg.list_runs("sess1")
        # Timestamps are ISO strings; sorted reverse means latest is first
        assert runs[0]["timestamp"] >= runs[1]["timestamp"]

    def test_list_runs_unknown_session_returns_empty(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("sess1", "h1", _sample_results())
        assert reg.list_runs("nonexistent") == []

    def test_metadata_stored_in_run(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("sess1", "h1", _sample_results(), metadata={"user": "test"})
        run = reg.list_runs()[0]
        assert run["metadata"]["user"] == "test"

    def test_missing_file_loads_empty(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "nonexistent.json"))
        assert reg.list_runs() == []

    def test_corrupt_file_loads_empty(self, tmp_path):
        path = str(tmp_path / "bad.json")
        with open(path, "w") as f:
            f.write("NOT JSON{{{")
        reg = ModelRegistry(path)
        assert reg.list_runs() == []


# ---------------------------------------------------------------------------
# best_run
# ---------------------------------------------------------------------------

class TestBestRun:

    def test_best_run_returns_lowest_mae(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("sess1", "h1", {"k": {"mae": 10.0}})
        reg.log_run("sess1", "h2", {"k": {"mae": 3.0}})
        best = reg.best_run("sess1", "mae")
        vals = [v["mae"] for v in best["results"].values() if "mae" in v]
        assert min(vals) == 3.0

    def test_best_run_empty_session_returns_none(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        assert reg.best_run("missing_sess") is None

    def test_best_run_ignores_runs_without_metric(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("sess1", "h1", {"k": {"rmse": 5.0}})  # no mae key
        reg.log_run("sess1", "h2", {"k": {"mae": 2.0}})
        best = reg.best_run("sess1", "mae")
        # Only the second run has mae — it must win
        assert "mae" in list(best["results"].values())[0]


# ---------------------------------------------------------------------------
# compare_sessions
# ---------------------------------------------------------------------------

class TestCompareSessions:

    def test_compare_sessions_returns_best_per_session(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("alpha", "h1", {"k": {"mae": 8.0}})
        reg.log_run("alpha", "h2", {"k": {"mae": 4.0}})
        reg.log_run("beta",  "h3", {"k": {"mae": 6.0}})
        cmp = reg.compare_sessions("mae")
        assert cmp["alpha"] == pytest.approx(4.0)
        assert cmp["beta"]  == pytest.approx(6.0)

    def test_compare_sessions_empty_registry(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        assert reg.compare_sessions("mae") == {}

    def test_compare_sessions_skips_runs_without_metric(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        reg.log_run("sess1", "h1", {"k": {"rmse": 2.0}})  # no mae
        cmp = reg.compare_sessions("mae")
        assert "sess1" not in cmp

    def test_multiple_runs_same_session_picks_min(self, tmp_path):
        reg = ModelRegistry(str(tmp_path / "reg.json"))
        for mae_val in [9.0, 5.0, 7.0]:
            reg.log_run("sess1", f"h{mae_val}", {"k": {"mae": mae_val}})
        cmp = reg.compare_sessions("mae")
        assert cmp["sess1"] == pytest.approx(5.0)
