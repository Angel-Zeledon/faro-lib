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


def parse_provider_number(value) -> Optional[float]:
    """Read a quantity or an amount out of a provider payload.

    The DTO fields are annotated `float`, but a dataclass does not enforce an
    annotation: an ERP that reports `"quantity": "10.5"` as a JSON string put a
    `str` straight into `ProviderSaleLine.quantity`, and the first arithmetic on
    it — `totals.get(key, 0.0) + line.quantity` in `_build_sales_csv` — raised
    `TypeError: unsupported operand type(s) for +: 'float' and 'str'`. Measured:
    ONE such line anywhere in the payload aborted the whole tenant's sync. The
    daily loop swallows that per connection, so nothing crashed, nobody was
    told, and the tenant's data simply stopped updating.

    The rules match `backend/workers/runner.py::_coerce_decimal_comma`, which is
    what the equivalent number written in an uploaded FILE goes through. Two
    paths reading the same number by different rules is how a distributor gets
    one answer from a CSV and another from their ERP:

        10.5      -> 10.5
        "10,5"    -> 10.5      comma decimal, how most of LatAm writes it
        "1.234,56"-> 1234.56   dot thousands, comma decimal
        "1,234"   -> None      1234 or 1.234 depending on the country

    `"1,234"` is refused rather than guessed. On the upload path the gate can
    ask the user which convention their file uses; a 3 a.m. daily sync has
    nobody to ask, and a silent 1000x on a purchase quantity is far worse than
    a line the caller is told it did not get.

    Returns None for anything unreadable — empty, text, NaN, infinity — so the
    caller decides whether to skip the line or fail. It never invents a zero:
    "the ERP sent something we could not read" and "the ERP sold none" are
    different facts, and collapsing them puts a phantom zero into the history.
    """
    import math
    import re

    if value is None:
        return None
    if isinstance(value, bool):          # True + True == 2 is never a quantity
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number

    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", text):      # 1,234 — ambiguous
        return None
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+,\d+", text):  # 1.234,56
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d+,\d+", text):                # 10,5
        text = text.replace(",", ".")

    try:
        number = float(text)
    except ValueError:
        return None
    return None if math.isnan(number) or math.isinf(number) else number


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
