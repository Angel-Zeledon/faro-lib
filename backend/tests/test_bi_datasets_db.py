"""The flat BI datasets against a real database: scope, tenant isolation, plan lock,
key scope and the contract as the live app serves it.

The world is `test_warehouse_scope.py`'s: one tenant, three warehouses (`principal`,
`Norte`, `Sur`) with forecasts kept per warehouse, and people at different
distances from them. On top of it these tests add purchase orders, receptions and
committed demand in every warehouse, so a leak shows up as a row that should not
be there.

Every refusal is asserted twice: the structured error AND the data unchanged
(a GET changes nothing, so "unchanged" here is the meter and the tenant's rows).
Tests that depend on a plan or a rate turn `testing_mode` off themselves.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.auth import api_key_auth
from backend.auth.api_key_auth import hash_key
from backend.auth.jwt_handler import create_access_token
from backend.bi_datasets.spec import DATASETS
from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.inventory import service as inv_svc
from backend.inventory.series import SERIES_SEPARATOR
from backend.users import service as user_svc

COMPANY = "warehouse_scope_company_totals"
BASE = "/api/v1/datasets"
SEP = SERIES_SEPARATOR


def _forecast_entry(daily, days=30):
    return {"lightgbm": {
        "historical": [],
        "forecast": [{"date": f"2026-08-{i + 1:02d}", "value": daily,
                      "lower": daily * 0.8, "upper": daily * 1.2} for i in range(days)],
    }}


def _stock(tid, sku, wh, qty, lead=5):
    inv_svc.upsert_stock(tid, sku, {"current_stock": qty, "lead_time_days": lead,
                                    "warehouse": wh, "moq": 1, "unit_cost": 2.0})


def _mint_key(tenant_id: str, role: str = "viewer", scope_ids=None) -> str:
    raw = f"sk_live_{uuid4().hex}{uuid4().hex}"
    execute(
        """INSERT INTO api_keys (id, tenant_id, name, key_hash, role, created_by, last4,
                                 warehouse_scope)
           VALUES (gen_random_uuid()::text, %s, 'bi', %s, %s, 'usr_test', %s, %s::jsonb)""",
        (tenant_id, hash_key(raw), role, raw[-4:],
         None if scope_ids is None else json.dumps(scope_ids)),
    )
    return raw


def _bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


def _calls(tenant_id: str) -> int:
    return int(query_one(
        "SELECT COALESCE(SUM(calls), 0) AS n FROM api_usage_daily WHERE tenant_id = %s",
        (tenant_id,))["n"])


def _po(tid, sid, number, dest, lines, *, generated=None, received_at=None,
        reception_status="pending", cancelled_at=None):
    """A purchase order with lines written directly: (sku, warehouse, final, received, cost)."""
    generated = generated or datetime.now(timezone.utc) - timedelta(days=10)
    row = query_one(
        """INSERT INTO inventory_po_log
               (tenant_id, session_id, generated_at, sku_count, total_units, po_number,
                destination_warehouse, reception_status, received_at, cancelled_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (tid, sid, generated, len(lines), sum(l[2] for l in lines), number, dest,
         reception_status, received_at, cancelled_at))
    for sku, wh, final, received, cost in lines:
        execute(
            """INSERT INTO inventory_po_items
                   (po_log_id, tenant_id, sku, display_name, supplier, signal,
                    recommended_qty, final_qty, unit_cost, status, warehouse, received_qty)
               VALUES (%s, %s, %s, %s, 'Acme', 'PEDIR_YA', %s, %s, %s, 'approved', %s, %s)""",
            (row["id"], tid, sku, f"name-{sku}", final, final, cost, wh, received))
    return row["id"]


def _commit(tid, sku, warehouse_id, qty=10, status="open", customer="ACME"):
    return query_one(
        """INSERT INTO committed_demand
               (tenant_id, sku, warehouse_id, delivery_date, quantity, customer, status, created_by)
           VALUES (%s, %s, %s, CURRENT_DATE + 30, %s, %s, %s, 'usr_test') RETURNING id""",
        (tid, sku, warehouse_id, qty, customer, status))["id"]


class World:
    pass


