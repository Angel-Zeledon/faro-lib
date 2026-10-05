"""Automatic realised-accuracy tracking: is the live forecast still holding up?

When a sales file lands, every active session whose forecast window it reaches
is graded against what really sold, and the LATEST reading is kept (one row per
session in `session_accuracy_tracking`). The reading sits next to the accuracy
the forecast had at training time, and one in-app alert is raised when it has
degraded past the threshold.

A notification, never an action: nothing here retrains, archives or replaces a
session. Whether to retrain is the buyer's call.

Why it is cheap enough to run on every upload:

* bounded: at most `MAX_SESSIONS_PER_UPLOAD` sessions (the newest completed,
  non-archived ones) and ONE file read per session;
* only the sales that landed in the forecast window are compared, and only
  periods that are fully covered by the file (`load_actual_series` drops a
  bucket the file only partly reaches, so a half-finished month never looks
  like a forecast that ran 50% high);
* page views read the stored row; nothing is recomputed on a GET.

The metric maths lives in ForecastingCore (`evaluation.realized`); this module
only lines the data up, stores the reading and decides when to speak.

Idempotency: the reading is an upsert, and the alert is guarded by a latch
(`alerted_at`) claimed with a single conditional UPDATE, so re-processing the
same upload - or two uploads racing - raises at most one alert per degradation
episode. The latch is cleared when the forecast recovers, so a later relapse
alerts again.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from backend.dataframes.actuals import load_actual_series
from backend.datasets.service import get_dataset
from backend.db import session_store
from backend.db.connection import execute, query, query_one
from backend.forecast_check.service import _champion_forecasts
from backend.service_config.resolver import effective
from backend.workers.runner import build_engine_config

log = logging.getLogger(__name__)

# Newest completed sessions graded per upload. An older session is graded the
# next time it is the newest one a file reaches.
MAX_SESSIONS_PER_UPLOAD = 5

SYSTEM_ACTOR = "system"


def _threshold_pct(tenant_id: str) -> float:
    try:
        return float(effective(tenant_id).accuracy_degradation_threshold_pct)
    except Exception:  # noqa: BLE001 - a bad value must not stop the tracker
        from forecasting_core.evaluation.realized import DEGRADATION_THRESHOLD_PCT
        return DEGRADATION_THRESHOLD_PCT


def _training_baseline(tenant_id: str, session_id: str, volume_by_key: dict) -> Optional[float]:
    """The session's training-time WAPE, restated over the series that sold."""
    from backend.inventory.service import best_model_by_sku
    from forecasting_core.evaluation.realized import baseline_wape, training_wape_by_series

    result = session_store.get_training_result(tenant_id, session_id) or {}
    rows = (result.get("metrics") or {}).get("rows") or []
    if not rows:
        return None
    train = training_wape_by_series(rows, best_model_by_sku(rows))
    return baseline_wape(train, volume_by_key)


def get_tracking(tenant_id: str, session_id: str) -> Optional[dict]:
    """The stored reading for a session, or None when none has been made."""
    row = query_one(
        """SELECT session_id, dataset_id, status, baseline_wape, realised_wape,
                  degradation_pct, bias, threshold_pct, n_points, n_skus,
                  compared_from::text AS compared_from, compared_to::text AS compared_to,
                  alerted_at, computed_at
           FROM session_accuracy_tracking WHERE tenant_id = %s AND session_id = %s""",
        (tenant_id, session_id),
    )
    if not row:
        return None
    out = dict(row)
    for key in ("alerted_at", "computed_at"):
        out[key] = out[key].isoformat() if out.get(key) else None
    return out


def _claim_alert(tenant_id: str, session_id: str) -> bool:
    """Take the once-per-episode latch. True for exactly one caller."""
    row = query_one(
        """UPDATE session_accuracy_tracking SET alerted_at = NOW()
           WHERE tenant_id = %s AND session_id = %s AND alerted_at IS NULL
           RETURNING session_id""",
        (tenant_id, session_id),
    )
    return row is not None


