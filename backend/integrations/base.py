"""Provider-agnostic contract + canonical DTOs for accounting imports."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


class IntegrationAuthError(Exception):
    """Credentials rejected by the provider."""


class IntegrationSyncError(Exception):
    """A recoverable failure while fetching/mapping provider data."""


@dataclass
class ProviderProduct:
    sku: str
    name: str
    unit_cost: Optional[float]


@dataclass
class ProviderStock:
    sku: str
    quantity: float
    warehouse: str  # 'principal' when the provider has no warehouse concept


@dataclass
class ProviderSaleLine:
    date: date
    sku: str
    quantity: float
    unit_price: Optional[float]
    store: Optional[str] = None  # branch/warehouse the sale shipped from; None when the payload has none


def parse_warehouse_name(value) -> Optional[str]:
    """Best-effort extraction of a warehouse/branch display name from a
    provider payload value.

    Both Alegra and Siigo represent a warehouse as an object like
    ``{"id": ..., "name": ...}``; be defensive and also accept a bare
    scalar (some payload variants carry just the name or id). Returns
    None when the value carries nothing usable.
    """
    if isinstance(value, dict):
        name = value.get("name") or value.get("id")
        return str(name) if name else None
    return str(value) if value else None


class AccountingProvider(ABC):
    def __init__(self, credentials: dict):
        self.credentials = credentials

    @abstractmethod
    def test_connection(self) -> None: ...
    @abstractmethod
    def fetch_products(self) -> list[ProviderProduct]: ...
    @abstractmethod
    def fetch_stock(self) -> list[ProviderStock]: ...
    @abstractmethod
    def fetch_sales(self, since: Optional[date] = None) -> list[ProviderSaleLine]: ...
