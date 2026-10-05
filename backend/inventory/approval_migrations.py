"""Schema for the two opt-in enterprise features of the purchasing side.

Kept in its own module (appended to `_MIGRATIONS` by one line in
`backend/db/migrations.py`) so the shared migration list is touched in a single
place.

* Purchase-order approval: rules, one row per approval request, who may approve,
  and the current approval state on the order header.
* Forecast adjustments: an append-only ledger of manual changes to a product's
  forecast for a period.
"""

MIGRATIONS = [
    # ── Purchase-order approval ──────────────────────────────────────────────
    # A rule says: an order worth `threshold` or more needs somebody else's
    # approval. `warehouse` / `supplier_id` narrow it (NULL = any). The creator
    # may approve their own order only while it is worth LESS than
    # `self_approve_below` (NULL = never).
    ("create_po_approval_rules",
     """CREATE TABLE IF NOT EXISTS po_approval_rules (
         id                 TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id          TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         threshold          DOUBLE PRECISION NOT NULL CHECK (threshold > 0),
         warehouse          TEXT,
         supplier_id        TEXT,
         self_approve_below DOUBLE PRECISION,
         active             BOOLEAN NOT NULL DEFAULT TRUE,
         created_by         TEXT NOT NULL,
         created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_po_approval_rules_idx",
     "CREATE INDEX IF NOT EXISTS po_approval_rules_tenant_idx ON po_approval_rules (tenant_id)"),
    # One row per request. Never updated except to record the decision, so the
    # history of an order (asked, rejected, asked again, approved) stays whole.
    ("create_po_approvals",
     """CREATE TABLE IF NOT EXISTS po_approvals (
         id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         po_log_id     TEXT NOT NULL REFERENCES inventory_po_log(id) ON DELETE CASCADE,
         status        TEXT NOT NULL DEFAULT 'requested'
                       CHECK (status IN ('requested', 'approved', 'rejected')),
         amount        DOUBLE PRECISION NOT NULL,
         rule_id       TEXT,
         requested_by  TEXT NOT NULL,
         requested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         request_note  TEXT,
         decided_by    TEXT,
         decided_at    TIMESTAMPTZ,
         comment       TEXT
     )"""),
    ("create_po_approvals_po_idx",
     "CREATE INDEX IF NOT EXISTS po_approvals_po_idx ON po_approvals (po_log_id, requested_at DESC)"),
    # At most ONE open request per order: two clicks (or two tabs) cannot ask
    # twice, and the database says so rather than the code hoping.
    ("create_po_approvals_one_open_idx",
     "CREATE UNIQUE INDEX IF NOT EXISTS po_approvals_one_open_idx "
     "ON po_approvals (po_log_id) WHERE status = 'requested'"),
    # The current state on the order header, so the PO list needs no join.
    # NULL = no approval was ever asked. `approved_amount` is the value the
    # approver saw: an order that grows past it needs asking again.
    ("add_po_log_approval_status",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS approval_status TEXT"),
    ("add_po_log_approved_amount",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS approved_amount DOUBLE PRECISION"),
    ("add_users_can_approve_po",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS can_approve_po BOOLEAN NOT NULL DEFAULT FALSE"),

    # ── Forecast adjustments ─────────────────────────────────────────────────
    # Append-only: a correction is a NEW row that supersedes the old one
    # (`superseded_by`), never an UPDATE of its numbers, so "who changed the
    # forecast, when and why" can always be answered and the value-added
    # measurement stays honest.
    #
    # `pct` is the multiplier the planner applies (+15 -> x1.15). An absolute
    # adjustment is entered as units over the range; `baseline_units` is the
    # unadjusted forecast it was measured against, and `pct` is derived from the
    # two once, at entry, so applying and grading it are the same arithmetic.
    ("create_forecast_adjustments",
     """CREATE TABLE IF NOT EXISTS forecast_adjustments (
         id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id      TEXT NOT NULL,
         sku             TEXT NOT NULL,
         start_date      DATE NOT NULL,
         end_date        DATE NOT NULL,
         mode            TEXT NOT NULL CHECK (mode IN ('percent', 'absolute')),
         value           DOUBLE PRECISION NOT NULL,
         pct             DOUBLE PRECISION NOT NULL,
         baseline_units  DOUBLE PRECISION,
         reason_code     TEXT NOT NULL,
         reason_note     TEXT,
         created_by      TEXT NOT NULL,
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         superseded_by   TEXT,
         superseded_at   TIMESTAMPTZ,
         CHECK (end_date >= start_date)
     )"""),
    ("create_forecast_adjustments_idx",
     "CREATE INDEX IF NOT EXISTS forecast_adjustments_tenant_idx "
     "ON forecast_adjustments (tenant_id, session_id, sku)"),
]
