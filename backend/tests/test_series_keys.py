"""Store-aware forecast key helpers (feature 5.4)."""

from backend.inventory.series import (
    SERIES_SEPARATOR, split_key, rollup_by_sku, stores_in, for_store,
)


def _fc(values):
    """Minimal single-model forecasts entry with the given forecast values."""
    return {"m1": {
        "historical": [],
        "forecast": [
            {"date": f"2026-08-{i+1:02d}", "value": v, "lower": None, "upper": None}
            for i, v in enumerate(values)
        ],
    }}


class TestSplitKey:
    def test_bare_sku(self):
        assert split_key("SKU_A") == ("SKU_A", None)

    def test_sku_with_store(self):
        assert split_key(f"SKU_A{SERIES_SEPARATOR}Norte") == ("SKU_A", "Norte")

    def test_only_first_separator_splits(self):
        key = f"SKU{SERIES_SEPARATOR}Store{SERIES_SEPARATOR}X"
        assert split_key(key) == ("SKU", f"Store{SERIES_SEPARATOR}X")


class TestRollup:
    def test_single_store_session_passthrough(self):
        fc = {"SKU_A": _fc([1.0, 2.0])}
        assert rollup_by_sku(fc) == fc

    def test_sums_across_stores_per_date(self):
        fc = {
            f"SKU_A{SERIES_SEPARATOR}Norte": _fc([1.0, 2.0]),
            f"SKU_A{SERIES_SEPARATOR}Sur":   _fc([10.0, 20.0]),
        }
        rolled = rollup_by_sku(fc)
        assert set(rolled) == {"SKU_A"}
        vals = [p["value"] for p in rolled["SKU_A"]["m1"]["forecast"]]
        assert vals == [11.0, 22.0]

    def test_stores_in_and_for_store(self):
        fc = {
            f"SKU_A{SERIES_SEPARATOR}Norte": _fc([1.0]),
            f"SKU_B{SERIES_SEPARATOR}Sur":   _fc([2.0]),
        }
        assert stores_in(fc) == {"Norte", "Sur"}
        norte = for_store(fc, "Norte")
        assert set(norte) == {"SKU_A"}
        assert norte["SKU_A"]["m1"]["forecast"][0]["value"] == 1.0
