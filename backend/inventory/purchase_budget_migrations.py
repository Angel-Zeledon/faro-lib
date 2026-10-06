"""Schema for purchase budgets (see `purchase_budget_service.py`)."""

MIGRATIONS: list[tuple[str, str]] = [
    # A purchasing budget with a cap. The ledger is APPEND-ONLY: every change
    # (new terms, deactivating, reactivating) is a NEW row of the same lineage
    # (`root_id`) with the next `revision`; the row it replaces is stamped
    # `superseded_by`/`superseded_at` and is otherwise never touched. Nothing is
    # deleted except by whole-tenant erasure.
    #
    # `scope_type`   company | warehouse | supplier | category
    # `scope_value`  warehouse id | supplier id | category text; NULL for company
    # `parent_root_id` an optional budget (lineage) this one sits inside: the
    #                money a child may still use is never more than its parent's.
    # `hard_cap`     when set, an order that exceeds the remaining money is
    #                refused unless an administrator overrides with a reason.
    ("create_purchase_budgets",
     """CREATE TABLE IF NOT EXISTS purchase_budgets (
         id              TEXT PRIMARY KEY,
         tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         root_id         TEXT NOT NULL,
         revision        INT NOT NULL CHECK (revision >= 1),
         period_type     TEXT NOT NULL CHECK (period_type IN ('month', 'quarter', 'custom')),
         period_start    DATE NOT NULL,
         period_end      DATE NOT NULL,
         amount          DOUBLE PRECISION NOT NULL CHECK (amount >= 0),
         currency        TEXT NOT NULL,
         scope_type      TEXT NOT NULL
                         CHECK (scope_type IN ('company', 'warehouse', 'supplier', 'category')),
         scope_value     TEXT,
         parent_root_id  TEXT,
         hard_cap        BOOLEAN NOT NULL DEFAULT FALSE,
         active          BOOLEAN NOT NULL DEFAULT TRUE,
         note            TEXT,
         created_by      TEXT NOT NULL,
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         superseded_by   TEXT,
         superseded_at   TIMESTAMPTZ,
         CHECK (period_end >= period_start),
         CHECK ((scope_type = 'company') = (scope_value IS NULL)),
         UNIQUE (tenant_id, root_id, revision)
     )"""),
    ("create_purchase_budgets_tenant_idx",
     "CREATE INDEX IF NOT EXISTS purchase_budgets_tenant_idx "
     "ON purchase_budgets (tenant_id, active, period_start, period_end) "
     "WHERE superseded_by IS NULL"),
    # Exactly one current revision per lineage: two simultaneous edits of the
    # same budget cannot both win.
    ("create_purchase_budgets_current_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS purchase_budgets_current_uniq "
     "ON purchase_budgets (tenant_id, root_id) WHERE superseded_by IS NULL"),
]
