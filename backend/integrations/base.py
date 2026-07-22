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
