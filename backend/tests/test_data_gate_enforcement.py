"""The gate is enforced by the API, not by a disabled button.

The defect this pins: a file whose sales are ALL ZERO warned "there is nothing
to forecast" and still offered "this looks good, continue". The warning was
soft, the button was live, and the only thing standing between that file and a
two-minute wait for `no_models_trained` was the browser. The API, the ERP sync
path and any client with a token walked straight past it.

Three claims are under test here and each maps to something a user would feel:

1. An all-zero file is refused BY THE API — status AND no job row.
2. A normal catalogue (intermittent demand, one product launched last month) is
   NOT refused. Over-blocking is the failure mode that makes the product
   unusable.
3. Every option we offer actually changes the data the way its consequence
   claims. An option that does nothing is worse than no option, because the user
   believes they decided something.
"""

from datetime import date, timedelta
from uuid import uuid4

import pandas as pd
import pytest

from backend.db import session_store
from backend.db.connection import query
from backend.sessions.defaults import default_quickstart_configs
from backend.workers import runner
from forecasting_core.data import gate


# ── Fixtures on disk ───────────────────────────────────────────────────────

HEADER = "sku,fecha,cantidad\n"


def _rows(sku: str, n: int, start: date = date(2025, 1, 1), qty=None) -> list[str]:
    return [f"{sku},{(start + timedelta(days=i)).isoformat()},"
            f"{qty if qty is not None else 10 + (i % 7)}" for i in range(n)]


def _csv(rows: list[str]) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode()


HEALTHY = _csv(_rows("SKU-A", 120) + _rows("SKU-B", 120) + _rows("SKU-C", 120)
               + _rows("SKU-NEW", 6, start=date(2025, 3, 1)))

ALL_ZERO = _csv(_rows("SKU-A", 120, qty=0) + _rows("SKU-B", 120, qty=0))

# One row per SALE, the way most ERPs export: the same day repeats.
PER_TRANSACTION = _csv(
    _rows("SKU-A", 120) + _rows("SKU-A", 30) + _rows("SKU-B", 120))

# An SMB catalogue: sells three days a week, one product launched last month.
INTERMITTENT = _csv(
    [f"SKU-{s},{(date(2025, 1, 1) + timedelta(days=i)).isoformat()},{4 + (i % 3)}"
     for s in ("A", "B", "C") for i in range(0, 240, 3)]
    + _rows("SKU-NEW", 8, start=date(2025, 8, 1)))


def _session_with(client, headers, csv: bytes) -> str:
    """Upload, attach and configure a session up to MODELS_CONFIGURED."""
    ds = client.post("/api/v1/datasets",
                     files={"file": (f"gate-{uuid4().hex[:6]}.csv", csv, "text/csv")},
                     headers=headers)
    assert ds.status_code == 201, ds.text
    sess = client.post("/api/v1/sessions", json={"name": f"gate-{uuid4().hex[:6]}"},
                       headers=headers)
    sid = sess.json()["data"]["id"]
    assert client.post(f"/api/v1/sessions/{sid}/dataset",
                       json={"dataset_id": ds.json()["data"]["id"]},
                       headers=headers).status_code == 200
    return sid


def _make_trainable(tenant_id: str, sid: str) -> None:
    from backend.sessions import service as session_svc

    for field, cfg in default_quickstart_configs().items():
        session_store.set_field(tenant_id, sid, field, cfg)
    session_svc.force_status(tenant_id, sid, "MODELS_CONFIGURED")


def _jobs(tenant_id: str, sid: str) -> list:
    return query("SELECT id, status FROM jobs WHERE tenant_id=%s AND session_id=%s",
                 (tenant_id, sid))


def _status(tenant_id: str, sid: str) -> str:
    return query("SELECT status FROM sessions WHERE tenant_id=%s AND id=%s",
                 (tenant_id, sid))[0]["status"]


# ── 1. Refused by the API ──────────────────────────────────────────────────

