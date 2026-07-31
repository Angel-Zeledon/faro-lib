"""
The decision-quality half of a training result has to reach the client.

Three things the engine now stores in `session_results` are read by the
/pronosticos screen and by nothing else:

  * `policy_backtest` — what the buyer would have lived through if they had
    ordered on this forecast, against the same policy driven by "repeat the last
    value". This is the headline the screen shows.
  * `demand_risk` — the measured cumulative uncertainty behind each safety
    stock, which the Inventory tab cites as the number's provenance.
  * `warnings.corrections` carrying `censored_demand_recovered` — the notice
    that the engine REWROTE the user's own sales figures upward. A system that
    silently edits someone's numbers and only logs it is the worst version of
    this feature, so the code reaching the client is asserted, not assumed.

None of them has a dedicated endpoint: `GET /sessions/{id}/results` returns the
stored payload whole, and these tests pin that passthrough. Every assertion
compares the response against a value written directly to the DB, so a
serialisation that flattened, rounded or dropped a nested field would fail
rather than pass on a 200.
"""

from uuid import uuid4

import pytest

from backend.db import session_store
from backend.sessions.service import force_status


SUMMARY = {
    "n_series": 7,
    "fill_rate": 0.9642,
    "baseline_fill_rate": 0.8511,
    "stockout_buckets": 23,
    "baseline_stockout_buckets": 54,
    "stockouts_avoided": 31,
    "avg_inventory": 214.5,
    "baseline_avg_inventory": 196.25,
    "capital_tied_up": 1_250_400.0,
    "baseline_capital_tied_up": 1_110_900.0,
}

BY_SKU = {
    "SKU_001": {
        "model": "lightgbm",
        "policy": {
            "n_buckets": 30, "total_demand": 900.0, "units_short": 12.0,
            "units_ordered": 910.0, "stockout_buckets": 2, "fill_rate": 0.9867,
            "stockout_rate": 0.0667, "avg_inventory": 88.4, "peak_inventory": 160.0,
            "days_of_cover": 2.95, "capital_tied_up": 309.4,
        },
        "baseline": {
            "n_buckets": 30, "total_demand": 900.0, "units_short": 140.0,
            "units_ordered": 760.0, "stockout_buckets": 9, "fill_rate": 0.8444,
            "stockout_rate": 0.3, "avg_inventory": 61.0, "peak_inventory": 120.0,
            "days_of_cover": 2.03, "capital_tied_up": 213.5,
        },
        "fill_rate_gain": 0.1423,
        "stockouts_avoided": 7,
        "inventory_delta": 27.4,
        "capital_freed": -95.9,
    },
}

DEMAND_RISK = {
    "SKU_001": {
        "model": "global_lgbm",
        "quantiles": [0.5, 0.95],
        "cumulative_offsets": {"7": {"0.5": 0.0, "0.95": 41.75}},
    },
}


