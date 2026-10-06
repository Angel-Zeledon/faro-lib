"""The Python reference of the S&OP consensus math, and the evidence it is graded on.

The Rust service is held to `tests/contract/consensus_reference.py` by exact
equality (a unit test over `consensus_cases.json`). This file keeps that spec
honest: the fixture is current, the reference agrees with the engine's own
`forecast_value_added` (within float noise, except the one case it deliberately
reads differently), and the consensus rule behaves as documented. It also pins the
pure part of the Python evidence writer. No database.
"""

import json
import math
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "tests" / "contract"
sys.path.insert(0, str(CONTRACT))

import consensus_reference as ref  # noqa: E402

pytestmark = pytest.mark.offline


def _cfg(rule="priority", **over):
    base = {"rule": rule, "priority": ["sales", "finance", "operations"],
            "weights": {"sales": 1, "finance": 1, "operations": 1}, "cap_down_bp": -10000, "cap_up_bp": 100000}
    return {**base, **over}


def _sub(i, f, a, b, bp, sku="X"):
    return {"id": i, "sku": sku, "function": f, "start": a, "end": b, "pct_bp": bp}


class TestFixtureIsCurrent:

    def test_the_rust_fixture_is_what_the_reference_answers_today(self):
        done = subprocess.run([sys.executable, str(CONTRACT / "gen_consensus_fixtures.py"), "--check"],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr

    def test_the_fixture_exercises_real_cases(self):
        doc = json.loads((ROOT / "backend-rs" / "src" / "consensus" / "consensus_cases.json").read_text("utf-8"))
        assert sum(len(c["lines"]) for c in doc["consensus"]) > 500
        assert {c["config"]["rule"] for c in doc["consensus"]} == {"priority", "weighted"}
        assert any(ln["inputs"] and any(i["capped"] for i in ln["inputs"])
                   for c in doc["consensus"] for ln in c["lines"]), "caps must bite somewhere"
        verdicts = {c["aggregate"]["verdict"] for c in doc["fva"]}
        assert {"improved", "worsened", "neutral", "too_little", "no_data"} <= verdicts


class TestConsensusRule:

    def test_priority_the_first_function_that_submitted_decides(self):
        cfg = _cfg(priority=["operations", "sales", "finance"])
        lines = ref.consensus_lines(cfg, [_sub("a", "sales", 0, 9, 1000), _sub("b", "finance", 0, 9, -500)])
        assert [(ln["pct_bp"], ln["source"]) for ln in lines] == [(1000, "sales")]

    def test_weighted_is_renormalised_over_the_functions_that_spoke(self):
        cfg = _cfg("weighted", weights={"sales": 3, "finance": 1, "operations": 0})
        assert ref.consensus_lines(cfg, [_sub("a", "sales", 0, 4, 1000), _sub("b", "finance", 0, 4, 0)])[0]["pct_bp"] == 750
        assert ref.consensus_lines(cfg, [_sub("b", "finance", 0, 4, 400)])[0]["pct_bp"] == 400
        assert ref.consensus_lines(cfg, [_sub("c", "operations", 0, 4, 400)]) == []   # no vote

    def test_caps_clamp_each_figure_before_it_counts_and_the_original_is_kept(self):
        cfg = _cfg("weighted", cap_up_bp=2000, cap_down_bp=-1000)
        line = ref.consensus_lines(cfg, [_sub("a", "sales", 0, 4, 9000), _sub("b", "finance", 0, 4, -4000)])[0]
        assert line["pct_bp"] == 500
        assert [(i["pct_bp"], i["capped"]) for i in line["inputs"]] == [(9000, True), (-4000, True)]

    def test_dates_are_cut_where_the_submissions_change(self):
        cfg = _cfg("weighted")
        lines = ref.consensus_lines(cfg, [_sub("a", "sales", 0, 9, 1000), _sub("b", "finance", 5, 14, 0)])
        assert [(ln["start"], ln["end"], ln["pct_bp"]) for ln in lines] == [(0, 4, 1000), (5, 9, 500), (10, 14, 0)]

    def test_rounding_goes_away_from_zero(self):
        assert [ref.div_round_half_away(n, 2) for n in (1, -1, 3, -3, 4)] == [1, -1, 2, -2, 2]

    def test_a_function_overlapping_itself_is_an_error_not_a_guess(self):
        with pytest.raises(ref.InconsistentSubmissions):
            ref.consensus_lines(_cfg(), [_sub("a", "sales", 0, 9, 1), _sub("b", "sales", 5, 14, 2)])


class TestForecastValueAdded:

    def _points(self):
        return [{"base": 100.0 + i, "pct_bp": 1000, "actual": 100.0 + i * 1.7 + (i % 3)} for i in range(30)]

    def test_it_agrees_with_the_engine_within_float_noise(self):
        from forecasting_core.evaluation.realized import forecast_value_added
        pts = self._points()
        mine = ref.forecast_value_added(pts)
        engine = forecast_value_added([{"base": p["base"], "adjusted": ref.adjusted_value(p["base"], p["pct_bp"]),
                                        "actual": p["actual"]} for p in pts])
        for key, want in engine.items():
            got = mine[key]
            if isinstance(want, float):
                assert math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-12), key
            else:
                assert got == want, key

    def test_a_perfect_forecast_an_adjustment_spoiled_is_worsened_not_neutral(self):
        from forecasting_core.evaluation.realized import forecast_value_added
        pts = [{"base": 100.0, "pct_bp": 5000, "actual": 100.0}] * 6
        engine = forecast_value_added([{"base": 100.0, "adjusted": 150.0, "actual": 100.0}] * 6)
        assert engine["verdict"] == "neutral", "the engine's reading, the gap this reference closes"
        assert ref.forecast_value_added(pts)["verdict"] == "worsened"
        assert ref.forecast_value_added([{"base": 100.0, "pct_bp": 0, "actual": 100.0}] * 6)["verdict"] == "neutral"

    def test_credit_needs_evidence(self):
        assert ref.forecast_value_added([])["verdict"] == "no_data"
        assert ref.forecast_value_added(self._points()[:4])["verdict"] == "too_little"

    def test_groups_come_out_best_first(self):
        pts = [{"base": 100.0, "pct_bp": 1000, "actual": 110.0, "function": "sales"},
               {"base": 100.0, "pct_bp": 1000, "actual": 90.0, "function": "finance"}]
        rows = ref.forecast_value_added_by(pts, "function")
        assert [r["function"] for r in rows] == ["sales", "finance"]


