"""Enterprise single sign-on: one OpenID Connect provider per tenant.

    oidc.py      talks to the provider (discovery, code exchange, ID token)
    service.py   the tenant's configuration, domain routing, accounts, roles

Decisions that are easy to get wrong, in one place:

1. **A domain belongs to one tenant** (`sso_domains.domain` is a primary key).
   The work e-mail typed on the login page resolves to that tenant, and the ID
   token's e-mail must be on one of ITS allowed domains. A person is only ever
   created or signed in inside the tenant that owns their domain.

2. **Identity is (issuer-scoped provider, subject)**, stored in `user_identities`
   under the provider name `sso:<tenant_id>`. The tenant is in the name because
   `sub` is only unique per provider: without it two tenants' IdPs that both
   issue `sub=1001` would be the same identity.

3. **Never an administrator by SSO.** New people get the tenant's default role
   (analyst or viewer) or what a group maps to; a mapping to `admin` is refused
   when it is configured. An existing admin's role is never touched by a
   sign-in.

4. **Enforcement cannot lock a tenant out.** It may only be switched on after an
   admin of the tenant has signed in through the provider (proof the
   configuration works and that an admin has a way in), and it is suspended
   while the instance switch is off.

5. **The session is the app's own**: the callback ends in the same one-time
   handoff code social login uses, and `/auth/oauth/exchange` mints the same
   JWT + refresh token a password login does.
"""
