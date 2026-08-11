"""An API key with no limit is a loop waiting to happen.

Named after the gap, not the function. Auditing the API surface on 2026-08-10:
`_check_rate` guarded the login endpoints and nothing else, so a machine
credential — the one credential that runs unattended, in a cron, with retries —
could call 246 routes as fast as it could open sockets. Nothing errored, because
nothing was watching.

`testing_mode` is on in this repo's .env and bypasses every quota, so these tests
turn it off themselves. That is the house rule, and it is why a limiter can ship
green while never having been exercised.
"""

import pytest

from backend.auth import api_key_auth
from backend.config import settings
from backend.db.connection import execute, query_one


@pytest.fixture
def live_key(test_tenant, auth_headers, client):
    """A real key on a plan that may use one.

    The plan upgrade is not decoration: with `testing_mode` off the guard checks
    the feature on every call, and a starter tenant is refused 403 before the
    limiter is ever reached. Which is the right order — a tenant without API
    access should not spend rate budget — and is why this fixture has to say so.
    """
    execute("UPDATE tenants SET plan = 'professional' WHERE id = %s", (test_tenant["id"],))
    r = client.post("/api/v1/api-keys",
                    json={"name": "erp-nightly", "role": "analyst"},
                    headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    data = r.json()["data"]
    raw = data.get("key") or data.get("api_key") or data.get("secret")
    assert raw, f"the endpoint returned no usable secret: {list(data)}"
    # The create response deliberately returns only the secret, so the id comes
    # from the same lookup the guard performs — anything else would be this test
    # guessing which row it is limiting.
    resolved = api_key_auth.resolve(raw)
    assert resolved, "the freshly minted key does not authenticate"
    yield {"raw": raw, "id": resolved["id"]}
    execute("DELETE FROM auth_rate_events WHERE key LIKE 'apikey:%%'")


class TestOneKeyCannotCallWithoutBound:
    def test_the_window_refuses_once_the_allowance_is_spent(self, monkeypatch):
        monkeypatch.setattr(settings, "testing_mode", False)
        key_id = "key_rate_probe"
        execute("DELETE FROM auth_rate_events WHERE key = %s", (f"apikey:{key_id}",))

        allowed = sum(1 for _ in range(api_key_auth.RATE_MAX_PER_MINUTE)
                      if api_key_auth.check_rate(key_id))
        assert allowed == api_key_auth.RATE_MAX_PER_MINUTE, (
            "the limiter refused a call inside the allowance"
        )
        assert api_key_auth.check_rate(key_id) is False, (
            "call number "
            f"{api_key_auth.RATE_MAX_PER_MINUTE + 1} was allowed; the key has no bound"
        )

    def test_one_key_running_hot_does_not_limit_another(self, monkeypatch):
        """Buckets are per key. A shared counter would let one customer's ERP
        throttle everybody else's."""
        monkeypatch.setattr(settings, "testing_mode", False)
        hot, cold = "key_hot", "key_cold"
        for k in (hot, cold):
            execute("DELETE FROM auth_rate_events WHERE key = %s", (f"apikey:{k}",))

        for _ in range(api_key_auth.RATE_MAX_PER_MINUTE + 5):
            api_key_auth.check_rate(hot)

        assert api_key_auth.check_rate(hot) is False
        assert api_key_auth.check_rate(cold) is True

    def test_a_broken_rate_store_does_not_take_the_integration_down(self, monkeypatch):
        """Fails OPEN on purpose: this runs on every machine call, and refusing
        every integration because a counter cannot write is worse than briefly
        not counting."""
        monkeypatch.setattr(settings, "testing_mode", False)

        def boom(*_a, **_k):
            raise RuntimeError("rate store unreachable")

        monkeypatch.setattr("backend.auth.api_key_auth.execute", boom)
        assert api_key_auth.check_rate("key_whatever") is True


class TestTheLimitIsWiredIntoAuthentication:
    def test_a_throttled_key_gets_429_with_retry_after(self, monkeypatch, client, live_key):
        monkeypatch.setattr(settings, "testing_mode", False)
        headers = {"Authorization": f"Bearer {live_key['raw']}"}

        first = client.get("/api/v1/data-sources", headers=headers)
        assert first.status_code != 429, "throttled before the allowance was spent"
        assert first.status_code != 403, (
            "the plan gate refused before the limiter was reached — the fixture "
            "must put the tenant on a plan that includes API access"
        )

        for _ in range(api_key_auth.RATE_MAX_PER_MINUTE + 2):
            api_key_auth.check_rate(live_key["id"])

        blocked = client.get("/api/v1/data-sources", headers=headers)
        assert blocked.status_code == 429, (
            f"a key over its window still answered {blocked.status_code}"
        )
        assert blocked.headers.get("Retry-After"), (
            "429 with no Retry-After makes the client guess, and a guessing "
            "client retries harder than the loop being limited"
        )