def _completed_session_with(client, headers, tenant_id: str, payload: dict) -> str:
    """A COMPLETED session whose stored training result is exactly `payload`."""
    resp = client.post(
        "/api/v1/sessions",
        json={"name": f"policy-backtest-{uuid4().hex[:6]}"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["data"]["id"]

    session_store.set_training_result(tenant_id, session_id, payload)
    force_status(tenant_id, session_id, "COMPLETED")
    return session_id


def _results(client, headers, session_id: str) -> dict:
    resp = client.get(f"/api/v1/sessions/{session_id}/results", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestPolicyBacktestReachesTheClient:

    def test_summary_and_per_sku_survive_the_round_trip(
        self, client, auth_headers, test_tenant,
    ):
        """Field for field, including the baseline half — a payload that kept
        only the model's own numbers would make the whole panel meaningless."""
        tid = test_tenant["id"]
        sid = _completed_session_with(client, auth_headers, tid, {
            "metrics": {"rows": [{"sku": "SKU_001", "model": "lightgbm", "wape": 0.1}]},
            "policy_backtest": {"summary": SUMMARY, "by_sku": BY_SKU},
            "config": {},
        })

        data = _results(client, auth_headers, sid)
        backtest = data["policy_backtest"]

        assert backtest["summary"] == SUMMARY
        assert backtest["by_sku"] == BY_SKU
        # The two halves of the comparison, explicitly: the screen states every
        # figure against its baseline, so losing either one is a silent failure.
        assert backtest["summary"]["fill_rate"] == pytest.approx(0.9642)
        assert backtest["summary"]["baseline_fill_rate"] == pytest.approx(0.8511)
        # Coverage is what stops the headline reading as catalogue-wide.
        assert backtest["summary"]["n_series"] == 7

    def test_demand_risk_survives_nested_under_lead_time_and_quantile(
        self, client, auth_headers, test_tenant,
    ):
        """`cumulative_offsets` is a dict of dicts keyed by lead time; a JSON
        round trip that stringified or flattened it would break the consumer."""
        tid = test_tenant["id"]
        sid = _completed_session_with(client, auth_headers, tid, {
            "metrics": {"rows": []},
            "demand_risk": DEMAND_RISK,
            "config": {},
        })

        data = _results(client, auth_headers, sid)
        assert data["demand_risk"] == DEMAND_RISK
        assert data["demand_risk"]["SKU_001"]["cumulative_offsets"]["7"]["0.95"] == \
            pytest.approx(41.75)

    def test_a_run_without_a_backtest_reports_absence_not_zero(
        self, client, auth_headers, test_tenant,
    ):
        """A run whose models produced no rolling-origin backtest, and every
        session trained before the field existed, must come back EMPTY. A zeroed
        summary would be the product claiming a 0% fill rate it never measured."""
        tid = test_tenant["id"]
        sid = _completed_session_with(client, auth_headers, tid, {
            "metrics": {"rows": [{"sku": "SKU_001", "model": "lightgbm", "wape": 0.1}]},
            "policy_backtest": {},
            "config": {},
        })

        data = _results(client, auth_headers, sid)
        assert data["policy_backtest"] == {}
        # A legacy session lacks the key outright — also absence, never a zero.
        legacy = _completed_session_with(client, auth_headers, tid, {
            "metrics": {"rows": []}, "config": {},
        })
        assert _results(client, auth_headers, legacy).get("policy_backtest") is None

    def test_a_viewer_can_read_it(self, client, auth_headers, viewer_headers, test_tenant):
        """It is a read, so the lowest role must see the same payload — the
        panel is on a screen viewers are expected to open."""
        tid = test_tenant["id"]
        sid = _completed_session_with(client, auth_headers, tid, {
            "metrics": {"rows": []},
            "policy_backtest": {"summary": SUMMARY, "by_sku": BY_SKU},
            "config": {},
        })

        assert _results(client, viewer_headers, sid)["policy_backtest"]["summary"] == SUMMARY

    def test_another_tenant_cannot_read_it(
        self, client, auth_headers, test_tenant, make_tenant_user_headers,
    ):
        """The backtest exposes a competitor's fill rate and the cash they hold
        in stock. Tenant isolation on this route is not optional."""
        tid = test_tenant["id"]
        sid = _completed_session_with(client, auth_headers, tid, {
            "metrics": {"rows": []},
            "policy_backtest": {"summary": SUMMARY, "by_sku": BY_SKU},
            "config": {},
        })

        outsider = make_tenant_user_headers(role="admin")
        resp = client.get(f"/api/v1/sessions/{sid}/results", headers=outsider)
        assert resp.status_code == 404, resp.text


class TestCensoringNoticeReachesTheClient:
    """The engine rewrites the user's demand figures upward when it detects
    stockout-truncated sales. The user learns about it through exactly one
    channel — the corrections list on GET /sessions/{id}/warnings — so the
    stable action code has to arrive intact for the UI to have copy to render.
    """

    def test_the_censoring_action_code_arrives_on_the_warnings_endpoint(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sid = _completed_session_with(client, auth_headers, tid, {
            "metrics": {"rows": []},
            "warnings": {
                "validation": [],
                "corrections": [
                    {"action": "censored_demand_recovered", "description": "", "n_skus": 4},
                ],
            },
            "config": {},
        })

        resp = client.get(f"/api/v1/sessions/{sid}/warnings", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        corrections = resp.json()["data"]["corrections"]

        actions = [c["action"] for c in corrections]
        assert "censored_demand_recovered" in actions

    def test_the_runner_preserves_the_action_code_from_the_engine_report(self):
        """The censoring report is a CensoringReport.to_dict(), which carries no
        `description` at all. The grouping step must still emit the action code:
        the UI renders its copy from that code, and an empty description alone
        would put a blank bullet on screen where the notice belongs.
        """
        from backend.workers.runner import _collect_run_warnings

        class _Engine:
            def get_run_warnings(self):
                return {
                    "validation": [],
                    # Exactly the shape censoring.CensoringReport.to_dict() emits.
                    "corrections": [{
                        "action": "censored_demand_recovered",
                        "inventory_column": "stock",
                        "n_rows": 900,
                        "n_flagged": 61,
                        "n_recovered": 47,
                        "units_recovered": 312.5,
                        "skus_affected": ["SKU_001", "SKU_002"],
                        "n_skus_affected": 2,
                        "skipped_reason": None,
                    }],
                }

        out = _collect_run_warnings(_Engine())
        assert [c["action"] for c in out["corrections"]] == ["censored_demand_recovered"]
