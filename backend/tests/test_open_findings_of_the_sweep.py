"""The 13 findings section 11 left open, and what closing each one had to mean.

They were not open because they were hard. Each one needed a decision that is
the owner's and not a developer's — what "retrain" means, what counts as "still
in transit", what happens to history a migration cannot attribute. Those calls
were made on 2026-09-16 and are recorded in docs/estabilidad.md §11; this file
is what holds them.

One class per finding, named after the defect rather than the fix, so a failure
says which promise broke.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one


# ── 11.33 A PO line that fails to insert is swallowed ─────────────────────────

class TestAPurchaseOrderIsAllOfItsLinesOrNone:
    """The header used to commit on its own and every line INSERT was wrapped in
    `except: log.warning(...)`. A line that failed vanished from the supplier's
    fill rate, from `purchased_value` and from the PDF the supplier receives,
    while the header kept its full `sku_count` and `total_value`."""

    def test_a_line_that_cannot_be_written_rolls_the_whole_order_back(
        self, test_tenant, monkeypatch,
    ):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        before = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s", (tid,))

        real_execute = roi_service.execute
        calls = {"n": 0}

        def failing_execute(sql, params, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:          # the second LINE, after the header
                raise RuntimeError("line insert exploded")
            return real_execute(sql, params, **kwargs)

        monkeypatch.setattr(roi_service, "execute", failing_execute)

        with pytest.raises(RuntimeError):
            roi_service.log_po_generation(tid, f"sess_{uuid4().hex[:6]}", [
                {"sku": "ROLL-A", "signal": "PEDIR_YA", "recommended_qty": 5,
                 "final_qty": 5, "unit_cost": 1.0, "status": "approved"},
                {"sku": "ROLL-B", "signal": "PEDIR_YA", "recommended_qty": 7,
                 "final_qty": 7, "unit_cost": 1.0, "status": "approved"},
            ])

        after = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s", (tid,))
        assert after["n"] == before["n"], (
            "the header survived a failed line: an order that says two lines "
            "while the database holds one"
        )
        assert query(
            "SELECT id FROM inventory_po_items WHERE tenant_id=%s AND sku IN ('ROLL-A','ROLL-B')",
            (tid,),
        ) == []

    def test_a_healthy_order_still_writes_every_line(self, test_tenant):
        from backend.inventory import roi_service

        tid = test_tenant["id"]
        po = roi_service.log_po_generation(tid, f"sess_{uuid4().hex[:6]}", [
            {"sku": "OK-A", "signal": "PEDIR_YA", "recommended_qty": 5,
             "final_qty": 5, "unit_cost": 2.0, "status": "approved"},
            {"sku": "OK-B", "signal": "PEDIR_PRONTO", "recommended_qty": 3,
             "final_qty": 3, "unit_cost": 1.0, "status": "rejected"},
        ])
        rows = query(
            "SELECT sku, status FROM inventory_po_items WHERE po_log_id=%s ORDER BY sku",
            (po["id"],))
        assert [r["sku"] for r in rows] == ["OK-A", "OK-B"]
        # The rejected line is persisted too: adoption is audited per SKU.
        assert rows[1]["status"] == "rejected"

    def test_a_manual_order_is_atomic_too(self, test_tenant, monkeypatch):
        """Typed line by line, so a line lost on the way to the database is a
        line the buyer wrote and nobody will ever see again."""
        from backend.inventory import roi_service, supplier_service as sup_svc

        tid = test_tenant["id"]
        supplier = sup_svc.create_supplier(tid, {"name": f"Prov-{uuid4().hex[:6]}"})
        before = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s", (tid,))

        real_execute = roi_service.execute
        monkeypatch.setattr(
            roi_service, "execute",
            lambda sql, params, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError):
            roi_service.create_manual_po(tid, supplier, [{"sku": "MAN-A", "qty": 4}])
        monkeypatch.setattr(roi_service, "execute", real_execute)

        after = query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s", (tid,))
        assert after["n"] == before["n"]


# ── 11.34 An import row that fails to write is dropped with a log ─────────────

class TestAnImportNamesTheRowsItCouldNotWrite:
    """`imported` did shrink, so the number was never a lie — but "83 products
    imported" after a clean 120-row preview was the only signal, and it named
    neither the 37 rows nor a reason."""

    def test_a_row_that_fails_to_write_is_reported_with_its_sku(self, test_tenant, monkeypatch):
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        real_upsert = inv_svc.upsert_stock

        def flaky(tenant_id, sku, data, **kwargs):
            if sku == "BAD":
                raise RuntimeError("write exploded")
            return real_upsert(tenant_id, sku, data, **kwargs)

        monkeypatch.setattr(inv_svc, "upsert_stock", flaky)

        failures: list[dict] = []
        written = inv_svc.bulk_upsert(
            tid,
            [{"sku": "GOOD", "current_stock": 5}, {"sku": "BAD", "current_stock": 9}],
            failures=failures,
        )

        assert written == 1
        assert len(failures) == 1
        assert failures[0]["sku"] == "BAD"
        # A code, not the driver's English sentence: this reaches a Spanish
        # screen through the i18n catalogue.
        assert failures[0]["code"] == "inventory_import_row_write_failed"

    def test_the_endpoint_hands_those_rows_back_to_the_user(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        from backend.inventory import service as inv_svc

        real_upsert = inv_svc.upsert_stock

        def flaky(tenant_id, sku, data, **kwargs):
            if sku == "EV-BAD":
                raise RuntimeError("write exploded")
            return real_upsert(tenant_id, sku, data, **kwargs)

        monkeypatch.setattr(inv_svc, "upsert_stock", flaky)

        csv_text = "sku,current_stock\nEV-OK,10\nEV-BAD,20\n"
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("f.csv", csv_text, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["imported"] == 1
        assert data["total_rows"] == 2
        reported = [e for e in data.get("errors", []) if e.get("sku") == "EV-BAD"]
        assert reported, "the row never reached the database and the response said nothing"
        assert reported[0]["code"] == "inventory_import_row_write_failed"


# ── 11.15 Stock snapshots have no warehouse column ────────────────────────────

class TestStockHistoryIsNotTwoWarehousesInterleaved:
    """principal at 500 and Norte at 20 produced the series 500, 20, 500, 20,
    and `_calc_demand_trend` read that difference as real consumption — "+585%
    demand" that never happened. One transfer did it on its own."""

    def _snapshot(self, tid, sku, stock, warehouse, minutes_ago):
        execute(
            """INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse,
                                                recorded_at)
               VALUES (%s, %s, %s, %s, NOW() - (%s || ' minutes')::INTERVAL)""",
            (tid, sku, stock, warehouse, minutes_ago),
        )

    def test_the_tenant_wide_series_sums_warehouses_instead_of_alternating(
        self, client, test_tenant,
    ):
        from backend.inventory.service import get_stock_history

        tid, sku = test_tenant["id"], "HIST-1"
        # One day, two warehouses, written alternately — the shape that produced
        # the sawtooth.
        self._snapshot(tid, sku, 500, "principal", 50)
        self._snapshot(tid, sku, 20,  "Norte",     45)
        self._snapshot(tid, sku, 480, "principal", 40)
        self._snapshot(tid, sku, 18,  "Norte",     35)

        series = [h["stock"] for h in get_stock_history(tid, sku, days=2)]
        assert series == [498], (
            f"expected one tenant-wide level for the day (480+18), got {series}"
        )

    def test_one_warehouse_reads_only_its_own_rows(self, client, test_tenant):
        from backend.inventory.service import get_stock_history

        tid, sku = test_tenant["id"], "HIST-2"
        self._snapshot(tid, sku, 500, "principal", 50)
        self._snapshot(tid, sku, 20,  "Norte",     45)

        assert [h["stock"] for h in get_stock_history(tid, sku, days=2, warehouse="Norte")] == [20]

    def test_rows_written_before_the_column_are_read_as_tenant_wide(self, client, test_tenant):
        """Owner's call (2026-09-16): no backfill. A NULL row is a total, which
        is what it always was — it is not attributed to a warehouse."""
        from backend.inventory.service import get_stock_history

        tid, sku = test_tenant["id"], "HIST-3"
        self._snapshot(tid, sku, 900, None, 60)          # legacy
        self._snapshot(tid, sku, 100, "principal", 30)

        assert [h["stock"] for h in get_stock_history(tid, sku, days=2)] == [900, 100]
        # ...and it does not leak into a location's own history.
        assert get_stock_history(tid, sku, days=2, warehouse="principal")[0]["stock"] == 100
        assert len(get_stock_history(tid, sku, days=2, warehouse="principal")) == 1

    def test_a_transfer_no_longer_looks_like_consumption(self, client, test_tenant):
        """The loud case: moving 30 units between two warehouses changes no
        total, so the tenant-wide series must not move either."""
        from backend.inventory import service as inv_svc
        from backend.inventory import transfer_service as tr_svc
        from backend.inventory import warehouse_service as wh_svc

        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        wh_svc.create_warehouse(tid, "Norte")
        inv_svc.upsert_stock(tid, "MOVE-1", {"current_stock": 100, "warehouse": "principal"})
        inv_svc.upsert_stock(tid, "MOVE-1", {"current_stock": 10, "warehouse": "Norte"})

        tr_svc.create_transfer(tid, "usr_test", "principal", "Norte",
                               [{"sku": "MOVE-1", "qty": 30}])

        series = [h["stock"] for h in inv_svc.get_stock_history(tid, "MOVE-1", days=2)]
        # Same day, so one point: what the tenant holds after the move. The
        # units in transit have left principal and not yet arrived, which is why
        # 80 and not 110 — but it is one honest level, not 100, 10, 70.
        assert len(series) == 1, f"a transfer produced {len(series)} tenant-wide levels: {series}"


