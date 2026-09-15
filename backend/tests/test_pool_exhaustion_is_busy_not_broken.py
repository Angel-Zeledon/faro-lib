"""A full connection pool is busy, not broken, and the answer has to say which.

Found by the stress suite: 10 concurrent session creates against a pool of 10
exhausted it, and the request came back **500 internal_error** — the same
answer a crash gives. That sends the caller's integration into "the service is
down, stop retrying" and whoever is on call into a hunt for a traceback that
does not exist, when the true statement is "more requests arrived at once than
this instance has connections, and waiting fixes it".

`_acquire` already waits (10s). What changed is what happens when the wait runs
out: a typed `PoolExhausted` instead of a bare `PoolError`, answered as 503 with
`Retry-After`.
"""

import psycopg2.pool
import pytest

from backend.db import connection as conn_mod


def test_the_wait_is_not_skipped(monkeypatch):
    """The retry loop is the first line of defence, and it must still be there:
    a connection freed 5ms after the burst has to be used, not refused."""
    calls = {"n": 0}

    class _Pool:
        def getconn(self):
            calls["n"] += 1
            if calls["n"] < 3:
                raise psycopg2.pool.PoolError("connection pool exhausted")
            return "a-connection"

    monkeypatch.setattr(conn_mod, "_pool", _Pool())
    assert conn_mod._acquire() == "a-connection"
    assert calls["n"] == 3, "it gave up instead of waiting for a free connection"


def test_an_exhausted_pool_raises_its_own_type(monkeypatch):
    """Not a bare PoolError: the API needs to tell this apart from every other
    database failure, and string-matching an exception message is how that kind
    of handler silently stops working."""
    class _Pool:
        def getconn(self):
            raise psycopg2.pool.PoolError("connection pool exhausted")

    monkeypatch.setattr(conn_mod, "_pool", _Pool())
    monkeypatch.setattr(conn_mod, "_POOL_WAIT_SECONDS", 0.05)

    with pytest.raises(conn_mod.PoolExhausted) as caught:
        conn_mod._acquire()
    # The message has to say the request never ran. "Something went wrong" would
    # leave a caller wondering whether their write half-happened.
    assert "not attempted" in str(caught.value)


def test_another_pool_error_is_not_dressed_as_busy(monkeypatch):
    """`PoolError` also covers "connection pool is closed", which is NOT busy
    and does not fix itself by waiting. Retrying it would hang the request for
    the full wait and then lie about why."""
    class _Pool:
        def getconn(self):
            raise psycopg2.pool.PoolError("connection pool is closed")

    monkeypatch.setattr(conn_mod, "_pool", _Pool())
    with pytest.raises(psycopg2.pool.PoolError) as caught:
        conn_mod._acquire()
    assert not isinstance(caught.value, conn_mod.PoolExhausted)


def test_the_api_answers_busy_with_a_retry_after(client, auth_headers, monkeypatch):
    """End to end: the shape a caller actually receives. 503 + a code the UI can
    translate + Retry-After, never a 500."""
    def exhausted(*_a, **_k):
        raise conn_mod.PoolExhausted(
            "All database connections were busy for 10s. The request was not attempted."
        )

    # Patched at the acquisition point, so every query path inherits it.
    monkeypatch.setattr(conn_mod, "_acquire", exhausted)

    resp = client.get("/api/v1/sessions", headers=auth_headers)
    assert resp.status_code == 503, (
        f"a busy pool answered {resp.status_code}; a 500 tells the caller the "
        f"service is broken and to stop retrying. Body: {resp.text[:200]}"
    )
    assert resp.json()["error_code"] == "server_busy"
    assert resp.headers.get("Retry-After") == "2"
