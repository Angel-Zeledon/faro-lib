"""Pure rules of file-drop sources, and the SigV4 signer against AWS's own vector.
No database, no network."""

from datetime import datetime, timezone

import pytest

from backend.file_drop import core
from backend.file_drop.transports import _reject_entities, sign_v4
from backend.errors import AppError


class TestSelection:
    def test_patterns_default_to_csv(self):
        assert core.parse_patterns(None) == ["*.csv"]
        assert core.parse_patterns(" , ") == ["*.csv"]
        assert core.parse_patterns("a*.csv, b.xlsx") == ["a*.csv", "b.xlsx"]

    @pytest.mark.parametrize("raw", ["../x.csv", "a/b.csv", "..", "c:\\x.csv", "x" * 201])
    def test_a_pattern_that_could_leave_the_directory_is_refused(self, raw):
        assert core.pattern_error(raw) is not None

    def test_selects_by_pattern_and_suffix_case_insensitively(self):
        pats = core.parse_patterns("sales_*.csv")
        assert core.selects(pats, "SALES_2026.CSV")
        assert not core.selects(pats, "stock_2026.csv")
        assert not core.selects(core.parse_patterns("*"), "notes.txt")

    def test_our_own_notes_hidden_files_and_paths_are_never_read(self):
        pats = core.parse_patterns("*")
        assert not core.selects(pats, "sales.csv.reason.txt")
        assert not core.selects(pats, ".sales.csv")
        assert not core.selects(pats, "processed/sales.csv")


class TestStability:
    def test_first_look_never_qualifies(self):
        assert not core.is_stable(None, 100, 1000.0, now=9999.0, min_age_seconds=0)

    def test_a_growing_file_is_not_finished(self):
        assert not core.is_stable([100, 1000.0], 250, 1000.0, now=9999.0, min_age_seconds=0)

    def test_a_touched_file_is_not_finished(self):
        assert not core.is_stable([100, 1000.0], 100, 1005.0, now=9999.0, min_age_seconds=0)

    def test_two_agreeing_looks_and_enough_age_qualify(self):
        assert core.is_stable([100, 1000.0], 100, 1000.0, now=1100.0, min_age_seconds=60)

    def test_agreeing_looks_but_too_young_do_not(self):
        assert not core.is_stable([100, 1000.0], 100, 1000.0, now=1030.0, min_age_seconds=60)


class TestJudge:
    existing = core.Snapshot(1000, ("date", "sku", "qty"))

    def test_empty_file_is_held_even_without_a_previous_file(self):
        assert core.judge(None, core.Snapshot(0, ("a",))).code == "file_drop_file_empty"

    def test_first_file_is_accepted(self):
        assert core.judge(None, core.Snapshot(10, ("a",))).ok

    def test_changed_columns_are_held_with_what_changed(self):
        v = core.judge(self.existing, core.Snapshot(1000, ("date", "sku", "units")))
        assert v.code == "file_drop_columns_changed"
        assert v.params == {"missing": ["qty"], "added": ["units"]}

    def test_column_order_and_case_do_not_matter(self):
        assert core.judge(self.existing, core.Snapshot(900, ("QTY", " Date", "sku"))).ok

    def test_far_fewer_rows_is_held(self):
        v = core.judge(self.existing, core.Snapshot(400, ("date", "sku", "qty")))
        assert v.code == "file_drop_rows_dropped" and v.params["previous_rows"] == 1000

    def test_exactly_half_is_accepted_and_growth_too(self):
        assert core.judge(self.existing, core.Snapshot(500, ("date", "sku", "qty"))).ok
        assert core.judge(self.existing, core.Snapshot(5000, ("date", "sku", "qty"))).ok

    def test_unknown_row_count_is_not_held_on_that_ground(self):
        assert core.judge(self.existing, core.Snapshot(None, ("date", "sku", "qty"))).ok


class TestLedgerRules:
    def test_hash_is_content_not_name(self):
        assert core.content_hash(b"a") == core.content_hash(b"a") != core.content_hash(b"b")

    def test_only_failures_are_retried_and_only_a_few_times(self):
        assert core.can_retry("failed", 1) and core.can_retry("applying", 2)
        assert not core.can_retry("failed", core.MAX_ATTEMPTS)
        for decided in ("applied", "held", "rejected"):
            assert not core.can_retry(decided, 1)

    def test_jitter_stays_within_twenty_percent(self):
        assert core.jittered_delay_seconds(10, 0.0) == pytest.approx(480)
        assert core.jittered_delay_seconds(10, 0.999999) == pytest.approx(720, rel=1e-3)

    def test_sidecar_names_the_reason_and_how_to_recover(self):
        text = core.sidecar_text("file_drop_columns_changed", {"missing": ["qty"]},
                                 datetime(2026, 10, 19, tzinfo=timezone.utc))
        assert "file_drop_columns_changed" in text and "qty" in text and "drop it again" in text


class TestSigV4:
    def test_matches_the_published_aws_example(self):
        # "GET Object with a Range header", Signature Calculations for the
        # Authorization Header (AWS S3 API reference).
        headers = sign_v4(
            "GET", "https://examplebucket.s3.amazonaws.com/test.txt", region="us-east-1",
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            headers={"Range": "bytes=0-9"}, now=datetime(2013, 5, 24, tzinfo=timezone.utc))
        assert headers["authorization"].endswith(
            "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
        assert "host" not in headers

    def test_a_listing_with_an_entity_declaration_is_refused(self):
        with pytest.raises(AppError):
            _reject_entities(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')
        _reject_entities(b'<?xml version="1.0"?><ListBucketResult/>')