# ── 11.12 Fill rate punishes orders still in transit ──────────────────────────

class TestFillRateJudgesOnlyDeliveriesThatAreDue:
    """It summed what had arrived against the FULL ordered quantity for every
    `partial` and `not_received` order, with no notion of a delivery still being
    on its way. Two half-delivered orders, both on schedule, printed 50% — as a
    performance verdict, with the supplier who had shorted nothing reading worst
    on the page."""

    def _supplier(self, tid, name, lead_time_days):
        execute(
            """INSERT INTO suppliers (tenant_id, name, lead_time_days, lead_time_set_by)
               VALUES (%s, %s, %s, 'user')""",
            (tid, name, lead_time_days),
        )

    def _po(self, client, headers, *, sku, qty, supplier, days_ago):
        resp = client.post(
            "/api/v1/inventory/log-po",
            params={"session_id": f"sess_{uuid4().hex[:6]}"},
            json={"items": [{
                "sku": sku, "supplier": supplier, "signal": "PEDIR_YA",
                "recommended_qty": qty, "final_qty": qty, "unit_cost": 1.0,
                "status": "approved",
            }]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        po_id = resp.json()["data"]["id"]
        execute(
            "UPDATE inventory_po_log SET generated_at = NOW() - (%s || ' days')::INTERVAL "
            "WHERE id = %s", (days_ago, po_id),
        )
        return po_id

    def _row_for(self, tid, supplier):
        from backend.inventory.reception_service import get_supplier_scorecard
        rows = [r for r in get_supplier_scorecard(tid) if r["supplier"] == supplier]
        return rows[0] if rows else None

    def test_a_half_delivered_order_inside_its_window_does_not_count(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        prov = f"OnTime-{uuid4().hex[:6]}"
        self._supplier(tid, prov, lead_time_days=30)

        po = self._po(client, auth_headers, sku="FR-1", qty=100, supplier=prov, days_ago=3)
        resp = client.post(f"/api/v1/inventory/po/{po}/receive",
                           json={"lines": [{"sku": "FR-1", "received_qty": 50}]},
                           headers=auth_headers)
        assert resp.status_code == 200, resp.text

        row = self._row_for(tid, prov)
        assert row is not None
        assert row["fill_rate"] is None, (
            f"a delivery 3 days into a 30-day window was scored: {row['fill_rate']}"
        )
        # ...and the row says why it is empty, instead of looking like a
        # supplier nobody has ever bought from.
        assert row["orders_in_transit"] == 1

    def test_the_same_order_counts_once_its_window_has_closed(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        prov = f"Late-{uuid4().hex[:6]}"
        self._supplier(tid, prov, lead_time_days=5)

        po = self._po(client, auth_headers, sku="FR-2", qty=100, supplier=prov, days_ago=40)
        client.post(f"/api/v1/inventory/po/{po}/receive",
                    json={"lines": [{"sku": "FR-2", "received_qty": 50}]},
                    headers=auth_headers)

        row = self._row_for(tid, prov)
        assert row["fill_rate"] == 0.5, row["fill_rate"]
        assert row["orders_in_transit"] == 0

    def test_a_fully_received_order_is_judged_immediately(
        self, client, auth_headers, test_tenant,
    ):
        """Nothing is left to arrive, so there is no window to wait out."""
        tid = test_tenant["id"]
        prov = f"Full-{uuid4().hex[:6]}"
        self._supplier(tid, prov, lead_time_days=60)

        po = self._po(client, auth_headers, sku="FR-3", qty=10, supplier=prov, days_ago=1)
        client.post(f"/api/v1/inventory/po/{po}/receive",
                    json={"lines": [{"sku": "FR-3", "received_qty": 10}]},
                    headers=auth_headers)

        row = self._row_for(tid, prov)
        assert row["fill_rate"] == 1.0
        assert row["orders_in_transit"] == 0

    def test_money_already_spent_is_not_held_back_by_the_window(
        self, client, auth_headers, test_tenant,
    ):
        """`purchased_value` is what left the company when the order was placed,
        whatever is still on the road."""
        tid = test_tenant["id"]
        prov = f"Value-{uuid4().hex[:6]}"
        self._supplier(tid, prov, lead_time_days=45)

        po = self._po(client, auth_headers, sku="FR-4", qty=7, supplier=prov, days_ago=2)
        client.post(f"/api/v1/inventory/po/{po}/receive",
                    json={"lines": [{"sku": "FR-4", "received_qty": 1}]},
                    headers=auth_headers)

        row = self._row_for(tid, prov)
        assert row["fill_rate"] is None
        assert row["purchased_value"] == 7.0


# ── 11.28 The daily and monthly loops keep no last-run marker ─────────────────

class TestARestartCannotSkipADayOfAlerts:
    """Each loop computed `next_run` from `datetime.now()` and persisted
    nothing, so a worker killed at 07:55 and restarted at 08:02 asked for the
    next 08:00 boundary AFTER now and slept until tomorrow. That day nobody got
    a digest, a lead-time alert or a freshness reminder, and no activity row was
    written — it looked exactly like a calm day."""

    def _clear(self, loop):
        execute("DELETE FROM system_loop_runs WHERE loop = %s", (loop,))

    def test_the_boundary_that_just_passed_is_caught_up(self, client):
        from backend.workers import loop_state

        loop = loop_state.INVENTORY_ALERTS
        self._clear(loop)
        now = datetime(2026, 9, 16, 8, 2, tzinfo=timezone.utc)
        boundary = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)

        # A first-ever boot does NOT fire: a fresh install would otherwise mail
        # a digest before anybody had uploaded a file.
        assert loop_state.missed_boundary(loop, boundary, now, loop_state.DAILY_CATCHUP) is None
        # ...and having been here yesterday, the missed pass IS caught up.
        loop_state.mark_run(loop, datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc))
        assert loop_state.missed_boundary(
            loop, boundary, now, loop_state.DAILY_CATCHUP) == boundary

    def test_a_boundary_already_run_is_not_run_twice(self, client):
        """The cost of getting this wrong is a second digest to everybody the
        first one reached."""
        from backend.workers import loop_state

        loop = loop_state.INVENTORY_ALERTS
        self._clear(loop)
        boundary = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)
        loop_state.mark_run(loop, boundary)

        now = datetime(2026, 9, 16, 8, 30, tzinfo=timezone.utc)
        assert loop_state.missed_boundary(loop, boundary, now, loop_state.DAILY_CATCHUP) is None

    def test_a_boundary_older_than_the_window_is_recorded_as_skipped(self, client):
        """Yesterday's digest sent today describes a day the buyer has already
        lived through. The gap becomes a row somebody can read instead of a day
        that silently produced nothing."""
        from backend.workers import loop_state

        loop = loop_state.INVENTORY_ALERTS
        self._clear(loop)
        loop_state.mark_run(loop, datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))

        boundary = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)
        now = datetime(2026, 9, 16, 7, 0, tzinfo=timezone.utc)      # 23 h late
        assert loop_state.missed_boundary(loop, boundary, now, loop_state.DAILY_CATCHUP) is None

        row = query_one(
            "SELECT last_boundary, last_status, last_error FROM system_loop_runs WHERE loop=%s",
            (loop,))
        assert row["last_status"] == loop_state.STATUS_SKIPPED
        assert row["last_error"] == "missed_beyond_catchup_window"
        # And it moved on: the skipped boundary is accounted for, so the loop
        # does not keep re-deciding it forever.
        assert row["last_boundary"].replace(tzinfo=timezone.utc) == boundary

    def test_the_monthly_pass_gets_three_days_because_nothing_else_can_take_it(
        self, client,
    ):
        """The snapshot on the 1st is the CLOSING measurement of the month that
        just ended; a missed 1st breaks that month's capital-freed figure
        permanently."""
        from backend.workers import loop_state

        loop = loop_state.MONTHLY_OVERSTOCK
        self._clear(loop)
        loop_state.mark_run(loop, datetime(2026, 8, 1, 0, 5, tzinfo=timezone.utc))

        boundary = datetime(2026, 9, 1, 0, 5, tzinfo=timezone.utc)
        two_days_late = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
        assert loop_state.missed_boundary(
            loop, boundary, two_days_late, loop_state.MONTHLY_CATCHUP) == boundary

        self._clear(loop)
        loop_state.mark_run(loop, datetime(2026, 8, 1, 0, 5, tzinfo=timezone.utc))
        a_week_late = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)
        assert loop_state.missed_boundary(
            loop, boundary, a_week_late, loop_state.MONTHLY_CATCHUP) is None

    def test_the_boundary_helpers_name_the_pass_that_already_happened(self):
        from backend.workers.worker import _previous_daily_run, _previous_month_start

        now = datetime(2026, 9, 16, 8, 2, tzinfo=timezone.utc)
        assert _previous_daily_run(now, 8) == datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)
        # Before today's boundary, the previous one is yesterday's.
        early = datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)
        assert _previous_daily_run(early, 8) == datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)
        # Month arithmetic must not blow up in January.
        assert _previous_month_start(
            datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
        ) == datetime(2025, 12, 1, 0, 5, tzinfo=timezone.utc)

    def test_a_failure_to_record_never_takes_the_loop_down(self, client, monkeypatch):
        """The pass that just ran is worth more than its marker."""
        from backend.workers import loop_state

        monkeypatch.setattr(
            loop_state, "execute",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
        loop_state.mark_run(loop_state.INVENTORY_ALERTS,
                            datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc))   # must not raise