@pytest.fixture
def world(client, registered_user, completed_session):
    w = World()
    w.client = client
    w.tid = registered_user["tenant"]["id"]
    w.sid = completed_session["id"]
    tid = w.tid

    session_store.set_forecasts(tid, w.sid, {
        f"A{SEP}Norte": _forecast_entry(10.0),
        f"A{SEP}principal": _forecast_entry(10.0),
        f"B{SEP}Norte": _forecast_entry(1.0),
        f"C{SEP}principal": _forecast_entry(1.0),
        f"S{SEP}Sur": _forecast_entry(1.0),
    })
    metrics = [
        {"sku": k, "model": m, "type": "ml", "wape": wape, "bias": bias, "mae": 1.0,
         "rmse": 2.0, "cost": cost}
        for k, (wape, bias, cost) in {
            f"A{SEP}Norte": (0.10, 0.5, 1.0), f"A{SEP}principal": (0.20, -0.5, 1.0),
            f"B{SEP}Norte": (0.30, 0.0, 1.0), f"C{SEP}principal": (0.40, 1.5, 1.0),
            f"S{SEP}Sur": (0.50, -2.0, 1.0),
        }.items() for m in ("lightgbm",)
    ]
    session_store.set_training_result(tid, w.sid, {"metrics": {"rows": metrics}})
    _stock(tid, "A", "principal", 600)
    _stock(tid, "A", "Norte", 5)
    _stock(tid, "B", "Norte", 50)
    _stock(tid, "C", "principal", 70)
    _stock(tid, "S", "Sur", 40)
    w.wh = {r["name"]: r["id"] for r in query(
        "SELECT id, name FROM warehouses WHERE tenant_id = %s", (tid,))}

    # Orders: one per destination, with a line each; Norte's order was received.
    now = datetime.now(timezone.utc)
    w.po_norte = _po(tid, w.sid, 101, "Norte", [("B", "Norte", 100, 100.0, 2.0)],
                     received_at=now - timedelta(days=2), reception_status="received")
    w.po_sur = _po(tid, w.sid, 102, "Sur", [("S", "Sur", 50, 20.0, 3.0)],
                   received_at=now - timedelta(days=1), reception_status="partial")
    w.po_main = _po(tid, w.sid, 103, None, [("C", "principal", 30, None, None)])

    w.cd_norte = _commit(tid, "B", w.wh["Norte"])
    w.cd_sur = _commit(tid, "S", w.wh["Sur"])
    w.cd_company = _commit(tid, "C", None)

    def person(role, scope="ALL"):
        email = f"{role}-{uuid4().hex[:8]}@example.com"
        u = user_svc.create_user(tenant_id=tid, email=email, password="TestPass123!", role=role)
        user_svc.mark_verified(tid, u["id"])
        if scope != "ALL":
            execute("UPDATE users SET warehouse_scope = %s::jsonb WHERE id = %s",
                    (json.dumps([w.wh[n] for n in scope]), u["id"]))
        tok = create_access_token(u["id"], tid, role, email_verified=True)
        return u, {"Authorization": f"Bearer {tok}"}

    tok = create_access_token(registered_user["user"]["id"], tid, "admin", email_verified=True)
    w.admin_h = {"Authorization": f"Bearer {tok}"}
    _, w.viewer_h = person("viewer")
    _, w.norte_h = person("viewer", ["Norte"])
    _, w.nobody_h = person("viewer", [])
    w.person = person
    return w


