"""SCIM 2.0 user provisioning (RFC 7643 / 7644), the subset identity providers use.

    protocol.py   pure: schemas, errors, the filter parser, PATCH application,
                  role mapping, resource rendering. No database.
    tokens.py     the per-tenant bearer token: mint, hash, verify, rotate, revoke
    service.py    users and role groups in the database, the provisioning log
    backend/api/v1/scim.py   the HTTP surface (`/scim/v2/*` and the admin routes)

Decisions that are easy to get wrong, in one place:

1. **The SCIM token is its own credential.** `scim_<id>_<secret>` is neither an
   `sk_live_*` key nor a JWT, so `get_current_user` refuses it on every other
   route (401), and the `/scim/v2` routes accept nothing else. The `scim` tag is
   INTERNAL in `backend/api/public_surface.py`: no API key reaches it either.
   The secret is stored as SHA-256 only and compared with `hmac.compare_digest`.

2. **SCIM rides on SSO.** A token can only be minted while the tenant has a
   company sign-in configured, and every request re-checks that SSO is still
   configured, enabled, and switched on for the installation. Removing the SSO
   configuration revokes the token.

3. **Deprovisioning deactivates; nothing is ever deleted.** `DELETE /Users/{id}`
   and `active: false` set `status = 'inactive'`, stamp
   `sessions_invalid_before` (so every access token already minted dies at
   once, see `backend/auth/guards.py`) and drop the refresh tokens. The user row
   and everything it authored stay. Reactivation is allowed.

4. **Only the tenant's own domains.** A provisioned `userName` must be an e-mail
   on one of the domains of the tenant's SSO configuration - the same rule a
   company sign-in applies - so a leaked token cannot squat arbitrary
   addresses. An address that already belongs to ANOTHER tenant is refused
   (409 uniqueness) and never touched.

5. **Administrators are opt-in.** SSO never makes an admin. SCIM may create,
   promote, demote or deactivate administrators only when the tenant admin
   switched "manage administrators" on for the token; otherwise an admin
   account is read-only to SCIM. Even then the last active administrator with
   access to every warehouse can never be deactivated or demoted (409).

6. **Groups are the three roles.** `/Groups` lists exactly admin / analyst /
   viewer. Adding a member sets that role; removing a member from the admin or
   analyst group drops them to viewer (the floor). Groups cannot be created,
   renamed or deleted.

   Warehouse-scope groups are deliberately NOT mapped. A scope is "these
   warehouses" or NULL = every warehouse, so removing somebody from their last
   warehouse group would either widen their access to everything or need a
   new meaning for "no group". Either is a silent change to what a person sees,
   decided by a group sync nobody reviews. Scopes stay an admin's decision on
   the users screen.

7. **Every write is logged twice, on purpose**: a `scim_events` row (the
   provisioning log the admin screen shows, refusals included) and an activity
   event with actor `scim` (the tenant's audit trail).
"""
