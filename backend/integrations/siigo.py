"""Siigo provider: OAuth2 auth, maps /products -> products+stock and /invoices -> sales.

Auth: OAuth2-style token exchange. `POST {base}/auth` with header `Partner-Id`
and JSON body `{"username": ..., "access_key": ...}` returns
`{"access_token": ...}`; the token is cached on the instance and sent as
`Authorization: Bearer <token>` (plus `Partner-Id`) on every subsequent read.

Field-shape notes (see `.superpowers/sdd/task-6-report.md` for sources):
- `/products` and `/invoices` list responses are wrapped in a pagination
  envelope: `{"pagination": {"page", "page_size", "total_results"}, "results": [...]}`
  (confirmed for `/invoices` against developers.siigo.com's "Listar Facturas"
  page; the same envelope is documented as consistent across Siigo list
  endpoints). `_paginate` also accepts a bare list defensively, since the
  brief's canned payload used that shape.
- `/products`: `code` is the SKU, `name` is the display name,
  `available_quantity` is the stock on hand. **Not confirmed**: a purchase
  cost field. The documented single-product schema
  (developers.siigo.com/docs/siigoapi/productos/consultar-producto) exposes
  `prices` (a sale price list) and no `unit_cost`/`average_cost` field — this
  needs live-API confirmation. `_product_unit_cost` reads `unit_cost` if the
  API ever returns one and otherwise yields `None` rather than guessing.
- `/invoices`: `date` (yyyy-MM-dd) and an `items` array of
  `{code, quantity, price}` are confirmed (developers.siigo.com's invoice
  create/list docs share the same item shape). Date-range filters are
  `date_start`/`date_end` (confirmed on the "Listar Facturas" page).
- Auth: `POST {base}/auth` with `{"username", "access_key"}` and header
  `Partner-Id` returning `{"access_token", "expires_in", "token_type"}` is
  confirmed via a public SDK's documented curl example. One doc snippet
  suggested the endpoint lives at `api.siigo.com/auth` (no `/v1`) rather than
  under the versioned base — that discrepancy is **not resolved** and should
  be checked against a live sandbox credential before going live; this
  implementation follows the brief's `{base}/auth` contract.
"""
import time
from datetime import date, datetime
from typing import Optional

import requests

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

_PAGE_SIZE = 30
_MAX_429_RETRIES = 3
_RETRY_SLEEP_SECONDS = 1


class SiigoProvider(AccountingProvider):
    def __init__(self, credentials: dict):
        super().__init__(credentials)
        self._base = settings.siigo_base_url
        self._token_cache: Optional[str] = None
        self._products_cache: Optional[list[dict]] = None

    def test_connection(self) -> None:
        self._token()

    def fetch_products(self) -> list[ProviderProduct]:
        products = []
        for item in self._fetch_products_raw():
            sku = self._product_sku(item)
            if not sku:
                continue
            products.append(ProviderProduct(sku=sku, name=item.get("name", ""), unit_cost=item.get("unit_cost")))
        return products

    def fetch_stock(self) -> list[ProviderStock]:
        stock = []
        for item in self._fetch_products_raw():
            sku = self._product_sku(item)
            if not sku:
                continue
            quantity = item.get("available_quantity") or 0
            stock.append(ProviderStock(sku=sku, quantity=quantity, warehouse="principal"))
        return stock

    def fetch_sales(self, since: Optional[date] = None) -> list[ProviderSaleLine]:
        params = {}
        if since is not None:
            params["date_start"] = since.isoformat()
        sales = []
        for invoice in self._paginate("/invoices", params=params):
            invoice_date = self._parse_date(invoice.get("date"))
            if invoice_date is None:
                continue
            if since is not None and invoice_date < since:
                continue
            # Siigo item lines carry a `warehouse` object ({"id", "name"})
            # when the account has inventory-by-warehouse enabled; some
            # payloads only reference it on the invoice header. Prefer the
            # line's, fall back to the invoice's, leave None when neither
            # exists.
            invoice_store = parse_warehouse_name(invoice.get("warehouse"))
            for line in invoice.get("items") or []:
                code = line.get("code")
                if not code:
                    continue
                sales.append(ProviderSaleLine(
                    date=invoice_date,
                    sku=str(code),
                    quantity=line.get("quantity") or 0,
                    unit_price=line.get("price"),
                    store=parse_warehouse_name(line.get("warehouse")) or invoice_store,
                ))
        return sales

    # ── internals ────────────────────────────────────────────────────────

    def _fetch_products_raw(self) -> list[dict]:
        """Fetch + paginate `/products` once per instance. `fetch_products`
        and `fetch_stock` both read this same endpoint, so within one sync
        (which calls both on the same provider instance) the second caller
        reuses the cached page set instead of re-walking the catalog."""
        if self._products_cache is None:
            self._products_cache = self._paginate("/products")
        return self._products_cache

    def _paginate(self, path: str, params: Optional[dict] = None) -> list[dict]:
        base_params = dict(params or {})
        page = 1
        results: list[dict] = []
        while True:
            response = self._get(path, params={**base_params, "page": page, "page_size": _PAGE_SIZE})
            page_results = self._unwrap(response)
            if not page_results:
                break
            results.extend(page_results)
            if len(page_results) < _PAGE_SIZE:
                break
            page += 1
        return results

    def _get(self, path: str, params: Optional[dict] = None):
        url = f"{self._base}{path}"
        attempts = 0
        while True:
            try:
                return http.get_json(url, headers=self._headers(), params=params)
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 429 and attempts < _MAX_429_RETRIES:
                    attempts += 1
                    time.sleep(_RETRY_SLEEP_SECONDS)
                    continue
                if status in (401, 403):
                    raise IntegrationAuthError(f"Siigo rejected credentials ({status}) for {path}") from exc
                raise IntegrationSyncError(f"Siigo request to {path} failed ({status})") from exc
            except requests.exceptions.RequestException as exc:
                raise IntegrationSyncError(f"Siigo request to {path} failed: {exc}") from exc

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Partner-Id": self.credentials["partner_id"],
        }

    def _token(self) -> str:
        if self._token_cache is None:
            self._token_cache = self._fetch_token()
        return self._token_cache

    def _fetch_token(self) -> str:
        url = f"{self._base}/auth"
        headers = {"Partner-Id": self.credentials["partner_id"]}
        body = {
            "username": self.credentials["username"],
            "access_key": self.credentials["access_key"],
        }
        try:
            response = http.post_json(url, headers=headers, json=body)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            raise IntegrationAuthError(f"Siigo auth failed ({status})") from exc
        except requests.exceptions.RequestException as exc:
            raise IntegrationAuthError(f"Siigo auth failed: {exc}") from exc
        token = response.get("access_token") if isinstance(response, dict) else None
        if not token:
            raise IntegrationAuthError("Siigo auth response missing access_token")
        return token

    @staticmethod
    def _unwrap(response) -> list[dict]:
        if isinstance(response, dict):
            return response.get("results") or []
        return response or []

    @staticmethod
    def _product_sku(item: dict) -> Optional[str]:
        code = item.get("code")
        return str(code) if code else None

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
