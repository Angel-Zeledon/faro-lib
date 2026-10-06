"""A tenant's SAML configuration (read side), the AuthnRequest, and the sign-in.

The configuration is WRITTEN by the Rust routes (`backend-rs/src/routes/saml.rs`)
and read here. See `backend/auth/saml/__init__.py` for the decisions.
"""

from __future__ import annotations

import base64
import logging
import zlib
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse
from xml.sax.saxutils import quoteattr

from backend.auth.saml import response as saml_response
from backend.auth.saml.xmlsig import XmlSecurityError
from backend.auth.social import flow
from backend.auth.social.providers import SocialAuthError
from backend.auth.sso import service as sso
from backend.config import settings
from backend.db.connection import query_one

log = logging.getLogger(__name__)

PROVIDER_PREFIX = "saml:"


def provider_name(tenant_id: str) -> str:
    """The `user_identities.provider` / `oauth_flows.provider` of a tenant's IdP."""
    return f"{PROVIDER_PREFIX}{tenant_id}"


def _frontend() -> str:
    return settings.frontend_url.rstrip("/")


def acs_url() -> str:
    """The Assertion Consumer Service URL the tenant's IdP administrator
    registers. Byte for byte what the Rust configuration route shows."""
    return f"{_frontend()}/api/v1/auth/saml/acs"


def sp_entity_id(tenant_id: str) -> str:
    """The audience of this tenant's assertions (one per tenant, see __init__)."""
    return f"{_frontend()}/api/v1/auth/saml/sp/{tenant_id}"


def get_row(tenant_id: str) -> dict | None:
    return query_one("SELECT * FROM saml_providers WHERE tenant_id = %s", (tenant_id,))


def active_row_for_domain(domain: str | None) -> dict | None:
    """The SAML provider row a sign-in with this e-mail domain must use, or None
    for every way SAML does not apply (instance switch off, unknown domain,
    provider disabled)."""
    if not domain or not sso.instance_enabled():
        return None
    row = query_one(
        """SELECT p.* FROM sso_domains d
             JOIN saml_providers p ON p.tenant_id = d.tenant_id
            WHERE d.domain = %s AND p.enabled = TRUE""",
        (domain,),
    )
    if not row:
        return None
    if domain not in (row["allowed_domains"] or []):
        return None
    return row


def role_view(row: dict) -> dict:
    """The row in the shape `backend.auth.sso.service` role mapping reads."""
    # The Rust route only ever stores analyst/viewer. A row edited by hand
    # must still never mint an administrator, so the same limit is applied on
    # read: anything else becomes a viewer / is ignored.
    assignable = sso.ASSIGNABLE_ROLES
    default = row["default_role"] if row["default_role"] in assignable else "viewer"
    rules = {g: r for g, r in (row.get("group_roles") or {}).items() if r in assignable}
    return {
        "tenant_id": row["tenant_id"],
        "default_role": default,
        "groups_claim": row.get("groups_attribute"),
        "group_roles": rules,
    }


# ── Starting a sign-in ───────────────────────────────────────────────────────

def request_id_for(started: flow.StartedFlow) -> str:
    """The AuthnRequest ID. An xs:ID must start with a letter or underscore;
    the flow's nonce is random and single-use, and is what the Response's
    InResponseTo is compared with."""
    return request_id_for_nonce(started.nonce)


def build_authn_request(*, request_id: str, sso_url: str, sp_entity: str, acs: str,
                        now: datetime | None = None) -> str:
    instant = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID={quoteattr(request_id)} Version="2.0" IssueInstant="{instant}" '
        f'Destination={quoteattr(sso_url)} '
        'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'AssertionConsumerServiceURL={quoteattr(acs)}>'
        f'<saml:Issuer>{_text(sp_entity)}</saml:Issuer>'
        '<samlp:NameIDPolicy AllowCreate="true"/>'
        '</samlp:AuthnRequest>'
    )


def _text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def redirect_url(sso_url: str, authn_request: str, relay_state: str) -> str:
    """HTTP-Redirect binding: raw DEFLATE, base64, then URL-encoded."""
    comp = zlib.compressobj(wbits=-15)
    packed = comp.compress(authn_request.encode("utf-8")) + comp.flush()
    query = urlencode({"SAMLRequest": base64.b64encode(packed).decode(),
                       "RelayState": relay_state})
    return f"{sso_url}{'&' if '?' in sso_url else '?'}{query}"


