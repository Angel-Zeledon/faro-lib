"""Tests for the accounting-integrations provider base: DTOs, ABC, HTTP seam."""
import pytest
import requests


def test_provider_abc_and_dtos():
    from datetime import date
    from backend.integrations.base import (
        AccountingProvider, ProviderProduct, ProviderStock, ProviderSaleLine,
    )
    p = ProviderProduct(sku="A", name="Aceite", unit_cost=5.0)
    s = ProviderStock(sku="A", quantity=10.0, warehouse="principal")
    line = ProviderSaleLine(date=date(2026, 1, 1), sku="A", quantity=3.0, unit_price=8.0)
    assert (p.sku, s.quantity, line.quantity) == ("A", 10.0, 3.0)
    assert line.store is None  # store is optional and defaults to None

    class Dummy(AccountingProvider):
        def test_connection(self): pass
        def fetch_products(self): return [p]
        def fetch_stock(self): return [s]
        def fetch_sales(self, since=None): return [line]
    d = Dummy({})
    assert d.fetch_products()[0].sku == "A"


def test_alegra_maps_items_and_invoices(monkeypatch):
    from backend.integrations.alegra import AlegraProvider
    from backend.integrations import http

    # Real Alegra /items shape: cost + stock live under the nested "inventory"
    # object, not at the top level (confirmed against developer.alegra.com's
    # GET /items reference — see task-5-report.md for the exact source).
    items = [{"reference": "A", "name": "Aceite", "price": [{"idPriceList": 1, "price": 8}],
              "inventory": {"unit": "und", "availableQuantity": 10, "unitCost": 5}}]
    invoices = [{"date": "2026-01-01", "items": [{"reference": "A", "quantity": 3, "price": 8}]}]

    def fake_get(url, **kw):
        return items if url.endswith("/items") else invoices if url.endswith("/invoices") else []
    monkeypatch.setattr(http, "get_json", fake_get)

    p = AlegraProvider({"email": "a@b.com", "token": "t"})
    prods = p.fetch_products()
    stock = p.fetch_stock()
    sales = p.fetch_sales()
    assert prods[0].sku == "A" and prods[0].name == "Aceite" and prods[0].unit_cost == 5
    assert stock[0].sku == "A" and stock[0].quantity == 10 and stock[0].warehouse == "principal"
    assert sales[0].sku == "A" and sales[0].quantity == 3 and sales[0].unit_price == 8
    assert sales[0].store is None  # payload carried no warehouse anywhere


def test_alegra_sale_lines_carry_warehouse_when_present(monkeypatch):
    from backend.integrations.alegra import AlegraProvider
    from backend.integrations import http

    # Multi-warehouse Alegra accounts put a {"id", "name"} warehouse object on
    # the invoice header; item lines may override it with their own.
    invoices = [
        {"date": "2026-01-01", "warehouse": {"id": 2, "name": "Norte"},
         "items": [
             {"reference": "A", "quantity": 3, "price": 8},
             {"reference": "B", "quantity": 1, "price": 4,
              "warehouse": {"id": 3, "name": "Sur"}},
         ]},
        {"date": "2026-01-02",
         "items": [{"reference": "A", "quantity": 2, "price": 8}]},
    ]

    def fake_get(url, **kw):
        return invoices if url.endswith("/invoices") else []
    monkeypatch.setattr(http, "get_json", fake_get)

    p = AlegraProvider({"email": "a@b.com", "token": "t"})
    sales = p.fetch_sales()
    by_sku_date = {(s.sku, s.date.isoformat()): s.store for s in sales}
    assert by_sku_date[("A", "2026-01-01")] == "Norte"   # invoice-level warehouse
    assert by_sku_date[("B", "2026-01-01")] == "Sur"     # line-level wins over invoice-level
    assert by_sku_date[("A", "2026-01-02")] is None      # no warehouse info at all


def test_alegra_skips_items_without_reference(monkeypatch):
    from backend.integrations.alegra import AlegraProvider
    from backend.integrations import http

    items = [
        {"reference": "A", "name": "Aceite", "inventory": {"availableQuantity": 1, "unitCost": 1}},
        {"name": "No SKU here", "inventory": {"availableQuantity": 99, "unitCost": 1}},
    ]

    def fake_get(url, **kw):
        return items if url.endswith("/items") else []
    monkeypatch.setattr(http, "get_json", fake_get)

    p = AlegraProvider({"email": "a@b.com", "token": "t"})
    assert [prod.sku for prod in p.fetch_products()] == ["A"]
    assert [s.sku for s in p.fetch_stock()] == ["A"]


