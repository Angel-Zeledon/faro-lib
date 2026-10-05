"""A tenant's SSO configuration, who it applies to, and what a sign-in does.

See `backend/auth/sso/__init__.py` for the decisions. Everything that reads or
writes `sso_providers`, `sso_domains` and the SSO rows of `user_identities`
lives here; the HTTP surface is `backend/api/v1/sso.py`.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any

from backend.auth.social import flow
from backend.auth.social.providers import SocialAuthError
from backend.auth.sso import oidc
from backend.db.connection import execute, query_one, transaction
from backend.errors import AppError
from backend.service_config import crypto
from backend.service_config.resolver import effective
from backend.utils.ids import generate_id

log = logging.getLogger(__name__)

# What a sign-in may ever grant. `admin` is deliberately absent.
ASSIGNABLE_ROLES = ("analyst", "viewer")

MAX_DOMAINS = 20
MAX_GROUP_RULES = 100
SECRET_MAX_LEN = 1024

# Mailbox providers anybody can register on. A tenant claiming one of these
# would route strangers' sign-ins into its workspace, and enforcing SSO on it
# would lock every other account at that provider out of its password.
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "yahoo.es", "ymail.com", "icloud.com", "me.com",
    "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net",
    "mail.com", "zoho.com", "yandex.com", "qq.com", "163.com",
})

_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def provider_name(tenant_id: str) -> str:
    """The `user_identities.provider` / `oauth_flows.provider` of a tenant's IdP."""
    return f"sso:{tenant_id}"


def instance_enabled() -> bool:
    return bool(effective().enterprise_sso_enabled)


# ── Domains ──────────────────────────────────────────────────────────────────

def email_domain(email: str | None) -> str | None:
    if not isinstance(email, str) or email.count("@") != 1:
        return None
    local, domain = email.strip().lower().split("@")
    if not local or not _DOMAIN_RE.match(domain):
        return None
    return domain


def normalize_domains(raw: list[str]) -> list[str]:
    seen: list[str] = []
    for item in raw:
        d = (item or "").strip().lower().lstrip("@")
        if not _DOMAIN_RE.match(d):
            raise AppError(
                "sso_domain_invalid", "Enter domains like example.com.",
                status_code=422, params={"domain": str(item)[:80]},
            )
        if d in FREE_MAIL_DOMAINS:
            raise AppError(
                "sso_domain_not_allowed",
                "Public mailbox domains cannot be used for company sign-in.",
                status_code=422, params={"domain": d},
            )
        if d not in seen:
            seen.append(d)
    if not seen:
        raise AppError("sso_domain_required", "Add at least one e-mail domain.",
                       status_code=422)
    if len(seen) > MAX_DOMAINS:
        raise AppError("sso_too_many_domains", "Too many domains.", status_code=422,
                       params={"max": MAX_DOMAINS})
    return seen


# ── Reading configuration ────────────────────────────────────────────────────

def get_row(tenant_id: str) -> dict | None:
    return query_one("SELECT * FROM sso_providers WHERE tenant_id = %s", (tenant_id,))


