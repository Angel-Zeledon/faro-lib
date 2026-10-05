"""The sign-in flow around a provider: state, accounts, handoff.

Decisions, in the order a sign-in meets them:

1. **State lives on the server, single-use, 10 minutes.** `oauth_flows` holds
   the hash of `state` with the PKCE verifier and the nonce. The callback
   consumes the row with `DELETE ... RETURNING`, so a replayed callback finds
   nothing — whichever request comes second loses, even concurrently.

2. **The flow is bound to the browser that started it.** `start` sets an
   HttpOnly cookie with a random value and the row stores its hash; the
   callback must present the same cookie. Without it, somebody could start a
   sign-in with THEIR provider account, stop before the callback and send the
   link to a victim, who would land signed in to the attacker's company and
   upload their data into it (login CSRF). Apple's callback is a cross-site
   POST, so its cookie is `SameSite=None; Secure` — Apple only accepts https
   return URLs anyway.

3. **Who the person is.** First by (provider, subject): a linked identity
   signs in its user even if the email changed at the provider. Otherwise by
   email — and only an email the provider says it VERIFIED. That is the
   industry norm, and the reason it is safe: the provider has proved control
   of the mailbox, which is exactly what our own verification link proves.

   One case needs more than linking. An existing account whose email WE never
   verified may have been registered by somebody who does not own that
   mailbox, waiting for the real owner to arrive ("pre-account hijacking").
   Linking would hand the real owner an account whose password the squatter
   knows. So linking to an unverified account marks it verified AND removes
   its password (`has_password = FALSE`, sessions cut). The owner can set a
   new one through "forgot password", which mails the address they just
   proved. The activity log says which of the two happened.

4. **Tokens never travel in a URL.** The callback ends with a redirect to
   `/auth/callback#code=…` carrying a one-time, 60-second code (hash stored in
   `oauth_handoffs`); the page POSTs it to `/auth/oauth/exchange` and receives
   the access and refresh tokens in the response body. The fragment is never
   sent to any server, so the code does not reach logs or a Referer either.

5. **A refused person is refused like password login is**: suspended or
   pending accounts and expired trial tenants get the same error codes.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.auth.password import hash_password
from backend.auth.social.providers import Identity, SocialAuthError
from backend.db.connection import execute, query_one
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

FLOW_TTL_MINUTES = 10
HANDOFF_TTL_SECONDS = 60
BINDING_COOKIE = "stockai_oauth_binding"


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ── Flow state ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StartedFlow:
    state: str
    nonce: str
    code_verifier: str
    binding: str


def start_flow(provider: str, *, intent: str, terms_accepted: bool) -> StartedFlow:
    flow = StartedFlow(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        # 43–128 chars of the unreserved set, per RFC 7636.
        code_verifier=secrets.token_urlsafe(64),
        binding=secrets.token_urlsafe(32),
    )
    # Expired rows are swept on each start; there is no other writer.
    execute("DELETE FROM oauth_flows WHERE expires_at < NOW()")
    execute(
        """INSERT INTO oauth_flows
               (state_hash, provider, code_verifier, nonce, binding_hash,
                intent, terms_accepted, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (_h(flow.state), provider, flow.code_verifier, flow.nonce, _h(flow.binding),
         intent if intent in ("login", "signup") else "login", bool(terms_accepted),
         datetime.now(timezone.utc) + timedelta(minutes=FLOW_TTL_MINUTES)),
    )
    return flow


