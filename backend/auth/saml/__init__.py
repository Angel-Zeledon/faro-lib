"""Enterprise single sign-on over SAML 2.0: the sibling of `backend/auth/sso/` (OIDC).

    xmlsig.py    hardened XML parsing and enveloped-signature verification
    response.py  the SAML rules a Response must satisfy (the trust decision)
    service.py   the tenant's configuration, the AuthnRequest, finishing a sign-in

The tenant model is the OIDC one on purpose (read `backend/auth/sso/__init__.py`):
a domain belongs to one tenant (`sso_domains`, shared), people are created just
in time and are NEVER administrators, roles come from an attribute mapping
limited to analyst/viewer, and "require SSO" can only be switched on after an
admin of the tenant has signed in through it. What differs:

1. **SP-initiated only.** `GET /auth/saml/start` builds an AuthnRequest whose id
   is stored (single-use, 10 minutes, bound to the browser by a cookie) in the
   same `oauth_flows` table OIDC uses. A Response must answer that id. An
   unsolicited, IdP-initiated Response is refused.
2. **Per-tenant SP entity id.** `<frontend>/api/v1/auth/saml/sp/<tenant_id>` is
   the audience, so an assertion minted for one tenant's connection cannot be
   replayed into another's. The ACS URL is shared: `<frontend>/api/v1/auth/saml/acs`.
3. **Identity** is (`saml:<tenant_id>`, NameID), in `user_identities`.
4. **One protocol per tenant.** Saving a SAML provider while an OIDC one exists
   (or the reverse) is refused: two enforcement rules for one domain have no
   good answer.
5. **Where this code lives.** The configuration routes (`/auth/saml/config`,
   `/auth/saml/sp-metadata`) are Rust (`backend-rs/src/routes/saml.rs`) and have
   NO Python twin. The sign-in endpoints and the assertion validation are
   Python because they end in session issuance and because XML-signature
   verification could not be shown identical in two languages. Python is
   also the owner of the schema and enforces "require SSO" on the password and
   social login paths.
"""
