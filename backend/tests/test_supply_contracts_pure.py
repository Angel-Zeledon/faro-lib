"""Blanket supply contracts: the pure half (backend/inventory/supply_contract_service.py).

No database: schedule expansion, validation, what to materialise, fulfilment
progress and the warehouse-scope rule, each pinned by hand-computed numbers.
"""

from datetime import date, timedelta

import pytest

from backend.errors import AppError
from backend.inventory import supply_contract_service as svc

TODAY = date(2026, 10, 5)


def _raw(**over):
    raw = {
        "customer": "Big Corp",
        "lines": [{"sku": "X", "total_quantity": 120000}],
        "period_start": "2026-01-15",
        "period_end": "2026-12-31",
        "schedule_kind": "monthly",
        "tolerance_pct": 5,
    }
    raw.update(over)
    return raw


def _code(**over):
    with pytest.raises(AppError) as e:
        svc.clean_contract(_raw(**over), today=TODAY)
    return e.value


# ── Schedule ─────────────────────────────────────────────────────────────────

class TestSchedule:

    def test_monthly_dates_clamp_to_the_last_day_of_short_months(self):
        dates = svc.release_dates("monthly", date(2026, 1, 31), date(2026, 5, 31))
        assert dates == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
                         date(2026, 4, 30), date(2026, 5, 31)]

    def test_weekly_dates_step_seven_days_and_include_the_end(self):
        dates = svc.release_dates("weekly", date(2026, 3, 2), date(2026, 3, 23))
        assert dates == [date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16), date(2026, 3, 23)]

    def test_whole_units_split_evenly_and_add_up(self):
        assert svc.split_evenly(120000, 12) == [10000.0] * 12
        parts = svc.split_evenly(120, 7)
        assert parts == [18.0, 17.0, 17.0, 17.0, 17.0, 17.0, 17.0]
        assert sum(parts) == 120

    def test_fractional_total_adds_up_exactly(self):
        parts = svc.split_evenly(100.5, 3)
        assert parts == [33.5, 33.5, 33.5]
        parts = svc.split_evenly(10.1, 3)
        assert sum(parts) == pytest.approx(10.1, abs=1e-9)
        assert all(p > 0 for p in parts)

    def test_even_monthly_contract_expands_to_twelve_equal_releases(self):
        c = svc.clean_contract(_raw(), today=TODAY)
        rel = svc.expand_releases(c)
        assert len(rel) == 12
        assert {r["quantity"] for r in rel} == {10000.0}
        assert rel[0]["date"] == date(2026, 1, 15) and rel[-1]["date"] == date(2026, 12, 15)
        assert sum(r["quantity"] for r in rel) == 120000

    def test_fewer_units_than_dates_leaves_no_zero_release(self):
        c = svc.clean_contract(_raw(lines=[{"sku": "X", "total_quantity": 5}]), today=TODAY)
        rel = svc.expand_releases(c)
        assert [r["quantity"] for r in rel] == [1.0] * 5
        assert sum(r["quantity"] for r in rel) == 5

    def test_two_lines_sorted_by_date_then_sku(self):
        c = svc.clean_contract(_raw(
            lines=[{"sku": "B", "total_quantity": 20}, {"sku": "A", "total_quantity": 40}],
            period_start="2026-01-01", period_end="2026-02-01"), today=TODAY)
        rel = svc.expand_releases(c)
        assert [(r["date"].isoformat(), r["sku"], r["quantity"]) for r in rel] == [
            ("2026-01-01", "A", 20.0), ("2026-01-01", "B", 10.0),
            ("2026-02-01", "A", 20.0), ("2026-02-01", "B", 10.0)]

    def test_explicit_schedule_is_taken_as_written_and_defines_the_total(self):
        c = svc.clean_contract(_raw(
            lines=[{"sku": "X"}], schedule_kind="explicit",
            releases=[{"date": "2026-03-01", "quantity": 300},
                      {"date": "2026-02-01", "quantity": 200}]), today=TODAY)
        assert c["lines"][0]["total_quantity"] == 500
        rel = svc.expand_releases(c)
        assert [(r["date"], r["quantity"]) for r in rel] == [
            (date(2026, 2, 1), 200.0), (date(2026, 3, 1), 300.0)]


