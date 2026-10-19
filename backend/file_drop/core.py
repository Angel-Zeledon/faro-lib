"""The decisions behind file-drop sources, as pure functions.

Free of the database, the network and the file system, so each rule is pinned by
a test that a mock cannot fool: which files a pattern selects, when a file is
finished uploading, whether a file is plausible next to the data it replaces,
and the line a person reads next to a file that was refused.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

ALLOWED_SUFFIXES = (".csv", ".xlsx", ".xls", ".parquet", ".json")

# Where finished files go, next to the files in the same directory/prefix.
PROCESSED_DIR = "processed"
REJECTED_DIR = "rejected"

# A new file whose row count falls under this share of the file it replaces is
# held for a person: a truncated export, an empty month or a wrong filter looks
# exactly like this and would otherwise erase the history a forecast reads.
MIN_ROW_RATIO = 0.5

MAX_ATTEMPTS = 3
MAX_PATTERN_LENGTH = 200


def parse_patterns(raw: Optional[str]) -> list[str]:
    """`*.csv, sales_*.xlsx` -> the individual patterns. Empty means CSV only."""
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    return parts or ["*.csv"]


def pattern_error(raw: Optional[str]) -> Optional[str]:
    """Why a pattern list cannot be used, or None. A pattern with a path
    separator could reach outside the watched directory."""
    if raw is not None and len(raw) > MAX_PATTERN_LENGTH:
        return "too_long"
    for p in parse_patterns(raw):
        if "/" in p or "\\" in p or ".." in p:
            return "path_in_pattern"
    return None


def selects(patterns: list[str], name: str) -> bool:
    """Whether a file name is one this source should read. Case-insensitive; the
    processed/ and rejected/ folders and our own sidecar notes are never read."""
    lowered = name.lower()
    if "/" in name or "\\" in name:
        return False
    if lowered.endswith(".reason.txt") or lowered.startswith("."):
        return False
    if not lowered.endswith(ALLOWED_SUFFIXES):
        return False
    return any(fnmatch.fnmatchcase(lowered, p.lower()) for p in patterns)


def is_stable(previous: Optional[list], size: int, mtime: float, *,
              now: float, min_age_seconds: int) -> bool:
    """A file is finished when two looks agree on its size and modification time
    AND it was last touched at least `min_age_seconds` ago. The first look of a
    file never qualifies: that is how a file that is still being uploaded (its
    size grows between looks) is not read half-written."""
    if not previous or len(previous) != 2:
        return False
    if int(previous[0]) != int(size) or abs(float(previous[1]) - float(mtime)) > 1e-6:
        return False
    return (now - float(mtime)) >= min_age_seconds


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    """What is known about a table of data without loading it."""
    rows: Optional[int]
    columns: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    code: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)


def judge(existing: Optional[Snapshot], incoming: Snapshot) -> Verdict:
    """Prove-before-apply: is `incoming` plausible next to the data it replaces?

    Held (never silently applied) when it has no rows, when its columns are not
    the ones the saved mapping was built on, or when it has far fewer rows than
    the file it replaces. With nothing to compare to (a first file) only emptiness
    is judged. Unknown row counts are never held on that ground.
    """
    if incoming.rows == 0:
        return Verdict(False, "file_drop_file_empty")
    if existing is None:
        return Verdict(True)
    if existing.columns and incoming.columns:
        before = {c.strip().lower() for c in existing.columns}
        after = {c.strip().lower() for c in incoming.columns}
        if before != after:
            return Verdict(False, "file_drop_columns_changed", {
                "missing": sorted(before - after)[:10], "added": sorted(after - before)[:10]})
    if existing.rows and incoming.rows is not None \
            and incoming.rows < existing.rows * MIN_ROW_RATIO:
        return Verdict(False, "file_drop_rows_dropped", {
            "previous_rows": existing.rows, "rows": incoming.rows,
            "min_ratio": MIN_ROW_RATIO})
    return Verdict(True)


def sidecar_text(code: str, params: dict[str, Any], at: datetime) -> str:
    """The note left beside a rejected file on the company's own server."""
    stamp = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()
    return ("StockAI did not apply this file.\n"
            f"time: {stamp}\n"
            f"reason: {code}\n"
            f"details: {json.dumps(params, default=str, sort_keys=True)}\n"
            "Fix the file and drop it again; a file with the same content is not read twice.\n")


def can_retry(status: str, attempts: int) -> bool:
    """A failure (a network blip while applying) is tried again a few times; a
    decision (applied, held, rejected) is final for that exact content."""
    return status in ("failed", "applying") and attempts < MAX_ATTEMPTS


def jittered_delay_seconds(interval_minutes: int, unit_random: float) -> float:
    """The interval ±20%, so many companies on the same interval do not all poll
    in the same second. `unit_random` is a number in [0, 1)."""
    base = interval_minutes * 60
    return base * (0.8 + 0.4 * max(0.0, min(0.999999, unit_random)))
