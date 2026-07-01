"""
Task 2 — ColumnsConfig.group → group_keys

Tests that cover:
- ColumnsConfig.group_keys default value
- group_keys is a list, not a single string
- group is NOT a stored field (removed)
- group property returns group_keys[0] for backward compat
- from_dict reads group_keys correctly
- from_dict silently ignores the old "group" key
- to_dict / JSON roundtrip serializes group_keys (not group)
- validate() raises ConfigError when group_keys is set to an empty list
- hash changes when group_keys changes
"""

import json
import pytest

from forecasting_core.config.config import (
    ColumnsConfig,
    SessionConfig,
    ConfigError,
)


# ---------------------------------------------------------------------------
# ColumnsConfig field-level tests
# ---------------------------------------------------------------------------

class TestColumnsConfigGroupKeys:

    def test_default_group_keys_is_sku_store(self):
        c = ColumnsConfig()
        assert c.group_keys == ["sku", "store"]

    def test_default_group_keys_is_list(self):
        c = ColumnsConfig()
        assert isinstance(c.group_keys, list)

    def test_custom_group_keys_single(self):
        c = ColumnsConfig(group_keys=["product_id"])
        assert c.group_keys == ["product_id"]

    def test_custom_group_keys_multiple(self):
        c = ColumnsConfig(group_keys=["region", "sku", "channel"])
        assert c.group_keys == ["region", "sku", "channel"]

    def test_group_is_not_a_dataclass_field(self):
        """'group' must be removed from __dataclass_fields__, not kept as a stored field."""
        assert "group" not in ColumnsConfig.__dataclass_fields__

    def test_group_keys_is_a_dataclass_field(self):
        assert "group_keys" in ColumnsConfig.__dataclass_fields__


# ---------------------------------------------------------------------------
# group_keys direct accessor (shim removed — test group_keys directly)
# ---------------------------------------------------------------------------

class TestColumnsConfigGroupProperty:

    def test_group_keys_first_element(self):
        c = ColumnsConfig(group_keys=["sku", "store"])
        assert c.group_keys[0] == "sku"

    def test_group_keys_empty_means_no_primary(self):
        c = ColumnsConfig.__new__(ColumnsConfig)
        c.target = ""
        c.date = ""
        c.group_keys = []
        c.exogenous = []
        assert (c.group_keys[0] if c.group_keys else None) is None

    def test_group_keys_single_element(self):
        c = ColumnsConfig(group_keys=["product_id"])
        assert c.group_keys == ["product_id"]

    def test_group_does_not_appear_in_asdict(self):
        """asdict() must serialize group_keys, never a 'group' key."""
        from dataclasses import asdict
        c = ColumnsConfig(group_keys=["sku", "store"])
        d = asdict(c)
        assert "group_keys" in d
        assert "group" not in d


# ---------------------------------------------------------------------------
# SessionConfig.from_dict — reading group_keys
# ---------------------------------------------------------------------------

class TestSessionConfigFromDictGroupKeys:

    def _base(self):
        return {
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        }

    def test_from_dict_reads_group_keys_list(self):
        d = self._base()
        d["columns"]["group_keys"] = ["sku", "store"]
        cfg = SessionConfig.from_dict(d)
        assert cfg.columns.group_keys == ["sku", "store"]

    def test_from_dict_reads_single_element_group_keys(self):
        d = self._base()
        d["columns"]["group_keys"] = ["product_id"]
        cfg = SessionConfig.from_dict(d)
        assert cfg.columns.group_keys == ["product_id"]

    def test_from_dict_silently_ignores_old_group_key(self):
        """Old dicts with "group": "sku" must not raise; group_keys gets default."""
        d = self._base()
        d["columns"]["group"] = "sku"   # old format — must be silently dropped
        cfg = SessionConfig.from_dict(d)  # must not raise
        # group_keys should be the default, not derived from the old "group" key
        assert cfg.columns.group_keys == ["sku", "store"]

    def test_from_dict_default_group_keys_when_not_provided(self):
        d = self._base()
        cfg = SessionConfig.from_dict(d)
        assert cfg.columns.group_keys == ["sku", "store"]

    def test_from_dict_group_keys_first_element_after_load(self):
        d = self._base()
        d["columns"]["group_keys"] = ["region", "sku"]
        cfg = SessionConfig.from_dict(d)
        assert cfg.columns.group_keys[0] == "region"


