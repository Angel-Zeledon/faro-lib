"""
What the engine did to the user's data has to survive the trip to the screen.

`_collect_run_warnings` is the narrow point every correction passes through. It
maps the engine's report into the payload the results screen reads, and three
separate details in that mapping were quietly destroying the one correction that
matters most — censored-demand recovery, which REWRITES the user's own sales
figures upward before training on them.

A distributor comparing our output against their own file will find the numbers
disagree. The only acceptable version of that is one where the app told them.
"""

import pytest

from backend.workers.runner import MAX_WARNING_CODES, _collect_run_warnings


class _Engine:
    """Minimal stand-in for ForecastEngine's warnings interface."""

    def __init__(self, corrections, validation=None):
        self._payload = {"validation": validation or [], "corrections": corrections}

    def get_run_warnings(self):
        return self._payload


def _censoring_report(n_skus=500, n_recovered=47, units=312.4):
    """The real shape, straight from the engine's own dataclass."""
    from forecasting_core.data.censoring import CensoringReport

    return CensoringReport(
        inventory_column="existencias", n_rows=900, n_flagged=61,
        n_recovered=n_recovered, units_recovered=units,
        skus_affected=[f"SKU_{i}" for i in range(n_skus)],
    ).to_dict()


class TestSkuCountIsTheRealOne:

    def test_uses_the_producers_own_total_not_the_truncated_sample(self):
        """
        `CensoringReport` truncates `skus_affected` to 20 entries for payload
        size while still publishing the true total. Measuring the truncated list
        told a tenant whose entire catalogue was rewritten that 20 SKUs were.
        """
        report = _censoring_report(n_skus=500)
        assert len(report["skus_affected"]) == 20, "fixture assumption"
        assert report["n_skus_affected"] == 500

        out = _collect_run_warnings(_Engine([report]))
        correction = out["corrections"][0]
        assert correction["n_skus"] == 500, (
            f"reported {correction['n_skus']} SKUs for a run that touched 500"
        )

    def test_falls_back_to_the_list_length_when_no_total_is_published(self):
        """Corrections from other producers carry no explicit total."""
        out = _collect_run_warnings(_Engine([
            {"action": "gap_filled", "description": "x", "skus_affected": ["A", "B"]},
        ]))
        assert out["corrections"][0]["n_skus"] == 2


class TestTheNumbersTravel:

    def test_recovery_counts_reach_the_payload(self):
        """
        "We adjusted some numbers" and "we recovered 47 stockout days, +312
        units, across 500 products" are different messages. The second one lets
        a buyer reconcile our figures against their file; the first does not.
        """
        out = _collect_run_warnings(_Engine([_censoring_report()]))
        correction = out["corrections"][0]
        assert correction["n_recovered"] == 47
        assert correction["n_flagged"] == 61
        assert correction["units_recovered"] == pytest.approx(312.4)

    def test_absent_counts_are_omitted_not_zeroed(self):
        """A missing count must not render as "0 units recovered"."""
        out = _collect_run_warnings(_Engine([
            {"action": "outliers_clipped", "description": "x", "skus_affected": ["A"]},
        ]))
        correction = out["corrections"][0]
        assert "n_recovered" not in correction
        assert "units_recovered" not in correction

    def test_the_sku_list_itself_is_still_dropped(self):
        """It can run to thousands; only the count belongs in the payload."""
        out = _collect_run_warnings(_Engine([_censoring_report()]))
        assert "skus_affected" not in out["corrections"][0]


