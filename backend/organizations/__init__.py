"""Organization hierarchy: a holding with subsidiary tenants (2026-10-06).

WHO owns what. The schema lives here (Python owns every table); the routes and
the consolidated queries are written in Rust (`backend-rs/src/routes/org*.rs`),
so this package carries only what Python itself must honour:

* the migrations (`migrations.py`);
* whole-tenant erasure and the data export (`service.py`), called from
  `backend/tenants/data_export.py`;
* dropping a person's grants when their account is deactivated or deleted, so
  a later reactivation can never silently give the access back (`service.py`).

THE GRANT MODEL (the rules the Rust code and the tests enforce; fail closed)

1. A tenant is NEVER reachable from another tenant by default. Reach exists
   only as an `org_links` row with `status = 'active'`, and it is created by a
   handshake that needs an administrator on BOTH sides: the parent's admin
   mints a one-time code, the child's admin redeems it from the child's own
   session. A parent cannot link a tenant that did not agree, and the child
   tenant is always the redeeming caller's own tenant, never a request field.
2. A link alone shows nothing. Each parent user needs an `org_link_grants`
   row (the parent's admin adds it, one person at a time). Admin role is not a
   grant: the admin grants themself like anybody else.
3. One level only. A tenant is at most one live child, and a tenant that is a
   live parent cannot be a child (and the other way round). A child belongs to
   at most one parent, so two holdings can never both read it.
4. Read-only and consolidated: the only things a grant opens are the four
   consolidated reads. No route writes into a child, an `sk_live_*` key or MCP
   never reaches them (they are internal routes), and a child never sees its
   siblings or the parent's data.
5. Either side can end the link at any moment; ending it deletes every grant.
"""
