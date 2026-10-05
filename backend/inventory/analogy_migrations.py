"""Schema for forecast-by-analogy (see `analogy_service.py`)."""

MIGRATIONS: list[tuple[str, str]] = [
    # The ledger. Append-only: a row is never edited or deleted. Undoing one
    # stamps `reverted_by`/`reverted_at`; defining it again later is a new row.
    # `superseded_at` is stamped ONCE, by the status computation, the first time
    # the product turns out to have a trained forecast of its own: from then on
    # the analogy no longer applies and the row says when that happened.
    # Keyed by tenant (a new product is a fact about the catalogue, not about one
    # session), so it serves whichever session is active.
    ("create_sku_analogies",
     """CREATE TABLE IF NOT EXISTS sku_analogies (
         id                    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         new_sku               TEXT NOT NULL,
         reference_skus        JSONB NOT NULL,
         scale_factor          DOUBLE PRECISION NOT NULL DEFAULT 1.0,
         start_date            DATE,
         note                  TEXT,
         created_by            TEXT NOT NULL,
         created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         reverted_by           TEXT,
         reverted_at           TIMESTAMPTZ,
         superseded_at         TIMESTAMPTZ,
         superseded_session_id TEXT,
         CHECK (scale_factor >= 0.1 AND scale_factor <= 10)
     )"""),
    ("create_sku_analogies_idx",
     "CREATE INDEX IF NOT EXISTS sku_analogies_tenant_idx "
     "ON sku_analogies (tenant_id, new_sku)"),
    # One live analogy per product: two simultaneous creates cannot both win.
    ("create_sku_analogies_active_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS sku_analogies_active_uniq "
     "ON sku_analogies (tenant_id, new_sku) WHERE reverted_at IS NULL"),
]
