"""
A warehouse must not disappear between the upload and the model.

A series is a (SKU, warehouse) pair whenever the session maps a store column —
which the integrations path does automatically for any tenant whose ERP reports
one. Two prep steps grouped by the SKU alone, and on that data neither one
"collapsed duplicates" or "filled gaps": they deleted branches.

Measured before the fix:

    in                                   out
    2026-01-05, SKU-A, 10.0, Norte       2026-01-05, SKU-A, 15.0, Norte
    2026-01-05, SKU-A,  5.0, Sur         2026-01-06, SKU-A, 16.0, Norte
    2026-01-06, SKU-A, 12.0, Norte
    2026-01-06, SKU-A,  4.0, Sur

Sur is gone. It never gets a forecast, never gets a reorder point, and its stock
sits without a signal forever — which on the semáforo is indistinguishable from
being well stocked. Norte is left carrying the whole network's demand and gets
over-ordered by exactly what the deleted branches sold. Nothing failed, nothing
was logged, and both numbers look ordinary.
"""

import pandas as pd
import pytest

from backend.workers.runner import (
    _apply_gap_fill, _collapse_duplicate_periods, _group_cols, _primary_group_col,
)


CANONICAL = {"group_keys": ["producto", "bodega"]}


def _two_branches():
    return pd.DataFrame({
        "fecha": pd.to_datetime(["2026-01-05", "2026-01-05",
                                 "2026-01-06", "2026-01-06"]),
        "producto": ["SKU-A"] * 4,
        "bodega": ["Norte", "Sur", "Norte", "Sur"],
        "cantidad": [10.0, 5.0, 12.0, 4.0],
    })


class TestGroupCols:

    def test_returns_every_key_not_just_the_first(self):
        assert _group_cols(CANONICAL) == ["producto", "bodega"]

    def test_the_primary_helper_still_returns_one(self):
        """Other call sites legitimately want only the SKU; both must exist."""
        assert _primary_group_col(CANONICAL) == "producto"

    def test_single_key_config(self):
        assert _group_cols({"group_keys": ["sku"]}) == ["sku"]

    def test_legacy_group_key(self):
        assert _group_cols({"group": "sku"}) == ["sku"]

    def test_no_grouping_at_all(self):
        assert _group_cols({}) == []


class TestCollapseKeepsEveryWarehouse:

    def test_no_branch_is_deleted(self):
        out = _collapse_duplicate_periods(_two_branches(), "fecha", "cantidad",
                                          _group_cols(CANONICAL))
        assert set(out["bodega"]) == {"Norte", "Sur"}, (
            f"a warehouse was deleted: {sorted(set(out['bodega']))}"
        )

    def test_each_branch_keeps_its_own_demand(self):
        out = _collapse_duplicate_periods(_two_branches(), "fecha", "cantidad",
                                          _group_cols(CANONICAL))
        norte = out[out["bodega"] == "Norte"]["cantidad"].tolist()
        sur = out[out["bodega"] == "Sur"]["cantidad"].tolist()
        assert norte == [10.0, 12.0]
        assert sur == [5.0, 4.0]

    def test_the_network_total_is_preserved(self):
        frame = _two_branches()
        out = _collapse_duplicate_periods(frame, "fecha", "cantidad",
                                          _group_cols(CANONICAL))
        assert out["cantidad"].sum() == frame["cantidad"].sum()

    def test_grouping_by_the_sku_alone_is_what_deleted_them(self):
        """The old behaviour, pinned so the regression is unmistakable."""
        out = _collapse_duplicate_periods(_two_branches(), "fecha", "cantidad",
                                          ["producto"])
        assert set(out["bodega"]) == {"Norte"}
        assert out["cantidad"].tolist() == [15.0, 16.0]

    def test_real_transactions_within_one_branch_still_collapse(self):
        """The feature must keep working: one row per SALE becomes one per day."""
        frame = pd.DataFrame({
            "fecha": pd.to_datetime(["2026-01-05"] * 3),
            "producto": ["SKU-A"] * 3,
            "bodega": ["Norte"] * 3,
            "cantidad": [4.0, 3.0, 3.0],
        })
        out = _collapse_duplicate_periods(frame, "fecha", "cantidad",
                                          _group_cols(CANONICAL))
        assert len(out) == 1
        assert out["cantidad"].iloc[0] == 10.0

    def test_it_reports_what_it_collapsed(self):
        notes = []
        frame = pd.DataFrame({
            "fecha": pd.to_datetime(["2026-01-05"] * 3),
            "producto": ["SKU-A"] * 3, "bodega": ["Norte"] * 3,
            "cantidad": [4.0, 3.0, 3.0],
        })
        _collapse_duplicate_periods(frame, "fecha", "cantidad",
                                    _group_cols(CANONICAL), notes=notes)
        assert notes and notes[0]["error_id"] == "PREP_DUPLICATES_COLLAPSED"

    def test_a_single_branch_dataset_is_untouched(self):
        frame = pd.DataFrame({
            "fecha": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "producto": ["SKU-A"] * 2, "bodega": ["Norte"] * 2,
            "cantidad": [10.0, 12.0],
        })
        out = _collapse_duplicate_periods(frame, "fecha", "cantidad",
                                          _group_cols(CANONICAL))
        pd.testing.assert_frame_equal(out, frame)


