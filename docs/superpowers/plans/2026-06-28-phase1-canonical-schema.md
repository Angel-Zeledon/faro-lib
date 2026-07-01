# Phase 1 — Canonical Schema + SKU×Tienda + Aggregation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-group engine with a 14-field canonical schema where every series is identified by (SKU × Tienda), add bottom-up aggregation, and expose it through the backend API and Quick Start wizard.

**Architecture:** A new `canonical.py` defines the 14-field contract and auto-fill defaults; `ColumnsConfig.group` is replaced by `group_keys: List[str]`; `Trainer`, `Pipeline`, `FeatureEngineer`, and `ModelRouter` all switch from a single `group_col` string to the list; a new `aggregation/rollup.py` sums base forecasts by SKU and by Store; the backend Pydantic schema and the Quick Start page surface the 14-field mapper to users.

**Tech Stack:** Python 3.12, pandas, FastAPI/Pydantic v2, Next.js 14 (TypeScript), pytest

## Global Constraints

- All Python tests in `ForecastingCore/` run with `python -m pytest` from that directory.
- All backend tests run with `python -m pytest` from `backend/`.
- Never use mock data: test DataFrames must be constructed explicitly with `pd.DataFrame(...)`.
- The separator for composite series keys is `│` (U+2502), not `|` (pipe), to avoid clashing with CSV delimiters.
- Pydantic v2 — use `.model_dump()` not `.dict()`.
- Default `store` value when not mapped: `"Tienda única"`.
- Default `lead_time` when not mapped: `7` (int, days).
- `price`, `cost`, `regular_price`, `promo_price` default to `None` (not `0`) when unmapped.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `ForecastingCore/forecastlib/` | **Delete** | Dead library |
| `ForecastingCore/faro_prep_build/` | **Delete** | Dead library copy |
| `ForecastingCore/forecasting_core/data/canonical.py` | **Create** | 14-field schema, defaults, `apply_canonical_defaults()`, `SeriesKey` |
| `ForecastingCore/forecasting_core/config/config.py` | **Modify** | `ColumnsConfig.group → group_keys: List[str]` |
| `ForecastingCore/forecasting_core/data/profiler.py` | **Modify** | Add `get_canonical_mapping(df)` → `CanonicalMapping` |
| `ForecastingCore/forecasting_core/features/engineer.py` | **Modify** | `group: Optional[str]` → `group_cols: List[str]` |
| `ForecastingCore/forecasting_core/training/trainer.py` | **Modify** | `group_col: str` → `group_cols: List[str]`; result keys use `SeriesKey` |
| `ForecastingCore/forecasting_core/training/router.py` | **Modify** | Routing keys → `"{sku}│{store}"`; `skus_for_model` → `series_for_model` |
| `ForecastingCore/forecasting_core/pipelines/pipeline.py` | **Modify** | `c.group` → `c.group_keys`; wire aggregation |
| `ForecastingCore/forecasting_core/aggregation/__init__.py` | **Create** | Package marker |
| `ForecastingCore/forecasting_core/aggregation/rollup.py` | **Create** | `by_sku()`, `by_store()` |
| `ForecastingCore/tests/test_canonical_schema.py` | **Create** | Canonical schema unit tests |
| `ForecastingCore/tests/test_profiler_canonical.py` | **Create** | Profiler canonical mapping tests |
| `ForecastingCore/tests/test_trainer_multigroup.py` | **Create** | Trainer multi-column tests |
| `ForecastingCore/tests/test_aggregation.py` | **Create** | Rollup tests |
| `backend/schemas/configuration.py` | **Modify** | Add `CanonicalColumnsRequest` alongside old `ColumnsConfigRequest` |
| `backend/api/v1/configuration.py` | **Modify** | `configure_columns` accepts new schema; inspection returns canonical suggestions |
| `Frontend/src/lib/types.ts` | **Modify** | Add `CanonicalColumnsBody`, `CanonicalFieldSuggestion`, `CanonicalMapping`; extend `ColumnOptions` |
| `Frontend/src/lib/api.ts` | **Modify** | `chooseColumns` uses `CanonicalColumnsBody` |
| `Frontend/src/app/quick-start/page.tsx` | **Modify** | Step 2: 14-row mapper replacing 3 dropdowns |

---

### Task 0: Delete dead library trees

**Files:**
- Delete: `ForecastingCore/forecastlib/` (entire directory)
- Delete: `ForecastingCore/faro_prep_build/` (entire directory)

**Interfaces:**
- Produces: nothing — removes dead weight before other tasks touch imports.

- [ ] **Step 1: Verify nothing in `forecasting_core/` imports from these directories**

```bash
grep -r "from forecastlib" ForecastingCore/forecasting_core/
grep -r "import forecastlib" ForecastingCore/forecasting_core/
grep -r "faro_prep_build" ForecastingCore/forecasting_core/
```
Expected: no output (zero matches).

- [ ] **Step 2: Delete the directories**

```bash
rm -rf ForecastingCore/forecastlib
rm -rf ForecastingCore/faro_prep_build
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: delete dead forecastlib and faro_prep_build library trees"
```

---

### Task 1: Canonical schema module

**Files:**
- Create: `ForecastingCore/forecasting_core/data/canonical.py`
- Create: `ForecastingCore/tests/test_canonical_schema.py`

**Interfaces:**
- Produces:
  - `CANONICAL_FIELDS: list[dict]` — ordered list of field metadata
  - `FIELD_DEFAULTS: dict[str, Any]` — internal-name → default value (None for price fields)
  - `REQUIRED_FIELDS: frozenset[str]` — `{"sku", "date", "demand"}`
  - `apply_canonical_defaults(df: pd.DataFrame, mapping: dict[str, str | None]) -> pd.DataFrame` — takes user mapping, fills missing optional fields with defaults, returns df with 14 internal columns
  - `series_key(sku: Any, store: Any) -> str` — returns `f"{sku}│{store}"`
  - `parse_series_key(key: str) -> tuple[str, str]` — splits on `│`, returns `(sku, store)`

- [ ] **Step 1: Write the failing tests**