# ── Validation ───────────────────────────────────────────────────────────────

class TestValidation:

    def test_a_release_without_a_quantity_is_refused_with_its_row(self):
        e = _code(schedule_kind="explicit", lines=[{"sku": "X"}],
                  releases=[{"date": "2026-02-01", "quantity": 10},
                            {"date": "2026-03-01", "quantity": None},
                            {"date": "2026-04-01", "quantity": ""}])
        assert e.code == "supply_contract_releases_invalid"
        assert e.params["bad_rows"] == 2
        assert [(x["row"], x["code"]) for x in e.params["errors"]] == [
            (2, "supply_contract_release_quantity_missing"),
            (3, "supply_contract_release_quantity_missing")]

    def test_every_bad_pasted_row_is_named_at_once(self):
        e = _code(schedule_kind="explicit",
                  lines=[{"sku": "X"}, {"sku": "Y"}],
                  releases=[{"sku": "X", "date": "not a date", "quantity": 1},
                            {"sku": "X", "date": "2026-02-01", "quantity": -4},
                            {"date": "2026-02-01", "quantity": 4},
                            {"sku": "Z", "date": "2026-02-01", "quantity": 4},
                            {"sku": "X", "date": "2027-06-01", "quantity": 4},
                            {"sku": "Y", "date": "2026-05-01", "quantity": 4},
                            {"sku": "Y", "date": "2026-05-01", "quantity": 4}])
        codes = [(x["row"], x["code"]) for x in e.params["errors"]]
        assert codes == [
            (1, "supply_contract_release_date_invalid"),
            (2, "supply_contract_release_quantity_invalid"),
            (3, "supply_contract_release_sku_required"),
            (4, "supply_contract_release_sku_unknown"),
            (5, "supply_contract_release_out_of_period"),
            (7, "supply_contract_release_duplicate"),
        ]

    def test_explicit_releases_must_add_up_to_a_stated_total(self):
        e = _code(schedule_kind="explicit", lines=[{"sku": "X", "total_quantity": 1000}],
                  releases=[{"date": "2026-02-01", "quantity": 400},
                            {"date": "2026-03-01", "quantity": 500}])
        assert e.code == "supply_contract_schedule_total_mismatch"
        assert e.params == {"sku": "X", "total": 1000, "scheduled": 900}

    def test_a_line_with_no_release_is_refused(self):
        e = _code(schedule_kind="explicit", lines=[{"sku": "X"}, {"sku": "Y"}],
                  releases=[{"sku": "X", "date": "2026-02-01", "quantity": 4}])
        assert e.code == "supply_contract_line_without_releases"

    def test_an_even_schedule_needs_a_total(self):
        assert _code(lines=[{"sku": "X"}]).code == "supply_contract_line_quantity_invalid"

    @pytest.mark.parametrize("over,code", [
        ({"customer": "  "}, "supply_contract_customer_required"),
        ({"lines": []}, "supply_contract_lines_required"),
        ({"lines": [{"sku": "X", "total_quantity": 1}, {"sku": "X", "total_quantity": 2}]},
         "supply_contract_line_duplicate"),
        ({"lines": [{"sku": "", "total_quantity": 1}]}, "supply_contract_line_sku_required"),
        ({"lines": [{"sku": "X", "total_quantity": 0}]}, "supply_contract_line_quantity_invalid"),
        ({"lines": [{"sku": "X", "total_quantity": 1, "unit_price": -1}]},
         "supply_contract_line_price_invalid"),
        ({"period_end": "2025-01-01"}, "supply_contract_period_invalid"),
        ({"period_end": "2040-01-01"}, "supply_contract_period_too_long"),
        ({"period_start": "15/01/2026"}, "date_invalid_iso"),
        ({"schedule_kind": "daily"}, "supply_contract_schedule_invalid"),
        ({"tolerance_pct": 101}, "supply_contract_tolerance_invalid"),
        ({"schedule_kind": "explicit", "releases": []}, "supply_contract_releases_required"),
    ])
    def test_refusals_carry_their_codes(self, over, code):
        assert _code(**over).code == code

    def test_too_many_releases_is_refused(self):
        lines = [{"sku": f"S{i}", "total_quantity": 1000} for i in range(10)]
        e = _code(lines=lines, schedule_kind="weekly",
                  period_start="2026-01-01", period_end="2029-12-31")
        assert e.code == "supply_contract_too_many_releases"

    def test_defaults(self):
        c = svc.clean_contract(_raw(tolerance_pct=None, note="  ", reference=""), today=TODAY)
        assert c["tolerance_pct"] == 0.0 and c["note"] is None and c["reference"] is None
        assert c["on_top_of_base"] is True and c["warehouse_id"] is None


