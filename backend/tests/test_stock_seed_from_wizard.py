"""What the Quick Start wizard collects has to reach inventory_stock.

The wizard offers Inventario / Lead Time / Costo / Precio in its mapping step,
the runner keeps those columns in the DataFrame, and `sync_stock_from_dataset`
runs at the end of every training. They still never met: the sync looked for
`current_stock`/`lead_time_days`/`unit_cost`/`sale_price` while the canonical
wizard produces `inventory`/`lead_time`/`cost`/`price`. Two vocabularies, no
translation — so for every canonical_v1 session (the whole onboarding path) it
read the file and seeded nothing. Verified on a real completed training whose
mapping had `price` mapped: `inventory_stock` came out with 0 rows.

The dangerous half is the filter. `apply_canonical_defaults` broadcasts a
default into every UNMAPPED canonical column (inventory 0, lead_time 7), so
those columns always exist. Harvesting them unconditionally would write
current_stock = 0 across an entire catalogue and fire PEDIR_YA on all of it —
worse than seeding nothing at all.
"""

import pandas as pd
import pytest

from backend.db.connection import query_one
from backend.inventory.service import (
    _mapped_canonical_columns,
    sync_stock_from_dataset,
)


def _df(**cols):
    base = {"sku": ["A", "A"], "date": ["2025-01-01", "2025-01-02"]}
    base.update(cols)
    return pd.DataFrame(base)


def _stock(tid, sku="A"):
    return query_one(
        "SELECT current_stock, lead_time_days, unit_cost, sale_price "
        "FROM inventory_stock WHERE tenant_id = %s AND sku = %s",
        (tid, sku),
    )


class TestOnlyMappedFieldsAreHarvested:
    @pytest.mark.offline
    def test_an_unmapped_field_is_ignored(self):
        """The test that prevents the catastrophe: unmapped means absent, even
        though the column exists and is full of the default."""
        assert _mapped_canonical_columns({"inventory": None, "lead_time": None}) == {}

    @pytest.mark.offline
    def test_a_mapped_field_is_translated_to_its_stock_column(self):
        got = _mapped_canonical_columns(
            {"inventory": "existencias", "cost": "costo", "price": None})
        assert got == {"inventory": "current_stock", "cost": "unit_cost"}

    @pytest.mark.offline
    def test_no_mapping_at_all_harvests_nothing(self):
        assert _mapped_canonical_columns(None) == {}
        assert _mapped_canonical_columns({}) == {}


class TestSeedingFromTheWizard:
    def test_mapped_inventory_reaches_inventory_stock(self, client, test_tenant):
        tid = test_tenant["id"]
        df = _df(inventory=[10.0, 42.0], lead_time=[5, 5])
        n = sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"inventory": "existencias", "lead_time": "dias"},
        )
        assert n == 1
        row = _stock(tid)
        # Last row per SKU by date — a stock column is a snapshot, and averaging
        # or summing two snapshots means nothing.
        assert float(row["current_stock"]) == 42.0
        assert int(row["lead_time_days"]) == 5

    def test_unmapped_inventory_seeds_nothing(self, client, test_tenant):
        """`inventory` is present and full of zeros because nobody mapped it.
        Seeding that would put stock 0 on the whole catalogue."""
        tid = test_tenant["id"]
        df = _df(inventory=[0.0, 0.0], lead_time=[7, 7])
        n = sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"inventory": None, "lead_time": None},
        )
        assert n == 0
        assert _stock(tid) is None

    def test_cost_and_price_are_translated(self, client, test_tenant):
        tid = test_tenant["id"]
        df = _df(cost=[3.5, 4.0], price=[9.0, 9.5])
        sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"cost": "costo", "price": "precio"},
        )
        row = _stock(tid)
        assert float(row["unit_cost"]) == 4.0
        assert float(row["sale_price"]) == 9.5

    def test_a_native_header_still_wins_over_the_canonical_alias(self, client, test_tenant):
        """A file whose own header says current_stock keeps its meaning; the
        alias may add data, never override it."""
        tid = test_tenant["id"]
        df = _df(current_stock=[1.0, 99.0], inventory=[5.0, 5.0])
        sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"inventory": "existencias"},
        )
        assert float(_stock(tid)["current_stock"]) == 99.0

    def test_without_a_mapping_behaviour_is_unchanged(self, client, test_tenant):
        """Legacy (non-canonical) sessions pass no mapping and must keep
        reading their native headers exactly as before."""
        tid = test_tenant["id"]
        df = _df(current_stock=[8.0, 12.0])
        n = sync_stock_from_dataset(tid, df, group_col="sku", date_col="date")
        assert n == 1
        assert float(_stock(tid)["current_stock"]) == 12.0

    def test_the_numeric_floor_still_applies_to_a_mapped_column(self, client, test_tenant):
        """A stray 0 in a mapped lead-time column would collapse every signal
        threshold to 0; the existing floor must cover the alias too."""
        tid = test_tenant["id"]
        df = _df(inventory=[4.0, 4.0], lead_time=[0, 0])
        sync_stock_from_dataset(
            tid, df, group_col="sku", date_col="date",
            canonical_mapping={"inventory": "existencias", "lead_time": "dias"},
        )
        row = _stock(tid)
        assert float(row["current_stock"]) == 4.0
        assert int(row["lead_time_days"]) != 0