```python
# ForecastingCore/tests/test_canonical_schema.py
import pandas as pd
import pytest
from forecasting_core.data.canonical import (
    apply_canonical_defaults, series_key, parse_series_key,
    REQUIRED_FIELDS, FIELD_DEFAULTS,
)

def _base_df():
    return pd.DataFrame({
        "fecha": ["2024-01-01", "2024-01-02"],
        "prod":  ["SKU-001", "SKU-001"],
        "ventas": [10.0, 12.0],
    })

def test_required_fields_are_sku_date_demand():
    assert REQUIRED_FIELDS == frozenset({"sku", "date", "demand"})

def test_apply_defaults_adds_store_column_when_not_mapped():
    df = _base_df()
    mapping = {"sku": "prod", "date": "fecha", "demand": "ventas"}
    result = apply_canonical_defaults(df, mapping)
    assert "store" in result.columns
    assert (result["store"] == "Tienda única").all()

def test_apply_defaults_adds_lead_time_7_when_not_mapped():
    df = _base_df()
    mapping = {"sku": "prod", "date": "fecha", "demand": "ventas"}
    result = apply_canonical_defaults(df, mapping)
    assert (result["lead_time"] == 7).all()

def test_apply_defaults_sets_price_to_none_when_not_mapped():
    df = _base_df()
    mapping = {"sku": "prod", "date": "fecha", "demand": "ventas"}
    result = apply_canonical_defaults(df, mapping)
    assert result["price"].isna().all()

def test_apply_defaults_uses_source_column_when_mapped():
    df = _base_df()
    df["shop"] = ["Bogota", "Bogota"]
    mapping = {"sku": "prod", "date": "fecha", "demand": "ventas", "store": "shop"}
    result = apply_canonical_defaults(df, mapping)
    assert (result["store"] == "Bogota").all()

def test_required_field_missing_raises():
    df = _base_df()
    mapping = {"sku": "prod", "date": "fecha"}  # demand missing
    with pytest.raises(ValueError, match="demand"):
        apply_canonical_defaults(df, mapping)

def test_series_key_uses_pipe_separator():
    assert series_key("SKU-001", "Bogota") == "SKU-001│Bogota"

def test_parse_series_key_round_trips():
    key = series_key("SKU-001", "Bogota")
    assert parse_series_key(key) == ("SKU-001", "Bogota")
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd ForecastingCore
python -m pytest tests/test_canonical_schema.py -v
```
Expected: `ModuleNotFoundError: No module named 'forecasting_core.data.canonical'`

- [ ] **Step 3: Create `canonical.py`**

```python
# ForecastingCore/forecasting_core/data/canonical.py
from __future__ import annotations

from typing import Any
import pandas as pd

REQUIRED_FIELDS: frozenset[str] = frozenset({"sku", "date", "demand"})

# internal_name → default when not mapped (None = unknown, stays NaN)
FIELD_DEFAULTS: dict[str, Any] = {
    "sku":           None,   # required — no default
    "date":          None,   # required — no default
    "demand":        None,   # required — no default
    "store":         "Tienda única",
    "region":        "Sin región",
    "inventory":     0,
    "lead_time":     7,
    "price":         None,
    "cost":          None,
    "regular_price": None,
    "promo_price":   None,
    "promo":         False,
    "promo_type":    "Sin promoción",
    "discount":      0.0,
}

CANONICAL_FIELDS: list[dict] = [
    {"name": "sku",           "label": "SKU / Producto",      "required": True,  "dtype": "str"},
    {"name": "date",          "label": "Fecha",               "required": True,  "dtype": "date"},
    {"name": "demand",        "label": "Demanda",             "required": True,  "dtype": "float"},
    {"name": "store",         "label": "Tienda",              "required": False, "dtype": "str"},
    {"name": "region",        "label": "Región",              "required": False, "dtype": "str"},
    {"name": "inventory",     "label": "Inventario",          "required": False, "dtype": "float"},
    {"name": "lead_time",     "label": "Lead Time (días)",    "required": False, "dtype": "int"},
    {"name": "price",         "label": "Precio",              "required": False, "dtype": "float"},
    {"name": "cost",          "label": "Costo",               "required": False, "dtype": "float"},
    {"name": "regular_price", "label": "Precio Regular",      "required": False, "dtype": "float"},
    {"name": "promo_price",   "label": "Precio Promocional",  "required": False, "dtype": "float"},
    {"name": "promo",         "label": "Promoción",           "required": False, "dtype": "bool"},
    {"name": "promo_type",    "label": "Tipo de Promoción",   "required": False, "dtype": "str"},
    {"name": "discount",      "label": "Descuento",           "required": False, "dtype": "float"},
]

_SEPARATOR = "│"   # U+2502 — chosen to avoid conflicts with CSV pipe characters


def series_key(sku: Any, store: Any) -> str:
    """Stable string key for a (sku, store) series."""
    return f"{sku}{_SEPARATOR}{store}"


def parse_series_key(key: str) -> tuple[str, str]:
    """Split a series key back into (sku, store)."""
    parts = key.split(_SEPARATOR, 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid series key (expected sku│store): {key!r}")
    return parts[0], parts[1]


def apply_canonical_defaults(
    df: pd.DataFrame,
    mapping: dict[str, str | None],
) -> pd.DataFrame:
    """
    Apply canonical mapping to df, filling missing optional fields with defaults.

    Args:
        df:      Source DataFrame with user column names.
        mapping: {canonical_field: source_column | None}
                 None means "not in file — use default".

    Returns:
        DataFrame with 14 internal canonical columns plus all original columns.

    Raises:
        ValueError: if a required field (sku, date, demand) has no mapping.
    """
    for field in REQUIRED_FIELDS:
        if not mapping.get(field):
            raise ValueError(
                f"Required canonical field '{field}' has no column mapping. "
                "Provide a source column name."
            )
        src = mapping[field]
        if src not in df.columns:
            raise ValueError(
                f"Column '{src}' mapped to '{field}' does not exist in the DataFrame. "
                f"Available columns: {list(df.columns)}"
            )

    result = df.copy()

    for field_def in CANONICAL_FIELDS:
        name = field_def["name"]
        src  = mapping.get(name)

        if src and src in df.columns:
            result[name] = df[src]
        else:
            default = FIELD_DEFAULTS[name]
            result[name] = default   # broadcasts scalar to full column

    return result
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd ForecastingCore
python -m pytest tests/test_canonical_schema.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/data/canonical.py ForecastingCore/tests/test_canonical_schema.py
git commit -m "feat: add canonical 14-field schema with auto-fill defaults"
```

---

### Task 2: Config — `group` → `group_keys`

**Files:**
- Modify: `ForecastingCore/forecasting_core/config/config.py:72-76` (ColumnsConfig)

**Interfaces:**
- Consumes: nothing new
- Produces:
  - `ColumnsConfig.group_keys: List[str]` — replaces `.group: Optional[str]`
  - `SessionConfig.validate()` — no longer requires `columns.target`/`columns.date` (those move to `canonical_mapping`), but kept for backward compat during transition; `group_keys` validated to be a non-empty list when set

- [ ] **Step 1: Edit `ColumnsConfig`**

Replace lines 72–76 in `ForecastingCore/forecasting_core/config/config.py`:

```python
@dataclass
class ColumnsConfig:
    target: str = ""
    date: str = ""
    group_keys: List[str] = field(default_factory=lambda: ["sku", "store"])
    exogenous: List[str] = field(default_factory=list)
```

- [ ] **Step 2: Update `_config_as_validation_dict` in `pipeline.py` line 119**

In `ForecastingCore/forecasting_core/pipelines/pipeline.py`, change:
```python
# old
"group_id": cfg.columns.group,
```
to:
```python
# new
"group_id": cfg.columns.group_keys[0] if cfg.columns.group_keys else None,
```

