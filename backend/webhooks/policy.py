"""The decisions behind webhook delivery, as pure functions.

Kept free of the database and of HTTP so each rule can be pinned by a test that
cannot be fooled by a mock: the retry schedule, which outcomes retry, the
failure-day streak that disables a hook, which warehouse scopes see an event,
and "newly entered" for once-per-transition events.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Iterable, Optional

# ── Retries ──────────────────────────────────────────────────────────────────
# Five attempts: now, then +1 min, +5 min, +15 min, +40 min - a hook that is
# down for an hour is retried across that hour, and then the delivery is given
# up (status `failed`, visible in the log, never silently dropped).
BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 900, 2400)
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1

# A claimed delivery that a crashed worker never finished is retried after this.
CLAIM_LEASE_SECONDS = 300

MAX_ERROR_LENGTH = 300

# A hook that fails every delivery on this many different days is switched off.
DISABLE_AFTER_FAILURE_DAYS = 3

OUTCOME_SUCCESS = "success"
OUTCOME_RETRY = "retry"
OUTCOME_FAIL = "fail"


def next_delay_seconds(attempts_done: int) -> Optional[int]:
    """Seconds to wait before the next attempt, or None when attempts are spent."""
    if attempts_done < 1 or attempts_done >= MAX_ATTEMPTS:
        return None
    return BACKOFF_SECONDS[attempts_done - 1]


def classify_outcome(status_code: Optional[int], error: Optional[str]) -> str:
    """2xx succeeds. A transport error or timeout (no status), a 5xx, a 408 and
    a 429 are the receiver's moment and are retried. Any other status (a 4xx, a
    3xx - redirects are not followed) will not change by waiting: it fails now
    with the code in the log."""
    if status_code is None:
        return OUTCOME_RETRY if error else OUTCOME_FAIL
    if 200 <= status_code < 300:
        return OUTCOME_SUCCESS
    if status_code >= 500 or status_code in (408, 429):
        return OUTCOME_RETRY
    return OUTCOME_FAIL


def truncate_error(text: object) -> str:
    return str(text).replace("\x00", "")[:MAX_ERROR_LENGTH]


# ── Auto-disable ─────────────────────────────────────────────────────────────

def advance_failure_days(failure_days: int, last_failure_on: Optional[date],
                         today: date) -> tuple[int, date]:
    """A delivery just gave up. Count the day once, however many give up on it."""
    if last_failure_on == today:
        return failure_days, today
    return failure_days + 1, today


def should_disable(failure_days: int) -> bool:
    return failure_days >= DISABLE_AFTER_FAILURE_DAYS


# ── Scope ────────────────────────────────────────────────────────────────────

def parse_scope(raw: Any) -> Optional[list[str]]:
    """The stored scope: None = company-wide, a list of warehouse ids otherwise.
    Fails closed: an unreadable value is an EMPTY scope, never an open one."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return [str(i) for i in raw]


def scope_allows(hook_scope: Optional[list[str]], warehouse_id: Optional[str],
                 warehouse_aware: bool) -> bool:
    """Whether a webhook with `hook_scope` may receive an event about
    `warehouse_id`. Company-wide hooks (None) receive everything. A scoped hook
    receives a warehouse event only for a warehouse in its scope, and never an
    event that names no warehouse (a company figure) - except events that are
    not about a warehouse at all (`warehouse_aware` False: job events)."""
    if hook_scope is None or not warehouse_aware:
        return True
    return warehouse_id is not None and warehouse_id in hook_scope


def manageable_by(user_scope: Optional[list[str]], hook_scope: Optional[list[str]]) -> bool:
    """Whether a user may see and manage a webhook: an unrestricted user any,
    a scoped one only hooks confined to warehouses inside their own scope."""
    if user_scope is None:
        return True
    if hook_scope is None:
        return False
    return all(w in user_scope for w in hook_scope)


def narrow_scope(creator: Optional[list[str]], requested: Optional[list[str]]
                 ) -> tuple[Optional[list[str]], list[str]]:
    """(scope to store, requested ids outside the creator's scope). A hook never
    reaches past its creator; with nothing asked it inherits the creator's."""
    if creator is None:
        return requested, []
    if requested is None:
        return list(creator), []
    outside = [w for w in requested if w not in creator]
    return requested, outside


# ── Once per transition ──────────────────────────────────────────────────────

def newly_entered(previous: Iterable[str], current: Iterable[str]) -> list[str]:
    """Keys in `current` that were not in `previous`, in a stable order. A key
    that stays in the state is not new, so it is never re-emitted; one that
    left and came back is new again."""
    before = set(previous)
    seen: set[str] = set()
    out: list[str] = []
    for key in current:
        if key not in before and key not in seen:
            seen.add(key)
            out.append(key)
    return sorted(out)
