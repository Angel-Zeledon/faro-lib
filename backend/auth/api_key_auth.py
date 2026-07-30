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


def actor_id(key_id: str) -> str:
    """The identity a key acts under.

    Deliberately NOT the person who created the key. Attributing a nightly ERP
    sync to whoever clicked "create key" eight months ago puts a name on rows
    that person never touched, and keeps doing it after they leave the company.
    `created_by` columns are free text with no foreign key, so a key can own its
    actions outright and the activity feed can name the integration.
    """
    return f"api_key:{key_id}"