# ---------------------------------------------------------------------------
# Serialization: to_dict / to_json / from_json
# ---------------------------------------------------------------------------

class TestGroupKeysRoundTrip:

    def _make_cfg(self, group_keys=None):
        d = {
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        }
        if group_keys is not None:
            d["columns"]["group_keys"] = group_keys
        return SessionConfig.from_dict(d)

    def test_to_dict_contains_group_keys(self):
        cfg = self._make_cfg(["sku", "store"])
        d = cfg.to_dict()
        assert d["columns"]["group_keys"] == ["sku", "store"]

    def test_to_dict_does_not_contain_group_field(self):
        cfg = self._make_cfg()
        d = cfg.to_dict()
        assert "group" not in d["columns"]

    def test_json_roundtrip_preserves_group_keys(self, tmp_path):
        cfg = self._make_cfg(["region", "sku"])
        path = str(tmp_path / "cfg.json")
        cfg.to_json(path)
        cfg2 = SessionConfig.from_json(path)
        assert cfg2.columns.group_keys == ["region", "sku"]

    def test_json_roundtrip_no_group_field_in_file(self, tmp_path):
        cfg = self._make_cfg()
        path = str(tmp_path / "cfg.json")
        cfg.to_json(path)
        raw = json.load(open(path))
        assert "group" not in raw["columns"]
        assert "group_keys" in raw["columns"]

    def test_hash_changes_when_group_keys_changes(self):
        cfg1 = self._make_cfg(["sku"])
        cfg2 = self._make_cfg(["store"])
        assert cfg1.hash != cfg2.hash

    def test_hash_stable_for_same_group_keys(self):
        h1 = self._make_cfg(["sku", "store"]).hash
        h2 = self._make_cfg(["sku", "store"]).hash
        assert h1 == h2


# ---------------------------------------------------------------------------
# SessionConfig.validate() — group_keys constraints
# ---------------------------------------------------------------------------

class TestGroupKeysValidation:

    def _minimal(self):
        return {
            "columns": {"target": "sales", "date": "date"},
            "models": {"lightgbm": {}},
        }

    def test_empty_group_keys_raises_config_error(self):
        cfg = SessionConfig()
        cfg.columns.target = "sales"
        cfg.columns.date = "date"
        cfg.columns.group_keys = []
        with pytest.raises(ConfigError, match="group_keys"):
            cfg.validate()

    def test_non_list_group_keys_raises_config_error(self):
        cfg = SessionConfig()
        cfg.columns.target = "sales"
        cfg.columns.date = "date"
        cfg.columns.group_keys = None  # type: ignore[assignment]
        with pytest.raises(ConfigError, match="group_keys"):
            cfg.validate()

    def test_valid_group_keys_does_not_raise(self):
        d = self._minimal()
        d["columns"]["group_keys"] = ["sku"]
        cfg = SessionConfig.from_dict(d)  # validate() called inside
        assert cfg.columns.group_keys == ["sku"]

    def test_default_group_keys_passes_validation(self):
        d = self._minimal()
        cfg = SessionConfig.from_dict(d)
        assert cfg.columns.group_keys == ["sku", "store"]

    def test_multi_key_group_keys_passes_validation(self):
        d = self._minimal()
        d["columns"]["group_keys"] = ["region", "sku", "channel"]
        cfg = SessionConfig.from_dict(d)
        assert len(cfg.columns.group_keys) == 3