- [ ] **Step 3: Verify existing pipeline tests still import without error**

```bash
cd ForecastingCore
python -c "from forecasting_core.config.config import SessionConfig; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add ForecastingCore/forecasting_core/config/config.py ForecastingCore/forecasting_core/pipelines/pipeline.py
git commit -m "feat: ColumnsConfig.group → group_keys list for multi-column grouping"
```

---

### Task 3: DataProfiler — canonical mapping

**Files:**
- Modify: `ForecastingCore/forecasting_core/data/profiler.py`
- Create: `ForecastingCore/tests/test_profiler_canonical.py`

**Interfaces:**
- Produces:
  - `DataProfiler.get_canonical_mapping(df: pd.DataFrame) -> dict` — returns `{"sku": {"top": "col", "candidates": [...], "confidence": 0.9, "can_use_default": False}, ...}` for all 14 canonical fields

- [ ] **Step 1: Write failing tests**

```python
# ForecastingCore/tests/test_profiler_canonical.py
import pandas as pd
from forecasting_core.data.profiler import DataProfiler

def _df_with_spanish_headers():
    return pd.DataFrame({
        "fecha":    ["2024-01-01", "2024-01-02", "2024-01-03"],
        "producto": ["SKU-A", "SKU-B", "SKU-A"],
        "ventas":   [10.0, 5.0, 8.0],
        "tienda":   ["Norte", "Sur", "Norte"],
        "stock":    [100, 50, 90],
        "precio":   [25.0, 30.0, 25.0],
    })

def _df_with_english_headers():
    return pd.DataFrame({
        "date":     ["2024-01-01", "2024-01-02", "2024-01-03"],
        "product":  ["A", "B", "A"],
        "sales":    [10.0, 5.0, 8.0],
        "store":    ["N", "S", "N"],
        "inventory":[100, 50, 90],
        "price":    [25.0, 30.0, 25.0],
    })

def test_date_field_detected_spanish():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["date"]["top"] == "fecha"
    assert result["date"]["confidence"] >= 0.7

def test_sku_field_detected_spanish():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["sku"]["top"] == "producto"
    assert result["sku"]["confidence"] >= 0.7

def test_demand_field_detected_spanish():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["demand"]["top"] == "ventas"

def test_store_field_detected_spanish():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["store"]["top"] == "tienda"

def test_price_detected_english():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_english_headers())
    assert result["price"]["top"] == "price"

def test_required_fields_cannot_use_default():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["sku"]["can_use_default"] is False
    assert result["date"]["can_use_default"] is False
    assert result["demand"]["can_use_default"] is False

def test_optional_fields_can_use_default():
    profiler = DataProfiler()
    result = profiler.get_canonical_mapping(_df_with_spanish_headers())
    assert result["lead_time"]["can_use_default"] is True
    assert result["cost"]["can_use_default"] is True
    assert result["promo_type"]["can_use_default"] is True

def test_undetected_field_has_none_top_and_low_confidence():
    profiler = DataProfiler()
    df = pd.DataFrame({
        "fecha": ["2024-01-01"], "producto": ["A"], "ventas": [10.0]
    })
    result = profiler.get_canonical_mapping(df)
    # 'store' not in df at all → top should be None, confidence 0
    assert result["store"]["top"] is None
    assert result["store"]["confidence"] == 0.0
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd ForecastingCore
python -m pytest tests/test_profiler_canonical.py -v
```
Expected: `AttributeError: 'DataProfiler' object has no attribute 'get_canonical_mapping'`

- [ ] **Step 3: Add `get_canonical_mapping` to `DataProfiler`**

Add this method after `get_column_options` (after line 148) in `profiler.py`:

```python
# Alias lists for each canonical field — ordered by match strength
_CANONICAL_ALIASES: dict[str, list[str]] = {
    "sku":           ["sku", "producto", "product", "articulo", "item",
                      "codigo", "code", "referencia", "ref", "id"],
    "date":          ["fecha", "date", "dt", "timestamp", "periodo", "mes", "semana"],
    "demand":        ["demanda", "demand", "ventas", "sales", "cantidad",
                      "qty", "units", "unidades", "pedidos"],
    "store":         ["tienda", "store", "sucursal", "punto_venta", "pdv",
                      "local", "canal", "negocio"],
    "region":        ["region", "zona", "area", "territory", "ciudad", "city"],
    "inventory":     ["inventario", "inventory", "stock", "existencias",
                      "disponible", "on_hand"],
    "lead_time":     ["lead_time", "leadtime", "tiempo_entrega",
                      "reposicion", "lt", "plazo"],
    "price":         ["precio", "price", "precio_venta", "pvp", "selling_price"],
    "cost":          ["costo", "cost", "precio_costo", "cog", "unit_cost"],
    "regular_price": ["precio_regular", "regular_price", "precio_base",
                      "precio_lista", "list_price"],
    "promo_price":   ["precio_promo", "promo_price", "precio_promocional",
                      "promotional_price"],
    "promo":         ["promocion", "promo", "oferta", "promotion",
                      "is_promo", "en_promo"],
    "promo_type":    ["tipo_promo", "promo_type", "tipo_oferta",
                      "tipo_promocion", "promotion_type"],
    "discount":      ["descuento", "discount", "pct_descuento",
                      "descuento_pct", "disc_pct"],
}

_REQUIRED_CANONICAL = frozenset({"sku", "date", "demand"})

def get_canonical_mapping(self, df: pd.DataFrame) -> dict:
    """
    Suggest which source column maps to each of the 14 canonical fields.

    Returns:
        {
          canonical_field: {
            "top":           str | None,   # best candidate column name
            "candidates":    list[str],    # all reasonable matches, ranked
            "confidence":    float,        # 0.0–1.0
            "can_use_default": bool,       # True for optional fields
          }
        }
    """
    col_names_lower = {c: c.lower().replace(" ", "_") for c in df.columns}
    result: dict = {}

    for field_name, aliases in self._CANONICAL_ALIASES.items():
        candidates: list[tuple[float, str]] = []   # (score, col)

        for col, col_lower in col_names_lower.items():
            score = 0.0
            for rank, alias in enumerate(aliases):
                if col_lower == alias:
                    score = 1.0 - rank * 0.03   # exact match, decays by alias rank
                    break
                if alias in col_lower or col_lower in alias:
                    score = max(score, 0.6 - rank * 0.02)

            # Type compatibility bonus
            if score > 0:
                if field_name == "date" and self._is_date(df[col]):
                    score = min(1.0, score + 0.2)
                elif field_name in ("demand", "inventory", "price", "cost",
                                    "regular_price", "promo_price", "discount",
                                    "lead_time"):
                    if pd.api.types.is_numeric_dtype(df[col]):
                        score = min(1.0, score + 0.1)
                elif field_name == "promo":
                    uniq = df[col].dropna().nunique()
                    if uniq <= 2:
                        score = min(1.0, score + 0.15)

            if score > 0:
                candidates.append((score, col))

        candidates.sort(key=lambda x: -x[0])
        top_score = candidates[0][0] if candidates else 0.0
        top_col   = candidates[0][1] if candidates else None

        result[field_name] = {
            "top":             top_col,
            "candidates":      [c for _, c in candidates],
            "confidence":      round(top_score, 2),
            "can_use_default": field_name not in _REQUIRED_CANONICAL,
        }

    return result
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd ForecastingCore
python -m pytest tests/test_profiler_canonical.py -v
```
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/data/profiler.py ForecastingCore/tests/test_profiler_canonical.py
git commit -m "feat: DataProfiler.get_canonical_mapping for 14-field auto-detection"
```

---

### Task 4: FeatureEngineer — `group` → `group_cols`

**Files:**
- Modify: `ForecastingCore/forecasting_core/features/engineer.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `FeatureEngineer.__init__(cfg, dt_col, target, group_cols: List[str] = [])` — replaces `group: Optional[str]`; internally creates `_group_key` temporary column as the join of all group columns