# ── 11.5 ERP stock all lands in `principal` while sales carry the branch ──────

class TestABranchWithNoStockRowIsNotToldToBuyEverything:
    """`fetch_stock` hardcodes warehouse='principal' in both providers while
    `fetch_sales` reads the real branch off each invoice. Every branch then has
    demand and no stock row, and `current_stock or 0.0` turned "we were never
    told" into "there are none" — PEDIR_YA at full reorder quantity for the
    whole catalogue at every branch, with the goods sitting in principal.

    The fetch itself cannot be fixed without a real account to read the
    per-warehouse payload against (owner's call, 2026-09-16). What is fixed is
    the consequence.
    """

    def _session_with_store_forecasts(self, tid):
        """A session whose forecasts are keyed per (sku, store) — the shape the
        ERP sync produces once sales carry a branch."""
        from backend.db import session_store
        session_id = f"sess_{uuid4().hex[:8]}"
        execute(
            """INSERT INTO sessions (id, tenant_id, name, status, created_by)
               VALUES (%s, %s, 'ERP sync', 'COMPLETED', 'usr_test')""",
            (session_id, tid),
        )
        curve = [{"date": f"2026-10-{d:02d}", "value": 10.0} for d in range(1, 29)]
        session_store.set_forecasts(tid, session_id, {
            "WIDGET│principal": {"prophet": {"forecast": curve}},
            "WIDGET│Norte":     {"prophet": {"forecast": curve}},
        })
        return session_id

    def test_the_branch_reads_sin_datos_instead_of_pedir_ya(self, client, test_tenant):
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc

        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        wh_svc.create_warehouse(tid, "Norte")
        # Exactly what the sync writes: every unit in principal, nothing else.
        inv_svc.upsert_stock(tid, "WIDGET", {"current_stock": 500, "warehouse": "principal"})

        session_id = self._session_with_store_forecasts(tid)
        rows = inv_svc.get_inventory_status_by_warehouse(tid, session_id)
        by_wh = {r["warehouse"]: r for r in rows if r["sku"] == "WIDGET"}

        assert by_wh["Norte"]["signal"] == "SIN_DATOS", (
            f"Norte was told to buy: {by_wh['Norte']['signal']} "
            f"qty={by_wh['Norte']['recommended_qty']}"
        )
        assert by_wh["Norte"]["recommended_qty"] is None
        # ...and it says why, so the buyer can fix it instead of guessing.
        assert by_wh["Norte"]["sin_datos_reason"] == "stock_not_recorded_in_this_warehouse"
        # The warehouse that DOES hold stock is unaffected.
        assert by_wh["principal"]["signal"] != "SIN_DATOS"
        assert by_wh["principal"]["sin_datos_reason"] is None

    def test_a_tenant_that_really_keeps_stock_per_warehouse_is_untouched(
        self, client, test_tenant,
    ):
        """A missing row means what it says once a tenant has proved it records
        stock in more than one place: they did not stock this SKU there."""
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc

        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        wh_svc.create_warehouse(tid, "Norte")
        inv_svc.upsert_stock(tid, "WIDGET", {"current_stock": 500, "warehouse": "principal"})
        # Another SKU recorded in Norte: this tenant does keep stock per branch.
        inv_svc.upsert_stock(tid, "OTHER", {"current_stock": 3, "warehouse": "Norte"})

        session_id = self._session_with_store_forecasts(tid)
        rows = inv_svc.get_inventory_status_by_warehouse(tid, session_id)
        norte = next(r for r in rows if r["sku"] == "WIDGET" and r["warehouse"] == "Norte")

        assert norte["signal"] != "SIN_DATOS"
        assert norte["sin_datos_reason"] is None