def public_view(row: dict | None) -> dict | None:
    """The configuration as an admin sees it: never the secret, not even masked."""
    if not row:
        return None
    return {
        "issuer": row["issuer"],
        "client_id": row["client_id"],
        "has_client_secret": bool(row["client_secret_enc"]),
        "allowed_domains": list(row["allowed_domains"] or []),
        "default_role": row["default_role"],
        "enforce_sso": bool(row["enforce_sso"]),
        "groups_claim": row["groups_claim"],
        "group_roles": dict(row["group_roles"] or {}),
        "enabled": bool(row["enabled"]),
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _client_secret(row: dict) -> str:
    try:
        return crypto.decrypt_value(row["client_secret_enc"])
    except Exception:  # noqa: BLE001 - key changed or ciphertext damaged
        log.error("[sso] client secret of tenant=%s cannot be decrypted", row["tenant_id"])
        raise SocialAuthError("sso_secret_unreadable", "stored client secret unreadable")


def active_row_for_domain(domain: str | None) -> dict | None:
    """The provider row a sign-in with this e-mail domain must use - or None.

    None for every way SSO does not apply: instance switch off, unknown domain,
    provider disabled. Callers then show email + password, never an error.
    """
    if not domain or not instance_enabled():
        return None
    row = query_one(
        """SELECT p.* FROM sso_domains d
             JOIN sso_providers p ON p.tenant_id = d.tenant_id
            WHERE d.domain = %s AND p.enabled = TRUE""",
        (domain,),
    )
    if not row:
        return None
    # Belt and braces: the table lookup and the provider's own list must agree.
    if domain not in (row["allowed_domains"] or []):
        return None
    return row


def enforced_for(tenant_id: str, email: str | None) -> bool:
    """True when this tenant has made SSO mandatory for this e-mail's domain."""
    domain = email_domain(email)
    row = active_row_for_domain(domain)
    return bool(row and row["tenant_id"] == tenant_id and row["enforce_sso"])


# ── Saving configuration ─────────────────────────────────────────────────────

def _admin_has_signed_in_with_sso(tenant_id: str) -> bool:
    row = query_one(
        """SELECT 1 AS ok FROM user_identities ui JOIN users u ON u.id = ui.user_id
            WHERE ui.provider = %s AND u.tenant_id = %s AND u.role = 'admin'
              AND u.status = 'active' LIMIT 1""",
        (provider_name(tenant_id), tenant_id),
    )
    return bool(row)


def _clean_group_rules(claim: str | None, rules: dict[str, str]) -> tuple[str | None, dict]:
    claim = (claim or "").strip() or None
    cleaned: dict[str, str] = {}
    for group, role in (rules or {}).items():
        g = str(group).strip()
        if not g or len(g) > 200:
            raise AppError("sso_group_invalid", "A group name is empty or too long.",
                           status_code=422)
        if role not in ASSIGNABLE_ROLES:
            # `admin` lands here too, on purpose.
            raise AppError(
                "sso_group_role_invalid",
                "Groups can map to analyst or viewer only; administrators are "
                "never created by sign-in.",
                status_code=422, params={"role": str(role)[:30]},
            )
        cleaned[g] = role
    if len(cleaned) > MAX_GROUP_RULES:
        raise AppError("sso_too_many_group_rules", "Too many group rules.",
                       status_code=422, params={"max": MAX_GROUP_RULES})
    if cleaned and not claim:
        raise AppError("sso_groups_claim_required",
                       "Name the claim that carries the groups.", status_code=422)
    if claim and (len(claim) > 100 or not re.fullmatch(r"[A-Za-z0-9_.:/\-]+", claim)):
        raise AppError("sso_groups_claim_invalid", "That claim name is not valid.",
                       status_code=422)
    return claim, cleaned


def save_config(
    tenant_id: str, admin_user_id: str, *, issuer: str, client_id: str,
    client_secret: str | None, allowed_domains: list[str], default_role: str,
    enforce_sso: bool, groups_claim: str | None, group_roles: dict[str, str],
    enabled: bool,
) -> dict:
    """Validate, run discovery, and store. Every refusal is an `AppError` code."""
    if not instance_enabled():
        raise AppError(
            "sso_instance_disabled",
            "Enterprise sign-in is not enabled on this installation.",
            status_code=409,
        )
    if not crypto.secret_storage_enabled():
        raise AppError(
            "secret_storage_unavailable",
            "Secrets cannot be stored on this installation yet.", status_code=409,
        )
    issuer = (issuer or "").strip()
    client_id = (client_id or "").strip()
    if not issuer or not client_id or len(client_id) > 512:
        raise AppError("sso_config_incomplete", "Issuer and client ID are required.",
                       status_code=422)
    if default_role not in ASSIGNABLE_ROLES:
        raise AppError(
            "sso_default_role_invalid",
            "The default role for new people is analyst or viewer.",
            status_code=422, params={"role": str(default_role)[:30]},
        )
    domains = normalize_domains(allowed_domains)
    claim, rules = _clean_group_rules(groups_claim, group_roles)

    existing = get_row(tenant_id)
    secret = (client_secret or "").strip()
    if not secret and not existing:
        raise AppError("sso_config_incomplete", "The client secret is required.",
                       status_code=422)
    if len(secret) > SECRET_MAX_LEN:
        raise AppError("sso_config_incomplete", "The client secret is too long.",
                       status_code=422)

    # The configuring admin must own one of the domains they claim, with an
    # address we verified. Not DNS-grade proof of ownership (that needs a TXT
    # record check), but it stops a tenant from claiming a company's domain
    # that nobody in it has ever shown a mailbox on.
    admin = query_one(
        "SELECT email, email_verified FROM users WHERE id = %s AND tenant_id = %s",
        (admin_user_id, tenant_id),
    )
    admin_domain = email_domain((admin or {}).get("email"))
    if not admin or not admin.get("email_verified") or admin_domain not in domains:
        raise AppError(
            "sso_domain_not_owned",
            "Include the domain of your own verified e-mail address.",
            status_code=422, params={"domain": admin_domain or ""},
        )

    for d in domains:
        owner = query_one("SELECT tenant_id FROM sso_domains WHERE domain = %s", (d,))
        if owner and owner["tenant_id"] != tenant_id:
            raise AppError("sso_domain_taken",
                           "That domain is already used by another account.",
                           status_code=409, params={"domain": d})

    try:
        found = oidc.discover(issuer)
    except SocialAuthError as exc:
        status = 502 if exc.code == "sso_provider_unreachable" else 422
        raise AppError(exc.code, exc.detail or exc.code, status_code=status)

    issuer_changed = bool(existing and existing["issuer"] != issuer)
    client_changed = bool(existing and existing["client_id"] != client_id)
    if enforce_sso and (issuer_changed or client_changed
                        or not _admin_has_signed_in_with_sso(tenant_id)):
        raise AppError(
            "sso_enforce_needs_admin_sign_in",
            "Before enforcing, an administrator must sign in through the "
            "provider at least once with this configuration.",
            status_code=409,
        )
    if enforce_sso and not enabled:
        raise AppError("sso_enforce_needs_enabled",
                       "Enforcement needs the provider to be enabled.", status_code=422)

    secret_enc = crypto.encrypt_value(secret) if secret else existing["client_secret_enc"]

    with transaction() as conn:
        execute(
            """INSERT INTO sso_providers
                   (tenant_id, issuer, client_id, client_secret_enc,
                    authorization_endpoint, token_endpoint, jwks_uri,
                    token_auth_method, allowed_domains, default_role, enforce_sso,
                    groups_claim, group_roles, enabled, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s)
               ON CONFLICT (tenant_id) DO UPDATE SET
                   issuer = EXCLUDED.issuer, client_id = EXCLUDED.client_id,
                   client_secret_enc = EXCLUDED.client_secret_enc,
                   authorization_endpoint = EXCLUDED.authorization_endpoint,
                   token_endpoint = EXCLUDED.token_endpoint,
                   jwks_uri = EXCLUDED.jwks_uri,
                   token_auth_method = EXCLUDED.token_auth_method,
                   allowed_domains = EXCLUDED.allowed_domains,
                   default_role = EXCLUDED.default_role,
                   enforce_sso = EXCLUDED.enforce_sso,
                   groups_claim = EXCLUDED.groups_claim,
                   group_roles = EXCLUDED.group_roles,
                   enabled = EXCLUDED.enabled,
                   updated_at = NOW(), updated_by = EXCLUDED.updated_by""",
            (tenant_id, issuer, client_id, secret_enc, found.authorization_endpoint,
             found.token_endpoint, found.jwks_uri, found.token_auth_method,
             _json(domains), default_role, bool(enforce_sso), claim, _json(rules),
             bool(enabled), admin_user_id),
            conn=conn,
        )
        execute("DELETE FROM sso_domains WHERE tenant_id = %s", (tenant_id,), conn=conn)
        for d in domains:
            execute("INSERT INTO sso_domains (domain, tenant_id) VALUES (%s, %s)",
                    (d, tenant_id), conn=conn)
        if issuer_changed or client_changed:
            # `sub` means nothing outside its provider: identities minted by the
            # old one must not be read as the new one's. People re-link by
            # verified e-mail on their next sign-in.
            execute("DELETE FROM user_identities WHERE provider = %s",
                    (provider_name(tenant_id),), conn=conn)
    return public_view(get_row(tenant_id))


def _json(value: Any):
    import json
    return json.dumps(value)


def delete_config(tenant_id: str) -> bool:
    with transaction() as conn:
        execute("DELETE FROM sso_domains WHERE tenant_id = %s", (tenant_id,), conn=conn)
        execute("DELETE FROM user_identities WHERE provider = %s",
                (provider_name(tenant_id),), conn=conn)
        row = query_one("SELECT 1 AS ok FROM sso_providers WHERE tenant_id = %s",
                        (tenant_id,), conn=conn)
        execute("DELETE FROM sso_providers WHERE tenant_id = %s", (tenant_id,), conn=conn)
    return bool(row)


# ── Starting a sign-in ───────────────────────────────────────────────────────

def begin(email: str, redirect_uri: str) -> tuple[str, flow.StartedFlow]:
    """The provider URL to send the browser to, and the flow to bind a cookie to.

    Raises `SocialAuthError("sso_not_available")` for every case where SSO does
    not apply to this address - the caller shows the password form instead.
    """
    row = active_row_for_domain(email_domain(email))
    if not row:
        raise SocialAuthError("sso_not_available", "No SSO for this e-mail domain")
    started = flow.start_flow(provider_name(row["tenant_id"]), intent="login",
                              terms_accepted=False)
    url = oidc.authorization_url(
        authorization_endpoint=row["authorization_endpoint"],
        client_id=row["client_id"], redirect_uri=redirect_uri, state=started.state,
        nonce=started.nonce, code_verifier=started.code_verifier,
        login_hint=email.strip().lower(),
    )
    return url, started


# ── Finishing a sign-in ──────────────────────────────────────────────────────

def peek_tenant(state: str | None) -> str | None:
    """Which tenant a state was issued for, without consuming it."""
    if not state:
        return None
    row = query_one("SELECT provider FROM oauth_flows WHERE state_hash = %s",
                    (flow._h(state),))
    prov = (row or {}).get("provider") or ""
    return prov[4:] if prov.startswith("sso:") else None


def role_from_groups(row: dict, claims: dict) -> str | None:
    claim = row.get("groups_claim")
    rules = row.get("group_roles") or {}
    if not claim or not rules:
        return None
    raw = claims.get(claim)
    if isinstance(raw, str):
        groups = [raw]
    elif isinstance(raw, list):
        groups = [g for g in raw if isinstance(g, str)]
    else:
        groups = []
    roles = {rules[g] for g in groups if g in rules}
    for candidate in ASSIGNABLE_ROLES:  # analyst outranks viewer
        if candidate in roles:
            return candidate
    return None


def _link(user: dict, tenant_id: str, subject: str, email: str) -> None:
    execute(
        """INSERT INTO user_identities (id, user_id, tenant_id, provider, subject, email, last_used_at)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
        (generate_id("uid"), user["id"], tenant_id, provider_name(tenant_id), subject, email),
    )


def _create_user(row: dict, claims: oidc.SsoClaims, role: str) -> dict:
    from backend.entitlements.service import enforce_limit, limit_guard
    from backend.users import service as user_svc
    from fastapi import HTTPException

    tenant_id = row["tenant_id"]
    try:
        with limit_guard(tenant_id) as conn:
            enforce_limit(tenant_id, "max_users",
                          user_svc.count_users(tenant_id, conn=conn), conn=conn)
            created = user_svc.create_user(
                tenant_id=tenant_id, email=claims.email,
                # Nobody knows it, and has_password = FALSE below makes the
                # password path refuse every attempt regardless.
                password=secrets.token_urlsafe(48), role=role,
                full_name=claims.full_name,
            )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "PLAN_LIMIT_REACHED":
            raise SocialAuthError("sso_seat_limit_reached", "User ceiling of the plan reached")
        raise
    execute(
        """UPDATE users SET email_verified = TRUE, has_password = FALSE,
                  status = 'active', updated_at = NOW()
            WHERE id = %s AND tenant_id = %s""",
        (created["id"], tenant_id),
    )
    return user_svc.get_user(tenant_id, created["id"])


def resolve_user(row: dict, claims: oidc.SsoClaims) -> tuple[dict, bool, str | None]:
    """The tenant's user for these claims: (user, created_now, previous_role).

    Order: a linked identity; else the same e-mail inside THIS tenant; else a
    new person - unless the e-mail already belongs to another tenant, which is
    never touched or reused.
    """
    from backend.auth.password import hash_password
    from backend.users import service as user_svc

    tenant_id = row["tenant_id"]
    name = provider_name(tenant_id)
    mapped = role_from_groups(row, claims.raw)
    wanted_role = mapped or row["default_role"]

    linked = query_one(
        """SELECT u.* FROM user_identities ui JOIN users u ON u.id = ui.user_id
            WHERE ui.provider = %s AND ui.subject = %s AND u.tenant_id = %s""",
        (name, claims.subject, tenant_id),
    )
    if linked:
        # The e-mail is only what the address says today; the identity is the
        # subject. But the address must STILL be on the tenant's domains (checked
        # by the caller), so a person moved to another company stops here.
        execute("UPDATE user_identities SET last_used_at = NOW(), email = %s "
                "WHERE provider = %s AND subject = %s", (claims.email, name, claims.subject))
        return linked, False, None

    existing = query_one("SELECT * FROM users WHERE email = %s", (claims.email,))
    if existing:
        if existing["tenant_id"] != tenant_id:
            raise SocialAuthError("sso_account_conflict",
                                  "E-mail belongs to an account outside this tenant")
        if query_one("SELECT 1 AS ok FROM user_identities WHERE user_id = %s AND provider = %s",
                     (existing["id"], name)):
            # Same person, a different subject at the IdP: not a silent swap.
            raise SocialAuthError("sso_identity_conflict",
                                  "User already linked to another identity")
        if not existing.get("email_verified"):
            # Same pre-account-hijacking rule as social login: a password nobody
            # proved they own the mailbox for is dropped when the provider does.
            execute(
                """UPDATE users SET email_verified = TRUE, has_password = FALSE,
                          hashed_password = %s, sessions_invalid_before = NOW(),
                          status = CASE WHEN status = 'pending_confirmation'
                                        THEN 'active' ELSE status END,
                          updated_at = NOW()
                    WHERE id = %s""",
                (hash_password(secrets.token_urlsafe(48)), existing["id"]),
            )
            execute("DELETE FROM refresh_tokens WHERE user_id = %s", (existing["id"],))
        _link(existing, tenant_id, claims.subject, claims.email)
        return user_svc.get_user(tenant_id, existing["id"]), False, None

    try:
        user = _create_user(row, claims, wanted_role)
    except SocialAuthError:
        raise
    except Exception:
        # Two first sign-ins racing on the unique e-mail: the loser reads the
        # winner's row instead of failing.
        raced = query_one("SELECT * FROM users WHERE email = %s AND tenant_id = %s",
                          (claims.email, tenant_id))
        if not raced:
            raise
        user = raced
    _link(user, tenant_id, claims.subject, claims.email)
    return user, True, None


def apply_group_role(row: dict, user: dict, claims: oidc.SsoClaims) -> str | None:
    """Re-map the role from the groups claim. Returns the previous role if it
    changed. Never touches an administrator and never grants one."""
    if not (row.get("groups_claim") and row.get("group_roles")):
        return None
    if user["role"] == "admin":
        return None
    wanted = role_from_groups(row, claims.raw) or row["default_role"]
    if wanted == user["role"] or wanted not in ASSIGNABLE_ROLES:
        return None
    execute("UPDATE users SET role = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
            (wanted, user["id"], user["tenant_id"]))
    # Tokens already minted carry the old role for up to 15 minutes; the
    # refresh token re-reads the role, so cut the sessions to make it prompt.
    execute("DELETE FROM refresh_tokens WHERE user_id = %s", (user["id"],))
    return user["role"]


def complete(state: str | None, binding: str | None, code: str | None,
             error: str | None, redirect_uri: str) -> tuple[dict, bool]:
    """Everything after the provider redirects back. Returns (user, created_now).

    Raises `SocialAuthError` with a stable code for every refusal. The state is
    consumed FIRST, whatever happens next.
    """
    tenant_id = peek_tenant(state)
    if not tenant_id:
        # Unknown, expired, replayed or somebody else's state: one code.
        raise SocialAuthError("oauth_state_invalid", "Unknown or already used state")
    flow_row = flow.consume_flow(provider_name(tenant_id), state, binding)

    if error:
        raise SocialAuthError(
            "sso_cancelled" if error in ("access_denied", "user_cancelled_authorize")
            else "sso_provider_error", error[:100],
        )
    if not instance_enabled():
        raise SocialAuthError("sso_not_available", "SSO switched off on this installation")
    row = get_row(tenant_id)
    if not row or not row["enabled"]:
        raise SocialAuthError("sso_not_available", "Provider missing or disabled")
    if not code:
        raise SocialAuthError("sso_provider_error", "No code on the callback")

    id_token = oidc.exchange_code(
        token_endpoint=row["token_endpoint"], auth_method=row["token_auth_method"],
        client_id=row["client_id"], client_secret=_client_secret(row), code=code,
        redirect_uri=redirect_uri, code_verifier=flow_row["code_verifier"],
    )
    claims = oidc.verify(
        id_token, issuer=row["issuer"], client_id=row["client_id"],
        jwks_uri=row["jwks_uri"], nonce=flow_row["nonce"],
    )
    if not claims.email:
        raise SocialAuthError("sso_email_missing", "ID token carries no e-mail")
    if not claims.email_verified:
        raise SocialAuthError("sso_email_unverified", "Provider has not verified this e-mail")
    domain = email_domain(claims.email)
    owner = query_one("SELECT tenant_id FROM sso_domains WHERE domain = %s", (domain or "",))
    if (not domain or domain not in (row["allowed_domains"] or [])
            or not owner or owner["tenant_id"] != tenant_id):
        raise SocialAuthError("sso_domain_not_allowed", "E-mail domain not allowed for this tenant")

    user, created, _ = resolve_user(row, claims)
    if user["tenant_id"] != tenant_id:  # unreachable by construction; fail closed
        raise SocialAuthError("sso_account_conflict", "Tenant mismatch")
    flow.check_can_sign_in(user)

    previous = apply_group_role(row, user, claims)
    if previous is not None:
        user = query_one("SELECT * FROM users WHERE id = %s", (user["id"],))
    _audit(row, user, claims, created, previous)
    return user, created


def _audit(row: dict, user: dict, claims: oidc.SsoClaims, created: bool,
           previous_role: str | None) -> None:
    from backend.activity.events import record_event

    tenant_id = row["tenant_id"]
    if created:
        record_event(tenant_id, user["id"], "account.sso_user_created",
                     resource=user["id"],
                     details={"email": user["email"], "role": user["role"]})
    if previous_role is not None:
        record_event(tenant_id, user["id"], "account.sso_role_mapped",
                     resource=user["id"],
                     details={"email": user["email"], "role": user["role"],
                              "previous_role": previous_role},
                     reason="mapped_from_identity_provider_groups")
    record_event(tenant_id, user["id"], "account.sso_sign_in", resource=user["id"],
                 details={"email": user["email"]})


def record_refusal(tenant_id: str | None, code: str, email: str | None = None) -> None:
    """A refused sign-in is visible to the tenant's admins, not only to a log."""
    if not tenant_id:
        return
    from backend.activity.events import record_event

    record_event(tenant_id, "system", "account.sso_sign_in_refused",
                 details={"email": email}, reason="sso_sign_in_refused",
                 reason_params={"code": code}, status="error")
