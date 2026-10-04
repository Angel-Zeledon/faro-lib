"""`dd/mm/yyyy` — the Latin American convention — must survive the load.

The bug this file exists for: pandas defaults to month-first, so a plain
`13/03/2025` export lost EVERY row whose day is past the 12th (reported to the
user as "unparseable or missing dates") and quietly moved the rest to another
month, inventing future dates and duplicate date+SKU pairs along the way.
Training then died on the first row month-first cannot explain, and the buyer
read a pandas message suggesting `format='ISO8601'`.

The gate's ambiguity detector had already PROVEN the order — it returns nothing
precisely because a day > 12 rules month-first out — but nothing acted on the
verdict. So the worst case was an UNambiguous file, while an ambiguous one got a
careful question. These tests pin both halves.
"""

from datetime import date, timedelta

import pandas as pd

from forecasting_core.data import gate
from forecasting_core.data.loader import DataLoader
from forecasting_core.data.profiler import DataProfiler


def _day_first_frame(days: int = 40) -> pd.DataFrame:
    """A history that only day-first can explain: it spans days past the 12th."""
    start = date(2025, 3, 1)
    return pd.DataFrame([
        {"Fecha": (start + timedelta(days=i)).strftime("%d/%m/%Y"),
         "Codigo": "ABA-100",
         "Unidades": 10 + (i % 7)}
        for i in range(days)
    ])


class TestTheOrderIsDetermined:
    def test_a_day_past_the_twelfth_settles_it_as_day_first(self):
        df = _day_first_frame()
        assert gate.detect_determined_date_order(df, "Fecha") == gate.DATE_ORDER_DAY_FIRST

    def test_a_month_first_file_is_recognised_and_left_alone(self):
        df = pd.DataFrame({"Fecha": ["01/13/2026", "02/28/2026", "03/05/2026"],
                           "Codigo": ["A"] * 3, "Unidades": [1, 2, 3]})
        assert gate.detect_determined_date_order(df, "Fecha") == gate.DATE_ORDER_MONTH_FIRST
        # Left as text: pandas' own default already reads this one correctly.
        assert not pd.api.types.is_datetime64_any_dtype(DataLoader().load_df(df)["Fecha"])

    def test_an_ambiguous_column_stays_a_question_for_the_user(self):
        """Both readings possible → no verdict here, and the gate must still ask.

        This is the case the product is right to interrupt for, and the fix must
        not swallow it: guessing day-first for everyone would misdate a genuine
        month-first export with no way for the buyer to notice.
        """
        df = pd.DataFrame({"Fecha": ["01/02/2026", "03/04/2026", "05/06/2026"],
                           "Codigo": ["A"] * 3, "Unidades": [1, 2, 3]})
        assert gate.detect_determined_date_order(df, "Fecha") is None
        loaded = DataLoader().load_df(df)
        assert not pd.api.types.is_datetime64_any_dtype(loaded["Fecha"])
        assert len(gate.detect_ambiguous_date_format(loaded, "Fecha")) == 1

    def test_contradictory_components_yield_no_verdict(self):
        """Values > 12 on BOTH sides: no reading works, and inventing one hides it."""
        df = pd.DataFrame({"Fecha": ["13/03/2025", "03/25/2025", "04/04/2025"],
                           "Codigo": ["A"] * 3, "Unidades": [1, 2, 3]})
        assert gate.detect_determined_date_order(df, "Fecha") is None


class TestTheLoaderAppliesTheVerdict:
    def test_no_row_is_lost_and_the_range_is_the_real_one(self):
        df = DataLoader().load_df(_day_first_frame(40))
        assert pd.api.types.is_datetime64_any_dtype(df["Fecha"])
        # Month-first dropped every day past the 12th; here nothing is dropped.
        assert int(df["Fecha"].isna().sum()) == 0
        assert df["Fecha"].min() == pd.Timestamp("2025-03-01")
        assert df["Fecha"].max() == pd.Timestamp("2025-04-09")
        # Each calendar day appears once. Month-first collapsed distinct days
        # onto each other and the gate then reported invented duplicates.
        assert df["Fecha"].nunique() == 40

    def test_no_date_lands_in_the_future(self):
        """Swapping day and month pushed rows past today — 84 of them, once."""
        df = DataLoader().load_df(_day_first_frame(120))
        assert int((df["Fecha"] > pd.Timestamp("2025-12-31")).sum()) == 0

    def test_an_iso_column_is_untouched(self):
        iso = pd.DataFrame({"fecha": ["2024-07-01", "2024-07-02"],
                            "sku": ["A", "A"], "ventas": [1, 2]})
        loaded = DataLoader().load_df(iso)
        assert list(loaded["fecha"]) == ["2024-07-01", "2024-07-02"]

    def test_a_text_column_that_is_not_a_date_survives(self):
        codes = pd.DataFrame({"Codigo": ["12/34/5678", "no-es-fecha", "ABA-1"],
                              "Unidades": [1, 2, 3]})
        loaded = DataLoader().load_df(codes)
        assert list(loaded["Codigo"]) == ["12/34/5678", "no-es-fecha", "ABA-1"]


class TestWhatTheUserIsToldAfterwards:
    def test_the_profiler_reports_the_real_range_and_no_false_warnings(self):
        df = DataLoader().load_df(_day_first_frame(40))
        profile = DataProfiler().profile(df)
        stats = profile["stats"]
        assert stats["date_min"] == "2025-03-01"
        assert stats["date_max"] == "2025-04-09"
        joined = " ".join(profile["warnings"])
        assert "unparseable" not in joined, joined
        assert "future dates" not in joined, joined
        assert "duplicate" not in joined, joined
