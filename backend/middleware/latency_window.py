"""Request latency per route family, in a bounded in-memory rolling window.

Why in memory and not a table: the question an operator asks is "is it slow
RIGHT NOW", and a write per request would make the metric a load of its own.
The cost of that choice is stated, not hidden: the window lives in one process
and is lost on restart, so it describes the API process that answers the
operations endpoint and nothing else.

Memory is bounded twice: each family keeps at most `SAMPLES_PER_FAMILY`
samples, and at most `MAX_FAMILIES` families exist - a route family beyond that
is folded into `other`, so a scanner inventing URLs cannot grow the dict.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

SAMPLES_PER_FAMILY = 500
MAX_FAMILIES = 64
WINDOW_SECONDS = 900.0  # samples older than 15 minutes are ignored in reports
_API_PREFIX = "/api/v1"
OTHER = "other"

_lock = threading.Lock()
# family -> deque[(epoch_seconds, elapsed_ms, is_server_error)]
_samples: dict[str, deque] = {}


def route_family(path_template: str | None, raw_path: str) -> str:
    """First path segment after `/api/v1`, taken from the route TEMPLATE when
    the router matched one (so `/skus/ABC-1` and `/skus/ABC-2` are one family
    and an id never becomes a key). Unmatched paths are `other`."""
    if path_template:
        return _first_segment(path_template)
    return OTHER


def _first_segment(path: str) -> str:
    rest = path[len(_API_PREFIX):] if path.startswith(_API_PREFIX) else path
    head = rest.strip("/").split("/", 1)[0]
    if not head or head.startswith("{"):
        return OTHER
    return head[:40]


def record(family: str, elapsed_ms: float, status_code: int, now: float | None = None) -> None:
    stamp = now if now is not None else time.time()
    with _lock:
        bucket = _samples.get(family)
        if bucket is None:
            if len(_samples) >= MAX_FAMILIES:
                family = OTHER
            bucket = _samples.setdefault(family, deque(maxlen=SAMPLES_PER_FAMILY))
        bucket.append((stamp, elapsed_ms, status_code >= 500))


def _percentile(sorted_values: list[float], pct: int) -> float:
    """Nearest-rank percentile of an already sorted, non-empty list."""
    rank = max(1, math.ceil(len(sorted_values) * pct / 100))
    return sorted_values[rank - 1]


def snapshot(now: float | None = None) -> list[dict]:
    """One row per family with traffic inside the window, busiest first."""
    cutoff = (now if now is not None else time.time()) - WINDOW_SECONDS
    with _lock:
        copies = {k: list(v) for k, v in _samples.items()}
    rows = []
    for family, items in copies.items():
        recent = [i for i in items if i[0] >= cutoff]
        if not recent:
            continue
        values = sorted(i[1] for i in recent)
        rows.append({
            "family": family,
            "count": len(recent),
            "errors_5xx": sum(1 for i in recent if i[2]),
            "p50_ms": round(_percentile(values, 50), 1),
            "p95_ms": round(_percentile(values, 95), 1),
            "p99_ms": round(_percentile(values, 99), 1),
            "max_ms": round(values[-1], 1),
        })
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows


def reset() -> None:
    """Test hook."""
    with _lock:
        _samples.clear()
