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

import logging

from fastapi import Depends

from backend.auth.guards import CurrentUser, get_current_user, require_admin
from backend.config import settings
from backend.errors import AppError

log = logging.getLogger(__name__)

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


def sole_tenant_id() -> str | None:
    """The one tenant on this deployment, when there is exactly one.

    This is what makes a VIRGIN install usable, and it is the whole reason the
    function exists. On a fresh deployment `INSTANCE_ADMIN_EMAILS` is empty, so
    a strict reading of "only the listed operators may configure services"
    leaves the panel locked at the exact moment it is the only way to configure
    anything — the buyer is sent back to editing a file and restarting a
    container, which is what the panel was built to end.

    So while the deployment has exactly ONE tenant, that tenant's admins operate
    the installation. The person who installed it is the person who signed up,
    always: they have to, to see whether it works.

    It stops at two. The moment a second company exists, "the only tenant" is no
    longer a statement about ownership, and implicit access would mean whoever
    signed up first can read everyone's credentials. From then on the deployment
    must say who operates it, out loud, in the environment — and the panel warns
    about that while there is still one tenant and time to do it.

    Returns None when there are zero tenants or more than one, and also when the
    question cannot be answered (an unreachable database is not a grant).
    """
    from backend.db.connection import query

    try:
        rows = query("SELECT id FROM tenants ORDER BY created_at LIMIT 2")
    except Exception as exc:  # noqa: BLE001 - a failed lookup grants nothing
        log.warning("Could not determine the sole tenant: %s", exc)
        return None
    if len(rows) != 1:
        return None
    return rows[0]["id"]


def bootstrap_scope() -> str | None:
    """The tenant whose admins operate this installation implicitly, if any.

    Empty when an explicit list exists: a deployment that named its operators
    has answered the question, and an implicit second answer could only widen
    it.
    """
    if operator_emails():
        return None
    return sole_tenant_id()


def instance_editing_enabled() -> bool:
    """Whether anyone at all may edit instance configuration from the app."""
    return bool(operator_emails()) or bootstrap_scope() is not None


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

    Two ways to be one, and the explicit one wins:

      * named in `INSTANCE_ADMIN_EMAILS`; or
      * admin of the only tenant on a deployment that has named nobody — the
        first-run case (see `sole_tenant_id`).
    """
    if user.is_machine or user.role != "admin":
        return False

    allowed = operator_emails()
    if allowed:
        return _email_of(user) in allowed

    scope = bootstrap_scope()
    return scope is not None and scope == user.tenant_id


def require_instance_operator(
    user: CurrentUser = Depends(require_admin),
) -> CurrentUser:
    """Guard for everything that reads or writes INSTANCE-scoped configuration.

    Two refusals, two different fixes, so they are two different codes:
    `instance_config_disabled` means the deployment named nobody and the fix is
    an environment variable; `not_instance_operator` means the caller is simply
    not on the list.
    """
    if not instance_editing_enabled():
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
