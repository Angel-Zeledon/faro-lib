"""Schema for cost centers, approval chains and chained purchase-order approval
(see `cost_center_chain_core.py` and `po_chain_service.py`).

Additive only: nothing here changes a row that exists. With no cost center and
no chain a tenant behaves exactly as before.
"""

MIGRATIONS: list[tuple[str, str]] = [
    # A tree of places money is spent from. `code` is the tenant's own label
    # (unique ignoring case). Centers are deactivated, never deleted: an order
    # and a budget keep pointing at them.
    ("create_cost_centers",
     """CREATE TABLE IF NOT EXISTS cost_centers (
         id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         code        TEXT NOT NULL,
         name        TEXT NOT NULL,
         parent_id   TEXT REFERENCES cost_centers(id) ON DELETE RESTRICT,
         active      BOOLEAN NOT NULL DEFAULT TRUE,
         created_by  TEXT NOT NULL,
         created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_cost_centers_code_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS cost_centers_code_uniq "
     "ON cost_centers (tenant_id, lower(code))"),
    ("create_cost_centers_parent_idx",
     "CREATE INDEX IF NOT EXISTS cost_centers_parent_idx ON cost_centers (tenant_id, parent_id)"),
    # An approval chain: `cost_center_id` NULL is the tenant's default chain.
    ("create_approval_chains",
     """CREATE TABLE IF NOT EXISTS approval_chains (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         name           TEXT NOT NULL,
         cost_center_id TEXT REFERENCES cost_centers(id) ON DELETE RESTRICT,
         active         BOOLEAN NOT NULL DEFAULT TRUE,
         created_by     TEXT NOT NULL,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    # One ACTIVE chain per center (and one default): two cannot both claim an
    # order, and the database says so rather than the code hoping.
    ("create_approval_chains_one_active_idx",
     "CREATE UNIQUE INDEX IF NOT EXISTS approval_chains_one_active_idx "
     "ON approval_chains (tenant_id, COALESCE(cost_center_id, '')) WHERE active"),
    # From `min_amount` up the order needs `levels` (JSON: [{kind: role, role} |
    # {kind: users, user_ids}], in order). Bands are replaced as a set.
    ("create_approval_chain_bands",
     """CREATE TABLE IF NOT EXISTS approval_chain_bands (
         id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id  TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         chain_id   TEXT NOT NULL REFERENCES approval_chains(id) ON DELETE CASCADE,
         min_amount DOUBLE PRECISION NOT NULL CHECK (min_amount >= 0),
         levels     JSONB NOT NULL,
         UNIQUE (chain_id, min_amount)
     )"""),
    # The cost center an order's spend is attributed to, and whether it went
    # past a budget when it was created (then the chain's top band decides).
    ("add_po_log_cost_center_id",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS cost_center_id TEXT"),
    ("add_po_log_chain_escalate",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS chain_escalate BOOLEAN NOT NULL DEFAULT FALSE"),
    ("create_po_log_cost_center_idx",
     "CREATE INDEX IF NOT EXISTS inventory_po_log_cost_center_idx "
     "ON inventory_po_log (tenant_id, cost_center_id) WHERE cost_center_id IS NOT NULL"),
    # A chained request keeps a SNAPSHOT of what it was asked under, so editing
    # the chain later never silently changes who an open request waits for.
    ("add_po_approvals_chain_id",
     "ALTER TABLE po_approvals ADD COLUMN IF NOT EXISTS chain_id TEXT"),
    ("add_po_approvals_chain_fingerprint",
     "ALTER TABLE po_approvals ADD COLUMN IF NOT EXISTS chain_fingerprint TEXT"),
    ("add_po_approvals_chain_levels",
     "ALTER TABLE po_approvals ADD COLUMN IF NOT EXISTS chain_levels JSONB"),
    ("add_po_approvals_cost_center_id",
     "ALTER TABLE po_approvals ADD COLUMN IF NOT EXISTS cost_center_id TEXT"),
    # One row per level of a chained request. `po_approvals.status` stays the
    # request's verdict; it becomes 'approved' only when the last level approves.
    ("create_po_approval_steps",
     """CREATE TABLE IF NOT EXISTS po_approval_steps (
         id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         approval_id TEXT NOT NULL REFERENCES po_approvals(id) ON DELETE CASCADE,
         level_no    INT NOT NULL CHECK (level_no >= 1),
         level       JSONB NOT NULL,
         status      TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'rejected')),
         decided_by  TEXT,
         decided_at  TIMESTAMPTZ,
         comment     TEXT,
         UNIQUE (approval_id, level_no)
     )"""),
    # A budget can now be scoped to a cost center (and the ones below it).
    ("allow_purchase_budget_cost_center_scope",
     """DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_constraint
                      WHERE conname = 'purchase_budgets_scope_type_check'
                        AND position('cost_center' in pg_get_constraintdef(oid)) = 0) THEN
            ALTER TABLE purchase_budgets DROP CONSTRAINT purchase_budgets_scope_type_check;
            ALTER TABLE purchase_budgets ADD CONSTRAINT purchase_budgets_scope_type_check
              CHECK (scope_type IN ('company', 'warehouse', 'supplier', 'category', 'cost_center'));
          END IF;
        END $$"""),
]
