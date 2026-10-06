"""Multi-currency on the paths Python still serves: purchase orders, budgets, the
cash calendar and approvals read the tenant's exchange rates (entered through the
Rust routes, written here straight into the table) with the rules of
`backend/fx/reference.py`.

What is asserted, every time, with a direct query: the currency and the rate are
recorded ON the order line and never re-derived; a missing rate is reported and
never valued at 1.0; a tenant with one currency and no rates writes exactly what
it always wrote.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one

SUPPLIERS = "/api/v1/inventory/suppliers"
PO = "/api/v1/inventory/po"
LOG_PO = "/api/v1/inventory/log-po"
BUDGETS = "/api/v1/inventory/budgets"
CHECK = "/api/v1/inventory/budget/check"
STATUS = "/api/v1/inventory/budget/status"
CURRENCY = "/api/v1/tenant/currency"


def _rate(tenant_id, currency, rate, on=None, base="CRC"):
    rid = uuid4().hex
    execute(
        """INSERT INTO exchange_rates (id, tenant_id, currency, base_currency, rate,
                                       effective_date, created_by)
           VALUES (%s, %s, %s, %s, %s::numeric, %s, 'test')""",
        (rid, tenant_id, currency, base, str(rate), on or date.today()))
    return rid


def _supplier(client, headers, name=None):
    r = client.post(SUPPLIERS, json={"name": name or f"Sup-{uuid4().hex[:6]}"}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _manual(client, headers, supplier, lines, key=None):
    h = {**headers, **({"Idempotency-Key": key} if key else {})}
    return client.post(PO, json={"supplier_id": supplier["id"], "lines": lines}, headers=h)


def _line(sku="FX-1", qty=10, cost=2.5, currency=None):
    ln = {"sku": sku, "qty": qty, "unit_cost": cost}
    if currency is not None:
        ln["currency"] = currency
    return ln


def _header(tid, po_id):
    return query_one("SELECT * FROM inventory_po_log WHERE tenant_id = %s AND id = %s", (tid, po_id))


def _items(tid, po_id):
    return query("SELECT * FROM inventory_po_items WHERE tenant_id = %s AND po_log_id = %s ORDER BY sku",
                 (tid, po_id))


def _count_orders(tid):
    return query_one("SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id = %s", (tid,))["n"]


class TestPurchaseOrderCurrency:

    def test_a_tenant_with_one_currency_writes_exactly_what_it_always_wrote(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sup = _supplier(client, analyst_headers)
        r = _manual(client, analyst_headers, sup, [_line(qty=3, cost=0.1), _line("FX-2", 7, 1.15)])
        assert r.status_code == 201, r.text
        po = r.json()["data"]
        h = _header(tid, po["id"])
        # the legacy float arithmetic, byte for byte (no decimal path was taken)
        assert h["total_value"] == 3 * 0.1 + 7 * 1.15
        assert h["fx_unconverted_lines"] == 0
        for it in _items(tid, po["id"]):
            assert (it["currency"], it["fx_base_currency"], it["fx_rate"], it["fx_rate_date"],
                    it["fx_rate_id"], it["value_base"]) == (None,) * 6

    def test_naming_the_tenants_own_currency_is_not_foreign(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sup = _supplier(client, analyst_headers)
        r = _manual(client, analyst_headers, sup, [_line(currency="crc")])  # the tenant default is CRC
        assert r.status_code == 201, r.text
        (it,) = _items(tid, r.json()["data"]["id"])
        assert it["currency"] is None and it["value_base"] is None
        assert _header(tid, r.json()["data"]["id"])["total_value"] == 25.0

    def test_a_foreign_line_is_converted_and_the_rate_is_recorded_on_it(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        rid = _rate(tid, "USD", "520.5")
        sup = _supplier(client, analyst_headers)
        r = _manual(client, analyst_headers, sup,
                    [_line("A", 3, "0.335", "usd"), _line("B", 10, 2.5)])
        assert r.status_code == 201, r.text
        po = r.json()["data"]
        a, b = _items(tid, po["id"])
        assert (a["currency"], a["fx_base_currency"], a["fx_rate"], a["fx_rate_id"], a["fx_rate_date"]) \
            == ("USD", "CRC", Decimal("520.5"), rid, date.today())
        assert a["unit_cost"] == 0.335                      # the line keeps its own currency's price
        assert a["value_base"] == Decimal("523.10")         # 3 x 0.335 x 520.5 = 523.1025, rounded once
        assert (b["currency"], b["value_base"]) == (None, None)
        h = _header(tid, po["id"])
        assert h["total_value"] == 548.10                   # 523.10 + 25.00, in colones
        assert h["fx_unconverted_lines"] == 0
        assert po["fx_unconverted_lines"] == 0

    def test_without_a_rate_the_line_is_unconverted_and_never_valued_at_one(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sup = _supplier(client, analyst_headers)
        r = _manual(client, analyst_headers, sup, [_line("A", 100, 1.0, "USD"), _line("B", 4, 5.0)])
        assert r.status_code == 201, r.text
        po = r.json()["data"]
        a, _b = _items(tid, po["id"])
        assert a["currency"] == "USD" and a["fx_rate"] is None and a["value_base"] is None
        h = _header(tid, po["id"])
        assert h["total_value"] == 20.0 and h["total_value"] != 120.0   # the USD 100 is NOT counted as 100 colones
        assert h["fx_unconverted_lines"] == 1
        assert po["fx_unconverted_lines"] == 1

    def test_only_foreign_lines_with_no_rate_leave_no_total_at_all(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sup = _supplier(client, analyst_headers)
        r = _manual(client, analyst_headers, sup, [_line("A", 5, 2.0, "EUR")])
        assert r.status_code == 201
        h = _header(tid, r.json()["data"]["id"])
        assert h["total_value"] is None and h["fx_unconverted_lines"] == 1

    def test_a_rate_dated_in_the_future_is_not_used_and_an_older_one_is(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "999", date.today() + timedelta(days=3))
        _rate(tid, "USD", "500", date.today() - timedelta(days=40))
        _rate(tid, "USD", "510", date.today() - timedelta(days=5))
        sup = _supplier(client, analyst_headers)
        r = _manual(client, analyst_headers, sup, [_line("A", 1, 10, "USD")])
        (a,) = _items(tid, r.json()["data"]["id"])
        assert a["fx_rate"] == Decimal("510") and a["value_base"] == Decimal("5100.00")
        assert a["fx_rate_date"] == date.today() - timedelta(days=5)

    def test_later_rates_never_rewrite_a_written_order(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        rid = _rate(tid, "USD", "500")
        sup = _supplier(client, analyst_headers)
        po = _manual(client, analyst_headers, sup, [_line("A", 2, 10, "USD")]).json()["data"]
        _rate(tid, "USD", "600", date.today() + timedelta(days=1))
        execute("UPDATE exchange_rates SET rate = 700 WHERE id = %s", (rid,))
        execute("DELETE FROM exchange_rates WHERE id = %s", (rid,))
        (a,) = _items(tid, po["id"])
        assert a["fx_rate"] == Decimal("500") and a["value_base"] == Decimal("10000.00")
        assert _header(tid, po["id"])["total_value"] == 10000.0

    def test_an_unsupported_currency_is_refused_and_nothing_is_written(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sup = _supplier(client, analyst_headers)
        r = _manual(client, analyst_headers, sup, [_line("A", 1, 1, "XXX")])
        assert r.status_code == 400 and r.json()["error_code"] == "currency_not_supported"
        assert _count_orders(tid) == 0

    def test_viewer_cannot_write_an_order_analyst_can(self, client, test_tenant, viewer_headers,
                                                      analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        sup = _supplier(client, analyst_headers)
        denied = _manual(client, viewer_headers, sup, [_line("A", 1, 1, "USD")])
        assert denied.status_code == 403 and _count_orders(tid) == 0
        assert _manual(client, analyst_headers, sup, [_line("A", 1, 1, "USD")]).status_code == 201
        assert _count_orders(tid) == 1

    def test_relabelling_the_base_currency_makes_old_rates_stop_applying(
            self, client, test_tenant, analyst_headers, auth_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500", base="CRC")
        assert client.patch(CURRENCY, json={"code": "MXN"}, headers=auth_headers).status_code == 200
        sup = _supplier(client, analyst_headers)
        r = _manual(client, analyst_headers, sup, [_line("A", 1, 10, "USD")])
        (a,) = _items(tid, r.json()["data"]["id"])
        assert a["fx_rate"] is None and a["value_base"] is None       # a CRC rate is not an MXN rate
        assert _header(tid, r.json()["data"]["id"])["fx_unconverted_lines"] == 1

    def test_the_keyed_replay_of_a_foreign_order_is_the_same_order(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        sup = _supplier(client, analyst_headers)
        key = uuid4().hex
        first = _manual(client, analyst_headers, sup, [_line("A", 1, 1, "USD")], key=key)
        again = _manual(client, analyst_headers, sup, [_line("A", 1, 1, "USD")], key=key)
        assert first.status_code == 201 and again.status_code == 200
        assert again.json()["data"]["id"] == first.json()["data"]["id"] and _count_orders(tid) == 1
        other = _manual(client, analyst_headers, sup, [_line("A", 1, 1, "EUR")], key=key)
        assert other.status_code == 409          # same key, a different currency: a different order

    def test_a_forecast_cart_line_carries_its_currency_too(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        body = {"items": [
            {"sku": "C1", "supplier": "Acme", "signal": "PEDIR_YA", "recommended_qty": 4,
             "final_qty": 4, "unit_cost": 2.0, "currency": "USD", "status": "approved"},
            {"sku": "C2", "supplier": "Acme", "signal": "PEDIR_YA", "recommended_qty": 9,
             "final_qty": 9, "unit_cost": 1.0, "currency": "USD", "status": "rejected"},
        ]}
        r = client.post(LOG_PO, params={"session_id": "s1"}, json=body, headers=analyst_headers)
        assert r.status_code == 201, r.text
        c1, c2 = _items(tid, r.json()["data"]["id"])
        assert c1["value_base"] == Decimal("4000.00")
        assert c2["currency"] == "USD" and c2["value_base"] is None     # rejected: recorded, never valued
        assert _header(tid, r.json()["data"]["id"])["total_value"] == 4000.0


class TestSupplierPriceCurrency:

    def _link(self, client, headers, sku, sup_id, **body):
        return client.put(f"/api/v1/inventory/stock/{sku}/suppliers/{sup_id}",
                          json={"unit_cost": 4.2, **body}, headers=headers)

    def test_the_price_currency_is_stored_and_listed(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sup = _supplier(client, analyst_headers)
        r = self._link(client, analyst_headers, "S1", sup["id"], currency="usd")
        assert r.status_code == 200, r.text
        row = query_one("SELECT currency, unit_cost FROM sku_suppliers WHERE tenant_id = %s AND sku = 'S1'", (tid,))
        assert (row["currency"], row["unit_cost"]) == ("USD", 4.2)
        listed = client.get("/api/v1/inventory/stock/S1/suppliers", headers=analyst_headers).json()["data"]
        assert listed[0]["currency"] == "USD"

    def test_omitting_it_leaves_it_and_blank_or_own_currency_resets_it(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        sup = _supplier(client, analyst_headers)
        self._link(client, analyst_headers, "S1", sup["id"], currency="EUR")
        self._link(client, analyst_headers, "S1", sup["id"], is_primary=True)        # no currency in the body
        cur = lambda: query_one("SELECT currency FROM sku_suppliers WHERE tenant_id = %s", (tid,))["currency"]  # noqa: E731
        assert cur() == "EUR"
        self._link(client, analyst_headers, "S1", sup["id"], currency="")
        assert cur() is None
        self._link(client, analyst_headers, "S1", sup["id"], currency="MXN")
        self._link(client, analyst_headers, "S1", sup["id"], currency="CRC")         # the tenant's own
        assert cur() is None

    def test_unsupported_code_refused_viewer_denied_state_unchanged(
            self, client, test_tenant, analyst_headers, viewer_headers):
        tid = test_tenant["id"]
        sup = _supplier(client, analyst_headers)
        self._link(client, analyst_headers, "S1", sup["id"], currency="USD")
        bad = self._link(client, analyst_headers, "S1", sup["id"], currency="ZZZ")
        assert bad.status_code == 400 and bad.json()["error_code"] == "currency_not_supported"
        denied = self._link(client, viewer_headers, "S1", sup["id"], currency="EUR")
        assert denied.status_code == 403
        assert query_one("SELECT currency FROM sku_suppliers WHERE tenant_id = %s", (tid,))["currency"] == "USD"


def _budget(client, headers, amount, hard=False, currency=None):
    body = {"period_type": "month", "period_start": date.today().isoformat(), "amount": amount,
            "hard_cap": hard}
    if currency:
        body["currency"] = currency
    r = client.post(BUDGETS, json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]


class TestBudgetsInBaseCurrency:

    def test_the_check_converts_a_foreign_line_at_todays_rate(self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        _budget(client, analyst_headers, 1000.0)
        over = client.post(CHECK, json={"lines": [{"sku": "A", "qty": 4, "unit_cost": 1, "currency": "USD"}]},
                           headers=analyst_headers).json()["data"]["exceeded"]
        assert len(over) == 1 and over[0]["order_value"] == 2000.0 and over[0]["over_by"] == 1000.0
        assert "unconverted_lines" not in over[0]
        fits = client.post(CHECK, json={"lines": [{"sku": "A", "qty": 1, "unit_cost": 1, "currency": "USD"}]},
                           headers=analyst_headers).json()["data"]["exceeded"]
        assert fits == []

    def test_a_soft_budget_flags_an_unconvertible_line_and_still_orders(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _budget(client, analyst_headers, 100000.0)
        flagged = client.post(CHECK, json={"lines": [{"sku": "A", "qty": 4, "unit_cost": 1, "currency": "USD"}]},
                              headers=analyst_headers).json()["data"]["exceeded"]
        assert len(flagged) == 1 and flagged[0]["unconverted_lines"] == 1
        assert flagged[0]["exceeds"] is False and flagged[0]["over_by"] == 0.0
        body = {"items": [{"sku": "A", "supplier": "Acme", "signal": "PEDIR_YA", "recommended_qty": 4,
                           "final_qty": 4, "unit_cost": 1.0, "currency": "USD", "status": "approved"}]}
        r = client.post(LOG_PO, params={"session_id": "s1"}, json=body, headers=analyst_headers)
        assert r.status_code == 201, r.text
        assert _count_orders(tid) == 1
        # a warning, not an excess: no "over budget" event is written for it
        assert not query("SELECT 1 FROM activity_logs WHERE tenant_id = %s AND action = 'purchase_budget.exceeded'",
                         (tid,))

    def test_a_hard_cap_refuses_an_unconvertible_order_with_its_own_code(
            self, client, test_tenant, analyst_headers, auth_headers):
        tid = test_tenant["id"]
        _budget(client, analyst_headers, 100000.0, hard=True)
        body = {"items": [{"sku": "A", "supplier": "Acme", "signal": "PEDIR_YA", "recommended_qty": 4,
                           "final_qty": 4, "unit_cost": 1.0, "currency": "USD", "status": "approved"}]}
        post = lambda h, **extra: client.post(  # noqa: E731
            LOG_PO, params={"session_id": "s1"}, json={**body, **extra}, headers=h)
        blocked = post(analyst_headers)
        assert blocked.status_code == 409 and blocked.json()["error_code"] == "purchase_budget_fx_rate_missing"
        assert blocked.json()["error_params"]["unconverted_lines"] == 1
        assert blocked.json()["error_params"]["override_possible"] is False
        assert post(analyst_headers, budget_override_reason="urgent").status_code == 403
        assert _count_orders(tid) == 0
        admin_no_reason = post(auth_headers)
        assert admin_no_reason.status_code == 409 and admin_no_reason.json()["error_params"]["override_possible"] is True
        assert _count_orders(tid) == 0
        ok = post(auth_headers, budget_override_reason="supplier quote in dollars, rate pending")
        assert ok.status_code == 201, ok.text
        assert _count_orders(tid) == 1
        ev = query("SELECT context FROM activity_logs WHERE tenant_id = %s AND action = 'purchase_budget.override'",
                   (tid,))
        assert len(ev) == 1 and "rate pending" in json.dumps(ev[0], default=str)

    def test_a_rate_makes_the_same_order_pass_the_hard_cap_without_an_override(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        _budget(client, analyst_headers, 100000.0, hard=True)
        body = {"items": [{"sku": "A", "supplier": "Acme", "signal": "PEDIR_YA", "recommended_qty": 4,
                           "final_qty": 4, "unit_cost": 1.0, "currency": "USD", "status": "approved"}]}
        assert client.post(LOG_PO, params={"session_id": "s1"}, json=body, headers=analyst_headers).status_code == 201
        assert _count_orders(tid) == 1

    def test_hard_cap_still_refuses_a_real_excess_in_converted_money(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        _budget(client, analyst_headers, 1500.0, hard=True)
        body = {"items": [{"sku": "A", "supplier": "Acme", "signal": "PEDIR_YA", "recommended_qty": 4,
                           "final_qty": 4, "unit_cost": 1.0, "currency": "USD", "status": "approved"}]}
        r = client.post(LOG_PO, params={"session_id": "s1"}, json=body, headers=analyst_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "purchase_budget_hard_cap"
        assert r.json()["error_params"]["over_by"] == 500.0
        assert _count_orders(tid) == 0

    def test_usage_counts_converted_orders_and_reports_the_unconverted_ones(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        _budget(client, analyst_headers, 10000.0)
        sup = _supplier(client, analyst_headers)
        assert _manual(client, analyst_headers, sup, [_line("A", 2, 1, "USD")]).status_code == 201   # 1000
        assert _manual(client, analyst_headers, sup, [_line("B", 3, 2.0)]).status_code == 201        # 6
        # an order written before any rate existed for EUR
        assert _manual(client, analyst_headers, sup, [_line("C", 1, 9, "EUR")]).status_code == 201
        # a manual order is not budget-checked on the way in, but it counts once written
        st = client.get(STATUS, headers=analyst_headers).json()["data"]
        assert st["usage"]["committed"] == 1006.0 and st["usage"]["unconverted_lines"] == 1
        assert {"code": "ordered_lines_unconverted", "params": {"count": 1}} in st["warnings"]

    def test_a_line_converted_into_another_currency_is_not_summed_into_this_budget(
            self, client, test_tenant, analyst_headers):
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        _budget(client, analyst_headers, 10000.0)
        sup = _supplier(client, analyst_headers)
        _manual(client, analyst_headers, sup, [_line("A", 2, 1, "USD")])
        # the books were relabelled afterwards: the stored value is in colones
        execute("UPDATE purchase_budgets SET currency = 'MXN' WHERE tenant_id = %s", (tid,))
        st = client.get(STATUS, headers=analyst_headers).json()["data"]
        assert st["usage"]["committed"] == 0.0 and st["usage"]["unconverted_lines"] == 1


class TestOtherMoneyReaders:

    def test_the_cash_calendar_pays_the_converted_value_and_says_when_it_cannot(
            self, client, test_tenant, analyst_headers):
        from backend.inventory import cash_service
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        sup = _supplier(client, analyst_headers)
        execute("UPDATE suppliers SET payment_terms_days = 30 WHERE id = %s", (sup["id"],))
        ok = _manual(client, analyst_headers, sup, [_line("A", 2, 1, "USD"), _line("B", 1, 5.0)]).json()["data"]
        miss = _manual(client, analyst_headers, sup, [_line("C", 2, 1, "EUR")]).json()["data"]
        execute("UPDATE inventory_po_log SET sent_at = NOW() WHERE id = ANY(%s)", ([ok["id"], miss["id"]],))
        pay = cash_service.get_payables(tid, 60)
        items = {i["po_log_id"]: i for i in pay["due_items"] + pay.get("unknown_terms", [])}
        assert items[ok["id"]]["amount"] == 1005.0 and items[ok["id"]]["amount_complete"] is True
        assert items[miss["id"]]["amount"] == 0.0 and items[miss["id"]]["amount_complete"] is False
        assert pay["totals_complete"] is False

    def test_the_approval_amount_is_the_converted_value(self, client, test_tenant, analyst_headers):
        from backend.inventory import po_approval_service as approvals
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        sup = _supplier(client, analyst_headers)
        po = _manual(client, analyst_headers, sup, [_line("A", 2, 1, "USD"), _line("B", 1, 5.0)]).json()["data"]
        facts = approvals._order_facts(tid, _header(tid, po["id"]))
        assert facts["amount"] == 1005.0
        unrated = _manual(client, analyst_headers, sup, [_line("C", 9, 1, "EUR")]).json()["data"]
        assert approvals._order_facts(tid, _header(tid, unrated["id"]))["amount"] is None

    def test_the_supplier_pdf_prints_each_line_in_its_own_currency(self):
        from backend.inventory import po_pdf
        crc = {"code": "CRC", "symbol": "₡", "locale": "es-CR", "decimals": 0}
        usd_line = {"final_qty": 2, "unit_cost": 1.5, "currency": "USD"}
        crc_line = {"final_qty": 1, "unit_cost": 700.0, "currency": None}
        one = po_pdf._amounts_text([crc_line], crc)
        assert one == "₡700"                                  # one currency: exactly as before
        mixed = po_pdf._amounts_text([usd_line, crc_line], crc)
        assert mixed == "$3.00 + ₡700"                       # never one figure from two currencies

    def test_price_history_ignores_lines_in_another_currency(self, client, test_tenant, analyst_headers):
        from backend.inventory import cost_alerts
        tid = test_tenant["id"]
        _rate(tid, "USD", "500")
        sup = _supplier(client, analyst_headers)
        po = _manual(client, analyst_headers, sup, [_line("A", 5, 1.0, "USD"), _line("B", 5, 700.0)]).json()["data"]
        execute("UPDATE inventory_po_items SET received_qty = final_qty WHERE po_log_id = %s", (po["id"],))
        execute("UPDATE inventory_po_log SET reception_status = 'received', received_at = NOW() WHERE id = %s",
                (po["id"],))
        obs, _n = cost_alerts._cost_observations(tid, 30)
        assert [o["sku"] for o in obs] == ["B"]
