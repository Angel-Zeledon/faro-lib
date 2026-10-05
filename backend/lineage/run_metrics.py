"""How long training runs really take, read back from the lineage manifests.

The manifest already stores `timing.duration_seconds`, the series count and the
session's granularity for every finished run, so an operator choosing a retrain
cadence ("weekly, or is nightly affordable?") can look at evidence instead of
guessing. Nothing new is persisted: this is a read-only aggregate over
`session_manifests`, bounded to the last N runs of ONE tenant.

Only COMPLETED runs are timed. A run that failed after twenty seconds says
nothing about how long a good one takes; failures are counted, not averaged.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from backend.db.connection import query

DEFAULT_RUNS = 50
MAX_RUNS = 200

# Catalogue-size buckets, by the number of series the run forecast. `max` None
# is open-ended. The UI formats the label; the backend only ships numbers.
SIZE_BUCKETS: tuple[tuple[int, Optional[int]], ...] = (
    (1, 50), (51, 200), (201, 1000), (1001, None),
)


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already sorted, non-empty list."""
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


def _summary(durations: list[float]) -> dict:
    ordered = sorted(durations)
    n = len(ordered)
    median = (ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2)
    return {
        "runs": n,
        "median_seconds": round(median, 1),
        "p95_seconds": round(_percentile(ordered, 95), 1),
        "max_seconds": round(ordered[-1], 1),
    }


def _bucket_of(series: int) -> Optional[tuple[int, Optional[int]]]:
    for low, high in SIZE_BUCKETS:
        if series >= low and (high is None or series <= high):
            return (low, high)
    return None


def run_duration_metrics(tenant_id: str, limit: int = DEFAULT_RUNS) -> dict[str, Any]:
    """Median / p95 training duration for this tenant's last `limit` runs, by
    catalogue-size bucket and by granularity.

    A run with no recorded duration (an old manifest, or one that ended before
    its timing could be read) is skipped and counted in `untimed_runs`, never
    treated as zero seconds.
    """
    limit = max(1, min(int(limit), MAX_RUNS))
    rows = query(
        """SELECT outcome,
                  manifest #>> '{timing,duration_seconds}'   AS duration,
                  manifest #>> '{counts,skus_forecast}'      AS series,
                  manifest #>> '{session,granularity}'       AS granularity,
                  created_at
           FROM session_manifests
           WHERE tenant_id = %s
           ORDER BY created_at DESC
           LIMIT %s""",
        (tenant_id, limit),
    )

    timed: list[tuple[float, int, str]] = []
    failed = untimed = 0
    for r in rows:
        if r["outcome"] != "COMPLETED":
            failed += 1
            continue
        try:
            seconds = float(r["duration"])
            series = int(r["series"] or 0)
        except (TypeError, ValueError):
            untimed += 1
            continue
        timed.append((seconds, series, r["granularity"] or "unknown"))

    by_size: list[dict] = []
    for low, high in SIZE_BUCKETS:
        in_bucket = [s for s, n, _g in timed if _bucket_of(n) == (low, high)]
        if in_bucket:
            by_size.append({"min_series": low, "max_series": high, **_summary(in_bucket)})

    by_granularity: list[dict] = []
    for granularity in sorted({g for _s, _n, g in timed}):
        in_group = [s for s, _n, g in timed if g == granularity]
        by_granularity.append({"granularity": granularity, **_summary(in_group)})

    return {
        "window_runs": len(rows),
        "limit": limit,
        "completed_runs": len(timed),
        "failed_runs": failed,
        "untimed_runs": untimed,
        "overall": _summary([s for s, _n, _g in timed]) if timed else None,
        "by_size": by_size,
        "by_granularity": by_granularity,
    }
