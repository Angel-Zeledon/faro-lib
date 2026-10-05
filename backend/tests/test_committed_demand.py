"""Committed demand: customer orders placed months ahead (backend/inventory/committed_demand_service.py).

Two groups. The pure classes (`TestCommittedUnitsPure`, `TestExtraRatePure`,
`TestCleanValidationPure`) use no DB fixture and pin the arithmetic by hand. The
DB classes pin the endpoints, the all-or-nothing bulk import, tenant isolation
and the effect on the purchase recommendation with direct read-backs.
"""

import math
from datetime import date, timedelta
from uuid import uuid4

import pytest

from backend.db import session_store
from backend.db.connection import query, query_one
from backend.errors import AppError
from backend.inventory import committed_demand_service as svc
from backend.inventory import service as inv_svc
from backend.sessions.service import create_session

TODAY = date(2026, 10, 5)


def _c(units=100.0, days=10, prob=1.0, wh=None, cid=None, customer="ACME"):
    return {"id": cid or uuid4().hex[:6], "delivery_date": TODAY + timedelta(days=days),
            "quantity": units, "probability": prob, "warehouse_id": wh, "customer": customer}


# ── Pure: committed_units ────────────────────────────────────────────────────

class TestCommittedUnitsPure:

    def test_sums_quantity_times_probability_inside_the_window(self):
        total, applied = svc.committed_units(
            [_c(100, days=5, prob=1.0), _c(200, days=19, prob=0.5)], TODAY, 20)
        assert total == pytest.approx(200.0)          # 100 + 200*0.5
        assert [a["units"] for a in applied] == [100.0, 100.0]
        assert applied[1]["probability"] == 0.5 and applied[1]["quantity"] == 200.0

    def test_window_is_half_open_today_in_end_out(self):
        on_today = _c(10, days=0, cid="t")
        last_day = _c(20, days=19, cid="last")
        on_end = _c(40, days=20, cid="end")           # today + window_days: excluded
        beyond = _c(80, days=21, cid="far")
        total, applied = svc.committed_units([on_today, last_day, on_end, beyond], TODAY, 20)
        assert total == 30.0
        assert {a["commitment_id"] for a in applied} == {"t", "last"}

    def test_overdue_counts_and_is_flagged(self):
        total, applied = svc.committed_units(
            [_c(70, days=-3, cid="late"), _c(30, days=2, cid="ok")], TODAY, 10)
        assert total == 100.0
        flags = {a["commitment_id"]: a["overdue"] for a in applied}
        assert flags == {"late": True, "ok": False}

    def test_a_commitment_due_today_is_not_overdue(self):
        _, applied = svc.committed_units([_c(5, days=0)], TODAY, 10)
        assert applied[0]["overdue"] is False

    @pytest.mark.parametrize("window", [0, -5, -0.1])
    def test_non_positive_window_is_neutral(self, window):
        assert svc.committed_units([_c(100, days=0)], TODAY, window) == (0.0, [])

    @pytest.mark.parametrize("empty", [None, []])
    def test_no_commitments_is_neutral(self, empty):
        assert svc.committed_units(empty, TODAY, 30) == (0.0, [])

    def test_fractional_window_end_is_respected(self):
        # window 10.5 days -> end = today + 10 days (timedelta keeps the half day);
        # a delivery on day 10 is still before the end date at midnight? date + 10.5d
        # truncates to day 10 for a date, so day 10 is excluded and day 9 is in.
        total, _ = svc.committed_units([_c(1, days=9), _c(2, days=10)], TODAY, 10.5)
        assert total == 1.0

    def test_company_view_counts_everything_in_full(self):
        total, applied = svc.committed_units(
            [_c(100, wh="W1", cid="a"), _c(50, wh="W2", cid="b"), _c(30, wh=None, cid="c")],
            TODAY, 30)
        assert total == 180.0
        scopes = {a["commitment_id"]: a["scope"] for a in applied}
        assert scopes == {"a": "warehouse", "b": "warehouse", "c": "company"}

    def test_warehouse_view_own_in_full_other_not_at_all_unassigned_by_share(self):
        rows = [_c(100, wh="W1", cid="mine"), _c(500, wh="W2", cid="theirs"),
                _c(40, wh=None, cid="shared")]
        total, applied = svc.committed_units(rows, TODAY, 30, warehouse_id="W1", share=0.25)
        assert total == pytest.approx(100 + 40 * 0.25)
        by_id = {a["commitment_id"]: a for a in applied}
        assert set(by_id) == {"mine", "shared"}       # the other warehouse's is absent
        assert by_id["mine"]["scope"] == "warehouse" and by_id["mine"]["units"] == 100.0
        assert by_id["shared"]["scope"] == "shared" and by_id["shared"]["units"] == 10.0

    def test_probability_multiplies_with_the_share(self):
        total, applied = svc.committed_units(
            [_c(200, prob=0.5, wh=None)], TODAY, 30, warehouse_id="W1", share=0.5)
        assert total == pytest.approx(50.0) and applied[0]["units"] == 50.0

    def test_zero_share_drops_unassigned_but_keeps_named(self):
        total, applied = svc.committed_units(
            [_c(40, wh=None, cid="u"), _c(60, wh="W1", cid="n")], TODAY, 30,
            warehouse_id="W1", share=0.0)
        assert total == 60.0 and [a["commitment_id"] for a in applied] == ["n"]

    @pytest.mark.parametrize("share,expected", [(7.0, 40.0), (-3.0, 0.0)])
    def test_share_is_clamped_between_zero_and_one(self, share, expected):
        total, _ = svc.committed_units([_c(40, wh=None)], TODAY, 30, warehouse_id="W1",
                                       share=share)
        assert total == expected

    def test_applied_entry_names_customer_and_date(self):
        _, applied = svc.committed_units([_c(10, days=4, customer="Big Corp", cid="x")],
                                         TODAY, 30)
        assert applied[0]["customer"] == "Big Corp"
        assert applied[0]["delivery_date"] == (TODAY + timedelta(days=4)).isoformat()
        assert applied[0]["commitment_id"] == "x"


