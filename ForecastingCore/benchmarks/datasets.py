"""Dataset adapters for the benchmark.

Every adapter returns a ``Dataset`` whose ``df`` is in the product's canonical
long schema: ``sku``, ``date``, ``demand`` (one row per series per period, no
gaps inside a series' own span). All adapters are deterministic for a given
``(max_series, seed)``.

* ``synthetic``  - seeded retail generator with known ground truth.
* ``m4_monthly`` / ``m4_weekly`` - public M4 competition series, downloaded on
  demand from the Mcompetitions/M4-methods GitHub raw files and cached under
  ``benchmarks/.cache`` (gitignored). When the network is unavailable the
  adapter raises ``DatasetUnavailable``; the CLI then falls back to
  ``synthetic`` and says so in the report.
* ``csv:<path>`` - any CSV already in the canonical schema.
"""

from __future__ import annotations

import io
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent / ".cache"

M4_BASE = "https://raw.githubusercontent.com/Mcompetitions/M4-methods/master/Dataset"

SEASON_BY_FREQ = {"D": 7, "W": 52, "M": 12, "MS": 12, "ME": 12, "Q": 4, "QS": 4}


class DatasetUnavailable(RuntimeError):
    """The dataset could not be obtained (typically: no network)."""


@dataclass
class Dataset:
    name: str
    freq: str                      # pandas offset alias
    season: int
    horizon: int
    df: pd.DataFrame               # sku, date, demand
    source: str                    # human-readable provenance
    truth: Optional[pd.DataFrame] = None   # sku, date, true_mean (synthetic only)
    series_type: dict = field(default_factory=dict)  # sku -> generator type
    notes: list = field(default_factory=list)

    @property
    def n_series(self) -> int:
        return int(self.df["sku"].nunique())


# --------------------------------------------------------------------------
# Synthetic retail generator
# --------------------------------------------------------------------------

SYNTH_TYPES = ("smooth", "seasonal", "erratic", "intermittent", "lumpy", "promo")


def _synth_series(kind: str, n: int, rng: np.random.Generator) -> tuple:
    """Return (observed, true_mean) arrays of length n for one series."""
    t = np.arange(n)
    if kind == "smooth":
        level = rng.uniform(50, 200)
        slope = rng.uniform(-0.001, 0.003)
        mean = level * (1 + slope * t)
        y = mean + rng.normal(0, 0.10 * level, n)
    elif kind == "seasonal":
        level = rng.uniform(50, 200)
        amp = rng.uniform(0.2, 0.5)
        phase = rng.uniform(0, 2 * np.pi)
        mean = level * (1 + amp * np.sin(2 * np.pi * t / 52.0 + phase))
        y = mean + rng.normal(0, 0.12 * level, n)
    elif kind == "erratic":
        p = rng.uniform(0.85, 0.97)
        sigma = rng.uniform(0.8, 1.1)
        mu_log = rng.uniform(2.5, 4.0)
        occurs = rng.random(n) < p
        size = np.maximum(1, np.round(rng.lognormal(mu_log, sigma, n)))
        y = np.where(occurs, size, 0.0)
        mean = np.full(n, p * np.exp(mu_log + sigma ** 2 / 2))
    elif kind == "intermittent":
        p = rng.uniform(0.15, 0.40)
        mu = rng.uniform(3, 8)
        occurs = rng.random(n) < p
        size = np.maximum(1, np.round(rng.normal(mu, 0.25 * mu, n)))
        y = np.where(occurs, size, 0.0)
        mean = np.full(n, p * mu)
    elif kind == "lumpy":
        p = rng.uniform(0.10, 0.30)
        sigma = rng.uniform(0.8, 1.2)
        mu_log = rng.uniform(1.5, 3.0)
        occurs = rng.random(n) < p
        size = np.maximum(1, np.round(rng.lognormal(mu_log, sigma, n)))
        y = np.where(occurs, size, 0.0)
        mean = np.full(n, p * np.exp(mu_log + sigma ** 2 / 2))
    elif kind == "promo":
        base = rng.uniform(40, 120)
        mean = np.full(n, base, dtype=float)
        pos = int(rng.integers(3, 8))
        while pos < n:
            mean[pos] *= rng.uniform(1.8, 3.0)
            pos += int(rng.integers(6, 11))
        y = mean + rng.normal(0, 0.10 * base, n)
    else:  # pragma: no cover
        raise ValueError(kind)
    return np.maximum(0.0, y), mean


def synthetic_retail(max_series: int = 100, seed: int = 42, n_periods: int = 156,
                     horizon: int = 8) -> Dataset:
    """Deterministic weekly retail generator. Types cycle evenly, so any
    ``max_series`` gets a balanced mix; the segment (ADI/CV2) is *measured*
    later on each training window, not assumed from the generator type."""
    end = pd.Timestamp("2024-12-30")  # a Monday
    dates = pd.date_range(end=end, periods=n_periods, freq="W-MON")
    rows, truth, types = [], [], {}
    for i in range(max_series):
        kind = SYNTH_TYPES[i % len(SYNTH_TYPES)]
        rng = np.random.default_rng([seed, i])
        y, mean = _synth_series(kind, n_periods, rng)
        sku = f"S{i:04d}"
        types[sku] = kind
        rows.append(pd.DataFrame({"sku": sku, "date": dates, "demand": y}))
        truth.append(pd.DataFrame({"sku": sku, "date": dates, "true_mean": mean}))
    return Dataset(
        name="synthetic",
        freq="W-MON", season=52, horizon=horizon,
        df=pd.concat(rows, ignore_index=True),
        truth=pd.concat(truth, ignore_index=True),
        series_type=types,
        source=f"synthetic retail generator (seed={seed}, {max_series} series, "
               f"{n_periods} weekly periods, types={'/'.join(SYNTH_TYPES)})",
    )


