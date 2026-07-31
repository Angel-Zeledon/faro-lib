"""The pre-training gate: what gets rejected, what gets a choice, what gets waved through.

The product rule under test: a soft warning under a button reading "this looks
good" is not a standard. Three outcomes, and the interesting assertions are on
the third — over-blocking a normal distributor catalogue is the failure mode
that makes the product unusable, so most of this file is about what must NOT
block.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from forecasting_core.data import gate
from forecasting_core.data.profiler import DataProfiler


def _frame(rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _row(sku: str, day: date, qty) -> dict:
    return {"sku": sku, "fecha": day.isoformat(), "cantidad": qty}


def _series(sku: str, n: int, start: date = date(2025, 1, 1), qty=None) -> list:
    return [_row(sku, start + timedelta(days=i),
                 qty if qty is not None else 10 + (i % 7)) for i in range(n)]


def healthy() -> pd.DataFrame:
    """Three established products plus one launched later. The control group."""
    rows = _series("SKU-A", 120) + _series("SKU-B", 120) + _series("SKU-C", 120)
    rows += _series("SKU-NEW", 6, start=date(2025, 3, 1))
    return _frame(rows)


def _gate(df: pd.DataFrame, **kw) -> dict:
    return DataProfiler().evaluate_gate(
        df, kw.get("date_col", "fecha"), kw.get("target_col", "cantidad"),
        kw.get("group_col", "sku"), canonical_mapping=kw.get("canonical_mapping"),
    )


def _types(dq: dict, klass: str) -> set:
    return {i["type"] for i in dq["issues"] if i["classification"] == klass}


# ── The catalogue is not allowed to lie ────────────────────────────────────

class TestTheOptionCatalogueIsHonest:
    def test_every_option_names_the_step_that_applies_it(self):
        gate.validate_catalogue()

    def test_every_option_offered_anywhere_is_in_the_catalogue(self):
        """`_opt` refuses an unknown code, so this pins that it stays refusing."""
        with pytest.raises(KeyError):
            gate._opt("an_option_nobody_implemented", "does", "costs")

    def test_a_fixable_issue_with_no_options_is_demoted_to_fatal(self):
        """The rule stated as code: an option that does nothing is worse than none.

        A finding that claims to be fixable but offers nothing to pick would
        block a run forever with no way out.
        """
        dq = gate.classify({"issues": [{
            "type": "invented_problem", "severity": "error", "blocking": True,
            "message": "", "classification": gate.BLOCKING_FIXABLE, "remediations": [],
        }]})
        assert dq["issues"][0]["classification"] == gate.BLOCKING_FATAL
        assert dq["outcome"] == "blocked_fatal"


# ── Rejected outright ──────────────────────────────────────────────────────

class TestRejectedOutright:
    def test_header_only_file(self):
        dq = _gate(_frame([]).reindex(columns=["sku", "fecha", "cantidad"]))
        assert dq["outcome"] == "blocked_fatal"
        assert "empty_dataset" in _types(dq, gate.BLOCKING_FATAL)

    def test_two_columns_cannot_tie_a_forecast_to_a_product(self):
        df = _frame([{"fecha": (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
                      "cantidad": 5 + i} for i in range(60)])
        dq = _gate(df, group_col=None)
        assert "insufficient_columns" in _types(dq, gate.BLOCKING_FATAL)

    def test_all_demand_zero(self):
        df = healthy()
        df["cantidad"] = 0
        dq = _gate(df)
        assert dq["outcome"] == "blocked_fatal"
        assert "all_zeros" in _types(dq, gate.BLOCKING_FATAL)

    def test_a_single_distinct_date_is_a_snapshot_not_a_history(self):
        df = _frame([_row(f"SKU-{i}", date(2025, 5, 5), 3 + i) for i in range(40)])
        dq = _gate(df)
        assert "single_date_value" in _types(dq, gate.BLOCKING_FATAL)

    def test_no_numeric_column_could_be_demand(self):
        df = _frame([{"sku": f"S{i}", "fecha": (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
                      "nota": "vendido"} for i in range(40)])
        dq = _gate(df, target_col=None)
        assert "no_numeric_column" in _types(dq, gate.BLOCKING_FATAL)

    def test_no_date_column_at_all(self):
        df = _frame([{"sku": f"S{i % 4}", "nota": f"x{i}", "cantidad": i}
                     for i in range(40)])
        dq = _gate(df, date_col=None)
        assert "no_date_column" in _types(dq, gate.BLOCKING_FATAL)

    def test_a_product_column_with_one_value_per_row_is_an_invoice_number(self):
        rows = [_row(f"FAC-{i:05d}", date(2025, 1, 1) + timedelta(days=i), 5)
                for i in range(60)]
        dq = _gate(_frame(rows))
        assert "identifier_not_product" in _types(dq, gate.BLOCKING_FATAL)

    def test_a_fatal_file_is_not_also_asked_to_choose_a_fix(self):
        """Asking how to net returns on a file that can never train is an insult."""
        df = pd.concat([healthy(), healthy().head(30)], ignore_index=True)
        assert "duplicates" in _gate(df)["blocking_fixable"]   # fixable on its own
        df["cantidad"] = 0                                      # now also fatal
        dq = _gate(df)
        assert "all_zeros" in dq["blocking_fatal"]
        assert dq["blocking_fixable"] == []

    def test_no_options_are_offered_for_anything_fatal(self):
        df = healthy()
        df["cantidad"] = 0
        dq = _gate(df)
        for issue in dq["issues"]:
            if issue["classification"] == gate.BLOCKING_FATAL:
                assert issue["remediations"] == []


# ── Rejected with options ──────────────────────────────────────────────────

class TestRejectedWithOptions:
    def _one(self, dq: dict, type_: str) -> dict:
        found = [i for i in dq["issues"] if i["type"] == type_]
        assert found, f"{type_} not detected; got {[i['type'] for i in dq['issues']]}"
        return found[0]

    def test_ambiguous_date_format_offers_both_readings(self):
        rows = [{"sku": "A", "fecha": f"{(i % 12) + 1:02d}/{(i % 11) + 1:02d}/2025",
                 "cantidad": 5 + i} for i in range(40)]
        issue = self._one(_gate(_frame(rows)), "ambiguous_date_format")
        assert issue["classification"] == gate.BLOCKING_FIXABLE
        assert {o["code"] for o in issue["remediations"]} == {
            "date_format_day_first", "date_format_month_first"}

    def test_an_unambiguous_date_is_not_questioned(self):
        """13/01/2025 can only be day-first — there is no choice to make."""
        rows = [{"sku": "A", "fecha": f"{13 + (i % 15):02d}/{(i % 12) + 1:02d}/2025",
                 "cantidad": 5 + i} for i in range(40)]
        assert not gate.detect_ambiguous_date_format(_frame(rows), "fecha")

    def test_ambiguous_thousands_separator_is_asked_not_guessed(self):
        rows = [_row("A", date(2025, 1, 1) + timedelta(days=i), "1,234")
                for i in range(40)]
        issue = self._one(_gate(_frame(rows)), "ambiguous_number_format")
        assert issue["classification"] == gate.BLOCKING_FIXABLE
        assert {o["code"] for o in issue["remediations"]} == {
            "separator_comma_is_thousands", "separator_comma_is_decimal"}
        # Both readings are spelled out, a thousandfold apart.
        assert issue["params"]["as_thousands"] == "1234"
        assert issue["params"]["as_decimal"] == "1.234"

    def test_an_unambiguous_european_decimal_is_not_questioned(self):
        rows = [_row("A", date(2025, 1, 1) + timedelta(days=i), "1.234,50")
                for i in range(40)]
        assert not gate.detect_ambiguous_number_format(_frame(rows), "cantidad")

    def test_cumulative_demand_offers_the_difference(self):
        rows = []
        for sku in ("A", "B"):
            total = 0
            for i in range(60):
                total += 5 + (i % 3)
                rows.append(_row(sku, date(2025, 1, 1) + timedelta(days=i), total))
        issue = self._one(_gate(_frame(rows)), "cumulative_demand")
        assert issue["classification"] == gate.BLOCKING_FIXABLE
        assert "cumulative_to_periodic" in {o["code"] for o in issue["remediations"]}

    def test_ordinary_growth_is_not_called_cumulative(self):
        """A product that grows but has down days is a product, not a running total."""
        assert not gate.detect_cumulative_demand(healthy(), "fecha", "cantidad", "sku")

    def test_duplicate_day_and_product_is_a_choice_not_an_assumption(self):
        df = pd.concat([healthy(), healthy().head(30)], ignore_index=True)
        issue = self._one(_gate(df), "duplicates")
        assert issue["classification"] == gate.BLOCKING_FIXABLE
        assert {o["code"] for o in issue["remediations"]} == {
            "duplicates_sum", "duplicates_keep_last"}

    def test_negative_demand_offers_three_readings(self):
        df = healthy()
        df.loc[df.index[:8], "cantidad"] = -3
        issue = self._one(_gate(df), "negative_target")
        assert {o["code"] for o in issue["remediations"]} == {
            "negatives_net_into_period", "negatives_as_zero", "negatives_drop_rows"}

    def test_an_impossible_date_states_how_much_history_dropping_it_costs(self):
        df = healthy()
        df.loc[df.index[0], "fecha"] = "1900-01-01"
        issue = self._one(_gate(df), "out_of_range_dates")
        drop = next(o for o in issue["remediations"] if o["code"] == "out_of_range_dates_drop")
        assert drop["params"]["span_days_before"] > drop["params"]["span_days_after"]

    def test_excel_serial_dates_are_offered_instead_of_a_dead_end(self):
        rows = [{"sku": f"S{i % 3}", "dia": 45000 + i, "cantidad": 5 + i}
                for i in range(60)]
        dq = _gate(_frame(rows), date_col=None)
        issue = self._one(dq, "excel_serial_dates")
        assert issue["classification"] == gate.BLOCKING_FIXABLE
        # And the fatal "no date column" is not ALSO raised — there is a way out.
        assert "no_date_column" not in _types(dq, gate.BLOCKING_FATAL)

    def test_inconsistent_sku_identity(self):
        rows = _series("SKU-1", 40) + [
            _row(" sku-1 ", date(2025, 3, 1) + timedelta(days=i), 4) for i in range(40)]
        issue = self._one(_gate(_frame(rows)), "inconsistent_sku_identity")
        assert issue["params"]["n_groups"] == 1
        assert {o["code"] for o in issue["remediations"]} == {
            "sku_identity_unify", "sku_identity_keep_separate"}

    def test_a_money_column_mapped_as_demand(self):
        rows = [_row(f"S{i % 3}", date(2025, 1, 1) + timedelta(days=i),
                     round(1500 + i * 13.37, 2)) for i in range(60)]
        issue = self._one(_gate(_frame(rows)), "target_looks_like_money")
        assert {o["code"] for o in issue["remediations"]} == {
            "target_is_units", "target_is_money_remap"}

    def test_whole_units_are_not_called_money(self):
        assert not gate.detect_target_looks_like_money(healthy(), "cantidad")

    def test_a_dataset_wide_hole_is_blocked_when_products_resume_after_it(self):
        rows = _series("A", 60) + _series("B", 60)
        resume = date(2025, 1, 1) + timedelta(days=120)
        rows += _series("A", 40, start=resume) + _series("B", 40, start=resume)
        issue = self._one(_gate(_frame(rows)), "dataset_wide_gap")
        assert issue["classification"] == gate.BLOCKING_FIXABLE
        assert {o["code"] for o in issue["remediations"]} == {
            "gaps_fill_zero", "gaps_interpolate", "gaps_forward_fill", "gaps_leave"}

    def test_text_where_a_quantity_belongs(self):
        df = healthy()
        df["cantidad"] = df["cantidad"].astype(object)
        df.loc[df.index[:12], "cantidad"] = ["N/D", "-", "$10", "10 kg"] * 3
        issue = self._one(_gate(df), "non_numeric_target")
        assert issue["classification"] == gate.BLOCKING_FIXABLE
        assert {o["code"] for o in issue["remediations"]} == {
            "non_numeric_strip_symbols", "non_numeric_as_zero", "non_numeric_drop_rows"}

    def test_every_fixable_issue_carries_at_least_two_real_choices(self):
        """A single-option "choice" is a dialog box, not a decision."""
        df = healthy()
        df.loc[df.index[:8], "cantidad"] = -3
        for issue in _gate(df)["issues"]:
            if issue["classification"] == gate.BLOCKING_FIXABLE:
                assert len(issue["remediations"]) >= 2, issue["type"]

    def test_every_option_states_a_consequence(self):
        """The consequence IS the product. An option without one is a dropdown."""
        df = pd.concat([healthy(), healthy().head(30)], ignore_index=True)
        df.loc[df.index[:8], "cantidad"] = -3
        for issue in _gate(df)["issues"]:
            for option in issue["remediations"]:
                assert option["consequence"].strip(), (issue["type"], option["code"])
                assert option["action"].strip(), (issue["type"], option["code"])


# ── Advisory: the part that must NOT block ─────────────────────────────────

class TestNormalDistributorDataIsNotBlocked:
    def test_the_control_catalogue_is_clear(self):
        dq = _gate(healthy())
        assert dq["outcome"] == "clear", dq["blocking_fatal"] + dq["blocking_fixable"]

    def test_a_new_product_with_six_days_of_history_does_not_block(self):
        dq = _gate(healthy())
        short = [i for i in dq["issues"] if i["type"] == "short_history"]
        assert short and short[0]["classification"] == gate.ADVISORY

    def test_nobody_sells_every_sku_every_day(self):
        """The single most important non-block: sparse per-SKU demand is the norm."""
        rows = []
        for sku in ("A", "B", "C"):
            for i in range(0, 240, 3):        # every third day only
                rows.append(_row(sku, date(2025, 1, 1) + timedelta(days=i), 4))
        dq = _gate(_frame(rows))
        assert dq["outcome"] == "clear", dq["blocking_fixable"]

    def test_frequent_zeros_on_one_sku_do_not_block(self):
        rows = _series("A", 200)
        rows += [_row("SLOW", date(2025, 1, 1) + timedelta(days=i), 0 if i % 9 else 2)
                 for i in range(200)]
        dq = _gate(_frame(rows))
        assert dq["outcome"] == "clear", dq["blocking_fixable"]

    def test_an_intermittent_catalogue_is_advisory_not_blocking(self):
        """80% zeros is a slow-moving distributor, and Croston exists for it."""
        rows = [_row(f"S{i % 3}", date(2025, 1, 1) + timedelta(days=i // 3),
                     0 if i % 5 else 4) for i in range(600)]
        dq = _gate(_frame(rows))
        assert "intermittent" in _types(dq, gate.ADVISORY)
        assert dq["outcome"] == "clear", dq["blocking_fixable"]

    def test_a_single_product_catalogue_does_not_block(self):
        dq = _gate(_frame(_series("ONLY-ONE", 200)))
        assert dq["outcome"] == "clear", dq["blocking_fixable"]

    def test_a_discontinued_product_does_not_block(self):
        rows = _series("LIVE", 200) + _series("DEAD", 60)
        dq = _gate(_frame(rows))
        assert dq["outcome"] == "clear", dq["blocking_fixable"]

    def test_a_catalogue_that_turns_over_is_not_a_missing_export(self):
        """Old products stop in April, new ones start in June. No series lost a day."""
        rows = _series("OLD-A", 90) + _series("OLD-B", 90)
        rows += _series("NEW-A", 90, start=date(2025, 7, 1))
        assert not gate.detect_dataset_wide_gap(_frame(rows), "fecha", "sku")

    def test_extreme_values_are_reported_with_their_cost_but_never_gated(self):
        """A wholesale order and a promotion are real demand, not corruption."""
        df = healthy()
        df.loc[df.index[10], "cantidad"] = 9000
        dq = _gate(df)
        issue = next(i for i in dq["issues"] if i["type"] == "outliers")
        assert issue["classification"] == gate.ADVISORY
        assert issue["blocking"] is False
        # Options are still offered — what was missing was never the block.
        assert "outliers_clip_iqr" in {o["code"] for o in issue["remediations"]}
        assert "under-order" in issue["remediations"][1]["consequence"]

    def test_a_few_stray_blanks_do_not_block(self):
        df = healthy()
        df.loc[df.index[:3], "cantidad"] = None
        dq = _gate(df)
        null = next(i for i in dq["issues"] if i["type"] == "null_target")
        assert null["classification"] == gate.ADVISORY

    def test_a_tenth_of_the_file_blank_does_block(self):
        df = healthy()
        df.loc[df.index[:60], "cantidad"] = None
        null = next(i for i in _gate(df)["issues"] if i["type"] == "null_target")
        assert null["classification"] == gate.BLOCKING_FIXABLE


class TestSaidButNotGated:
    def test_censored_demand_is_stated_when_no_stock_column_is_mapped(self):
        """No fix exists — only a column the user does not have. Say it anyway."""
        dq = _gate(healthy(), canonical_mapping={"sku": "sku", "date": "fecha",
                                                 "demand": "cantidad"})
        issue = next(i for i in dq["issues"] if i["type"] == "censored_demand_no_inventory")
        assert issue["classification"] == gate.ADVISORY
        assert issue["remediable"] is False
        assert issue["remediations"] == []
        assert dq["outcome"] == "clear"

    def test_it_is_silent_once_a_stock_column_is_mapped(self):
        dq = _gate(healthy(), canonical_mapping={"sku": "sku", "date": "fecha",
                                                 "demand": "cantidad",
                                                 "inventory": "stock"})
        assert "censored_demand_no_inventory" not in {i["type"] for i in dq["issues"]}

    def test_it_is_silent_before_the_user_has_mapped_anything(self):
        """Naming a missing column the user has not been offered yet is noise."""
        dq = _gate(healthy())
        assert "censored_demand_no_inventory" not in {i["type"] for i in dq["issues"]}


# ── Resolution bookkeeping ─────────────────────────────────────────────────

class TestUnresolved:
    def _dq(self) -> dict:
        df = healthy()
        df.loc[df.index[:8], "cantidad"] = -3
        return _gate(df)

    def test_a_fixable_issue_starts_unresolved(self):
        dq = self._dq()
        assert gate.unresolved(dq, {}) == ["negative_target"]

    def test_a_valid_choice_resolves_it(self):
        dq = self._dq()
        assert gate.unresolved(dq, {"negative_target": "negatives_drop_rows"}) == []

    def test_an_option_from_a_different_issue_resolves_nothing(self):
        """A stale answer left over from another file must not open the gate."""
        dq = self._dq()
        assert gate.unresolved(dq, {"negative_target": "gaps_fill_zero"}) == [
            "negative_target"]

    def test_an_advisory_issue_never_needs_an_answer(self):
        dq = _gate(healthy())
        assert gate.unresolved(dq, {}) == []