class TestGapFillKeepsEveryWarehouse:
    """Worse here than merely wrong: the per-group `drop_duplicates(subset=[date])`
    threw away one branch's row for every date two branches both sold on."""

    def _gappy(self):
        """Two branches on a DAILY cadence, each missing 2026-01-03.

        The cadence has to be established by the surrounding days: the helper
        infers frequency from the median gap of the whole frame, so a fixture
        holding only two distinct dates three days apart is a 3-day series with
        no gap at all — not the case under test.
        """
        dates = ["2026-01-01", "2026-01-02", "2026-01-04", "2026-01-05"]
        rows = []
        for branch, base in (("Norte", 10.0), ("Sur", 5.0)):
            for i, d in enumerate(dates):
                rows.append({"fecha": pd.Timestamp(d), "producto": "SKU-A",
                             "bodega": branch, "cantidad": base + i})
        return pd.DataFrame(rows)

    def test_both_branches_survive(self):
        out = _apply_gap_fill(self._gappy(), "fecha", "cantidad",
                              _group_cols(CANONICAL), "zero")
        assert set(out["bodega"]) == {"Norte", "Sur"}

    def test_each_branch_is_filled_independently(self):
        out = _apply_gap_fill(self._gappy(), "fecha", "cantidad",
                              _group_cols(CANONICAL), "zero")
        for branch in ("Norte", "Sur"):
            dates = sorted(out[out["bodega"] == branch]["fecha"])
            assert len(dates) == 5, (
                f"{branch} kept {len(dates)} buckets; the 01-03 gap was not filled"
            )
            assert pd.Timestamp("2026-01-03") in dates

    def test_the_filled_rows_carry_their_series_identity(self):
        """A reindexed row with no warehouse belongs to nothing."""
        out = _apply_gap_fill(self._gappy(), "fecha", "cantidad",
                              _group_cols(CANONICAL), "zero")
        assert out["bodega"].notna().all()
        assert out["producto"].notna().all()

    def test_original_values_are_preserved(self):
        out = _apply_gap_fill(self._gappy(), "fecha", "cantidad",
                              _group_cols(CANONICAL), "zero")
        norte = out[(out["bodega"] == "Norte")
                    & (out["fecha"] == pd.Timestamp("2026-01-04"))]
        assert norte["cantidad"].iloc[0] == 12.0

    def test_leave_strategy_is_still_a_no_op(self):
        frame = self._gappy()
        assert _apply_gap_fill(frame, "fecha", "cantidad",
                               _group_cols(CANONICAL), "leave") is frame