def begin(email: str) -> tuple[str, flow.StartedFlow]:
    """The IdP URL to send the browser to, and the flow to bind a cookie to.

    Raises `SocialAuthError("sso_not_available")` for every case where SAML does
    not apply to this address - the caller shows the password form instead.
    """
    row = active_row_for_domain(sso.email_domain(email))
    if not row:
        raise SocialAuthError("sso_not_available", "No SAML sign-in for this e-mail domain")
    parsed = urlparse(row["sso_url"])
    if parsed.scheme != "https" or not parsed.netloc:
        # The Rust route refuses to store anything else; a row edited by hand
        # must not turn the login page into a redirect to somewhere else.
        raise SocialAuthError("saml_config_invalid", "stored sign-on URL is not https")
    tenant_id = row["tenant_id"]
    started = flow.start_flow(provider_name(tenant_id), intent="login", terms_accepted=False)
    authn = build_authn_request(
        request_id=request_id_for(started), sso_url=row["sso_url"],
        sp_entity=sp_entity_id(tenant_id), acs=acs_url(),
    )
    return redirect_url(row["sso_url"], authn, started.state), started


# ── Finishing a sign-in ──────────────────────────────────────────────────────

def peek_tenant(relay_state: str | None) -> str | None:
    """Which tenant a RelayState was issued for, without consuming it."""
    if not relay_state or len(relay_state) > 200:
        return None
    row = query_one("SELECT provider FROM oauth_flows WHERE state_hash = %s",
                    (flow._h(relay_state),))
    prov = (row or {}).get("provider") or ""
    return prov[len(PROVIDER_PREFIX):] if prov.startswith(PROVIDER_PREFIX) else None


def complete(relay_state: str | None, binding: str | None,
             saml_response_b64: str | None) -> tuple[dict, bool]:
    """Everything after the IdP posts back. Returns (user, created_now).

    Raises `SocialAuthError` with a stable code for every refusal. The flow row
    is consumed FIRST, whatever happens next, so a Response can be tried once.
    """
    tenant_id = peek_tenant(relay_state)
    if not tenant_id:
        raise SocialAuthError("oauth_state_invalid", "Unknown, expired or replayed RelayState")
    flow_row = flow.consume_flow(provider_name(tenant_id), relay_state, binding)

    if not sso.instance_enabled():
        raise SocialAuthError("sso_not_available", "SSO switched off on this installation")
    row = get_row(tenant_id)
    if not row or not row["enabled"]:
        raise SocialAuthError("sso_not_available", "Provider missing or disabled")

    expected = saml_response.Expected(
        sp_entity_id=sp_entity_id(tenant_id), acs_url=acs_url(),
        idp_entity_id=row["idp_entity_id"], request_id=request_id_for_nonce(flow_row["nonce"]),
        certificates=list(row["idp_certificates"] or []),
        email_attribute=row.get("email_attribute"),
    )
    try:
        claims = saml_response.validate_response(saml_response_b64, expected)
    except XmlSecurityError as exc:  # certificates unreadable and similar
        raise SocialAuthError(exc.code, exc.detail) from None

    if not claims.email:
        raise SocialAuthError("sso_email_missing", "Assertion carries no e-mail")
    domain = sso.email_domain(claims.email)
    owner = query_one("SELECT tenant_id FROM sso_domains WHERE domain = %s", (domain or "",))
    if (not domain or domain not in (row["allowed_domains"] or [])
            or not owner or owner["tenant_id"] != tenant_id):
        raise SocialAuthError("sso_domain_not_allowed", "E-mail domain not allowed for this tenant")

    view = role_view(row)
    user, created, _ = sso.resolve_user(view, claims, provider=provider_name(tenant_id))
    if user["tenant_id"] != tenant_id:  # unreachable by construction; fail closed
        raise SocialAuthError("sso_account_conflict", "Tenant mismatch")
    flow.check_can_sign_in(user)

    previous = sso.apply_group_role(view, user, claims)
    if previous is not None:
        user = query_one("SELECT * FROM users WHERE id = %s", (user["id"],))
    sso.audit_sign_in(view, user, claims, created, previous)
    return user, created


def request_id_for_nonce(nonce: str) -> str:
    return "_" + nonce
