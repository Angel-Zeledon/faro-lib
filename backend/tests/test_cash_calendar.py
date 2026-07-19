"""
Cash calendar / accounts payable (feature 3.6).

Covers the free-text -> credit-days parser (cases it reads AND cases it must
refuse to read), the migration backfill that must not lose existing data, the
payables built from sent POs, and the "does this purchase fit?" verdict.
"""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.db.connection import query_one, execute
from backend.inventory import cash_service as cash


def _ok(resp, code=200):
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"
    return resp.json()["data"]


# ── The parser ────────────────────────────────────────────────────────────────

class TestParsePaymentTerms:

    @pytest.mark.parametrize("text,expected", [
        ("30 dias", 30),
        ("30 días", 30),
        ("a 45 días", 45),
        ("net 30", 30),
        ("Net 60", 60),
        ("60d", 60),
        ("15 días fin de mes", 15),
        ("crédito 90 días", 90),
    ])
    def test_reads_day_counts(self, text, expected):
        assert cash.parse_payment_terms_days(text) == expected

    @pytest.mark.parametrize("text", [
        "contado", "de contado", "CONTADO", "pago anticipado",
        "prepago", "contra entrega", "contraentrega", "COD", "cash",
    ])
    def test_immediate_terms_are_zero_days_not_unknown(self, text):
        """'Contado' is a known term meaning zero credit — it must not fall
        through to None, which would hide real cash due today."""
        assert cash.parse_payment_terms_days(text) == 0

    def test_immediate_wins_over_a_number_in_the_same_string(self):
        """'pago de contado a 8 dias' is cash on delivery, not 8 days of
        credit — rule order matters and this is the case that proves it."""
        assert cash.parse_payment_terms_days("pago de contado a 8 dias") == 0

    @pytest.mark.parametrize("text,expected", [
        ("1 mes", 30),
        ("2 meses", 60),
        ("3 meses", 90),
    ])
    def test_months_are_not_read_as_days(self, text, expected):
        """Without the months rule ahead of the generic number rule, '2 meses'
        would parse as 2 days."""
        assert cash.parse_payment_terms_days(text) == expected

    def test_fortnightly(self):
        assert cash.parse_payment_terms_days("quincenal") == 15

    @pytest.mark.parametrize("text", [
        None, "", "   ", "a convenir", "según acuerdo", "por definir",
        "transferencia bancaria",
    ])
    def test_unreadable_terms_stay_none_rather_than_guess(self, text):
        """A wrong due date is worse than an admitted gap: unparseable text must
        yield None so the calendar reports it under 'missing terms'."""
        assert cash.parse_payment_terms_days(text) is None

    def test_absurd_numbers_are_clamped(self):
        assert cash.parse_payment_terms_days("3000 dias") == 365

    def test_zero_is_preserved_and_not_confused_with_unknown(self):
        assert cash.parse_payment_terms_days("0 dias") == 0


class TestResolveCreditDays:

    def test_structured_column_wins_over_free_text(self):
        """A user who corrected a bad parse must have their correction stick."""
        assert cash.resolve_credit_days(
            {"payment_terms": "30 dias", "payment_terms_days": 45},
        ) == 45

    def test_falls_back_to_parsing_when_column_is_empty(self):
        assert cash.resolve_credit_days(
            {"payment_terms": "30 dias", "payment_terms_days": None},
        ) == 30

    def test_zero_days_column_is_not_treated_as_missing(self):
        """0 is falsy in Python — this guards the `is not None` check."""
        assert cash.resolve_credit_days(
            {"payment_terms": "60 dias", "payment_terms_days": 0},
        ) == 0

    def test_no_supplier_is_unknown(self):
        assert cash.resolve_credit_days(None) is None


# ── The migration backfill: existing data must survive ────────────────────────