class TestAnUntrainableFileIsRefusedByTheApi:
    def test_all_zero_demand_cannot_start_a_run(self, client, auth_headers, test_tenant):
        sid = _session_with(client, auth_headers, ALL_ZERO)
        _make_trainable(test_tenant["id"], sid)

        r = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)

        assert r.status_code == 422, r.text
        assert r.json()["error_code"] == "training_blocked_data_fatal"
        assert "all_zeros" in r.json()["error_params"]["issues"]

    def test_and_no_job_row_was_created(self, client, auth_headers, test_tenant):
        """A 422 the queue ignored would be a gate in name only."""
        sid = _session_with(client, auth_headers, ALL_ZERO)
        _make_trainable(test_tenant["id"], sid)
        client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)

        assert _jobs(test_tenant["id"], sid) == []
        assert _status(test_tenant["id"], sid) == "MODELS_CONFIGURED"

    def test_the_integrations_sync_path_cannot_walk_past_it_either(
            self, client, auth_headers, test_tenant):
        """The gate lives in launch_training_family, which every path goes through."""
        from backend.errors import AppError
        from backend.sessions import family_service as fam

        sid = _session_with(client, auth_headers, ALL_ZERO)
        _make_trainable(test_tenant["id"], sid)
        with pytest.raises(AppError) as exc:
            fam.launch_training_family(test_tenant["id"], sid, "system")
        assert exc.value.code == "training_blocked_data_fatal"
        assert _jobs(test_tenant["id"], sid) == []


# ── 2. Normal data is not refused ──────────────────────────────────────────

class TestANormalCatalogueIsNotBlocked:
    def test_intermittent_demand_with_a_new_product_trains(
            self, client, auth_headers, test_tenant):
        """The false positive that would be worse than the original defect."""
        sid = _session_with(client, auth_headers, INTERMITTENT)
        _make_trainable(test_tenant["id"], sid)

        r = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)

        assert r.status_code == 202, r.text
        assert _jobs(test_tenant["id"], sid), "a clear file must actually queue a job"

    def test_the_gate_reports_it_as_clear(self, client, auth_headers):
        sid = _session_with(client, auth_headers, INTERMITTENT)
        r = client.get(f"/api/v1/sessions/{sid}/data-gate", headers=auth_headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["outcome"] == "clear", data["blocking_fatal"] + data["blocking_fixable"]

    def test_the_new_product_is_still_mentioned_softly(self, client, auth_headers):
        sid = _session_with(client, auth_headers, HEALTHY)
        data = client.get(f"/api/v1/sessions/{sid}/data-gate",
                          headers=auth_headers).json()["data"]
        short = [i for i in data["issues"] if i["type"] == "short_history"]
        assert short and short[0]["classification"] == "advisory"
        assert short[0]["blocking"] is False


# ── 3. A fixable file waits for a decision ─────────────────────────────────

class TestAFixableFileWaitsForADecision:
    def test_duplicated_days_block_until_the_user_says_what_they_mean(
            self, client, auth_headers, test_tenant):
        sid = _session_with(client, auth_headers, PER_TRANSACTION)
        _make_trainable(test_tenant["id"], sid)

        r = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)

        assert r.status_code == 422, r.text
        assert r.json()["error_code"] == "training_blocked_unresolved"
        assert "duplicates" in r.json()["error_params"]["issues"]
        assert _jobs(test_tenant["id"], sid) == []

    def test_the_options_arrive_with_what_each_one_costs(self, client, auth_headers):
        sid = _session_with(client, auth_headers, PER_TRANSACTION)
        data = client.get(f"/api/v1/sessions/{sid}/data-gate",
                          headers=auth_headers).json()["data"]
        issue = next(i for i in data["issues"] if i["type"] == "duplicates")
        by_code = {o["code"]: o for o in issue["remediations"]}
        assert set(by_code) == {"duplicates_sum", "duplicates_keep_last"}
        for option in by_code.values():
            assert option["consequence"].strip()

    def test_recording_a_choice_opens_the_gate(self, client, auth_headers, test_tenant):
        sid = _session_with(client, auth_headers, PER_TRANSACTION)
        _make_trainable(test_tenant["id"], sid)

        choice = client.post(
            f"/api/v1/sessions/{sid}/configure/remediations",
            json={"remediations": {"duplicates": "duplicates_keep_last"}},
            headers=auth_headers)
        assert choice.status_code == 200, choice.text
        assert choice.json()["data"]["unresolved"] == []

        r = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert r.status_code == 202, r.text
        assert _jobs(test_tenant["id"], sid)

    def test_the_choice_is_stored_where_the_runner_reads_it(
            self, client, auth_headers, test_tenant):
        """Direct DB read: a choice the worker cannot see is not a choice."""
        sid = _session_with(client, auth_headers, PER_TRANSACTION)
        client.post(f"/api/v1/sessions/{sid}/configure/remediations",
                    json={"remediations": {"duplicates": "duplicates_keep_last"}},
                    headers=auth_headers)

        cfg = session_store.get_field(test_tenant["id"], sid, "columns_cfg")
        assert cfg["remediations"] == {"duplicates": "duplicates_keep_last"}
        assert runner._remediation_choices(cfg) == {"duplicates": "duplicates_keep_last"}

    def test_an_option_that_answers_nothing_is_refused(self, client, auth_headers,
                                                       test_tenant):
        """A stale code would sit in the config looking like a decision."""
        sid = _session_with(client, auth_headers, PER_TRANSACTION)
        r = client.post(f"/api/v1/sessions/{sid}/configure/remediations",
                        json={"remediations": {"duplicates": "gaps_fill_zero"}},
                        headers=auth_headers)
        assert r.status_code == 422, r.text
        assert r.json()["error_code"] == "remediation_not_offered"
        cfg = session_store.get_field(test_tenant["id"], sid, "columns_cfg") or {}
        assert not cfg.get("remediations")


