"""Lists that grow into thousands of rows are filtered, sorted and paged by the
server. Each test seeds more rows than a page, then checks the page against the
database rather than against what the response says about itself.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one


@pytest.mark.integration
class TestSessionHistoryIsServerPaged:
    def _seed(self, tid, n=7):
        names = []
        for i in range(n):
            sid = f"sess_{uuid4().hex[:8]}"
            name = f"Plan {chr(ord('A') + i)}"
            execute(
                "INSERT INTO sessions (id, tenant_id, name, status, created_by, created_at) "
                "VALUES (%s,%s,%s,%s,'u',%s)",
                (sid, tid, name, "COMPLETED" if i % 2 == 0 else "FAILED",
                 datetime.now(timezone.utc) - timedelta(days=n - i)),
            )
            names.append(name)
        return names

    def test_paging_total_and_order_come_from_the_database(
        self, client, auth_headers, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        names = self._seed(tid)
        r = client.get("/api/v1/sessions/summary?limit=3&skip=0&sort=name_asc", headers=auth_headers)
        data = r.json()["data"]
        assert data["total"] == 7 == query_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE tenant_id=%s", (tid,))["n"]
        assert [i["name"] for i in data["items"]] == sorted(names)[:3]

        page3 = client.get("/api/v1/sessions/summary?limit=3&skip=6&sort=name_asc",
                           headers=auth_headers).json()["data"]
        assert [i["name"] for i in page3["items"]] == sorted(names)[6:]

    def test_status_and_search_filters_narrow_the_total_not_just_the_page(
        self, client, auth_headers, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        self._seed(tid)
        failed = client.get("/api/v1/sessions/summary?status=FAILED&limit=2", headers=auth_headers).json()["data"]
        assert failed["total"] == query_one(
            "SELECT COUNT(*) AS n FROM sessions WHERE tenant_id=%s AND status='FAILED'", (tid,))["n"] == 3
        assert len(failed["items"]) == 2
        assert {i["status"] for i in failed["items"]} == {"FAILED"}

        found = client.get("/api/v1/sessions/summary?q=plan%20c", headers=auth_headers).json()["data"]
        assert found["total"] == 1 and found["items"][0]["name"] == "Plan C"

    def test_newest_first_is_still_the_default_and_a_bad_sort_is_refused(
        self, client, auth_headers, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        self._seed(tid, n=3)
        items = client.get("/api/v1/sessions/summary", headers=auth_headers).json()["data"]["items"]
        assert [i["name"] for i in items] == ["Plan C", "Plan B", "Plan A"]
        assert client.get("/api/v1/sessions/summary?sort=name;DROP", headers=auth_headers).status_code == 422

    def test_another_tenant_sees_none_of_it(self, client, auth_headers, registered_user,
                                            make_tenant_user_headers):
        self._seed(registered_user["tenant"]["id"])
        outsider = make_tenant_user_headers(role="admin")
        data = client.get("/api/v1/sessions/summary?q=Plan", headers=outsider).json()["data"]
        assert data["total"] == 0 and data["items"] == []


@pytest.mark.integration
class TestPurchaseOrderHistoryIsServerPaged:
    def _seed(self, tid):
        base = datetime.now(timezone.utc)
        rows = []
        for i in range(8):
            po_id = f"po_{uuid4().hex[:8]}"
            sent = base if i % 2 == 0 else None
            paid = base if i in (0, 1) else None
            cancelled = base if i == 7 else None
            reception = "received" if i == 3 else "pending"
            execute(
                """INSERT INTO inventory_po_log
                   (id, tenant_id, generated_at, sku_count, total_units, total_value,
                    skus_order_now, skus_order_soon, reception_status, sent_at,
                    paid_at, cancelled_at, po_number)
                   VALUES (%s,%s,%s,1,10,100,1,0,%s,%s,%s,%s,%s)""",
                (po_id, tid, base - timedelta(hours=i), reception, sent, paid, cancelled, 1000 + i),
            )
            rows.append(po_id)
        return rows

    def test_pages_filters_and_the_awaiting_badge_are_computed_on_the_server(
        self, client, auth_headers, registered_user, viewer_headers,
    ):
        tid = registered_user["tenant"]["id"]
        ids = self._seed(tid)

        p1 = client.get("/api/v1/inventory/po-history/page?limit=3", headers=viewer_headers).json()["data"]
        assert p1["total"] == 8 and len(p1["items"]) == 3
        assert [i["id"] for i in p1["items"]] == ids[:3]          # newest first
        p3 = client.get("/api/v1/inventory/po-history/page?limit=3&offset=6", headers=viewer_headers).json()["data"]
        assert [i["id"] for i in p3["items"]] == ids[6:]

        # Awaiting = not cancelled and not fully received: 8 - 1 received - 1 cancelled.
        assert p1["awaiting_reception"] == query_one(
            "SELECT COUNT(*) AS n FROM inventory_po_log WHERE tenant_id=%s AND cancelled_at IS NULL "
            "AND reception_status IN ('pending','partial','not_received')", (tid,))["n"] == 6

        unpaid = client.get("/api/v1/inventory/po-history/page?status=unpaid", headers=viewer_headers).json()["data"]
        # sent (even index), not paid (not 0/1), not cancelled (not 7): 2, 4, 6.
        assert unpaid["total"] == 3
        assert {i["po_number"] for i in unpaid["items"]} == {1002, 1004, 1006}
        assert unpaid["awaiting_reception"] == 6, "the badge must not follow the table filter"

        cancelled = client.get("/api/v1/inventory/po-history/page?status=cancelled",
                               headers=viewer_headers).json()["data"]
        assert cancelled["total"] == 1 and cancelled["items"][0]["po_number"] == 1007
        by_number = client.get("/api/v1/inventory/po-history/page?q=1004", headers=viewer_headers).json()["data"]
        assert by_number["total"] == 1

    def test_the_tenants_history_does_not_leak(self, client, registered_user, make_tenant_user_headers):
        self._seed(registered_user["tenant"]["id"])
        outsider = make_tenant_user_headers(role="admin")
        data = client.get("/api/v1/inventory/po-history/page", headers=outsider).json()["data"]
        assert data["total"] == 0 and data["awaiting_reception"] == 0


@pytest.mark.integration
class TestSuppliersAndStockAreServerPaged:
    def test_suppliers_are_searched_and_paged_but_the_plain_list_stays_whole(
        self, client, auth_headers, registered_user,
    ):
        tid = registered_user["tenant"]["id"]
        for i in range(6):
            execute("INSERT INTO suppliers (tenant_id, name, email) VALUES (%s,%s,%s)",
                    (tid, f"Proveedor {i}", f"p{i}@acme.test" if i < 2 else None))
        page = client.get("/api/v1/inventory/suppliers/page?limit=4", headers=auth_headers).json()["data"]
        assert page["total"] == 6 and [s["name"] for s in page["items"]] == [f"Proveedor {i}" for i in range(4)]
        page2 = client.get("/api/v1/inventory/suppliers/page?limit=4&offset=4", headers=auth_headers).json()["data"]
        assert [s["name"] for s in page2["items"]] == ["Proveedor 4", "Proveedor 5"]
        found = client.get("/api/v1/inventory/suppliers/page?q=acme", headers=auth_headers).json()["data"]
        assert found["total"] == 2
        assert len(client.get("/api/v1/inventory/suppliers", headers=auth_headers).json()["data"]) == 6

    def test_stock_rows_are_paged_with_a_filtered_total(self, client, auth_headers, registered_user):
        tid = registered_user["tenant"]["id"]
        for i in range(7):
            execute("INSERT INTO inventory_stock (tenant_id, sku, warehouse, current_stock, category) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (tid, f"SKU-{i:03d}", "principal" if i < 5 else "Norte", 10, "tools" if i % 2 else "paint"))
        page = client.get("/api/v1/inventory/stock/page?limit=3&offset=3", headers=auth_headers).json()["data"]
        assert page["total"] == 7
        assert [r["sku"] for r in page["items"]] == ["SKU-003", "SKU-004", "SKU-005"]
        north = client.get("/api/v1/inventory/stock/page?warehouse=Norte", headers=auth_headers).json()["data"]
        assert north["total"] == 2
        tools = client.get("/api/v1/inventory/stock/page?q=tools", headers=auth_headers).json()["data"]
        assert tools["total"] == 3
        # /stock/{sku} still resolves: "page" did not swallow a SKU route.
        assert client.get("/api/v1/inventory/stock/SKU-001", headers=auth_headers).status_code == 200


@pytest.mark.integration
class TestInventoryStatusPaging:
    def test_a_page_changes_the_rows_never_the_summary(
        self, client, auth_headers, completed_session, registered_user,
    ):
        sid = completed_session["id"]
        for i in range(3):
            client.put(f"/api/v1/inventory/stock/SKU_{i + 1:03d}",
                       json={"current_stock": 5 + i}, headers=auth_headers)

        whole = client.get(f"/api/v1/inventory/status?session_id={sid}", headers=auth_headers).json()["data"]
        assert whole["page"] is None, "the original contract must not change when no limit is sent"
        n = len(whole["items"])
        assert n >= 2

        first = client.get(f"/api/v1/inventory/status?session_id={sid}&limit=1&sort=sku",
                           headers=auth_headers).json()["data"]
        assert len(first["items"]) == 1
        assert first["page"] == {"limit": 1, "offset": 0, "total": n, "sort": "sku"}
        assert first["summary"] == whole["summary"]
        skus = sorted(i["sku"] for i in whole["items"])
        assert first["items"][0]["sku"] == skus[0]

        second = client.get(f"/api/v1/inventory/status?session_id={sid}&limit=1&offset=1&sort=sku",
                            headers=auth_headers).json()["data"]
        assert second["items"][0]["sku"] == skus[1]

        found = client.get(f"/api/v1/inventory/status?session_id={sid}&limit=5&q={skus[0]}",
                           headers=auth_headers).json()["data"]
        assert [i["sku"] for i in found["items"]] == [skus[0]]
        assert found["page"]["total"] == 1
