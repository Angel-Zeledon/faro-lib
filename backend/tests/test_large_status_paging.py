"""The screens read the stock list one server page at a time, so a 2,500-SKU
tenant never ships 2,500 rows to render 100. These tests seed that volume
directly into the database and check each page, each total and each summary
against what the database holds, not against what the response says about itself.
"""
import pytest

from backend.db.connection import execute, query, query_one

N = 2500          # more than the largest page (500) several times over
NO_STOCK = 100    # the last SKUs have a forecast but no stock row


@pytest.fixture
def big_status(client, auth_headers, completed_session, registered_user):
    from tests.fixtures.synthetic_data import seed_completed_session

    tid = registered_user["tenant"]["id"]
    sid = completed_session["id"]
    seed_completed_session(tid, sid, n_skus=N)           # replaces the 3-SKU forecast
    execute("DELETE FROM inventory_stock WHERE tenant_id = %s", (tid,))
    execute(
        """INSERT INTO inventory_stock
             (tenant_id, sku, warehouse, display_name, current_stock, lead_time_days, unit_cost, supplier)
           SELECT %s, 'SKU_' || CASE WHEN g < 1000 THEN LPAD(g::text, 3, '0') ELSE g::text END,
                  'principal', 'Product ' || g,
                  (g * 37) %% 400, 3 + (g %% 20), 1 + (g %% 9), 'Supplier ' || LPAD((g %% 40)::text, 2, '0')
           FROM generate_series(1, %s) g""",
        (tid, N - NO_STOCK),
    )
    return {"tid": tid, "sid": sid}


