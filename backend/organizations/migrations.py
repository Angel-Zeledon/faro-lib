"""Schema for the organization hierarchy (see `backend/organizations/__init__.py`).

Additive: with no link created nothing reads or writes these tables.
"""

MIGRATIONS: list[tuple[str, str]] = [
    # One row per attempt to join a subsidiary to a holding.
    #
    # `pending`  the parent minted a one-time code (only its SHA-256 is stored);
    #            `child_tenant_id` is NULL until somebody redeems it.
    # `active`   the child's own administrator redeemed the code; the code hash
    #            is cleared. Grants may exist only on an active link.
    # `revoked`  ended by `revoked_side` (parent, child, or system for a tenant
    #            erasure). Kept as the record of who ended it, never reactivated:
    #            a new link needs a new handshake.
    #
    # `label` is the PARENT's own name for the subsidiary: it is what the parent
    # sees and what the activity feed shows, so no side has to be told the other
    # side's real name more than the redeem screen needs.
    ("create_org_links",
     """CREATE TABLE IF NOT EXISTS org_links (
         id               TEXT PRIMARY KEY,
         parent_tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         child_tenant_id  TEXT REFERENCES tenants(id) ON DELETE CASCADE,
         label            TEXT NOT NULL,
         status           TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'active', 'revoked')),
         code_hash        TEXT,
         code_expires_at  TIMESTAMPTZ,
         created_by       TEXT NOT NULL,
         created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         accepted_by      TEXT,
         accepted_at      TIMESTAMPTZ,
         revoked_by       TEXT,
         revoked_at       TIMESTAMPTZ,
         revoked_side     TEXT CHECK (revoked_side IN ('parent', 'child', 'system')),
         CHECK (child_tenant_id IS NULL OR child_tenant_id <> parent_tenant_id),
         CHECK (status <> 'active' OR child_tenant_id IS NOT NULL),
         CHECK (status <> 'active' OR code_hash IS NULL),
         CHECK (status <> 'pending'
                OR (child_tenant_id IS NULL AND code_hash IS NOT NULL
                    AND code_expires_at IS NOT NULL)),
         CHECK (status <> 'revoked' OR (revoked_at IS NOT NULL AND revoked_side IS NOT NULL))
     )"""),
    ("create_org_links_parent_idx",
     "CREATE INDEX IF NOT EXISTS org_links_parent_idx ON org_links (parent_tenant_id, status)"),
    ("create_org_links_child_idx",
     "CREATE INDEX IF NOT EXISTS org_links_child_idx ON org_links (child_tenant_id) "
     "WHERE child_tenant_id IS NOT NULL"),
    # A subsidiary has at most ONE live parent: two holdings can never both read it.
    ("create_org_links_one_live_parent",
     "CREATE UNIQUE INDEX IF NOT EXISTS org_links_one_live_parent "
     "ON org_links (child_tenant_id) WHERE status = 'active'"),
    ("create_org_links_code_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS org_links_code_uniq "
     "ON org_links (code_hash) WHERE code_hash IS NOT NULL"),
    # Who in the PARENT may read the child through the link. Deleting a row is the
    # revocation (the activity feed is the record), so there is nothing dormant a
    # later reactivation could bring back.
    ("create_org_link_grants",
     """CREATE TABLE IF NOT EXISTS org_link_grants (
         link_id    TEXT NOT NULL REFERENCES org_links(id) ON DELETE CASCADE,
         user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         granted_by TEXT NOT NULL,
         granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (link_id, user_id)
     )"""),
    ("create_org_link_grants_user_idx",
     "CREATE INDEX IF NOT EXISTS org_link_grants_user_idx ON org_link_grants (user_id)"),
]