def test_alegra_paginates_items(monkeypatch):
    from backend.integrations.alegra import AlegraProvider
    from backend.integrations import http

    page1 = [
        {"reference": f"SKU{i}", "name": f"Item {i}",
         "inventory": {"availableQuantity": 1, "unitCost": 1}}
        for i in range(30)
    ]
    page2 = [{"reference": "SKU30", "name": "Item 30",
              "inventory": {"availableQuantity": 1, "unitCost": 1}}]
    seen_starts = []

    def fake_get(url, **kw):
        if not url.endswith("/items"):
            return []
        start = (kw.get("params") or {}).get("start", 0)
        seen_starts.append(start)
        return page1 if start == 0 else page2
    monkeypatch.setattr(http, "get_json", fake_get)

    p = AlegraProvider({"email": "a@b.com", "token": "t"})
    products = p.fetch_products()
    assert len(products) == 31
    assert products[-1].sku == "SKU30"
    assert seen_starts == [0, 30]


def test_alegra_401_raises_auth_error(monkeypatch):
    from backend.integrations.alegra import AlegraProvider
    from backend.integrations import http
    from backend.integrations.base import IntegrationAuthError

    def fake_get(url, **kw):
        resp = requests.Response()
        resp.status_code = 401
        raise requests.exceptions.HTTPError(response=resp)
    monkeypatch.setattr(http, "get_json", fake_get)

    p = AlegraProvider({"email": "a@b.com", "token": "bad"})
    with pytest.raises(IntegrationAuthError):
        p.test_connection()


def test_alegra_non_auth_error_raises_sync_error(monkeypatch):
    from backend.integrations.alegra import AlegraProvider
    from backend.integrations import http
    from backend.integrations.base import IntegrationSyncError

    def fake_get(url, **kw):
        resp = requests.Response()
        resp.status_code = 500
        raise requests.exceptions.HTTPError(response=resp)
    monkeypatch.setattr(http, "get_json", fake_get)

    p = AlegraProvider({"email": "a@b.com", "token": "t"})
    with pytest.raises(IntegrationSyncError):
        p.test_connection()


def test_siigo_auths_then_maps(monkeypatch):
    from backend.integrations.siigo import SiigoProvider
    from backend.integrations import http

    monkeypatch.setattr(http, "post_json", lambda url, **kw: {"access_token": "TOK"})
    # Real Siigo list responses wrap results in a pagination envelope
    # (confirmed against developers.siigo.com's "Listar Facturas" page and
    # consistent across list endpoints per docs) — see task-6-report.md.
    products = {"results": [
        {"code": "A", "name": "Aceite", "available_quantity": 10,
         "prices": [{"currency_code": "COP", "price_list": [{"position": 1, "name": "General", "value": 8}]}],
         "unit_cost": 5},
    ], "pagination": {"page": 1, "page_size": 30, "total_results": 1}}
    invoices = {"results": [
        {"date": "2026-01-01", "items": [{"code": "A", "quantity": 3, "price": 8}]},
    ], "pagination": {"page": 1, "page_size": 30, "total_results": 1}}

    def fake_get(url, **kw):
        return products if "/products" in url else invoices if "/invoices" in url else {"results": []}
    monkeypatch.setattr(http, "get_json", fake_get)

    p = SiigoProvider({"partner_id": "faro", "username": "u", "access_key": "k"})
    assert p.fetch_products()[0].sku == "A"
    assert p.fetch_stock()[0].quantity == 10
    sale = p.fetch_sales()[0]
    assert sale.quantity == 3
    assert sale.store is None  # payload carried no warehouse anywhere


def test_siigo_sale_lines_carry_warehouse_when_present(monkeypatch):
    from backend.integrations.siigo import SiigoProvider
    from backend.integrations import http

    monkeypatch.setattr(http, "post_json", lambda url, **kw: {"access_token": "TOK"})
    # Siigo accounts with inventory-by-warehouse enabled put a {"id", "name"}
    # warehouse object on each item line; fall back to the invoice header.
    invoices = {"results": [
        {"date": "2026-01-01",
         "items": [
             {"code": "A", "quantity": 3, "price": 8,
              "warehouse": {"id": 5, "name": "Bodega Centro"}},
             {"code": "B", "quantity": 1, "price": 4},
         ]},
        {"date": "2026-01-02", "warehouse": {"id": 6, "name": "Bodega Sur"},
         "items": [{"code": "A", "quantity": 2, "price": 8}]},
    ], "pagination": {"page": 1, "page_size": 30, "total_results": 2}}

    def fake_get(url, **kw):
        return invoices if "/invoices" in url else {"results": []}
    monkeypatch.setattr(http, "get_json", fake_get)

    p = SiigoProvider({"partner_id": "faro", "username": "u", "access_key": "k"})
    sales = p.fetch_sales()
    by_sku_date = {(s.sku, s.date.isoformat()): s.store for s in sales}
    assert by_sku_date[("A", "2026-01-01")] == "Bodega Centro"  # line-level warehouse
    assert by_sku_date[("B", "2026-01-01")] is None             # no warehouse on line or invoice
    assert by_sku_date[("A", "2026-01-02")] == "Bodega Sur"     # invoice-level fallback


