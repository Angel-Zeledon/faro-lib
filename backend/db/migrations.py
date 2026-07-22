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
                   CHECK (scope IN ('sku', 'category'));
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
         plan       TEXT NOT NULL DEFAULT 'starter',
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
    # Adoption metrics on the PO header: how many recommendations Faro made vs.
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
    # recommended_qty = what Faro suggested; final_qty = what the buyer
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
    #   cash-on-delivery/prepaid → 0 · "N mes(es)" → N*30 · "quincenal" → 15 ·
    #   first number found → N days · anything else → NULL.
    # Unparseable text stays NULL on purpose: the cash calendar reports it under
    # "missing terms" instead of inventing a due date.
    ("backfill_suppliers_payment_terms_days",
     """UPDATE suppliers
           SET payment_terms_days = CASE
               WHEN payment_terms ~* '(contado|cash|anticipad|prepag|inmediat|contra ?entrega|\\mcod\\M)'
                    THEN 0
               WHEN payment_terms ~* '([0-9]+)\\s*mes'
                    THEN LEAST((substring(payment_terms from '([0-9]+)\\s*mes'))::int * 30, 365)
               WHEN payment_terms ~* 'quincen'
                    THEN 15
               WHEN payment_terms ~ '[0-9]+'
                    THEN LEAST((substring(payment_terms from '[0-9]+'))::int, 365)
               ELSE NULL
           END
         WHERE payment_terms_days IS NULL
           AND payment_terms IS NOT NULL
           AND btrim(payment_terms) <> ''"""),
    # When the PO was sent to the supplier. The invoice clock starts at send
    # time, so without this there is no due date to compute.
    ("add_po_log_sent_at",
     "ALTER TABLE inventory_po_log ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ"),

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

    # ── Plan-based entitlements (feature: plan catalog) ──────────────────────
    # New tenants start on a time-boxed Starter trial; trial_ends_at is NULL
    # once the trial converts/expires-to-paid. Existing 'free' tenants had no
    # trial concept, so they are migrated straight to 'enterprise' (no
    # feature loss for accounts that predate this plan model) with no trial.
    ("add_tenants_trial_ends_at",
     "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ"),
    ("migrate_free_plan_to_enterprise",
     "UPDATE tenants SET plan = 'enterprise', trial_ends_at = NULL "
     "WHERE plan = 'free'"),
    # CREATE TABLE IF NOT EXISTS above never runs again on an existing
    # database, so its DEFAULT 'free' (now updated to 'starter' in the
    # CREATE TABLE itself, for fresh databases) never reaches a database that
    # already has the tenants table — only an explicit ALTER does.
    ("alter_tenants_plan_default_starter",
     "ALTER TABLE tenants ALTER COLUMN plan SET DEFAULT 'starter'"),
    ("create_integration_connections",
     """CREATE TABLE IF NOT EXISTS integration_connections (
         id           TEXT PRIMARY KEY,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         provider     TEXT NOT NULL,
         credentials  TEXT NOT NULL,
         status       TEXT NOT NULL DEFAULT 'connected',
         last_sync_at TIMESTAMPTZ,
         last_error   TEXT,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_integration_connections_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS integration_conn_tenant_provider_idx "
     "ON integration_connections (tenant_id, provider)"),
]


# Postgres SQLSTATE codes that mean "this object is already there", which is the
# expected outcome of re-running an idempotent migration on a live database.
# Everything else is a real failure and must not be swallowed.
#   42P07 duplicate_table · 42701 duplicate_column · 42710 duplicate_object
#   42P16 invalid_table_definition (re-adding an existing PK/constraint)
_ALREADY_APPLIED_SQLSTATES = frozenset({"42P07", "42701", "42710", "42P16"})


class MigrationError(RuntimeError):
    """One or more migrations failed for a reason other than 'already applied'."""


def run_all(*, strict: bool = True) -> None:
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
