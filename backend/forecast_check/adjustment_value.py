"""Did a person's forecast adjustments beat the model? (Forecast value added.)

Orchestration only, like `service.py` next to it: it finds the sales that arrived
after the adjusted periods, lines each adjusted forecast up against the
UNADJUSTED forecast of the same series and period, and hands the points to
ForecastingCore (`evaluation.realized.forecast_value_added*`) for the maths. The
adjusted value is `forecast * (1 + pct/100)`; the same percentage the planner
applied to the purchase recommendation.

Only the CURRENT adjustment of a period is graded (a superseded one was replaced
by its author's later word), and a 0% entry, which only clears a period, is not a
bet and is left out.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.dataframes.actuals import load_actual_series
from backend.datasets.service import get_dataset
from backend.forecast_check import service as fc_service
from backend.inventory import forecast_adjustment_service as adj_svc
from backend.inventory.series import split_key
from backend.workers.runner import build_engine_config

log = logging.getLogger(__name__)


def build_points(adjustments: list[dict], forecasts: dict[str, dict[str, float]],
                 actuals: dict[str, dict[str, float]]) -> list[dict]:
    """One point per (adjustment, series, period) inside the adjustment's dates
    where both a forecast and a real sale exist. Pure; no I/O."""
    points: list[dict] = []
    for adj in adjustments:
        lo, hi = str(adj["start_date"])[:10], str(adj["end_date"])[:10]
        factor = 1.0 + float(adj["pct"]) / 100.0
        for key, series in forecasts.items():
            if split_key(key)[0] != adj["sku"]:
                continue
            real = actuals.get(key) or {}
            for day, base in series.items():
                if not (lo <= day <= hi) or day not in real or base is None:
                    continue
                points.append({
                    "base": float(base), "adjusted": float(base) * factor,
                    "actual": float(real[day]), "sku": adj["sku"], "date": day,
                    "adjustment_id": adj["id"], "user": adj["created_by"],
                    "reason": adj["reason_code"],
                })
    return points


def _pick_actuals(tenant_id: str, session: dict, cols: dict, target_freq,
                  forecasts: dict, adjustments: list[dict],
                  dataset_id: Optional[str]) -> tuple[Optional[dict], Optional[dict], str]:
    """(dataset, loaded actuals, reason). The dataset with the most graded points
    wins when none is named."""
    from backend.forecast_check.service import _candidates, _rank_for_auto_pick

    if dataset_id:
        ds = get_dataset(tenant_id, dataset_id)
        to_try = [ds] if ds else []
        if not to_try:
            return None, None, "dataset_not_found"
    else:
        dates = [d for s in forecasts.values() for d in s]
        candidates = _candidates(tenant_id, session, cols["date"],
                                 min(dates) if dates else None, max(dates) if dates else None)
        ranked = _rank_for_auto_pick(candidates, session.get("backtest_source_dataset_id"))
        to_try = [d for d in (get_dataset(tenant_id, c["dataset_id"])
                              for c in ranked[:fc_service.MAX_CANDIDATES_TRIED]) if d]
        if not to_try:
            return None, None, "no_data_yet"

    group_cols = fc_service.grading_group_cols(tenant_id, session["id"], cols)
    best, best_n, reason = None, 0, "no_data_yet"
    for ds in to_try:
        try:
            loaded = load_actual_series(ds["file_path"], cols["date"], cols["target"],
                                        group_cols, target_freq)
        except Exception as exc:  # an unreadable file is a reason, not a 500
            log.warning("adjustment value: could not read dataset %s: %s", ds["id"], exc)
            reason = "unreadable"
            continue
        if "error" in loaded:
            reason = loaded["error"]
            continue
        n = len(build_points(adjustments, forecasts, loaded["series"]))
        if n > best_n:
            best, best_n = (ds, loaded), n
    if best is None:
        return None, None, reason
    return best[0], best[1], "ok"


def value_added(tenant_id: str, session: dict, dataset_id: Optional[str] = None) -> dict:
    """The forecast-value-added reading of one session's adjustments.

    ``status``: ok | no_adjustments | no_forecast | no_data_yet | dataset_not_found
    | unreadable | columns_missing ... (the loader's own reasons pass through).
    """
    from forecasting_core.evaluation.realized import (
        forecast_value_added, forecast_value_added_by,
    )

    session_id = session["id"]
    adjustments = [a for a in adj_svc.list_for_session(tenant_id, session_id)
                   if float(a["pct"]) != 0.0]
    base = {"session_id": session_id, "n_adjustments": len(adjustments), "source": None,
            "aggregate": None, "by_user": [], "by_reason": []}
    if not adjustments:
        return {**base, "status": "no_adjustments"}
    forecasts = fc_service._champion_forecasts(tenant_id, session_id)
    if not forecasts:
        return {**base, "status": "no_forecast"}

    cfg = build_engine_config(tenant_id, session_id)
    ds, loaded, reason = _pick_actuals(
        tenant_id, session, cfg["columns"], (cfg.get("granularity") or {}).get("target_freq"),
        forecasts, adjustments, dataset_id)
    if ds is None:
        return {**base, "status": reason}

    points = build_points(adjustments, forecasts, loaded["series"])
    if not points:
        return {**base, "status": "no_data_yet"}

    names = {a["created_by"]: a.get("created_by_name") for a in adjustments}
    by_user = forecast_value_added_by(points, "user")
    for row in by_user:
        row["name"] = names.get(row["user"])
    graded = {p["adjustment_id"] for p in points}
    return {
        **base, "status": "ok",
        "source": {"dataset_id": ds["id"], "name": ds["name"]},
        "n_adjustments_graded": len(graded),
        "aggregate": forecast_value_added(points),
        "by_user": by_user,
        "by_reason": forecast_value_added_by(points, "reason"),
    }
