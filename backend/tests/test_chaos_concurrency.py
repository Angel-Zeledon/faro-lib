"""Chaos: what happens when two people click at the same instant.

Every limit in this product is a read followed by a write:

    current = count_stock(tenant)          # ← another request lands here
    enforce_limit(tenant, "max_skus", current)
    write()

Single-threaded, that is correct. Under load it is a check that has already
expired by the time it is acted on, and the gap is wide — a `SELECT COUNT(*)`, a
tenant lookup, and a plan resolution all happen between the two. A ceiling that
can be walked through by clicking twice is not a ceiling, and on the free tier
it is the entire commercial boundary of the product.

The existing stress file tests the *database layer* under concurrency — lost
commit acks, dead pooled connections, writes beyond pool size — and tests it
well. What it never asks is whether the *business rules* on top of that layer
survive the same conditions. That is this file.

Every test here follows the same shape: fire N real HTTP requests
simultaneously, then ask the database what actually happened. The assertion is
always about the final state, never about the responses — a race that answers
200 twice and writes one row is fine; one that answers 403 and writes anyway is
not.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from backend.db.connection import execute, query, query_one, _json

pytestmark = pytest.mark.stress


# ── Helpers ──────────────────────────────────────────────────────────────────

def _set_tier(tenant_id: str, tier: str) -> None:
    execute("UPDATE tenants SET tier = %s WHERE id = %s", (tier, tenant_id))


def _set_quota(tenant_id: str, quota: dict) -> None:
    execute("UPDATE tenants SET quota = %s WHERE id = %s", (_json(quota), tenant_id))


def _stock(**over):
    return {"current_stock": 1, "min_stock": 0, "lead_time_days": 5, **over}


def _count_stock(tenant_id: str) -> int:
    return query_one(
        "SELECT COUNT(*) AS c FROM inventory_stock WHERE tenant_id = %s", (tenant_id,)
    )["c"]


def _fire(fn, n: int, workers: int | None = None):
    """Run `fn(i)` for i in range(n) as simultaneously as threads allow.

    A barrier, not just a thread pool: without it the first thread finishes
    before the last one starts and the "race" never happens. Exceptions are
    returned rather than raised so one thread's failure cannot hide the state
    the others produced — which is the only thing being measured.
    """
    workers = workers or n
    ready = threading.Barrier(workers)
    results: list = [None] * n

    def run(i):
        try:
            ready.wait(timeout=30)
        except threading.BrokenBarrierError:        # pragma: no cover
            pass
        try:
            results[i] = fn(i)
        except Exception as exc:                    # noqa: BLE001
            results[i] = exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, range(n)))
    return results


def _statuses(results):
    return [r.status_code if hasattr(r, "status_code") else r for r in results]


# ── 1. The commercial boundary, under a race ─────────────────────────────────

def test_a_plan_ceiling_cannot_be_walked_through_by_clicking_twice(
    monkeypatch, make_tenant_user_headers, client,
):
    """Twelve simultaneous SKU creations against a ceiling of five.

    This is the free tier's entire value proposition under the conditions it
    will actually meet: a bulk import in one tab, a manual entry in another, an
    integration syncing in the background. If the count ends above five, the
    limit is advisory — and the tenant who needed to talk to us never has to.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _set_quota(tenant_id, {"max_skus": 5})

    results = _fire(
        lambda i: client.put(f"/api/v1/inventory/stock/RACE-{i:03d}",
                             json=_stock(), headers=headers),
        n=12,
    )

    final = _count_stock(tenant_id)
    accepted = sum(1 for s in _statuses(results) if s == 200)
    assert final <= 5, (
        f"{final} SKUs exist against a ceiling of 5 — {accepted} of 12 concurrent "
        f"writes were accepted. The check and the write are not atomic, so the "
        f"free tier can be exceeded by anyone who clicks twice."
    )
    # And the responses must not have lied in the other direction either.
    assert accepted == final, (
        f"{accepted} requests were told they succeeded but only {final} rows exist"
    )


def test_the_same_sku_written_twelve_times_at_once_is_one_row(
    make_tenant_user_headers, client,
):
    """The lost-update shape. Twelve threads upsert the SAME SKU with different
    stock values: exactly one row must exist afterwards, holding one of the
    values sent — never two rows, never a merged nonsense number."""
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)

    sent = list(range(1, 13))
    results = _fire(
        lambda i: client.put("/api/v1/inventory/stock/CONTENDED",
                             json=_stock(current_stock=sent[i]), headers=headers),
        n=12,
    )

    rows = query(
        "SELECT sku, current_stock FROM inventory_stock "
        "WHERE tenant_id = %s AND sku = %s",
        (tenant_id, "CONTENDED"),
    )
    assert len(rows) == 1, (
        f"{len(rows)} rows for one SKU — concurrent upserts are inserting instead "
        f"of updating: {_statuses(results)}"
    )
    assert float(rows[0]["current_stock"]) in [float(v) for v in sent], (
        f"stock is {rows[0]['current_stock']}, which nobody sent — two writes "
        f"were interleaved into one value"
    )


