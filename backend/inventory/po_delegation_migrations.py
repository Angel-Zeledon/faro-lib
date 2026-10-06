"""Schema for purchase-order approval delegation (a substitute approver).

Kept in its own module, appended to `_MIGRATIONS` by one line in
`backend/db/migrations.py`, like the other enterprise features. Additive only:
a tenant that never delegates has an empty table and an untouched
`po_approvals`.

A delegation says: from `starts_on` to `ends_on` (both inclusive, UTC dates)
`delegate_id` may approve and reject what `delegator_id` could. Whether it is
in force is decided from these dates at decision time; no job ever flips a
flag, so an expired delegation stops working the moment its last day ends.
"""

MIGRATIONS = [
    ("create_po_approval_delegations",
     """CREATE TABLE IF NOT EXISTS po_approval_delegations (
         id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         delegator_id TEXT NOT NULL,
         delegate_id  TEXT NOT NULL,
         starts_on    DATE NOT NULL,
         ends_on      DATE NOT NULL,
         note         TEXT,
         created_by   TEXT NOT NULL,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         revoked_at   TIMESTAMPTZ,
         revoked_by   TEXT,
         CHECK (ends_on >= starts_on),
         CHECK (delegator_id <> delegate_id)
     )"""),
    ("create_po_approval_delegations_delegate_idx",
     "CREATE INDEX IF NOT EXISTS po_approval_delegations_delegate_idx "
     "ON po_approval_delegations (tenant_id, delegate_id)"),
    ("create_po_approval_delegations_delegator_idx",
     "CREATE INDEX IF NOT EXISTS po_approval_delegations_delegator_idx "
     "ON po_approval_delegations (tenant_id, delegator_id)"),
    # A decision taken through a delegation names the person it stood in for
    # and the delegation that allowed it, so "approved by X on behalf of Y" is
    # a fact in the row, not something reconstructed from dates later.
    ("add_po_approvals_decided_on_behalf_of",
     "ALTER TABLE po_approvals ADD COLUMN IF NOT EXISTS decided_on_behalf_of TEXT"),
    ("add_po_approvals_delegation_id",
     "ALTER TABLE po_approvals ADD COLUMN IF NOT EXISTS delegation_id TEXT"),
]
