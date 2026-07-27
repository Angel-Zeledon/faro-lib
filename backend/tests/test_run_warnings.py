"""The validation layers must reach the user, not only the server log.

They run in WARNING mode and never abort a run. TARGET_FEATURE_LEAKAGE is why
that mattered: it yields a near-perfect accuracy on a forecast that is
worthless, and until these findings travelled with the results there was no
channel at all to tell the user their 97% was fake.
"""

import pytest

from backend.workers.runner import (
    MAX_WARNING_CODES,
    MAX_WARNING_SAMPLES,
    _collect_run_warnings,
)


class _Engine:
    """Stands in for ForecastEngine.get_run_warnings()."""

    def __init__(self, validation=None, corrections=None, raises=False):
        self._validation = validation or []
        self._corrections = corrections or []
        self._raises = raises

    def get_run_warnings(self):
        if self._raises:
            raise RuntimeError("engine blew up")
        return {"validation": self._validation, "corrections": self._corrections}


def _finding(error_id, severity="warning", message="msg", layer="leakage", **ctx):
    return {
        "error_id": error_id,
        "severity": severity,
        "message": message,
        "layer": layer,
        "context": ctx,
        "suggestions": ["do a thing", "do another", "and another", "a fourth"],
    }


class TestGrouping:
    @pytest.mark.offline
    def test_leakage_survives_to_the_payload(self):
        out = _collect_run_warnings(_Engine([_finding("TARGET_FEATURE_LEAKAGE")]))
        codes = [g["code"] for g in out["validation"]]
        assert "TARGET_FEATURE_LEAKAGE" in codes

    @pytest.mark.offline
    def test_same_code_across_skus_becomes_one_group_with_a_count(self):
        findings = [_finding("INSUFFICIENT_HISTORY", message=f"sku {i}") for i in range(7)]
        out = _collect_run_warnings(_Engine(findings))
        assert len(out["validation"]) == 1
        assert out["validation"][0]["count"] == 7

    @pytest.mark.offline
    def test_samples_are_capped_but_the_count_is_not(self):
        findings = [_finding("INSUFFICIENT_HISTORY", message=f"sku {i}") for i in range(50)]
        group = _collect_run_warnings(_Engine(findings))["validation"][0]
        assert group["count"] == 50
        assert len(group["samples"]) == MAX_WARNING_SAMPLES

    @pytest.mark.offline
    def test_suggestions_are_trimmed(self):
        group = _collect_run_warnings(_Engine([_finding("OUT_OF_RANGE")]))["validation"][0]
        assert len(group["samples"][0]["suggestions"]) == 3

    @pytest.mark.offline
    def test_context_travels_so_the_ui_can_name_numbers(self):
        group = _collect_run_warnings(
            _Engine([_finding("OUT_OF_RANGE", n_rows=12)])
        )["validation"][0]
        assert group["samples"][0]["context"]["n_rows"] == 12


class TestSeverity:
    @pytest.mark.offline
    def test_an_error_anywhere_in_the_group_outranks_a_warning(self):
        findings = [
            _finding("SKU_DATA_ERRORS", severity="warning"),
            _finding("SKU_DATA_ERRORS", severity="error"),
        ]
        assert _collect_run_warnings(_Engine(findings))["validation"][0]["severity"] == "error"

    @pytest.mark.offline
    def test_errors_are_listed_before_warnings(self):
        findings = [
            _finding("INSUFFICIENT_HISTORY", severity="warning"),
            _finding("ALL_NAN_TARGET", severity="error"),
        ]
        out = _collect_run_warnings(_Engine(findings))
        assert [g["code"] for g in out["validation"]] == ["ALL_NAN_TARGET", "INSUFFICIENT_HISTORY"]

    @pytest.mark.offline
    def test_within_a_severity_the_most_frequent_comes_first(self):
        findings = (
            [_finding("OUT_OF_RANGE") for _ in range(2)]
            + [_finding("INSUFFICIENT_HISTORY") for _ in range(9)]
        )
        out = _collect_run_warnings(_Engine(findings))
        assert out["validation"][0]["code"] == "INSUFFICIENT_HISTORY"