def track_session(tenant_id: str, session: dict, dataset: dict) -> Optional[dict]:
    """Grade one session's forecast against one dataset and store the reading.

    Returns the stored reading, or None when there is nothing to grade (no
    forecast, a file that does not reach the forecast window, an unreadable
    file) or when an equally recent or more recent reading already exists.
    """
    from forecasting_core.evaluation.realized import (
        assess_degradation, compare_forecast_to_actuals,
    )

    session_id = session["id"]
    forecasts = _champion_forecasts(tenant_id, session_id)
    if not forecasts or not dataset.get("file_path"):
        return None
    forecast_dates = {d for series in forecasts.values() for d in series}
    cfg = build_engine_config(tenant_id, session_id)
    cols = cfg["columns"]
    loaded = load_actual_series(
        dataset["file_path"], cols["date"], cols["target"],
        list(cols["group_keys"]), (cfg.get("granularity") or {}).get("target_freq"))
    if "error" in loaded:
        return None
    if not any(d in (loaded["series"].get(sku) or {})
               for sku, fc in forecasts.items() for d in fc):
        return None   # the file does not reach the forecast window

    compared = compare_forecast_to_actuals(forecasts, loaded["series"])
    agg = compared["aggregate"]
    if not agg["n_points"] or agg["wape"] is None:
        return None
    volume = {r["sku"]: r["total_actual"] for r in compared["skus"]}
    assessment = assess_degradation(
        _training_baseline(tenant_id, session_id, volume), agg["wape"],
        agg["n_points"], threshold_pct=_threshold_pct(tenant_id))
    dates = [p["date"] for p in agg["series"]]
    compared_from, compared_to = min(dates), max(dates)

    previous = get_tracking(tenant_id, session_id)
    if previous and previous["compared_to"] and previous["compared_to"] > compared_to:
        return None   # an older file must not overwrite a reading of newer sales

    execute(
        """INSERT INTO session_accuracy_tracking
               (session_id, tenant_id, dataset_id, status, baseline_wape, realised_wape,
                degradation_pct, bias, threshold_pct, n_points, n_skus,
                compared_from, compared_to, computed_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
           ON CONFLICT (session_id) DO UPDATE SET
               dataset_id = EXCLUDED.dataset_id, status = EXCLUDED.status,
               baseline_wape = EXCLUDED.baseline_wape,
               realised_wape = EXCLUDED.realised_wape,
               degradation_pct = EXCLUDED.degradation_pct, bias = EXCLUDED.bias,
               threshold_pct = EXCLUDED.threshold_pct, n_points = EXCLUDED.n_points,
               n_skus = EXCLUDED.n_skus, compared_from = EXCLUDED.compared_from,
               compared_to = EXCLUDED.compared_to, computed_at = NOW(),
               -- recovered: a later relapse is a new episode and may alert again
               alerted_at = CASE WHEN EXCLUDED.status = 'stable' THEN NULL
                                 ELSE session_accuracy_tracking.alerted_at END""",
        (session_id, tenant_id, dataset["id"], assessment["status"],
         assessment["baseline_wape"], assessment["realised_wape"],
         assessment["degradation_pct"], agg["bias"], assessment["threshold_pct"],
         agg["n_points"], agg["n_skus"], compared_from, compared_to),
    )

    if assessment["status"] == "degraded" and _claim_alert(tenant_id, session_id):
        _raise_alert(tenant_id, session, assessment)
    return get_tracking(tenant_id, session_id)


def _raise_alert(tenant_id: str, session: dict, assessment: dict) -> None:
    """The one in-app (bell) alert for this degradation episode."""
    from backend.activity.events import record_event

    record_event(
        tenant_id, SYSTEM_ACTOR, "forecast.accuracy_degraded",
        resource=session["id"],
        details={
            "session_id": session["id"],
            "session_name": session.get("name") or session["id"],
            "degradation_pct": round(float(assessment["degradation_pct"])),
        },
        reason="realised_accuracy_below_training",
        reason_params={
            "baseline": round(float(assessment["baseline_wape"]) * 100),
            "realised": round(float(assessment["realised_wape"]) * 100),
        },
    )


def track_dataset(tenant_id: str, dataset_id: str) -> list[dict]:
    """Grade the tenant's newest completed sessions against a freshly arrived
    sales file. Never raises: a failed reading is logged and skipped, because
    this runs behind an upload that has already succeeded."""
    dataset = get_dataset(tenant_id, dataset_id)
    if not dataset:
        return []
    sessions = query(
        """SELECT id, name FROM sessions
           WHERE tenant_id = %s AND status = 'COMPLETED'
             AND archived_at IS NULL AND NOT is_backtest
           ORDER BY updated_at DESC LIMIT %s""",
        (tenant_id, MAX_SESSIONS_PER_UPLOAD),
    )
    readings = []
    for s in sessions:
        try:
            reading = track_session(tenant_id, dict(s), dataset)
        except Exception:  # noqa: BLE001 - see the docstring
            log.exception("accuracy tracking failed for session=%s dataset=%s",
                          s["id"], dataset_id)
            continue
        if reading:
            readings.append(reading)
    return readings


def _spawn(fn) -> None:
    threading.Thread(target=fn, daemon=True, name="accuracy-tracking").start()


def schedule_tracking(tenant_id: str, dataset_id: str) -> None:
    """Fire-and-forget hook for every path that lands a sales file. The upload
    response never waits on it and never fails because of it."""
    def _run() -> None:
        try:
            track_dataset(tenant_id, dataset_id)
        except Exception:  # noqa: BLE001
            log.exception("accuracy tracking crashed for dataset=%s", dataset_id)

    try:
        _spawn(_run)
    except Exception:  # noqa: BLE001
        log.exception("could not start accuracy tracking for dataset=%s", dataset_id)