def _get(world, name, headers, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return world.client.get(f"{BASE}/{name}" + (f"?{qs}" if qs else ""), headers=headers)


def _items(r):
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()["data"]["items"]


def _refused(r, code, status=403):
    assert r.status_code == status, (r.status_code, r.text)
    assert r.json()["error_code"] == code, r.text


# ── The datasets mirror their screens ────────────────────────────────────────

class TestInventoryStatus:
    def test_one_row_per_sku_and_warehouse_with_the_screens_numbers(self, world):
        items = _items(_get(world, "inventory-status", world.admin_h))
        assert {(i["sku"], i["warehouse"]) for i in items} == {
            ("A", "Norte"), ("A", "principal"), ("B", "Norte"), ("C", "principal"),
            ("S", "Sur")}
        screen = world.client.get(
            f"/api/v1/inventory/status?session_id={world.sid}&by_warehouse=true",
            headers=world.admin_h).json()["data"]["items"]
        by_key = {(i["sku"], i["warehouse"]): i for i in screen}
        for i in items:
            s = by_key[(i["sku"], i["warehouse"])]
            for col in ("signal", "current_stock", "incoming_qty", "coverage_days",
                        "reorder_point", "recommended_qty", "lead_time_days", "moq"):
                assert i[col] == s[col], (i["sku"], i["warehouse"], col)
            assert i["session_id"] == world.sid

    def test_every_declared_column_is_present_on_every_row(self, world):
        items = _items(_get(world, "inventory-status", world.admin_h))
        assert all(list(i) == list(DATASETS["inventory-status"].column_names) for i in items)

    def test_a_scoped_caller_gets_only_their_warehouses_and_no_trace_of_others(self, world):
        r = _get(world, "inventory-status", world.norte_h)
        items = _items(r)
        assert {(i["sku"], i["warehouse"]) for i in items} == {("A", "Norte"), ("B", "Norte")}
        assert r.headers["X-Warehouse-Scope"] == "limited"
        assert r.json()["data"]["scope"] == {"warehouses": ["Norte"]}
        assert "principal" not in json.dumps(items) and "Sur" not in json.dumps(items)
        # A transfer from principal is not named; the action falls back to ordering.
        assert all(i["recommended_action"] != "transfer" for i in items)

    def test_the_company_wide_snapshot_is_never_what_a_scoped_caller_reads(self, world):
        """The recently fixed leak: the snapshot is summed over every warehouse."""
        items = _items(_get(world, "inventory-status", world.norte_h))
        a = next(i for i in items if i["sku"] == "A")
        assert a["current_stock"] == 5          # Norte's, not the company's 605

    def test_a_caller_with_an_empty_scope_gets_nothing_not_everything(self, world):
        assert _items(_get(world, "inventory-status", world.nobody_h)) == []

    def test_an_unknown_session_is_a_404_and_a_foreign_one_too(self, world, make_tenant_user_headers):
        _refused(_get(world, "inventory-status", world.admin_h, session_id="nope"),
                 "session_not_found", 404)
        other_headers, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        _refused(_get(world, "inventory-status", other_headers, session_id=world.sid),
                 "session_not_found", 404)


class TestPurchaseOrderLines:
    def test_every_line_of_every_order_in_one_call(self, world):
        items = _items(_get(world, "purchase-order-lines", world.admin_h))
        # Oldest order first: the sort is (ordered_at, order id, supplier, sku, line id).
        assert [(i["po_number"], i["sku"]) for i in items] == [(101, "B"), (102, "S"), (103, "C")]
        by_po = {i["po_number"]: i for i in items}
        n = by_po[101]
        assert (n["final_qty"], n["received_qty"], n["outstanding_qty"]) == (100, 100, 0)
        assert n["unit_cost"] == 2.0 and n["line_value"] == 200.0
        assert n["destination_warehouse"] == "Norte" and n["reception_status"] == "received"
        s = by_po[102]
        assert s["outstanding_qty"] == 30 and s["reception_status"] == "partial"
        m = by_po[103]
        assert m["received_qty"] is None and m["unit_cost"] is None and m["line_value"] is None
        # A NULL destination is the tenant's default warehouse, spelled out.
        assert m["destination_warehouse"] == "principal"

    def test_a_scoped_caller_sees_only_orders_destined_to_their_warehouses(self, world):
        items = _items(_get(world, "purchase-order-lines", world.norte_h))
        assert {i["po_number"] for i in items} == {101}
        assert {i["destination_warehouse"] for i in items} == {"Norte"}

    def test_an_empty_scope_sees_no_order(self, world):
        assert _items(_get(world, "purchase-order-lines", world.nobody_h)) == []

    def test_updated_since_returns_only_what_changed(self, world):
        long_ago = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert len(_items(_get(world, "purchase-order-lines", world.admin_h,
                               updated_since=long_ago))) == 3
        recent = (datetime.now(timezone.utc) - timedelta(days=1, hours=12)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        # Order 101 last changed 2 days ago (its reception) and 102 yesterday.
        got = {i["po_number"] for i in _items(_get(
            world, "purchase-order-lines", world.admin_h, updated_since=recent))}
        assert got == {102}

    def test_paging_is_stable_and_complete(self, world):
        seen, page = [], 1
        while page:
            r = _get(world, "purchase-order-lines", world.admin_h, limit=2, page=page)
            d = r.json()["data"]
            seen += [i["line_id"] for i in d["items"]]
            page = d["next_page"]
        assert len(seen) == len(set(seen)) == 3
        assert r.headers["X-Total-Count"] == "3"

    def test_csv_has_the_declared_header_and_one_line_per_row(self, world):
        r = _get(world, "purchase-order-lines", world.admin_h, format="csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        assert rows[0] == list(DATASETS["purchase-order-lines"].column_names)
        assert len(rows) == 4


class TestReceptions:
    def test_only_lines_with_units_received(self, world):
        items = _items(_get(world, "receptions", world.admin_h))
        assert {(i["po_number"], i["received_qty"]) for i in items} == {(101, 100), (102, 20)}
        s = next(i for i in items if i["po_number"] == 102)
        assert s["outstanding_qty"] == 30 and s["received_value"] == 60.0
        assert s["days_to_last_reception"] == pytest.approx(9.0, abs=0.1)

    def test_a_scoped_caller_sees_only_their_receptions(self, world):
        items = _items(_get(world, "receptions", world.norte_h))
        assert {i["po_number"] for i in items} == {101}


class TestForecastPointsAndAccuracy:
    def test_the_champion_forecast_per_sku_per_period_with_its_band(self, world):
        items = _items(_get(world, "forecast-points", world.admin_h, limit=10000))
        assert len(items) == 5 * 30
        first = next(i for i in items if i["sku"] == "A" and i["warehouse"] == "Norte")
        assert first["model"] == "lightgbm" and first["model_is_champion"] is True
        assert (first["step"], first["date"]) == (1, "2026-08-01")
        assert (first["lower"], first["forecast"], first["upper"]) == (8.0, 10.0, 12.0)

    def test_a_scoped_caller_gets_only_their_warehouse_forecasts(self, world):
        items = _items(_get(world, "forecast-points", world.norte_h, limit=10000))
        assert {(i["sku"], i["warehouse"]) for i in items} == {("A", "Norte"), ("B", "Norte")}

    def test_a_company_wide_forecast_is_refused_to_a_scoped_caller(self, world):
        session_store.set_forecasts(world.tid, world.sid, {"A": _forecast_entry(10.0)})
        _refused(_get(world, "forecast-points", world.norte_h), COMPANY)
        # and an unrestricted caller still gets it
        assert len(_items(_get(world, "forecast-points", world.admin_h, limit=100))) == 30

    def test_accuracy_is_the_champions_wape_and_bias(self, world):
        items = _items(_get(world, "accuracy", world.admin_h))
        got = {(i["sku"], i["warehouse"]): (i["wape"], i["bias"]) for i in items}
        assert got[("A", "Norte")] == (0.10, 0.5)
        assert got[("A", "principal")] == (0.20, -0.5)
        assert len(got) == 5

    def test_a_window_with_no_demand_is_not_reported_as_a_perfect_forecast(self, world):
        rows = [{"sku": "A", "model": "lightgbm", "type": "ml", "wape": 0.0, "mae": 0.0,
                 "bias": 0.0, "rmse": 0.0, "cost": 1.0}]
        session_store.set_training_result(world.tid, world.sid, {"metrics": {"rows": rows}})
        items = _items(_get(world, "accuracy", world.admin_h))
        assert items[0]["wape"] is None

    def test_a_scoped_caller_gets_only_their_warehouses_accuracy(self, world):
        items = _items(_get(world, "accuracy", world.norte_h))
        assert {(i["sku"], i["warehouse"]) for i in items} == {("A", "Norte"), ("B", "Norte")}

    def test_company_wide_accuracy_is_refused_to_a_scoped_caller(self, world):
        rows = [{"sku": "A", "model": "lightgbm", "type": "ml", "wape": 0.1, "mae": 1.0,
                 "bias": 0.0, "rmse": 1.0, "cost": 1.0}]
        session_store.set_training_result(world.tid, world.sid, {"metrics": {"rows": rows}})
        _refused(_get(world, "accuracy", world.norte_h), COMPANY)


class TestCommittedDemand:
    def test_the_commitments_with_the_screens_risk_verdict(self, world):
        items = _items(_get(world, "committed-demand", world.admin_h))
        assert {i["id"] for i in items} == {world.cd_norte, world.cd_sur, world.cd_company}
        norte = next(i for i in items if i["id"] == world.cd_norte)
        assert norte["warehouse"] == "Norte" and norte["status"] == "open"
        assert norte["at_risk"] is not None          # annotated for an unrestricted caller
        company = next(i for i in items if i["id"] == world.cd_company)
        assert company["warehouse_id"] is None and company["warehouse"] is None
        screen = world.client.get("/api/v1/committed-demand", headers=world.admin_h)
        screen_risk = {i["id"]: i["at_risk"] for i in screen.json()["data"]["items"]}
        assert {i["id"]: i["at_risk"] for i in items} == screen_risk

    def test_a_scoped_caller_never_gets_company_wide_rows_or_company_wide_risk(self, world):
        r = _get(world, "committed-demand", world.norte_h)
        items = _items(r)
        assert {i["id"] for i in items} == {world.cd_norte}
        assert all(i["at_risk"] is None and i["shortfall"] is None
                   and i["latest_safe_order_date"] is None for i in items)

    def test_updated_since_filters_on_the_last_change(self, world):
        execute("UPDATE committed_demand SET updated_at = NOW() - INTERVAL '30 days' "
                "WHERE id = %s", (world.cd_sur,))
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        got = {i["id"] for i in _items(_get(world, "committed-demand", world.admin_h,
                                            updated_since=since))}
        assert world.cd_sur not in got and world.cd_norte in got


# ── Tenant isolation ─────────────────────────────────────────────────────────

class TestTenantIsolation:
    @pytest.mark.parametrize("name", [n for n in DATASETS if n != "inventory-status"
                                      and n not in ("forecast-points", "accuracy")])
    def test_another_tenant_sees_none_of_these_rows(self, world, make_tenant_user_headers, name):
        other_h = make_tenant_user_headers(role="admin")
        assert _items(_get(world, name, other_h)) == []
        assert len(_items(_get(world, name, world.admin_h))) > 0

    @pytest.mark.parametrize("name", ["inventory-status", "forecast-points", "accuracy"])
    def test_another_tenant_cannot_read_this_tenants_session(self, world, name,
                                                             make_tenant_user_headers):
        other_h = make_tenant_user_headers(role="admin")
        _refused(_get(world, name, other_h, session_id=world.sid), "session_not_found", 404)

    def test_a_key_reads_only_its_own_tenant(self, world, make_tenant_user_headers):
        other_h, other_tid = make_tenant_user_headers(role="admin", return_tenant_id=True)
        raw = _mint_key(other_tid)
        for name in ("purchase-order-lines", "receptions", "committed-demand"):
            assert _items(_get(world, name, _bearer(raw))) == []


# ── The plan lock ────────────────────────────────────────────────────────────

class TestPlanLock:
    @pytest.fixture
    def real_limits(self, monkeypatch):
        monkeypatch.setattr("backend.config.settings.testing_mode", False)

    def _locked(self, r):
        assert r.status_code == 403, r.text
        body = r.json()
        assert body["error_code"] == "plan_feature_locked"
        assert body["error_params"] == {"feature": "api", "required_plan": "paid"}

    @pytest.mark.parametrize("name", list(DATASETS) + ["schema"])
    def test_a_free_tenant_is_refused_on_every_dataset_with_a_person_login(
            self, world, real_limits, name):
        execute("UPDATE tenants SET tier = 'free' WHERE id = %s", (world.tid,))
        self._locked(_get(world, name, world.admin_h))

    @pytest.mark.parametrize("name", list(DATASETS) + ["schema"])
    def test_a_key_left_over_on_a_free_tenant_is_refused_and_not_metered(
            self, world, real_limits, name):
        raw = _mint_key(world.tid)
        execute("UPDATE tenants SET tier = 'free' WHERE id = %s", (world.tid,))
        before = _calls(world.tid)
        self._locked(_get(world, name, _bearer(raw)))
        assert _calls(world.tid) == before

    @pytest.mark.parametrize("tier", ["paid", "corporate"])
    def test_paid_and_corporate_get_the_data(self, world, real_limits, tier):
        execute("UPDATE tenants SET tier = %s WHERE id = %s", (tier, world.tid))
        assert len(_items(_get(world, "purchase-order-lines", world.admin_h))) == 3


# ── Keys: how they authenticate, what they are metered for, what they may do ─

class TestKeys:
    def test_bearer_and_x_api_key_both_work_and_are_metered_once_each(self, world):
        raw = _mint_key(world.tid)
        before = _calls(world.tid)
        a = world.client.get(f"{BASE}/receptions", headers=_bearer(raw))
        b = world.client.get(f"{BASE}/receptions", headers={"X-API-Key": raw})
        assert a.status_code == b.status_code == 200
        assert a.json()["data"]["items"] == b.json()["data"]["items"]
        assert _calls(world.tid) == before + 2

    def test_a_key_in_the_url_does_not_authenticate_and_is_not_metered(self, world):
        raw = _mint_key(world.tid)
        before = _calls(world.tid)
        r = world.client.get(f"{BASE}/receptions?api_key={raw}")
        assert r.status_code in (401, 403)
        assert _calls(world.tid) == before

    def test_a_read_key_may_call_every_dataset_and_every_call_is_metered(self, world):
        raw = _mint_key(world.tid, role="viewer")
        before = _calls(world.tid)
        names = [n for n in DATASETS] + ["schema"]
        for name in names:
            r = _get(world, name, _bearer(raw))
            assert r.status_code == 200, (name, r.text)
        assert _calls(world.tid) == before + len(names)

    def test_a_call_refused_for_its_parameters_was_still_authorised_and_is_metered(self, world):
        """The key passed every check before the endpoint saw the parameter, so
        the call is counted (a refused credential is not; see TestPlanLock)."""
        raw = _mint_key(world.tid)
        before = _calls(world.tid)
        r = _get(world, "inventory-status", _bearer(raw), updated_since="2026-10-01")
        _refused(r, "dataset_parameter_unsupported", 422)
        assert _calls(world.tid) == before + 1

    def test_a_key_scoped_to_a_warehouse_gets_that_warehouse_only(self, world):
        raw = _mint_key(world.tid, scope_ids=[world.wh["Norte"]])
        r = _get(world, "inventory-status", _bearer(raw))
        assert {(i["sku"], i["warehouse"]) for i in _items(r)} == {("A", "Norte"), ("B", "Norte")}
        assert r.headers["X-Warehouse-Scope"] == "limited"
        assert {i["po_number"] for i in _items(_get(world, "purchase-order-lines",
                                                    _bearer(raw)))} == {101}
        assert {i["id"] for i in _items(_get(world, "committed-demand", _bearer(raw)))} \
            == {world.cd_norte}
        # A session forecast company-wide cannot be cut by warehouse: refused.
        session_store.set_forecasts(world.tid, world.sid, {"A": _forecast_entry(1.0)})
        _refused(_get(world, "forecast-points", _bearer(raw)), COMPANY)

    def test_the_feeds_never_write(self, world):
        """Reads only: no verb but GET is routed, and a GET leaves the rows alone."""
        before = {t: query_one(f"SELECT COUNT(*) AS n FROM {t} WHERE tenant_id = %s",
                               (world.tid,))["n"]
                  for t in ("inventory_po_log", "inventory_po_items", "committed_demand",
                            "inventory_stock")}
        for name in DATASETS:
            _get(world, name, world.admin_h)
            for verb in ("post", "put", "patch", "delete"):
                r = getattr(world.client, verb)(f"{BASE}/{name}", headers=world.admin_h)
                assert r.status_code in (404, 405, 422), (verb, name, r.status_code)
        after = {t: query_one(f"SELECT COUNT(*) AS n FROM {t} WHERE tenant_id = %s",
                              (world.tid,))["n"] for t in before}
        assert after == before


# ── The contract, as the live app serves it ──────────────────────────────────

class TestSchemaContract:
    def test_the_schema_endpoint_matches_what_the_datasets_return(self, world):
        schema = world.client.get(f"{BASE}/schema", headers=world.admin_h)
        assert schema.status_code == 200 and schema.headers["X-Schema-Version"] == "1"
        published = {d["name"]: [c["name"] for c in d["columns"]]
                     for d in schema.json()["data"]["datasets"]}
        for name, columns in published.items():
            r = _get(world, name, world.admin_h)
            assert r.headers["X-Schema-Version"] == DATASETS[name].version
            assert r.json()["data"]["columns"] == columns
            items = r.json()["data"]["items"]
            assert all(list(i) == columns for i in items), name
            csv_r = _get(world, name, world.admin_h, format="csv")
            header = next(csv.reader(io.StringIO(csv_r.content.decode("utf-8-sig"))))
            assert header == columns, name

    def test_the_viewer_role_can_read_them(self, world):
        assert _get(world, "committed-demand", world.viewer_h).status_code == 200