def test_siigo_sends_bearer_and_partner_id_headers(monkeypatch):
    from backend.integrations.siigo import SiigoProvider
    from backend.integrations import http

    monkeypatch.setattr(http, "post_json", lambda url, **kw: {"access_token": "TOK"})
    seen_headers = {}

    def fake_get(url, **kw):
        seen_headers.update(kw.get("headers") or {})
        return {"results": []}
    monkeypatch.setattr(http, "get_json", fake_get)

    p = SiigoProvider({"partner_id": "faro", "username": "u", "access_key": "k"})
    p.fetch_products()
    assert seen_headers["Authorization"] == "Bearer TOK"
    assert seen_headers["Partner-Id"] == "faro"


def test_siigo_paginates_products(monkeypatch):
    from backend.integrations.siigo import SiigoProvider
    from backend.integrations import http

    monkeypatch.setattr(http, "post_json", lambda url, **kw: {"access_token": "TOK"})
    page1 = {"results": [
        {"code": f"SKU{i}", "name": f"Item {i}", "available_quantity": 1} for i in range(30)
    ]}
    page2 = {"results": [{"code": "SKU30", "name": "Item 30", "available_quantity": 1}]}
    seen_pages = []

    def fake_get(url, **kw):
        if "/products" not in url:
            return {"results": []}
        page = (kw.get("params") or {}).get("page", 1)
        seen_pages.append(page)
        return page1 if page == 1 else page2
    monkeypatch.setattr(http, "get_json", fake_get)

    p = SiigoProvider({"partner_id": "faro", "username": "u", "access_key": "k"})
    products = p.fetch_products()
    assert len(products) == 31
    assert products[-1].sku == "SKU30"
    assert seen_pages == [1, 2]


def test_siigo_auth_failure_raises_auth_error(monkeypatch):
    from backend.integrations.siigo import SiigoProvider
    from backend.integrations import http
    from backend.integrations.base import IntegrationAuthError

    def fake_post(url, **kw):
        resp = requests.Response()
        resp.status_code = 401
        raise requests.exceptions.HTTPError(response=resp)
    monkeypatch.setattr(http, "post_json", fake_post)

    p = SiigoProvider({"partner_id": "faro", "username": "u", "access_key": "bad"})
    with pytest.raises(IntegrationAuthError):
        p.test_connection()


def test_siigo_retries_on_429_then_succeeds(monkeypatch):
    from backend.integrations.siigo import SiigoProvider
    from backend.integrations import http, siigo as siigo_module

    monkeypatch.setattr(http, "post_json", lambda url, **kw: {"access_token": "TOK"})
    sleep_calls = []
    monkeypatch.setattr(siigo_module.time, "sleep", lambda s: sleep_calls.append(s))

    call_count = {"n": 0}

    def fake_get(url, **kw):
        call_count["n"] += 1
        if call_count["n"] < 3:
            resp = requests.Response()
            resp.status_code = 429
            raise requests.exceptions.HTTPError(response=resp)
        return {"results": [{"code": "A", "name": "Aceite", "available_quantity": 1}]}
    monkeypatch.setattr(http, "get_json", fake_get)

    p = SiigoProvider({"partner_id": "faro", "username": "u", "access_key": "k"})
    products = p.fetch_products()
    assert products[0].sku == "A"
    assert len(sleep_calls) == 2  # two 429s absorbed before the third call succeeds


def test_siigo_gives_up_after_bounded_429_retries(monkeypatch):
    from backend.integrations.siigo import SiigoProvider
    from backend.integrations import http, siigo as siigo_module
    from backend.integrations.base import IntegrationSyncError

    monkeypatch.setattr(http, "post_json", lambda url, **kw: {"access_token": "TOK"})
    sleep_calls = []
    monkeypatch.setattr(siigo_module.time, "sleep", lambda s: sleep_calls.append(s))

    def fake_get(url, **kw):
        resp = requests.Response()
        resp.status_code = 429
        raise requests.exceptions.HTTPError(response=resp)
    monkeypatch.setattr(http, "get_json", fake_get)

    p = SiigoProvider({"partner_id": "faro", "username": "u", "access_key": "k"})
    with pytest.raises(IntegrationSyncError):
        p.fetch_products()
    assert len(sleep_calls) == siigo_module._MAX_429_RETRIES