- [ ] **Step 1: Find the current constructor signature**

```bash
grep -n "def __init__" ForecastingCore/forecasting_core/features/engineer.py
```
Note the exact line number, then proceed.

- [ ] **Step 2: Replace `group` param with `group_cols`**

Find in `engineer.py`:
```python
def __init__(self, cfg, dt_col: str, target: str, group: Optional[str] = None):
```
Replace with:
```python
def __init__(self, cfg, dt_col: str, target: str,
             group_cols: Optional[List[str]] = None,
             group: Optional[str] = None):   # kept for one-step backward compat
    # Normalize: accept old single-string form during transition
    if group_cols is None:
        group_cols = [group] if group else []
    self._group_cols = group_cols
```

- [ ] **Step 3: Replace every use of `self._group` (or `self.group`) inside `engineer.py`**

Run:
```bash
grep -n "self\.group\b\|self\._group\b" ForecastingCore/forecasting_core/features/engineer.py
```
For each hit where a single group column name was used to `groupby(...)`, replace with a helper:

```python
def _groupby(self, df: pd.DataFrame):
    """Return a DataFrameGroupBy using the configured group columns."""
    if not self._group_cols:
        return [(None, df)]
    if len(self._group_cols) == 1:
        return df.groupby(self._group_cols[0])
    return df.groupby(self._group_cols)
```

And use `self._groupby(df)` in place of `df.groupby(self._group)` throughout the file.

- [ ] **Step 4: Verify the engine still imports cleanly**

```bash
cd ForecastingCore
python -c "from forecasting_core.features.engineer import FeatureEngineer; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/features/engineer.py
git commit -m "feat: FeatureEngineer accepts group_cols list for multi-column grouping"
```

---

### Task 5: Trainer — `group_col` → `group_cols`

**Files:**
- Modify: `ForecastingCore/forecasting_core/training/trainer.py`
- Create: `ForecastingCore/tests/test_trainer_multigroup.py`

**Interfaces:**
- Produces:
  - `Trainer.train(df, models, group_cols: List[str], target, dt)` — `group_col: str` replaced
  - Result keys: `f"{model_name}_{series_key(sku, store)}"` e.g. `"lightgbm_SKU-001│Bogota"`
  - Each result dict gains `"store": str` and `"sku": str` alongside existing `"model"`, `"mae"`, etc.

- [ ] **Step 1: Write failing tests**

```python
# ForecastingCore/tests/test_trainer_multigroup.py
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from forecasting_core.training.trainer import Trainer

def _two_sku_two_store_df():
    rows = []
    for sku in ["A", "B"]:
        for store in ["Norte", "Sur"]:
            for i in range(30):
                rows.append({
                    "sku": sku, "store": store,
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                    "sales": float(10 + i + (5 if sku == "B" else 0)),
                    "feat1": float(i),
                })
    return pd.DataFrame(rows)

def test_multigroup_produces_four_result_keys():
    df = _two_sku_two_store_df()
    models = {"linreg": LinearRegression()}
    trainer = Trainer(train_ratio=0.7, walk_forward=False)
    results = trainer.train(df, models, group_cols=["sku", "store"],
                            target="sales", dt="date")
    # 4 series × 1 model = 4 keys
    assert len(results) == 4

def test_multigroup_result_keys_contain_pipe_separator():
    df = _two_sku_two_store_df()
    models = {"linreg": LinearRegression()}
    trainer = Trainer(train_ratio=0.7, walk_forward=False)
    results = trainer.train(df, models, group_cols=["sku", "store"],
                            target="sales", dt="date")
    for key in results:
        assert "│" in key, f"Expected │ in key, got: {key}"

def test_multigroup_result_has_sku_and_store_fields():
    df = _two_sku_two_store_df()
    models = {"linreg": LinearRegression()}
    trainer = Trainer(train_ratio=0.7, walk_forward=False)
    results = trainer.train(df, models, group_cols=["sku", "store"],
                            target="sales", dt="date")
    for r in results.values():
        assert "sku" in r
        assert "store" in r

def test_single_group_col_still_works():
    df = _two_sku_two_store_df()
    models = {"linreg": LinearRegression()}
    trainer = Trainer(train_ratio=0.7, walk_forward=False)
    # Pass as list with one element
    results = trainer.train(df, models, group_cols=["sku"],
                            target="sales", dt="date")
    assert len(results) == 2   # 2 SKUs
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd ForecastingCore
python -m pytest tests/test_trainer_multigroup.py -v
```
Expected: `TypeError: train() got an unexpected keyword argument 'group_cols'`

- [ ] **Step 3: Modify `Trainer.train()` signature and body**

In `ForecastingCore/forecasting_core/training/trainer.py`, replace the `train` method signature at line 68:

```python
def train(
    self,
    df: pd.DataFrame,
    models: dict,
    group_cols: List[str],          # replaces group_col: str
    target: str,
    dt: str,
    group_col: str | None = None,   # deprecated alias, ignored if group_cols given
) -> Dict[str, dict]:
```

Replace the grouping logic at lines 89–95:

```python
# Normalise: accept deprecated single-col kwarg during transition
if not group_cols and group_col:
    group_cols = [group_col]

has_group = bool(group_cols) and all(c in df.columns for c in group_cols)
exclude = {dt, target} | set(group_cols)
trainable = {n: m for n, m in models.items() if hasattr(m, "fit")}
results = {}

if has_group:
    if len(group_cols) == 1:
        groups = df.groupby(group_cols[0])
    else:
        groups = df.groupby(group_cols)
else:
    groups = [("__all__", df)]

for group_val, g in groups:
    if isinstance(group_val, tuple):
        sku_val   = str(group_val[0]) if len(group_val) > 0 else "__all__"
        store_val = str(group_val[1]) if len(group_val) > 1 else "Tienda única"
    elif isinstance(group_val, str):
        sku_val   = group_val
        store_val = "Tienda única"
    else:
        sku_val   = str(group_val)
        store_val = "Tienda única"

    from forecasting_core.data.canonical import series_key
    sk = series_key(sku_val, store_val)
```

