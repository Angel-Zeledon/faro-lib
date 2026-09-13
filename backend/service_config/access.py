"""Who may look at this deployment's credentials, and who may change them.

There are two different jobs here and they used to be one word, `admin`:

  - **Instance operator** — the person who owns the installation. Their
    DeepSeek key, their Twilio account, their SMTP server. In a source-code
    deployment this is the buyer.
  - **Tenant admin** — the `admin` role inside one tenant. Every tenant that
    signs up has one, including a tenant that signed up five minutes ago.

`require_admin` answers the second question, so it is the wrong guard for the
first: with it, any tenant's admin could rewrite the instance's LLM key, read
the last four characters of every stored secret, and probe the owner's Twilio
account. The panel therefore asks a separate question, answered by
`INSTANCE_ADMIN_EMAILS`, which is environment-only precisely because the list
of people who can rewrite the credentials must not live on the screen where the
credentials are typed.

What a tenant admin keeps: the channels that carry their OWN identity to their
OWN people — the sender their customers see. That is the per-tenant exception,
and it is limited to services the registry marks `tenant_scoped`.
"""

from __future__ import annotations

from fastapi import Depends

from backend.auth.guards import CurrentUser, get_current_user, require_admin
from backend.config import settings
from backend.errors import AppError

# Named so the panel and the docs can point at the same string.
OPERATOR_ENV = "INSTANCE_ADMIN_EMAILS"


def operator_emails() -> list[str]:
    """The configured operators, lowercased. Environment only — never the store.

    Read through `settings` rather than the resolver on purpose: a layer that
    could be written from the panel would let an operator-less deployment grant
    itself an operator, which is the one thing this list exists to prevent.
    """
    raw = settings.instance_admin_emails or []
    if isinstance(raw, str):  # tolerated: a single address with no commas
        raw = [raw]
    return [str(e).strip().lower() for e in raw if str(e).strip()]


def instance_editing_enabled() -> bool:
    """Whether anyone at all may edit instance configuration from the app."""
    return bool(operator_emails())


def _email_of(user: CurrentUser) -> str:
    """The caller's address. One query, and only on this guard's path."""
    from backend.db.connection import query_one

    row = query_one("SELECT email FROM users WHERE id = %s", (user.user_id,))
    return (row["email"] if row and row.get("email") else "").strip().lower()


def is_instance_operator(user: CurrentUser) -> bool:
    """True when this caller owns the installation, not just a tenant in it.

    A machine credential is never an operator: an API key is issued inside a
    tenant, it cannot be verified against a human address, and nothing in the
    published API needs to read the deployment's secrets.
    """
    if user.is_machine or user.role != "admin":
        return False
    allowed = operator_emails()
    if not allowed:
        return False
    return _email_of(user) in allowed


def require_instance_operator(
    user: CurrentUser = Depends(require_admin),
) -> CurrentUser:
    """Guard for everything that reads or writes INSTANCE-scoped configuration.

    Two refusals, two different fixes, so they are two different codes:
    `instance_config_disabled` means the deployment named nobody and the fix is
    an environment variable; `not_instance_operator` means the caller is simply
    not on the list.
    """
    if not operator_emails():
        raise AppError(
            "instance_config_disabled",
            f"No instance operator is configured. Set {OPERATOR_ENV} to the "
            "addresses allowed to manage this deployment's services, then "
            "restart the API.",
            status_code=403,
            params={"env": OPERATOR_ENV},
        )
    if not is_instance_operator(user):
        raise AppError(
            "not_instance_operator",
            "This account is not an operator of this installation.",
            status_code=403,
            params={"env": OPERATOR_ENV},
        )
    return user


def require_tenant_channel_admin(
    user: CurrentUser = Depends(require_admin),
) -> CurrentUser:
    """Guard for a tenant editing its OWN sender identity.

    Deliberately just `admin` plus "not a machine": the scope is the caller's
    own tenant_id, taken from the token and never from the request body, so
    there is nothing here to escalate with.
    """
    if user.is_machine:
        raise AppError(
            "not_instance_operator",
            "API keys cannot manage service configuration.",
            status_code=403,
        )
    return user


def viewer_scope(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Any authenticated human or machine — used only by `/capabilities`,
    which carries booleans and no configuration at all."""
    return user