class TestPermissions:
    def test_a_viewer_cannot_record_a_choice(self, client, auth_headers,
                                             viewer_headers, test_tenant):
        sid = _session_with(client, auth_headers, PER_TRANSACTION)
        r = client.post(f"/api/v1/sessions/{sid}/configure/remediations",
                        json={"remediations": {"duplicates": "duplicates_sum"}},
                        headers=viewer_headers)
        assert r.status_code == 403, r.text
        cfg = session_store.get_field(test_tenant["id"], sid, "columns_cfg") or {}
        assert not cfg.get("remediations"), "a denied request must change nothing"

    def test_an_analyst_can(self, client, auth_headers, analyst_headers, test_tenant):
        sid = _session_with(client, auth_headers, PER_TRANSACTION)
        r = client.post(f"/api/v1/sessions/{sid}/configure/remediations",
                        json={"remediations": {"duplicates": "duplicates_sum"}},
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        cfg = session_store.get_field(test_tenant["id"], sid, "columns_cfg")
        assert cfg["remediations"]["duplicates"] == "duplicates_sum"

    def test_a_viewer_can_read_the_gate(self, client, auth_headers, viewer_headers):
        """Seeing the dead end is a read; only deciding is a mutation."""
        sid = _session_with(client, auth_headers, ALL_ZERO)
        r = client.get(f"/api/v1/sessions/{sid}/data-gate", headers=viewer_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["outcome"] == "blocked_fatal"

    def test_no_token_is_refused(self, client, auth_headers):
        sid = _session_with(client, auth_headers, ALL_ZERO)
        assert client.get(f"/api/v1/sessions/{sid}/data-gate").status_code in (401, 403)


# ── 4. Every option actually does what it says ─────────────────────────────
#
# Each test below asserts the CONSEQUENCE the option states, not that the
# function ran. The numbers are the point: `duplicates_keep_last` on three
# transactions of 4+3+3 has to yield 3, and `duplicates_sum` has to yield 10 —
# that difference is a third of a distributor's demand.

def _tx(day: str, sku: str, qty) -> dict:
    return {"fecha": day, "sku": sku, "cantidad": qty}


class TestTheOptionsChangeTheDataAsClaimed:
    def test_duplicates_sum_gives_the_days_total(self):
        df = pd.DataFrame([_tx("2025-01-01", "A", 4), _tx("2025-01-01", "A", 3),
                           _tx("2025-01-01", "A", 3)])
        out = runner._collapse_duplicate_periods(df, "fecha", "cantidad", ["sku"])
        assert len(out) == 1
        assert out["cantidad"].iloc[0] == 10

    def test_duplicates_keep_last_gives_the_correction(self):
        df = pd.DataFrame([_tx("2025-01-01", "A", 4), _tx("2025-01-01", "A", 3),
                           _tx("2025-01-01", "A", 3)])
        out = runner._dedupe_keep_last(df, "fecha", ["sku"])
        assert len(out) == 1
        assert out["cantidad"].iloc[0] == 3

    def test_negatives_net_subtracts_the_return_from_that_day(self):
        df = pd.DataFrame([_tx("2025-01-01", "A", 10), _tx("2025-01-01", "A", -3),
                           _tx("2025-01-02", "A", 8)])
        out = runner._apply_negative_policy(df, "fecha", "cantidad", ["sku"], "net")
        totals = out.set_index("fecha")["cantidad"].to_dict()
        assert totals["2025-01-01"] == 7      # 10 - 3
        assert totals["2025-01-02"] == 8      # untouched

    def test_negatives_net_floors_a_day_that_returned_more_than_it_sold(self):
        df = pd.DataFrame([_tx("2025-01-01", "A", 2), _tx("2025-01-01", "A", -9)])
        out = runner._apply_negative_policy(df, "fecha", "cantidad", ["sku"], "net")
        assert out["cantidad"].iloc[0] == 0

    def test_negatives_as_zero_makes_demand_read_higher_than_it_was(self):
        df = pd.DataFrame([_tx("2025-01-01", "A", 10), _tx("2025-01-01", "A", -3)])
        out = runner._apply_negative_policy(df, "fecha", "cantidad", ["sku"], "zero")
        assert out["cantidad"].sum() == 10    # the return is gone, not subtracted
        assert len(out) == 2                  # and no row was lost

    def test_negatives_drop_loses_exactly_those_rows(self):
        df = pd.DataFrame([_tx("2025-01-01", "A", 10), _tx("2025-01-02", "A", -3),
                           _tx("2025-01-03", "A", 8)])
        out = runner._apply_negative_policy(df, "fecha", "cantidad", ["sku"], "drop")
        assert len(out) == 2
        assert (out["cantidad"] >= 0).all()

    def test_negatives_net_leaves_days_without_a_return_alone(self):
        """So a `keep_last` choice still governs every other duplicated day."""
        df = pd.DataFrame([_tx("2025-01-01", "A", 4), _tx("2025-01-01", "A", 3),
                           _tx("2025-01-02", "A", 5), _tx("2025-01-02", "A", -2)])
        out = runner._apply_negative_policy(df, "fecha", "cantidad", ["sku"], "net")
        assert len(out[out["fecha"] == "2025-01-01"]) == 2   # untouched
        assert len(out[out["fecha"] == "2025-01-02"]) == 1   # netted to 3

    def test_day_first_and_month_first_produce_different_months(self):
        df = pd.DataFrame([{"fecha": "03/04/2026"}])
        day = runner._apply_date_format(df, "fecha", dayfirst=True)
        month = runner._apply_date_format(df, "fecha", dayfirst=False)
        assert day["fecha"].iloc[0].month == 4
        assert month["fecha"].iloc[0].month == 3

    def test_the_two_number_conventions_are_a_thousand_apart(self):
        df = pd.DataFrame({"cantidad": ["1,234", "2,500"]})
        thousands = runner._coerce_decimal_comma(df, "cantidad", convention="thousands")
        decimal = runner._coerce_decimal_comma(df, "cantidad", convention="decimal")
        assert thousands["cantidad"].iloc[0] == 1234
        assert decimal["cantidad"].iloc[0] == 1.234

    def test_without_an_answer_the_ambiguous_column_is_still_left_alone(self):
        """Refusing to guess stays the default — a silent 1000x is the worst outcome."""
        df = pd.DataFrame({"cantidad": ["1,234"]})
        assert runner._coerce_decimal_comma(df, "cantidad")["cantidad"].iloc[0] == "1,234"

    def test_decumulating_recovers_the_per_period_quantity(self):
        rows = []
        total = 0
        for i, qty in enumerate([5, 7, 3, 9]):
            total += qty
            rows.append(_tx((date(2025, 1, 1) + timedelta(days=i)).isoformat(), "A", total))
        out = runner._decumulate(pd.DataFrame(rows), "fecha", "cantidad", ["sku"])
        assert out["cantidad"].tolist() == [7, 3, 9]   # first period has no predecessor

    def test_decumulating_floors_a_counter_that_reset(self):
        rows = [_tx((date(2025, 1, 1) + timedelta(days=i)).isoformat(), "A", v)
                for i, v in enumerate([10, 25, 4, 11])]
        out = runner._decumulate(pd.DataFrame(rows), "fecha", "cantidad", ["sku"])
        assert out["cantidad"].tolist() == [15, 0, 7]

    def test_dropping_impossible_dates_shortens_the_history_as_stated(self):
        rows = [_tx("1900-01-01", "A", 5)] + [
            _tx((date(2025, 1, 1) + timedelta(days=i)).isoformat(), "A", 5)
            for i in range(10)]
        df = pd.DataFrame(rows)
        out = runner._drop_out_of_range_dates(df, "fecha")
        assert len(out) == len(df) - 1
        assert pd.to_datetime(out["fecha"]).min().year == 2025

    def test_excel_serials_become_the_dates_the_option_promised(self):
        out = runner._apply_excel_serial_dates(pd.DataFrame({"dia": [45000]}), "dia")
        assert str(out["dia"].iloc[0].date()) == "2023-03-15"

    def test_unifying_sku_identity_merges_the_series(self):
        df = pd.DataFrame([_tx("2025-01-01", "SKU-1", 3), _tx("2025-01-02", " sku-1 ", 4),
                           _tx("2025-01-03", "SKU-2", 5)])
        out = runner._unify_group_values(df, ["sku"])
        assert out["sku"].nunique() == 2

    def test_keeping_them_separate_leaves_two_half_length_series(self):
        df = pd.DataFrame([_tx("2025-01-01", "SKU-1", 3), _tx("2025-01-02", " sku-1 ", 4)])
        assert df["sku"].nunique() == 2   # the do-nothing option, and its cost

    def test_stripping_symbols_recovers_the_number(self):
        df = pd.DataFrame({"cantidad": ["$10", "10 kg", "7", "N/D"]})
        out = runner._apply_non_numeric_policy(df, "cantidad", "strip")
        assert out["cantidad"].tolist()[:3] == [10.0, 10.0, 7.0]
        assert pd.isna(out["cantidad"].iloc[3])

    def test_reading_them_as_zero_understates_demand(self):
        df = pd.DataFrame({"cantidad": ["$10", "7"]})
        out = runner._apply_non_numeric_policy(df, "cantidad", "zero")
        assert out["cantidad"].tolist() == [0.0, 7.0]

    def test_dropping_them_loses_exactly_those_rows(self):
        df = pd.DataFrame({"cantidad": ["$10", "7", "N/D"]})
        out = runner._apply_non_numeric_policy(df, "cantidad", "drop")
        assert out["cantidad"].tolist() == [7.0]

    def test_blank_quantities_as_zero_versus_dropped(self):
        df = pd.DataFrame({"cantidad": [5, None, 7]})
        assert runner._apply_null_target_policy(df, "cantidad", "zero")["cantidad"].tolist() \
            == [5.0, 0.0, 7.0]
        assert len(runner._apply_null_target_policy(df, "cantidad", "drop")) == 2

    def test_gap_strategies_assert_different_things_about_the_hole(self):
        """Jan 4 and 5 are missing. Each strategy claims something different
        about what happened on those two days — that claim is the choice."""
        df = pd.DataFrame([_tx("2025-01-01", "A", 10), _tx("2025-01-02", "A", 20),
                           _tx("2025-01-03", "A", 20), _tx("2025-01-06", "A", 30),
                           _tx("2025-01-07", "A", 30)])

        def hole(strategy):
            out = runner._apply_gap_fill(df, "fecha", "cantidad", ["sku"], strategy)
            out = out.sort_values("fecha")
            missing = out["fecha"].astype(str).str.startswith(("2025-01-04", "2025-01-05"))
            return out.loc[missing, "cantidad"].tolist()

        assert hole("zero") == [0.0, 0.0]        # nothing was sold
        assert hole("forward") == [20.0, 20.0]   # the last value, repeated
        interpolated = hole("interpolate")
        assert interpolated not in ([0.0, 0.0], [20.0, 20.0])   # never observed
        assert 20 < interpolated[0] < 30


class TestNoOptionIsOfferedWithoutAnImplementation:
    def test_every_option_code_is_wired_into_the_runner(self):
        """The rule as a test: an option that changes nothing is worse than none.

        Three preps are exempt and NAMED as exempt in the catalogue rather than
        guessed at here — `no_change`, `confirm_only`, `remap_required`. That
        distinction is the whole point: "does nothing on purpose" and "nobody
        wired it up" look identical from the outside, and only one of them is a
        decision the user actually made.
        """
        import pathlib

        runner_source = pathlib.Path(runner.__file__).read_text(encoding="utf-8")
        unwired = sorted(
            code for code, meta in gate.OPTION_EFFECTS.items()
            if meta["prep"] not in gate.NON_TRANSFORMING_PREPS
            and code not in runner_source
        )
        assert unwired == [], f"offered but never applied: {unwired}"

    def test_the_exemption_list_cannot_be_used_to_smuggle_a_dead_option(self):
        """Only the four 'leave it as it is' answers may claim `no_change`."""
        assert {c for c, m in gate.OPTION_EFFECTS.items() if m["prep"] == "no_change"} == {
            "cumulative_keep", "out_of_range_dates_keep",
            "excel_serial_as_number", "sku_identity_keep_separate",
        }

    def test_choosing_remap_keeps_the_gate_closed(self, client, auth_headers,
                                                  test_tenant):
        """It is not a transformation: it is a promise to change the mapping."""
        # Money-shaped and deliberately NOT monotone: a running total is a
        # different finding, and mixing them would make this test pass for the
        # wrong reason.
        money = _csv([f"SKU-{i % 3},{(date(2025, 1, 1) + timedelta(days=i)).isoformat()},"
                      f"{round(1500 + (i % 7) * 113.37 + (i % 3) * 47.11, 2)}"
                      for i in range(60)])
        sid = _session_with(client, auth_headers, money)
        _make_trainable(test_tenant["id"], sid)
        data = client.get(f"/api/v1/sessions/{sid}/data-gate",
                          headers=auth_headers).json()["data"]
        assert "target_looks_like_money" in data["blocking_fixable"]

        client.post(f"/api/v1/sessions/{sid}/configure/remediations",
                    json={"remediations": {"target_looks_like_money": "target_is_units"}},
                    headers=auth_headers)
        # Affirming it IS units clears the gate; nothing about the data changed,
        # and that is exactly what the user asserted.
        r = client.post(f"/api/v1/sessions/{sid}/train", headers=auth_headers)
        assert r.status_code == 202, r.text
