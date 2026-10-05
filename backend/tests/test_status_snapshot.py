"""The persisted status snapshot (backend/inventory/status_snapshot.py).

What these tests protect is one sentence: a snapshot is never served as fresh
when something it was computed from has changed. So the main assertion is not
"the endpoint returns 200" but "the rows the endpoint serves equal a live
computation made right now", checked after every kind of input change, with the
database inspected directly for the generation and the invalidation ledger.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.encoders import jsonable_encoder

from backend.db.connection import execute, query, query_one

N = 60            # SKUs in the mixed dataset
NO_STOCK = 6      # last SKUs: a forecast but no stock row


def _sku(g: int) -> str:
    return f"SKU_{g:03d}"


@pytest.fixture
def mixed(client, auth_headers, completed_session, registered_user):
    """A tenant whose status rows exercise most of the computation: several
    suppliers (one with a card, one with learned lead times), rules, a second
    warehouse, an open purchase order, a declared event, stock history, SKUs
    with no stock, a zero-cost row and a zero-stock row."""
    from backend.inventory import service as svc
    from tests.fixtures.synthetic_data import seed_completed_session

    tid = registered_user["tenant"]["id"]
    sid = completed_session["id"]
    seed_completed_session(tid, sid, n_skus=N)
    execute("DELETE FROM inventory_stock WHERE tenant_id = %s", (tid,))
    execute(
        """INSERT INTO inventory_stock
             (tenant_id, sku, warehouse, display_name, current_stock, lead_time_days,
              unit_cost, supplier, category)
           SELECT %s, 'SKU_' || LPAD(g::text, 3, '0'), 'principal', 'Product ' || g,
                  CASE WHEN g %% 11 = 0 THEN 0 ELSE (g * 37) %% 400 END,
                  3 + (g %% 20),
                  CASE WHEN g %% 13 = 0 THEN NULL ELSE 1 + (g %% 9) END,
                  'Supplier ' || LPAD((g %% 6)::text, 2, '0'), 'cat' || (g %% 4)
             FROM generate_series(1, %s) g""",
        (tid, N - NO_STOCK),
    )
    execute(
        """INSERT INTO inventory_stock (tenant_id, sku, warehouse, display_name, current_stock)
           SELECT %s, 'SKU_' || LPAD(g::text, 3, '0'), 'norte', 'Product ' || g, 25
             FROM generate_series(5, 40, 5) g""",
        (tid,),
    )
    execute(
        """INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse, recorded_at)
           SELECT %s, 'SKU_' || LPAD(g::text, 3, '0'), 100 + g + d, 'principal',
                  NOW() - (d || ' days')::interval
             FROM generate_series(1, %s) g, generate_series(0, 4) d""",
        (tid, N // 2),
    )
    execute(
        "INSERT INTO suppliers (tenant_id, name, lead_time_days, lead_time_set_by, review_period_days) "
        "VALUES (%s, 'Supplier 03', 9, 'user', 7)", (tid,))
    execute(
        "INSERT INTO stock_defaults (tenant_id, scope_type, scope_value, moq) "
        "VALUES (%s, 'category', 'cat1', 5)", (tid,))
    po = query_one(
        "INSERT INTO inventory_po_log (tenant_id, session_id, sku_count, total_units, po_number) "
        "VALUES (%s, %s, 2, 90, 1) RETURNING id", (tid, sid))
    for sku, qty in ((_sku(2), 40), (_sku(7), 50)):
        execute(
            "INSERT INTO inventory_po_items (po_log_id, tenant_id, sku, final_qty, status) "
            "VALUES (%s, %s, %s, %s, 'approved')", (po["id"], tid, sku, qty))
    for i in range(4):
        execute(
            "INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days) "
            "VALUES (%s, 'Supplier 05', %s, %s)", (tid, po["id"], 6 + i))
    today = date.today()
    svc.create_event(tid, {"name": "Holiday", "start_date": today,
                           "end_date": today + timedelta(days=20), "multiplier": 1.6})
    return {"tid": tid, "sid": sid, "po_log_id": po["id"]}


def _get(client, headers, sid, qs="", expect=200):
    r = client.get(f"/api/v1/inventory/status?session_id={sid}{qs}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()["data"]


def _live(tid, sid, sl=0.95, period="daily"):
    from backend.inventory import service as svc
    return jsonable_encoder(svc.get_inventory_status(tid, sid, sl, period))


def _meta(tid, sid):
    return query_one(
        "SELECT * FROM inventory_status_snapshot_meta WHERE tenant_id = %s AND session_id = %s",
        (tid, sid))


def _generation(tid, sid):
    m = _meta(tid, sid)
    return m["generation"] if m else 0


def _reference_summary(items):
    return {
        "total_skus": len(items),
        "order_now": sum(1 for i in items if i["signal"] == "PEDIR_YA"),
        "order_soon": sum(1 for i in items if i["signal"] == "PEDIR_PRONTO"),
        "ok": sum(1 for i in items if i["signal"] == "OK"),
        "without_stock": sum(1 for i in items if not i.get("has_stock")),
        "with_forecast": sum(1 for i in items if i.get("has_forecast")),
        "overstock": sum(1 for i in items if i["signal"] == "SOBRESTOCK"),
        "sin_datos": sum(1 for i in items if i["signal"] == "SIN_DATOS"),
    }


@pytest.mark.integration
class TestSnapshotEqualsLive:
    def test_every_row_and_every_filter_matches_the_live_computation(
        self, client, auth_headers, mixed,
    ):
        from backend.inventory import service as svc
        tid, sid = mixed["tid"], mixed["sid"]

        served = _get(client, auth_headers, sid)
        live = _live(tid, sid)
        # Property: the snapshot's rows are the live rows, same order, same values.
        assert served["items"] == live
        assert len(live) == N
        assert len({i["signal"] for i in live}) >= 3, "the dataset must mix signals"
        assert any(i["incoming_qty"] > 0 for i in live)
        assert any(i["calc_explanation"] and i["calc_explanation"]["events_applied"] for i in live)
        assert any(i["stock_history"] for i in live)

        # The DB holds one row per SKU, and the meta row says so.
        rows = query(
            "SELECT sku, urgency_pos, signal FROM inventory_status_snapshot "
            "WHERE tenant_id = %s ORDER BY urgency_pos", (tid,))
        assert [r["sku"] for r in rows] == [i["sku"] for i in live]
        assert [r["signal"] for r in rows] == [i["signal"] for i in live]
        meta = _meta(tid, sid)
        assert meta["n_rows"] == N and meta["generation"] == 1
        assert served["computed_at"] == meta["computed_at"].isoformat()

        # Summary equals what the live rows say.
        s = served["summary"]
        ref = _reference_summary(live)
        assert {k: s[k] for k in ref} == ref
        assert s["total_inventory_value"] == pytest.approx(
            round(sum(i["inventory_value"] for i in live if i.get("inventory_value")), 2))

        # Filters: the live endpoint's semantics, applied here to the live rows.
        sig = live[0]["signal"]
        sup = next(i["supplier"] for i in live if i.get("supplier"))
        needle = "product 1"
        cases = {
            f"&signal={sig.lower()}": [i for i in live if i["signal"] == sig],
            f"&supplier={sup.upper().replace(' ', '%20')}":
                [i for i in live if (i.get("supplier") or "").lower() == sup.lower()],
            "&skus=SKU_005,SKU_020,NOPE": [i for i in live if i["sku"] in {"SKU_005", "SKU_020"}],
            f"&q={needle.replace(' ', '%20').upper()}":
                [i for i in live if needle in " ".join(str(i.get(k) or "") for k in
                                                       ("sku", "display_name", "category", "supplier")).lower()],
        }
        for qs, expected in cases.items():
            got = _get(client, auth_headers, sid, qs)
            assert got["items"] == expected, qs
            assert got["summary"]["total_skus"] == len(expected) > 0, qs
            assert {k: got["summary"][k] for k in ref} == _reference_summary(expected), qs
        assert _generation(tid, sid) == 1, "filtering must not recompute"

        # Paging and every sort: same function, same rows, any page.
        from backend.api.v1 import inventory as inv_api  # noqa: F401  (route import check)
        for sort in ("urgency", "sku", "coverage", "value", "recommended", "signal", "name",
                     "stock", "demand_lt", "qty", "lead_time", "moq", "abc_xyz", "decision",
                     "supplier_urgency"):
            for order in (None, "asc", "desc"):
                qs = f"&limit=17&offset=11&sort={sort}" + (f"&order={order}" if order else "")
                got = _get(client, auth_headers, sid, qs)
                expected = svc.sort_status_items(live, sort, order)[11:28]
                assert [i["sku"] for i in got["items"]] == [i["sku"] for i in expected], qs
                assert got["items"] == expected, qs
                assert got["page"]["total"] == N
        assert _generation(tid, sid) == 1

    def test_history_batch_equals_the_one_at_a_time_reader(self, mixed):
        from backend.inventory import service as svc
        tid = mixed["tid"]
        skus = [_sku(g) for g in range(1, 41)] + ["NOT_A_SKU"]
        batch = svc.get_stock_history_batch(tid, skus, days=14)
        for sku in skus:
            assert batch[sku] == svc.get_stock_history(tid, sku, days=14), sku

    def test_unauthenticated_is_rejected_and_viewer_can_read(
        self, client, viewer_headers, mixed,
    ):
        assert client.get(f"/api/v1/inventory/status?session_id={mixed['sid']}").status_code in (401, 403)
        got = _get(client, viewer_headers, mixed["sid"], "&limit=5")
        assert len(got["items"]) == 5


def _m_stock_update(tid, sid, po):
    execute("UPDATE inventory_stock SET current_stock = current_stock + 500 "
            "WHERE tenant_id = %s AND sku = 'SKU_003'", (tid,))


def _m_stock_insert(tid, sid, po):
    execute("INSERT INTO inventory_stock (tenant_id, sku, warehouse, display_name, current_stock) "
            "VALUES (%s, 'SKU_058', 'principal', 'late arrival', 1)", (tid,))


def _m_stock_delete(tid, sid, po):
    execute("DELETE FROM inventory_stock WHERE tenant_id = %s AND sku = 'SKU_004'", (tid,))


def _m_warehouse(tid, sid, po):
    execute("INSERT INTO warehouses (tenant_id, name) VALUES (%s, 'Bodega Norte')", (tid,))


def _m_supplier_card(tid, sid, po):
    execute("UPDATE suppliers SET lead_time_days = 2 WHERE tenant_id = %s AND name = 'Supplier 03'", (tid,))


def _m_primary_supplier(tid, sid, po):
    sup = query_one("SELECT id FROM suppliers WHERE tenant_id = %s", (tid,))
    execute("INSERT INTO sku_suppliers (tenant_id, sku, supplier_id, is_primary) "
            "VALUES (%s, 'SKU_058', %s, TRUE)", (tid, sup["id"]))


def _m_rule(tid, sid, po):
    execute("INSERT INTO stock_defaults (tenant_id, scope_type, scope_value, lead_time_days) "
            "VALUES (%s, 'global', '', 4)", (tid,))


def _m_event(tid, sid, po):
    from backend.inventory import service as svc
    today = date.today()
    svc.create_event(tid, {"name": "Second", "start_date": today,
                           "end_date": today + timedelta(days=10), "multiplier": 2.5})


def _m_event_override(tid, sid, po):
    from backend.inventory import service as svc
    ev = query_one("SELECT id FROM inventory_events WHERE tenant_id = %s", (tid,))
    svc.set_event_multiplier(tid, ev["id"], "sku", "SKU_010", 3.0)


def _m_po_received(tid, sid, po):
    execute("UPDATE inventory_po_items SET received_qty = final_qty WHERE tenant_id = %s", (tid,))


def _m_po_cancelled(tid, sid, po):
    execute("UPDATE inventory_po_log SET cancelled_at = NOW() WHERE id = %s", (po,))


def _m_learned_lead_time(tid, sid, po):
    execute("INSERT INTO supplier_lead_time_obs (tenant_id, supplier, po_log_id, lead_time_days) "
            "VALUES (%s, 'Supplier 05', %s, 40)", (tid, po))


def _m_stock_history(tid, sid, po):
    execute("INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse) "
            "VALUES (%s, 'SKU_001', 7, 'principal')", (tid,))


def _m_forecast(tid, sid, po):
    from backend.db import session_store
    blob = session_store.get_forecasts(tid, sid)
    first = sorted(blob)[0]
    del blob[first]                      # one SKU loses its forecast
    session_store.set_forecasts(tid, sid, blob)


MUTATIONS = [
    _m_stock_update, _m_stock_insert, _m_stock_delete, _m_warehouse, _m_supplier_card,
    _m_primary_supplier, _m_rule, _m_event, _m_event_override, _m_po_received,
    _m_po_cancelled, _m_learned_lead_time, _m_stock_history, _m_forecast,
]


@pytest.mark.integration
class TestInvalidation:
    @pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda f: f.__name__[3:])
    def test_each_input_change_forces_a_recompute_that_matches_live(
        self, client, auth_headers, mixed, mutation,
    ):
        tid, sid = mixed["tid"], mixed["sid"]
        _get(client, auth_headers, sid, "&limit=5")
        gen0 = _generation(tid, sid)
        version0 = query_one("SELECT MAX(id) AS v FROM status_input_bumps WHERE tenant_id = %s", (tid,))["v"]
        # Nothing changed: a second read is served from the same generation.
        _get(client, auth_headers, sid, "&limit=5")
        assert _generation(tid, sid) == gen0

        mutation(tid, sid, mixed["po_log_id"])

        version1 = query_one("SELECT MAX(id) AS v FROM status_input_bumps WHERE tenant_id = %s", (tid,))["v"]
        assert version1 > version0, "the database trigger did not record the change"
        served = _get(client, auth_headers, sid)
        assert _generation(tid, sid) == gen0 + 1, "stale snapshot served as fresh"
        assert served["items"] == _live(tid, sid)

    def test_a_change_made_to_a_row_is_visible_in_the_next_response(
        self, client, auth_headers, mixed,
    ):
        tid, sid = mixed["tid"], mixed["sid"]
        before = {i["sku"]: i for i in _get(client, auth_headers, sid)["items"]}["SKU_003"]
        execute("UPDATE inventory_stock SET current_stock = current_stock + 500 "
                "WHERE tenant_id = %s AND sku = 'SKU_003'", (tid,))
        after = {i["sku"]: i for i in _get(client, auth_headers, sid)["items"]}["SKU_003"]
        assert after["current_stock"] == before["current_stock"] + 500
        stored = query_one(
            "SELECT item->>'current_stock' AS s FROM inventory_status_snapshot "
            "WHERE tenant_id = %s AND sku = 'SKU_003' AND generation = %s",
            (tid, _generation(tid, sid)))
        assert float(stored["s"]) == after["current_stock"]

    def test_every_input_table_has_all_three_triggers(self):
        from backend.db.migrations import STATUS_INPUT_TABLES
        have = {r["tgname"] for r in query(
            "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'status_bump_%%'")}
        for table in STATUS_INPUT_TABLES:
            for op in ("insert", "update", "delete"):
                assert f"status_bump_{table}_{op}" in have, (table, op)

    def test_the_tables_the_computation_reads_are_all_watched(self):
        """If the status path starts reading a new table, the list of watched
        tables has to grow: this reads the SQL of the computation's helpers and
        fails on a table nobody registered."""
        import inspect
        import re
        from backend.db.migrations import STATUS_INPUT_TABLES
        from backend.inventory import (
            service, stock_defaults_service, supplier_service, warehouse_service)
        from backend.db import session_store
        reads = set()
        for fn in (
            service.list_stock, service.get_incoming_detail, service.get_learned_lead_times,
            service.get_learned_lead_time_stds, service.get_supplier_observation_counts,
            service.get_stock_history_batch, service._active_events_window,
            service.get_event_multipliers, supplier_service.get_primary_suppliers_map,
            supplier_service.get_lead_time_std_map, supplier_service.get_review_period_map,
            stock_defaults_service.build_rule_index, warehouse_service.list_warehouses,
            session_store.get_forecasts, session_store.get_training_result,
        ):
            reads |= set(re.findall(r"(?:FROM|JOIN)\s+([a-z_]+)", inspect.getsource(fn)))
        reads -= {"generate_series", "unnest"}
        assert reads <= set(STATUS_INPUT_TABLES), reads - set(STATUS_INPUT_TABLES)

    @pytest.mark.parametrize("breaker", ["day", "age", "code", "session"])
    def test_time_code_and_session_staleness(self, client, auth_headers, mixed, breaker):
        tid, sid = mixed["tid"], mixed["sid"]
        _get(client, auth_headers, sid, "&limit=5")
        gen0 = _generation(tid, sid)
        if breaker == "day":
            execute("UPDATE inventory_status_snapshot_meta SET computed_on = %s WHERE tenant_id = %s",
                    (date.today() - timedelta(days=1), tid))
        elif breaker == "age":
            execute("UPDATE inventory_status_snapshot_meta SET computed_at = %s WHERE tenant_id = %s",
                    (datetime.now(timezone.utc) - timedelta(hours=2), tid))
        elif breaker == "code":
            execute("UPDATE inventory_status_snapshot_meta SET code_hash = 'old' WHERE tenant_id = %s", (tid,))
        else:
            # Another service level is a different snapshot, never this one's rows.
            _get(client, auth_headers, sid, "&limit=5&service_level=0.9")
            assert query_one(
                "SELECT COUNT(*) AS n FROM inventory_status_snapshot_meta WHERE tenant_id = %s",
                (tid,))["n"] == 2
            assert _generation(tid, sid) == gen0
            return
        _get(client, auth_headers, sid, "&limit=5")
        assert _generation(tid, sid) == gen0 + 1

    def test_failure_to_build_the_snapshot_falls_back_to_live(
        self, client, auth_headers, mixed, monkeypatch,
    ):
        from backend.inventory import status_snapshot
        tid, sid = mixed["tid"], mixed["sid"]

        def boom(*a, **k):
            raise RuntimeError("snapshot store down")
        monkeypatch.setattr(status_snapshot, "refresh", boom)
        served = _get(client, auth_headers, sid, "&limit=7&q=product")
        assert served["page"]["total"] == len([i for i in _live(tid, sid)
                                               if "product" in (i.get("display_name") or "").lower()])
        assert query_one("SELECT COUNT(*) AS n FROM inventory_status_snapshot WHERE tenant_id = %s",
                         (tid,))["n"] == 0, "a failed refresh must leave no half-written rows"


@pytest.mark.integration
class TestConcurrencyAndIsolation:
    def test_concurrent_refreshes_compute_once(self, mixed, monkeypatch):
        from backend.inventory import service as svc
        from backend.inventory import status_snapshot
        tid, sid = mixed["tid"], mixed["sid"]
        calls = []
        real = svc._compute_inventory_status

        def slow(*a, **k):
            calls.append(threading.get_ident())
            time.sleep(0.8)
            return real(*a, **k)
        monkeypatch.setattr(svc, "_compute_inventory_status", slow)

        barrier = threading.Barrier(4)

        def go():
            barrier.wait()
            return status_snapshot.ensure_fresh(tid, sid, 0.95, "daily")
        with ThreadPoolExecutor(4) as pool:
            metas = [f.result() for f in [pool.submit(go) for _ in range(4)]]

        assert len(calls) == 1, "four racing readers computed more than once"
        assert {m["generation"] for m in metas} == {1}
        assert query_one("SELECT COUNT(*) AS n FROM inventory_status_snapshot WHERE tenant_id = %s",
                         (tid,))["n"] == N
        assert query_one("SELECT COUNT(*) AS n FROM inventory_status_snapshot_meta WHERE tenant_id = %s",
                         (tid,))["n"] == 1

    def test_a_refresh_during_a_write_is_not_marked_fresh(self, mixed, monkeypatch):
        """A write that commits while the snapshot is being computed leaves the
        stored version older than the live one, so the next read recomputes."""
        from backend.inventory import service as svc
        from backend.inventory import status_snapshot
        tid, sid = mixed["tid"], mixed["sid"]
        real = svc._compute_inventory_status

        def write_midway(*a, **k):
            out = real(*a, **k)
            execute("UPDATE inventory_stock SET current_stock = 9999 "
                    "WHERE tenant_id = %s AND sku = 'SKU_003'", (tid,))
            return out
        monkeypatch.setattr(svc, "_compute_inventory_status", write_midway)
        status_snapshot.ensure_fresh(tid, sid, 0.95, "daily")
        monkeypatch.setattr(svc, "_compute_inventory_status", real)
        meta = _meta(tid, sid)
        assert not status_snapshot.is_fresh(meta, status_snapshot.current_version(tid))
        status_snapshot.ensure_fresh(tid, sid, 0.95, "daily")
        row = query_one("SELECT item->>'current_stock' AS s FROM inventory_status_snapshot "
                        "WHERE tenant_id = %s AND sku = 'SKU_003' AND generation = %s",
                        (tid, _generation(tid, sid)))
        assert float(row["s"]) == 9999.0

    def test_tenants_never_share_snapshots_or_invalidations(self, client, auth_headers, mixed):
        from backend.inventory import status_snapshot
        from backend.tenants.service import create_tenant
        tid, sid = mixed["tid"], mixed["sid"]
        other = create_tenant(f"pytest-iso-{int(time.time() * 1000)}")
        try:
            _get(client, auth_headers, sid, "&limit=5")
            gen_a = _generation(tid, sid)
            version_a = status_snapshot.current_version(tid)

            # Writes by another tenant leave this tenant's snapshot valid...
            execute("INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock) "
                    "VALUES (%s, 'SKU_001', 'principal', 1)", (other["id"],))
            assert status_snapshot.current_version(tid) == version_a
            assert status_snapshot.current_version(other["id"]) > 0
            _get(client, auth_headers, sid, "&limit=5")
            assert _generation(tid, sid) == gen_a

            # ...and asking for another tenant's session through the other tenant's
            # id yields nothing of this tenant's, and writes nothing under this one.
            theirs = status_snapshot.read_status(other["id"], sid, 0.95, "daily")
            assert theirs is not None and theirs["items"] == [] and theirs["total"] == 0
            assert query_one(
                "SELECT COUNT(*) AS n FROM inventory_status_snapshot WHERE tenant_id = %s",
                (tid,))["n"] == N
            assert _generation(tid, sid) == gen_a
        finally:
            execute("DELETE FROM tenants WHERE id = %s", (other["id"],))
            assert query_one("SELECT COUNT(*) AS n FROM inventory_status_snapshot_meta "
                             "WHERE tenant_id = %s", (other["id"],))["n"] == 0
            assert query_one("SELECT COUNT(*) AS n FROM status_input_bumps "
                             "WHERE tenant_id = %s", (other["id"],))["n"] == 0