class TestEvidenceWriter:
    """The pure half of `forecast_check/consensus_evidence.py`."""

    def test_overlapping_dates_are_graded_once(self):
        from backend.forecast_check.consensus_evidence import merge_ranges
        d = date
        assert merge_ranges([(d(2026, 1, 10), d(2026, 1, 20)), (d(2026, 1, 15), d(2026, 1, 25)),
                             (d(2026, 1, 26), d(2026, 1, 30)), (d(2026, 3, 1), d(2026, 3, 2))]) == [
            (d(2026, 1, 10), d(2026, 1, 30)), (d(2026, 3, 1), d(2026, 3, 2))]

    def test_one_row_per_sku_and_day_summed_over_stores(self):
        from backend.forecast_check.consensus_evidence import evidence_points
        forecasts = {"A│N": {"2026-01-10": 4.0, "2026-01-11": 4.0}, "A│S": {"2026-01-10": 6.0},
                     "B": {"2026-01-10": 1.0}}
        actuals = {"A│N": {"2026-01-10": 5.0}, "A│S": {"2026-01-10": 7.0}, "B": {}}
        rows = evidence_points({"A": [(date(2026, 1, 10), date(2026, 1, 12)), (date(2026, 1, 11), date(2026, 1, 12))],
                                "B": [(date(2026, 1, 10), date(2026, 1, 10))]}, forecasts, actuals)
        assert rows == [{"sku": "A", "period": "2026-01-10", "base": 10.0, "actual": 12.0}]
