"""The evidence the consensus accuracy report is graded on.

The S&OP consensus routes are served by Rust (`backend-rs/src/routes/consensus.rs`)
and Rust reads no dataset files. What a forecast was worth is only known once real
sales exist, and those sit in the dataset files that this Python service reads. So
this module does the one thing only Python can: for every SKU and period any
current consensus submission (or the published consensus) touches, it records the
UNADJUSTED statistical forecast next to what really sold, in `consensus_evidence`.
Rust then grades each function, each reason and the consensus itself against it.

Nothing here retrains or changes the forecast; it copies two numbers per SKU and
period. A refresh replaces the session's previous evidence in one transaction, so
a reader never sees half of one.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from backend.db.connection import query, transaction
from backend.forecast_check import adjustment_value, service as fc_service
from backend.inventory.series import split_key
from backend.workers.runner import build_engine_config

log = logging.getLogger(__name__)

# One refresh writes at most this many SKU-periods; a longer window is a data problem.
MAX_EVIDENCE_ROWS = 400_000


def _ranges(tenant_id: str, session_id: str) -> dict[str, list[tuple[date, date]]]:
    """The dates to grade per SKU: every current submission plus the published lines."""
    out: dict[str, list[tuple[date, date]]] = {}
    for r in query(
            """SELECT sku, start_date, end_date FROM consensus_submissions
                WHERE tenant_id = %s AND session_id = %s AND superseded_by IS NULL""",
            (tenant_id, session_id)):
        out.setdefault(r["sku"], []).append((r["start_date"], r["end_date"]))
    # Every consensus that was published at some point (approved now, or since
    # withdrawn or replaced) is graded, not only the one in force.
    for version in query(
            """SELECT lines FROM consensus_versions
                WHERE tenant_id = %s AND session_id = %s
                  AND status IN ('approved', 'withdrawn', 'superseded')""",
            (tenant_id, session_id)):
        for line in version.get("lines") or []:
            try:
                out.setdefault(str(line["sku"]), []).append(
                    (date.fromisoformat(str(line["start_date"])[:10]),
                     date.fromisoformat(str(line["end_date"])[:10])))
            except (KeyError, TypeError, ValueError):
                log.error("consensus evidence: unreadable published line skipped")
    return out


def merge_ranges(ranges: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Overlapping or touching ranges folded together, so a day is graded once."""
    merged: list[tuple[date, date]] = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def evidence_points(ranges_by_sku: dict[str, list[tuple[date, date]]],
                    forecasts: dict[str, dict[str, float]],
                    actuals: dict[str, dict[str, float]]) -> list[dict]:
    """One row per (SKU, day) where a forecast and a real sale both exist, summed
    over the SKU's series (stores). Pure; no I/O."""
    totals: dict[tuple[str, str], list[float]] = {}
    for sku, ranges in ranges_by_sku.items():
        for lo, hi in merge_ranges(ranges):
            pseudo = [{"id": "evidence", "sku": sku, "start_date": lo, "end_date": hi, "pct": 0.0,
                       "created_by": "", "reason_code": ""}]
            for p in adjustment_value.build_points(pseudo, forecasts, actuals):
                acc = totals.setdefault((p["sku"], p["date"]), [0.0, 0.0])
                acc[0] += p["base"]
                acc[1] += p["actual"]
    return [{"sku": sku, "period": day, "base": base, "actual": actual}
            for (sku, day), (base, actual) in sorted(totals.items())]


def refresh(tenant_id: str, session: dict, dataset_id: Optional[str] = None) -> dict:
    """Rebuild the session's evidence. ``status``: ok | nothing_to_grade | no_forecast
    | no_data_yet | dataset_not_found | unreadable | <the loader's own reasons>."""
    session_id = session["id"]
    ranges = _ranges(tenant_id, session_id)
    base = {"session_id": session_id, "rows": 0, "source": None}
    if not ranges:
        return {**base, "status": "nothing_to_grade"}
    forecasts = fc_service._champion_forecasts(tenant_id, session_id)
    if not forecasts:
        return {**base, "status": "no_forecast"}
    cfg = build_engine_config(tenant_id, session_id)
    pseudo_all = [{"id": "evidence", "sku": sku, "start_date": lo, "end_date": hi, "pct": 0.0,
                   "created_by": "", "reason_code": ""}
                  for sku, rs in ranges.items() for lo, hi in merge_ranges(rs)]
    ds, loaded, reason = adjustment_value._pick_actuals(
        tenant_id, session, cfg["columns"], (cfg.get("granularity") or {}).get("target_freq"),
        forecasts, pseudo_all, dataset_id)
    if ds is None:
        return {**base, "status": reason}
    rows = evidence_points(ranges, forecasts, loaded["series"])
    if not rows:
        return {**base, "status": "no_data_yet"}
    if len(rows) > MAX_EVIDENCE_ROWS:
        return {**base, "status": "too_large", "rows": len(rows)}
    with transaction() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM consensus_evidence WHERE tenant_id = %s AND session_id = %s",
                    (tenant_id, session_id))
        cur.executemany(
            """INSERT INTO consensus_evidence (tenant_id, session_id, sku, period, base, actual, dataset_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            [(tenant_id, session_id, r["sku"], r["period"], r["base"], r["actual"], ds["id"])
             for r in rows])
    return {**base, "status": "ok", "rows": len(rows),
            "source": {"dataset_id": ds["id"], "name": ds["name"]}}