# ── Pure: extra_rate ─────────────────────────────────────────────────────────

class TestExtraRatePure:

    def test_by_hand(self):
        assert svc.extra_rate(100.0, 20.0) == 5.0
        assert svc.extra_rate(30.0, 4.0) == 7.5

    @pytest.mark.parametrize("units,periods", [
        (0.0, 10.0), (-5.0, 10.0), (100.0, 0.0), (100.0, -2.0), (0.0, 0.0)])
    def test_neutral_inputs_return_zero(self, units, periods):
        assert svc.extra_rate(units, periods) == 0.0

    @pytest.mark.parametrize("units,periods", [
        (100.0, 20.0), (37.5, 3.0), (1e6, 0.5), (0.01, 1000.0), (4000.0, 10 / 7)])
    def test_rate_times_the_protection_interval_returns_the_units(self, units, periods):
        assert svc.extra_rate(units, periods) * periods == pytest.approx(units, rel=1e-12)

    def test_end_to_end_breakdown_adds_up(self):
        units, applied = svc.committed_units(
            [_c(4000, days=70, prob=0.8), _c(500, days=10)], TODAY, 84)
        rate = svc.extra_rate(units, 12.0)            # 84 days = 12 weekly periods
        assert rate * 12.0 == pytest.approx(sum(a["units"] for a in applied))
        assert units == pytest.approx(4000 * 0.8 + 500)


# ── Pure: _clean validation ──────────────────────────────────────────────────

def _fields(**over):
    base = dict(sku="SKU-1", delivery_date=(TODAY + timedelta(days=30)).isoformat(),
                quantity=10, customer="ACME", probability=1.0, warehouse_id=None,
                on_top_of_base=True, note=None)
    base.update(over)
    return base


def _code(**over):
    with pytest.raises(AppError) as e:
        svc._clean(**_fields(**over), today=TODAY)
    return e.value.code