# --------------------------------------------------------------------------
# M4
# --------------------------------------------------------------------------

_M4 = {
    # name: (file stem, pandas freq, season, official horizon, tail cap, start)
    "m4_monthly": ("Monthly", "MS", 12, 18, 144, "2015-01-01"),
    "m4_weekly": ("Weekly", "W-MON", 52, 13, 260, "2015-01-05"),
}


def _download(url: str, cache_name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / cache_name
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
    except Exception as exc:  # network down, DNS, 404 ...
        raise DatasetUnavailable(f"could not download {url}: {exc}") from exc
    if not data:
        raise DatasetUnavailable(f"empty download from {url}")
    path.write_bytes(data)
    return path


def _read_m4_rows(path: Path) -> dict:
    """{id: np.ndarray} from an M4 wide CSV (id then values, NaN padded)."""
    wide = pd.read_csv(path, low_memory=False)
    ids = wide.iloc[:, 0].astype(str).to_numpy()
    values = wide.iloc[:, 1:].to_numpy(dtype=float)
    out = {}
    for sid, row in zip(ids, values):
        row = row[~np.isnan(row)]
        out[sid] = row
    return out


def m4(name: str, max_series: int = 100, seed: int = 42) -> Dataset:
    stem, freq, season, h, cap, start = _M4[name]
    train = _read_m4_rows(_download(f"{M4_BASE}/Train/{stem}-train.csv", f"{stem}-train.csv"))
    test = _read_m4_rows(_download(f"{M4_BASE}/Test/{stem}-test.csv", f"{stem}-test.csv"))
    min_len = 3 * h + 24
    eligible = sorted(
        sid for sid in train
        if sid in test and len(train[sid]) + len(test[sid]) >= min_len
        and np.all(np.concatenate([train[sid], test[sid]]) > 0)
    )
    rng = np.random.default_rng(seed)
    pick = sorted(rng.choice(len(eligible), size=min(max_series, len(eligible)),
                             replace=False).tolist())
    chosen = [eligible[i] for i in pick]
    end = pd.Timestamp(start) + pd.tseries.frequencies.to_offset(freq) * (cap - 1)
    rows = []
    for sid in chosen:
        full = np.concatenate([train[sid], test[sid]])[-cap:]
        d = pd.date_range(end=end, periods=len(full), freq=freq)
        rows.append(pd.DataFrame({"sku": sid, "date": d, "demand": full}))
    return Dataset(
        name=name, freq=freq, season=season, horizon=h,
        df=pd.concat(rows, ignore_index=True),
        source=(f"M4 {stem} (Mcompetitions/M4-methods), seeded sample of {len(chosen)} "
                f"of {len(eligible)} eligible series (seed={seed}), last <= {cap} "
                f"observations of train+test; dates are synthetic (M4 ships none)"),
        notes=["M4 series are strictly positive and smooth: no intermittent "
               "segment exists in this dataset by construction."],
    )


# --------------------------------------------------------------------------
# CSV in the canonical schema
# --------------------------------------------------------------------------

def csv_dataset(path: str, max_series: int = 100, seed: int = 42,
                horizon: Optional[int] = None, freq: Optional[str] = None,
                season: Optional[int] = None) -> Dataset:
    raw = pd.read_csv(path)
    missing = {"sku", "date", "demand"} - set(raw.columns)
    if missing:
        raise ValueError(f"CSV is not in the canonical schema; missing {sorted(missing)}")
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.groupby(["sku", "date"], as_index=False)["demand"].sum()
    skus = sorted(raw["sku"].astype(str).unique())
    rng = np.random.default_rng(seed)
    if len(skus) > max_series:
        skus = sorted(rng.choice(skus, size=max_series, replace=False).tolist())
    raw["sku"] = raw["sku"].astype(str)
    raw = raw[raw["sku"].isin(skus)]
    if freq is None:
        first = raw[raw["sku"] == skus[0]].sort_values("date")["date"]
        freq = pd.infer_freq(first) or "D"
    base = freq.split("-")[0]
    season = season or SEASON_BY_FREQ.get(base, 1)
    horizon = horizon or {"D": 14, "W": 8, "M": 12, "MS": 12, "ME": 12}.get(base, 8)
    pieces = []
    for sku, g in raw.groupby("sku"):
        g = g.sort_values("date").set_index("date")["demand"]
        full = pd.date_range(g.index.min(), g.index.max(), freq=freq)
        g = g.reindex(full).fillna(0.0)     # a missing period in a sales file is a zero
        pieces.append(pd.DataFrame({"sku": sku, "date": full, "demand": g.to_numpy()}))
    return Dataset(
        name=f"csv:{Path(path).name}", freq=freq, season=season, horizon=horizon,
        df=pd.concat(pieces, ignore_index=True),
        source=f"CSV {path}, {len(skus)} series, freq={freq}",
        notes=["Missing periods inside a series' span were filled with 0."],
    )


def load_dataset(spec: str, max_series: int, seed: int, **kw) -> Dataset:
    if spec == "synthetic":
        return synthetic_retail(max_series=max_series, seed=seed)
    if spec in _M4:
        return m4(spec, max_series=max_series, seed=seed)
    if spec.startswith("csv:"):
        return csv_dataset(spec[4:], max_series=max_series, seed=seed, **kw)
    raise ValueError(f"unknown dataset {spec!r}; use synthetic, m4_monthly, m4_weekly or csv:<path>")