class TestBounds:
    @pytest.mark.offline
    def test_distinct_codes_are_capped(self):
        findings = [_finding(f"CODE_{i}") for i in range(MAX_WARNING_CODES + 15)]
        assert len(_collect_run_warnings(_Engine(findings))["validation"]) == MAX_WARNING_CODES

    @pytest.mark.offline
    def test_a_finding_with_no_error_id_is_still_reported(self):
        out = _collect_run_warnings(_Engine([{"message": "orphan", "severity": "warning"}]))
        assert out["validation"][0]["code"] == "UNKNOWN"


class TestNeverBreaksAGoodRun:
    @pytest.mark.offline
    def test_an_engine_failure_degrades_to_empty(self):
        """Reporting is an extra; it must not fail a run that produced a forecast."""
        assert _collect_run_warnings(_Engine(raises=True)) == {
            "validation": [], "corrections": [],
        }

    @pytest.mark.offline
    def test_no_findings_is_an_empty_payload_not_a_crash(self):
        assert _collect_run_warnings(_Engine()) == {"validation": [], "corrections": []}


class TestCorrections:
    @pytest.mark.offline
    def test_auto_corrections_are_reported(self):
        out = _collect_run_warnings(_Engine(corrections=[
            {"action": "fill_gaps", "description": "Filled 3 missing days"},
        ]))
        assert out["corrections"] == [
            {"action": "fill_gaps", "description": "Filled 3 missing days"},
        ]

    @pytest.mark.offline
    def test_corrections_are_capped(self):
        many = [{"action": "clip", "description": f"row {i}"} for i in range(60)]
        out = _collect_run_warnings(_Engine(corrections=many))
        assert len(out["corrections"]) == MAX_WARNING_CODES


# ── Data-prep notes ─────────────────────────────────────────────────────────
#
# These are the runner's OWN silent rewrites of the user's data. Each one used
# to live only in the server log, so a per-transaction ERP export got summed
# into daily totals — the difference between ordering 10/day and ordering 3 —
# without the user ever learning their export format was the problem.

import pandas as pd  # noqa: E402

from backend.workers.runner import (  # noqa: E402
    _apply_gap_fill,
    _apply_outlier_treatment,
    _collapse_duplicate_periods,
    _neutralize_infinities,
)


def _codes(notes):
    return [n["error_id"] for n in notes]