Then replace every use of `str(sku)` in `_wfv` and `_simple` results with `sk`, and add `"sku": sku_val, "store": store_val` to each result dict. Specifically:

In `_wfv` result at line 169, change:
```python
results[f"{name}_{sku}"] = {
    **avg, "sku": sku, "model": name, ...
```
to:
```python
results[f"{name}_{sk}"] = {
    **avg, "sku": sku_val, "store": store_val, "model": name, ...
```

In `_simple` result at line 198:
```python
results[f"{name}_{sk}"] = {
    **metrics, "sku": sku_val, "store": store_val, "model": name, ...
```

Pass `sku_val` (not `str(sku)`) to the existing `_wfv(X, y, trainable, sku_val)` and `_simple(X, y, trainable, sku_val)` calls, and update `_wfv`/`_simple` to receive `sku_val` + `store_val` as separate params (or pass `sk` directly).

- [ ] **Step 4: Run tests — expect pass**

```bash
cd ForecastingCore
python -m pytest tests/test_trainer_multigroup.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/training/trainer.py ForecastingCore/tests/test_trainer_multigroup.py
git commit -m "feat: Trainer.train() accepts group_cols list; result keys use series_key(sku│store)"
```

---

### Task 6: ModelRouter — series keys

**Files:**
- Modify: `ForecastingCore/forecasting_core/training/router.py`

**Interfaces:**
- Consumes: `dq_reports: Dict[str, SKUReport]` — keys stay as single SKU strings for now (DataQualityChecker still uses single-group; multi-group is Task 7 pipeline wiring)
- Produces:
  - `ModelRouter.series_for_model(routing, model)` — replaces `skus_for_model`; `skus_for_model` kept as alias for backward compat

- [ ] **Step 1: Add `series_for_model` alias in `router.py`**

After line 77 (end of `skus_for_model`), add:

```python
def series_for_model(self, routing: Dict[str, Set[str]], model: str) -> List[str]:
    """Return list of series keys that should run a specific model."""
    return [key for key, models in routing.items() if model in models]
```

- [ ] **Step 2: Verify router still imports**

```bash
cd ForecastingCore
python -c "from forecasting_core.training.router import ModelRouter; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add ForecastingCore/forecasting_core/training/router.py
git commit -m "feat: ModelRouter.series_for_model alias for multi-group series keys"
```

---

### Task 7: Aggregation module

**Files:**
- Create: `ForecastingCore/forecasting_core/aggregation/__init__.py`
- Create: `ForecastingCore/forecasting_core/aggregation/rollup.py`
- Create: `ForecastingCore/tests/test_aggregation.py`

**Interfaces:**
- Produces:
  - `aggregate_by_sku(forecast_df: pd.DataFrame) -> pd.DataFrame` — sums `forecast` column over `store`, groups by `(sku, date, step)`, returns rows with `sku, date, step, forecast`
  - `aggregate_by_store(forecast_df: pd.DataFrame) -> pd.DataFrame` — sums `forecast` over `sku`, groups by `(store, date, step)`, returns rows with `store, date, step, forecast`
  - Input contract: `forecast_df` must have columns `["sku", "store", "date", "forecast", "step"]`

- [ ] **Step 1: Write failing tests**

```python
# ForecastingCore/tests/test_aggregation.py
import pandas as pd
import pytest
from forecasting_core.aggregation.rollup import aggregate_by_sku, aggregate_by_store

def _base_forecast_df():
    rows = []
    for sku in ["A", "B"]:
        for store in ["Norte", "Sur"]:
            for step in [1, 2, 3]:
                rows.append({
                    "sku": sku, "store": store,
                    "date": pd.Timestamp("2024-02-01") + pd.Timedelta(days=step - 1),
                    "step": step,
                    "forecast": float(10 * (1 if sku == "A" else 2) * (1 if store == "Norte" else 3)),
                })
    return pd.DataFrame(rows)

def test_by_sku_sums_across_stores():
    df = _base_forecast_df()
    result = aggregate_by_sku(df)
    sku_a = result[result["sku"] == "A"]
    # A-Norte = 10, A-Sur = 30 → total = 40 per step
    assert (sku_a["forecast"] == 40.0).all()

def test_by_store_sums_across_skus():
    df = _base_forecast_df()
    result = aggregate_by_store(df)
    norte = result[result["store"] == "Norte"]
    # A-Norte=10, B-Norte=20 → total = 30 per step
    assert (norte["forecast"] == 30.0).all()

def test_by_sku_has_no_store_column():
    df = _base_forecast_df()
    result = aggregate_by_sku(df)
    assert "store" not in result.columns

def test_by_store_has_no_sku_column():
    df = _base_forecast_df()
    result = aggregate_by_store(df)
    assert "sku" not in result.columns

def test_no_double_counting_by_sku():
    df = _base_forecast_df()
    grand_total_base  = df["forecast"].sum()
    by_sku_total      = aggregate_by_sku(df)["forecast"].sum()
    by_store_total    = aggregate_by_store(df)["forecast"].sum()
    # Each step appears for 2 stores in by_sku / 2 skus in by_store
    # grand_total per step = 10+30+20+60 = 120 × 3 steps = 360
    assert by_sku_total == grand_total_base
    assert by_store_total == grand_total_base
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd ForecastingCore
python -m pytest tests/test_aggregation.py -v
```
Expected: `ModuleNotFoundError: No module named 'forecasting_core.aggregation'`

- [ ] **Step 3: Create `__init__.py`**

```python
# ForecastingCore/forecasting_core/aggregation/__init__.py
```
(empty file)

- [ ] **Step 4: Create `rollup.py`**

```python
# ForecastingCore/forecasting_core/aggregation/rollup.py
from __future__ import annotations
import pandas as pd


def aggregate_by_sku(forecast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Sum base (SKU × Store) forecasts by SKU across all stores.

    Input:  DataFrame with columns [sku, store, date, step, forecast]
    Output: DataFrame with columns [sku, date, step, forecast]
    """
    return (
        forecast_df
        .groupby(["sku", "date", "step"], as_index=False)["forecast"]
        .sum()
    )


def aggregate_by_store(forecast_df: pd.DataFrame) -> pd.DataFrame:
    """
    Sum base (SKU × Store) forecasts by Store across all SKUs.

    Input:  DataFrame with columns [sku, store, date, step, forecast]
    Output: DataFrame with columns [store, date, step, forecast]
    """
    return (
        forecast_df
        .groupby(["store", "date", "step"], as_index=False)["forecast"]
        .sum()
    )
```

- [ ] **Step 5: Run tests — expect pass**

```bash
cd ForecastingCore
python -m pytest tests/test_aggregation.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add ForecastingCore/forecasting_core/aggregation/ ForecastingCore/tests/test_aggregation.py
git commit -m "feat: aggregation rollup module — by_sku and by_store bottom-up sums"
```