def consume_flow(provider: str, state: str | None, binding: str | None) -> dict:
    """The flow row for this callback, deleted in the same statement.

    Every way this can fail is one code, `oauth_state_invalid`: the person's
    next step is identical (start again), and distinguishing "expired" from
    "replayed" would only help someone probing.
    """
    if not state:
        raise SocialAuthError("oauth_state_invalid", "No state on the callback")
    row = query_one(
        "DELETE FROM oauth_flows WHERE state_hash = %s RETURNING *", (_h(state),),
    )
    if not row:
        raise SocialAuthError("oauth_state_invalid", "Unknown or already used state")
    if row["provider"] != provider:
        raise SocialAuthError("oauth_state_invalid", "State belongs to another provider")
    expires = row["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise SocialAuthError("oauth_state_invalid", "State expired")
    if not binding or not secrets.compare_digest(_h(binding), row["binding_hash"]):
        raise SocialAuthError("oauth_state_invalid", "Browser binding missing or wrong")
    return row


def peek_flow_intent(state: str | None) -> str:
    """Where to send an error back to, BEFORE the state is consumed. Read-only."""
    if not state:
        return "login"
    row = query_one("SELECT intent FROM oauth_flows WHERE state_hash = %s", (_h(state),))
    return (row or {}).get("intent") or "login"


# ── Accounts ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resolution:
    user: dict
    is_new_account: bool
    linked_now: bool
    password_removed: bool


def _user_by_identity(identity: Identity) -> dict | None:
    return query_one(
        """SELECT u.* FROM user_identities ui JOIN users u ON u.id = ui.user_id
            WHERE ui.provider = %s AND ui.subject = %s""",
        (identity.provider, identity.subject),
    )


def _link(user: dict, identity: Identity) -> None:
    execute(
        """INSERT INTO user_identities (id, user_id, tenant_id, provider, subject, email, last_used_at)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
        (generate_id("uid"), user["id"], user["tenant_id"], identity.provider,
         identity.subject, (identity.email or "").lower() or None),
    )


def _record(user: dict, action: str, identity: Identity, reason: str | None = None) -> None:
    from backend.activity.events import record_event

    record_event(
        user["tenant_id"], user["id"], action,
        resource=identity.provider,
        details={"provider": identity.provider, "email": identity.email},
        reason=reason,
    )


def record_terms_acceptance(user_id: str) -> bool:
    """Stamp the terms acceptance, when the schema has somewhere to put it.

    The page the person clicked on said "by continuing you accept the Terms
    and the Privacy Policy". The columns belong to the legal workstream
    (`users.terms_accepted_at`, and `terms_version` if it exists); this writes
    them only when present, so it neither depends on that work landing first
    nor invents a column of its own. Returns whether anything was written.
    """
    cols = {
        r["column_name"] for r in _columns("users")
    }
    if "terms_accepted_at" not in cols:
        return False
    version = _current_terms_version()
    if "terms_version" in cols and version:
        execute(
            "UPDATE users SET terms_accepted_at = NOW(), terms_version = %s WHERE id = %s",
            (version, user_id),
        )
    else:
        execute("UPDATE users SET terms_accepted_at = NOW() WHERE id = %s", (user_id,))
    return True


def _columns(table: str) -> list[dict]:
    from backend.db.connection import query

    return query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )


def _current_terms_version() -> str | None:
    """The terms version in force — the same constant password signup and
    trial accounts record (backend/users/terms.py)."""
    from backend.users.terms import TERMS_VERSION
    return TERMS_VERSION


def _company_name_for(identity: Identity) -> str:
    """The new tenant's display name: the person's own name, else the part of
    the address before the @. No words of ours added — it is stored as their
    data and shown back to them, in whatever language they use the app."""
    name = (identity.full_name or "").strip()
    if name:
        return name[:120]
    return (identity.email or "").split("@", 1)[0][:120] or "StockAI"


def _create_account(identity: Identity, *, terms_accepted: bool) -> dict:
    from backend.tenants.service import create_tenant, delete_empty_tenant
    from backend.users import service as user_svc

    tenant = create_tenant(_company_name_for(identity))
    try:
        user = user_svc.create_user(
            tenant_id=tenant["id"],
            email=identity.email,
            # Nobody knows this password, and `has_password = FALSE` below makes
            # `verify_credentials` refuse every attempt regardless.
            password=secrets.token_urlsafe(48),
            role="admin",
            full_name=identity.full_name,
        )
    except Exception:
        delete_empty_tenant(tenant["id"])
        raise
    execute(
        """UPDATE users SET email_verified = TRUE, has_password = FALSE, updated_at = NOW()
            WHERE id = %s""",
        (user["id"],),
    )
    try:
        _link(user, identity)
    except Exception:
        # Same promise signup makes: a tenant with no way in is not left behind.
        execute("DELETE FROM users WHERE id = %s", (user["id"],))
        delete_empty_tenant(tenant["id"])
        raise
    if terms_accepted:
        record_terms_acceptance(user["id"])
    return user_svc.get_user(tenant["id"], user["id"])


def resolve_account(identity: Identity, *, terms_accepted: bool) -> Resolution:
    """Find, link or create the account this identity signs in to."""
    existing = _user_by_identity(identity)
    if existing:
        execute(
            "UPDATE user_identities SET last_used_at = NOW() WHERE provider = %s AND subject = %s",
            (identity.provider, identity.subject),
        )
        return Resolution(existing, False, False, False)

    if not identity.email:
        # Work/school Microsoft accounts often carry no `email` claim.
        raise SocialAuthError(
            "oauth_email_missing", f"{identity.provider} returned no email address",
        )
    if not identity.email_verified:
        raise SocialAuthError(
            "oauth_email_unverified", f"{identity.provider} has not verified this email",
        )

    email = identity.email.lower().strip()
    user = query_one("SELECT * FROM users WHERE email = %s", (email,))
    if user:
        already = query_one(
            "SELECT subject FROM user_identities WHERE user_id = %s AND provider = %s",
            (user["id"], identity.provider),
        )
        if already:
            # This person already linked a DIFFERENT account at this provider.
            # Silently swapping it would move their way in without telling them.
            raise SocialAuthError(
                "oauth_identity_conflict",
                f"User already linked to another {identity.provider} account",
            )
        password_removed = False
        if not user.get("email_verified"):
            # See the module docstring, point 3: an unverified password may
            # belong to somebody who never owned this mailbox.
            execute(
                """UPDATE users
                      SET email_verified = TRUE, has_password = FALSE,
                          hashed_password = %s, sessions_invalid_before = NOW(),
                          status = CASE WHEN status = 'pending_confirmation'
                                        THEN 'active' ELSE status END,
                          updated_at = NOW()
                    WHERE id = %s""",
                (hash_password(secrets.token_urlsafe(48)), user["id"]),
            )
            execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user["id"],))
            password_removed = True
        _link(user, identity)
        _record(
            user, "account.provider_linked", identity,
            reason=("linked_unverified_password_removed" if password_removed
                    else "linked_at_provider_sign_in"),
        )
        fresh = query_one("SELECT * FROM users WHERE id = %s", (user["id"],))
        return Resolution(fresh, False, True, password_removed)

    if not terms_accepted:
        # The button the person pressed was not on a page that stated the
        # terms; an account is not created on their behalf without it.
        raise SocialAuthError("oauth_terms_required", "No terms acceptance for a new account")
    user = _create_account(identity, terms_accepted=terms_accepted)
    _record(user, "account.signed_up_with_provider", identity)
    return Resolution(user, True, False, False)


def check_can_sign_in(user: dict) -> None:
    """Same refusals, same codes, as POST /auth/login."""
    from backend.tenants.service import get_tenant
    from backend.trial.service import is_expired_trial

    if is_expired_trial(get_tenant(user["tenant_id"])):
        raise SocialAuthError("trial_account_expired", "Trial ended")
    status = user.get("status", "active")
    if status == "pending_confirmation":
        raise SocialAuthError("account_pending_confirmation", "Pending confirmation")
    if status != "active":
        raise SocialAuthError("account_not_active", f"Status {status}")


# ── Handoff ──────────────────────────────────────────────────────────────────

def issue_handoff(user: dict, provider: str, is_new_account: bool) -> str:
    code = secrets.token_urlsafe(32)
    execute("DELETE FROM oauth_handoffs WHERE expires_at < NOW()")
    execute(
        """INSERT INTO oauth_handoffs (code_hash, user_id, provider, is_new_account, expires_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (_h(code), user["id"], provider, is_new_account,
         datetime.now(timezone.utc) + timedelta(seconds=HANDOFF_TTL_SECONDS)),
    )
    return code


def consume_handoff(code: str) -> dict | None:
    if not code:
        return None
    return query_one(
        """DELETE FROM oauth_handoffs WHERE code_hash = %s AND expires_at > NOW()
           RETURNING user_id, provider, is_new_account""",
        (_h(code),),
    )
