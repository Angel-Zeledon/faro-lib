"""What deactivating a supplier means, decided 2026-08-23.

It means **stop acting towards them** — do not auto-send a purchase order, do
not offer them for new work. It does NOT mean "forget what we know about them".

That distinction was not being applied consistently, and the inconsistency was
invisible: three different sources answer "what is this supplier's lead time",
and exactly one of them reacted to a deactivation.

  · `suppliers.lead_time_days` (the card)      — disappeared from the rule index
  · a `stock_defaults` rule on the same name   — kept applying (free-text key)
  · the lead time LEARNED from receptions      — kept applying (free-text key)

So archiving a card silently pushed every SKU naming that supplier back to the
15-day system default — a worse number than the one the user had typed, applied
with no message anywhere. Now none of the three react, and the act that changes
a SKU's lead time is editing the SKU, not archiving a card.

The second half of the decision: deactivation is reversible. It always was
logical (`active = FALSE`) and never had an undo, so a mistaken drop was a
one-way door — and the 409 the create endpoint returns
(`supplier_name_taken_by_deactivated`) named a row the user had no verb for.
"""

from uuid import uuid4

import pytest

from backend.db.connection import execute, query_one


def _make_supplier(client, headers, name, lead_time_days=None):
    body = {"name": name}
    if lead_time_days is not None:
        body["lead_time_days"] = lead_time_days
    r = client.post("/api/v1/inventory/suppliers", json=body, headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _deactivate(client, headers, supplier_id):
    r = client.delete(f"/api/v1/inventory/suppliers/{supplier_id}", headers=headers)
    assert r.status_code == 204, r.text


def _is_active(supplier_id):
    return query_one("SELECT active FROM suppliers WHERE id = %s", (supplier_id,))["active"]


# ── 1. Knowledge survives a deactivation ─────────────────────────────────────

class TestADeactivatedSupplierStillAnswersForItsLeadTime:

    def test_the_rule_index_keeps_a_deactivated_cards_lead_time(
        self, client, analyst_headers, test_tenant,
    ):
        """The regression that matters: before this, the index dropped the row
        and every SKU naming it fell to the 15-day system default."""
        from backend.inventory import stock_defaults_service as sds

        name = f"Andina-{uuid4().hex[:6]}"
        sup = _make_supplier(client, analyst_headers, name, lead_time_days=40)

        idx = sds.build_rule_index(test_tenant["id"])
        assert idx["supplier"][name.lower()]["lead_time_days"] == 40

        _deactivate(client, analyst_headers, sup["id"])
        assert _is_active(sup["id"]) is False, "the fixture did not actually deactivate"

        idx = sds.build_rule_index(test_tenant["id"])
        assert name.lower() in idx["supplier"], (
            "the deactivated supplier vanished from the lead-time index — every SKU "
            "naming it just silently reverted to the system default"
        )
        assert idx["supplier"][name.lower()]["lead_time_days"] == 40

    def test_a_live_card_wins_a_case_only_collision_with_a_dead_one(
        self, client, analyst_headers, test_tenant,
    ):
        """Two cards differing only in case collapse to one key in the index
        (it is lowercased), so the ORDER BY has to put the live one first —
        otherwise a dead card's number answers for a live supplier."""
        from backend.inventory import stock_defaults_service as sds

        base = f"norte-{uuid4().hex[:6]}"
        dead = _make_supplier(client, analyst_headers, base.upper(), lead_time_days=99)
        _deactivate(client, analyst_headers, dead["id"])
        live = _make_supplier(client, analyst_headers, base.lower() + "x", lead_time_days=7)
        # Rename the live one onto the colliding spelling directly in the DB:
        # the API refuses it on purpose (that is the 409 under test elsewhere),
        # and what this test needs is the collision itself.
        execute("UPDATE suppliers SET name = %s WHERE id = %s", (base.lower(), live["id"]))

        idx = sds.build_rule_index(test_tenant["id"])
        assert idx["supplier"][base.lower()]["lead_time_days"] == 7, (
            "a deactivated card's lead time is answering for the live supplier"
        )


# ── 2. Deactivation is reversible ────────────────────────────────────────────

class TestReactivation:

    def test_analyst_can_bring_a_supplier_back_and_the_row_flips(
        self, client, analyst_headers,
    ):
        sup = _make_supplier(client, analyst_headers, f"Volver-{uuid4().hex[:6]}")
        _deactivate(client, analyst_headers, sup["id"])
        assert _is_active(sup["id"]) is False

        r = client.post(f"/api/v1/inventory/suppliers/{sup['id']}/reactivate",
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert _is_active(sup["id"]) is True, "the endpoint answered 200 and changed nothing"
        # And it is visible again to the reads that filter on `active`.
        listed = client.get("/api/v1/inventory/suppliers", headers=analyst_headers)
        assert any(s["id"] == sup["id"] for s in listed.json()["data"])

    def test_viewer_cannot_reactivate_and_the_row_does_not_move(
        self, client, analyst_headers, viewer_headers,
    ):
        """The permission pair. A viewer must not be able to put a supplier back
        into circulation — the next PO could be sent to them."""
        sup = _make_supplier(client, analyst_headers, f"Viewer-{uuid4().hex[:6]}")
        _deactivate(client, analyst_headers, sup["id"])

        r = client.post(f"/api/v1/inventory/suppliers/{sup['id']}/reactivate",
                        headers=viewer_headers)
        assert r.status_code == 403, r.text
        assert _is_active(sup["id"]) is False, "a viewer reactivated a supplier"

    def test_reactivating_a_live_supplier_is_a_no_op_not_an_error(
        self, client, analyst_headers,
    ):
        """Idempotent: a double click, or a retry after a dropped response, must
        not turn into a failure the user has to interpret."""
        sup = _make_supplier(client, analyst_headers, f"Idem-{uuid4().hex[:6]}")
        r = client.post(f"/api/v1/inventory/suppliers/{sup['id']}/reactivate",
                        headers=analyst_headers)
        assert r.status_code == 200, r.text
        assert _is_active(sup["id"]) is True

    def test_the_database_makes_a_duplicate_name_impossible_in_the_first_place(
        self, client, analyst_headers, test_tenant,
    ):
        """Why `reactivate_supplier` has no name check, pinned so the day that
        stops being true is loud.

        The worry is real in shape: reactivate a card whose name a live supplier
        has since taken, and two suppliers answer the same question. It is not
        reachable, because `UNIQUE (tenant_id, name)` does not exclude inactive
        rows — the database refuses the second row outright. That same index is
        why `create_supplier` DOES need a guard: it turns the UniqueViolation
        into a 409 that says something instead of a bare 500.

        If the index is ever made partial (`WHERE active`), this test fails and
        the check has to grow back in `reactivate_supplier`.
        """
        import psycopg2

        name = f"Unico-{uuid4().hex[:6]}"
        dead = _make_supplier(client, analyst_headers, name)
        _deactivate(client, analyst_headers, dead["id"])
        other = _make_supplier(client, analyst_headers, f"{name}-otro")

        with pytest.raises(psycopg2.errors.UniqueViolation):
            execute("UPDATE suppliers SET name = %s WHERE id = %s", (name, other["id"]))

        # And the API refuses the friendlier route into the same state.
        r = client.post("/api/v1/inventory/suppliers", json={"name": name},
                        headers=analyst_headers)
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "supplier_name_taken_by_deactivated"

    def test_reactivating_something_that_does_not_exist_is_a_404(
        self, client, analyst_headers,
    ):
        r = client.post("/api/v1/inventory/suppliers/sup_does_not_exist/reactivate",
                        headers=analyst_headers)
        assert r.status_code == 404, r.text


# ── 3. The one-shot repair for rows the old parser already spoiled ───────────

class TestPaymentTermsRepairScript:

    def test_it_finds_a_row_the_old_parser_stored_wrong(self, client, analyst_headers):
        """`"50% anticipo"` used to be stored as 50 days of credit for a payment
        that is due immediately. The corrected backfill only fills NULLs, so
        those rows keep the 50 until something goes and looks."""
        from backend.scripts.repair_payment_terms import plan, apply

        sup = _make_supplier(client, analyst_headers, f"Reparar-{uuid4().hex[:6]}")
        execute(
            "UPDATE suppliers SET payment_terms = %s, payment_terms_days = %s WHERE id = %s",
            ("50% anticipo", 50, sup["id"]),
        )

        changes = [c for c in plan() if c["id"] == sup["id"]]
        assert changes, "the script did not notice a row it was written for"
        assert changes[0]["stored"] == 50
        assert changes[0]["correct"] == 0, (
            "an advance payment is due now — zero days of credit, not fifty"
        )

        # Dry run must not have written anything.
        assert query_one("SELECT payment_terms_days AS d FROM suppliers WHERE id = %s",
                         (sup["id"],))["d"] == 50

        apply(changes)
        assert query_one("SELECT payment_terms_days AS d FROM suppliers WHERE id = %s",
                         (sup["id"],))["d"] == 0

    def test_it_leaves_a_row_the_parser_agrees_with_alone(self, client, analyst_headers):
        """It must only touch DISAGREEMENTS. A blanket rewrite would fight a user
        who typed a number the parser cannot see."""
        from backend.scripts.repair_payment_terms import plan

        sup = _make_supplier(client, analyst_headers, f"Intacto-{uuid4().hex[:6]}")
        execute(
            "UPDATE suppliers SET payment_terms = %s, payment_terms_days = %s WHERE id = %s",
            ("30 dias", 30, sup["id"]),
        )
        assert not [c for c in plan() if c["id"] == sup["id"]]
