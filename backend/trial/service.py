"""Throwaway trial accounts, handed out from the landing page.

Somebody who wants to look at StockAI before talking to us should not have to
sign up, verify an address and upload a file first. `POST /trial` gives them a
user and a password on the spot, for an account that disappears 24 hours later.
The page that shows the credentials signs them in straight into the existing
one-click demo (`/ventas?demo=1` → `POST /demo/quickstart`), with its progress
screen — so a trial that is never entered never trains anything.

What keeps it from being abused, in order of how much it matters:

* **It cannot reach anybody.** The login is `demo-xxxxxx@stockai.demo` — a TLD
  that does not exist, refused again at the email transport
  (`notifications/email.py::_transport_send`) — and the address is never
  verified, so every action that leaves the tenant (inviting, sending a
  purchase order, notifications) is already closed by
  `require_verified_email`. A trial account is not a way to send mail from our
  domain.
* **It cannot grow.** The `demo` tier (`entitlements/plans.py`) is 30 SKUs, one
  user, one training job at a time and a 5 MB upload.
* **There can only be so many.** A ceiling on live trial accounts and on how
  many are created per hour across the whole installation. The per-address
  limit is a speed bump, not a wall: behind a proxy the address is whatever
  the proxy says it is.
* **It ends.** `trial_ends_at` is set at creation; past it the tenant is read
  only, login refuses it, and `reap_expired_trials` erases it within the hour.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from backend.config import settings
from backend.db.connection import execute, query, query_one
from backend.entitlements.plans import DEMO
from backend.errors import AppError

log = logging.getLogger(__name__)

TRIAL_HOURS = 24

# Not a real TLD, so no message to it can ever be delivered; the transport
# refuses it before trying. It is also what marks a login as a trial login.
TRIAL_EMAIL_DOMAIN = "stockai.demo"

# Installation-wide. Each trial trains the bundled history once, so these are
# what a burst of visitors can cost the server at most.
MAX_LIVE_TRIALS = 100
MAX_TRIALS_PER_HOUR = 30
# Per client address, per day.
MAX_TRIALS_PER_ADDRESS = 3

# No 0/o, 1/l/i: the visitor may copy these by eye.
_LETTERS = "abcdefghjkmnpqrstuvwxyz"
_DIGITS = "23456789"


def is_trial_email(email: str) -> bool:
    return (email or "").strip().lower().endswith("@" + TRIAL_EMAIL_DOMAIN)


def _new_email() -> str:
    suffix = "".join(secrets.choice(_LETTERS + _DIGITS) for _ in range(6))
    return f"demo-{suffix}@{TRIAL_EMAIL_DOMAIN}"


def _new_password() -> str:
    """Readable, and passes `validate_strength` (letters and digits, >= 8)."""
    word = "".join(secrets.choice(_LETTERS) for _ in range(5))
    number = "".join(secrets.choice(_DIGITS) for _ in range(4))
    return f"{word}-{number}"


def _enforce_capacity() -> None:
    if settings.testing_mode:
        return
    live = query_one(
        "SELECT COUNT(*) AS n FROM tenants WHERE tier = %s", (DEMO,),
    )
    recent = query_one(
        """SELECT COUNT(*) AS n FROM tenants
           WHERE tier = %s AND created_at > NOW() - INTERVAL '1 hour'""",
        (DEMO,),
    )
    if (live and live["n"] >= MAX_LIVE_TRIALS) or (
        recent and recent["n"] >= MAX_TRIALS_PER_HOUR
    ):
        raise AppError(
            "trial_capacity_reached",
            "No trial accounts are available right now. Please try again later.",
            status_code=503,
        )


def create_trial_account() -> dict:
    """Create the tenant and its one user. Returns the credentials.

    The password is returned once, here, and never stored in the clear — the
    page that shows it is the only place it exists.
    """
    from backend.tenants.data_export import delete_tenant
    from backend.tenants.service import create_tenant
    from backend.users import service as user_svc

    _enforce_capacity()

    expires_at = datetime.now(timezone.utc) + timedelta(hours=TRIAL_HOURS)
    tenant = create_tenant("Demo")
    tenant_id = tenant["id"]
    execute(
        "UPDATE tenants SET tier = %s, trial_ends_at = %s WHERE id = %s",
        (DEMO, expires_at, tenant_id),
    )

    password = _new_password()
    try:
        # Six random characters collide about once in a billion; if one does,
        # the unique index says so and the next try is a fresh address.
        for attempt in range(3):
            email = _new_email()
            try:
                user = user_svc.create_user(
                    tenant_id=tenant_id, email=email, password=password,
                    role="admin", full_name="Demo",
                )
                break
            except Exception as exc:
                if "email" not in str(exc).lower() or attempt == 2:
                    raise
    except Exception:
        # A tenant nobody can log into is worse than none.
        delete_tenant(tenant_id)
        raise

    log.info("[trial] created tenant=%s user=%s expires=%s",
             tenant_id, user["id"], expires_at.isoformat())
    return {
        "email": email,
        "password": password,
        "expires_at": expires_at.isoformat(),
        "hours": TRIAL_HOURS,
    }


def is_expired_trial(tenant: dict | None) -> bool:
    if not tenant or tenant.get("tier") != DEMO:
        return False
    ends = tenant.get("trial_ends_at")
    if ends is None:
        return False
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return ends <= datetime.now(timezone.utc)


def reap_expired_trials() -> int:
    """Erase every trial tenant past its end. Returns how many went.

    Keyed on the tier AND the date: a tenant somebody moved off `demo` by hand
    (because the visitor became a customer) is never touched, whatever its
    `trial_ends_at` says.

    A trial that wrote to us is kept, read only, until somebody answers: the
    ask lives in `upgrade_requests`, which the erasure would take with it, and
    a lead lost to a cron job is the most expensive row in the database. Once
    the request is no longer `new`, the next sweep takes the tenant.
    """
    from backend.tenants.data_export import delete_tenant

    rows = query(
        """SELECT t.id FROM tenants t
           WHERE t.tier = %s AND t.trial_ends_at IS NOT NULL AND t.trial_ends_at <= NOW()
             AND NOT EXISTS (
                 SELECT 1 FROM upgrade_requests u
                 WHERE u.tenant_id = t.id AND u.status = 'new')""",
        (DEMO,),
    )
    erased = 0
    for row in rows:
        try:
            delete_tenant(row["id"])
            erased += 1
        except Exception as exc:
            log.error("[trial] could not erase tenant=%s: %s", row["id"], exc, exc_info=True)
    if erased:
        log.info("[trial] erased %d expired trial tenant(s)", erased)
    return erased