class TestTruncationCannotDropTheImportantOne:

    def test_data_rewriting_corrections_survive_a_full_list(self):
        """
        The pipeline appends the censoring report LAST. With a cap on the list
        and twenty cosmetic corrections ahead of it, the single most
        consequential thing the engine did to the data was the first casualty.
        """
        noise = [
            {"action": f"cosmetic_{i}", "description": "x", "skus_affected": ["A"]}
            for i in range(MAX_WARNING_CODES)
        ]
        out = _collect_run_warnings(_Engine(noise + [_censoring_report()]))

        actions = [c["action"] for c in out["corrections"]]
        assert len(actions) == MAX_WARNING_CODES
        assert "censored_demand_recovered" in actions, (
            "the correction that rewrote the user's sales figures was truncated "
            "away in favour of cosmetic ones"
        )

    def test_a_cosmetic_correction_is_what_gets_dropped(self):
        noise = [
            {"action": f"cosmetic_{i}", "description": "x", "skus_affected": ["A"]}
            for i in range(MAX_WARNING_CODES)
        ]
        out = _collect_run_warnings(_Engine(noise + [_censoring_report()]))
        actions = [c["action"] for c in out["corrections"]]
        assert sum(1 for a in actions if a.startswith("cosmetic_")) == MAX_WARNING_CODES - 1

    def test_ordering_is_stable_within_a_priority_band(self):
        """Two cosmetic corrections must not swap places run to run."""
        noise = [
            {"action": f"cosmetic_{i}", "description": "x", "skus_affected": ["A"]}
            for i in range(5)
        ]
        out = _collect_run_warnings(_Engine(noise))
        assert [c["action"] for c in out["corrections"]] == [
            f"cosmetic_{i}" for i in range(5)
        ]

    def test_short_lists_are_untouched(self):
        out = _collect_run_warnings(_Engine([_censoring_report()]))
        assert len(out["corrections"]) == 1


class TestInventoryFindingsReachThePanel:
    """
    The engine reports two silent outcomes of inventory generation as ordinary
    validation findings. They have to survive this mapper to be rendered.

    `SKU_WITHOUT_RECOMMENDATION` is the one that matters: on the semáforo a
    product with no suggested quantity looks exactly like a product that is
    well stocked, so losing this finding means the buyer does not order
    something they needed to order.
    """

    def _engine_with(self, baseline_losses=(), missing=()):
        from forecasting_core.config.config import SessionConfig
        from forecasting_core.pipelines.pipeline import Pipeline

        pipeline = Pipeline(SessionConfig.from_dict({
            "columns": {"target": "demand", "date": "date", "group_keys": ["sku"]},
            "models": {"lightgbm": {}},
        }))
        pipeline._outperformed_by_baseline = list(baseline_losses)
        pipeline._skipped_no_forecast = list(missing)
        return _Engine([], validation=pipeline._inventory_findings())

    def test_grouped_by_code_with_a_count(self):
        engine = self._engine_with(
            missing=[{"sku": f"SKU_{i}", "model": "prophet"} for i in range(3)],
        )
        out = _collect_run_warnings(engine)
        group = next(g for g in out["validation"]
                     if g["code"] == "SKU_WITHOUT_RECOMMENDATION")
        assert group["count"] == 3
        assert group["severity"] == "error"
        assert group["samples"], "the affected SKUs must be inspectable"

    def test_a_missing_recommendation_outranks_a_weak_model(self):
        """The panel shows errors first; the dangerous one must lead."""
        engine = self._engine_with(
            baseline_losses=[{"sku": "A", "model": "lightgbm", "baseline": "naive"}],
            missing=[{"sku": "B", "model": "prophet"}],
        )
        codes = [g["code"] for g in _collect_run_warnings(engine)["validation"]]
        assert codes[0] == "SKU_WITHOUT_RECOMMENDATION"

    def test_a_clean_run_produces_neither(self):
        out = _collect_run_warnings(self._engine_with())
        assert out["validation"] == []


class TestResilience:

    def test_an_engine_that_raises_does_not_fail_the_run(self):
        """Reporting is an extra; it must never take a good training run down."""
        class _Broken:
            def get_run_warnings(self):
                raise RuntimeError("boom")

        out = _collect_run_warnings(_Broken())
        assert out == {"validation": [], "corrections": []}

    def test_no_corrections_is_an_empty_list(self):
        out = _collect_run_warnings(_Engine([]))
        assert out["corrections"] == []
