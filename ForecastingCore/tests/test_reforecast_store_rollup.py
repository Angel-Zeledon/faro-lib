"""A session trained per (SKU, store) before stores were summed cannot be
re-forecast, and the refusal names that reason instead of "your columns
changed" — the user changed nothing."""
import pandas as pd
import pytest

from forecasting_core.config.config import SessionConfig
from forecasting_core.reforecast import ReforecastRefused
from forecasting_core.reforecast.prepare import input_schema
from forecasting_core.reforecast.reforecast import _check_schema


def _cfg(group_keys):
    return SessionConfig.from_dict({
        "name": "t",
        "columns": {"target": "ventas", "date": "fecha", "group_keys": group_keys},
        "models": {"lightgbm": {}},
    })


def _refusal(trained_keys, current_keys):
    ctx = {"input_schema": input_schema(_cfg(trained_keys))}
    with pytest.raises(ReforecastRefused) as exc:
        _check_schema(ctx, _cfg(current_keys), pd.DataFrame())
    return exc.value


def test_store_dimension_summed_since_training_has_its_own_code():
    err = _refusal(["producto", "tienda"], ["producto"])
    assert err.code == "stores_summed_since_training"
    assert err.params == {"trained_group_keys": "producto, tienda"}


def test_any_other_key_change_stays_schema_incompatible():
    assert _refusal(["producto"], ["producto", "tienda"]).code == "schema_incompatible"
    assert _refusal(["producto", "tienda"], ["sku"]).code == "schema_incompatible"


def test_the_same_keys_pass():
    ctx = {"input_schema": input_schema(_cfg(["producto"]))}
    _check_schema(ctx, _cfg(["producto"]), pd.DataFrame())