class TestBackfillMigration:

    def test_backfill_structures_legacy_text_without_destroying_it(self, client, test_tenant):
        """Rows written before the column existed get credit days derived, and
        the original free text is left untouched — it is the user's own words
        and the fallback when the parser is wrong."""
        from backend.db.migrations import _MIGRATIONS
        backfill = dict(_MIGRATIONS)["backfill_suppliers_payment_terms_days"]

        cases = {
            "30 dias":      30,
            "contado":      0,
            "2 meses":      60,
            "quincenal":    15,
            "a convenir":   None,   # unreadable stays unreadable
        }
        ids = {}
        for text in cases:
            row = query_one(
                """INSERT INTO suppliers (tenant_id, name, payment_terms)
                   VALUES (%s, %s, %s) RETURNING id""",
                (test_tenant["id"], f"Legacy-{uuid4().hex[:8]}", text),
            )
            # Simulate a pre-migration row.
            execute("UPDATE suppliers SET payment_terms_days = NULL WHERE id = %s", (row["id"],))
            ids[text] = row["id"]

        execute(backfill)

        for text, expected in cases.items():
            row = query_one(
                "SELECT payment_terms, payment_terms_days FROM suppliers WHERE id = %s",
                (ids[text],),
            )
            assert row["payment_terms"] == text, "Original free text was modified"
            assert row["payment_terms_days"] == expected, f"Bad backfill for {text!r}"

    def test_backfill_matches_the_python_parser(self, client, test_tenant):
        """The SQL backfill and parse_payment_terms_days must agree, or rows
        written before and after the migration would disagree forever."""
        from backend.db.migrations import _MIGRATIONS
        backfill = dict(_MIGRATIONS)["backfill_suppliers_payment_terms_days"]

        texts = [
            "30 dias", "30 días", "net 45", "contado", "de contado",
            "contra entrega", "COD", "2 meses", "1 mes", "quincenal",
            "a convenir", "60d", "pago de contado a 8 dias", "3000 dias",
        ]
        ids = []
        for text in texts:
            row = query_one(
                """INSERT INTO suppliers (tenant_id, name, payment_terms)
                   VALUES (%s, %s, %s) RETURNING id""",
                (test_tenant["id"], f"Parity-{uuid4().hex[:8]}", text),
            )
            execute("UPDATE suppliers SET payment_terms_days = NULL WHERE id = %s", (row["id"],))
            ids.append(row["id"])

        execute(backfill)

        for supplier_id in ids:
            row = query_one(
                "SELECT payment_terms, payment_terms_days FROM suppliers WHERE id = %s",
                (supplier_id,),
            )
            assert row["payment_terms_days"] == cash.parse_payment_terms_days(row["payment_terms"]), (
                f"SQL and Python disagree on {row['payment_terms']!r}"
            )

    def test_backfill_does_not_overwrite_an_explicit_value(self, client, test_tenant):
        row = query_one(
            """INSERT INTO suppliers (tenant_id, name, payment_terms, payment_terms_days)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (test_tenant["id"], f"Explicit-{uuid4().hex[:8]}", "30 dias", 7),
        )
        from backend.db.migrations import _MIGRATIONS
        execute(dict(_MIGRATIONS)["backfill_suppliers_payment_terms_days"])
        assert query_one(
            "SELECT payment_terms_days FROM suppliers WHERE id = %s", (row["id"],),
        )["payment_terms_days"] == 7


# ── Supplier endpoints derive credit days ─────────────────────────────────────

class TestSupplierCreditDaysEndpoint:

    def test_analyst_create_derives_credit_days_from_free_text(self, client, analyst_headers):
        r = client.post("/api/v1/inventory/suppliers", headers=analyst_headers, json={
            "name": f"Terms-{uuid4().hex[:6]}", "payment_terms": "45 dias",
        })
        data = _ok(r, 201)
        row = query_one(
            "SELECT payment_terms, payment_terms_days FROM suppliers WHERE id = %s",
            (data["id"],),
        )
        assert row["payment_terms"] == "45 dias", "Free text must be preserved verbatim"
        assert row["payment_terms_days"] == 45

    def test_explicit_credit_days_beats_the_parser(self, client, analyst_headers):
        r = client.post("/api/v1/inventory/suppliers", headers=analyst_headers, json={
            "name": f"Terms-{uuid4().hex[:6]}",
            "payment_terms": "30 dias", "payment_terms_days": 60,
        })
        data = _ok(r, 201)
        assert query_one(
            "SELECT payment_terms_days FROM suppliers WHERE id = %s", (data["id"],),
        )["payment_terms_days"] == 60

    def test_unparseable_terms_leave_credit_days_null(self, client, analyst_headers):
        r = client.post("/api/v1/inventory/suppliers", headers=analyst_headers, json={
            "name": f"Terms-{uuid4().hex[:6]}", "payment_terms": "a convenir",
        })
        data = _ok(r, 201)
        row = query_one(
            "SELECT payment_terms, payment_terms_days FROM suppliers WHERE id = %s",
            (data["id"],),
        )
        assert row["payment_terms"] == "a convenir"
        assert row["payment_terms_days"] is None

    def test_patch_updates_credit_days_and_persists(self, client, analyst_headers):
        created = _ok(client.post("/api/v1/inventory/suppliers", headers=analyst_headers, json={
            "name": f"Terms-{uuid4().hex[:6]}", "payment_terms": "30 dias",
        }), 201)

        r = client.patch(
            f"/api/v1/inventory/suppliers/{created['id']}",
            headers=analyst_headers, json={"payment_terms": "2 meses"},
        )
        assert r.status_code == 200
        row = query_one(
            "SELECT payment_terms, payment_terms_days FROM suppliers WHERE id = %s",
            (created["id"],),
        )
        assert row["payment_terms"] == "2 meses"
        assert row["payment_terms_days"] == 60

    def test_viewer_cannot_change_payment_terms_and_state_is_unchanged(
        self, client, analyst_headers, viewer_headers,
    ):
        created = _ok(client.post("/api/v1/inventory/suppliers", headers=analyst_headers, json={
            "name": f"Terms-{uuid4().hex[:6]}", "payment_terms": "30 dias",
        }), 201)

        r = client.patch(
            f"/api/v1/inventory/suppliers/{created['id']}",
            headers=viewer_headers, json={"payment_terms": "contado"},
        )
        assert r.status_code == 403
        row = query_one(
            "SELECT payment_terms, payment_terms_days FROM suppliers WHERE id = %s",
            (created["id"],),
        )
        assert row["payment_terms"] == "30 dias", "Viewer's rejected PATCH changed the terms"
        assert row["payment_terms_days"] == 30


# ── Payables from sent POs ────────────────────────────────────────────────────

def _make_po(tenant_id, supplier_name, amount_units, unit_cost, sent_days_ago=None):
    """Creates a PO with one line; `sent_days_ago=None` leaves it unsent."""
    po = query_one(
        """INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units)
           VALUES (%s, %s, 1, %s) RETURNING id""",
        (tenant_id, "sess-" + uuid4().hex[:8], amount_units),
    )
    execute(
        """INSERT INTO inventory_po_items
               (po_log_id, tenant_id, sku, supplier, recommended_qty,
                final_qty, unit_cost, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'approved')""",
        (po["id"], tenant_id, f"SKU-{uuid4().hex[:6]}", supplier_name,
         amount_units, amount_units, unit_cost),
    )
    if sent_days_ago is not None:
        execute(
            "UPDATE inventory_po_log SET sent_at = %s WHERE id = %s",
            (datetime.now(timezone.utc) - timedelta(days=sent_days_ago), po["id"]),
        )
    return po["id"]


def _make_supplier_row(tenant_id, name, terms, days):
    execute(
        """INSERT INTO suppliers (tenant_id, name, payment_terms, payment_terms_days)
           VALUES (%s, %s, %s, %s)""",
        (tenant_id, name, terms, days),
    )


class TestPayables:

    def test_unsent_po_is_not_a_payable(self, test_tenant):
        """An unsent draft commits the business to nobody. Counting it would
        inflate the very number used to decide affordability."""
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "30 dias", 30)
        _make_po(test_tenant["id"], name, 10, 100.0, sent_days_ago=None)

        result = cash.get_payables(test_tenant["id"], 30)
        assert result["due_items"] == []
        assert result["horizon_total"] == 0

    def test_sent_po_becomes_a_dated_payable(self, test_tenant):
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "30 dias", 30)
        _make_po(test_tenant["id"], name, 10, 100.0, sent_days_ago=5)

        result = cash.get_payables(test_tenant["id"], 30)
        assert len(result["due_items"]) == 1
        item = result["due_items"][0]
        assert item["amount"] == 1000.0
        assert item["credit_days"] == 30
        assert item["days_until_due"] == 25    # sent 5 days ago on 30-day terms
        assert item["overdue"] is False
        assert result["horizon_total"] == 1000.0

    def test_overdue_invoice_is_flagged_and_totalled_apart(self, test_tenant):
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "contado", 0)
        _make_po(test_tenant["id"], name, 5, 200.0, sent_days_ago=10)

        result = cash.get_payables(test_tenant["id"], 30)
        item = result["due_items"][0]
        assert item["overdue"] is True
        assert item["days_until_due"] == -10
        assert result["overdue_total"] == 1000.0

    def test_supplier_without_usable_terms_is_reported_separately(self, test_tenant):
        """Money is owed but the date is unknown. It must not silently vanish
        from the calendar, nor be given an invented due date."""
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "a convenir", None)
        _make_po(test_tenant["id"], name, 4, 250.0, sent_days_ago=2)

        result = cash.get_payables(test_tenant["id"], 30)
        assert result["due_items"] == []
        assert len(result["unknown_terms"]) == 1
        assert result["unknown_terms"][0]["amount"] == 1000.0
        assert result["unknown_terms_total"] == 1000.0

    def test_this_week_total_only_counts_the_next_seven_days(self, test_tenant):
        soon = f"Soon-{uuid4().hex[:6]}"
        later = f"Later-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], soon, "10 dias", 10)
        _make_supplier_row(test_tenant["id"], later, "60 dias", 60)
        _make_po(test_tenant["id"], soon, 1, 500.0, sent_days_ago=5)    # due in 5 days
        _make_po(test_tenant["id"], later, 1, 900.0, sent_days_ago=5)   # due in 55 days

        result = cash.get_payables(test_tenant["id"], 90)
        assert result["this_week_total"] == 500.0
        assert result["horizon_total"] == 1400.0

    def test_payables_are_tenant_scoped(self, test_tenant):
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "30 dias", 30)
        _make_po(test_tenant["id"], name, 10, 100.0, sent_days_ago=1)

        other = cash.get_payables("tenant-that-does-not-exist", 30)
        assert other["due_items"] == []


class TestPurchaseFit:

    def test_purchase_fits_within_budget(self, test_tenant):
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "contado", 0)

        result = cash.evaluate_purchase_fit(
            test_tenant["id"],
            [{"sku": "S1", "supplier_name": name, "quantity": 10, "unit_cost": 50.0}],
            budget=1000.0, horizon_days=30,
        )
        assert result["purchase_total"] == 500.0
        assert result["purchase_in_horizon"] == 500.0
        assert result["fits"] is True
        assert result["shortfall"] == 0

    def test_purchase_does_not_fit_once_committed_invoices_count(self, test_tenant):
        """The same purchase, same budget — but invoices already committed eat
        the cash. This is the case the feature exists for."""
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "contado", 0)
        _make_po(test_tenant["id"], name, 10, 80.0, sent_days_ago=1)   # 800 already owed

        result = cash.evaluate_purchase_fit(
            test_tenant["id"],
            [{"sku": "S1", "supplier_name": name, "quantity": 10, "unit_cost": 50.0}],
            budget=1000.0, horizon_days=30,
        )
        assert result["committed_total"] == 800.0
        assert result["required_total"] == 1300.0
        assert result["fits"] is False
        assert result["shortfall"] == 300.0

    def test_long_credit_terms_push_the_purchase_out_of_the_window(self, test_tenant):
        """A 60-day-terms order placed today costs nothing in a 30-day window.
        Ignoring terms would wrongly report 'does not fit'."""
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "60 dias", 60)

        result = cash.evaluate_purchase_fit(
            test_tenant["id"],
            [{"sku": "S1", "supplier_name": name, "quantity": 100, "unit_cost": 50.0}],
            budget=1000.0, horizon_days=30,
        )
        assert result["purchase_total"] == 5000.0
        assert result["purchase_in_horizon"] == 0.0, "60-day terms must not hit a 30-day window"
        assert result["fits"] is True

    def test_unknown_terms_are_assumed_due_immediately(self, test_tenant):
        """Conservative side of an unknown: telling a buyer an order fits when
        it might be cash-on-delivery is the expensive mistake."""
        result = cash.evaluate_purchase_fit(
            test_tenant["id"],
            [{"sku": "S1", "supplier_name": "Nunca Registrado", "quantity": 10, "unit_cost": 50.0}],
            budget=100.0, horizon_days=30,
        )
        assert result["purchase_in_horizon"] == 500.0
        assert result["fits"] is False
        assert "Nunca Registrado" in result["suppliers_assumed_immediate"]
        assert result["lines"][0]["terms_known"] is False

    def test_without_a_budget_the_verdict_is_unknown_not_a_guess(self, test_tenant):
        """Faro stores no cash balance. With no budget supplied it reports the
        totals and refuses to invent a verdict."""
        result = cash.evaluate_purchase_fit(
            test_tenant["id"],
            [{"sku": "S1", "supplier_name": "X", "quantity": 10, "unit_cost": 50.0}],
            budget=None, horizon_days=30,
        )
        assert result["fits"] is None
        assert result["shortfall"] is None
        assert result["purchase_total"] == 500.0


# ── Endpoints ─────────────────────────────────────────────────────────────────

class TestCashCalendarEndpoints:

    def test_requires_auth(self, client):
        assert client.get("/api/v1/inventory/cash-calendar").status_code in (401, 403)

    def test_viewer_may_read_the_calendar(self, client, viewer_headers):
        """Read-only surface: a viewer must be able to see the cash picture."""
        data = _ok(client.get("/api/v1/inventory/cash-calendar", headers=viewer_headers))
        assert "due_items" in data and "weeks" in data

    def test_calendar_reflects_a_sent_po(self, client, auth_headers, test_tenant):
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "30 dias", 30)
        _make_po(test_tenant["id"], name, 10, 100.0, sent_days_ago=5)

        data = _ok(client.get("/api/v1/inventory/cash-calendar", headers=auth_headers))
        amounts = [d["amount"] for d in data["due_items"]]
        assert 1000.0 in amounts

    def test_fit_endpoint_returns_a_verdict(self, client, auth_headers, test_tenant):
        name = f"Prov-{uuid4().hex[:6]}"
        _make_supplier_row(test_tenant["id"], name, "contado", 0)

        data = _ok(client.post(
            "/api/v1/inventory/cash-calendar/fit", headers=auth_headers,
            json={
                "budget": 100.0,
                "items": [{"sku": "S1", "supplier_name": name,
                           "quantity": 10, "unit_cost": 50.0}],
            },
        ))
        assert data["purchase_total"] == 500.0
        assert data["fits"] is False


class TestMarkPOSent:

    def test_send_stamps_sent_at_only_once(self, test_tenant):
        """Resending must not move the due date of an invoice already issued."""
        from backend.inventory import reception_service as rec_svc
        name = f"Prov-{uuid4().hex[:6]}"
        po_id = _make_po(test_tenant["id"], name, 10, 100.0, sent_days_ago=None)

        assert query_one(
            "SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,),
        )["sent_at"] is None

        rec_svc.mark_po_sent(test_tenant["id"], po_id)
        first = query_one(
            "SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,),
        )["sent_at"]
        assert first is not None

        rec_svc.mark_po_sent(test_tenant["id"], po_id)
        second = query_one(
            "SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,),
        )["sent_at"]
        assert second == first, "Resending overwrote the original send time"

    def test_mark_po_sent_is_tenant_scoped(self, test_tenant):
        from backend.inventory import reception_service as rec_svc
        po_id = _make_po(test_tenant["id"], f"Prov-{uuid4().hex[:6]}", 5, 10.0)

        rec_svc.mark_po_sent("some-other-tenant", po_id)
        assert query_one(
            "SELECT sent_at FROM inventory_po_log WHERE id = %s", (po_id,),
        )["sent_at"] is None, "A foreign tenant was able to stamp this PO"
