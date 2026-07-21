"""Tests for the accounting-integrations provider base: DTOs, ABC, HTTP seam."""


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
