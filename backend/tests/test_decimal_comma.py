"""
A quantity written the Latin American way must be read, not crashed on.

Reproduced end to end in the browser: a CSV of `fecha;producto;cantidad` with
`10,5` was parsed into the right columns, the upload screen showed no warning at
all and offered the button "Esto se ve bien, continuar →", the job trained for
roughly two minutes, and then the UI displayed, inside a Spanish sentence:

    El cálculo falló: could not convert string to float: '10,5'

Every guard on the way in passed. The column was not empty, it was not the word
"diez", the `;` separator was detected correctly. Nothing was wrong except that
half the continent writes ten and a half as `10,5`.
"""

import pandas as pd
import pytest

from backend.workers.runner import _coerce_decimal_comma


def _frame(values, col="cantidad"):
    return pd.DataFrame({
        "fecha": pd.date_range("2026-01-01", periods=len(values), freq="D"),
        "producto": "A",
        col: values,
    })


class TestItReadsWhatThePersonMeant:

    def test_plain_decimal_comma(self):
        out = _coerce_decimal_comma(_frame(["10,5", "3,25", "0,75"]), "cantidad")
        assert out["cantidad"].tolist() == [10.5, 3.25, 0.75]

    def test_dot_thousands_with_decimal_comma(self):
        out = _coerce_decimal_comma(_frame(["1.234,56", "9.876,10"]), "cantidad")
        assert out["cantidad"].tolist() == [1234.56, 9876.10]

    def test_negatives_survive(self):
        out = _coerce_decimal_comma(_frame(["-4,5", "10,0"]), "cantidad")
        assert out["cantidad"].tolist() == [-4.5, 10.0]

    def test_whitespace_is_tolerated(self):
        out = _coerce_decimal_comma(_frame([" 10,5 ", "2,5"]), "cantidad")
        assert out["cantidad"].tolist() == [10.5, 2.5]

    def test_the_result_is_numeric_not_text(self):
        out = _coerce_decimal_comma(_frame(["10,5", "2,5"]), "cantidad")
        assert pd.api.types.is_numeric_dtype(out["cantidad"])

    def test_it_reports_what_it_did(self):
        """Silently reinterpreting someone's numbers is not acceptable."""
        notes = []
        _coerce_decimal_comma(_frame(["10,5", "2,5"]), "cantidad", notes=notes)
        assert notes and notes[0]["error_id"] == "PREP_DECIMAL_COMMA_PARSED"
        assert notes[0]["context"]["n_rows"] == 2
        assert notes[0]["context"]["column"] == "cantidad"


class TestItRefusesToGuess:
    """Where the comma is genuinely ambiguous, doing nothing is the right move."""

    def test_thousands_separator_is_left_alone(self):
        """
        `1,234` is 1234 in English and 1.234 in Spanish. Picking wrong changes
        the quantity by a factor of a thousand — much worse than the honest
        failure this whole function exists to prevent.
        """
        frame = _frame(["1,234", "5,678"])
        out = _coerce_decimal_comma(frame, "cantidad")
        assert out["cantidad"].tolist() == ["1,234", "5,678"]

    def test_a_mixed_column_is_left_alone(self):
        out = _coerce_decimal_comma(_frame(["10,5", "1,234", "7,25"]), "cantidad")
        assert out["cantidad"].iloc[0] == "10,5", "no conversion should have happened"

    def test_a_column_with_real_text_is_left_alone(self):
        """That case is already rejected at upload with a row-by-row message."""
        out = _coerce_decimal_comma(_frame(["diez", "once"]), "cantidad")
        assert out["cantidad"].tolist() == ["diez", "once"]

    def test_ambiguity_produces_no_note(self):
        notes = []
        _coerce_decimal_comma(_frame(["1,234"]), "cantidad", notes=notes)
        assert notes == []


class TestItDoesNotDisturbHealthyData:

    def test_an_already_numeric_column_is_returned_unchanged(self):
        frame = _frame([10.5, 2.5])
        out = _coerce_decimal_comma(frame, "cantidad")
        assert out is frame

    def test_english_decimals_written_as_text_are_left_alone(self):
        """`10.5` is already unambiguous; pandas handles it downstream."""
        out = _coerce_decimal_comma(_frame(["10.5", "2.5"]), "cantidad")
        assert out["cantidad"].tolist() == ["10.5", "2.5"]

    def test_a_missing_column_is_not_an_error(self):
        frame = _frame([1.0, 2.0])
        assert _coerce_decimal_comma(frame, "ghost") is frame

    def test_an_empty_column_is_not_an_error(self):
        frame = _frame([None, None])
        assert _coerce_decimal_comma(frame, "cantidad") is frame


class TestItRunsBeforeTheInfinityGuard:
    """Order matters: the infinity guard coerces with pd.to_numeric, which turns
    every "10,5" into NaN. Running it first would empty the quantities instead
    of reading them — a silent zeroing rather than a visible crash."""

    def test_the_pair_in_pipeline_order_preserves_the_values(self):
        from backend.workers.runner import _neutralize_infinities

        frame = _frame(["10,5", "3,25"])
        frame = _coerce_decimal_comma(frame, "cantidad")
        frame = _neutralize_infinities(frame, "cantidad")
        assert frame["cantidad"].tolist() == [10.5, 3.25]

    def test_the_reverse_order_would_have_destroyed_them(self):
        """Stated explicitly so the ordering is not "fixed" later by accident."""
        from backend.workers.runner import _neutralize_infinities

        frame = _neutralize_infinities(_frame(["10,5", "3,25"]), "cantidad")
        # _neutralize_infinities leaves the text in place, but any later
        # to_numeric on it yields NaN — which is why the coercion must come
        # first, while the original strings are still intact.
        assert pd.to_numeric(frame["cantidad"], errors="coerce").isna().all()