# ── 11.6 A scheduled retrain runs on the COMPLETED session ────────────────────

class TestAScheduledRetrainCannotBlankTheProduct:
    """`create_job(tenant_id, session_id)` retrained the session the whole app
    was reading. `runner.py` marks a failed run FAILED and
    `resolve_active_session` only returns COMPLETED sessions, so an engine error
    at 3 a.m. left /hoy, the semáforo and the digest with nothing — and the only
    trace was `scheduled_jobs.last_error`, on a screen nobody opens because
    nothing announced a problem.

    The owner's call (2026-09-16) was the most complete option: each run trains
    a NEW session and only replaces what the buyer reads once it succeeds.
    """

    def _template(self, tid, *, status="COMPLETED"):
        """A trained session with a dataset and the two required configs — what
        a schedule points at."""
        from backend.db import session_store

        dataset_id = f"ds_{uuid4().hex[:8]}"
        execute(
            """INSERT INTO datasets (id, tenant_id, name, original_filename, file_path,
                                     file_type, row_count, uploaded_by)
               VALUES (%s, %s, 'sales.csv', 'sales.csv', 'x/sales.csv', 'csv', 10, 'usr_test')""",
            (dataset_id, tid),
        )
        session_id = f"sess_{uuid4().hex[:8]}"
        execute(
            """INSERT INTO sessions (id, tenant_id, name, status, created_by, dataset_id)
               VALUES (%s, %s, 'Semanal', %s, 'usr_test', %s)""",
            (session_id, tid, status, dataset_id),
        )
        execute(
            "INSERT INTO session_configs (session_id, tenant_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (session_id, tid),
        )
        session_store.set_field(tid, session_id, "columns_cfg", {"sku": "sku"})
        session_store.set_field(tid, session_id, "models_cfg", {"models": ["prophet"]})
        return session_id

    def _schedule(self, tid, session_id):
        sched_id = f"sched-{uuid4().hex[:8]}"
        execute(
            """INSERT INTO scheduled_jobs (id, tenant_id, session_id, cron_expr, next_run)
               VALUES (%s, %s, %s, '0 6 * * 1', NOW())""",
            (sched_id, tid, session_id),
        )
        return sched_id

    def test_the_run_trains_a_new_session_and_leaves_the_template_alone(
        self, client, test_tenant, monkeypatch,
    ):
        from backend.sessions import family_service, retrain_service

        tid = test_tenant["id"]
        template = self._template(tid)
        sched = self._schedule(tid, template)

        launched = {}
        monkeypatch.setattr(
            family_service, "launch_training_family",
            lambda tenant_id, session_id, user_id, **kw: launched.setdefault(
                "session_id", session_id) and None or {"base_job_id": "job_x"},
        )

        retrain_service.launch_scheduled_retrain(tid, sched, template)

        run_id = launched["session_id"]
        assert run_id != template, "the schedule retrained the session serving the app"
        run = query_one("SELECT status, dataset_id, scheduled_job_id FROM sessions WHERE id=%s",
                        (run_id,))
        assert run["scheduled_job_id"] == sched
        assert run["status"] == "MODELS_CONFIGURED"
        # It carries the template's data and configuration, or it would train
        # something the user never approved.
        template_row = query_one("SELECT dataset_id, status FROM sessions WHERE id=%s",
                                 (template,))
        assert run["dataset_id"] == template_row["dataset_id"]
        assert template_row["status"] == "COMPLETED", "the template was touched"
        assert query_one(
            "SELECT columns_cfg, models_cfg FROM session_configs WHERE session_id=%s",
            (run_id,))["models_cfg"] == {"models": ["prophet"]}

    def test_a_second_run_is_skipped_while_the_first_is_still_training(
        self, client, test_tenant, monkeypatch,
    ):
        """An hourly preset over a two-hour training queued B while A ran, and
        both wrote results for one session_id."""
        from backend.sessions import family_service, retrain_service

        tid = test_tenant["id"]
        template = self._template(tid)
        sched = self._schedule(tid, template)
        monkeypatch.setattr(family_service, "launch_training_family",
                            lambda *a, **k: {"base_job_id": "job_x"})

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        # The first run is in flight: QUEUED is what launch_training_family
        # leaves behind, and here it was faked, so set it explicitly.
        first = query_one(
            "SELECT id FROM sessions WHERE tenant_id=%s AND scheduled_job_id=%s",
            (tid, sched))["id"]
        execute("UPDATE sessions SET status='RUNNING' WHERE id=%s", (first,))

        assert retrain_service.launch_scheduled_retrain(tid, sched, template) is None
        assert query_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE tenant_id=%s AND scheduled_job_id=%s",
            (tid, sched))["n"] == 1

    def test_the_schedule_reuses_its_own_slot_instead_of_filling_the_ceiling(
        self, client, test_tenant, monkeypatch,
    ):
        """A saved forecast is a plan ceiling (3 on free). A daily schedule that
        kept every run filled it in three days."""
        from backend.sessions import family_service, retrain_service

        tid = test_tenant["id"]
        template = self._template(tid)
        sched = self._schedule(tid, template)
        monkeypatch.setattr(family_service, "launch_training_family",
                            lambda *a, **k: {"base_job_id": "job_x"})

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        first = query_one(
            "SELECT id FROM sessions WHERE tenant_id=%s AND scheduled_job_id=%s",
            (tid, sched))["id"]
        execute("UPDATE sessions SET status='COMPLETED' WHERE id=%s", (first,))

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        rows = query(
            "SELECT id, status FROM sessions WHERE tenant_id=%s AND scheduled_job_id=%s "
            "ORDER BY created_at", (tid, sched))
        # The one serving and the one training. Never three.
        assert len(rows) == 2, [r["status"] for r in rows]
        assert first in [r["id"] for r in rows], (
            "the session the buyer is reading was deleted to make room"
        )
        # The template, which a person created, is never in scope for the prune.
        assert query_one("SELECT id FROM sessions WHERE id=%s", (template,)) is not None

    def test_a_failed_run_is_pruned_before_the_next_one(
        self, client, test_tenant, monkeypatch,
    ):
        from backend.sessions import family_service, retrain_service

        tid = test_tenant["id"]
        template = self._template(tid)
        sched = self._schedule(tid, template)
        monkeypatch.setattr(family_service, "launch_training_family",
                            lambda *a, **k: {"base_job_id": "job_x"})

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        failed = query_one(
            "SELECT id FROM sessions WHERE tenant_id=%s AND scheduled_job_id=%s",
            (tid, sched))["id"]
        execute("UPDATE sessions SET status='FAILED' WHERE id=%s", (failed,))

        retrain_service.launch_scheduled_retrain(tid, sched, template)
        remaining = query(
            "SELECT id FROM sessions WHERE tenant_id=%s AND scheduled_job_id=%s", (tid, sched))
        assert len(remaining) == 1
        assert remaining[0]["id"] != failed

    def test_a_template_that_cannot_train_is_refused_with_a_reason(
        self, client, test_tenant,
    ):
        """The user-facing path validates the session, its configs and the
        active-job cap. The scheduler validated nothing at all."""
        from backend.errors import AppError
        from backend.sessions import retrain_service

        tid = test_tenant["id"]

        # No such session.
        with pytest.raises(AppError) as missing:
            retrain_service.launch_scheduled_retrain(tid, "sched-x", "sess_nope")
        assert missing.value.code == "scheduled_retrain_template_missing"

        # A session with no configuration behind it.
        bare = f"sess_{uuid4().hex[:8]}"
        execute(
            """INSERT INTO sessions (id, tenant_id, name, status, created_by)
               VALUES (%s, %s, 'Sin configurar', 'DRAFT', 'usr_test')""",
            (bare, tid),
        )
        with pytest.raises(AppError) as no_dataset:
            retrain_service.launch_scheduled_retrain(tid, "sched-x", bare)
        assert no_dataset.value.code == "scheduled_retrain_template_has_no_dataset"


