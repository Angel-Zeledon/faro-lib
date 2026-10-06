"""A short-lived, per-process cache of what a connection's schema looks like.

Opening the schema browser lists every table the login can read; expanding a
table lists its columns. Each is a connection to the customer's database, and
the answer changes rarely — so it is kept for `TTL_S` seconds, keyed by
tenant, source AND a fingerprint of the stored connection, so editing the
connection (another database, another user, a new password) can never serve
the previous connection's schema. The test, an edit and a delete also drop
the source's entries explicitly.

Bounded (`MAX_ENTRIES`, oldest evicted first) and in-process: a second API
process keeps its own copy, which costs at most one extra catalogue query.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

TTL_S = 300
MAX_ENTRIES = 512

_lock = threading.Lock()
_entries: "OrderedDict[tuple, tuple[float, str, Any]]" = OrderedDict()


def fingerprint(cfg: dict) -> str:
    keys = ("engine", "host", "port", "database", "username", "password_enc", "ssl_mode")
    raw = json.dumps({k: cfg.get(k) for k in keys}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_or_load(tenant_id: str, source_id: str, cfg: dict, what: tuple,
                load: Callable[[], Any], *, refresh: bool = False) -> tuple[Any, str]:
    """(value, iso time it was read). `load` runs outside the lock, so a slow
    catalogue query never blocks another tenant's cache hit."""
    key = (tenant_id, source_id, fingerprint(cfg)) + tuple(what)
    now = time.monotonic()
    if not refresh:
        with _lock:
            hit = _entries.get(key)
            if hit and hit[0] > now:
                _entries.move_to_end(key)
                return hit[2], hit[1]
    value = load()
    stamp = datetime.now(timezone.utc).isoformat()
    with _lock:
        _entries[key] = (now + TTL_S, stamp, value)
        _entries.move_to_end(key)
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)
    return value, stamp


def invalidate(tenant_id: str, source_id: str) -> None:
    with _lock:
        for key in [k for k in _entries if k[0] == tenant_id and k[1] == source_id]:
            del _entries[key]


def clear() -> None:
    with _lock:
        _entries.clear()
