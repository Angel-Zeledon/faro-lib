"""Benchmark CLI.

    cd ForecastingCore
    python -m benchmarks.run --dataset synthetic --max-series 100
    python -m benchmarks.run --dataset m4_monthly --max-series 100 --origins 2
    python -m benchmarks.run --dataset csv:path/to/canonical.csv

Rolling-origin holdout: the last ``origins * horizon`` periods of every series
are held out in ``origins`` consecutive blocks. At each origin the engine is
trained ONLY on data before it (so no leakage) and its forecast for the next
``horizon`` periods is scored against what really happened.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from . import report
from .baselines import BASELINES
from .datasets import Dataset, DatasetUnavailable, load_dataset, synthetic_retail
from .engine_runner import DEFAULT_MODELS, run_engine
from .metrics import classify_segment, score_forecast

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def lower_priority() -> None:
    """Run niced: this is a measurement, not a service."""
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL
        else:
            os.nice(10)
    except Exception:
        pass


def _series_arrays(df: pd.DataFrame) -> dict:
    return {str(sku): g.sort_values("date") for sku, g in df.groupby("sku")}


def origin_cuts(ds: Dataset, origins: int) -> List[pd.Timestamp]:
    """Last training date of each origin, oldest first."""
    grid = np.sort(ds.df["date"].unique())
    h = ds.horizon
    return [pd.Timestamp(grid[len(grid) - 1 - h * (origins - k)]) for k in range(origins)]


def evaluate(ds: Dataset, origins: int, models: List[str], chunk: int,
             budget_s: Optional[float], log=print) -> dict:
    t0 = time.time()
    h, season = ds.horizon, ds.season
    cuts = origin_cuts(ds, origins)
    by_sku = _series_arrays(ds.df)
    skus = sorted(by_sku)
    truth = None
    if ds.truth is not None:
        truth = {str(s): g.sort_values("date") for s, g in ds.truth.groupby("sku")}
    records: List[dict] = []
    engine_errors: List[str] = []
    done_skus = 0
    stopped_early = False

    for start in range(0, len(skus), chunk):
        if budget_s is not None and time.time() - t0 > budget_s:
            stopped_early = True
            break
        batch = skus[start:start + chunk]
        for oi, cut in enumerate(cuts):
            train_parts, hold = [], {}
            for s in batch:
                g = by_sku[s]
                tr = g[g["date"] <= cut]
                te = g[g["date"] > cut].head(h)
                if len(tr) < 24 or len(te) < h:
                    continue
                train_parts.append(tr[["sku", "date", "demand"]])
                hold[s] = (tr["demand"].to_numpy(), te["demand"].to_numpy(), te["date"].to_numpy())
            if not hold:
                continue
            res = run_engine(pd.concat(train_parts, ignore_index=True), h, season, models)
            if res["error"]:
                engine_errors.append(res["error"])
            for s, (hist, y, te_dates) in hold.items():
                rec = {"series": s, "origin": oi, "origin_end": str(cut.date()),
                       "segment": classify_segment(hist), "methods": {},
                       "type": ds.series_type.get(s)}
                for name, fn in BASELINES.items():
                    rec["methods"][name] = score_forecast(
                        y, fn(hist, h, season), hist, season)
                if truth is not None and s in truth:
                    tm = truth[s]
                    tm = tm[tm["date"].isin(te_dates)]["true_mean"].to_numpy()
                    if len(tm) == h:
                        rec["methods"]["oracle_true_mean"] = score_forecast(y, tm, hist, season)
                fs = res["forecasts"].get(s)
                champ = res["champion"].get(s)
                if fs and champ:
                    c = fs[champ]
                    rec["champion"] = champ
                    rec["methods"]["engine"] = score_forecast(
                        y, c["point"], hist, season, quantiles=c["q"] or None)
                    for mname, mv in fs.items():
                        rec["methods"][f"model:{mname}"] = score_forecast(
                            y, mv["point"], hist, season)
                else:
                    rec["engine_failed"] = True
                records.append(rec)
        done_skus = min(len(skus), start + chunk)
        log(f"  {done_skus}/{len(skus)} series done, {time.time() - t0:.0f}s elapsed")

    n_failed = sum(1 for r in records if r.get("engine_failed"))
    agg = report.aggregate(records)
    mix = report.champion_mix(records)
    meta = {
        "n_series": done_skus, "n_series_requested": len(skus), "n_origins": origins,
        "horizon": h, "season": season, "freq": ds.freq,
        "n_scored": sum(1 for r in records if "engine" in r["methods"]),
        "n_engine_failed": n_failed, "stopped_early_for_budget": stopped_early,
        "engine_errors": sorted(set(engine_errors))[:5],
        "models_requested": models, "runtime_s": round(time.time() - t0, 1),
    }
    return {"meta": meta, "aggregate": agg, "champion_mix": mix, "records": records}


def _versions() -> dict:
    out = {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__}
    for mod in ("lightgbm", "xgboost", "statsmodels"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:
            out[mod] = None
    try:
        import forecasting_core
        out["forecasting_core"] = getattr(forecasting_core, "__version__", "dev")
    except Exception:
        pass
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="StockAI engine accuracy benchmark")
    ap.add_argument("--dataset", default="synthetic",
                    help="synthetic | m4_monthly | m4_weekly | csv:<path> (canonical schema)")
    ap.add_argument("--max-series", type=int, default=100)
    ap.add_argument("--origins", type=int, default=2, help="rolling origins")
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--chunk", type=int, default=25, help="series per engine run")
    ap.add_argument("--time-budget-min", type=float, default=15.0)
    ap.add_argument("--out", default=None, help="JSON output path")
    ap.add_argument("--no-records", action="store_true", help="omit per-series records from JSON")
    args = ap.parse_args(argv)

    if args.max_series > 300:
        print("note: capping --max-series at 300 (resource rule)")
        args.max_series = 300
    lower_priority()

    fallback = None
    kw = {"horizon": args.horizon} if args.dataset.startswith("csv:") and args.horizon else {}
    try:
        ds = load_dataset(args.dataset, args.max_series, args.seed, **kw)
    except DatasetUnavailable as exc:
        fallback = f"{args.dataset} unavailable ({exc}); FELL BACK TO synthetic"
        print("WARNING:", fallback)
        ds = synthetic_retail(max_series=args.max_series, seed=args.seed)
    if args.horizon and not args.dataset.startswith("csv:"):
        ds.horizon = args.horizon

    models = [m for m in args.models.split(",") if m]
    print(f"dataset={ds.name} series={ds.n_series} freq={ds.freq} h={ds.horizon} "
          f"season={ds.season} models={models}")
    result = evaluate(ds, args.origins, models, args.chunk, args.time_budget_min * 60)
    result["dataset"] = {"name": ds.name, "source": ds.source, "seed": args.seed,
                         "requested": args.dataset, "fallback": fallback, "notes": ds.notes}
    result["versions"] = _versions()
    result["generated_at"] = datetime.now(timezone.utc).isoformat()

    label = f"{ds.name} ({ds.source})"
    md = report.to_markdown(label, result["aggregate"], result["champion_mix"], result["meta"])
    print(md)
    RESULTS_DIR.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else RESULTS_DIR / f"{ds.name.replace(':', '_')}_{args.seed}.json"
    payload = dict(result)
    if args.no_records:
        payload.pop("records")
    out.write_text(json.dumps(payload, indent=1, default=lambda o: None if (
        isinstance(o, float) and not math.isfinite(o)) else str(o)), encoding="utf-8")
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
