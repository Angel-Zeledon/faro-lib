"""
Idempotent schema migrations — safe to run on every startup.

The Spanish->English rename block runs first (see _SPANISH_SWEEP), then the
base schema (tenants, users, sessions, datasets, …): the latter lets a
brand-new/empty database be bootstrapped from scratch, and the
incremental ALTER/CREATE migrations that follow reference these tables via FK.
All statements are CREATE TABLE IF NOT EXISTS, so they are no-ops on databases
that already have the schema (e.g. the original Supabase instance). Columns
added later live in the incremental section, not here.
"""
import logging

from backend.db.connection import execute

log = logging.getLogger(__name__)

def _rename_column(table: str, old: str, new: str) -> str:
    """
    Idempotent `RENAME COLUMN`. Postgres has no `IF EXISTS` for this, and a
    second run would otherwise abort with "column does not exist", so the rename
    is guarded on both sides: the old name must still be there AND the new name
    must not be. That makes the statement a no-op both on an already-renamed
    database and on a freshly created one (where the base schema already used
    the new name).
    """
    return f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = '{table}' AND column_name = '{old}')
        AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = '{table}' AND column_name = '{new}')
        THEN
            ALTER TABLE {table} RENAME COLUMN {old} TO {new};
        END IF;
    END $$"""


def _rename_table(old: str, new: str) -> str:
    """Idempotent `ALTER TABLE ... RENAME TO`, guarded the same way."""
    return f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.tables
                    WHERE table_name = '{old}')
        AND NOT EXISTS (SELECT 1 FROM information_schema.tables
                    WHERE table_name = '{new}')
        THEN
            ALTER TABLE {old} RENAME TO {new};
        END IF;
    END $$"""


# ─────────────────────────────────────────────────────────────────────────────
# Spanish → English schema sweep.
#
# This block MUST run before everything else. The base schema and the
# incremental ALTERs below now declare the ENGLISH names, and they are all
# "IF NOT EXISTS": on a database that still has the Spanish columns, letting
# them run first would ADD an empty English column next to the populated
# Spanish one and the rename would then be skipped — silent data loss.
# Renaming first means the later statements correctly find the column present
# and no-op. Every statement here is idempotent, so this is safe on a brand-new
# database too (nothing to rename) and on one already swept (nothing left).
# ─────────────────────────────────────────────────────────────────────────────
_COLUMN_RENAMES: list[tuple[str, str, str]] = [
    ("inventory_stock", "stock_actual", "current_stock"),
    ("inventory_stock", "stock_minimo", "min_stock"),
    ("inventory_stock", "lead_time_dias", "lead_time_days"),
    ("inventory_stock", "costo_unitario", "unit_cost"),
    ("inventory_stock", "proveedor", "supplier"),
    ("inventory_stock", "notas", "notes"),
    ("inventory_stock", "precio_venta", "sale_price"),
    ("inventory_stock", "categoria", "category"),
    ("inventory_stock", "marca", "brand"),
    ("inventory_stock", "unidad_medida", "unit_of_measure"),
    ("inventory_stock", "codigo_barras", "barcode"),
    ("inventory_stock", "bodega", "warehouse"),
    ("inventory_snapshots", "stock_actual", "current_stock"),
    ("inventory_po_log", "skus_pedir_ya", "skus_order_now"),
    ("inventory_po_log", "skus_pedir_pronto", "skus_order_soon"),
    ("inventory_po_items", "proveedor", "supplier"),
    ("inventory_po_items", "cantidad_recomendada", "recommended_qty"),
    ("inventory_po_items", "cantidad_final", "final_qty"),
    ("inventory_po_items", "cantidad_recibida", "received_qty"),
    ("inventory_po_items", "costo_unitario", "unit_cost"),
    ("inventory_po_items", "bodega", "warehouse"),
    ("suppliers", "lead_time_dias", "lead_time_days"),
    ("sku_suppliers", "lead_time_dias", "lead_time_days"),
    ("supplier_lead_time_obs", "proveedor", "supplier"),
    # inventory_mermas is renamed to inventory_shrinkage just below; the column
    # renames are expressed against the NEW table name because the table rename
    # is ordered first.
    ("inventory_shrinkage", "bodega", "warehouse"),
    ("inventory_shrinkage", "costo_unitario", "unit_cost"),
    ("inventory_shrinkage", "costo_total", "total_cost"),
]

_SPANISH_SWEEP = (
    [("rename_table_inventory_mermas", _rename_table("inventory_mermas", "inventory_shrinkage"))]
    + [
        (f"rename_{table}_{old}", _rename_column(table, old, new))
        for table, old, new in _COLUMN_RENAMES
    ]
    + [
        # Indexes and constraints keep their own names after a rename, so the
        # Spanish ones are renamed explicitly. `ALTER INDEX ... RENAME TO` is
        # a no-op-safe guard on pg_class.
        ("rename_inventory_mermas_indexes",
         """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'inventory_mermas_tenant_idx') THEN
                 ALTER INDEX inventory_mermas_tenant_idx RENAME TO inventory_shrinkage_tenant_idx;
             END IF;
             IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'inventory_mermas_sku_idx') THEN
                 ALTER INDEX inventory_mermas_sku_idx RENAME TO inventory_shrinkage_sku_idx;
             END IF;
             -- The primary key index was missed by the original sweep: ALTER
             -- TABLE ... RENAME leaves the PK index on its old name, and unlike
             -- the two above it was never listed here. It is the last Spanish
             -- object name left in the schema.
             IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'inventory_mermas_pkey') THEN
                 ALTER INDEX inventory_mermas_pkey RENAME TO inventory_shrinkage_pkey;
             END IF;
           END $$"""),
        ("rename_inventory_stock_bodega_constraint",
         """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM pg_constraint
                         WHERE conname = 'inventory_stock_tenant_sku_bodega_key') THEN
                 ALTER TABLE inventory_stock
                   RENAME CONSTRAINT inventory_stock_tenant_sku_bodega_key
                   TO inventory_stock_tenant_sku_warehouse_key;
             END IF;
           END $$"""),
        # The event-multiplier scope is a stored VALUE, not a column, so it needs
        # a data update. The CHECK constraint has to be dropped before the rows
        # can be rewritten, then re-added over the English vocabulary.
        #
        # The vocabulary listed here must stay in sync with the widening
        # migration further down ('family', PENDIENTES #6): this block runs on
        # EVERY startup and re-adds the constraint, so leaving 'family' out
        # makes it fail on any database that already stores family-scoped rows.
        ("migrate_event_multiplier_scope_categoria",
         """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables
                         WHERE table_name = 'inventory_event_multipliers') THEN
                 ALTER TABLE inventory_event_multipliers
                   DROP CONSTRAINT IF EXISTS inventory_event_multipliers_scope_check;
                 UPDATE inventory_event_multipliers
                    SET scope = 'category' WHERE scope = 'categoria';
                 ALTER TABLE inventory_event_multipliers
                   ADD CONSTRAINT inventory_event_multipliers_scope_check
                   CHECK (scope IN ('sku', 'family', 'category'));
             END IF;
           END $$"""),
    ]
)