---

### Task 8: Pipeline wiring — `group_cols` + aggregation

**Files:**
- Modify: `ForecastingCore/forecasting_core/pipelines/pipeline.py`

**Interfaces:**
- Consumes: all prior tasks
- Produces:
  - `PipelineResults.forecast_by_sku_df: Optional[pd.DataFrame]` — new field
  - `PipelineResults.forecast_by_store_df: Optional[pd.DataFrame]` — new field
  - Pipeline reads `cfg.columns.group_keys` (Task 2) and passes it to `Trainer.train(group_cols=...)`, `FeatureEngineer(group_cols=...)`, `DataQualityChecker`

- [ ] **Step 1: Add new fields to `PipelineResults`**

In `pipeline.py`, extend the `PipelineResults` dataclass (lines 47–58):

```python
@dataclass
class PipelineResults:
    metrics_df:           Optional[pd.DataFrame] = None
    forecast_df:          Optional[pd.DataFrame] = None
    forecast_by_sku_df:   Optional[pd.DataFrame] = None   # NEW
    forecast_by_store_df: Optional[pd.DataFrame] = None   # NEW
    inventory_df:         Optional[pd.DataFrame] = None
    quality_df:           Optional[pd.DataFrame] = None
    run_id:               str = ""
    config_hash:          str = ""
    metadata:             dict = field(default_factory=dict)
    fitted_models:        dict = field(default_factory=dict)
    stat_forecasts:       dict = field(default_factory=dict)
```

- [ ] **Step 2: Update `Pipeline.run()` to use `group_keys`**

In `pipeline.py` around line 262, find:
```python
engineer = FeatureEngineer(cfg.features, dt_col=c.date, target=c.target, group=c.group)
```
Replace with:
```python
engineer = FeatureEngineer(
    cfg.features, dt_col=c.date, target=c.target,
    group_cols=c.group_keys,
)
```

Around line 302, find:
```python
results_ml = trainer.train(df_ml_f, ml_models, c.group or None, c.target, c.date)
```
Replace with:
```python
results_ml = trainer.train(
    df_ml_f, ml_models,
    group_cols=c.group_keys,
    target=c.target, dt=c.date,
) if ml_models else {}
```

Do the same for the quantile training call around line 310.

Around line 247, update the DataQualityChecker instantiation to use `group_col=c.group_keys[0] if c.group_keys else None` (DataQualityChecker still takes single col; multi-col checker is Phase 2).

- [ ] **Step 3: Wire aggregation at the end of `run()`**

After the section that builds `forecast_df`, add:

```python
from forecasting_core.aggregation.rollup import aggregate_by_sku, aggregate_by_store

results = PipelineResults(
    metrics_df=metrics_df,
    forecast_df=forecast_df,
    ...
)

if forecast_df is not None and "store" in forecast_df.columns:
    results.forecast_by_sku_df   = aggregate_by_sku(forecast_df)
    results.forecast_by_store_df = aggregate_by_store(forecast_df)
```

- [ ] **Step 4: Smoke test the pipeline imports**

```bash
cd ForecastingCore
python -c "from forecasting_core.pipelines.pipeline import Pipeline, PipelineResults; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add ForecastingCore/forecasting_core/pipelines/pipeline.py
git commit -m "feat: Pipeline uses group_keys list and produces forecast_by_sku/store rollups"
```

---

### Task 9: Backend schema — `CanonicalColumnsRequest`

**Files:**
- Modify: `backend/schemas/configuration.py`

**Interfaces:**
- Produces:
  - `CanonicalColumnsRequest` Pydantic model with fields:
    - `canonical_mapping: dict[str, str | None]` — 14 fields, required ones validated
    - `defaults_override: dict[str, Any]` — optional overrides (e.g. `lead_time: 14`)

- [ ] **Step 1: Add the new model to `configuration.py`**

After `ColumnsConfigRequest` (line 37), add:

```python
class CanonicalColumnsRequest(BaseModel):
    """New canonical 14-field column mapping request."""
    canonical_mapping: Dict[str, Optional[str]] = {}
    defaults_override: Dict[str, Any] = {}

    def validate_required(self, available_columns: list[str]) -> None:
        """
        Raise HTTPException-compatible ValueError if required fields are missing
        or mapped to columns that don't exist.
        """
        from forecasting_core.data.canonical import REQUIRED_FIELDS
        errors: list[str] = []
        for field in REQUIRED_FIELDS:
            src = self.canonical_mapping.get(field)
            if not src:
                errors.append(f"'{field}' es requerido y no tiene columna mapeada.")
            elif src not in available_columns:
                errors.append(
                    f"'{field}' → columna '{src}' no existe en el archivo. "
                    f"Columnas disponibles: {', '.join(available_columns)}."
                )
        for field, src in self.canonical_mapping.items():
            if src and src not in available_columns and field not in REQUIRED_FIELDS:
                errors.append(
                    f"'{field}' → columna '{src}' no existe en el archivo."
                )
        if errors:
            raise ValueError("; ".join(errors))
```

- [ ] **Step 2: Verify import**

```bash
cd backend
python -c "from backend.schemas.configuration import CanonicalColumnsRequest; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/schemas/configuration.py
git commit -m "feat: CanonicalColumnsRequest Pydantic model for 14-field column mapping"
```

---

### Task 10: Backend API — canonical columns endpoint + inspect canonical suggestions

**Files:**
- Modify: `backend/api/v1/configuration.py`

**Interfaces:**
- Produces:
  - `POST /sessions/{id}/configure/columns` now accepts both old `ColumnsConfigRequest` AND new `CanonicalColumnsRequest` (detected by presence of `canonical_mapping` key)
  - `GET /sessions/{id}/inspect` response gains `canonical_suggestions: dict` field

- [ ] **Step 1: Add canonical mapping to the inspect endpoint response**

In `configuration.py` at line 110, extend the `inspection` dict:

```python
from forecasting_core.data.profiler import DataProfiler

profiler_instance = DataProfiler()
col_options      = engine.get_column_options()
canonical_suggestions = profiler_instance.get_canonical_mapping(engine._df)  # or equivalent

inspection = {
    "profile":               profile,
    "column_options":        col_options,
    "canonical_suggestions": canonical_suggestions,   # NEW
    "config_schema":         config_schema,
    "inspected_at":          _now(),
}
```

Note: `engine._df` or however the engine exposes the loaded DataFrame. Check `ForecastingCore/forecasting_core/engine.py` for the attribute name.

- [ ] **Step 2: Add `CanonicalColumnsRequest` handling to `configure_columns`**

In `configuration.py`, update the imports at line 31:

```python
from backend.schemas.configuration import (
    AttachDatasetRequest, BusinessConfigRequest, ColumnsConfigRequest,
    CanonicalColumnsRequest,                      # NEW
    FeaturesConfigRequest, ForecastConfigRequest, ModelsConfigRequest,
    ValidationConfigRequest,
)
```

Replace the `configure_columns` function body to detect which schema was used:

