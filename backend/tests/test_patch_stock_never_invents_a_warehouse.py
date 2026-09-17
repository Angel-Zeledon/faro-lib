"""PATCH /inventory/stock/{sku} must update a row that exists, never create one.

The defect this guards (docs/stability.md §1.nonies): the endpoint checked
existence with `get_stock()` and NO warehouse filter — which finds the row
wherever it lives — and then wrote through `upsert_stock`, whose fallback for a
missing warehouse is 'principal'. A SKU that only lived in 'Norte' therefore
passed the 404 check on the Norte row and got a SECOND row inserted in
'principal'. Nobody counted those units; the consolidated view summed them, the
coverage went up, and a SKU that should have read PEDIR_YA reads OK — the
inverse of the invented zero in §1.2, and it costs the same money.

Every assertion here reads the DB directly: the response echo was never the
thing that was wrong.
"""
from uuid import uuid4

import pytest

from backend.db.connection import query


def _sku():
    return f"PW_{uuid4().hex[:8]}"


def _rows(tid, sku):
    """(warehouse -> current_stock) straight from the table."""
    return {
        r["warehouse"]: float(r["current_stock"])
        for r in query(
            "SELECT warehouse, current_stock FROM inventory_stock "
            "WHERE tenant_id = %s AND sku = %s",
            (tid, sku),
        )
    }


def _seed(tid, sku, warehouse, stock):
    from backend.inventory import service as inv_svc
    inv_svc.upsert_stock(tid, sku, {
        "current_stock": stock, "lead_time_days": 10, "warehouse": warehouse,
    })


class TestPatchLandsOnTheRowItChecked:
    def test_a_sku_that_lives_only_in_norte_is_patched_in_norte(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "Norte", 100)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 55},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text

        rows = _rows(tid, sku)
        # The bug, stated as an assertion: no row was conjured in 'principal'.
        assert "principal" not in rows, (
            f"PATCH invented a stock row in 'principal': {rows}"
        )
        assert rows == {"Norte": 55.0}
        assert resp.json()["data"]["warehouse"] == "Norte"

    def test_the_named_warehouse_is_the_one_written(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "Norte", 100)
        _seed(tid, sku, "Sur", 40)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 7, "warehouse": "Sur"},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        assert _rows(tid, sku) == {"Norte": 100.0, "Sur": 7.0}

    def test_a_lowercase_warehouse_resolves_to_the_existing_spelling(
        self, client, analyst_headers, test_tenant
    ):
        """resolve_canonical_name is what keeps ' norte ' from being read as a
        warehouse the SKU is not in — and then 404ing on a row that exists."""
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "Norte", 100)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 12, "warehouse": " norte "},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        assert _rows(tid, sku) == {"Norte": 12.0}


class TestPatchRefusesRatherThanGuessing:
    def test_a_warehouse_the_sku_is_not_in_is_a_404_and_changes_nothing(
        self, client, analyst_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "Norte", 100)
        before = _rows(tid, sku)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 3, "warehouse": "Sur"},
            headers=analyst_headers,
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "stock_sku_not_found_in_warehouse"
        assert _rows(tid, sku) == before

    def test_several_warehouses_and_no_principal_is_a_422_that_names_them(
        self, client, analyst_headers, test_tenant
    ):
        """The exact shape that used to fabricate the phantom row. There is no
        safe guess here, so the endpoint asks instead of picking."""
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "Norte", 100)
        _seed(tid, sku, "Sur", 40)
        before = _rows(tid, sku)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 3},
            headers=analyst_headers,
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error_code"] == "stock_warehouse_required"
        # The user has to be told WHICH ones, or the message is a dead end.
        assert "Norte" in body["error_params"]["warehouses"]
        assert "Sur" in body["error_params"]["warehouses"]
        assert _rows(tid, sku) == before

    def test_principal_still_wins_when_the_sku_is_in_it(
        self, client, analyst_headers, test_tenant
    ):
        """Preserved on purpose: this is what the endpoint has always done for
        a SKU that has a 'principal' row, and it updates a REAL row. Turning it
        into a 422 would break callers for no safety gain."""
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "principal", 100)
        _seed(tid, sku, "Norte", 40)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 9},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        assert _rows(tid, sku) == {"principal": 9.0, "Norte": 40.0}

    def test_an_unknown_sku_is_still_a_404(
        self, client, analyst_headers, test_tenant
    ):
        resp = client.patch(
            f"/api/v1/inventory/stock/{_sku()}",
            json={"current_stock": 1},
            headers=analyst_headers,
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "stock_sku_not_found"


class TestPermissionPair:
    def test_viewer_is_denied_and_the_row_is_untouched(
        self, client, viewer_headers, test_tenant
    ):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "Norte", 100)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 1},
            headers=viewer_headers,
        )
        assert resp.status_code == 403, resp.text
        assert _rows(tid, sku) == {"Norte": 100.0}

    def test_analyst_succeeds(self, client, analyst_headers, test_tenant):
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "Norte", 100)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}",
            json={"current_stock": 1},
            headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        assert _rows(tid, sku) == {"Norte": 1.0}


class TestEmptyPatch:
    def test_a_patch_with_no_fields_returns_the_targeted_row(
        self, client, analyst_headers, test_tenant
    ):
        """An empty body is a no-op, and the row it echoes must be the one it
        WOULD have written — not whichever row the SKU lookup happened to
        find."""
        tid = test_tenant["id"]
        sku = _sku()
        _seed(tid, sku, "Norte", 100)

        resp = client.patch(
            f"/api/v1/inventory/stock/{sku}", json={}, headers=analyst_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["warehouse"] == "Norte"
        assert _rows(tid, sku) == {"Norte": 100.0}
