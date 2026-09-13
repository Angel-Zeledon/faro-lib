"""Machine credentials: the `sk_live_*` half of authentication.

A person presents a JWT minted at login. An integration presents a key it was
handed once and stored in its own configuration. Both arrive in the same
`Authorization: Bearer` header, and both have to end up as the same
`CurrentUser` — every route in this API is written against that object, and not
one of them should have to learn what an API key is.

This module owns the database side of that: hashing, lookup, expiry and the
`last_used` stamp. The HTTP semantics (what a bad key answers) live in
`backend.auth.guards`, which is also the only place that builds a CurrentUser.
"""

import hashlib
from typing import Optional

from backend.db.connection import execute, query_one

KEY_PREFIX = "sk_live_"

# `last_used` is a convenience for the customer ("is this key still in use?"),
# not an audit log. The statement still runs on every request, but the WHERE
# clause makes it match no row — and therefore write nothing and log nothing to
# the WAL — except once per window. What that buys is a column that stays
# useful without charging a real row update to every call an integration makes.
LAST_USED_THROTTLE = "1 minute"


def hash_key(raw: str) -> str:
    """The stored form of a key.

    SHA-256 with no salt on purpose: the input is 32 bytes of `secrets`
    randomness, so there is no dictionary to attack and no rainbow table to
    build — and an unsalted digest is what makes the lookup a single indexed
    equality instead of a scan over every key in the table.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


def looks_like_api_key(credential: str) -> bool:
    """Whether to try the key path at all.

    Cheap and prefix-based so a JWT never costs a database round trip, and a
    malformed key never reaches the JWT decoder to be reported as a bad token.
    """
    return credential.startswith(KEY_PREFIX)


def resolve(credential: str) -> Optional[dict]:
    """The key's row, or None if it cannot authenticate.

    None covers every reason equally — unknown, deleted, expired — because the
    caller must not be able to tell those apart. A key that once existed and a
    key that never did are the same 401.
    """
    row = query_one(
        """SELECT id, tenant_id, role, expires_at
             FROM api_keys
            WHERE key_hash = %s
              AND (expires_at IS NULL OR expires_at > NOW())""",
        (hash_key(credential),),
    )
    return dict(row) if row else None


def touch(key_id: str) -> None:
    """Record that the key was used, at most once per throttle window.

    Written as one conditional UPDATE rather than read-then-write: two
    concurrent requests from the same integration would otherwise both read a
    stale timestamp and both write, which is the exact stampede the throttle
    exists to prevent.
    """
    execute(
        f"""UPDATE api_keys
               SET last_used = NOW()
             WHERE id = %s
               AND (last_used IS NULL OR last_used < NOW() - INTERVAL '{LAST_USED_THROTTLE}')""",
        (key_id,),
    )


# How many calls one key may make per minute. Chosen for the job the API exists
# to do — a nightly ERP push and the polling around it — not to be generous: an
# integration that needs more than this per minute is looping, and a loop with a
# valid key is exactly what nothing currently stops.
#
# It used to be a per-tier number (60 / 120 / unlimited) with this constant as
# the floor under them. There is one plan now, so this IS the policy: one
# ceiling, the same for everybody, living in the module that enforces it.
RATE_MAX_PER_MINUTE = 120

RATE_WINDOW_SECONDS = 60


DAY_WINDOW_SECONDS = 86_400


def check_rate(key_id: str, tenant_id: str | None = None) -> bool:
    """Whether this key may make one more call now; records it when it may.

    Two windows, and a call has to clear both:

    - **Per minute**, the same for everybody. It exists to stop a loop, not to
      sell anything.
    - **Per day**, only when the tenant's plan sets `max_api_calls_per_day` —
      which is the free tier. A nightly ERP push and the polling around it fit
      inside it; an integration that reads all day does not, and that is the
      difference the tiers are actually selling. Pass `tenant_id` to have it
      checked; without it only the per-minute window applies (the callers that
      exercise the limiter directly do not know a tenant).

    Reuses `auth_rate_events`, the same table the login endpoints use, keyed by
    `apikey:<id>` and `apikeyday:<id>`. A second table would have been a second
    definition of "a window", with its own pruning to forget.

    Fails OPEN on a database problem, deliberately. This runs on every
    authenticated machine call: if the rate store is unreachable, refusing every
    integration in the product is a far worse outcome than briefly not counting.
    The customer's nightly sync must not go down because a limiter cannot write.
    """
    from backend.db.connection import query_one as _query_one, transaction
    try:
        daily = _daily_ceiling(tenant_id)
        # Counting and then inserting in two steps is how a limiter admits more
        # than its ceiling: twenty simultaneous calls against a ceiling of five
        # let SIXTEEN through, because each read a counter none of the others
        # had written yet (measured 2026-08-22). A machine credential — the one
        # that runs unattended, in a cron, with retries — is precisely what
        # arrives in parallel, so the ceiling has to be decided under a lock.
        #
        # The lock is keyed on the KEY, not the tenant: two integrations
        # belonging to the same customer never wait on each other, and the
        # section it protects is three short statements.
        with transaction() as conn:
            _query_one("SELECT pg_advisory_xact_lock(hashtext(%s))",
                       (f"ratelimit:{key_id}",), conn=conn)
            if not _within(f"apikey:{key_id}", RATE_MAX_PER_MINUTE,
                           RATE_WINDOW_SECONDS, conn=conn):
                return False
            if daily is not None and not _within(f"apikeyday:{key_id}", daily,
                                                 DAY_WINDOW_SECONDS, conn=conn):
                return False
            # Recorded once both windows agreed: a call refused by the daily
            # ceiling must not also consume a slot in the minute it was refused
            # in. Both inserts commit with the lock, so the next caller in line
            # counts them.
            execute("INSERT INTO auth_rate_events (key) VALUES (%s)",
                    (f"apikey:{key_id}",), conn=conn)
            if daily is not None:
                execute("INSERT INTO auth_rate_events (key) VALUES (%s)",
                        (f"apikeyday:{key_id}",), conn=conn)
        return True
    except Exception:
        return True


def _within(bucket: str, ceiling: int, window_secs: int, conn=None) -> bool:
    """Whether `bucket` is under `ceiling` over the last `window_secs`.

    Checks only. Recording is `check_rate`'s job and happens after BOTH windows
    have agreed, so a request refused by the daily ceiling does not also burn a
    slot in the minute it was refused in. `conn` is the locked transaction from
    `check_rate` — the count has to be read there, not on a connection that
    cannot see the writes the lock is protecting.
    """
    execute(
        "DELETE FROM auth_rate_events WHERE key = %s AND created_at < NOW() - make_interval(secs => %s)",
        (bucket, window_secs), conn=conn,
    )
    row = query_one("SELECT COUNT(*) AS n FROM auth_rate_events WHERE key = %s",
                    (bucket,), conn=conn)
    used = int(row["n"]) if row else 0
    return used < ceiling


def _daily_ceiling(tenant_id: str | None) -> int | None:
    """The tenant's daily call ceiling, or None when it has none (paid tier, or
    a per-tenant quota override that says so)."""
    if not tenant_id:
        return None
    from backend.entitlements.service import tenant_limits
    tenant = query_one(
        "SELECT tier, quota FROM tenants WHERE id = %s", (tenant_id,)
    )
    if tenant is None:
        return None
    return tenant_limits(dict(tenant))["max_api_calls_per_day"]


def actor_id(key_id: str) -> str:
    """The identity a key acts under.

    Deliberately NOT the person who created the key. Attributing a nightly ERP
    sync to whoever clicked "create key" eight months ago puts a name on rows
    that person never touched, and keeps doing it after they leave the company.
    `created_by` columns are free text with no foreign key, so a key can own its
    actions outright and the activity feed can name the integration.
    """
    return f"api_key:{key_id}"
