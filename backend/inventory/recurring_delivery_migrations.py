"""Schema for recurring delivery schedules (see `recurring_delivery_dates.py`
for the date rules and `backend-rs/src/recurring/` for the service).

Python owns the schema; the schedules' routes and the materialiser that turns
them into committed-demand rows are written in Rust.
"""

MIGRATIONS: list[tuple[str, str]] = [
    # A schedule says "`quantity` units of `sku` to `customer` every week /
    # fortnight / half month / month from `start_date` to `end_date`". Rows of
    # `committed_demand` are materialised from it ahead of time, as ordinary
    # commitments marked `source = 'contract'` with `contract_root_id` = this
    # schedule's id, so they are locked like a blanket contract's rows and the
    # unique index on (tenant_id, contract_root_id, sku, contract_release_date)
    # makes the materialisation idempotent.
    #
    # Unlike a blanket contract this ledger is NOT append-only: a schedule is a
    # standing instruction. Editing it revises only the future, still-open
    # commitments it made; fulfilled, cancelled and withdrawn rows are history
    # and are never touched. `revision` counts edits (optimistic concurrency).
    ("create_recurring_delivery_schedules",
     """CREATE TABLE IF NOT EXISTS recurring_delivery_schedules (
         id                     TEXT PRIMARY KEY,
         tenant_id              TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         customer               TEXT NOT NULL,
         reference              TEXT,
         sku                    TEXT NOT NULL,
         warehouse_id           TEXT,
         quantity               DOUBLE PRECISION NOT NULL CHECK (quantity > 0),
         frequency              TEXT NOT NULL
                                CHECK (frequency IN ('weekly', 'fortnightly', 'semimonthly', 'monthly')),
         weekday                INT CHECK (weekday BETWEEN 0 AND 6),
         day_of_month           INT CHECK (day_of_month BETWEEN 1 AND 31),
         start_date             DATE NOT NULL,
         end_date               DATE NOT NULL,
         holiday_dates          JSONB NOT NULL DEFAULT '[]'::jsonb,
         avoid_weekends         BOOLEAN NOT NULL DEFAULT FALSE,
         shift_rule             TEXT NOT NULL DEFAULT 'after'
                                CHECK (shift_rule IN ('skip', 'before', 'after')),
         horizon_days           INT NOT NULL DEFAULT 180
                                CHECK (horizon_days BETWEEN 1 AND 730),
         on_top_of_base         BOOLEAN NOT NULL DEFAULT TRUE,
         note                   TEXT,
         status                 TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'paused', 'cancelled')),
         revision               INT NOT NULL DEFAULT 1 CHECK (revision >= 1),
         created_by             TEXT NOT NULL,
         created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         last_materialised_at   TIMESTAMPTZ,
         last_materialise_error TEXT,
         CHECK (end_date >= start_date)
     )"""),
    ("create_recurring_delivery_schedules_idx",
     "CREATE INDEX IF NOT EXISTS recurring_delivery_schedules_tenant_idx "
     "ON recurring_delivery_schedules (tenant_id, status)"),
]
