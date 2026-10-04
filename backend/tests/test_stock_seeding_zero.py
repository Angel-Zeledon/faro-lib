"""A price column must not invent an inventory count.

`inventory_stock.current_stock` is NOT NULL DEFAULT 0, so creating a row for a
SKU whose upload said nothing about stock materialises a 0 — and nothing
downstream can tell that 0 apart from a shelf that is genuinely empty.

Measured on a real upload of 200 SKUs whose only inventory-ish mapping was
`precio_unitario`: 200 rows appeared with a `sale_price` and `current_stock = 0`,
and the forecast screen showed **every one of them** as "Pedir YA" — an urgent
purchase recommendation for a catalogue nobody had ever counted.

The guard above this one already stopped unmapped columns from WRITING a zero.
This is the other door: the row itself.
"""

import pandas as pd

from backend.db.connection import query_one
from backend.inventory.service import sync_stock_from_dataset


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _stock(tenant_id: str, sku: str):
    return query_one(
        "SELECT current_stock, sale_price, lead_time_days FROM inventory_stock "
        "WHERE tenant_id = %s AND sku = %s", (tenant_id, sku),
    )


class TestPriceAloneCreatesNothing:
    def test_a_price_only_upload_creates_no_stock_row(self, test_tenant):
        tid = test_tenant["id"]
        df = _df([
            {"sku": "PRICE-1", "date": "2026-01-01", "demand": 5, "price": 169.64},
            {"sku": "PRICE-2", "date": "2026-01-02", "demand": 7, "price": 111.50},
        ])
        written = sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"sku": "sku", "date": "date", "demand": "demand",
                               "price": "price"},
        )
        assert written == 0, "a price column seeded inventory rows"
        assert _stock(tid, "PRICE-1") is None
        assert _stock(tid, "PRICE-2") is None

    def test_a_stock_column_does_create_the_row(self, test_tenant):
        """The feature still works: this is the mapping that means inventory."""
        tid = test_tenant["id"]
        df = _df([{"sku": "STK-1", "date": "2026-01-01", "demand": 5,
                   "inventory": 42, "price": 9.5}])
        written = sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"sku": "sku", "date": "date", "demand": "demand",
                               "inventory": "inventory", "price": "price"},
        )
        assert written == 1
        row = _stock(tid, "STK-1")
        assert row is not None
        assert row["current_stock"] == 42
        # The price rides along on a row that had a reason to exist.
        assert float(row["sale_price"]) == 9.5

    def test_a_lead_time_column_also_counts_as_inventory_data(self, test_tenant):
        """Replenishment terms describe the shelf too, so they may create a row."""
        tid = test_tenant["id"]
        df = _df([{"sku": "LT-1", "date": "2026-01-01", "demand": 5, "lead_time": 9}])
        written = sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"sku": "sku", "date": "date", "demand": "demand",
                               "lead_time": "lead_time"},
        )
        assert written == 1
        assert _stock(tid, "LT-1")["lead_time_days"] == 9


class TestPriceStillUpdatesWhatExists:
    def test_a_price_only_upload_updates_an_existing_row(self, test_tenant):
        """Refusing to CREATE must not turn price refreshes into a no-op — that
        would trade one silent failure for another."""
        tid = test_tenant["id"]
        seed = _df([{"sku": "UPD-1", "date": "2026-01-01", "demand": 5,
                     "inventory": 30}])
        sync_stock_from_dataset(
            tid, seed, group_col="sku", date_col="date",
            canonical_mapping={"sku": "sku", "date": "date", "demand": "demand",
                               "inventory": "inventory"},
        )
        assert _stock(tid, "UPD-1")["current_stock"] == 30

        later = _df([{"sku": "UPD-1", "date": "2026-02-01", "demand": 6,
                      "price": 77.25}])
        written = sync_stock_from_dataset(
            tid, later, group_col="sku", date_col="date",
            canonical_mapping={"sku": "sku", "date": "date", "demand": "demand",
                               "price": "price"},
        )
        assert written == 1, "the price refresh was dropped"
        row = _stock(tid, "UPD-1")
        assert float(row["sale_price"]) == 77.25
        assert row["current_stock"] == 30, "the count was overwritten by a price upload"