```python
@router.post("/sessions/{session_id}/configure/columns")
def configure_columns(
    session_id: str,
    body: dict,                                   # accept raw dict first
    user: CurrentUser = Depends(require_analyst_or_above),
):
    s = _get_session_or_404(user.tenant_id, session_id)
    inspection = session_store.get_field(user.tenant_id, session_id, "inspection") or {}
    real_columns = [c["name"] for c in inspection.get("profile", {}).get("columns", [])]

    if "canonical_mapping" in body:
        req = CanonicalColumnsRequest(**body)
        try:
            req.validate_required(real_columns)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        config = {
            **req.model_dump(),
            "schema_version": "canonical_v1",
            "configured_at":  _now(),
            "configured_by":  user.user_id,
        }
    else:
        req_old = ColumnsConfigRequest(**body)

        def _require_column(value, field_label):
            if not value or not value.strip():
                raise HTTPException(422, f"'{field_label}' column is required.")
            if real_columns and value not in real_columns:
                raise HTTPException(422, f"Column '{value}' not in file. Available: {', '.join(real_columns)}.")

        _require_column(req_old.date_column, "Date")
        _require_column(req_old.target_column, "Target")
        if req_old.sku_column and real_columns and req_old.sku_column not in real_columns:
            raise HTTPException(422, f"SKU column '{req_old.sku_column}' not in file.")
        config = {**req_old.model_dump(), "configured_at": _now(), "configured_by": user.user_id}

    session_store.set_field(user.tenant_id, session_id, "columns_cfg", config)
    if s["status"] in ("INSPECTED", "COLUMNS_CONFIGURED", "FEATURES_CONFIGURED",
                        "MODELS_CONFIGURED", "COMPLETED", "FAILED"):
        try:
            session_svc.transition(user.tenant_id, session_id, "COLUMNS_CONFIGURED", "features")
        except ValueError:
            pass
    return ok(config)
```

- [ ] **Step 3: Check the engine's DataFrame attribute**

```bash
grep -n "_df\|self\.df\b" ForecastingCore/forecasting_core/engine.py | head -20
```
Use the correct attribute name in Step 1.

- [ ] **Step 4: Run offline tests to verify nothing broke**

```bash
cd backend
python -m pytest tests/test_endpoints_offline.py -q --tb=short
```
Expected: 91 passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add backend/api/v1/configuration.py backend/schemas/configuration.py
git commit -m "feat: configure_columns accepts CanonicalColumnsRequest; inspect returns canonical_suggestions"
```

---

### Task 11: Frontend types — canonical additions

**Files:**
- Modify: `Frontend/src/lib/types.ts`

**Interfaces:**
- Produces new exported types:
  - `CanonicalFieldSuggestion` — `{ top: string | null; candidates: string[]; confidence: number; can_use_default: boolean }`
  - `CanonicalMapping` — `Record<string, CanonicalFieldSuggestion>`
  - `CanonicalColumnsBody` — `{ canonical_mapping: Record<string, string | null>; defaults_override?: Record<string, unknown> }`
  - Extended `ColumnOptions` — adds `canonical_suggestions?: CanonicalMapping`
  - Extended `InspectionResult` — adds `canonical_suggestions?: CanonicalMapping`
  - Extended `MetricRow` — adds `store: string | null`

- [ ] **Step 1: Add types after `ColumnOptions` (line 91)**

```typescript
// ── Canonical mapping (14-field schema) ──────────────────────────────────────
export interface CanonicalFieldSuggestion {
  top:             string | null
  candidates:      string[]
  confidence:      number
  can_use_default: boolean
}

export type CanonicalMapping = Record<string, CanonicalFieldSuggestion>

export interface CanonicalColumnsBody {
  canonical_mapping:  Record<string, string | null>
  defaults_override?: Record<string, unknown>
}
```

- [ ] **Step 2: Extend `ColumnOptions` (line 86)**

```typescript
export interface ColumnOptions {
  date_candidates:        string[]
  target_candidates:      string[]
  group_candidates:       string[]
  exog_candidates:        string[]
  canonical_suggestions?: CanonicalMapping   // NEW
}
```

- [ ] **Step 3: Extend `InspectionResult` (line 106)**

```typescript
export interface InspectionResult {
  profile:                DataProfile
  column_options:         ColumnOptions
  canonical_suggestions?: CanonicalMapping   // NEW (also nested in column_options)
  config_schema:          ConfigSchema | null
  inspected_at:           string
}
```

- [ ] **Step 4: Add `store` to `MetricRow` (line 216)**

```typescript
export interface MetricRow {
  model:      string
  type:       string
  sku:        string | null
  store:      string | null   // NEW
  mae:        number | null
  ...
}
```

- [ ] **Step 5: Run TypeScript check**

```bash
cd Frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add Frontend/src/lib/types.ts
git commit -m "feat: add CanonicalMapping, CanonicalColumnsBody types; extend MetricRow with store"
```

---

### Task 12: Frontend `api.ts` — update `chooseColumns`

**Files:**
- Modify: `Frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: `CanonicalColumnsBody` from Task 11
- Produces: `chooseColumns(id, body: CanonicalColumnsBody | ChooseColumnsBody)` — accepts both for transition period

- [ ] **Step 1: Update the import at line 3**

```typescript
import type {
  ...,
  ChooseColumnsBody,
  CanonicalColumnsBody,   // ADD
  ...
} from './types'
```

- [ ] **Step 2: Add overloaded `chooseColumnsCanonical` helper alongside existing `chooseColumns`**

After line 160:

```typescript
export const chooseColumnsCanonical = (id: string, body: CanonicalColumnsBody) =>
  request<{ ok: boolean }>('POST', `/sessions/${id}/configure/columns`, body)
```

- [ ] **Step 3: Run TypeScript check**

```bash
cd Frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add Frontend/src/lib/api.ts
git commit -m "feat: add chooseColumnsCanonical API helper for 14-field mapping"
```

---

### Task 13: Frontend Quick Start — 14-row canonical mapper

**Files:**
- Modify: `Frontend/src/app/quick-start/page.tsx`

**Interfaces:**
- Consumes: `chooseColumnsCanonical` (Task 12), `CanonicalMapping` from `inspection.canonical_suggestions`
- Produces: Step 2 replaced with 14-row mapper table

- [ ] **Step 1: Replace state variables**

Remove:
```typescript
const [dateCol,   setDateCol]   = useState('')
const [targetCol, setTargetCol] = useState('')
const [skuCol,    setSkuCol]    = useState<string>('__none__')
```

