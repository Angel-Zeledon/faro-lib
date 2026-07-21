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
