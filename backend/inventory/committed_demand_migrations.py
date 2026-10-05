"""Schema for committed demand (see `committed_demand_service.py`)."""

MIGRATIONS: list[tuple[str, str]] = [
    # A commitment is a real customer order (or a promise of one) with a delivery
    # date months or years out. It carries NO session_id on purpose: it is a fact
    # about the business, not an output of any training run, so it must survive a
    # retrain and be visible to every session.
    ("create_committed_demand",
     """CREATE TABLE IF NOT EXISTS committed_demand (
         id                 TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id          TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         sku                TEXT NOT NULL,
         warehouse_id       TEXT,
         delivery_date      DATE NOT NULL,
         quantity           DOUBLE PRECISION NOT NULL CHECK (quantity > 0),
         customer           TEXT,
         probability        DOUBLE PRECISION NOT NULL DEFAULT 1
                            CHECK (probability > 0 AND probability <= 1),
         on_top_of_base     BOOLEAN NOT NULL DEFAULT TRUE,
         status             TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'fulfilled', 'cancelled')),
         note               TEXT,
         created_by         TEXT NOT NULL,
         created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         status_changed_by  TEXT,
         status_changed_at  TIMESTAMPTZ
     )"""),
    ("create_committed_demand_idx",
     "CREATE INDEX IF NOT EXISTS committed_demand_tenant_idx "
     "ON committed_demand (tenant_id, status, sku, delivery_date)"),
]
