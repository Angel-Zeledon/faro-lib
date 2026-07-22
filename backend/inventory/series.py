"""
Store-aware forecast key helpers (feature 5.4).

Session forecasts are stored as {key: {model: {historical, forecast}}}. For
single-store sessions the key is the bare SKU (unchanged legacy shape); when
the sales history has a mapped store column the key is "sku│store" — the same
series key forecasting_core builds (see forecasting_core/data/canonical.py
series_key()). The separator is re-declared here because backend/ must not
import forecasting_core outside workers/runner.py (layer rule in CLAUDE.md).
"""

from __future__ import annotations

# Mirror of forecasting_core.data.canonical._SEPARATOR — keep in sync.
SERIES_SEPARATOR = "│"


def split_key(key: str) -> tuple[str, str | None]:
    """'sku│store' -> (sku, store); bare 'sku' -> (sku, None)."""
    if SERIES_SEPARATOR in key:
        sku, store = key.split(SERIES_SEPARATOR, 1)
        return sku, store
    return key, None


def stores_in(forecasts: dict) -> set[str]:
    """Distinct store names present in a forecasts dict (empty for legacy keys)."""
    out: set[str] = set()
    for key in forecasts:
        _, store = split_key(key)
        if store is not None:
            out.add(store)
    return out


def for_store(forecasts: dict, store: str) -> dict:
    """Subset of a store-keyed forecasts dict for one store, re-keyed by bare SKU."""
    out: dict = {}
    for key, models in forecasts.items():
        sku, key_store = split_key(key)
        if key_store == store:
            out[sku] = models
    return out


def rollup_by_sku(forecasts: dict) -> dict:
    """
    Collapse store-keyed forecasts to per-SKU by summing forecast values (and
    lower/upper bands when every store has them) date-by-date per model.
    Legacy (bare-SKU) dicts pass through unchanged. Historical series are taken
    from the first store seen — SKU-level consumers use them for charting, and
    summing histories would double-count dates missing in some stores.
    """
    if not any(SERIES_SEPARATOR in k for k in forecasts):
        return forecasts

    out: dict = {}
    for key, models in forecasts.items():
        sku, _ = split_key(key)
        if sku not in out:
            # Deep-enough copy: new dicts/lists so summing never mutates input.
            out[sku] = {
                m: {
                    "historical": list(entry.get("historical") or []),
                    "forecast": [dict(p) for p in entry.get("forecast") or []],
                }
                for m, entry in models.items()
            }
            continue
        for model, entry in models.items():
            base = out[sku].setdefault(
                model, {"historical": [], "forecast": []}
            )
            by_date = {p["date"]: p for p in base["forecast"]}
            for p in entry.get("forecast") or []:
                tgt = by_date.get(p["date"])
                if tgt is None:
                    base["forecast"].append(dict(p))
                    by_date[p["date"]] = base["forecast"][-1]
                    continue
                tgt["value"] = round(float(tgt["value"]) + float(p["value"]), 4)
                for band in ("lower", "upper"):
                    if tgt.get(band) is not None and p.get(band) is not None:
                        tgt[band] = round(float(tgt[band]) + float(p[band]), 4)
                    else:
                        tgt[band] = None
    return out
