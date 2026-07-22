"""Alegra provider: maps /items -> products+stock and /invoices -> sales.

Auth: HTTP Basic, with the account email as username and the Alegra API
token as password (`requests.auth.HTTPBasicAuth`).

Field-shape notes (see `.superpowers/sdd/task-5-report.md` for sources):
- `/items`: cost and stock quantity live under the nested `inventory` object
  (`inventory.unitCost`, `inventory.availableQuantity`), not at the item's
  top level. The SKU is the item's `reference` (a plain string for most
  countries; Costa Rica returns `{"type": ..., "reference": ...}`).
- `/invoices`: pagination and date fields (`date`, `date_afterOrNow` filter)
  are confirmed. The exact key Alegra uses for the product reference inside
  an invoice's `items` line (`reference` vs. a numeric `id` requiring a
  separate `/items` lookup) could not be confirmed from the docs — see the
  report for what remains to be verified against the live API.
"""
from datetime import date, datetime
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

from backend.config import settings
from backend.integrations import http
from backend.integrations.base import (
    AccountingProvider,
    IntegrationAuthError,
    IntegrationSyncError,
    ProviderProduct,
    ProviderSaleLine,
    ProviderStock,
    parse_warehouse_name,
)

_PAGE_LIMIT = 30


class AlegraProvider(AccountingProvider):
    def __init__(self, credentials: dict):
        super().__init__(credentials)
        self._auth = HTTPBasicAuth(credentials["email"], credentials["token"])
        self._base = settings.alegra_base_url
        self._items_cache: Optional[list[dict]] = None

    def test_connection(self) -> None:
        self._get("/items", params={"limit": 1})

    def fetch_products(self) -> list[ProviderProduct]:
        products = []
        for item in self._fetch_items():
            sku = self._item_sku(item)
            if not sku:
                continue
            inventory = item.get("inventory") or {}
            unit_cost = inventory.get("unitCost", item.get("unitCost"))
            products.append(ProviderProduct(sku=sku, name=item.get("name", ""), unit_cost=unit_cost))
        return products

    def fetch_stock(self) -> list[ProviderStock]:
        stock = []
        for item in self._fetch_items():
            sku = self._item_sku(item)
            if not sku:
                continue
            inventory = item.get("inventory") or {}
            quantity = inventory.get("availableQuantity") or 0
            stock.append(ProviderStock(sku=sku, quantity=quantity, warehouse="principal"))
        return stock

    def fetch_sales(self, since: Optional[date] = None) -> list[ProviderSaleLine]:
        params = {}
        if since is not None:
            params["date_afterOrNow"] = since.isoformat()
        sales = []
        for invoice in self._paginate("/invoices", params=params):
            invoice_date = self._parse_date(invoice.get("date"))
            if invoice_date is None:
                continue
            if since is not None and invoice_date < since:
                continue
            # Alegra puts the warehouse on the invoice header ({"id", "name"})
            # for accounts with the multi-warehouse feature; individual item
            # lines may also carry their own warehouse. Prefer the line's,
            # fall back to the invoice's, leave None when neither exists.
            invoice_store = parse_warehouse_name(invoice.get("warehouse"))
            for line in invoice.get("items") or []:
                sku = self._line_sku(line)
                if not sku:
                    continue
                sales.append(ProviderSaleLine(
                    date=invoice_date,
                    sku=sku,
                    quantity=line.get("quantity") or 0,
                    unit_price=line.get("price"),
                    store=parse_warehouse_name(line.get("warehouse")) or invoice_store,
                ))
        return sales

    # ── internals ────────────────────────────────────────────────────────

    def _fetch_items(self) -> list[dict]:
        """Fetch + paginate `/items` once per instance. `fetch_products` and
        `fetch_stock` both read this same endpoint, so within one sync
        (which calls both on the same provider instance) the second caller
        reuses the cached page set instead of re-walking the catalog."""
        if self._items_cache is None:
            self._items_cache = self._paginate("/items")
        return self._items_cache

    def _paginate(self, path: str, params: Optional[dict] = None) -> list[dict]:
        base_params = dict(params or {})
        start = 0
        results: list[dict] = []
        while True:
            page = self._get(path, params={**base_params, "start": start, "limit": _PAGE_LIMIT})
            if not page:
                break
            results.extend(page)
            if len(page) < _PAGE_LIMIT:
                break
            start += _PAGE_LIMIT
        return results

    def _get(self, path: str, params: Optional[dict] = None):
        url = f"{self._base}{path}"
        try:
            return http.get_json(url, auth=self._auth, params=params)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (401, 403):
                raise IntegrationAuthError(f"Alegra rejected credentials ({status}) for {path}") from exc
            raise IntegrationSyncError(f"Alegra request to {path} failed ({status})") from exc
        except requests.exceptions.RequestException as exc:
            raise IntegrationSyncError(f"Alegra request to {path} failed: {exc}") from exc

    @staticmethod
    def _item_sku(item: dict) -> Optional[str]:
        reference = item.get("reference")
        if isinstance(reference, dict):  # Costa Rica: {"type": ..., "reference": "..."}
            reference = reference.get("reference")
        if reference:
            return str(reference)
        code = item.get("code")
        return str(code) if code else None

    @staticmethod
    def _line_sku(line: dict) -> Optional[str]:
        reference = line.get("reference")
        if not reference:
            nested_item = line.get("item") or {}
            reference = nested_item.get("reference")
        if not reference:
            item_id = line.get("id")
            reference = item_id
        return str(reference) if reference else None

    @staticmethod
    def _parse_date(value) -> Optional[date]:
        if not value:
            return None
        if isinstance(value, date):
            return value
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
