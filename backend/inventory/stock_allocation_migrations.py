"""Schema for stock allocation among committed customers.

The routes that read and write these tables are written in Rust
(`backend-rs/src/routes/stock_allocation.rs`); Python owns the schema, like
every other table.

Everything here is ADVISORY DATA. No table below is an input of the semaforo
(they are deliberately NOT in `STATUS_INPUT_TABLES`) and nothing writes
`inventory_stock`: the purchase recommendation keeps planning on
`quantity x probability` of every open commitment, exactly as before. A
reservation says "if the stock on hand is shared like this, this customer gets
this many units"; it never moves a unit.
"""

MIGRATIONS: list[tuple[str, str]] = [
    # Which customers come first when stock is short. `tier` 1 is served first
    # and 9 last; a customer with no row is served at the default tier (5).
    # `customer_key` is the strip + casefold of the name, the key a commitment's
    # free-text customer is matched with.
    ("create_allocation_customer_priorities",
     """CREATE TABLE IF NOT EXISTS allocation_customer_priorities (
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         customer_key  TEXT NOT NULL,
         customer      TEXT NOT NULL,
         tier          INT  NOT NULL CHECK (tier BETWEEN 1 AND 9),
         updated_by    TEXT NOT NULL,
         updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (tenant_id, customer_key)
     )"""),
    # Tiers whose members split what is left proportionally instead of being
    # served earliest-date-first. A tier with no row is NOT fair-share.
    ("create_allocation_tier_policy",
     """CREATE TABLE IF NOT EXISTS allocation_tier_policy (
         tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         tier        INT  NOT NULL CHECK (tier BETWEEN 1 AND 9),
         fair_share  BOOLEAN NOT NULL,
         updated_by  TEXT NOT NULL,
         updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (tenant_id, tier)
     )"""),
    # One row per apply: the policy and the supply it was computed from, so a
    # reservation can always be explained after the inputs have moved on.
    ("create_allocation_runs",
     """CREATE TABLE IF NOT EXISTS allocation_runs (
         id           TEXT PRIMARY KEY,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         sku          TEXT NOT NULL,
         result_hash  TEXT NOT NULL,
         policy       JSONB NOT NULL,
         supply       JSONB NOT NULL,
         created_by   TEXT NOT NULL,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    # What an apply recorded. Units are the commitment's expected units
    # (quantity x probability) in micro-units (1e-6 of a unit) so the arithmetic
    # that produced them is exact. The commitment's quantity, probability and
    # delivery date at apply time are kept so a later edit shows the
    # reservation as stale instead of silently standing for a different order.
    # Rows are never deleted: a re-apply or a release marks them 'released'.
    ("create_stock_reservations",
     """CREATE TABLE IF NOT EXISTS stock_reservations (
         id                          TEXT PRIMARY KEY,
         tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         run_id                      TEXT NOT NULL,
         sku                         TEXT NOT NULL,
         commitment_id               TEXT NOT NULL,
         customer                    TEXT,
         tier                        INT NOT NULL,
         units_micro                 BIGINT NOT NULL CHECK (units_micro > 0),
         reserved_micro              BIGINT NOT NULL CHECK (reserved_micro >= 0),
         short_micro                 BIGINT NOT NULL CHECK (short_micro >= 0),
         commitment_quantity         DOUBLE PRECISION NOT NULL,
         commitment_probability      DOUBLE PRECISION NOT NULL,
         commitment_delivery_date    DATE NOT NULL,
         status                      TEXT NOT NULL DEFAULT 'active'
                                     CHECK (status IN ('active', 'released')),
         created_by                  TEXT NOT NULL,
         created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         released_by                 TEXT,
         released_at                 TIMESTAMPTZ,
         release_reason              TEXT
     )"""),
    # A commitment holds at most one active reservation: two concurrent applies
    # cannot both win.
    ("create_stock_reservations_active_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS stock_reservations_active_uniq "
     "ON stock_reservations (tenant_id, commitment_id) WHERE status = 'active'"),
    ("create_stock_reservations_sku_idx",
     "CREATE INDEX IF NOT EXISTS stock_reservations_sku_idx "
     "ON stock_reservations (tenant_id, sku, status)"),
]
