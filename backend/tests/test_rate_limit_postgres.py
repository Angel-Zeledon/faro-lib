"""
Postgres-backed rate limiter (hardening item 4.3).

The auth rate limiter (`backend/api/v1/auth.py::_check_rate`) keeps its sliding
window in Postgres (`auth_rate_events`) rather than a per-process dict, so the
window survives restarts and is shared across worker processes/instances.

These tests exercise `_check_rate` directly (the endpoint-level path is covered
by `test_audit_hardening.py::TestDbBackedRateLimit`) and pin the behaviour the
migration to Postgres exists to guarantee:

- the (N+1)th call inside the window is blocked with a 429,
- every allowed call lands a row in `auth_rate_events`,
- rows older than the window do NOT count (sliding window really slides),
- `settings.testing_mode` bypasses the limiter entirely (never blocks, never
  writes rows).
"""

import uuid

import pytest
from fastapi import HTTPException

from backend.api.v1.auth import _check_rate
from backend.config import settings
from backend.db.connection import execute, query_one


def _unique_key() -> str:
    return f"ratelimit-test:{uuid.uuid4().hex}"


def _count(key: str) -> int:
    row = query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s", (key,))
    return int(row["n"]) if row else 0


class TestPostgresRateLimit:
    def test_blocks_after_threshold_and_persists_rows(self, client, monkeypatch):
        """N allowed calls each persist a row; the (N+1)th is blocked with 429."""
        monkeypatch.setattr(settings, "testing_mode", False)
        key = _unique_key()
        max_attempts = 3
        try:
            # The first `max_attempts` calls are allowed and each records a row.
            for i in range(max_attempts):
                _check_rate(key, max_attempts=max_attempts, window_secs=300)
                assert _count(key) == i + 1, "each allowed call must persist one row"

            # The next call exceeds the window count and is blocked.
            with pytest.raises(HTTPException) as exc:
                _check_rate(key, max_attempts=max_attempts, window_secs=300)
            assert exc.value.status_code == 429

            # The blocked call must NOT have added a row — still exactly N.
            assert _count(key) == max_attempts
        finally:
            execute("DELETE FROM auth_rate_events WHERE key = %s", (key,))

    def test_rows_outside_window_do_not_count(self, client, monkeypatch):
        """Backdated rows (older than the window) are excluded, so the limit resets."""
        monkeypatch.setattr(settings, "testing_mode", False)
        key = _unique_key()
        max_attempts = 3
        window_secs = 60
        try:
            # Seed `max_attempts` rows that are already older than the window.
            for _ in range(max_attempts):
                execute(
                    "INSERT INTO auth_rate_events (key, created_at) "
                    "VALUES (%s, NOW() - make_interval(secs => %s))",
                    (key, window_secs + 120),
                )
            assert _count(key) == max_attempts

            # Despite `max_attempts` stale rows, the call is allowed: the sliding
            # window excludes them (they fall outside `window_secs`), so the
            # effective count is 0 and one fresh row is recorded.
            _check_rate(key, max_attempts=max_attempts, window_secs=window_secs)

            # The stale rows were pruned and only the fresh in-window row remains.
            assert _count(key) == 1
        finally:
            execute("DELETE FROM auth_rate_events WHERE key = %s", (key,))

    def test_at_threshold_within_window_blocks(self, client, monkeypatch):
        """`max_attempts` fresh in-window rows block the next call."""
        monkeypatch.setattr(settings, "testing_mode", False)
        key = _unique_key()
        max_attempts = 2
        try:
            for _ in range(max_attempts):
                execute("INSERT INTO auth_rate_events (key) VALUES (%s)", (key,))

            with pytest.raises(HTTPException) as exc:
                _check_rate(key, max_attempts=max_attempts, window_secs=300)
            assert exc.value.status_code == 429
            # Blocked call adds nothing.
            assert _count(key) == max_attempts
        finally:
            execute("DELETE FROM auth_rate_events WHERE key = %s", (key,))

    def test_testing_mode_bypasses_limiter(self, client, monkeypatch):
        """With testing_mode on, the limiter never blocks and never writes rows."""
        monkeypatch.setattr(settings, "testing_mode", True)
        key = _unique_key()
        try:
            # Far exceed the threshold; every call must be a silent no-op.
            for _ in range(10):
                _check_rate(key, max_attempts=2, window_secs=300)
            assert _count(key) == 0, "testing_mode must not record rate-limit events"
        finally:
            execute("DELETE FROM auth_rate_events WHERE key = %s", (key,))