def test_the_user_ceiling_holds_when_two_admins_invite_at_once(
    monkeypatch, make_tenant_user_headers, client,
):
    """Same race, different resource — and this one costs money directly, since
    seats are the thing a growing team hits first."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers, tenant_id = make_tenant_user_headers(role="admin", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _set_quota(tenant_id, {"max_users": 2})       # the tenant already has 1

    before = query_one(
        "SELECT COUNT(*) AS c FROM users WHERE tenant_id = %s", (tenant_id,)
    )["c"]

    _fire(
        lambda i: client.post(
            "/api/v1/users",
            json={"email": f"race-{uuid4().hex[:8]}@example.com",
                  "full_name": "Race", "role": "viewer"},
            headers=headers,
        ),
        n=8,
    )

    after = query_one(
        "SELECT COUNT(*) AS c FROM users WHERE tenant_id = %s", (tenant_id,)
    )["c"]
    assert after <= 2, (
        f"the tenant went from {before} to {after} users against a ceiling of 2 — "
        f"eight simultaneous invitations all passed the same stale count"
    )


def test_two_people_asking_to_pay_at_the_same_moment_file_one_request(
    monkeypatch, make_tenant_user_headers, client,
):
    """The upgrade funnel is read by hand, so it holds one open ask per tenant.
    That invariant is also a read-then-write, and the two people most likely to
    hit it are the two who just watched the same import fail."""
    monkeypatch.setattr("backend.config.settings.upgrade_notify_email", "")
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)

    _fire(
        lambda i: client.post("/api/v1/entitlements/upgrade-request",
                              json={"message": f"from click {i}"}, headers=headers),
        n=6,
    )

    rows = query(
        "SELECT id FROM upgrade_requests WHERE tenant_id = %s AND status = 'new'",
        (tenant_id,),
    )
    assert len(rows) == 1, (
        f"{len(rows)} open upgrade requests for one tenant — six simultaneous "
        f"clicks each found no existing row and inserted their own"
    )


def test_the_session_ceiling_holds_under_simultaneous_creates(
    monkeypatch, make_tenant_user_headers, client,
):
    """Saved forecasts, raced from two tabs. Same shape as the SKU ceiling, and
    it has to be guarded separately — a ceiling is only as strong as its least
    protected write path."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _set_quota(tenant_id, {"max_sessions": 2})

    _fire(
        lambda i: client.post("/api/v1/sessions",
                              json={"name": f"race-{i}", "description": ""},
                              headers=headers),
        n=8,
    )
    count = query_one(
        "SELECT COUNT(*) AS c FROM sessions WHERE tenant_id = %s", (tenant_id,)
    )["c"]
    assert count <= 2, f"{count} sessions against a ceiling of 2"


def test_the_api_key_ceiling_holds_under_simultaneous_creates(
    monkeypatch, make_tenant_user_headers, client,
):
    """A single machine credential. Two clicks on "create key" must not both
    read "0 keys" — a second credential nobody meant to issue is a second thing
    to revoke when it leaks. Paid tier (the API is paid-only) narrowed to one."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "paid")
    _set_quota(tenant_id, {"max_api_keys": 1})

    _fire(
        lambda i: client.post("/api/v1/api-keys",
                              json={"name": f"race-{i}", "role": "viewer"},
                              headers=headers),
        n=6,
    )
    count = query_one(
        "SELECT COUNT(*) AS c FROM api_keys WHERE tenant_id = %s", (tenant_id,)
    )["c"]
    assert count <= 1, f"{count} API keys against a ceiling of 1"


def test_the_warehouse_ceiling_holds_under_simultaneous_creates(
    monkeypatch, make_tenant_user_headers, client,
):
    """One warehouse on the free tier. Multi-warehouse transfers — the
    optimizer's whole reason to exist — need a second one, so this is a ceiling
    with a direct commercial meaning."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    headers, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _set_quota(tenant_id, {"max_locations": 2})

    _fire(
        lambda i: client.post("/api/v1/inventory/warehouses",
                              json={"name": f"Bodega-{i}"}, headers=headers),
        n=8,
    )
    count = query_one(
        "SELECT COUNT(*) AS c FROM warehouses WHERE tenant_id = %s", (tenant_id,)
    )["c"]
    assert count <= 2, f"{count} warehouses against a ceiling of 2"


# ── 2. The limiter, which only exists for the concurrent case ────────────────

