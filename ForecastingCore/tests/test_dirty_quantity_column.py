"""A quantity column with a few dirty cells is still the quantity column.

The bug: `_detect_target` searched `select_dtypes(include=np.number)`, so 66
stray cells out of 850 ("3 unidades", a blank) made pandas read the column as
text and the search found NOTHING. The gate then emitted `no_numeric_column`
— fatal and flagged non-remediable, "this file cannot generate a forecast" —
while the mapping screen right above it showed that very column selected as
Demanda. The `non_numeric_target` remediation, which offers to strip, zero or
drop those cells, existed the whole time and never got the chance to run.
"""

from datetime import date, timedelta

import pandas as pd

from forecasting_core.data import gate
from forecasting_core.data.profiler import DataProfiler


def _frame(dirty_every: int = 13, days: int = 90) -> pd.DataFrame:
    """A clean daily history with one unreadable quantity every `dirty_every`."""
    start = date(2025, 1, 1)
    rows = []
    for i in range(days):
        day = (start + timedelta(days=i)).isoformat()
        for sku in ("FAR-01", "FAR-02"):
            qty = f"{i % 9} unidades" if i % dirty_every == 0 else str(10 + i % 7)
            rows.append({"fecha": day, "sku": sku, "ventas": qty})
    return pd.DataFrame(rows)


class TestTheColumnIsFound:
    def test_a_mostly_numeric_text_column_is_detected_as_the_target(self):
        df = _frame()
        assert not pd.api.types.is_numeric_dtype(df["ventas"])
        assert DataProfiler().profile(df)["recommended"]["target"] == "ventas"

    def test_a_column_that_is_mostly_text_is_not_claimed(self):
        """The threshold has to mean something: a category column is not demand."""
        df = _frame()
        df["ventas"] = ["categoria-" + str(i % 3) for i in range(len(df))]
        assert DataProfiler().profile(df)["recommended"]["target"] != "ventas"

    def test_a_date_column_is_never_offered_as_a_quantity(self):
        df = _frame()
        del df["ventas"]
        assert DataProfiler().profile(df)["recommended"]["target"] is None


class TestWhatTheUserIsOffered:
    def test_the_file_is_fixable_not_a_dead_end(self):
        df = _frame()
        rec = DataProfiler().profile(df)["recommended"]
        result = gate.evaluate(df, rec["date"], rec["target"], rec["group"], {}, {})

        assert "no_numeric_column" not in result["blocking_fatal"], (
            "a 92%-numeric column was reported as no quantity column at all")
        assert "non_numeric_target" in result["blocking_fixable"]

        issue = next(i for i in result["issues"] if i["type"] == "non_numeric_target")
        assert issue["remediable"] is True
        # Every option must be actionable, and the examples must be the real
        # offending values — "revisa el archivo" is not a fix.
        codes = {o["code"] for o in issue["remediations"]}
        assert codes == {"non_numeric_strip_symbols", "non_numeric_as_zero",
                         "non_numeric_drop_rows"}
        assert any("unidades" in str(e) for e in issue["params"]["examples"])

    def test_a_file_with_no_numbers_at_all_still_blocks_fatally(self):
        """The narrow fix must not open the gate for a file that truly has none."""
        df = _frame()
        df["ventas"] = "no reportado"
        rec = DataProfiler().profile(df)["recommended"]
        result = gate.evaluate(df, rec["date"], rec["target"], rec["group"], {}, {})
        assert "no_numeric_column" in result["blocking_fatal"]