# ── Materialisation plan ─────────────────────────────────────────────────────

class TestMaterialisationPlan:

    def _rel(self, *days):
        return [{"sku": "X", "date": TODAY + timedelta(days=d), "quantity": 10.0} for d in days]

    def test_takes_past_and_near_releases_not_far_ones(self):
        rel = self._rel(-40, 0, 30, 180, 181)
        plan = svc.releases_to_materialise(rel, set(), TODAY, 180)
        assert [r["date"] - TODAY for r in plan] == [timedelta(days=d) for d in (-40, 0, 30, 180)]

    def test_idempotent_existing_keys_are_never_planned_again(self):
        rel = self._rel(0, 30, 60)
        first = svc.releases_to_materialise(rel, set(), TODAY, 180)
        held = {(r["sku"], r["date"]) for r in first}
        assert svc.releases_to_materialise(rel, held, TODAY, 180) == []

    def test_horizon_follows_the_longest_lead_time(self):
        assert svc.materialise_horizon_days(None) == 180
        assert svc.materialise_horizon_days(30) == 180
        assert svc.materialise_horizon_days(150) == 210        # 150 + 60 review margin
        assert svc.materialise_horizon_days(5000) == svc.MAX_HORIZON_DAYS


# ── Fulfilment ───────────────────────────────────────────────────────────────

def _releases(n=12, qty=100.0, start=date(2026, 1, 15)):
    return [{"sku": "X", "date": svc._add_months(start, k), "quantity": qty} for k in range(n)]


def _commit(d, status, qty=100.0, sku="X"):
    return {"sku": sku, "contract_release_date": d, "quantity": qty, "status": status}