class TestCleanValidationPure:

    def test_a_valid_row_is_normalised(self):
        c = svc._clean(**_fields(sku="  SKU-9 ", quantity="12.5", customer="  Big  ",
                                 probability=None, warehouse_id="  ", note="  hi  ",
                                 on_top_of_base=None), today=TODAY)
        assert c["sku"] == "SKU-9" and c["quantity"] == 12.5 and c["customer"] == "Big"
        assert c["probability"] == 1.0 and c["warehouse_id"] is None
        assert c["note"] == "hi" and c["on_top_of_base"] is True
        assert c["delivery_date"] == TODAY + timedelta(days=30)

    def test_on_top_of_base_false_is_kept(self):
        assert svc._clean(**_fields(on_top_of_base=False), today=TODAY)["on_top_of_base"] is False

    @pytest.mark.parametrize("q", [0, -1, -0.001, math.nan, math.inf, -math.inf, 1e9 + 1,
                                   "abc", None])
    def test_bad_quantity(self, q):
        assert _code(quantity=q) == "committed_demand_quantity_invalid"

    @pytest.mark.parametrize("p", [0, 0.0, -0.5, 1.0001, 2, math.nan, math.inf, "x"])
    def test_bad_probability(self, p):
        assert _code(probability=p) == "committed_demand_probability_invalid"

    @pytest.mark.parametrize("p", [0.0001, 0.5, 1, 1.0])
    def test_probability_boundaries_accepted(self, p):
        assert svc._clean(**_fields(probability=p), today=TODAY)["probability"] == float(p)

    def test_date_more_than_ten_years_out(self):
        far = TODAY + timedelta(days=365 * 10 + 1)
        assert _code(delivery_date=far.isoformat()) == "committed_demand_date_too_far"
        assert _code(delivery_date="2206-01-01") == "committed_demand_date_too_far"

    def test_date_exactly_ten_years_out_is_accepted(self):
        ok = TODAY + timedelta(days=365 * 10)
        assert svc._clean(**_fields(delivery_date=ok.isoformat()), today=TODAY)[
            "delivery_date"] == ok

    def test_past_dates_are_allowed_because_overdue_still_counts(self):
        past = (TODAY - timedelta(days=40)).isoformat()
        assert svc._clean(**_fields(delivery_date=past), today=TODAY)["delivery_date"] < TODAY

    @pytest.mark.parametrize("d", ["not-a-date", "2026-13-40", "", None])
    def test_unparseable_date(self, d):
        assert _code(delivery_date=d) == "date_invalid_iso"

    @pytest.mark.parametrize("sku", ["", "   ", None])
    def test_empty_sku(self, sku):
        assert _code(sku=sku) == "committed_demand_sku_required"

    def test_long_text_is_truncated_not_refused(self):
        c = svc._clean(**_fields(customer="c" * 500, note="n" * 900), today=TODAY)
        assert len(c["customer"]) == svc.MAX_CUSTOMER_LENGTH
        assert len(c["note"]) == svc.MAX_NOTE_LENGTH


# ── DB: the endpoints ────────────────────────────────────────────────────────
# These need Postgres (test_tenant / registered_user); they cannot run offline.

URL = "/api/v1/committed-demand"


def _body(sku="SKU-A", days=30, quantity=100, **over):
    b = {"sku": sku, "delivery_date": (date.today() + timedelta(days=days)).isoformat(),
         "quantity": quantity, "customer": "ACME", "probability": 0.8}
    b.update(over)
    return b


def _count(tid):
    return query_one("SELECT COUNT(*) AS n FROM committed_demand WHERE tenant_id = %s",
                     (tid,))["n"]