def test_the_rate_limiter_counts_exactly_under_parallel_hammering(
    monkeypatch, make_tenant_user_headers,
):
    """A limiter is only ever exercised concurrently, and a read-then-insert
    counter over-admits under exactly those conditions.

    Twenty threads against a ceiling of five: at most five may pass. Fewer is
    acceptable (a limiter may be conservative); more means the ceiling is a
    suggestion, which for a machine credential is an open door.
    """
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.auth import api_key_auth

    _, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    _set_tier(tenant_id, "free")
    _set_quota(tenant_id, {"max_api_calls_per_day": 5})

    key_id = f"race-{uuid4().hex[:8]}"
    results = _fire(lambda i: api_key_auth.check_rate(key_id, tenant_id), n=20)

    admitted = sum(1 for r in results if r is True)
    assert admitted <= 5, (
        f"{admitted} of 20 concurrent calls were admitted against a daily ceiling "
        f"of 5 — the limiter reads its counter and writes it in two steps"
    )
    assert admitted >= 1, "the limiter refused everything, including the first call"


def test_the_limiter_fails_open_rather_than_locking_out_every_integration(
    monkeypatch, make_tenant_user_headers,
):
    """The deliberate trade-off, asserted so nobody 'fixes' it by accident: if
    the rate store is unreachable, every customer's nightly sync must keep
    working. A limiter that fails closed takes down every integration in the
    product the moment one table is unavailable."""
    monkeypatch.setattr("backend.config.settings.testing_mode", False)
    from backend.auth import api_key_auth

    _, tenant_id = make_tenant_user_headers(role="analyst", return_tenant_id=True)

    def explode(*_a, **_k):
        raise RuntimeError("rate store is down")

    monkeypatch.setattr(api_key_auth, "_within", explode)
    assert api_key_auth.check_rate("any-key", tenant_id) is True


# ── 3. Isolation and the pool, under simultaneous load ───────────────────────

def test_two_tenants_writing_the_same_sku_names_at_once_never_mix(
    make_tenant_user_headers, client,
):
    """The failure this guards against is not a crash — it is tenant A's buyer
    seeing tenant B's stock. Both tenants write the same twenty SKU codes at the
    same instant, with values that identify their owner."""
    a_headers, a_tenant = make_tenant_user_headers(role="analyst", return_tenant_id=True)
    b_headers, b_tenant = make_tenant_user_headers(role="analyst", return_tenant_id=True)

    def write(i):
        headers, stock = (a_headers, 100) if i % 2 == 0 else (b_headers, 900)
        return client.put(f"/api/v1/inventory/stock/SHARED-{i // 2:02d}",
                          json=_stock(current_stock=stock), headers=headers)

    _fire(write, n=40, workers=20)

    a_values = {float(r["current_stock"]) for r in query(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s", (a_tenant,))}
    b_values = {float(r["current_stock"]) for r in query(
        "SELECT current_stock FROM inventory_stock WHERE tenant_id = %s", (b_tenant,))}

    assert a_values <= {100.0}, f"tenant A holds values it never wrote: {a_values}"
    assert b_values <= {900.0}, f"tenant B holds values it never wrote: {b_values}"


def test_more_simultaneous_requests_than_the_pool_has_connections(
    client, auth_headers,
):
    """The pool is ten connections and does not queue — it raises
    `PoolError("connection pool exhausted")` the moment it is empty. Thirty
    simultaneous reads must therefore be *answered*, every one of them, either
    served or refused in the envelope. What must not happen is a bare exception
    reaching the client, which is what an exhausted pool produces if nothing
    catches it."""
    results = _fire(
        lambda i: client.get("/api/v1/inventory/stock", headers=auth_headers),
        n=30,
    )

    raised = [r for r in results if isinstance(r, Exception)]
    assert not raised, (
        f"{len(raised)} of 30 concurrent reads raised instead of answering: "
        f"{ {type(e).__name__ for e in raised} }"
    )
    codes = set(_statuses(results))
    assert codes <= {200, 429, 503}, (
        f"pool pressure produced {codes} — a saturated pool must degrade, not error"
    )


def test_a_burst_of_logins_does_not_hand_out_a_token_to_the_wrong_account(
    client, registered_user, analyst_user,
):
    """Two identities logging in simultaneously, repeatedly. Any shared mutable
    state in the auth path — a module-level "current user", a reused cursor —
    shows up here as a token minted for the wrong subject."""
    from backend.auth.jwt_handler import decode_token

    people = [registered_user, analyst_user]

    def login(i):
        who = people[i % 2]
        resp = client.post("/api/v1/auth/login",
                           json={"email": who["email"], "password": who["password"]})
        return who["email"], resp

    results = _fire(login, n=20, workers=10)

    for item in results:
        assert not isinstance(item, Exception), f"a concurrent login raised: {item}"
        expected_email, resp = item
        assert resp.status_code == 200, resp.text
        claims = decode_token(resp.json()["data"]["access_token"])
        got = query_one("SELECT email FROM users WHERE id = %s", (claims["sub"],))
        assert got["email"] == expected_email, (
            f"a login as {expected_email} returned a token for {got['email']}"
        )