def _get(client, headers, sid, qs=""):
    r = client.get(f"/api/v1/inventory/status?session_id={sid}{qs}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.mark.integration
class TestStatusAtVolume:
    """One test, one seeding: the status list is recomputed per request, so the
    volume is what makes each call expensive and the test keeps the calls few."""

    def test_pages_filters_sorts_and_summaries_at_volume(
        self, client, auth_headers, viewer_headers, big_status,
    ):
        sid, tid = big_status["sid"], big_status["tid"]
        whole = _get(client, auth_headers, sid)
        assert whole["page"] is None
        assert len(whole["items"]) == N == whole["summary"]["total_skus"]

        # The summary is computed over the set, so it equals what the rows say...
        by_signal = {}
        for i in whole["items"]:
            by_signal[i["signal"]] = by_signal.get(i["signal"], 0) + 1
        s = whole["summary"]
        assert s["order_now"] == by_signal.get("PEDIR_YA", 0)
        assert s["order_soon"] == by_signal.get("PEDIR_PRONTO", 0)
        assert s["ok"] == by_signal.get("OK", 0)
        assert s["overstock"] == by_signal.get("SOBRESTOCK", 0)
        assert s["sin_datos"] == by_signal.get("SIN_DATOS", 0)
        # ...and the stock rows the database holds.
        stocked = query_one("SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id=%s", (tid,))["n"]
        assert stocked == N - NO_STOCK
        assert s["without_stock"] == sum(1 for i in whole["items"] if not i["has_stock"]) >= NO_STOCK

        # Walking every page of 500 yields each SKU exactly once, in the unpaged
        # order, and no page changes the summary.
        seen = []
        for offset in range(0, N, 500):
            page = _get(client, viewer_headers, sid, f"&limit=500&offset={offset}")
            assert page["summary"] == whole["summary"]
            assert page["page"] == {"limit": 500, "offset": offset, "total": N, "sort": "urgency"}
            assert len(page["items"]) == min(500, N - offset)
            seen += [i["sku"] for i in page["items"]]
        assert seen == [i["sku"] for i in whole["items"]]
        assert len(set(seen)) == N

        # Filters narrow the total; a filtered page's summary describes the filter,
        # which is why the screen asks an unfiltered request for its KPI row.
        urgent = _get(client, auth_headers, sid, "&signal=PEDIR_YA&limit=50")
        assert urgent["page"]["total"] == whole["summary"]["order_now"]
        assert {i["signal"] for i in urgent["items"]} <= {"PEDIR_YA"}
        assert urgent["summary"]["total_skus"] == whole["summary"]["order_now"]

        # Search matches the product NAME as well as SKU, category and supplier.
        by_name = _get(client, auth_headers, sid, "&limit=10&q=Product%201234")
        assert [i["sku"] for i in by_name["items"]] == ["SKU_1234"]
        by_supplier = _get(client, auth_headers, sid, "&limit=500&q=supplier%2007")
        expected = query_one(
            "SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id=%s AND supplier='Supplier 07'",
            (tid,))["n"]
        assert by_supplier["page"]["total"] == expected > 50
        known = _get(client, auth_headers, sid, "&skus=SKU_005,SKU_2000,NOT_A_SKU")
        assert sorted(i["sku"] for i in known["items"]) == ["SKU_005", "SKU_2000"]

        # Column sorts follow the database values in both directions; SKUs with no
        # stock row count as missing: first when ascending, last when descending.
        asc = _get(client, auth_headers, sid, "&limit=40&sort=stock&order=asc")
        desc = _get(client, auth_headers, sid, "&limit=40&sort=stock&order=desc")
        assert asc["page"]["order"] == "asc"
        assert all(not i["has_stock"] for i in asc["items"])
        db_desc = [r["current_stock"] for r in query(
            "SELECT current_stock FROM inventory_stock WHERE tenant_id=%s ORDER BY current_stock DESC LIMIT 40", (tid,))]
        assert [i["current_stock"] for i in desc["items"]] == db_desc
        named = _get(client, auth_headers, sid, "&limit=3&q=Product&sort=name&order=desc")
        assert [i["sku"] for i in named["items"]] == [r["sku"] for r in query(
            "SELECT sku FROM inventory_stock WHERE tenant_id=%s ORDER BY LOWER(display_name) DESC LIMIT 3", (tid,))]
        assert client.get(f"/api/v1/inventory/status?session_id={sid}&limit=5&sort=bogus",
                          headers=auth_headers).status_code == 422
        assert client.get(f"/api/v1/inventory/status?session_id={sid}&limit=5&sort=stock&order=up",
                          headers=auth_headers).status_code == 422

        # Ordering rules for the two grouped views, on the real rows (the same
        # function the endpoint calls).
        from backend.inventory.service import sort_status_items
        runs, last = [], object()
        for i in sort_status_items(whole["items"], "supplier_urgency"):
            sup = i.get("supplier") or ""
            if sup != last:
                runs.append(sup)
                last = sup
        assert len(runs) == len(set(runs)), "a supplier's rows were split into two runs"
        flags = [i["signal"] in ("OK", "SIN_DATOS") for i in sort_status_items(whole["items"], "decision")]
        assert flags == sorted(flags), "an OK/SIN_DATOS row came before a row that needs a decision"


@pytest.mark.integration
class TestStockAndSuppliersAtVolume:
    def test_stock_list_pages_total_and_search_by_name(self, client, auth_headers, viewer_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        execute(
            """INSERT INTO inventory_stock (tenant_id, sku, warehouse, display_name, current_stock)
               SELECT %s, 'S' || LPAD(g::text, 5, '0'), 'principal', 'Item ' || g, g
               FROM generate_series(1, 2500) g""",
            (tid,),
        )
        page = client.get("/api/v1/inventory/stock/page?limit=500&offset=2000", headers=viewer_headers).json()["data"]
        assert page["total"] == 2500 == query_one(
            "SELECT COUNT(*) AS n FROM inventory_stock WHERE tenant_id=%s", (tid,))["n"]
        assert [r["sku"] for r in page["items"]] == [r["sku"] for r in query(
            "SELECT sku FROM inventory_stock WHERE tenant_id=%s ORDER BY sku, warehouse LIMIT 500 OFFSET 2000", (tid,))]
        named = client.get("/api/v1/inventory/stock/page?q=Item%202222", headers=viewer_headers).json()["data"]
        assert [r["sku"] for r in named["items"]] == ["S02222"]

    def test_suppliers_page_totals_and_search_at_volume(self, client, auth_headers, viewer_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        execute(
            """INSERT INTO suppliers (tenant_id, name, email)
               SELECT %s, 'Vendor ' || LPAD(g::text, 4, '0'), 'v' || g || '@example.test'
               FROM generate_series(1, 260) g""",
            (tid,),
        )
        page = client.get("/api/v1/inventory/suppliers/page?limit=50&offset=250", headers=viewer_headers).json()["data"]
        assert page["total"] == 260 == query_one(
            "SELECT COUNT(*) AS n FROM suppliers WHERE tenant_id=%s AND active", (tid,))["n"]
        assert [s["name"] for s in page["items"]] == [f"Vendor {i:04d}" for i in range(251, 261)]
        found = client.get("/api/v1/inventory/suppliers/page?q=vendor%200042", headers=viewer_headers).json()["data"]
        assert found["total"] == 1 and found["items"][0]["name"] == "Vendor 0042"
