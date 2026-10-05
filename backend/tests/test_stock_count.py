"""
Physical stock count: sessions, scans, differences, one-shot apply.

Every assertion on state reads the database directly: a 200 proves nothing about
what the stock table or the adjustment ledger now holds.
"""

import threading
import uuid

import pytest

from backend.db.connection import execute, query, query_one

API = "/api/v1/inventory"


def _sku():
    return f"CNT-{uuid.uuid4().hex[:8]}"


def _seed(client, headers, sku, stock=10, cost=2.0, barcode=None, warehouse=None, **extra):
    body = {"current_stock": stock, "unit_cost": cost, "display_name": f"Item {sku}", **extra}
    if barcode:
        body["barcode"] = barcode
    if warehouse:
        body["warehouse"] = warehouse
    r = client.put(f"{API}/stock/{sku}", json=body, headers=headers)
    assert r.status_code == 200, r.text


def _stock(tid, sku, warehouse="principal"):
    return query_one(
        "SELECT * FROM inventory_stock WHERE tenant_id=%s AND sku=%s AND warehouse=%s",
        (tid, sku, warehouse),
    )


def _new_count(client, headers, **body):
    r = client.post(f"{API}/stock-counts", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _scan(client, headers, count_id, sku, qty=1, **extra):
    return client.put(
        f"{API}/stock-counts/{count_id}/lines",
        json={"sku": sku, "quantity": qty, **extra}, headers=headers,
    )


def _line(count_id, sku):
    return query_one("SELECT * FROM stock_count_lines WHERE count_id=%s AND sku=%s",
                     (count_id, sku))


def _status(count_id):
    return query_one("SELECT status FROM stock_counts WHERE id=%s", (count_id,))["status"]


def _closed(client, headers, lines):
    """A closed count with `lines` = [(sku, counted_qty)] (set mode)."""
    cid = _new_count(client, headers)
    for sku, qty in lines:
        assert _scan(client, headers, cid, sku, qty, mode="set").status_code == 200
    assert client.post(f"{API}/stock-counts/{cid}/close", headers=headers).status_code == 200
    return cid


# ── lookup ────────────────────────────────────────────────────────────────────

class TestLookup:
    def test_by_barcode_and_by_sku(self, client, auth_headers):
        sku, code = _sku(), f"789{uuid.uuid4().hex[:9]}"
        _seed(client, auth_headers, sku, stock=7, barcode=code)
        by_code = client.get(f"{API}/stock/lookup", params={"code": code}, headers=auth_headers)
        assert by_code.status_code == 200, by_code.text
        d = by_code.json()["data"]
        assert d["sku"] == sku and d["matched_by"] == "barcode" and d["system_qty"] == 7
        by_sku = client.get(f"{API}/stock/lookup", params={"code": sku}, headers=auth_headers)
        assert by_sku.json()["data"]["matched_by"] == "sku"

    def test_unknown_code_is_404_and_not_a_sku_route(self, client, auth_headers):
        r = client.get(f"{API}/stock/lookup", params={"code": "no-such-thing"},
                       headers=auth_headers)
        assert r.status_code == 404
        assert r.json()["error_code"] == "lookup_code_not_found"

    def test_is_tenant_scoped(self, client, auth_headers, make_tenant_user_headers):
        sku, code = _sku(), f"555{uuid.uuid4().hex[:9]}"
        _seed(client, auth_headers, sku, barcode=code)
        other = make_tenant_user_headers(role="analyst")
        r = client.get(f"{API}/stock/lookup", params={"code": code}, headers=other)
        assert r.status_code == 404

    def test_shared_barcode_is_ambiguous_not_a_guess(self, client, auth_headers):
        a, b, code = _sku(), _sku(), f"111{uuid.uuid4().hex[:9]}"
        _seed(client, auth_headers, a, barcode=code)
        _seed(client, auth_headers, b, barcode=code)
        r = client.get(f"{API}/stock/lookup", params={"code": code}, headers=auth_headers)
        assert r.status_code == 409
        assert r.json()["error_code"] == "lookup_code_ambiguous"

    def test_sku_in_another_warehouse_reports_zero_here(self, client, auth_headers):
        sku = _sku()
        _seed(client, auth_headers, sku, stock=9, warehouse="Norte")
        r = client.get(f"{API}/stock/lookup", params={"code": sku, "warehouse": "Norte"},
                       headers=auth_headers)
        assert r.json()["data"]["system_qty"] == 9 and r.json()["data"]["in_warehouse"] is True
        r = client.get(f"{API}/stock/lookup", params={"code": sku, "warehouse": "principal"},
                       headers=auth_headers)
        assert r.json()["data"]["system_qty"] == 0 and r.json()["data"]["in_warehouse"] is False


# ── sessions and lines ────────────────────────────────────────────────────────

class TestCountLines:
    def test_create_writes_an_open_count(self, client, auth_headers, test_tenant):
        cid = _new_count(client, auth_headers, scope_category="Bebidas")
        row = query_one("SELECT * FROM stock_counts WHERE id=%s", (cid,))
        assert row["tenant_id"] == test_tenant["id"]
        assert row["status"] == "open" and row["warehouse"] == "principal"
        assert row["scope_category"] == "Bebidas" and row["created_by"]

    def test_unknown_warehouse_is_refused(self, client, auth_headers, test_tenant):
        r = client.post(f"{API}/stock-counts", json={"warehouse": "Nowhere"},
                        headers=auth_headers)
        assert r.status_code == 404
        assert query_one("SELECT 1 FROM stock_counts WHERE tenant_id=%s",
                         (test_tenant["id"],)) is None

    def test_scanning_twice_adds_and_set_replaces(self, client, auth_headers):
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _new_count(client, auth_headers)
        assert _scan(client, auth_headers, cid, sku, 1, source="scan").status_code == 200
        assert _scan(client, auth_headers, cid, sku, 1, source="scan").status_code == 200
        line = _line(cid, sku)
        assert line["counted_qty"] == 2 and line["system_qty_at_count"] == 10
        assert line["source"] == "scan"
        assert _scan(client, auth_headers, cid, sku, 7, mode="set").status_code == 200
        assert _line(cid, sku)["counted_qty"] == 7
        n = query_one("SELECT COUNT(*) AS c FROM stock_count_lines WHERE count_id=%s", (cid,))
        assert n["c"] == 1  # one line per SKU, never duplicated

    def test_client_ref_makes_a_replayed_scan_a_no_op(self, client, auth_headers):
        sku = _sku()
        _seed(client, auth_headers, sku)
        cid = _new_count(client, auth_headers)
        first = _scan(client, auth_headers, cid, sku, 3, client_ref="op-1")
        again = _scan(client, auth_headers, cid, sku, 3, client_ref="op-1")
        assert first.json()["data"]["duplicate"] is False
        assert again.json()["data"]["duplicate"] is True
        assert _line(cid, sku)["counted_qty"] == 3
        assert _scan(client, auth_headers, cid, sku, 3, client_ref="op-2").status_code == 200
        assert _line(cid, sku)["counted_qty"] == 6

    def test_unknown_sku_creates_no_line(self, client, auth_headers):
        cid = _new_count(client, auth_headers)
        r = _scan(client, auth_headers, cid, "NOPE-" + uuid.uuid4().hex[:6])
        assert r.status_code == 404
        n = query_one("SELECT COUNT(*) AS c FROM stock_count_lines WHERE count_id=%s", (cid,))
        assert n["c"] == 0

    def test_negative_quantity_rejected(self, client, auth_headers):
        sku = _sku()
        _seed(client, auth_headers, sku)
        cid = _new_count(client, auth_headers)
        assert _scan(client, auth_headers, cid, sku, -1).status_code == 422
        assert _line(cid, sku) is None

    def test_delete_line_and_closed_count_is_frozen(self, client, auth_headers):
        a, b = _sku(), _sku()
        _seed(client, auth_headers, a)
        _seed(client, auth_headers, b)
        cid = _new_count(client, auth_headers)
        _scan(client, auth_headers, cid, a, 1)
        _scan(client, auth_headers, cid, b, 1)
        assert client.delete(f"{API}/stock-counts/{cid}/lines/{a}",
                             headers=auth_headers).status_code == 204
        assert _line(cid, a) is None and _line(cid, b) is not None
        assert client.post(f"{API}/stock-counts/{cid}/close", headers=auth_headers).status_code == 200
        assert _scan(client, auth_headers, cid, b, 5).status_code == 409
        assert client.delete(f"{API}/stock-counts/{cid}/lines/{b}",
                             headers=auth_headers).status_code == 409
        assert _line(cid, b)["counted_qty"] == 1

    def test_close_needs_a_line(self, client, auth_headers):
        cid = _new_count(client, auth_headers)
        r = client.post(f"{API}/stock-counts/{cid}/close", headers=auth_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "count_empty"
        assert _status(cid) == "open"


# ── preview ───────────────────────────────────────────────────────────────────

class TestPreview:
    def test_differences_sorted_by_value_and_missing_cost_never_divides(
        self, client, auth_headers,
    ):
        small, big, nocost, same = _sku(), _sku(), _sku(), _sku()
        _seed(client, auth_headers, small, stock=10, cost=1.0)    # -1  -> -1
        _seed(client, auth_headers, big, stock=10, cost=50.0)     # +2  -> +100
        _seed(client, auth_headers, nocost, stock=10, cost=None)  # -5  -> unpriced
        _seed(client, auth_headers, same, stock=4, cost=3.0)
        cid = _closed(client, auth_headers,
                      [(small, 9), (big, 12), (nocost, 5), (same, 4)])
        r = client.get(f"{API}/stock-counts/{cid}/preview", headers=auth_headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        skus = [l["sku"] for l in d["lines"]]
        assert skus.index(big) < skus.index(small) < skus.index(nocost)
        by = {l["sku"]: l for l in d["lines"]}
        assert by[big]["value_impact"] == 100.0 and by[small]["value_impact"] == -1.0
        assert by[nocost]["value_impact"] is None and by[nocost]["unit_cost"] is None
        t = d["totals"]
        assert t["lines_with_difference"] == 3 and t["unpriced_lines"] == 1
        assert t["net_value"] == 99.0 and t["units_short"] == 6 and t["units_over"] == 2

    def test_uncounted_skus_are_reported_not_zeroed(self, client, auth_headers, test_tenant):
        a, b = _sku(), _sku()
        _seed(client, auth_headers, a, stock=5)
        _seed(client, auth_headers, b, stock=5)
        cid = _closed(client, auth_headers, [(a, 5)])
        d = client.get(f"{API}/stock-counts/{cid}/preview", headers=auth_headers).json()["data"]
        assert d["totals"]["uncounted_skus"] == 1
        client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        assert _stock(test_tenant["id"], b)["current_stock"] == 5

    def test_viewer_can_read_the_preview(self, client, auth_headers, viewer_headers):
        sku = _sku()
        _seed(client, auth_headers, sku)
        cid = _closed(client, auth_headers, [(sku, 3)])
        assert client.get(f"{API}/stock-counts/{cid}/preview",
                          headers=viewer_headers).status_code == 200


# ── apply ─────────────────────────────────────────────────────────────────────

class TestApply:
    def test_apply_writes_stock_ledger_snapshot_and_marks_lines(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        short, over = _sku(), _sku()
        _seed(client, auth_headers, short, stock=10, cost=4.0)
        _seed(client, auth_headers, over, stock=3, cost=2.0)
        cid = _closed(client, auth_headers, [(short, 7), (over, 8)])
        r = client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        assert r.status_code == 200, r.text
        res = r.json()["data"]
        assert res["lines_adjusted"] == 2 and res["units_added"] == 5 and res["units_removed"] == 3
        assert res["net_value"] == -2.0  # -3*4 + 5*2

        assert _stock(tid, short)["current_stock"] == 7
        assert _stock(tid, over)["current_stock"] == 8
        led = query("SELECT * FROM stock_adjustments WHERE tenant_id=%s AND ref_id=%s "
                    "ORDER BY sku", (tid, cid))
        assert len(led) == 2
        assert {l["reason"] for l in led} == {"physical_count"}
        by = {l["sku"]: l for l in led}
        assert (by[short]["qty_before"], by[short]["qty_after"], by[short]["delta"]) == (10, 7, -3)
        assert by[over]["delta"] == 5
        snap = query_one("SELECT current_stock FROM inventory_snapshots WHERE tenant_id=%s "
                         "AND sku=%s ORDER BY recorded_at DESC LIMIT 1", (tid, short))
        assert snap["current_stock"] == 7
        assert _status(cid) == "applied"
        assert _line(cid, short)["applied_at"] is not None
        assert _line(cid, short)["applied_from"] == 10 and _line(cid, short)["applied_to"] == 7
        ev = query_one("SELECT 1 FROM activity_logs WHERE tenant_id=%s AND action="
                       "'data.stock_count_applied'", (tid,))
        assert ev is not None

    def test_cannot_apply_twice(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _closed(client, auth_headers, [(sku, 6)])
        assert client.post(f"{API}/stock-counts/{cid}/apply", json={},
                           headers=auth_headers).status_code == 200
        again = client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        assert again.status_code == 409 and again.json()["error_code"] == "count_already_applied"
        assert _stock(tid, sku)["current_stock"] == 6
        n = query_one("SELECT COUNT(*) AS c FROM stock_adjustments WHERE ref_id=%s", (cid,))
        assert n["c"] == 1

    def test_concurrent_applies_write_once(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _closed(client, auth_headers, [(sku, 4)])
        from backend.inventory import stock_count_service as svc
        outcomes = []
        barrier = threading.Barrier(2)

        def go():
            barrier.wait()
            try:
                svc.apply_count(tid, cid, "u")
                outcomes.append("ok")
            except Exception as e:  # AppError
                outcomes.append(getattr(e, "code", "error"))

        ts = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(outcomes) == ["count_already_applied", "ok"]
        assert _stock(tid, sku)["current_stock"] == 4  # not 4 - 6 twice

    def test_open_count_cannot_be_applied(self, client, auth_headers, test_tenant):
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _new_count(client, auth_headers)
        _scan(client, auth_headers, cid, sku, 1)
        r = client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        assert r.status_code == 409 and r.json()["error_code"] == "count_not_closed"
        assert _stock(test_tenant["id"], sku)["current_stock"] == 10

    def test_partial_apply_touches_only_selected_lines(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        a, b = _sku(), _sku()
        _seed(client, auth_headers, a, stock=10)
        _seed(client, auth_headers, b, stock=10)
        cid = _closed(client, auth_headers, [(a, 5), (b, 5)])
        r = client.post(f"{API}/stock-counts/{cid}/apply", json={"skus": [a]},
                        headers=auth_headers)
        assert r.status_code == 200
        assert _stock(tid, a)["current_stock"] == 5
        assert _stock(tid, b)["current_stock"] == 10
        assert _line(cid, b)["applied_at"] is None
        assert _status(cid) == "applied"

    def test_unknown_selected_sku_changes_nothing(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        a = _sku()
        _seed(client, auth_headers, a, stock=10)
        cid = _closed(client, auth_headers, [(a, 5)])
        r = client.post(f"{API}/stock-counts/{cid}/apply", json={"skus": [a, "GHOST"]},
                        headers=auth_headers)
        assert r.status_code == 404
        assert _stock(tid, a)["current_stock"] == 10 and _status(cid) == "closed"
        assert client.post(f"{API}/stock-counts/{cid}/apply", json={"skus": []},
                           headers=auth_headers).status_code == 422

    def test_stock_that_moved_since_the_scan_keeps_its_movement(
        self, client, auth_headers, test_tenant,
    ):
        """Counted 8 when the system said 10 (-2); a reception then made it 30.
        The count must remove 2 (28), not overwrite with 8."""
        tid = test_tenant["id"]
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _closed(client, auth_headers, [(sku, 8)])
        execute("UPDATE inventory_stock SET current_stock=30 WHERE tenant_id=%s AND sku=%s",
                (tid, sku))
        d = client.get(f"{API}/stock-counts/{cid}/preview", headers=auth_headers).json()["data"]
        assert d["lines"][0]["moved_since_count"] is True
        client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        assert _stock(tid, sku)["current_stock"] == 28

    def test_never_goes_below_zero(self, client, auth_headers, test_tenant):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _closed(client, auth_headers, [(sku, 0)])
        execute("UPDATE inventory_stock SET current_stock=4 WHERE tenant_id=%s AND sku=%s",
                (tid, sku))
        client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        assert _stock(tid, sku)["current_stock"] == 0
        led = query_one("SELECT * FROM stock_adjustments WHERE ref_id=%s", (cid,))
        assert led["delta"] == -4 and led["qty_after"] == 0

    def test_sku_missing_from_the_counted_warehouse_gets_a_row(
        self, client, auth_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(client, auth_headers, sku, stock=9, warehouse="Norte")
        cid = _closed(client, auth_headers, [(sku, 3)])  # counted in 'principal'
        client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        assert _stock(tid, sku, "principal")["current_stock"] == 3
        assert _stock(tid, sku, "Norte")["current_stock"] == 9

    def test_sku_ceiling_rolls_the_whole_apply_back(
        self, client, auth_headers, test_tenant, monkeypatch,
    ):
        """A new row would exceed max_skus: the apply fails and NOTHING lands,
        including the adjustment of the line that was fine."""
        tid = test_tenant["id"]
        ok_sku, new_sku = _sku(), _sku()
        _seed(client, auth_headers, ok_sku, stock=10)
        _seed(client, auth_headers, new_sku, stock=5, warehouse="Norte")
        cid = _closed(client, auth_headers, [(ok_sku, 6), (new_sku, 2)])
        execute("UPDATE tenants SET quota=%s WHERE id=%s",
                ('{"max_skus": 2}', tid))
        monkeypatch.setattr("backend.config.settings.testing_mode", False)
        r = client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        assert r.status_code == 403, r.text
        assert _stock(tid, ok_sku)["current_stock"] == 10
        assert _status(cid) == "closed"
        n = query_one("SELECT COUNT(*) AS c FROM stock_adjustments WHERE ref_id=%s", (cid,))
        assert n["c"] == 0
        assert _line(cid, ok_sku)["applied_at"] is None


# ── cancel ────────────────────────────────────────────────────────────────────

class TestCancel:
    def test_cancel_leaves_stock_alone_and_blocks_apply(self, client, auth_headers, test_tenant):
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _closed(client, auth_headers, [(sku, 1)])
        assert client.post(f"{API}/stock-counts/{cid}/cancel",
                           headers=auth_headers).status_code == 200
        assert _status(cid) == "cancelled"
        assert client.post(f"{API}/stock-counts/{cid}/apply", json={},
                           headers=auth_headers).status_code == 409
        assert _stock(test_tenant["id"], sku)["current_stock"] == 10

    def test_applied_count_cannot_be_cancelled(self, client, auth_headers):
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _closed(client, auth_headers, [(sku, 1)])
        client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        r = client.post(f"{API}/stock-counts/{cid}/cancel", headers=auth_headers)
        assert r.status_code == 409 and _status(cid) == "applied"


# ── tenant isolation and permissions ──────────────────────────────────────────

class TestIsolationAndPermissions:
    def test_other_tenant_cannot_see_or_touch_a_count(
        self, client, auth_headers, make_tenant_user_headers,
    ):
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _closed(client, auth_headers, [(sku, 1)])
        other = make_tenant_user_headers(role="admin")
        assert client.get(f"{API}/stock-counts/{cid}", headers=other).status_code == 404
        assert client.get(f"{API}/stock-counts/{cid}/preview", headers=other).status_code == 404
        assert client.post(f"{API}/stock-counts/{cid}/apply", json={},
                           headers=other).status_code == 404
        assert client.post(f"{API}/stock-counts/{cid}/cancel", headers=other).status_code == 404
        assert _scan(client, other, cid, sku).status_code == 404
        assert _status(cid) == "closed"
        assert client.get(f"{API}/stock-counts", headers=other).json()["data"] == []

    def test_viewer_is_denied_every_mutation_and_state_is_unchanged(
        self, client, auth_headers, viewer_headers, test_tenant,
    ):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        open_id = _new_count(client, auth_headers)
        _scan(client, auth_headers, open_id, sku, 1)
        closed_id = _closed(client, auth_headers, [(sku, 4)])

        assert client.post(f"{API}/stock-counts", json={}, headers=viewer_headers).status_code == 403
        assert _scan(client, viewer_headers, open_id, sku, 9).status_code == 403
        assert client.delete(f"{API}/stock-counts/{open_id}/lines/{sku}",
                             headers=viewer_headers).status_code == 403
        assert client.post(f"{API}/stock-counts/{open_id}/close",
                           headers=viewer_headers).status_code == 403
        assert client.post(f"{API}/stock-counts/{closed_id}/apply", json={},
                           headers=viewer_headers).status_code == 403
        assert client.post(f"{API}/stock-counts/{closed_id}/cancel",
                           headers=viewer_headers).status_code == 403

        assert _line(open_id, sku)["counted_qty"] == 1
        assert _status(open_id) == "open" and _status(closed_id) == "closed"
        assert _stock(tid, sku)["current_stock"] == 10
        n = query_one("SELECT COUNT(*) AS c FROM stock_counts WHERE tenant_id=%s", (tid,))
        assert n["c"] == 2

    def test_analyst_can_run_the_whole_flow(self, client, auth_headers, analyst_headers,
                                            test_tenant):
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _new_count(client, analyst_headers)
        assert _scan(client, analyst_headers, cid, sku, 6, mode="set").status_code == 200
        assert client.post(f"{API}/stock-counts/{cid}/close",
                           headers=analyst_headers).status_code == 200
        assert client.post(f"{API}/stock-counts/{cid}/apply", json={},
                           headers=analyst_headers).status_code == 200
        assert _stock(test_tenant["id"], sku)["current_stock"] == 6
        row = query_one("SELECT created_by, applied_by FROM stock_counts WHERE id=%s", (cid,))
        assert row["created_by"] and row["applied_by"] == row["created_by"]

    def test_tenant_erasure_leaves_no_count_rows(self, client, auth_headers, test_tenant):
        sku = _sku()
        _seed(client, auth_headers, sku, stock=10)
        cid = _closed(client, auth_headers, [(sku, 4)])
        client.post(f"{API}/stock-counts/{cid}/apply", json={}, headers=auth_headers)
        from backend.tenants.data_export import _DELETE_ORDER
        for t in ("stock_counts", "stock_count_lines", "stock_count_ops", "stock_adjustments"):
            assert t in _DELETE_ORDER