class TestPrepNotesAreRaised:
    @pytest.mark.offline
    def test_collapsing_transactions_is_reported(self):
        df = pd.DataFrame([
            {"sku": "A", "date": "2025-01-01", "demand": 4},
            {"sku": "A", "date": "2025-01-01", "demand": 6},
        ])
        notes: list = []
        _collapse_duplicate_periods(df, "date", "demand", "sku", notes)
        assert _codes(notes) == ["PREP_DUPLICATES_COLLAPSED"]
        assert notes[0]["context"]["n_rows"] == 2

    @pytest.mark.offline
    def test_infinities_are_reported_with_their_count(self):
        df = pd.DataFrame({"demand": [1.0, float("inf"), 3.0, float("-inf")]})
        notes: list = []
        _neutralize_infinities(df, "demand", notes)
        assert _codes(notes) == ["PREP_INFINITE_NEUTRALIZED"]
        assert notes[0]["context"]["n_rows"] == 2

    @pytest.mark.offline
    def test_skipped_gap_fill_names_the_sku_and_its_dates(self):
        """The whole point is that the user can go find the typo'd year."""
        dates = ["1900-01-01"] + [f"2025-01-{d:02d}" for d in range(1, 11)]
        df = pd.DataFrame({
            "sku": ["A"] * len(dates),
            "date": pd.to_datetime(dates),
            "demand": [10] * len(dates),
        })
        notes: list = []
        _apply_gap_fill(df, "date", "demand", "sku", "zero", notes)
        assert _codes(notes) == ["PREP_GAP_FILL_SKIPPED"]
        ctx = notes[0]["context"]
        assert ctx["sku"] == "A"
        assert ctx["date_min"] == "1900-01-01"

    @pytest.mark.offline
    def test_failed_outlier_treatment_names_the_sku_and_strategy(self):
        """The wizard still says the strategy is active — silence would lie.

        A corrupted `n_sigma` (the config is a JSONB blob) makes the sigma
        arithmetic raise, which is the branch that used to only log.
        """
        df = pd.DataFrame({
            "sku": ["A"] * 6,
            "date": pd.to_datetime([f"2025-01-{d:02d}" for d in range(1, 7)]),
            "demand": [1.0, 2.0, 3.0, 4.0, 5.0, 900.0],
        })
        notes: list = []
        _apply_outlier_treatment(
            df, "date", "demand", "sku",
            {"strategy": "winsorize_sigma", "n_sigma": "three"}, notes,
        )
        assert _codes(notes) == ["PREP_OUTLIER_TREATMENT_FAILED"]
        assert notes[0]["context"] == {"sku": "A", "strategy": "winsorize_sigma"}

    @pytest.mark.offline
    def test_a_working_outlier_strategy_reports_nothing(self):
        """Contrast for the test above: the same data with a valid config."""
        df = pd.DataFrame({
            "sku": ["A"] * 6,
            "date": pd.to_datetime([f"2025-01-{d:02d}" for d in range(1, 7)]),
            "demand": [1.0, 2.0, 3.0, 4.0, 5.0, 900.0],
        })
        notes: list = []
        out = _apply_outlier_treatment(
            df, "date", "demand", "sku",
            {"strategy": "winsorize_sigma", "n_sigma": 2.0}, notes,
        )
        assert notes == []
        assert out["demand"].max() < 900.0, "the outlier should have been clipped"


class TestPrepNotesStaySilentOnHealthyData:
    @pytest.mark.offline
    def test_a_clean_daily_file_raises_nothing(self):
        df = pd.DataFrame({
            "sku": ["A"] * 6,
            "date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03",
                                    "2025-01-04", "2025-01-05", "2025-01-06"]),
            "demand": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        })
        notes: list = []
        out = _neutralize_infinities(df, "demand", notes)
        _collapse_duplicate_periods(out, "date", "demand", "sku", notes)
        _apply_gap_fill(out, "date", "demand", "sku", "zero", notes)
        assert notes == []

    @pytest.mark.offline
    def test_notes_are_optional_so_the_helpers_stay_pure(self):
        """Existing callers pass no accumulator; that must keep working."""
        df = pd.DataFrame([
            {"sku": "A", "date": "2025-01-01", "demand": 4},
            {"sku": "A", "date": "2025-01-01", "demand": 6},
        ])
        out = _collapse_duplicate_periods(df, "date", "demand", "sku")
        assert len(out) == 1


class TestPrepNotesReachThePayload:
    @pytest.mark.offline
    def test_prep_notes_are_grouped_alongside_engine_findings(self):
        prep = [
            {"error_id": "PREP_DUPLICATES_COLLAPSED", "severity": "warning",
             "layer": "data_prep", "message": "", "context": {"n_rows": 180},
             "suggestions": []},
        ]
        out = _collect_run_warnings(
            _Engine([_finding("TARGET_FEATURE_LEAKAGE", severity="error")]), prep,
        )
        codes = [g["code"] for g in out["validation"]]
        assert "PREP_DUPLICATES_COLLAPSED" in codes
        assert "TARGET_FEATURE_LEAKAGE" in codes

    @pytest.mark.offline
    def test_prep_notes_survive_an_engine_that_reports_nothing(self):
        """A broken engine getter must not swallow the runner's own findings."""
        prep = [{"error_id": "PREP_GAP_FILL_SKIPPED", "severity": "warning",
                 "layer": "data_prep", "message": "", "context": {"sku": "A"},
                 "suggestions": []}]
        out = _collect_run_warnings(_Engine(raises=True), prep)
        assert [g["code"] for g in out["validation"]] == ["PREP_GAP_FILL_SKIPPED"]
