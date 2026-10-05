"""A file that cannot train must be called untrainable at inspection time.

The defect: a one-row upload passed the mapping screen with no warning at all,
under a button reading "this looks good", and only failed two minutes later
with `no_models_trained`. The profiler already had every number it needed.

The rule under test lives in `DataProfiler._check_trainability`: no product can
reach `min_history` periods, so `DataQualityChecker.filter_valid_skus` drops
every series and the run has nothing left to train.

The second half of these tests is the half that keeps the rule honest — a
catalogue that merely CONTAINS a short product is normal and must not be
blocked.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from forecasting_core.data.profiler import DataProfiler

MIN = DataProfiler.MIN_TRAINABLE_PERIODS


def _rows(sku: str, n: int, start: date = date(2025, 1, 1), qty: int = 10) -> list[dict]:
    return [
        {"producto": sku,
         "fecha": (start + timedelta(days=i)).isoformat(),
         "cantidad": qty + (i % 5)}
        for i in range(n)
    ]


def _profile(rows: list[dict]) -> dict:
    return DataProfiler().profile(pd.DataFrame(rows))


def _blocking_issues(profile: dict) -> list[dict]:
    return [i for i in profile["data_quality"]["issues"] if i.get("blocking")]


class TestAFileThatCannotTrainIsBlocked:
    def test_a_single_row_upload_is_reported_as_untrainable(self):
        """The exact file from the defect report: one header, one row."""
        profile = _profile([{"fecha": "2026-01-01", "producto": "A", "cantidad": 5}])

        assert profile["data_quality"]["blocking"] is True
        issues = _blocking_issues(profile)
        assert [i["type"] for i in issues] == ["no_trainable_history"]
        assert issues[0]["longest_history"] == 1
        assert issues[0]["min_required"] == MIN

    def test_a_single_row_is_caught_even_though_its_date_column_is_undetectable(self):
        """Why the check cannot sit behind the date/target guard.

        One date value never passes `_is_date` (it needs more than one distinct
        parsed value), so `recommended["date"]` is None and the whole
        `_check_quality` block — including `short_history` — is skipped. That is
        precisely why nothing fired for this file before.
        """
        profile = _profile([{"fecha": "2026-01-01", "producto": "A", "cantidad": 5}])

        assert profile["recommended"]["date"] is None, (
            "if date detection starts working on 1-row files, this test's premise "
            "is stale — but the blocking assert below must still hold"
        )
        assert profile["data_quality"]["blocking"] is True

    def test_a_catalogue_where_every_product_is_short_is_blocked(self):
        """Plenty of rows, but spread so thin that no series clears min_history."""
        rows: list[dict] = []
        for i in range(60):
            rows.extend(_rows(f"SKU-{i:03d}", n=MIN - 1))
        profile = _profile(rows)

        assert len(rows) > MIN, "the row-count bound must not be what catches this"
        assert profile["data_quality"]["blocking"] is True
        assert _blocking_issues(profile)[0]["longest_history"] == MIN - 1

    def test_an_empty_file_is_blocked(self):
        profile = DataProfiler().profile(
            pd.DataFrame(columns=["fecha", "producto", "cantidad"])
        )
        assert profile["data_quality"]["blocking"] is True

    def test_the_blocking_issue_travels_with_the_other_findings(self):
        """Reuses the existing channel — no second warning surface to render."""
        profile = _profile([{"fecha": "2026-01-01", "producto": "A", "cantidad": 5}])
        issues = profile["data_quality"]["issues"]

        assert issues, "the panel reads data_quality.issues; an empty list shows nothing"
        assert issues[0]["severity"] == "error"
        assert issues[0]["message"], "unknown codes fall back to this text"


class TestAFileThatCanTrainIsNotBlocked:
    def test_a_catalogue_with_one_brand_new_product_still_trains(self):
        """The case the rule must never break: three long series, one 5-day newcomer."""
        rows: list[dict] = []
        for sku in ("SKU-A", "SKU-B", "SKU-C"):
            rows.extend(_rows(sku, n=120))
        rows.extend(_rows("SKU-NEW", n=5, start=date(2025, 6, 1)))
        profile = _profile(rows)

        assert profile["data_quality"]["blocking"] is False
        assert _blocking_issues(profile) == []

    def test_the_short_newcomer_is_still_reported_as_a_soft_warning(self):
        """"Worse" and "impossible" are different messages, and both must exist."""
        rows: list[dict] = []
        for sku in ("SKU-A", "SKU-B", "SKU-C"):
            rows.extend(_rows(sku, n=120))
        rows.extend(_rows("SKU-NEW", n=5, start=date(2025, 6, 1)))
        issues = _profile(rows)["data_quality"]["issues"]

        short = [i for i in issues if i["type"] == "short_history"]
        assert short, "the newcomer must still be mentioned"
        assert not short[0].get("blocking"), "a soft warning must stay overrulable"

    def test_exactly_min_history_periods_is_enough(self):
        """The boundary the engine itself uses is `>=`, not `>`."""
        profile = _profile(_rows("SKU-A", n=MIN))
        assert profile["data_quality"]["blocking"] is False

    def test_one_period_short_of_min_history_is_blocked(self):
        """The other side of the same boundary — proves the assert above can fail."""
        profile = _profile(_rows("SKU-A", n=MIN - 1))
        assert profile["data_quality"]["blocking"] is True

    def test_a_long_series_hiding_past_the_fifty_group_sample_is_not_blocked(self):
        """`_check_quality` samples 50 groups; the blocking rule must not.

        One trainable product anywhere in the catalogue makes the file
        trainable. Stopping at the 50th group would reject a good file.
        """
        rows: list[dict] = []
        for i in range(80):
            rows.extend(_rows(f"SKU-{i:03d}", n=3))
        rows.extend(_rows("SKU-LONG", n=200))
        profile = _profile(rows)

        assert profile["recommended"]["group"] == "producto"
        assert profile["data_quality"]["blocking"] is False

    def test_duplicate_rows_do_not_inflate_the_history_count(self):
        """Rows are not periods: the pipeline sums same-date rows into one bucket.

        Counting rows here would call a file trainable that has only 10 real
        dates, and hand the user back the two-minute wait.
        """
        one_sku = _rows("SKU-A", n=10)
        profile = _profile(one_sku + one_sku + one_sku)

        assert len(one_sku * 3) > MIN
        assert profile["data_quality"]["blocking"] is True
        assert _blocking_issues(profile)[0]["longest_history"] == 10


class TestTheRuleMatchesTheEngineItAnticipates:
    def test_the_threshold_is_the_engines_min_history_default(self):
        """If either default moves, the screen starts lying about the other."""
        from forecasting_core.config.config import TrainingConfig

        assert DataProfiler.MIN_TRAINABLE_PERIODS == TrainingConfig().min_history

    @pytest.mark.parametrize("n_periods", [1, 5, MIN - 1])
    def test_a_file_the_profiler_blocks_is_a_file_the_checker_empties(self, n_periods):
        """End of the argument: what the screen claims, the engine then does."""
        from forecasting_core.data.quality import DataQualityChecker

        df = pd.DataFrame(_rows("SKU-A", n=n_periods))
        assert _profile(_rows("SKU-A", n=n_periods))["data_quality"]["blocking"] is True

        checker = DataQualityChecker(
            dt_col="fecha", target_col="cantidad", group_col="producto",
            min_history=MIN,
        )
        surviving = checker.filter_valid_skus(df, checker.check(df))
        assert len(surviving) == 0, "no series survives — training has nothing to do"