def _make(client, headers, **kw):
    r = client.post(URL, json=_body(**kw), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


class TestCommittedDemandApi:

    def test_create_permission_pair_and_the_stored_row(
            self, client, viewer_headers, analyst_headers, analyst_user, registered_user):
        tid = registered_user["tenant"]["id"]
        denied = client.post(URL, json=_body(), headers=viewer_headers)
        assert denied.status_code == 403
        assert _count(tid) == 0

        ok = client.post(URL, json=_body(), headers=analyst_headers)
        assert ok.status_code == 201, ok.text
        row = query_one("SELECT * FROM committed_demand WHERE tenant_id = %s", (tid,))
        assert row["id"] == ok.json()["data"]["id"]
        assert (row["sku"], row["quantity"], row["probability"], row["status"],
                row["customer"], row["on_top_of_base"]) == (
            "SKU-A", 100.0, 0.8, "open", "ACME", True)
        assert row["created_by"] == analyst_user["user"]["id"]
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'committed_demand.created' AND resource = %s",
                     (tid, row["id"]))

    @pytest.mark.parametrize("change", [
        {"quantity": 0}, {"quantity": -5}, {"probability": 0}, {"probability": 1.5},
        {"sku": ""}])
    def test_invalid_body_is_refused_and_writes_nothing(
            self, client, analyst_headers, registered_user, change):
        r = client.post(URL, json=_body(**change), headers=analyst_headers)
        assert r.status_code == 422
        assert _count(registered_user["tenant"]["id"]) == 0

    def test_service_level_refusals_carry_their_codes(
            self, client, analyst_headers, registered_user):
        far = (date.today() + timedelta(days=365 * 11)).isoformat()
        r = client.post(URL, json=_body(delivery_date=far), headers=analyst_headers)
        assert r.status_code == 422
        assert r.json()["error_code"] == "committed_demand_date_too_far"
        r = client.post(URL, json=_body(warehouse_id="no-such-warehouse"),
                        headers=analyst_headers)
        assert r.status_code == 404
        assert r.json()["error_code"] == "committed_demand_warehouse_unknown"
        assert _count(registered_user["tenant"]["id"]) == 0

    def test_list_filters_by_sku_and_status_and_flags_overdue(
            self, client, analyst_headers, viewer_headers, registered_user):
        a = _make(client, analyst_headers, sku="A", days=-4)       # overdue
        b = _make(client, analyst_headers, sku="B", days=10)
        client.post(f"{URL}/{b['id']}/status", json={"status": "cancelled"},
                    headers=analyst_headers)
        data = client.get(URL, headers=viewer_headers).json()["data"]
        assert {i["id"] for i in data["items"]} == {a["id"], b["id"]}
        flags = {i["id"]: i["overdue"] for i in data["items"]}
        assert flags == {a["id"]: True, b["id"]: False}   # closed is never overdue
        only_a = client.get(URL, params={"sku": "A"}, headers=viewer_headers).json()["data"]
        assert [i["id"] for i in only_a["items"]] == [a["id"]]
        open_only = client.get(URL, params={"status": "open"}, headers=viewer_headers)
        assert [i["id"] for i in open_only.json()["data"]["items"]] == [a["id"]]
        bad = client.get(URL, params={"status": "bogus"}, headers=viewer_headers)
        assert bad.status_code == 422

    def test_patch_permission_pair_and_read_back(
            self, client, viewer_headers, analyst_headers, registered_user):
        created = _make(client, analyst_headers)
        denied = client.patch(f"{URL}/{created['id']}", json={"quantity": 999},
                              headers=viewer_headers)
        assert denied.status_code == 403
        assert query_one("SELECT quantity FROM committed_demand WHERE id = %s",
                         (created["id"],))["quantity"] == 100.0

        ok = client.patch(f"{URL}/{created['id']}",
                          json={"quantity": 250, "probability": 0.5, "note": "revised"},
                          headers=analyst_headers)
        assert ok.status_code == 200, ok.text
        row = query_one("SELECT * FROM committed_demand WHERE id = %s", (created["id"],))
        assert (row["quantity"], row["probability"], row["note"]) == (250.0, 0.5, "revised")
        assert row["sku"] == "SKU-A" and row["customer"] == "ACME"   # untouched fields kept

    def test_patch_with_an_invalid_value_changes_nothing(self, client, analyst_headers):
        created = _make(client, analyst_headers)
        r = client.patch(f"{URL}/{created['id']}", json={"quantity": -1},
                         headers=analyst_headers)
        assert r.status_code == 422
        assert query_one("SELECT quantity FROM committed_demand WHERE id = %s",
                         (created["id"],))["quantity"] == 100.0

    def test_status_permission_pair_and_read_back(
            self, client, viewer_headers, analyst_headers, analyst_user, registered_user):
        tid = registered_user["tenant"]["id"]
        created = _make(client, analyst_headers)
        denied = client.post(f"{URL}/{created['id']}/status", json={"status": "fulfilled"},
                             headers=viewer_headers)
        assert denied.status_code == 403
        assert query_one("SELECT status FROM committed_demand WHERE id = %s",
                         (created["id"],))["status"] == "open"

        ok = client.post(f"{URL}/{created['id']}/status", json={"status": "fulfilled"},
                         headers=analyst_headers)
        assert ok.status_code == 200, ok.text
        row = query_one("SELECT * FROM committed_demand WHERE id = %s", (created["id"],))
        assert row["status"] == "fulfilled"
        assert row["status_changed_by"] == analyst_user["user"]["id"]
        assert row["status_changed_at"] is not None
        assert query("SELECT 1 FROM activity_logs WHERE tenant_id = %s "
                     "AND action = 'committed_demand.changed' AND resource = %s",
                     (tid, created["id"]))

    def test_unknown_status_is_refused(self, client, analyst_headers):
        created = _make(client, analyst_headers)
        r = client.post(f"{URL}/{created['id']}/status", json={"status": "done"},
                        headers=analyst_headers)
        assert r.status_code == 422
        assert query_one("SELECT status FROM committed_demand WHERE id = %s",
                         (created["id"],))["status"] == "open"

    @pytest.mark.parametrize("closed", ["fulfilled", "cancelled"])
    def test_a_closed_commitment_cannot_be_edited_until_reopened(
            self, client, analyst_headers, closed):
        created = _make(client, analyst_headers)
        client.post(f"{URL}/{created['id']}/status", json={"status": closed},
                    headers=analyst_headers)
        r = client.patch(f"{URL}/{created['id']}", json={"quantity": 5},
                         headers=analyst_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "committed_demand_closed"
        assert query_one("SELECT quantity FROM committed_demand WHERE id = %s",
                         (created["id"],))["quantity"] == 100.0
        # reopening makes it editable again
        client.post(f"{URL}/{created['id']}/status", json={"status": "open"},
                    headers=analyst_headers)
        again = client.patch(f"{URL}/{created['id']}", json={"quantity": 5},
                             headers=analyst_headers)
        assert again.status_code == 200
        assert query_one("SELECT quantity FROM committed_demand WHERE id = %s",
                         (created["id"],))["quantity"] == 5.0

    def test_missing_commitment_is_404(self, client, analyst_headers):
        r = client.patch(f"{URL}/{uuid4()}", json={"quantity": 5}, headers=analyst_headers)
        assert r.status_code == 404
        assert r.json()["error_code"] == "committed_demand_not_found"


class TestBulkImportApi:

    def test_permission_pair_and_all_rows_written(
            self, client, viewer_headers, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        rows = [_body(sku=f"S{i}", quantity=10 + i) for i in range(3)]
        denied = client.post(f"{URL}/bulk", json={"rows": rows}, headers=viewer_headers)
        assert denied.status_code == 403
        assert _count(tid) == 0

        ok = client.post(f"{URL}/bulk", json={"rows": rows}, headers=analyst_headers)
        assert ok.status_code == 201, ok.text
        assert ok.json()["data"]["created"] == 3
        stored = query("SELECT sku, quantity FROM committed_demand WHERE tenant_id = %s "
                       "ORDER BY sku", (tid,))
        assert [(r["sku"], r["quantity"]) for r in stored] == [
            ("S0", 10.0), ("S1", 11.0), ("S2", 12.0)]
        assert set(ok.json()["data"]["ids"]) == {r["id"] for r in query(
            "SELECT id FROM committed_demand WHERE tenant_id = %s", (tid,))}

    def test_one_bad_row_writes_zero_rows_and_names_the_row(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        far = (date.today() + timedelta(days=365 * 11)).isoformat()
        rows = [_body(sku="OK1"), _body(sku="BAD", delivery_date=far), _body(sku="OK2")]
        r = client.post(f"{URL}/bulk", json={"rows": rows}, headers=analyst_headers)
        assert r.status_code == 422
        assert r.json()["error_code"] == "committed_demand_bulk_invalid"
        errors = r.json()["error_params"]["errors"]
        assert errors == [{"row": 2, "code": "committed_demand_date_too_far",
                           "params": errors[0]["params"]}]
        assert _count(tid) == 0

    def test_an_unknown_warehouse_in_any_row_writes_zero_rows(
            self, client, analyst_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        rows = [_body(sku="OK1"), _body(sku="OK2", warehouse_id="ghost")]
        r = client.post(f"{URL}/bulk", json={"rows": rows}, headers=analyst_headers)
        assert r.status_code == 404
        assert _count(tid) == 0

    def test_empty_and_oversized_payloads_are_refused(self, client, analyst_headers,
                                                       registered_user):
        assert client.post(f"{URL}/bulk", json={"rows": []},
                           headers=analyst_headers).status_code == 422
        too_many = [_body(sku=f"S{i}") for i in range(svc.MAX_BULK_ROWS + 1)]
        assert client.post(f"{URL}/bulk", json={"rows": too_many},
                           headers=analyst_headers).status_code == 422
        assert _count(registered_user["tenant"]["id"]) == 0


class TestTenantIsolation:

    def test_another_tenant_sees_and_touches_nothing(
            self, client, analyst_headers, make_tenant_user_headers, registered_user):
        created = _make(client, analyst_headers)
        other = make_tenant_user_headers(role="admin")
        assert client.get(URL, headers=other).json()["data"]["items"] == []
        assert client.patch(f"{URL}/{created['id']}", json={"quantity": 1},
                            headers=other).status_code == 404
        assert client.post(f"{URL}/{created['id']}/status", json={"status": "cancelled"},
                           headers=other).status_code == 404
        row = query_one("SELECT quantity, status FROM committed_demand WHERE id = %s",
                        (created["id"],))
        assert (row["quantity"], row["status"]) == (100.0, "open")

    def test_active_by_sku_never_returns_another_tenants_rows(
            self, test_tenant, make_tenant_user_headers):
        _, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        svc.create(other_tid, "u1", sku="SHARED-SKU",
                   delivery_date=date.today() + timedelta(days=5), quantity=10)
        assert svc.active_by_sku(test_tenant["id"]) == {}
        assert list(svc.active_by_sku(other_tid)) == ["SHARED-SKU"]

    def test_active_by_sku_skips_closed_and_already_in_baseline(self, test_tenant):
        tid = test_tenant["id"]
        when = date.today() + timedelta(days=5)
        keep = svc.create(tid, "u1", sku="K", delivery_date=when, quantity=10)
        closed = svc.create(tid, "u1", sku="K", delivery_date=when, quantity=20)
        svc.set_status(tid, closed["id"], "u1", "fulfilled")
        svc.create(tid, "u1", sku="K", delivery_date=when, quantity=30, on_top_of_base=False)
        assert [r["id"] for r in svc.active_by_sku(tid)["K"]] == [keep["id"]]


# ── DB: effect on the purchase recommendation (hand-computed) ────────────────

def _flat_session(tid, sku, per_day=10.0, lead_time=20, stock=50.0):
    sid = create_session(tid, "usr_test", f"cd-{uuid4().hex[:6]}")["id"]
    inv_svc.upsert_stock(tid, sku, {"current_stock": stock, "lead_time_days": lead_time,
                                    "moq": 1.0})
    start = date.today()
    session_store.set_forecasts(tid, sid, {sku: {"lightgbm": {"forecast": [
        {"date": (start + timedelta(days=i)).isoformat(), "value": per_day}
        for i in range(40)]}}})
    return sid


def _row(tid, sid, sku):
    return {i["sku"]: i for i in inv_svc.get_inventory_status(tid, sid)}[sku]


def _commit(tid, sku, days, quantity, probability=1.0, **kw):
    return svc.create(tid, "u1", sku=sku, delivery_date=date.today() + timedelta(days=days),
                      quantity=quantity, probability=probability, **kw)


class TestApplicationToTheRecommendation:

    def test_no_commitment_leaves_every_number_identical(self, test_tenant):
        tid = test_tenant["id"]
        plain, with_other = f"CD-{uuid4().hex[:6]}", f"CD-{uuid4().hex[:6]}"
        sid = _flat_session(tid, plain)
        inv_svc.upsert_stock(tid, with_other, {"current_stock": 50.0, "lead_time_days": 20,
                                               "moq": 1.0})
        base = _row(tid, sid, plain)
        assert base["recommended_qty"] == 150.0            # 10/day * 20 - 50
        assert base["committed_applied"] == []
        # a commitment on ANOTHER sku must not move this one
        _commit(tid, with_other, 5, 999)
        again = _row(tid, sid, plain)
        for key in ("recommended_qty", "daily_demand", "signal", "coverage_days",
                    "reorder_point"):
            assert again.get(key) == base.get(key), key
        assert again["committed_applied"] == []

    def test_an_open_commitment_inside_lead_time_adds_exactly_its_units(self, test_tenant):
        tid = test_tenant["id"]
        sku = f"CD-{uuid4().hex[:6]}"
        sid = _flat_session(tid, sku)
        base = _row(tid, sid, sku)
        c = _commit(tid, sku, days=10, quantity=100, probability=1.0, customer="Big Corp")
        after = _row(tid, sid, sku)
        assert after["recommended_qty"] == pytest.approx(base["recommended_qty"] + 100.0)
        assert after["recommended_qty"] == pytest.approx(250.0)  # 10*20 + 100 - 50
        applied = after["committed_applied"]
        assert len(applied) == 1
        assert applied[0]["commitment_id"] == c["id"]
        assert applied[0]["customer"] == "Big Corp" and applied[0]["units"] == 100.0
        assert after["calc_explanation"]["committed_applied"] == applied
        assert after["daily_demand"] == base["daily_demand"]   # model's own number stays

    def test_probability_scales_the_added_units(self, test_tenant):
        tid = test_tenant["id"]
        sku = f"CD-{uuid4().hex[:6]}"
        sid = _flat_session(tid, sku)
        _commit(tid, sku, days=3, quantity=200, probability=0.5)
        assert _row(tid, sid, sku)["recommended_qty"] == pytest.approx(150.0 + 100.0)

    def test_commitments_outside_the_window_or_closed_or_in_baseline_change_nothing(
            self, test_tenant):
        tid = test_tenant["id"]
        sku = f"CD-{uuid4().hex[:6]}"
        sid = _flat_session(tid, sku)
        _commit(tid, sku, days=20, quantity=500)            # exactly at the window end
        _commit(tid, sku, days=60, quantity=500)            # months ahead
        done = _commit(tid, sku, days=5, quantity=500)
        svc.set_status(tid, done["id"], "u1", "cancelled")
        _commit(tid, sku, days=5, quantity=500, on_top_of_base=False)
        row = _row(tid, sid, sku)
        assert row["recommended_qty"] == 150.0 and row["committed_applied"] == []

    def test_an_overdue_open_commitment_still_counts_and_is_flagged(self, test_tenant):
        tid = test_tenant["id"]
        sku = f"CD-{uuid4().hex[:6]}"
        sid = _flat_session(tid, sku)
        # inserted directly: the API allows past dates, this keeps the setup obvious
        query_one(
            """INSERT INTO committed_demand (tenant_id, sku, delivery_date, quantity, created_by)
               VALUES (%s, %s, %s, 40, 'u1') RETURNING id""",
            (tid, sku, date.today() - timedelta(days=2)))
        row = _row(tid, sid, sku)
        assert row["recommended_qty"] == pytest.approx(190.0)
        assert row["committed_applied"][0]["overdue"] is True

    def test_closing_the_commitment_restores_the_original_recommendation(self, test_tenant):
        tid = test_tenant["id"]
        sku = f"CD-{uuid4().hex[:6]}"
        sid = _flat_session(tid, sku)
        c = _commit(tid, sku, days=10, quantity=100)
        assert _row(tid, sid, sku)["recommended_qty"] == pytest.approx(250.0)
        svc.set_status(tid, c["id"], "u1", "fulfilled")
        assert _row(tid, sid, sku)["recommended_qty"] == 150.0

    def test_another_tenants_commitment_never_applies(self, test_tenant,
                                                      make_tenant_user_headers):
        tid = test_tenant["id"]
        sku = f"CD-{uuid4().hex[:6]}"
        sid = _flat_session(tid, sku)
        _, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        _commit(other_tid, sku, days=5, quantity=900)
        row = _row(tid, sid, sku)
        assert row["recommended_qty"] == 150.0 and row["committed_applied"] == []

    def test_the_warehouse_view_counts_only_what_belongs_to_each_warehouse(self, test_tenant):
        from backend.db.connection import execute
        tid = test_tenant["id"]
        sku = f"CD-{uuid4().hex[:6]}"
        sid = _flat_session(tid, sku)
        execute("INSERT INTO warehouses (tenant_id, name, is_default) VALUES (%s, %s, false)",
                (tid, "Bodega Norte"))
        north = query_one("SELECT id FROM warehouses WHERE tenant_id = %s AND name = %s",
                          (tid, "Bodega Norte"))["id"]
        _commit(tid, sku, days=5, quantity=300, warehouse_id=north)
        rows = [r for r in inv_svc.get_inventory_status_by_warehouse(tid, sid)
                if r["sku"] == sku and r["signal"] != "SIN_DATOS"]
        named = [r for r in rows if r.get("committed_applied")]
        # the commitment names Norte, so it is counted there and nowhere else
        assert named, "the named warehouse must show the commitment"
        assert all(r["warehouse_id"] == north for r in named)
        assert all(a["scope"] == "warehouse" for r in named for a in r["committed_applied"])