Add:
```typescript
const CANONICAL_FIELDS = [
  { name: 'sku',           label: 'SKU / Producto',     required: true  },
  { name: 'date',          label: 'Fecha',              required: true  },
  { name: 'demand',        label: 'Demanda',            required: true  },
  { name: 'store',         label: 'Tienda',             required: false, default: 'Tienda única' },
  { name: 'region',        label: 'Región',             required: false, default: 'Sin región' },
  { name: 'inventory',     label: 'Inventario',         required: false, default: '0' },
  { name: 'lead_time',     label: 'Lead Time (días)',   required: false, default: '7' },
  { name: 'price',         label: 'Precio',             required: false, default: 'Desconocido' },
  { name: 'cost',          label: 'Costo',              required: false, default: 'Desconocido' },
  { name: 'regular_price', label: 'Precio Regular',     required: false, default: 'Desconocido' },
  { name: 'promo_price',   label: 'Precio Promocional', required: false, default: '= Precio Regular' },
  { name: 'promo',         label: 'Promoción',          required: false, default: 'false' },
  { name: 'promo_type',    label: 'Tipo de Promoción',  required: false, default: 'Sin promoción' },
  { name: 'discount',      label: 'Descuento',          required: false, default: '0%' },
] as const

const [mapping, setMapping] = useState<Record<string, string | null>>(
  Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null]))
)
```

- [ ] **Step 2: Update `handleFile` pre-selection**

Replace the pre-select block (lines 259–262):

```typescript
const suggestions = insp.canonical_suggestions ?? {}
setMapping(prev => {
  const next = { ...prev }
  for (const field of CANONICAL_FIELDS) {
    const sug = suggestions[field.name]
    if (sug?.top && sug.confidence >= 0.7) {
      next[field.name] = sug.top
    }
  }
  return next
})
```

Remove `setDateCol`, `setTargetCol`, `setSkuCol` calls.

- [ ] **Step 3: Replace Step 2 JSX with 14-row mapper**

Replace the entire `{step === 2 && opts && ( ... )}` block with:

```tsx
{step === 2 && inspection && (
  <div>
    <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: '0 0 6px' }}>
      Confirma tus columnas
    </h2>
    <p style={{ fontSize: 14, color: 'var(--dim)', margin: '0 0 20px', lineHeight: 1.6 }}>
      Detectamos las siguientes columnas. Confirma cuál es cuál para los campos que necesitas.
    </p>

    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {CANONICAL_FIELDS.map(field => {
        const allCols = inspection.profile.columns.map(c => c.name)
        const val     = mapping[field.name]
        const isNone  = val === null

        return (
          <div key={field.name} style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr',
            alignItems: 'center', gap: 12,
            padding: '10px 0',
            borderBottom: '1px solid var(--border)',
          }}>
            <div>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                {field.required && <span style={{ color: '#ef4444', marginRight: 4 }}>★</span>}
                {field.label}
              </span>
              {!field.required && isNone && (
                <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>
                  Default: {(field as { default?: string }).default}
                </div>
              )}
            </div>
            <select
              value={val ?? '__none__'}
              onChange={e => {
                const v = e.target.value
                setMapping(prev => ({ ...prev, [field.name]: v === '__none__' ? null : v }))
              }}
              style={{
                padding: '8px 10px', borderRadius: 8,
                border: `1px solid ${field.required && !val ? '#ef4444' : 'var(--border)'}`,
                background: 'var(--surface)', color: 'var(--text)', fontSize: 13,
                cursor: 'pointer',
              }}
            >
              {!field.required && (
                <option value="__none__">No está en mi archivo</option>
              )}
              {field.required && !val && (
                <option value="__none__">— Selecciona una columna —</option>
              )}
              {allCols.map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
        )
      })}
    </div>

    <PreviewTable />

    {error && (
      <div style={{ marginTop: 16, padding: '10px 14px', background: '#fee2e2',
        borderRadius: 8, fontSize: 13, color: '#dc2626' }}>
        {error}
      </div>
    )}

    <button
      onClick={handleConfirm}
      disabled={busy || CANONICAL_FIELDS.filter(f => f.required).some(f => !mapping[f.name])}
      style={{
        marginTop: 28, width: '100%', padding: '14px 0',
        background: 'var(--accent)', color: '#fff',
        border: 'none', borderRadius: 10, fontSize: 15, fontWeight: 700,
        cursor: (busy || CANONICAL_FIELDS.filter(f => f.required).some(f => !mapping[f.name]))
          ? 'not-allowed' : 'pointer',
        opacity: busy ? 0.7 : 1,
        transition: 'opacity 0.15s',
      }}
    >
      {busy ? 'Procesando…' : 'Esto se ve bien, continuar →'}
    </button>
  </div>
)}
```

- [ ] **Step 4: Update `handleConfirm` to use new API**

Replace the `await chooseColumns(...)` call (lines 286–297):

```typescript
await chooseColumnsCanonical(sessionId, {
  canonical_mapping: mapping,
  defaults_override: {},
})
```

Update the import at line 7:

```typescript
import {
  createSession, uploadDataset, attachDataset, inspectSession,
  chooseColumnsCanonical,              // replaces chooseColumns
  setFeatures, setModels, setValidationConfig,
  setForecastConfig, setBusinessConfig, startTraining, getJob,
} from '@/lib/api'
```

- [ ] **Step 5: Update `handleRetry` to reset `mapping`**

Replace `setDateCol('')`, `setTargetCol('')`, `setSkuCol('__none__')` with:

```typescript
setMapping(Object.fromEntries(CANONICAL_FIELDS.map(f => [f.name, null])))
```

- [ ] **Step 6: Run TypeScript check**

```bash
cd Frontend
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 7: Run backend offline tests**

```bash
cd backend
python -m pytest tests/test_endpoints_offline.py -q --tb=short
```
Expected: 91 passed, 0 failed.

- [ ] **Step 8: Commit**

```bash
git add Frontend/src/app/quick-start/page.tsx Frontend/src/lib/api.ts
git commit -m "feat: Quick Start Step 2 replaced with 14-row canonical mapper"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| 14-field canonical schema with defaults | Task 1 |
| SKU column required, store auto-defaults | Task 1, 13 |
| `group_col → group_keys` throughout engine | Tasks 2, 4, 5, 6, 7, 8 |
| DataProfiler auto-detection of 14 fields | Task 3 |
| Bottom-up aggregation (by SKU + by Store) | Task 7 |
| Backend API accepts canonical mapping | Tasks 9, 10 |
| Frontend 14-row mapper | Task 13 |
| Frontend types | Task 11 |
| Dead library trees removed | Task 0 |
| Tests without mock data | Tasks 1, 3, 5, 7 |

### Placeholder scan

No TBD, no TODO. Every code block is complete.

### Type consistency

- `series_key(sku, store) → str` defined in Task 1, consumed in Task 5 via `from forecasting_core.data.canonical import series_key`.
- `CanonicalColumnsBody` defined in Task 11, used in Task 12 import and Task 13 call.
- `chooseColumnsCanonical` defined in Task 12, imported in Task 13.
- `PipelineResults.forecast_by_sku_df` defined in Task 8, no other tasks reference it yet (consumed by backend worker in a future phase).
- `group_keys` set on `ColumnsConfig` in Task 2; read by pipeline in Task 8 as `c.group_keys`.