_BASE_SCHEMA = [
    ("base_tenants",
     """CREATE TABLE IF NOT EXISTS tenants (
         id         TEXT PRIMARY KEY,
         name       TEXT NOT NULL,
         slug       TEXT UNIQUE NOT NULL,
         status     TEXT NOT NULL DEFAULT 'active',
         quota      JSONB NOT NULL DEFAULT '{}',
         settings   JSONB NOT NULL DEFAULT '{}',
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("base_users",
     """CREATE TABLE IF NOT EXISTS users (
         id              TEXT PRIMARY KEY,
         tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         email           TEXT UNIQUE NOT NULL,
         full_name       TEXT,
         role            TEXT NOT NULL DEFAULT 'analyst',
         hashed_password TEXT NOT NULL,
         email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
         status          TEXT NOT NULL DEFAULT 'active',
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("base_refresh_tokens",
     """CREATE TABLE IF NOT EXISTS refresh_tokens (
         id         BIGSERIAL PRIMARY KEY,
         user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         tenant_id  TEXT NOT NULL,
         hash       TEXT NOT NULL,
         expires_at TIMESTAMPTZ NOT NULL,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("base_pw_change_codes",
     """CREATE TABLE IF NOT EXISTS pw_change_codes (
         id         TEXT PRIMARY KEY,
         user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         tenant_id  TEXT NOT NULL,
         code_hash  TEXT NOT NULL,
         expires_at TIMESTAMPTZ NOT NULL,
         purpose    TEXT NOT NULL DEFAULT 'change',
         used       BOOLEAN NOT NULL DEFAULT FALSE,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("base_sessions",
     """CREATE TABLE IF NOT EXISTS sessions (
         id            TEXT PRIMARY KEY,
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         name          TEXT NOT NULL,
         description   TEXT,
         status        TEXT NOT NULL DEFAULT 'DRAFT',
         pipeline_step TEXT NOT NULL DEFAULT 'upload',
         created_by    TEXT,
         created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         tags          JSONB NOT NULL DEFAULT '[]',
         version       INT NOT NULL DEFAULT 1,
         dataset_id    TEXT,
         last_job_id   TEXT
     )"""),
    ("base_datasets",
     """CREATE TABLE IF NOT EXISTS datasets (
         id                TEXT PRIMARY KEY,
         tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         name              TEXT NOT NULL,
         original_filename TEXT,
         file_type         TEXT,
         file_path         TEXT,
         size_bytes        BIGINT NOT NULL DEFAULT 0,
         row_count         INT,
         column_count      INT,
         uploaded_by       TEXT,
         uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("base_session_configs",
     """CREATE TABLE IF NOT EXISTS session_configs (
         session_id     TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
         tenant_id      TEXT NOT NULL,
         dataset_ref    JSONB,
         inspection     JSONB,
         columns_cfg    JSONB,
         features_cfg   JSONB,
         models_cfg     JSONB,
         validation_cfg JSONB,
         business_cfg   JSONB,
         forecast_cfg   JSONB,
         updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("base_session_results",
     """CREATE TABLE IF NOT EXISTS session_results (
         session_id      TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
         tenant_id       TEXT NOT NULL,
         training_result JSONB,
         forecasts       JSONB,
         updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("base_training_logs",
     """CREATE TABLE IF NOT EXISTS training_logs (
         id         BIGSERIAL PRIMARY KEY,
         tenant_id  TEXT NOT NULL,
         session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
         job_id     TEXT,
         message    TEXT NOT NULL,
         logged_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
]

_MIGRATIONS = _SPANISH_SWEEP + _BASE_SCHEMA + [
    ("add_last_login_at",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ"),
    ("add_pending_email",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_email TEXT"),
    ("add_pw_change_codes_purpose",
     "ALTER TABLE pw_change_codes ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'change'"),
    ("create_user_permissions",
     """CREATE TABLE IF NOT EXISTS user_permissions (
         id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         tenant_id  TEXT NOT NULL,
         permission TEXT NOT NULL,
         granted_at TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (user_id, permission)
     )"""),
    ("create_documents",
     """CREATE TABLE IF NOT EXISTS documents (
         id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id     TEXT NOT NULL,
         uploaded_by   TEXT NOT NULL,
         name          TEXT NOT NULL,
         original_name TEXT,
         file_path     TEXT NOT NULL,
         file_type     TEXT NOT NULL,
         file_size     BIGINT NOT NULL DEFAULT 0,
         page_count    INT,
         status        TEXT NOT NULL DEFAULT 'PENDING',
         error         TEXT,
         chunk_count   INT,
         uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         indexed_at    TIMESTAMPTZ
     )"""),
    ("create_documents_tenant_idx",
     "CREATE INDEX IF NOT EXISTS documents_tenant_idx ON documents (tenant_id, uploaded_at DESC)"),
    ("create_api_keys",
     """CREATE TABLE IF NOT EXISTS api_keys (
         id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id  TEXT NOT NULL,
         name       TEXT NOT NULL,
         key_hash   TEXT NOT NULL UNIQUE,
         last_used  TIMESTAMPTZ,
         created_at TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_api_keys_tenant_idx",
     "CREATE INDEX IF NOT EXISTS api_keys_tenant_idx ON api_keys (tenant_id)"),
    ("create_webhooks",
     """CREATE TABLE IF NOT EXISTS webhooks (
         id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id  TEXT NOT NULL,
         url        TEXT NOT NULL,
         events     TEXT[] NOT NULL,
         secret     TEXT NOT NULL,
         created_at TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_webhooks_tenant_idx",
     "CREATE INDEX IF NOT EXISTS webhooks_tenant_idx ON webhooks (tenant_id)"),
    ("create_scheduled_jobs",
     """CREATE TABLE IF NOT EXISTS scheduled_jobs (
         id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id  TEXT NOT NULL,
         session_id TEXT NOT NULL,
         cron_expr  TEXT NOT NULL,
         last_run   TIMESTAMPTZ,
         next_run   TIMESTAMPTZ NOT NULL,
         enabled    BOOLEAN DEFAULT TRUE,
         created_at TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_scheduled_jobs_idx",
     "CREATE INDEX IF NOT EXISTS scheduled_jobs_next_idx ON scheduled_jobs (next_run) WHERE enabled = TRUE"),
    ("create_forecast_overrides",
     """CREATE TABLE IF NOT EXISTS forecast_overrides (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         session_id     TEXT NOT NULL,
         sku            TEXT NOT NULL,
         date           DATE NOT NULL,
         original_value FLOAT NOT NULL,
         override_value FLOAT NOT NULL,
         reason         TEXT,
         created_by     TEXT NOT NULL,
         created_at     TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (session_id, sku, date)
     )"""),
    ("create_accuracy_snapshots",
     """CREATE TABLE IF NOT EXISTS accuracy_snapshots (
         id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id  TEXT NOT NULL,
         session_id TEXT NOT NULL,
         sku        TEXT NOT NULL,
         date       DATE NOT NULL,
         forecasted FLOAT NOT NULL,
         actual     FLOAT,
         mae        FLOAT,
         wape       FLOAT,
         created_at TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (session_id, sku, date)
     )"""),
    ("create_accuracy_snapshots_idx",
     "CREATE INDEX IF NOT EXISTS accuracy_session_idx ON accuracy_snapshots (session_id, tenant_id)"),
    ("create_inventory_stock",
     """CREATE TABLE IF NOT EXISTS inventory_stock (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         sku            TEXT NOT NULL,
         display_name   TEXT,
         current_stock   FLOAT NOT NULL DEFAULT 0,
         min_stock   FLOAT NOT NULL DEFAULT 0,
         lead_time_days INT   NOT NULL DEFAULT 15,
         unit_cost FLOAT,
         moq            FLOAT NOT NULL DEFAULT 1,
         supplier      TEXT,
         notes          TEXT,
         updated_at     TIMESTAMPTZ DEFAULT NOW(),
         created_at     TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (tenant_id, sku)
     )"""),
    ("create_inventory_stock_idx",
     "CREATE INDEX IF NOT EXISTS inventory_stock_tenant_idx ON inventory_stock (tenant_id, sku)"),
    ("create_inventory_snapshots",
     """CREATE TABLE IF NOT EXISTS inventory_snapshots (
         id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id    TEXT NOT NULL,
         sku          TEXT NOT NULL,
         current_stock FLOAT NOT NULL,
         recorded_at  TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_inventory_snapshots_idx",
     "CREATE INDEX IF NOT EXISTS inventory_snapshots_sku_idx ON inventory_snapshots (tenant_id, sku, recorded_at DESC)"),
    ("create_inventory_events",
     """CREATE TABLE IF NOT EXISTS inventory_events (
         id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id    TEXT NOT NULL,
         name         TEXT NOT NULL,
         start_date   DATE NOT NULL,
         end_date     DATE NOT NULL,
         multiplier   FLOAT NOT NULL DEFAULT 1.0,
         notes        TEXT,
         created_at   TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_inventory_events_idx",
     "CREATE INDEX IF NOT EXISTS inventory_events_tenant_idx ON inventory_events (tenant_id, start_date)"),
    ("create_inventory_po_log",
     """CREATE TABLE IF NOT EXISTS inventory_po_log (
         id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id       TEXT NOT NULL,
         session_id      TEXT NOT NULL,
         generated_at    TIMESTAMPTZ DEFAULT NOW(),
         sku_count       INT NOT NULL DEFAULT 0,
         total_units     FLOAT NOT NULL DEFAULT 0,
         total_value     FLOAT,
         skus_order_now   INT NOT NULL DEFAULT 0,
         skus_order_soon INT NOT NULL DEFAULT 0
     )"""),
    ("create_inventory_po_log_idx",
     "CREATE INDEX IF NOT EXISTS po_log_tenant_idx ON inventory_po_log (tenant_id, generated_at DESC)"),
    # Adoption metrics on the PO header: how many recommendations StockAI made vs.
    # how many the buyer actually approved / modified / rejected. Lets us prove
    # value ("you followed 8 of 10") instead of just counting downloads.
    ("add_po_log_suggested_count",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS suggested_count INT NOT NULL DEFAULT 0"),
    ("add_po_log_approved_count",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS approved_count INT NOT NULL DEFAULT 0"),
    ("add_po_log_modified_count",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS modified_count INT NOT NULL DEFAULT 0"),
    ("add_po_log_rejected_count",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS rejected_count INT NOT NULL DEFAULT 0"),
    # Per-line record of every recommendation in a PO, with the buyer's decision.
    # recommended_qty = what StockAI suggested; final_qty = what the buyer
    # kept; status ∈ approved | modified | rejected. Rejected lines are stored
    # too (not in the order) so adoption rate is measurable.
    ("create_inventory_po_items",
     """CREATE TABLE IF NOT EXISTS inventory_po_items (
         id                   TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         po_log_id            TEXT NOT NULL REFERENCES inventory_po_log(id) ON DELETE CASCADE,
         tenant_id            TEXT NOT NULL,
         sku                  TEXT NOT NULL,
         display_name         TEXT,
         supplier            TEXT,
         signal               TEXT,
         recommended_qty FLOAT NOT NULL DEFAULT 0,
         final_qty       FLOAT NOT NULL DEFAULT 0,
         unit_cost       FLOAT,
         status               TEXT NOT NULL DEFAULT 'approved',
         created_at           TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_inventory_po_items_log_idx",
     "CREATE INDEX IF NOT EXISTS po_items_log_idx ON inventory_po_items (po_log_id)"),
    ("create_inventory_po_items_sku_idx",
     "CREATE INDEX IF NOT EXISTS po_items_sku_idx ON inventory_po_items (tenant_id, sku)"),
    ("add_warehouse_to_inventory_po_items",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS warehouse TEXT NOT NULL DEFAULT 'principal'"),
    ("create_suppliers",
     """CREATE TABLE IF NOT EXISTS suppliers (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         name           TEXT NOT NULL,
         email          TEXT,
         phone          TEXT,
         whatsapp       TEXT,
         lead_time_days INT  NOT NULL DEFAULT 15,
         lead_time_std  INT  NOT NULL DEFAULT 3,
         payment_terms  TEXT,
         notes          TEXT,
         active         BOOLEAN DEFAULT TRUE,
         created_at     TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (tenant_id, name)
     )"""),
    ("create_suppliers_idx",
     "CREATE INDEX IF NOT EXISTS suppliers_tenant_idx ON suppliers (tenant_id)"),
    ("create_sku_suppliers",
     """CREATE TABLE IF NOT EXISTS sku_suppliers (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         sku            TEXT NOT NULL,
         supplier_id    TEXT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
         is_primary     BOOLEAN DEFAULT TRUE,
         unit_cost      FLOAT,
         moq            FLOAT DEFAULT 1,
         lead_time_days INT,
         notes          TEXT,
         created_at     TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (tenant_id, sku, supplier_id)
     )"""),
    ("create_sku_suppliers_idx",
     "CREATE INDEX IF NOT EXISTS sku_suppliers_sku_idx ON sku_suppliers (tenant_id, sku)"),
    ("add_product_type_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS product_type TEXT NOT NULL DEFAULT 'finished_good'"),
    ("create_bom_items",
     """CREATE TABLE IF NOT EXISTS bom_items (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         parent_sku     TEXT NOT NULL,
         child_sku      TEXT NOT NULL,
         quantity       FLOAT NOT NULL DEFAULT 1.0,
         unit           TEXT,
         notes          TEXT,
         created_at     TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (tenant_id, parent_sku, child_sku)
     )"""),
    ("create_bom_items_idx",
     "CREATE INDEX IF NOT EXISTS bom_items_parent_idx ON bom_items (tenant_id, parent_sku)"),
    ("create_bom_items_child_idx",
     "CREATE INDEX IF NOT EXISTS bom_items_child_idx ON bom_items (tenant_id, child_sku)"),
    ("add_service_level_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS service_level FLOAT NOT NULL DEFAULT 0.95"),
    ("add_sale_price_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS sale_price FLOAT"),
    ("add_category_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS category TEXT"),
    ("add_brand_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS brand TEXT"),
    ("add_unit_of_measure_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS unit_of_measure TEXT"),
    ("add_barcode_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS barcode TEXT"),
    ("create_warehouses",
     """CREATE TABLE IF NOT EXISTS warehouses (
         id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id  TEXT NOT NULL,
         name       TEXT NOT NULL,
         is_default BOOLEAN NOT NULL DEFAULT FALSE,
         created_at TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (tenant_id, name)
     )"""),
    ("add_warehouse_to_inventory_stock",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS warehouse TEXT NOT NULL DEFAULT 'principal'"),
    ("drop_inventory_stock_tenant_sku_unique",
     "ALTER TABLE inventory_stock DROP CONSTRAINT IF EXISTS inventory_stock_tenant_id_sku_key"),
    ("add_inventory_stock_tenant_sku_warehouse_unique",
     """DO $$ BEGIN
         IF NOT EXISTS (
           SELECT 1 FROM pg_constraint WHERE conname = 'inventory_stock_tenant_sku_warehouse_key'
         ) THEN
           ALTER TABLE inventory_stock
             ADD CONSTRAINT inventory_stock_tenant_sku_warehouse_key UNIQUE (tenant_id, sku, warehouse);
         END IF;
       END $$"""),
    ("create_jobs",
     """CREATE TABLE IF NOT EXISTS jobs (
         id           TEXT PRIMARY KEY,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
         created_by   TEXT NOT NULL,
         status       TEXT NOT NULL DEFAULT 'QUEUED',
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         started_at   TIMESTAMPTZ,
         completed_at TIMESTAMPTZ,
         progress     JSONB NOT NULL DEFAULT '{}',
         error        TEXT,
         worker_id    TEXT
     )"""),
    ("create_jobs_tenant_idx",
     "CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON jobs (tenant_id)"),
    ("create_jobs_session_idx",
     "CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs (session_id)"),
    # The daily training ceiling counts one tenant's jobs created today.
    ("create_jobs_tenant_created_idx",
     "CREATE INDEX IF NOT EXISTS idx_jobs_tenant_created ON jobs (tenant_id, created_at)"),
    ("create_jobs_status_idx",
     "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status)"),
    ("create_chats",
     """CREATE TABLE IF NOT EXISTS chats (
         id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id       TEXT NOT NULL,
         user_id         TEXT NOT NULL,
         session_id      TEXT REFERENCES sessions(id) ON DELETE SET NULL,
         title           TEXT NOT NULL DEFAULT 'New Chat',
         is_favorite     BOOLEAN NOT NULL DEFAULT FALSE,
         data_sources    TEXT[] NOT NULL DEFAULT '{}',
         last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         message_count   INT NOT NULL DEFAULT 0,
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_chats_tenant_user_idx",
     "CREATE INDEX IF NOT EXISTS idx_chats_tenant_user_ts ON chats (tenant_id, user_id, last_message_at DESC)"),
    ("create_chat_messages",
     """CREATE TABLE IF NOT EXISTS chat_messages (
         id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         chat_id         TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
         tenant_id       TEXT NOT NULL,
         role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
         content         TEXT NOT NULL,
         source          TEXT,
         retrieved_count INT,
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_chat_messages_chat_idx",
     "CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_created ON chat_messages (chat_id, created_at)"),
    # A message the person saved as a favorite: NULL = not starred, otherwise when
    # it was starred (the Favorites list is ordered by it). Additive, nullable.
    ("add_chat_messages_starred_at",
     "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS starred_at TIMESTAMPTZ"),
    ("create_chat_messages_starred_idx",
     "CREATE INDEX IF NOT EXISTS idx_chat_messages_starred ON chat_messages (tenant_id, starred_at) "
     "WHERE starred_at IS NOT NULL"),
    ("add_pw_change_codes_attempts",
     "ALTER TABLE pw_change_codes ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0"),
    ("create_auth_rate_events",
     """CREATE TABLE IF NOT EXISTS auth_rate_events (
         key        TEXT NOT NULL,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_auth_rate_events_idx",
     "CREATE INDEX IF NOT EXISTS idx_auth_rate_events_key_created ON auth_rate_events (key, created_at)"),
    ("add_users_whatsapp_number",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS whatsapp_number TEXT"),
    # ── PO reception (feature 1.4): close the purchase loop ──────────────────
    # reception_status: pending | received | partial | not_received
    ("add_po_log_reception_status",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS reception_status TEXT NOT NULL DEFAULT 'pending'"),
    ("add_po_log_received_at",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ"),
    ("add_po_log_received_by",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS received_by TEXT"),
    ("add_po_items_received_qty",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS received_qty FLOAT"),
    # Real lead-time observations per supplier, learned from PO receptions.
    # Keyed by supplier NAME (po lines carry the free-text supplier field).
    ("create_supplier_lead_time_obs",
     """CREATE TABLE IF NOT EXISTS supplier_lead_time_obs (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         supplier      TEXT NOT NULL,
         po_log_id      TEXT NOT NULL REFERENCES inventory_po_log(id) ON DELETE CASCADE,
         lead_time_days FLOAT NOT NULL,
         observed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_supplier_lead_time_obs_idx",
     "CREATE INDEX IF NOT EXISTS slto_tenant_prov_idx ON supplier_lead_time_obs (tenant_id, supplier)"),
    # ── ROI monthly evolution (feature 1.5): capital freed from overstock ────
    # One row per tenant per month, taken by a scheduled job on the 1st.
    # No historical backfill — the metric only exists from here forward.
    ("create_inventory_overstock_snapshots",
     """CREATE TABLE IF NOT EXISTS inventory_overstock_snapshots (
         id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id       TEXT NOT NULL,
         session_id      TEXT NOT NULL,
         overstock_value FLOAT NOT NULL,
         recorded_at     TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_inventory_overstock_snapshots_idx",
     "CREATE INDEX IF NOT EXISTS overstock_snapshots_tenant_idx ON inventory_overstock_snapshots (tenant_id, recorded_at DESC)"),
    # Data Alignment Wizard (granularity/resampling): the user's chosen
    # strategy ("native" vs "resample") and target frequency. The
    # reconciliation UI that writes this is not built yet — runner.py reads
    # it defensively (falls back to "native") — but the column and the
    # session_store field whitelist must exist now so that read never 500s.
    ("add_session_configs_granularity_cfg",
     "ALTER TABLE session_configs ADD COLUMN IF NOT EXISTS granularity_cfg JSONB"),
    # ── Mermas (shrinkage / non-sale stock-outs) ─────────────────────────────
    # Records inventory that left stock for a reason OTHER than a sale
    # (breakage, expiry, self-consumption, gift/sample). unit_cost is
    # captured at record time (the SKU's unit cost can change later — this
    # keeps the historical cost accurate); total_cost = quantity * unit_cost,
    # precomputed here so a future monthly shrinkage summary is a simple SUM.
    ("create_inventory_shrinkage",
     """CREATE TABLE IF NOT EXISTS inventory_shrinkage (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         sku            TEXT NOT NULL,
         warehouse         TEXT NOT NULL DEFAULT 'principal',
         quantity       FLOAT NOT NULL,
         reason         TEXT NOT NULL,
         unit_cost FLOAT,
         total_cost    FLOAT,
         notes          TEXT,
         created_by     TEXT,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_inventory_shrinkage_tenant_idx",
     "CREATE INDEX IF NOT EXISTS inventory_shrinkage_tenant_idx ON inventory_shrinkage (tenant_id, created_at DESC)"),
    ("create_inventory_shrinkage_sku_idx",
     "CREATE INDEX IF NOT EXISTS inventory_shrinkage_sku_idx ON inventory_shrinkage (tenant_id, sku, created_at DESC)"),
    # ── LatAm commercial calendar (feature 3.4) ───────────────────────────────
    # Seeded events live in the same table as user-created ones so the existing
    # simulator needs no changes. `catalog_key` identifies a seeded occurrence
    # (e.g. "co_semana_santa:2026") and makes re-seeding idempotent; `active`
    # lets the user switch an event off without deleting it, so a later re-seed
    # does not silently resurrect it.
    ("add_inventory_events_catalog_key",
     "ALTER TABLE inventory_events ADD COLUMN IF NOT EXISTS catalog_key TEXT"),
    ("add_inventory_events_country",
     "ALTER TABLE inventory_events ADD COLUMN IF NOT EXISTS country TEXT"),
    ("add_inventory_events_source",
     "ALTER TABLE inventory_events ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'user'"),
    ("add_inventory_events_active",
     "ALTER TABLE inventory_events ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE"),
    ("create_inventory_events_catalog_uniq",
     """CREATE UNIQUE INDEX IF NOT EXISTS inventory_events_catalog_uniq
        ON inventory_events (tenant_id, catalog_key)
        WHERE catalog_key IS NOT NULL"""),
    # Per-product / per-category multipliers for an event.
    # One multiplier per event is false in practice: on Black Friday
    # electronics spike and milk does not move. `scope` is 'sku' or
    # 'category'; resolution order is sku > category > the event's multiplier.
    ("create_inventory_event_multipliers",
     """CREATE TABLE IF NOT EXISTS inventory_event_multipliers (
         id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id   TEXT NOT NULL,
         event_id    TEXT NOT NULL REFERENCES inventory_events(id) ON DELETE CASCADE,
         scope       TEXT NOT NULL CHECK (scope IN ('sku', 'category')),
         scope_value TEXT NOT NULL,
         multiplier  FLOAT NOT NULL,
         created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_inventory_event_multipliers_uniq",
     """CREATE UNIQUE INDEX IF NOT EXISTS inventory_event_multipliers_uniq
        ON inventory_event_multipliers (tenant_id, event_id, scope, scope_value)"""),

    # ── Supplier price breaks (feature 3.5) ──────────────────────────────────
    # A break says "from min_qty units on, each unit costs unit_price". Modeled
    # per (supplier, SKU) because unit price is a property of the product, not
    # of the supplier: one supplier has a different scale for every SKU it sells.
    # ALL-UNITS semantics (what LatAm distributors use in practice): once the
    # break is crossed the price applies to EVERY unit in the order, not only to
    # the units above min_qty. See price_break_service.py.
    ("create_supplier_price_breaks",
     """CREATE TABLE IF NOT EXISTS supplier_price_breaks (
         id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id   TEXT NOT NULL,
         supplier_id TEXT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
         sku         TEXT NOT NULL,
         min_qty     FLOAT NOT NULL,
         unit_price  FLOAT NOT NULL,
         notes       TEXT,
         created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_supplier_price_breaks_uniq",
     """CREATE UNIQUE INDEX IF NOT EXISTS supplier_price_breaks_uniq
        ON supplier_price_breaks (tenant_id, supplier_id, sku, min_qty)"""),
    ("create_supplier_price_breaks_sku_idx",
     """CREATE INDEX IF NOT EXISTS supplier_price_breaks_sku_idx
        ON supplier_price_breaks (tenant_id, sku)"""),

    # ── Cash calendar / accounts payable (feature 3.6) ────────────────────────
    # `payment_terms` stays free text and is NEVER touched: it is what the user
    # typed and the only source of truth when parsing fails.
    # `payment_terms_days` is its structured counterpart (credit days).
    ("add_suppliers_payment_terms_days",
     "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS payment_terms_days INT"),
    # Backfill of what users already captured. Mirrors parse_payment_terms_days()
    # in backend/inventory/cash_service.py (same cases, same rule order):
    #   cash-on-delivery/prepaid/anticipo → 0 · instalment schedule → NULL ·
    #   "N mes(es)" → N*30 · "quincenal" → 15 · first number that is not a
    #   percentage → N days · anything else → NULL.
    # Unparseable text stays NULL on purpose: the cash calendar reports it under
    # "missing terms" instead of inventing a due date. A test asserts this SQL
    # and the Python parser agree case by case
    # (tests/test_cash_calendar.py::test_backfill_matches_the_python_parser) —
    # change one and you must change the other.
    # The `%%` are deliberate: every statement goes through psycopg2, which
    # treats a lone `%` as a parameter placeholder. Postgres sees one `%`.
    ("backfill_suppliers_payment_terms_days",
     """UPDATE suppliers
           SET payment_terms_days = CASE
               WHEN payment_terms ~* '(contado|cash|anticip|adelant|prepag|inmediat|contra ?entrega|\\mcod\\M)'
                    THEN 0
               WHEN payment_terms ~* '[0-9]+[[:space:]]*[x/×-][[:space:]]*[0-9]+'
                    THEN NULL
               WHEN payment_terms ~* '([0-9]+)\\s*mes'
                    THEN LEAST((substring(payment_terms from '([0-9]+)\\s*mes'))::int * 30, 365)
               WHEN payment_terms ~* 'quincen'
                    THEN 15
               WHEN regexp_replace(payment_terms, '[0-9]+[[:space:]]*%%', ' ', 'g') ~ '[0-9]+'
                    THEN LEAST((substring(
                             regexp_replace(payment_terms, '[0-9]+[[:space:]]*%%', ' ', 'g')
                             from '[0-9]+'))::int, 365)
               ELSE NULL
           END
         WHERE payment_terms_days IS NULL
           AND payment_terms IS NOT NULL
           AND btrim(payment_terms) <> ''"""),
    # When the PO was sent to the supplier. The invoice clock starts at send
    # time, so without this there is no due date to compute.
    ("add_po_log_sent_at",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ"),
    # When the supplier's invoice for this PO was paid, and who said so (math
    # audit 2026-10-01, O3). Without it every PO ever sent stayed a payable
    # forever, so `overdue_total` only grew and the affordability check
    # eventually answered "does not fit" to every cart. Nullable, no default,
    # no backfill: an existing order is "not marked as paid", which is exactly
    # what is known about it — inventing a payment date for old rows would be
    # the opposite lie.
    ("add_po_log_paid_at",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ"),
    ("add_po_log_paid_by",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS paid_by TEXT"),
    # A purchase order the buyer abandoned (2026-10-01, owner's decision).
    # Without it an order that was never going to arrive kept counting as
    # "on the way" until somebody received it, holding the recommendation down
    # by exactly its units. NULL = not cancelled, which is what every existing
    # order is; nothing is backfilled.
    ("add_po_log_cancelled_at",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ"),
    ("add_po_log_cancelled_by",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS cancelled_by TEXT"),
    ("add_po_log_cancel_reason",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS cancel_reason TEXT"),

    # One row per tenant per month once the monthly recap email has been sent.
    # The unique constraint is the dedup mechanism: the worker re-runs on every
    # process restart, and a customer must never receive the same recap twice.
    ("create_inventory_roi_email_log",
     """CREATE TABLE IF NOT EXISTS inventory_roi_email_log (
         id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id     TEXT NOT NULL,
         month         TEXT NOT NULL,
         recipients    INT NOT NULL DEFAULT 0,
         sent_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_inventory_roi_email_log_uniq",
     """CREATE UNIQUE INDEX IF NOT EXISTS inventory_roi_email_log_uniq
        ON inventory_roi_email_log (tenant_id, month)"""),

    # ── Trial clock ──────────────────────────────────────────────────────────
    # A new tenant starts on a time-boxed trial; NULL means the clock does not
    # apply to this account. This is all that is left of what used to be a
    # three-tier plan model with a Stripe subscription behind it.
    ("add_tenants_trial_ends_at",
     "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ"),

    # Human-readable per-tenant order number (spec 2026-07-22-po-flow-polish).
    ("po_log_add_po_number",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS po_number INT"),
    # Backfill pre-feature rows per tenant in generated_at order. Offset numbering
    # past the tenant's existing maximum to avoid collisions with already-numbered
    # rows (which can occur if a NULL row appears after some rows are already
    # numbered, e.g., during a rolling deploy with an old-code instance).
    # Idempotent: only NULL rows are updated.
    ("po_log_backfill_po_number",
     """UPDATE inventory_po_log t
        SET po_number = s.rn + COALESCE((SELECT MAX(m.po_number)
                                           FROM inventory_po_log m
                                          WHERE m.tenant_id = t.tenant_id), 0)
        FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY generated_at, id) AS rn
              FROM inventory_po_log WHERE po_number IS NULL) s
        WHERE t.id = s.id AND t.po_number IS NULL"""),
    # Uniqueness guard: two concurrent inserts computing the same MAX+1 — the
    # loser gets a 23505 and retries (see roi_service.log_po_generation).
    ("po_log_po_number_unique_idx",
     "CREATE UNIQUE INDEX IF NOT EXISTS po_log_tenant_po_number_idx ON inventory_po_log (tenant_id, po_number)"),
    # ── Multi-warehouse complete (feature 5.4) ───────────────────────────────
    ("create_inventory_transfer_log",
     """CREATE TABLE IF NOT EXISTS inventory_transfer_log (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         from_warehouse TEXT NOT NULL,
         to_warehouse   TEXT NOT NULL,
         status         TEXT NOT NULL DEFAULT 'in_transit',
         notes          TEXT,
         created_by     TEXT NOT NULL,
         created_at     TIMESTAMPTZ DEFAULT NOW(),
         received_at    TIMESTAMPTZ
     )"""),
    ("create_inventory_transfer_log_idx",
     "CREATE INDEX IF NOT EXISTS transfer_log_tenant_idx ON inventory_transfer_log (tenant_id, created_at DESC)"),
    ("create_inventory_transfer_items",
     """CREATE TABLE IF NOT EXISTS inventory_transfer_items (
         id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id    TEXT NOT NULL,
         transfer_id  TEXT NOT NULL,
         sku          TEXT NOT NULL,
         qty_sent     FLOAT NOT NULL,
         qty_received FLOAT NOT NULL DEFAULT 0
     )"""),
    ("create_inventory_transfer_items_idx",
     "CREATE INDEX IF NOT EXISTS transfer_items_transfer_idx ON inventory_transfer_items (tenant_id, transfer_id)"),
    # Manual demand split for tenants whose sales history has no store column:
    # per-warehouse demand = SKU-global demand x normalized share (0-100).
    ("add_warehouses_demand_share",
     "ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS demand_share FLOAT"),
    # Where a PO's goods physically arrive. NULL = tenant default warehouse
    # (today's implicit behavior, preserved).
    ("add_po_log_destination_warehouse",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS destination_warehouse TEXT"),
    # Negative stock is nonsensical. App-level guards (transfer_service._adjust_stock's
    # atomic decrement floor, shrinkage_service's conditional UPDATE) close the TOCTOU
    # races, but a DB CHECK is the backstop that makes the invariant unviolable no matter
    # which code path writes the row. Clamp any pre-existing negatives to 0 FIRST — there
    # is no better recovery signal than "empty" — so the constraint below can be added.
    # Idempotent by nature (a second run matches no rows).
    ("clamp_negative_inventory_stock",
     "UPDATE inventory_stock SET current_stock = 0 WHERE current_stock < 0"),
    ("add_inventory_stock_current_stock_nonneg",
     """DO $$ BEGIN
         IF NOT EXISTS (
           SELECT 1 FROM pg_constraint WHERE conname = 'inventory_stock_current_stock_nonneg'
         ) THEN
           ALTER TABLE inventory_stock
             ADD CONSTRAINT inventory_stock_current_stock_nonneg CHECK (current_stock >= 0);
         END IF;
       END $$"""),
    # Repair the over-receipt residue left by the uncapped reception paths
    # (fixed in reception_service): received_qty could exceed final_qty, an
    # impossible ledger state that corrupts supplier fill-rate. Clamp to
    # final_qty — the most that could legitimately have been ordered on the
    # line. Idempotent (a second run matches no rows). We intentionally do NOT
    # add a CHECK(received_qty <= final_qty) constraint: a supplier genuinely
    # shipping extra is a real-world case the product may later choose to
    # record, so the invariant lives in the service layer, not the schema.
    ("clamp_po_items_over_receipt",
     "UPDATE inventory_po_items SET received_qty = final_qty "
     "WHERE received_qty > final_qty"),
    # Same class of residue on transfers: qty_received > qty_sent from the
    # pre-fix receive race. Clamp to qty_sent.
    ("clamp_transfer_items_over_receipt",
     "UPDATE inventory_transfer_items SET qty_received = qty_sent "
     "WHERE qty_received > qty_sent"),
    # Multi-period planning (Phase A): a training launch fans out into a
    # "family" of sessions, one per supported granularity, sharing a family_id.
    # Nullable — pre-feature sessions keep NULL and behave as a lone family.
    ("add_sessions_family_id",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS family_id TEXT"),
    ("add_sessions_granularity",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS granularity TEXT"),
    ("create_sessions_family_idx",
     "CREATE INDEX IF NOT EXISTS sessions_family_idx ON sessions (tenant_id, family_id)"),
    # ── The seven columns `datasets` was missing on a FRESH database ────────
    #
    # Found 2026-09-14 by walking every screen of a virgin install: `/ventas`
    # and `/archivos` — the first two screens a new user opens, the ones that
    # say "upload your sales" — answered 500 with
    # `psycopg2.errors.UndefinedColumn: column "updated_at" does not exist`.
    #
    # The data-sources feature grew these columns over time and every existing
    # database has them, because each one was added by hand or by a migration
    # that no longer exists. `base_datasets` above was never updated to match,
    # so the schema this code bootstraps from scratch has 12 columns while the
    # code writes 19. Nobody saw it because nobody creates a new database:
    # development runs on one that has been migrated forward for months.
    #
    # That is exactly the failure a buyer meets first and the owner can never
    # reproduce. Added here rather than inside `base_datasets` on purpose —
    # rewriting a CREATE that has already run changes nothing on an existing
    # database, and these have to reach both.
    ("add_datasets_description",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS description TEXT"),
    ("add_datasets_updated_at",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ "
     "NOT NULL DEFAULT NOW()"),
    # 'file' | 'sql' — what the source IS. Defaulted so the rows that predate
    # SQL sources read as the uploads they are.
    ("add_datasets_source_type",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS source_type TEXT "
     "NOT NULL DEFAULT 'file'"),
    # 'connected' | 'pending' | 'error' — whether the source can be read right
    # now. An uploaded file is connected the moment it lands.
    ("add_datasets_connection_status",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS connection_status TEXT "
     "NOT NULL DEFAULT 'connected'"),
    ("add_datasets_sql_config",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS sql_config JSONB"),
    ("add_datasets_saved_query",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS saved_query TEXT"),
    ("add_datasets_preview_cache",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS preview_cache JSONB"),

    # In-app dataset editor: a save-as-new dataset links to the source dataset it
    # was edited from. Nullable — uploads and SQL sources keep NULL.
    ("add_datasets_parent_id",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS parent_id TEXT"),
    # ── Conversational WhatsApp bot (spec 2026-07-23) ────────────────────────
    # A pre-registered number identifies the sender; it is only usable once
    # verified. whatsapp_number itself already exists (add_users_whatsapp_number).
    ("add_users_whatsapp_verified_at",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS whatsapp_verified_at TIMESTAMPTZ"),
    # One number links to at most one user. Partial so many users may keep NULL.
    ("create_users_whatsapp_number_uniq",
     """CREATE UNIQUE INDEX IF NOT EXISTS users_whatsapp_number_uniq
        ON users (whatsapp_number) WHERE whatsapp_number IS NOT NULL"""),
    # Recent turns + one pending write action, one row per (tenant, user).
    # last_message_sid is the idempotency anchor (Twilio retries resend the
    # same MessageSid). Rows older than the 24h window are pruned on read.
    ("create_whatsapp_conversations",
     """CREATE TABLE IF NOT EXISTS whatsapp_conversations (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id        TEXT NOT NULL,
         user_id          TEXT NOT NULL,
         phone            TEXT NOT NULL,
         history          JSONB NOT NULL DEFAULT '[]',
         pending_action   JSONB,
         last_message_sid TEXT,
         updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_whatsapp_conversations_uniq",
     """CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_conversations_tenant_user_uniq
        ON whatsapp_conversations (tenant_id, user_id)"""),
    # ── Manual purchase orders (PENDIENTES #1) ───────────────────────────────
    # A PO no longer requires a forecast session: manual orders carry
    # session_id NULL and source='manual'; forecast-driven ones keep their
    # session and source='forecast' (backfilled by the DEFAULT).
    ("drop_po_log_session_not_null",
     "ALTER TABLE inventory_po_log ALTER COLUMN session_id DROP NOT NULL"),
    ("add_po_log_source",
     """ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS
        source TEXT NOT NULL DEFAULT 'forecast'"""),
    # The supplier the buyer actually PICKED for a line. Historical rows keep
    # only the free-text name, so this stays nullable and the send path falls
    # back to resolving by name.
    ("add_po_items_supplier_id",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS supplier_id TEXT"),
    # ── Product family (PENDIENTES #6) ───────────────────────────────────────
    # A grouping between category and SKU: "Bebidas" (category) > "Gaseosas"
    # (family) > "Coca 1L" (sku). Events need it because a Christmas multiplier
    # is rarely uniform across a whole category.
    ("add_stock_family",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS family TEXT"),
    ("widen_event_multiplier_scope_family",
     """DO $$ BEGIN
         IF EXISTS (SELECT 1 FROM information_schema.tables
                     WHERE table_name = 'inventory_event_multipliers') THEN
             ALTER TABLE inventory_event_multipliers
               DROP CONSTRAINT IF EXISTS inventory_event_multipliers_scope_check;
             ALTER TABLE inventory_event_multipliers
               ADD CONSTRAINT inventory_event_multipliers_scope_check
               CHECK (scope IN ('sku', 'family', 'category'));
         END IF;
       END $$"""),
    # ── Transfer lanes: time + money for inter-warehouse moves (PENDIENTES #2) ─
    # One row per ordered (from, to) pair. Missing lane = the documented
    # fallback in transfer_lane_service (1 day, zero cost), so tenants that
    # never configure lanes keep the pre-feature behavior.
    ("create_transfer_lanes",
     """CREATE TABLE IF NOT EXISTS transfer_lanes (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         from_warehouse TEXT NOT NULL,
         to_warehouse   TEXT NOT NULL,
         lead_time_days INT NOT NULL DEFAULT 1,
         cost_per_unit  FLOAT NOT NULL DEFAULT 0,
         fixed_cost     FLOAT NOT NULL DEFAULT 0,
         created_at     TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (tenant_id, from_warehouse, to_warehouse)
     )"""),
    # What the lane promised when the transfer was sent, frozen on the row:
    # editing the lane later must not rewrite history. Nullable — transfers
    # created before this feature keep NULL.
    ("add_transfer_log_lead_time_days",
     "ALTER TABLE inventory_transfer_log ADD COLUMN IF NOT EXISTS lead_time_days INT"),
    ("add_transfer_log_expected_arrival",
     "ALTER TABLE inventory_transfer_log ADD COLUMN IF NOT EXISTS expected_arrival TIMESTAMPTZ"),
    # ── What-if scenarios (PENDIENTES #7) ────────────────────────────────────
    # A named, reusable set of what-if rules bound to one forecast session.
    # `rules` holds the typed rule list (demand_multiplier / promo /
    # supplier_delay / safety_stock) exactly as the API validated it. Running a
    # scenario is a pure read, so no result is persisted here.
    ("create_scenarios",
     """CREATE TABLE IF NOT EXISTS scenarios (
         id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id  TEXT NOT NULL,
         name        TEXT NOT NULL,
         rules       JSONB NOT NULL DEFAULT '[]'::jsonb,
         created_by  TEXT,
         created_at  TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_scenarios_idx",
     "CREATE INDEX IF NOT EXISTS scenarios_tenant_session_idx ON scenarios (tenant_id, session_id)"),
    # ── Scheduled-job failure visibility ─────────────────────────────────────
    # A weekly retrain whose trigger keeps failing used to look exactly like a
    # healthy one: the error went to the log and nowhere else.
    # Nullable with no default, so every existing row reads as "never failed".
    ("add_scheduled_jobs_last_error",
     "ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS last_error TEXT"),
    ("add_scheduled_jobs_last_error_at",
     "ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ"),
    # ── Report generation runs ───────────────────────────────────────────────
    # Report building happens in a FastAPI background task, so a failure had no
    # trace anywhere: the later download 404'd with "generate one first", i.e.
    # it told the user to redo exactly what had just failed. One row per
    # generation request makes the outcome durable and retrievable.
    # `status` is plain TEXT on purpose — a CHECK constraint here would have to
    # be re-added on every startup, and this repo has already been burned once
    # by a re-added CHECK carrying a stale vocabulary.
    ("create_report_runs",
     """CREATE TABLE IF NOT EXISTS report_runs (
         id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id   TEXT NOT NULL,
         report_type  TEXT NOT NULL,
         formats      TEXT[] NOT NULL DEFAULT '{}',
         status       TEXT NOT NULL DEFAULT 'running',
         error_code   TEXT,
         error_detail TEXT,
         created_by   TEXT,
         created_at   TIMESTAMPTZ DEFAULT NOW(),
         finished_at  TIMESTAMPTZ
     )"""),
    ("create_report_runs_idx",
     "CREATE INDEX IF NOT EXISTS report_runs_session_idx "
     "ON report_runs (tenant_id, session_id, created_at DESC)"),
    # ── Provenance of the planning values (friction plan #2, step 2) ─────────
    # `lead_time_days INT NOT NULL DEFAULT 15` made two completely different
    # situations physically indistinguishable: "the buyer configured 15 days"
    # and "nobody ever touched this row". The product then told the user, in the
    # explanation it uses to earn their trust, that their supplier's lead time
    # was "configurado" — asserting something false.
    #
    # A sibling `<field>_set_by` column rather than making the value nullable:
    # nullable would have forced a COALESCE into every read path (and every
    # existing NOT NULL contract), while the sibling adds provenance without
    # touching the value's type. NULL here means "unknown provenance", which the
    # resolver reads as 'default' — the honest answer for a row nobody claimed.
    # Values ∈ user | file | supplier_rule | learned | default (see
    # backend/inventory/defaults.py). Plain TEXT, no CHECK: this repo has
    # already been burned by a re-added CHECK carrying a stale vocabulary.
    ("add_stock_lead_time_set_by",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS lead_time_set_by TEXT"),
    ("add_stock_service_level_set_by",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS service_level_set_by TEXT"),
    ("add_stock_unit_cost_set_by",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS unit_cost_set_by TEXT"),
    ("add_stock_moq_set_by",
     "ALTER TABLE inventory_stock ADD COLUMN IF NOT EXISTS moq_set_by TEXT"),
    # One-time, best-effort backfill for rows that predate provenance. We cannot
    # recover who set what, but a value that DIFFERS from the schema default
    # could only have got there because somebody put it there — so it is marked
    # 'user'. A value equal to the schema default stays NULL and therefore reads
    # as 'default', which is the honest reading: it is exactly the case we could
    # never tell apart. Idempotent twice over — guarded on IS NULL, and it can
    # only ever move a row from "unknown" to "user".
    ("backfill_stock_lead_time_set_by",
     "UPDATE inventory_stock SET lead_time_set_by = 'user' "
     "WHERE lead_time_set_by IS NULL AND lead_time_days IS DISTINCT FROM 15"),
    ("backfill_stock_service_level_set_by",
     "UPDATE inventory_stock SET service_level_set_by = 'user' "
     "WHERE service_level_set_by IS NULL AND service_level IS DISTINCT FROM 0.95"),
    ("backfill_stock_unit_cost_set_by",
     "UPDATE inventory_stock SET unit_cost_set_by = 'user' "
     "WHERE unit_cost_set_by IS NULL AND unit_cost IS NOT NULL"),
    ("backfill_stock_moq_set_by",
     "UPDATE inventory_stock SET moq_set_by = 'user' "
     "WHERE moq_set_by IS NULL AND moq IS DISTINCT FROM 1"),
    # The supplier card has the SAME problem one table over:
    # `suppliers.lead_time_days INT NOT NULL DEFAULT 15`. Without provenance
    # here, creating a supplier and never touching its lead time would inject a
    # 15 into the cascade and the SKU would report 'supplier_rule' — the same
    # false claim of authorship, just relocated. The cascade only reads this
    # column when somebody actually set it.
    ("add_suppliers_lead_time_set_by",
     "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS lead_time_set_by TEXT"),
    ("backfill_suppliers_lead_time_set_by",
     "UPDATE suppliers SET lead_time_set_by = 'user' "
     "WHERE lead_time_set_by IS NULL AND lead_time_days IS DISTINCT FROM 15"),
    # ── Planning defaults by supplier / category / global (friction plan #1) ──
    # A distributor does not have 2.000 lead times, it has 12 suppliers.
    # Configuring per SKU is why the setup never gets finished, so one rule
    # covers a supplier's whole catalogue at once. Resolution is
    # SKU > supplier > category > global > system default, and it reports which
    # level won — that answer IS the provenance vocabulary above, which is why
    # the two features share one migration instead of migrating twice.
    #
    # Every value column is nullable on purpose: a rule that only sets a lead
    # time must not also silently impose a service level. NULL = "this rule says
    # nothing about this field", so the cascade keeps falling through.
    ("create_stock_defaults",
     """CREATE TABLE IF NOT EXISTS stock_defaults (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id        TEXT NOT NULL,
         scope_type       TEXT NOT NULL,
         scope_value      TEXT NOT NULL DEFAULT '',
         lead_time_days   INT,
         service_level    FLOAT,
         moq              FLOAT,
         holding_cost_pct FLOAT,
         created_at       TIMESTAMPTZ DEFAULT NOW(),
         updated_at       TIMESTAMPTZ DEFAULT NOW(),
         UNIQUE (tenant_id, scope_type, scope_value)
     )"""),
    ("create_stock_defaults_idx",
     "CREATE INDEX IF NOT EXISTS stock_defaults_tenant_idx "
     "ON stock_defaults (tenant_id, scope_type)"),

    # ── Team messaging (Professional plan) ────────────────────────────────────
    # 1-to-1 chat between users of the same tenant. Conversations are derived
    # from the (sender, recipient) pair — no thread table. `chats` /
    # `chat_messages` are the AI-analyst chat and stay untouched.
    ("create_direct_messages",
     """CREATE TABLE IF NOT EXISTS direct_messages (
         id           BIGSERIAL PRIMARY KEY,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         sender_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         recipient_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         body         TEXT NOT NULL,
         read_at      TIMESTAMPTZ,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    # Unread badge: recipient scans their own unread rows.
    ("create_direct_messages_unread_idx",
     "CREATE INDEX IF NOT EXISTS direct_messages_unread_idx "
     "ON direct_messages (tenant_id, recipient_id) WHERE read_at IS NULL"),
    # Thread view and conversation list both walk messages touching one user,
    # newest first.
    ("create_direct_messages_thread_idx",
     "CREATE INDEX IF NOT EXISTS direct_messages_thread_idx "
     "ON direct_messages (tenant_id, sender_id, recipient_id, id DESC)"),
    # Opt-in per user: forward new direct messages as an SMS to
    # users.whatsapp_number. Lives in user_preferences next to language/theme.
    ("add_user_preferences_dm_sms_enabled",
     "ALTER TABLE user_preferences "
     "ADD COLUMN IF NOT EXISTS dm_sms_enabled BOOLEAN NOT NULL DEFAULT FALSE"),

    # A SQL data source has no uploaded file, so original_filename cannot be
    # NOT NULL — the constraint made create_sql_source fail on a fresh schema.
    ("datasets_original_filename_nullable",
     "ALTER TABLE datasets ALTER COLUMN original_filename DROP NOT NULL"),

    # ── API keys that actually authenticate ─────────────────────────────────
    # A key carries its OWN role rather than inheriting its creator's: the
    # integration must keep working when that person leaves the company, and
    # must not gain power when they are promoted.
    # 'viewer' is the default because a key minted before this column existed
    # was never meant to write anything.
    ("add_api_keys_role",
     "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'viewer'"),
    ("add_api_keys_role_check",
     "ALTER TABLE api_keys ADD CONSTRAINT api_keys_role_check "
     "CHECK (role IN ('viewer', 'analyst'))"),
    # Who minted it — for the audit trail only. The key acts as itself, never
    # as this user.
    ("add_api_keys_created_by",
     "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_by TEXT"),
    # Last four characters, so the list can tell two keys apart without ever
    # storing enough of one to use it.
    ("add_api_keys_last4",
     "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last4 TEXT"),
    # Optional expiry. NULL = never expires, which is what every existing key is.
    ("add_api_keys_expires_at",
     "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ"),
    # The key's scope as the public API names it: 'read' or 'write'. DERIVED
    # from `role` rather than stored beside it, on purpose. Two columns that
    # must agree are two columns that eventually do not, and a backfill to a
    # flat 'read' default would have quietly demoted every existing analyst
    # key — an integration whose nightly push starts answering 403 with nobody
    # having touched it. Generated, the mapping is the schema's: viewer → read,
    # analyst → write, for every row that exists and every row inserted later
    # by any path.
    ("add_api_keys_scope",
     "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS scope TEXT "
     "GENERATED ALWAYS AS (CASE WHEN role = 'analyst' THEN 'write' ELSE 'read' END) STORED"),
    # Metering: one row per key per UTC day, incremented by a single upsert on
    # every API-key call that reached an endpoint. This is what a call-based
    # bill is computed from, so it survives the key: `api_key_id` has no FK and
    # `key_name` is copied in, because revoking a key DELETES its row and the
    # month's calls it made are still owed. Tenant-owned: cascades with it.
    ("create_api_usage_daily",
     """CREATE TABLE IF NOT EXISTS api_usage_daily (
         tenant_id   TEXT   NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         api_key_id  TEXT   NOT NULL,
         key_name    TEXT   NOT NULL,
         day         DATE   NOT NULL,
         calls       BIGINT NOT NULL DEFAULT 0 CHECK (calls >= 0),
         PRIMARY KEY (tenant_id, api_key_id, day)
     )"""),
    ("create_api_usage_daily_day_idx",
     "CREATE INDEX IF NOT EXISTS api_usage_daily_tenant_day_idx "
     "ON api_usage_daily (tenant_id, day)"),

    # Of the 45 tables carrying `tenant_id`, only four declared a foreign key to
    # `tenants`. Deleting a tenant therefore left every other table's rows behind
    # — unreachable (no tenant owns them), invisible, and permanent. Measured on
    # the dev database before this ran: 1.3M orphan rows from 24,794 tenants that
    # no longer existed, 596 MB, and a backend suite that had slowed from 26
    # minutes to 4h21 dragging them around.
    #
    # `data_export.delete_tenant()` compensates by deleting each table explicitly,
    # and its list is now schema-checked by a test — but that only protects the
    # product's own erasure path. Anything else that removes a tenant (a script,
    # a fixture, a psql session) still orphaned everything. This makes the
    # database itself keep the invariant.
    #
    # Written against the live catalog rather than a list of 41 table names, so a
    # tenant-scoped table added later gets its cascade on the next boot instead of
    # waiting to be remembered. It is idempotent by construction: it only touches
    # tables that carry `tenant_id` and do not already have that FK.
    #
    # Orphans MUST be deleted before the constraint is added or the ALTER cannot
    # validate — and on `strict` startup a failure here would stop the server
    # booting. They are rows belonging to accounts that no longer exist, which in
    # a module that cites the right to erasure is a liability, not an asset.
    # NOT VALID + VALIDATE keeps the write lock short on a large table.
    # NOTE: no `%` anywhere in this block. `execute()` hands the SQL to psycopg,
    # which reads `%s`/`%I` as ITS OWN placeholders and fails with "tuple index
    # out of range" — so Postgres's `format()` and `RAISE NOTICE '%'` cannot be
    # used here. `quote_ident` + concatenation says the same thing safely.
    ("cascade_tenant_id_foreign_keys",
     r"""
     DO $$
     DECLARE
         r      record;
         ident  text;
         cname  text;
     BEGIN
         FOR r IN
             SELECT c.table_name AS t
             FROM information_schema.columns c
             JOIN information_schema.tables x
               ON x.table_name = c.table_name
              AND x.table_schema = c.table_schema
              AND x.table_type = 'BASE TABLE'
             WHERE c.column_name = 'tenant_id'
               AND c.table_schema = 'public'
               AND c.table_name <> 'tenants'
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint pc
                   JOIN pg_attribute a
                     ON a.attrelid = pc.conrelid AND a.attnum = ANY (pc.conkey)
                   WHERE pc.conrelid = ('public.' || quote_ident(c.table_name))::regclass
                     AND pc.contype = 'f'
                     AND pc.confrelid = 'public.tenants'::regclass
                     AND a.attname = 'tenant_id')
         LOOP
             ident := 'public.' || quote_ident(r.t);
             cname := quote_ident('fk_' || r.t || '_tenant');

             -- Per table, never fatal. This runs inside run_all(strict=True) at
             -- startup, so an unforeseen table — one where tenant_id holds
             -- something that was never a tenant id, or one too large to
             -- validate inside the statement timeout — would otherwise stop the
             -- server from booting. Skipping one table costs that table's
             -- cascade and is caught loudly by the catalog test in
             -- test_tenant_cascade_fk.py; refusing to boot costs the product.
             BEGIN
                 EXECUTE 'DELETE FROM ' || ident || ' x WHERE NOT EXISTS '
                      || '(SELECT 1 FROM public.tenants t WHERE t.id = x.tenant_id)';
                 EXECUTE 'ALTER TABLE ' || ident || ' ADD CONSTRAINT ' || cname
                      || ' FOREIGN KEY (tenant_id) REFERENCES public.tenants(id)'
                      || ' ON DELETE CASCADE NOT VALID';
                 EXECUTE 'ALTER TABLE ' || ident || ' VALIDATE CONSTRAINT ' || cname;
             EXCEPTION WHEN OTHERS THEN
                 -- USING MESSAGE, not RAISE's format string: that needs a
                 -- percent placeholder, which psycopg would eat. Same trap as
                 -- above -- and it bites inside SQL comments too, since psycopg
                 -- interpolates over the whole string without parsing it.
                 RAISE WARNING USING MESSAGE =
                     'cascade FK skipped for table ' || r.t
                     || ' (SQLSTATE ' || SQLSTATE || ')';
             END;
         END LOOP;
     END $$;
     """),
    # ── Cutting live sessions on a password change (walked 2026-08-10) ───────
    # Access tokens are stateless and the reset flow is UNAUTHENTICATED, so it
    # holds no `jti` to put on the revoked_tokens blocklist the way /logout
    # does. Measured before this column existed: after a completed reset the
    # pre-reset access token still answered 200 for the rest of its 15 minutes,
    # while the endpoint claimed every session had been revoked.
    #
    # The cut is per USER and per TIME: any access token minted at or before
    # this instant is refused (guards.py compares it against the token's `iat`).
    # NULL means "never cut anything", which is every account that has not
    # changed its password — their tokens are untouched.
    ("add_users_sessions_invalid_before",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_invalid_before TIMESTAMPTZ"),

    # ── One plan, and no Stripe (2026-08-16) ─────────────────────────────────
    # StockAI sold three tiers with a subscription behind them. It sells one
    # product now, and there is nothing to buy in the app: a customer who needs
    # something writes to us. So the columns that only existed to answer "which
    # tier is this tenant on" and "what is its subscription doing" go, rather
    # than sit in the schema holding a value nothing reads — which is how a
    # dead column gets read again by mistake two years later.
    #
    # `trial_ends_at` and `quota` stay: the first is still enforced, the second
    # is how one account's limits get widened without a deploy.
    ("drop_stripe_events", "DROP TABLE IF EXISTS stripe_events"),
    ("drop_tenants_stripe_customer_uniq",
     "DROP INDEX IF EXISTS tenants_stripe_customer_uniq"),
    ("drop_tenants_stripe_customer_id",
     "ALTER TABLE tenants DROP COLUMN IF EXISTS stripe_customer_id"),
    ("drop_tenants_stripe_subscription_id",
     "ALTER TABLE tenants DROP COLUMN IF EXISTS stripe_subscription_id"),
    ("drop_tenants_subscription_status",
     "ALTER TABLE tenants DROP COLUMN IF EXISTS subscription_status"),
    ("drop_tenants_plan", "ALTER TABLE tenants DROP COLUMN IF EXISTS plan"),

    # ── Free tier with short limits, paid tier without (2026-08-22) ──────────
    # Still no checkout and still no feature gates: both tiers include every
    # feature, and `tier` only decides how much of it fits (see
    # backend/entitlements/plans.py). A tenant becomes 'paid' because somebody
    # talked to us and we wrote it here.
    #
    # These steps re-run on every boot — this module has no applied-ledger — so
    # each one is written to touch a row exactly once, ever. The whole one-time
    # window is "tier IS NULL", which is true only for rows that predate the
    # column. Without that guard, a boot would re-promote an account we had
    # deliberately moved back to free, and clear a suspension somebody set by
    # hand this morning.
    ("add_tenants_tier", "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS tier TEXT"),
    # Nobody is on a countdown any more: free is a permanent home, so the
    # 14-day trial every existing tenant carries stops being enforced. The
    # column stays — writing a past date into it is still how one account gets
    # suspended by hand.
    ("clear_legacy_trials_on_tier_backfill",
     "UPDATE tenants SET trial_ends_at = NULL WHERE tier IS NULL"),
    # Grandfathering, the owner's call: everything that existed when the tiers
    # landed keeps unlimited access. Only accounts created from here on start
    # free.
    ("backfill_existing_tenants_to_paid",
     "UPDATE tenants SET tier = 'paid' "
     "WHERE tier IS NULL AND created_at < TIMESTAMPTZ '2026-08-22 00:00:00+00'"),
    # A stock snapshot belongs to a warehouse, and until now it did not say
    # which. /inventario's sparkline and the briefing's demand_trend_pct read
    # the rows of one SKU in time order, so a tenant with principal at 500 and
    # Norte at 20 produced the series 500, 20, 500, 20 — and _calc_demand_trend
    # read that difference as real consumption ("+585% demand" that never
    # happened). One inter-warehouse transfer did it on its own.
    #
    # Deliberately NULLABLE with no backfill (owner's call, 2026-09-16): rows
    # written before this column are tenant-wide TOTALS, which is exactly what
    # they are read as. Stamping them 'principal' would assert something nobody
    # can know after the fact and would make principal's chart wrong instead of
    # the tenant's. Per-warehouse history therefore starts here; the aggregate
    # keeps its full history, because summing today's per-warehouse rows per day
    # continues the same series the old rows were.
    # When each recurring loop last fired. Until this existed every cron loop
    # computed its next run from `datetime.now()` and kept nothing, so a worker
    # killed at 07:55 and restarted at 08:02 asked for the next 08:00 boundary
    # AFTER now and got tomorrow: that day nobody got a digest, a lead-time
    # alert or a freshness reminder, and it looked like a calm day
    # (stability 11.28). Deployment state, not tenant state — there is one
    # scheduler, and this answers whether it did its rounds.
    # Which schedule created a session, when one did. A scheduled retrain now
    # trains a NEW session rather than the one the whole app is reading
    # (stability 11.6), and this column is what lets the schedule reuse its
    # own slots instead of consuming a saved-forecast ceiling every night: the
    # prune can only ever reach rows that carry it, and a session a person
    # created never does.
    ("add_sessions_scheduled_job_id",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS scheduled_job_id TEXT"),
    ("create_sessions_scheduled_job_idx",
     "CREATE INDEX IF NOT EXISTS sessions_scheduled_job_idx "
     "ON sessions (tenant_id, scheduled_job_id) WHERE scheduled_job_id IS NOT NULL"),
    # ── Sessions are permanent (2026-10-04) ──────────────────────────────────
    # A session is never erased on a real tenant's behalf. "Delete" became
    # "archive": the row, its results and its artifacts stay, and the session
    # leaves the working list until somebody restores it. NULL = active.
    ("add_sessions_archived_at",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ"),
    ("add_sessions_archived_by",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS archived_by TEXT"),
    # A back-test is a session trained on a copy of a dataset with its last
    # periods held out, so its forecast can be graded against the full file.
    # It must never drive purchasing, so planning queries exclude it.
    ("add_sessions_is_backtest",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_backtest BOOLEAN NOT NULL DEFAULT FALSE"),
    ("add_sessions_backtest_source_dataset_id",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS backtest_source_dataset_id TEXT"),
    ("add_sessions_backtest_holdout_periods",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS backtest_holdout_periods INT"),
    ("create_sessions_library_idx",
     "CREATE INDEX IF NOT EXISTS sessions_library_idx "
     "ON sessions (tenant_id, archived_at, created_at DESC)"),
    ("create_system_loop_runs",
     """CREATE TABLE IF NOT EXISTS system_loop_runs (
         loop          TEXT PRIMARY KEY,
         last_boundary TIMESTAMPTZ,
         last_run_at   TIMESTAMPTZ,
         last_status   TEXT,
         last_error    TEXT
     )"""),
    ("add_inventory_snapshots_warehouse",
     "ALTER TABLE inventory_snapshots ADD COLUMN IF NOT EXISTS warehouse TEXT"),
    ("create_inventory_snapshots_warehouse_idx",
     "CREATE INDEX IF NOT EXISTS inventory_snapshots_wh_idx "
     "ON inventory_snapshots (tenant_id, sku, warehouse, recorded_at DESC)"),
    ("backfill_remaining_tenants_to_free",
     "UPDATE tenants SET tier = 'free' WHERE tier IS NULL"),
    ("tenants_tier_default_free",
     "ALTER TABLE tenants ALTER COLUMN tier SET DEFAULT 'free'"),
    ("tenants_tier_not_null",
     "ALTER TABLE tenants ALTER COLUMN tier SET NOT NULL"),

    # Who asked to pay. There is no checkout, so this IS the funnel: the row is
    # written when a tenant asks for more room, and we answer it by hand. It
    # keeps the ask even when the notification email fails to leave — a lost
    # "I want to pay you" is the most expensive silent failure in the product.
    ("create_upgrade_requests", """
        CREATE TABLE IF NOT EXISTS upgrade_requests (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            user_id     TEXT,
            limit_key   TEXT,
            message     TEXT,
            contact     TEXT,
            status      TEXT NOT NULL DEFAULT 'new',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            handled_at  TIMESTAMPTZ
        )
    """),
    ("idx_upgrade_requests_tenant",
     "CREATE INDEX IF NOT EXISTS idx_upgrade_requests_tenant "
     "ON upgrade_requests (tenant_id, created_at DESC)"),
    # "One open ask per tenant" was enforced by SELECT-then-INSERT, which under
    # six simultaneous clicks produced four rows (measured 2026-08-22 by
    # tests/test_chaos_concurrency.py). A rule that only holds when nobody is in
    # a hurry is not a rule — so the database enforces it, and the endpoint uses
    # ON CONFLICT instead of a read. Anything already duplicated is collapsed
    # first, newest kept, or the index cannot be built.
    ("collapse_duplicate_open_upgrade_requests", """
        UPDATE upgrade_requests SET status = 'superseded'
         WHERE status = 'new' AND id NOT IN (
             SELECT DISTINCT ON (tenant_id) id FROM upgrade_requests
              WHERE status = 'new' ORDER BY tenant_id, created_at DESC
         )
    """),
    ("uniq_upgrade_requests_open",
     "CREATE UNIQUE INDEX IF NOT EXISTS uniq_upgrade_requests_open "
     "ON upgrade_requests (tenant_id) WHERE status = 'new'"),

    # Nothing recorded a training run's accuracy anywhere comparable: a
    # session's metrics lived only in session_results.training_result, one
    # JSONB blob overwritten by the next run, with no way to ask "is this
    # worse than last week's". engine.get_metrics()["by_model"] already
    # computes these aggregates every run; this just keeps them queryable.
    # UNIQUE (session_id, model) mirrors session_results itself: retraining
    # the same session overwrites its row instead of accumulating duplicates.
    ("create_training_run_metrics", """
        CREATE TABLE IF NOT EXISTS training_run_metrics (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            model       TEXT NOT NULL,
            avg_mae     DOUBLE PRECISION,
            avg_rmse    DOUBLE PRECISION,
            avg_wape    DOUBLE PRECISION,
            avg_bias    DOUBLE PRECISION,
            avg_mape    DOUBLE PRECISION,
            avg_smape   DOUBLE PRECISION,
            trained_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (session_id, model)
        )
    """),
    ("idx_training_run_metrics_tenant_model",
     "CREATE INDEX IF NOT EXISTS idx_training_run_metrics_tenant_model "
     "ON training_run_metrics (tenant_id, model, trained_at DESC)"),

    # Configuration overrides written from the admin panel — the layer that sits
    # above the environment so a deployment can turn a service on without
    # editing a file and restarting a container. See
    # `backend/service_config/store.py` for the precedence rules.
    #
    # `tenant_id NULL` is the instance scope. Exactly one of value_plain /
    # value_encrypted is ever populated: secrets are Fernet-encrypted with
    # INTEGRATIONS_SECRET_KEY, and a write of a secret with no key configured is
    # refused rather than downgraded to plaintext.
    ("create_service_config", """
        CREATE TABLE IF NOT EXISTS service_config (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT REFERENCES tenants(id) ON DELETE CASCADE,
            service         TEXT NOT NULL,
            field           TEXT NOT NULL,
            value_plain     TEXT,
            value_encrypted TEXT,
            updated_by      TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """),
    # Uniqueness is over COALESCE(tenant_id, '') because a NULL does not
    # collide with itself: a plain UNIQUE (tenant_id, field) would happily
    # accept four instance-level values for the same field, and the resolver
    # would then pick whichever the planner returned first.
    ("uniq_service_config_scope_field",
     "CREATE UNIQUE INDEX IF NOT EXISTS uniq_service_config_scope_field "
     "ON service_config (COALESCE(tenant_id, ''), field)"),

    # The accounting integrations (Alegra, Siigo) were removed on 2026-09-20.
    # The code was written and never ran against a live account, the stock
    # fetch was known wrong for a multi-branch tenant, and nothing in the
    # product could be verified against a real ERP — so it went rather than
    # being sold as a checkbox (stability 14.f, owner's call).
    #
    # This is the ONE migration in the list that is not additive, and
    # `deploy/UPGRADE.md` leans on that property for cheap rollbacks: after
    # this runs, rolling back to a release that still has the integrations
    # screen leaves it reading a table that no longer exists. Deliberate, and
    # the only correct alternative — leaving an encrypted-credential table
    # nothing reads — is worse: those rows are third-party ERP credentials.
    # They should not outlive the feature that needed them.
    ("drop_integration_connections",
     "DROP TABLE IF EXISTS integration_connections"),

    # Daily recommendation log: what the semaphore said and asked for, per
    # tenant per SKU per day. Every other inventory table records a fact about
    # the world (stock levels, POs sent, shrinkage) — none of them record the
    # RECOMMENDATION itself, so the product could never answer "what did it
    # cost me to ignore you" or "why is today's number different from last
    # week's". This table is that record, written by
    # `backend/inventory/recommendation_log.py::record_recommendations` from
    # the rows `get_inventory_status` already computed (no new computation
    # here — this only persists what was already decided).
    #
    # Natural key is (tenant_id, sku, recorded_on): the log is a daily
    # snapshot of an opinion, not an event stream — recomputing the status
    # twice in the same day (a screen view, then the digest an hour later)
    # must overwrite the same day's row, not create a second one, or the
    # "why did it change" comparison would be comparing two computations from
    # the same day instead of two different days. The UNIQUE constraint
    # enforces that in the schema (upsert via ON CONFLICT), not in Python, so
    # a second writer or a retried call cannot slip a duplicate in.
    #
    # `recorded_on` is a DATE, not a TIMESTAMPTZ, precisely so that key holds —
    # a TIMESTAMPTZ natural key would let the same day record twice a second
    # apart.
    #
    # `tenant_id` carries `REFERENCES tenants(id) ON DELETE CASCADE` directly
    # (the `training_run_metrics` / `service_config` pattern below), not the
    # bare TEXT column the older inventory tables use — this table is created
    # AFTER `cascade_tenant_id_foreign_keys` in migration order, so a bare
    # column here would miss that pass's single sweep and sit un-cascaded
    # until a second server restart re-ran it (test_tenant_cascade_fk.py's
    # catalog check would fail in between).
    ("create_inventory_recommendation_log", """
        CREATE TABLE IF NOT EXISTS inventory_recommendation_log (
            id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            sku               TEXT NOT NULL,
            recorded_on       DATE NOT NULL,
            signal            TEXT NOT NULL,
            recommended_qty   FLOAT,
            current_stock     FLOAT,
            reorder_point     FLOAT,
            safety_stock      FLOAT,
            avg_daily_demand  FLOAT,
            lead_time_days    FLOAT,
            session_id        TEXT NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, sku, recorded_on)
        )
    """),
    # Serves both reads this table exists for: the per-SKU "why did it change"
    # comparison (tenant_id, sku, recorded_on DESC — a prefix of the UNIQUE
    # index above already serves this) and the tenant-wide "cost of ignoring"
    # window scan across every SKU, which is NOT a prefix of that index and
    # needs its own.
    ("create_inventory_recommendation_log_window_idx",
     "CREATE INDEX IF NOT EXISTS inventory_recommendation_log_window_idx "
     "ON inventory_recommendation_log (tenant_id, recorded_on DESC)"),

    # Order cadence per supplier (stability.md 17/19.3): a buyer places one
    # order to one supplier on a cadence, not forty a day. Without a review
    # period the product sized the order-up-to level on the reorder point
    # itself, so the moment a shipment landed the position was back at the
    # trigger and the next look re-fired — the buyer's own batching, not the
    # product's arithmetic, was what kept that from showing on screen. The
    # review period is how many days this buyer actually waits between orders
    # to this supplier; `backend/inventory/service.py` adds it to the lead
    # time to form the PROTECTION INTERVAL the order has to last through (see
    # `_calc_recommended`'s docstring).
    #
    # DEFAULT 0 on purpose, and additive: 0 means "no declared cadence",
    # which collapses the protection interval back to the lead time alone —
    # today's exact arithmetic, for every supplier nobody has told this to.
    # `test_review_period_zero_reproduces_today_exactly` pins that.
    ("add_suppliers_review_period_days",
     "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS review_period_days INT "
     "NOT NULL DEFAULT 0 CHECK (review_period_days >= 0)"),

    # Idempotent purchase-order creation. A second tap on "Descargar orden de
    # compra" (or a client retrying a request whose answer it never saw) used
    # to write a second, identical order: OC-000003 and OC-000004, 5 s apart,
    # same SKU and quantity — and both then counted as stock on its way.
    # The client sends one key per cart submission; the PARTIAL unique index
    # is what makes a replay resolve to the first order even when the two
    # requests race (the loser hits the index, not a read-then-write window).
    # Nullable: API callers that send no key keep today's behaviour.
    # `idempotency_fingerprint` is a hash of what was ordered, so a key reused
    # for a DIFFERENT order is refused instead of silently answered with the
    # wrong PO.
    ("add_po_log_idempotency_key",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS idempotency_key TEXT"),
    ("add_po_log_idempotency_fingerprint",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS idempotency_fingerprint TEXT"),
    ("create_po_log_idempotency_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS po_log_tenant_idempotency_key_uniq "
     "ON inventory_po_log (tenant_id, idempotency_key) "
     "WHERE idempotency_key IS NOT NULL"),
    # ── Semáforo multipliers (backend/inventory/signal_thresholds.py) ──────────
    # The two lead-time multiples the signal is judged by, configurable per
    # tenant (scope 'global'), supplier or category on the existing planning-
    # rule rows. NULL on purpose: NULL means "nobody configured this", which is
    # NOT the same as a tenant that saved 0.5/3.0 (silent-failures, question
    # 3). Both are written together and resolved as one pair, so the CHECK only
    # has to guard the ordering when a pair is present.
    ("add_stock_defaults_order_now_factor",
     "ALTER TABLE stock_defaults ADD COLUMN IF NOT EXISTS order_now_factor FLOAT"),
    ("add_stock_defaults_overstock_factor",
     "ALTER TABLE stock_defaults ADD COLUMN IF NOT EXISTS overstock_factor FLOAT"),
    ("add_stock_defaults_signal_factors_check",
     "ALTER TABLE stock_defaults ADD CONSTRAINT stock_defaults_signal_factors_check "
     "CHECK (order_now_factor IS NULL OR overstock_factor IS NULL OR "
     "(order_now_factor > 0 AND order_now_factor < overstock_factor))"),
    # ── Acceptance of the Terms and the Privacy Policy (2026-10-02) ──────────
    # When the person accepted, and which version (backend/users/terms.py).
    # Set at signup and on trial accounts. NULL on every user that predates the
    # record and on users an admin invited — nobody accepted on their behalf.
    ("add_users_terms_accepted_at",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ"),
    ("add_users_terms_version",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_version TEXT"),
]


# ── Social sign-in (backend/auth/social/) ────────────────────────────────────
# Kept as its own list, appended, so it never collides with edits to the long
# list above. All additive; nothing here changes an existing row's meaning.
_SOCIAL_LOGIN = [
    # Whether the account has a password anybody chose. FALSE for an account
    # created through a provider (its `hashed_password` is a random hash
    # nobody knows) and for one whose unverified password was dropped when a
    # provider proved the mailbox belonged to someone else. DEFAULT TRUE: every
    # existing account was created with a password.
    #
    # Guarded by a catalog check rather than `ADD COLUMN IF NOT EXISTS`: the
    # latter still takes ACCESS EXCLUSIVE on `users` on every boot, and an
    # instance starting while another holds a transaction on `users` then
    # queues every login behind a no-op (measured 2026-10-02: a boot stalled
    # five minutes and blocked an unrelated INSERT the whole time).
    ("add_users_has_password",
     """DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'users'
               AND column_name = 'has_password'
          ) THEN
            ALTER TABLE users ADD COLUMN has_password BOOLEAN NOT NULL DEFAULT TRUE;
          END IF;
        END $$"""),
    # One row per (provider account -> our user). (provider, subject) is the
    # identity — the email can change at the provider, the subject never does.
    # One identity per provider per user: a second Google account cannot be
    # stacked onto the same login.
    ("create_user_identities",
     """CREATE TABLE IF NOT EXISTS user_identities (
         id           TEXT PRIMARY KEY,
         user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         provider     TEXT NOT NULL,
         subject      TEXT NOT NULL,
         email        TEXT,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         last_used_at TIMESTAMPTZ,
         UNIQUE (provider, subject),
         UNIQUE (user_id, provider)
     )"""),
    # A started sign-in: state, PKCE verifier and nonce, server-side and
    # single-use (consumed with DELETE ... RETURNING). Only the HASH of the
    # state and of the browser-binding cookie is stored.
    ("create_oauth_flows",
     """CREATE TABLE IF NOT EXISTS oauth_flows (
         state_hash     TEXT PRIMARY KEY,
         provider       TEXT NOT NULL,
         code_verifier  TEXT NOT NULL,
         nonce          TEXT NOT NULL,
         binding_hash   TEXT NOT NULL,
         intent         TEXT NOT NULL DEFAULT 'login',
         terms_accepted BOOLEAN NOT NULL DEFAULT FALSE,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         expires_at     TIMESTAMPTZ NOT NULL
     )"""),
    # The one-time code that carries a finished sign-in from the provider's
    # redirect to the page that stores the tokens — so no token is ever in a
    # URL. 60 seconds, single use, hash only.
    ("create_oauth_handoffs",
     """CREATE TABLE IF NOT EXISTS oauth_handoffs (
         code_hash      TEXT PRIMARY KEY,
         user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         provider       TEXT NOT NULL,
         is_new_account BOOLEAN NOT NULL DEFAULT FALSE,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         expires_at     TIMESTAMPTZ NOT NULL
     )"""),
]
_MIGRATIONS += _SOCIAL_LOGIN


# ── Enterprise slice (2026-10-04): retrain history, run lineage ──────────────
_ENTERPRISE = [
    # One row per time a schedule was due and what came of it: launched,
    # skipped because nothing new had arrived, or failed. `jobs` only records
    # runs that started, so a skip left no trace at all.
    ("create_schedule_runs",
     """CREATE TABLE IF NOT EXISTS schedule_runs (
         id            TEXT PRIMARY KEY,
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         schedule_id   TEXT NOT NULL,
         ran_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         outcome       TEXT NOT NULL,
         reason        TEXT,
         reason_params JSONB NOT NULL DEFAULT '{}',
         session_id    TEXT,
         dataset_id    TEXT,
         content_hash  TEXT
     )"""),
    ("create_schedule_runs_idx",
     "CREATE INDEX IF NOT EXISTS idx_schedule_runs_tenant_time "
     "ON schedule_runs (tenant_id, ran_at DESC)"),
    ("create_schedule_runs_sched_idx",
     "CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule "
     "ON schedule_runs (schedule_id, ran_at DESC)"),
    # Where a dataset row came from when a schedule re-materialized it, so the
    # schedule can later free the snapshots nothing references any more.
    ("add_datasets_created_by_schedule",
     "ALTER TABLE datasets ADD COLUMN IF NOT EXISTS created_by_schedule_id TEXT"),
    # The lineage manifest: how one training run was produced. Written once when
    # the run ends (one row per training job, so a retried session keeps every
    # attempt); an UPDATE is refused by the trigger below. No FK to
    # `sessions` on purpose: deleting a forecast must not erase the record of how
    # it was made. The tenant FK keeps tenant erasure complete.
    ("create_session_manifests",
     """CREATE TABLE IF NOT EXISTS session_manifests (
         id            TEXT PRIMARY KEY,
         session_id    TEXT NOT NULL,
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         job_id        TEXT,
         outcome       TEXT NOT NULL,
         manifest      JSONB NOT NULL,
         created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_session_manifests_idx",
     "CREATE INDEX IF NOT EXISTS idx_session_manifests_session "
     "ON session_manifests (tenant_id, session_id, created_at DESC)"),
    ("create_session_manifests_guard_fn",
     """CREATE OR REPLACE FUNCTION session_manifests_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'session_manifests rows are immutable';
        END;
        $$ LANGUAGE plpgsql"""),
    ("create_session_manifests_guard",
     """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'session_manifests_no_update') THEN
            CREATE TRIGGER session_manifests_no_update
              BEFORE UPDATE ON session_manifests
              FOR EACH ROW EXECUTE FUNCTION session_manifests_immutable();
          END IF;
        END $$"""),
    # The latest realised-accuracy reading per session: how the forecast is doing
    # against sales that arrived after it was made, next to how it did at
    # training time. ONE row per session (an upsert on every new sales upload),
    # unlike `accuracy_snapshots` which is one row per (sku, date) and is filled
    # by hand from a CSV of actuals. `alerted_at` is the idempotency latch for
    # the single in-app alert: set when the alert fires, cleared when the
    # forecast recovers, so re-reading the same upload never alerts twice.
    ("create_session_accuracy_tracking",
     """CREATE TABLE IF NOT EXISTS session_accuracy_tracking (
         session_id      TEXT PRIMARY KEY,
         tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         dataset_id      TEXT,
         status          TEXT NOT NULL,
         baseline_wape   DOUBLE PRECISION,
         realised_wape   DOUBLE PRECISION,
         degradation_pct DOUBLE PRECISION,
         bias            DOUBLE PRECISION,
         threshold_pct   DOUBLE PRECISION,
         n_points        INT NOT NULL DEFAULT 0,
         n_skus          INT NOT NULL DEFAULT 0,
         compared_from   DATE,
         compared_to     DATE,
         alerted_at      TIMESTAMPTZ,
         computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_session_accuracy_tracking_idx",
     "CREATE INDEX IF NOT EXISTS idx_session_accuracy_tracking_tenant "
     "ON session_accuracy_tracking (tenant_id)"),

    # Sales by e-mail (backend/inbound_email/). One private address per tenant:
    # `token` is the secret half of sales+<token>@<domain>, so it is unique and
    # is NEVER exported. Regenerating it overwrites the column, which is what
    # makes the old address stop working. `allowed_senders` is the admin's
    # allow-list on top of the tenant's own verified analyst/admin users.
    ("create_inbound_email_addresses",
     """CREATE TABLE IF NOT EXISTS inbound_email_addresses (
         tenant_id       TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
         token           TEXT NOT NULL UNIQUE,
         allowed_senders JSONB NOT NULL DEFAULT '[]',
         created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         rotated_at      TIMESTAMPTZ
     )"""),
    # One row per received attachment (or per message with none). The unique
    # key is what makes a provider's retry a no-op: the claim INSERT either
    # wins or does nothing. `attachment_sha256` is '' for a message with no
    # attachment so the key stays total.
    ("create_inbound_email_messages",
     """CREATE TABLE IF NOT EXISTS inbound_email_messages (
         id                TEXT PRIMARY KEY,
         tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         message_id        TEXT NOT NULL,
         sender            TEXT NOT NULL DEFAULT '',
         filename          TEXT,
         attachment_sha256 TEXT NOT NULL DEFAULT '',
         size_bytes        BIGINT,
         outcome           TEXT NOT NULL DEFAULT 'processing',
         reason            TEXT,
         reason_params     JSONB NOT NULL DEFAULT '{}',
         dataset_id        TEXT,
         retrain           TEXT,
         received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         UNIQUE (tenant_id, message_id, attachment_sha256)
     )"""),
    ("create_inbound_email_messages_idx",
     "CREATE INDEX IF NOT EXISTS idx_inbound_email_messages_tenant "
     "ON inbound_email_messages (tenant_id, received_at DESC)"),
]
_MIGRATIONS += _ENTERPRISE

# ── Physical stock count (2026-10-05) ────────────────────────────────────────
# A count session walks one warehouse with a phone; its lines hold what was
# counted next to what the system believed AT THAT MOMENT. Applying writes the
# difference into inventory_stock and leaves one row per adjustment in
# `stock_adjustments`, the ledger of stock changes that are neither a sale nor a
# reception.
_STOCK_COUNT = [
    ("create_stock_counts",
     """CREATE TABLE IF NOT EXISTS stock_counts (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         warehouse      TEXT NOT NULL,
         status         TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'closed', 'applied', 'cancelled')),
         scope_category TEXT,
         scope_supplier TEXT,
         notes          TEXT,
         created_by     TEXT,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         closed_at      TIMESTAMPTZ,
         closed_by      TEXT,
         applied_at     TIMESTAMPTZ,
         applied_by     TEXT,
         cancelled_at   TIMESTAMPTZ,
         cancelled_by   TEXT
     )"""),
    ("create_stock_counts_tenant_idx",
     "CREATE INDEX IF NOT EXISTS stock_counts_tenant_idx ON stock_counts (tenant_id, created_at DESC)"),
    ("create_stock_count_lines",
     """CREATE TABLE IF NOT EXISTS stock_count_lines (
         id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id           TEXT NOT NULL,
         count_id            TEXT NOT NULL REFERENCES stock_counts(id) ON DELETE CASCADE,
         sku                 TEXT NOT NULL,
         counted_qty         FLOAT NOT NULL CHECK (counted_qty >= 0),
         system_qty_at_count FLOAT NOT NULL,
         source              TEXT NOT NULL DEFAULT 'manual'
                             CHECK (source IN ('scan', 'manual')),
         scanned_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         scanned_by          TEXT,
         applied_at          TIMESTAMPTZ,
         applied_by          TEXT,
         applied_from        FLOAT,
         applied_to          FLOAT,
         UNIQUE (count_id, sku)
     )"""),
    ("create_stock_count_lines_tenant_idx",
     "CREATE INDEX IF NOT EXISTS stock_count_lines_tenant_idx ON stock_count_lines (tenant_id, count_id)"),
    # One row per scan the phone has already delivered, so a retry after a lost
    # response (or an offline queue replayed twice) cannot add the same units twice.
    ("create_stock_count_ops",
     """CREATE TABLE IF NOT EXISTS stock_count_ops (
         count_id   TEXT NOT NULL REFERENCES stock_counts(id) ON DELETE CASCADE,
         client_ref TEXT NOT NULL,
         tenant_id  TEXT NOT NULL,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (count_id, client_ref)
     )"""),
    ("create_stock_adjustments",
     """CREATE TABLE IF NOT EXISTS stock_adjustments (
         id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id   TEXT NOT NULL,
         sku         TEXT NOT NULL,
         warehouse   TEXT NOT NULL,
         qty_before  FLOAT NOT NULL,
         qty_after   FLOAT NOT NULL,
         delta       FLOAT NOT NULL,
         unit_cost   FLOAT,
         reason      TEXT NOT NULL,
         ref_id      TEXT,
         created_by  TEXT,
         created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_stock_adjustments_sku_idx",
     "CREATE INDEX IF NOT EXISTS stock_adjustments_sku_idx ON stock_adjustments (tenant_id, sku, created_at DESC)"),
]
_MIGRATIONS += _STOCK_COUNT


# ── Persisted models and re-forecasts (backend/model_registry/) ──────────────
# Its own appended list, like the two above. All additive.
_MODEL_ARTIFACTS = [
    # One row per stored artifact FILE of a session: one per model family plus a
    # `context` file. `content_hash` is the SHA-256 of the stored bytes and is
    # verified before the file is parsed. A re-forecast session does not copy
    # its parent's files: it registers the same paths with
    # `inherited_from_session_id` set, so the chain "full fit -> daily
    # re-forecasts" keeps one set of models on disk. Sessions are permanent and
    # so are these rows; the tenant FK only makes tenant erasure complete.
    ("create_model_artifacts",
     """CREATE TABLE IF NOT EXISTS model_artifacts (
         id                        TEXT PRIMARY KEY,
         tenant_id                 TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id                TEXT NOT NULL,
         family                    TEXT NOT NULL,
         kind                      TEXT NOT NULL,
         version                   INT  NOT NULL DEFAULT 1,
         format                    TEXT NOT NULL,
         storage_path              TEXT NOT NULL,
         content_hash              TEXT NOT NULL,
         size_bytes                BIGINT NOT NULL,
         metadata                  JSONB NOT NULL DEFAULT '{}'::jsonb,
         inherited_from_session_id TEXT,
         created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         UNIQUE (tenant_id, session_id, family)
     )"""),
    ("create_model_artifacts_session_idx",
     "CREATE INDEX IF NOT EXISTS idx_model_artifacts_session "
     "ON model_artifacts (tenant_id, session_id)"),
    # A re-forecast is a NEW session that names the one it was derived from;
    # the parent is never modified.
    ("add_sessions_is_reforecast",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_reforecast BOOLEAN NOT NULL DEFAULT FALSE"),
    ("add_sessions_parent_session_id",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS parent_session_id TEXT"),
    # When the models behind this session were last FITTED (not re-forecast). A
    # full training stamps its own completion; a re-forecast inherits its
    # parent's, so "how old are these models" survives a chain of re-forecasts.
    ("add_sessions_last_full_refit_at",
     "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_full_refit_at TIMESTAMPTZ"),
    # 'refit' (default, today's behaviour) | 'reforecast': re-forecast when new
    # data arrived and the last full refit is younger than
    # `reforecast_full_refit_days`, otherwise refit.
    ("add_scheduled_jobs_retrain_mode",
     "ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS retrain_mode TEXT NOT NULL DEFAULT 'refit'"),
]
_MIGRATIONS += _MODEL_ARTIFACTS

# PO approval workflow + forecast adjustments (own module, see its header).
from backend.inventory.approval_migrations import MIGRATIONS as _APPROVALS  # noqa: E402
_MIGRATIONS += _APPROVALS
from backend.inventory.committed_demand_migrations import MIGRATIONS as _COMMITTED  # noqa: E402
_MIGRATIONS += _COMMITTED
# Blanket supply contracts: after committed demand, whose table they extend.
from backend.inventory.supply_contract_migrations import MIGRATIONS as _SUPPLY_CONTRACTS  # noqa: E402
_MIGRATIONS += _SUPPLY_CONTRACTS
from backend.inventory.spike_edit_migrations import MIGRATIONS as _SPIKE_EDITS  # noqa: E402
_MIGRATIONS += _SPIKE_EDITS
from backend.inventory.analogy_migrations import MIGRATIONS as _SKU_ANALOGIES  # noqa: E402
_MIGRATIONS += _SKU_ANALOGIES
from backend.inventory.demand_plan_migrations import MIGRATIONS as _DEMAND_PLANS  # noqa: E402
_MIGRATIONS += _DEMAND_PLANS
from backend.webhooks.migrations import MIGRATIONS as _WEBHOOK_DELIVERY  # noqa: E402
_MIGRATIONS += _WEBHOOK_DELIVERY
from backend.inventory.purchase_budget_migrations import MIGRATIONS as _PURCHASE_BUDGETS  # noqa: E402
_MIGRATIONS += _PURCHASE_BUDGETS
from backend.inventory.po_confirmation_migrations import MIGRATIONS as _PO_CONFIRMATIONS  # noqa: E402
_MIGRATIONS += _PO_CONFIRMATIONS


# ── Inventory status snapshot (docs/status-performance.md) ───────────────────
#
# `status_input_bumps` is an insert-only ledger of "something the status
# computation reads just changed for this tenant". Statement-level triggers on
# every table that computation reads append one row per affected tenant, so the
# invalidation lives in the database and cannot be bypassed by a code path that
# forgets to call an invalidator (raw SQL, scripts, endpoints written later).
# Insert-only on purpose: a counter row per tenant would make every writer of
# that tenant queue behind one row lock until commit.
#
# `inventory_status_snapshot` holds the computed rows; `..._meta` says which
# generation is current and what inputs it was computed from.
STATUS_INPUT_TABLES = (
    "inventory_stock", "warehouses", "inventory_po_items", "inventory_po_log",
    "inventory_transfer_items", "inventory_transfer_log",
    "supplier_lead_time_obs", "suppliers", "sku_suppliers", "stock_defaults",
    "inventory_events", "inventory_event_multipliers", "inventory_snapshots",
    "session_results",
    # The two ledgers that move demand beside the forecast. A manual adjustment was
    # missing here, so a new one left the cached status stale until it expired.
    "forecast_adjustments", "committed_demand",
)


def _status_trigger_migrations() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for table in STATUS_INPUT_TABLES:
        for op, ref in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
            trig = f"status_bump_{table}_{op.lower()}"
            out.append((
                f"create_{trig}",
                f"""DO $$
                BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = '{trig}') THEN
                    CREATE TRIGGER {trig}
                      AFTER {op} ON {table}
                      REFERENCING {ref} TABLE AS changed
                      FOR EACH STATEMENT EXECUTE FUNCTION status_inputs_bump();
                  END IF;
                END $$""",
            ))
    return out


_STATUS_SNAPSHOT = [
    ("create_status_input_bumps",
     """CREATE TABLE IF NOT EXISTS status_input_bumps (
         id         BIGSERIAL PRIMARY KEY,
         tenant_id  TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         source     TEXT NOT NULL,
         at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_status_input_bumps_idx",
     "CREATE INDEX IF NOT EXISTS status_input_bumps_tenant_idx "
     "ON status_input_bumps (tenant_id, id DESC)"),
    ("create_status_inputs_bump_fn",
     """CREATE OR REPLACE FUNCTION status_inputs_bump() RETURNS trigger AS $$
        BEGIN
          -- The EXISTS guard keeps a tenant's own deletion (whose cascade
          -- fires these triggers after the tenants row is gone) from tripping
          -- the foreign key.
          INSERT INTO status_input_bumps (tenant_id, source)
          SELECT DISTINCT c.tenant_id, TG_TABLE_NAME
            FROM changed c
           WHERE EXISTS (SELECT 1 FROM tenants t WHERE t.id = c.tenant_id);
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql"""),
    ("create_inventory_status_snapshot_meta",
     """CREATE TABLE IF NOT EXISTS inventory_status_snapshot_meta (
         tenant_id      TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id     TEXT NOT NULL,
         period         TEXT NOT NULL,
         service_level  DOUBLE PRECISION NOT NULL,
         generation     BIGINT NOT NULL,
         inputs_version BIGINT NOT NULL,
         code_hash      TEXT NOT NULL,
         computed_on    DATE NOT NULL,
         computed_at    TIMESTAMPTZ NOT NULL,
         n_rows         INT NOT NULL,
         PRIMARY KEY (tenant_id, session_id, period, service_level)
     )"""),
    ("create_inventory_status_snapshot",
     """CREATE TABLE IF NOT EXISTS inventory_status_snapshot (
         tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id      TEXT NOT NULL,
         period          TEXT NOT NULL,
         service_level   DOUBLE PRECISION NOT NULL,
         generation      BIGINT NOT NULL,
         sku             TEXT NOT NULL,
         urgency_pos     INT NOT NULL,
         signal          TEXT NOT NULL,
         supplier_lc     TEXT NOT NULL,
         search_text     TEXT NOT NULL,
         has_stock       BOOLEAN NOT NULL,
         has_forecast    BOOLEAN NOT NULL,
         inventory_value DOUBLE PRECISION,
         sort_keys       JSONB NOT NULL,
         item            JSONB NOT NULL,
         computed_at     TIMESTAMPTZ NOT NULL,
         PRIMARY KEY (tenant_id, session_id, period, service_level, generation, sku)
     )"""),
    ("create_inventory_status_snapshot_idx",
     "CREATE INDEX IF NOT EXISTS inventory_status_snapshot_order_idx "
     "ON inventory_status_snapshot "
     "(tenant_id, session_id, period, service_level, generation, urgency_pos)"),
] + _status_trigger_migrations()
_MIGRATIONS += _STATUS_SNAPSHOT


# ── Enterprise access (2026-10-05): per-tenant OIDC sign-on, warehouse scopes ─
_ENTERPRISE_ACCESS = [
    # One OpenID Connect provider per tenant (backend/auth/sso/). The client
    # secret is stored ONLY as Fernet ciphertext (service_config/crypto.py).
    # The three endpoints are copied from the provider's discovery document at
    # save time, so a sign-in never trusts a URL read at request time.
    ("create_sso_providers",
     """CREATE TABLE IF NOT EXISTS sso_providers (
         tenant_id              TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
         issuer                 TEXT NOT NULL,
         client_id              TEXT NOT NULL,
         client_secret_enc      TEXT NOT NULL,
         authorization_endpoint TEXT NOT NULL,
         token_endpoint         TEXT NOT NULL,
         jwks_uri               TEXT NOT NULL,
         token_auth_method      TEXT NOT NULL DEFAULT 'client_secret_basic',
         allowed_domains        JSONB NOT NULL DEFAULT '[]',
         default_role           TEXT NOT NULL DEFAULT 'viewer',
         enforce_sso            BOOLEAN NOT NULL DEFAULT FALSE,
         groups_claim           TEXT,
         group_roles            JSONB NOT NULL DEFAULT '{}',
         enabled                BOOLEAN NOT NULL DEFAULT TRUE,
         created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_by             TEXT
     )"""),
    # A domain belongs to exactly one tenant, instance-wide. That is what makes
    # "which tenant does this e-mail sign in to" a lookup with one answer.
    ("create_sso_domains",
     """CREATE TABLE IF NOT EXISTS sso_domains (
         domain     TEXT PRIMARY KEY,
         tenant_id  TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_sso_domains_tenant_idx",
     "CREATE INDEX IF NOT EXISTS idx_sso_domains_tenant ON sso_domains (tenant_id)"),
    # Warehouse scope. NULL = every warehouse (all existing rows). A JSON array
    # of warehouse ids = only those; an EMPTY array = none at all. Deleting a
    # warehouse therefore can only ever shrink a scope, never widen it.
    # Guarded by a catalog check, not ADD COLUMN IF NOT EXISTS, for the lock
    # reason written above `add_users_has_password`.
    ("add_users_warehouse_scope",
     """DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'users'
               AND column_name = 'warehouse_scope'
          ) THEN
            ALTER TABLE users ADD COLUMN warehouse_scope JSONB;
          END IF;
        END $$"""),
    ("add_api_keys_warehouse_scope",
     "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS warehouse_scope JSONB"),
]
_MIGRATIONS += _ENTERPRISE_ACCESS


# ── "Send feedback" (2026-10-05) ─────────────────────────────────────────────
# What a signed-in person typed into the feedback dialog, plus the facts they
# saw on the confirmation step. The screenshot is a FILE under storage/feedback/
# (never a column): `screenshot_path` is relative to that tenant's folder. The
# two consent flags are separate on purpose and each carries its own timestamp:
# `consent_reply` = "you may e-mail me about this report", `consent_news` =
# explicit opt-in to product news. No tenant FK on purpose, like most tenant
# tables: erasure is the explicit list in tenants/data_export.py.
_FEEDBACK = [
    ("create_feedback_reports",
     """CREATE TABLE IF NOT EXISTS feedback_reports (
         id                  TEXT PRIMARY KEY,
         tenant_id           TEXT NOT NULL,
         user_id             TEXT NOT NULL,
         created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         message             TEXT NOT NULL,
         error_code          TEXT,
         page_path           TEXT,
         user_agent          TEXT,
         app_version         TEXT,
         account_email       TEXT,
         screenshot_path     TEXT,
         consent_reply       BOOLEAN NOT NULL DEFAULT FALSE,
         consent_reply_at    TIMESTAMPTZ,
         consent_news        BOOLEAN NOT NULL DEFAULT FALSE,
         consent_news_at     TIMESTAMPTZ,
         notified            BOOLEAN NOT NULL DEFAULT FALSE
     )"""),
    ("idx_feedback_reports_tenant",
     "CREATE INDEX IF NOT EXISTS idx_feedback_reports_tenant "
     "ON feedback_reports (tenant_id, created_at DESC)"),
    ("idx_feedback_reports_user",
     "CREATE INDEX IF NOT EXISTS idx_feedback_reports_user "
     "ON feedback_reports (user_id, created_at DESC)"),
]
_MIGRATIONS += _FEEDBACK
# ── Online payments (backend/billing/) ───────────────────────────────────────
# Stripe and PayPal, hosted pages only. Everything additive: with no provider
# configured nothing writes to these tables and every tenant behaves exactly as
# before.
_BILLING = [
    # Who set the tier. 'manual' for every existing row: until today the only
    # way onto `paid` was the owner setting the column by hand, and billing
    # must never undo that (the grandfather rule in billing/entitlement.py).
    # Catalog-guarded, not ADD COLUMN IF NOT EXISTS, for the lock reason
    # written above `add_users_has_password`: `tenants` is read by every login.
    ("add_tenants_tier_source",
     """DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'tenants'
               AND column_name = 'tier_source'
          ) THEN
            ALTER TABLE tenants ADD COLUMN tier_source TEXT NOT NULL DEFAULT 'manual';
          END IF;
        END $$"""),
    ("add_tenants_tier_changed_at",
     """DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'tenants'
               AND column_name = 'tier_changed_at'
          ) THEN
            ALTER TABLE tenants ADD COLUMN tier_changed_at TIMESTAMPTZ;
          END IF;
        END $$"""),
    # A tenant's customer record at each provider. One per (tenant, provider);
    # a provider customer belongs to exactly one tenant, instance-wide.
    ("create_billing_customers",
     """CREATE TABLE IF NOT EXISTS billing_customers (
         id                   TEXT PRIMARY KEY,
         tenant_id            TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         provider             TEXT NOT NULL,
         provider_customer_id TEXT NOT NULL,
         created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         UNIQUE (provider, provider_customer_id),
         UNIQUE (tenant_id, provider)
     )"""),
    # What the provider last told us about each subscription, as fetched from
    # the provider when its webhook arrived. `last_event_at` is the creation
    # time of the newest event applied: an older event arriving late is
    # recorded and ignored, never applied over a newer one.
    ("create_billing_subscriptions",
     """CREATE TABLE IF NOT EXISTS billing_subscriptions (
         id                       TEXT PRIMARY KEY,
         tenant_id                TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         provider                 TEXT NOT NULL,
         provider_subscription_id TEXT NOT NULL,
         status                   TEXT NOT NULL,
         plan                     TEXT,
         current_period_end       TIMESTAMPTZ,
         cancel_at_period_end     BOOLEAN NOT NULL DEFAULT FALSE,
         past_due_since           TIMESTAMPTZ,
         ended_at                 TIMESTAMPTZ,
         last_event_at            TIMESTAMPTZ,
         created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         UNIQUE (provider, provider_subscription_id)
     )"""),
    ("create_billing_subscriptions_tenant_idx",
     "CREATE INDEX IF NOT EXISTS idx_billing_subscriptions_tenant "
     "ON billing_subscriptions (tenant_id)"),
    # Every verified webhook, once. The UNIQUE (provider, event_id) IS the
    # idempotency: a replay inserts nothing and therefore changes nothing.
    # Rows are never updated after their transaction commits and never deleted
    # except with the whole tenant. `tenant_id` is NULL for an event that
    # named no tenant we know (kept: it is still the record that it arrived).
    ("create_billing_events",
     """CREATE TABLE IF NOT EXISTS billing_events (
         id               TEXT PRIMARY KEY,
         provider         TEXT NOT NULL,
         event_id         TEXT NOT NULL,
         event_type       TEXT NOT NULL,
         tenant_id        TEXT,
         event_created_at TIMESTAMPTZ,
         received_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         processed_at     TIMESTAMPTZ,
         outcome          TEXT,
         UNIQUE (provider, event_id)
     )"""),
    ("create_billing_events_tenant_idx",
     "CREATE INDEX IF NOT EXISTS idx_billing_events_tenant "
     "ON billing_events (tenant_id, received_at DESC)"),
]
_MIGRATIONS += _BILLING


# ── SCIM 2.0 provisioning (backend/scim/) ────────────────────────────────────
# Additive: with no token minted nothing reads or writes these tables.
_SCIM = [
    # One LIVE token per tenant (the partial unique index); revoked rows stay as
    # the record of who minted and who revoked what. Only the SHA-256 of the
    # secret is stored; the id is the non-secret half of the token.
    ("create_scim_tokens",
     """CREATE TABLE IF NOT EXISTS scim_tokens (
         id             TEXT PRIMARY KEY,
         tenant_id      TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         secret_hash    TEXT NOT NULL,
         manage_admins  BOOLEAN NOT NULL DEFAULT FALSE,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         created_by     TEXT,
         last_used_at   TIMESTAMPTZ,
         revoked_at     TIMESTAMPTZ,
         revoked_by     TEXT
     )"""),
    ("create_scim_tokens_live_idx",
     "CREATE UNIQUE INDEX IF NOT EXISTS uq_scim_tokens_live "
     "ON scim_tokens (tenant_id) WHERE revoked_at IS NULL"),
    # The provisioning log the admin screen shows: every write the identity
    # provider made, and every one it was refused, with the code.
    ("create_scim_events",
     """CREATE TABLE IF NOT EXISTS scim_events (
         id             TEXT PRIMARY KEY,
         tenant_id      TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         token_id       TEXT,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         operation      TEXT NOT NULL,
         resource_type  TEXT NOT NULL,
         resource_id    TEXT,
         email          TEXT,
         outcome        TEXT NOT NULL,
         http_status    INTEGER NOT NULL,
         error_code     TEXT,
         changes        JSONB NOT NULL DEFAULT '{}'
     )"""),
    ("create_scim_events_tenant_idx",
     "CREATE INDEX IF NOT EXISTS idx_scim_events_tenant "
     "ON scim_events (tenant_id, created_at DESC)"),
    # What SCIM knows about a person that `users` has no column for: the
    # provider's own id for them and the split name. A row here also means
    # "this person was provisioned or adopted by the identity provider".
    ("create_scim_user_links",
     """CREATE TABLE IF NOT EXISTS scim_user_links (
         user_id      TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         external_id  TEXT,
         given_name   TEXT,
         family_name  TEXT,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_scim_user_links_external_idx",
     "CREATE INDEX IF NOT EXISTS idx_scim_user_links_external "
     "ON scim_user_links (tenant_id, external_id)"),
]
_MIGRATIONS += _SCIM


# ── Session and password policy (backend/auth/session_policy.py) ─────────────
# Additive: with no row in `tenant_session_policies` nothing reads a new column
# for any decision, and every account behaves exactly as before. The Rust API
# owns the admin routes; Python enforces the policy at login, refresh,
# password change and token validation.
_SESSION_POLICY = [
    # One row per tenant; every limit NULL (or FALSE) means "not set". Bounds
    # are checked by the admin route AND here, so a hand-written row cannot
    # hold a value the enforcement code was never meant to see.
    ("create_tenant_session_policies",
     """CREATE TABLE IF NOT EXISTS tenant_session_policies (
         tenant_id                TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
         max_session_hours        INTEGER CHECK (max_session_hours BETWEEN 1 AND 168),
         idle_timeout_minutes     INTEGER CHECK (idle_timeout_minutes BETWEEN 5 AND 1440),
         min_password_length      INTEGER CHECK (min_password_length BETWEEN 8 AND 64),
         require_mixed_case       BOOLEAN NOT NULL DEFAULT FALSE,
         require_symbol           BOOLEAN NOT NULL DEFAULT FALSE,
         password_max_age_days    INTEGER CHECK (password_max_age_days BETWEEN 7 AND 730),
         -- When the age limit was switched on. Passwords older than the limit
         -- on that day get the whole limit from then, not an instant lockout.
         password_max_age_since   TIMESTAMPTZ,
         max_concurrent_sessions  INTEGER CHECK (max_concurrent_sessions BETWEEN 1 AND 20),
         lockout_threshold        INTEGER CHECK (lockout_threshold BETWEEN 3 AND 20),
         lockout_minutes          INTEGER CHECK (lockout_minutes BETWEEN 1 AND 1440),
         updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_by               TEXT
     )"""),
    # Last authenticated request (idle timeout), when the password was last
    # set (its age), and the failed-login counter with its lock.
    ("add_users_last_activity_at",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ"),
    ("add_users_password_changed_at",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ"),
    ("add_users_failed_login_count",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0"),
    ("add_users_locked_until",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ"),
]
_MIGRATIONS += _SESSION_POLICY


# Postgres SQLSTATE codes that mean "this object is already there", which is the
# expected outcome of re-running an idempotent migration on a live database.
# Everything else is a real failure and must not be swallowed.
#   42P07 duplicate_table · 42701 duplicate_column · 42710 duplicate_object
#   42P16 invalid_table_definition (re-adding an existing PK/constraint)
_ALREADY_APPLIED_SQLSTATES = frozenset({"42P07", "42701", "42710", "42P16"})


class MigrationError(RuntimeError):
    """One or more migrations failed for a reason other than 'already applied'."""


# Any constant works as long as every instance agrees; this one is just a fixed
# 64-bit id nothing else in the product uses.
_MIGRATION_LOCK_ID = 8_412_330_071_004_517


def _with_migration_lock(fn):
    """Run `fn` while holding a cluster-wide advisory lock.

    Every instance runs migrations at boot. With one process that is harmless;
    the moment a public-API container and the app container start together, both
    walk the same list against the same database at the same time. Today the
    statements are idempotent and the losers get "already exists", which is why
    it has held — but "it works because every statement happens to be
    re-runnable" is a property nobody is checking, and the first migration that
    is not re-runnable turns a deploy into a coin flip.

    `pg_advisory_lock` is the right tool: it is held by the SESSION, released
    automatically if the process dies, and costs nothing when uncontended. The
    second instance BLOCKS here rather than racing, then finds every migration
    already applied and moves on.

    A database that cannot grant the lock is not a reason to skip migrating —
    that would trade a rare race for a silent half-built schema — so a failure
    to acquire falls through to running unlocked, exactly as before.
    """
    from backend.db.connection import get_conn

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
            try:
                return fn()
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))
    except MigrationError:
        raise
    except Exception as exc:
        log.warning(
            "Could not take the migration advisory lock (%s) — migrating without "
            "it, as before", exc,
        )
        return fn()


def run_all(*, strict: bool = True) -> None:
    """Apply every migration, serialised across instances. See `_run_all`."""
    return _with_migration_lock(lambda: _run_all(strict=strict))


def _run_all(*, strict: bool = True) -> None:
    """
    Apply every migration in order.

    Historically this swallowed EVERY exception as a warning, so a genuinely
    broken migration — a typo, a missing FK target, a bad backfill — looked
    exactly like a no-op re-run and the server booted on a half-built schema.
    Now only "object already exists" errors are tolerated; anything else is
    logged at ERROR with its SQLSTATE and, when `strict`, raised after the whole
    list has been attempted (so the log shows every problem, not just the first).

    `strict=False` is an escape hatch for recovery/inspection tooling, never for
    normal startup.
    """
    failures: list[tuple[str, str, str]] = []  # (name, sqlstate, message)

    # Two tables are created by their own services rather than by this list,
    # yet later migrations ALTER them. The server pre-creates them at startup,
    # so only a caller that runs migrations on its own (seed_demo, tooling) on
    # a virgin database hit "relation user_preferences does not exist". Making
    # run_all self-sufficient removes the hidden ordering requirement.
    from backend.preferences.service import ensure_table as _ensure_prefs
    from backend.activity.service import ensure_table as _ensure_activity
    _ensure_prefs()
    _ensure_activity()

    for name, sql in _MIGRATIONS:
        try:
            execute(sql)
            log.debug("Migration OK: %s", name)
        except Exception as exc:
            sqlstate = getattr(exc, "pgcode", None) or ""
            if sqlstate in _ALREADY_APPLIED_SQLSTATES:
                log.debug("Migration '%s' already applied (%s)", name, sqlstate)
                continue
            log.error(
                "Migration '%s' FAILED (sqlstate=%s): %s",
                name, sqlstate or "unknown", exc,
            )
            failures.append((name, sqlstate or "unknown", str(exc)))

    if failures and strict:
        detail = "; ".join(f"{n} (sqlstate={s}): {m}" for n, s, m in failures)
        raise MigrationError(f"{len(failures)} migration(s) failed: {detail}")