class TestProgress:

    def test_on_pace_contract(self):
        rel = _releases()
        # Jan..Sep due (9 releases <= Oct 5); all fulfilled; Oct open.
        com = [_commit(r["date"], "fulfilled") for r in rel[:9]] + [_commit(rel[9]["date"], "open")]
        p = svc.contract_progress(rel, com, TODAY, 5, 180, "active")
        assert p["scheduled_total"] == 1200 and p["due_to_date"] == 900
        assert p["delivered"] == 900 and p["remaining"] == 300
        assert p["progress_pct"] == 75.0
        assert p["behind_schedule"] is False and p["overdue_count"] == 0
        assert p["projected_shortfall"] == 0 and p["shortfall_beyond_tolerance"] is False
        assert p["next_release"]["date"] == "2026-10-15"

    def test_overdue_releases_are_listed_not_hidden(self):
        rel = _releases()
        com = ([_commit(r["date"], "fulfilled") for r in rel[:6]]
               + [_commit(rel[6]["date"], "open")])          # Jul open; Aug, Sep never materialised
        p = svc.contract_progress(rel, com, TODAY, 0, 180, "active")
        overdue = [r for r in p["releases"] if r["overdue"]]
        assert [(r["date"], r["state"]) for r in overdue] == [
            ("2026-07-15", "open"), ("2026-08-15", "missing"), ("2026-09-15", "missing")]
        assert p["overdue_count"] == 3 and p["overdue_units"] == 300
        assert p["behind_schedule"] is True
        # 600 delivered of 900 due -> pace 2/3; 300 future units at that pace -> 200.
        assert p["projected_delivered"] == pytest.approx(800)
        assert p["projected_shortfall"] == pytest.approx(400)
        assert p["shortfall_beyond_tolerance"] is True
        # Oct..Dec are inside the 180-day horizon and missing too.
        assert p["unmaterialised_due"] == 5

    def test_tolerance_absorbs_a_small_shortfall(self):
        rel = _releases(n=10)
        com = [_commit(r["date"], "fulfilled", qty=96.0) for r in rel[:9]]   # 864 of 900
        assert svc.contract_progress(rel, com, TODAY, 5, 180, "active")["behind_schedule"] is False
        assert svc.contract_progress(rel, com, TODAY, 3, 180, "active")["behind_schedule"] is True

    def test_nothing_due_yet_means_no_projection(self):
        rel = _releases(start=date(2026, 11, 1))
        p = svc.contract_progress(rel, [], TODAY, 5, 180, "active")
        assert p["due_to_date"] == 0
        assert p["projected_shortfall"] is None and p["shortfall_beyond_tolerance"] is None
        assert p["behind_schedule"] is False and p["overdue_count"] == 0

    def test_a_draft_is_never_judged(self):
        rel = _releases()
        p = svc.contract_progress(rel, [], TODAY, 0, 180, "draft")
        assert p["overdue_count"] == 0 and p["behind_schedule"] is False
        assert p["unmaterialised_due"] == 0 and p["projected_shortfall"] is None

    def test_an_ended_contract_reports_its_final_shortfall(self):
        rel = _releases(n=3)
        com = [_commit(rel[0]["date"], "fulfilled"), _commit(rel[1]["date"], "cancelled")]
        p = svc.contract_progress(rel, com, TODAY, 0, 180, "cancelled")
        assert p["delivered"] == 100 and p["projected_shortfall"] == 200
        assert p["overdue_count"] == 0

    def test_far_releases_are_scheduled_not_missing(self):
        rel = [{"sku": "X", "date": TODAY + timedelta(days=400), "quantity": 5.0}]
        p = svc.contract_progress(rel, [], TODAY, 0, 180, "active")
        assert p["releases"][0]["state"] == "scheduled" and p["unmaterialised_due"] == 0

    def test_per_line_figures(self):
        rel = ([{"sku": "A", "date": date(2026, 9, 1), "quantity": 10.0}]
               + [{"sku": "B", "date": date(2026, 9, 1), "quantity": 50.0}])
        com = [_commit(date(2026, 9, 1), "fulfilled", 10.0, "A"),
               _commit(date(2026, 9, 1), "open", 50.0, "B")]
        p = svc.contract_progress(rel, com, TODAY, 0, 180, "active")
        lines = {ln["sku"]: ln for ln in p["lines"]}
        assert lines["A"]["behind_schedule"] is False and lines["A"]["remaining"] == 0
        assert lines["B"]["behind_schedule"] is True and lines["B"]["remaining"] == 50
        assert p["behind_schedule"] is True


# ── Warehouse scope ──────────────────────────────────────────────────────────

class TestScope:

    def test_unrestricted_sees_everything(self):
        assert svc.visible(None, None) and svc.visible(None, "Norte")
        svc.require_writable(None, None)

    def test_scoped_sees_only_its_warehouses_and_never_company_wide(self):
        scope = frozenset({"Norte"})
        assert svc.visible(scope, "norte ") is True
        assert svc.visible(scope, "Sur") is False
        assert svc.visible(scope, None) is False
        assert svc.visible(frozenset(), "Norte") is False

    def test_scoped_writes_are_refused_outside_and_company_wide(self):
        scope = frozenset({"Norte"})
        svc.require_writable(scope, "Norte")
        with pytest.raises(AppError) as e:
            svc.require_writable(scope, "Sur")
        assert (e.value.code, e.value.status_code) == ("warehouse_out_of_scope", 403)
        with pytest.raises(AppError) as e:
            svc.require_writable(scope, None)
        assert (e.value.code, e.value.status_code) == ("supply_contract_scope_company_wide", 403)