# ── 11.14 The price-break panel quotes a supplier the buyer already changed ───

class TestThePriceBreakPanelQuotesTheSupplierOnTheLine:
    """`compras/page.tsx` sent only {sku, quantity}, so the supplier came from
    the status row and switching supplier on a line did not even re-evaluate.
    The panel promised a price only the OTHER supplier ever quoted."""

    def _two_ladders(self, tid, sku):
        from backend.inventory import supplier_service as sup_svc
        from backend.inventory import price_break_service as pb_svc
        from backend.inventory import service as inv_svc

        andina = sup_svc.create_supplier(tid, {"name": f"Andina-{uuid4().hex[:4]}"})
        norte = sup_svc.create_supplier(tid, {"name": f"Norte-{uuid4().hex[:4]}"})
        inv_svc.upsert_stock(tid, sku, {"current_stock": 10, "unit_cost": 10.0,
                                        "supplier": andina["name"]})
        pb_svc.upsert_price_break(tid, andina["id"], sku, min_qty=100, unit_price=9.0)
        pb_svc.upsert_price_break(tid, norte["id"], sku, min_qty=100, unit_price=5.0)
        return andina, norte

    def test_the_cart_line_supplier_decides_which_ladder_is_quoted(self, client, test_tenant):
        from backend.inventory import price_break_service as pb_svc

        tid = test_tenant["id"]
        sku = f"PB-{uuid4().hex[:6]}"
        andina, norte = self._two_ladders(tid, sku)
        status = [{
            "sku": sku, "supplier": andina["name"], "supplier_id": andina["id"],
            "unit_cost": 10.0, "daily_demand": 5.0, "current_stock": 10,
            "lead_time_days": 7,
        }]

        # The buyer switched this line to Norte. Norte's rung is what may be
        # quoted — the saving is real only if the supplier receiving the order
        # is the one who offered it.
        out = pb_svc.evaluate_cart(
            tid, [{"sku": sku, "quantity": 50, "supplier_id": norte["id"]}], status)
        assert out, "the chosen supplier's ladder was not evaluated"
        assert out[0]["supplier_name"] == norte["name"]

    def test_a_supplier_with_no_ladder_is_quoted_nothing_rather_than_somebody_elses(
        self, client, test_tenant,
    ):
        from backend.inventory import price_break_service as pb_svc
        from backend.inventory import supplier_service as sup_svc

        tid = test_tenant["id"]
        sku = f"PB-{uuid4().hex[:6]}"
        andina, _norte = self._two_ladders(tid, sku)
        third = sup_svc.create_supplier(tid, {"name": f"Sin-escala-{uuid4().hex[:4]}"})
        status = [{
            "sku": sku, "supplier": andina["name"], "supplier_id": andina["id"],
            "unit_cost": 10.0, "daily_demand": 5.0, "current_stock": 10,
            "lead_time_days": 7,
        }]

        out = pb_svc.evaluate_cart(
            tid, [{"sku": sku, "quantity": 50, "supplier_id": third["id"]}], status)
        assert out == [], "a supplier who quoted no ladder was credited with one"

    def test_the_endpoint_passes_the_line_supplier_through(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sku = f"PB-{uuid4().hex[:6]}"
        _andina, norte = self._two_ladders(tid, sku)

        resp = client.post(
            f"/api/v1/inventory/price-breaks/evaluate?session_id=sess_{uuid4().hex[:6]}",
            json={"items": [{"sku": sku, "quantity": 50, "supplier_id": norte["id"]}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        # No session results behind this id, so there is nothing to quote — what
        # is being asserted is that the field is accepted and travels, not the
        # arithmetic (covered above against the service).
        assert "opportunities" in resp.json()["data"]


# ── 11.7 Every /inventario export ignores the open warehouse tab ──────────────

class TestTheExportFollowsTheWarehouseTabThatIsOpen:
    """The download menu sits above the warehouse selector and stayed enabled
    with a warehouse tab open, so the buyer read "Norte needs 40" and downloaded
    a file saying 150 — and logPOGeneration wrote that into /pedidos as an order
    they never saw."""

    def test_the_endpoint_takes_a_warehouse_and_exports_its_rows(
        self, client, auth_headers, test_tenant,
    ):
        import csv as _csv
        import io as _io
        from backend.db import session_store
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc

        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        wh_svc.create_warehouse(tid, "Norte")
        inv_svc.upsert_stock(tid, "EXP-1", {"current_stock": 200, "warehouse": "principal"})
        inv_svc.upsert_stock(tid, "EXP-1", {"current_stock": 0, "warehouse": "Norte"})

        session_id = f"sess_{uuid4().hex[:8]}"
        execute(
            """INSERT INTO sessions (id, tenant_id, name, status, created_by)
               VALUES (%s, %s, 'Export', 'COMPLETED', 'usr_test')""",
            (session_id, tid),
        )
        curve = [{"date": f"2026-10-{d:02d}", "value": 20.0} for d in range(1, 29)]
        session_store.set_forecasts(tid, session_id, {
            "EXP-1│principal": {"prophet": {"forecast": curve}},
            "EXP-1│Norte":     {"prophet": {"forecast": curve}},
        })

        resp = client.get(
            f"/api/v1/inventory/status/export-po?session_id={session_id}&warehouse=Norte",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        rows = list(_csv.reader(_io.StringIO(resp.content.decode("utf-8-sig"))))
        body = [r for r in rows[1:] if r]
        # Norte is empty and needs the goods; principal is full and needs
        # nothing. The tenant-wide view nets them out — which is exactly why the
        # file used to disagree with the tab.
        assert body, "the warehouse tab had rows to order and the file had none"
        assert all(r[0] == "EXP-1" for r in body)

    def test_the_order_logged_behind_the_file_is_the_same_list(
        self, client, auth_headers, test_tenant,
    ):
        """`exportInventoryPO` downloads the file and then logs the order. The
        download was scoped to the warehouse and the LOG was not, so a buyer who
        exported an empty Norte got an order for two SKUs in /pedidos that they
        never saw on screen — 11.7 surviving one function to the left. Found by
        walking the screen, not by a test."""
        from backend.db import session_store
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc

        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        wh_svc.create_warehouse(tid, "Norte")
        # Everything is in principal; Norte has no stock row at all.
        inv_svc.upsert_stock(tid, "LOG-1", {"current_stock": 5, "unit_cost": 1.0,
                                            "warehouse": "principal"})

        session_id = f"sess_{uuid4().hex[:8]}"
        execute(
            """INSERT INTO sessions (id, tenant_id, name, status, created_by)
               VALUES (%s, %s, 'Log', 'COMPLETED', 'usr_test')""",
            (session_id, tid),
        )
        curve = [{"date": f"2026-10-{d:02d}", "value": 30.0} for d in range(1, 29)]
        session_store.set_forecasts(tid, session_id, {
            "LOG-1│principal": {"prophet": {"forecast": curve}},
            "LOG-1│Norte":     {"prophet": {"forecast": curve}},
        })

        resp = client.post(
            f"/api/v1/inventory/log-po?session_id={session_id}",
            json={"destination_warehouse": "Norte"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        record = resp.json()["data"]
        assert record["destination_warehouse"] == "Norte"

        lines = query(
            "SELECT sku FROM inventory_po_items WHERE po_log_id = %s", (record["id"],))
        # Norte reads SIN_DATOS (no stock row, see 11.5), so there is nothing to
        # order there — and nothing to log. The tenant-wide list would have put
        # a line here.
        assert lines == [], f"logged an order the file never contained: {lines}"

    def test_a_warehouse_spelled_differently_still_resolves(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import service as inv_svc
        from backend.inventory import warehouse_service as wh_svc

        tid = test_tenant["id"]
        wh_svc.create_warehouse(tid, "principal", is_default=True)
        wh_svc.create_warehouse(tid, "Norte")
        inv_svc.upsert_stock(tid, "EXP-2", {"current_stock": 5, "warehouse": "Norte"})

        resp = client.get(
            f"/api/v1/inventory/status/export-po?session_id=sess_{uuid4().hex[:6]}&warehouse=norte",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text


# ── 11.2 A thousands-dot file imported every quantity divided by 1000 ─────────

class TestAnImportAsksInsteadOfGuessingTheDecimalMark:
    """`["1.250","980","12.500"]` parsed to 1.25 and 12.5. No row errors, the
    wizard said "1,200 products imported", and the whole catalogue dropped to
    PEDIR_YA with every quantity divided by a thousand. Adding one `3,50`
    anywhere fixed it, which is why every hand-made test file passed."""

    _AMBIGUOUS = "sku,current_stock\nA,1.250\nB,980\nC,12.500\n"

    def test_the_preview_reports_the_question_with_both_readings(
        self, client, auth_headers,
    ):
        resp = client.post(
            "/api/v1/inventory/bulk/preview",
            files={"file": ("erp.csv", self._AMBIGUOUS, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        fmt = resp.json()["data"]["number_format"]
        assert fmt["ambiguous"] is True
        assert "1.250" in fmt["samples"]
        # Asked in numbers, not in vocabulary: nobody should need to know what a
        # thousands separator is to answer.
        assert fmt["as_decimal"] == 1.25
        assert fmt["as_thousands"] == 1250

    def test_importing_without_an_answer_is_refused(self, client, auth_headers):
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("erp.csv", self._AMBIGUOUS, "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "inventory_import_number_format_unclear"

    def test_the_answer_is_honoured(self, client, auth_headers, test_tenant):
        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("erp.csv", self._AMBIGUOUS, "text/csv")},
            data={"thousands_dot": "true"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        row = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku='A'",
            (test_tenant["id"],))
        assert float(row["current_stock"]) == 1250.0

        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("erp.csv", self._AMBIGUOUS, "text/csv")},
            data={"thousands_dot": "false"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        row = query_one(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s AND sku='A'",
            (test_tenant["id"],))
        assert float(row["current_stock"]) == 1.25

    def test_a_file_that_settles_it_is_never_questioned(self, client, auth_headers):
        """One `3,50` proves the comma is the decimal mark, so the dot is
        thousands and there is nothing to ask."""
        resp = client.post(
            "/api/v1/inventory/bulk",
            # The decimal comma has to be QUOTED or the CSV reader splits the
            # cell in two and the file never shows a comma at all.
            files={"file": ("erp.csv",
                            'sku,current_stock,unit_cost\nA,1.250,"3,50"\n',
                            "text/csv")},
            headers=auth_headers,
        )
        # Whatever the row-level outcome, it is not the format question.
        assert resp.status_code != 422 or (
            resp.json().get("error_code") != "inventory_import_number_format_unclear")


# ── 11.9 A monthly ERP re-import reverts every hand-corrected lead time ───────

class TestAReimportCanBeToldNotToOverwriteHandWork:
    """`only_fill_missing` and `_fields_to_fill` existed precisely so "a lead
    time the buyer corrected by hand in March is not silently reverted by
    April's ERP export" — and the only caller passing True was a test."""

    def test_the_wizard_can_ask_for_fill_only_and_hand_work_survives(
        self, client, auth_headers, test_tenant,
    ):
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        # The buyer corrected this by hand: provenance says 'user'.
        inv_svc.upsert_stock(tid, "RE-1", {"lead_time_days": 3}, source="user")

        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("april.csv", "sku,lead_time_days\nRE-1,30\n", "text/csv")},
            data={"only_fill_missing": "true"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["only_fill_missing"] is True

        row = query_one(
            "SELECT lead_time_days, lead_time_set_by FROM inventory_stock "
            "WHERE tenant_id=%s AND sku='RE-1'", (tid,))
        assert float(row["lead_time_days"]) == 3, "April's export reverted March's correction"
        assert row["lead_time_set_by"] == "user"

    def test_the_default_is_still_a_full_overwrite(
        self, client, auth_headers, test_tenant,
    ):
        """Unchanged for every caller that does not ask: this is a choice the
        screen offers, not a new default applied behind everybody's back."""
        from backend.inventory import service as inv_svc

        tid = test_tenant["id"]
        inv_svc.upsert_stock(tid, "RE-2", {"lead_time_days": 3}, source="user")

        resp = client.post(
            "/api/v1/inventory/bulk",
            files={"file": ("april.csv", "sku,lead_time_days\nRE-2,30\n", "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["only_fill_missing"] is False
        row = query_one(
            "SELECT lead_time_days FROM inventory_stock WHERE tenant_id=%s AND sku='RE-2'",
            (tid,))
        assert float(row["lead_time_days"]) == 30


# ── 11.32 The supplier form pre-fills the system default ──────────────────────

class TestASupplierOnlyDeclaresALeadTimeWhenSomebodyTypesOne:
    """Three backend call sites gate on `lead_time_set_by` precisely to keep
    Faro's own assumption from being reported as the supplier's promise. The
    create form defeated that guard by always sending a number — and so did the
    API model itself, whose `lead_time_days: int = Field(default=15)` handed the
    service a 15 for every caller that sent none.

    Found by creating a supplier in a browser, not by a test: the service-level
    tests passed the field explicitly, so the model's default never showed.
    """

    def test_creating_one_without_a_lead_time_records_no_declaration(
        self, client, auth_headers, test_tenant,
    ):
        name = f"Sin-plazo-{uuid4().hex[:6]}"
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": name}, headers=auth_headers)
        assert resp.status_code == 201, resp.text

        row = query_one(
            "SELECT lead_time_days, lead_time_set_by FROM suppliers "
            "WHERE tenant_id=%s AND name=%s", (test_tenant["id"], name))
        # The column's own DEFAULT still applies — the planner needs a number —
        # but nobody is credited with having chosen it.
        assert row["lead_time_days"] == 15
        assert row["lead_time_set_by"] is None, (
            "the product filed its own assumption as the supplier's declaration"
        )

    def test_an_explicit_null_is_accepted_the_same_way(self, client, auth_headers, test_tenant):
        """The form sends `null` for an empty field rather than omitting it."""
        name = f"Null-plazo-{uuid4().hex[:6]}"
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": name, "lead_time_days": None},
                           headers=auth_headers)
        assert resp.status_code == 201, resp.text
        row = query_one(
            "SELECT lead_time_set_by FROM suppliers WHERE tenant_id=%s AND name=%s",
            (test_tenant["id"], name))
        assert row["lead_time_set_by"] is None

    def test_a_typed_lead_time_is_still_the_suppliers_own(
        self, client, auth_headers, test_tenant,
    ):
        name = f"Con-plazo-{uuid4().hex[:6]}"
        resp = client.post("/api/v1/inventory/suppliers",
                           json={"name": name, "lead_time_days": 21},
                           headers=auth_headers)
        assert resp.status_code == 201, resp.text
        row = query_one(
            "SELECT lead_time_days, lead_time_set_by FROM suppliers "
            "WHERE tenant_id=%s AND name=%s", (test_tenant["id"], name))
        assert row["lead_time_days"] == 21
        assert row["lead_time_set_by"] == "user"
