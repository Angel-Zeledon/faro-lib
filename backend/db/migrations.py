"""
Idempotent schema migrations — safe to run on every startup.

The base-schema block below (tenants, users, sessions, datasets, …) MUST come
first: it lets a brand-new/empty database be bootstrapped from scratch, and the
incremental ALTER/CREATE migrations that follow reference these tables via FK.
All statements are CREATE TABLE IF NOT EXISTS, so they are no-ops on databases
that already have the schema (e.g. the original Supabase instance). Columns
added later live in the incremental section, not here.
"""
import logging

from backend.db.connection import execute

log = logging.getLogger(__name__)

_BASE_SCHEMA = [
    ("base_tenants",
     """CREATE TABLE IF NOT EXISTS tenants (
         id         TEXT PRIMARY KEY,
         name       TEXT NOT NULL,
         slug       TEXT UNIQUE NOT NULL,
         plan       TEXT NOT NULL DEFAULT 'free',
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

_MIGRATIONS = _BASE_SCHEMA + [
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
         stock_actual   FLOAT NOT NULL DEFAULT 0,
         stock_minimo   FLOAT NOT NULL DEFAULT 0,
         lead_time_dias INT   NOT NULL DEFAULT 15,
         costo_unitario FLOAT,
         moq            FLOAT NOT NULL DEFAULT 1,
         proveedor      TEXT,
         notas          TEXT,
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
         stock_actual FLOAT NOT NULL,
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
         skus_pedir_ya   INT NOT NULL DEFAULT 0,
         skus_pedir_pronto INT NOT NULL DEFAULT 0
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
    # cantidad_recomendada = what Faro suggested; cantidad_final = what the buyer
    # kept; status ∈ approved | modified | rejected. Rejected lines are stored
    # too (not in the order) so adoption rate is measurable.
    ("create_inventory_po_items",
     """CREATE TABLE IF NOT EXISTS inventory_po_items (
         id                   TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         po_log_id            TEXT NOT NULL REFERENCES inventory_po_log(id) ON DELETE CASCADE,
         tenant_id            TEXT NOT NULL,
         sku                  TEXT NOT NULL,
         display_name         TEXT,
         proveedor            TEXT,
         signal               TEXT,
         cantidad_recomendada FLOAT NOT NULL DEFAULT 0,
         cantidad_final       FLOAT NOT NULL DEFAULT 0,
         costo_unitario       FLOAT,
         status               TEXT NOT NULL DEFAULT 'approved',
         created_at           TIMESTAMPTZ DEFAULT NOW()
     )"""),
    ("create_inventory_po_items_log_idx",
     "CREATE INDEX IF NOT EXISTS po_items_log_idx ON inventory_po_items (po_log_id)"),
    ("create_inventory_po_items_sku_idx",
     "CREATE INDEX IF NOT EXISTS po_items_sku_idx ON inventory_po_items (tenant_id, sku)"),
    ("create_suppliers",
     """CREATE TABLE IF NOT EXISTS suppliers (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         name           TEXT NOT NULL,
         email          TEXT,
         phone          TEXT,
         whatsapp       TEXT,
         lead_time_dias INT  NOT NULL DEFAULT 15,
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
         lead_time_dias INT,
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
    ("add_po_items_cantidad_recibida",
     "ALTER TABLE inventory_po_items ADD COLUMN IF NOT EXISTS cantidad_recibida FLOAT"),
    # Real lead-time observations per supplier, learned from PO receptions.
    # Keyed by supplier NAME (po lines carry the free-text proveedor field).
    ("create_supplier_lead_time_obs",
     """CREATE TABLE IF NOT EXISTS supplier_lead_time_obs (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL,
         proveedor      TEXT NOT NULL,
         po_log_id      TEXT NOT NULL REFERENCES inventory_po_log(id) ON DELETE CASCADE,
         lead_time_days FLOAT NOT NULL,
         observed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_supplier_lead_time_obs_idx",
     "CREATE INDEX IF NOT EXISTS slto_tenant_prov_idx ON supplier_lead_time_obs (tenant_id, proveedor)"),
]


def run_all() -> None:
    for name, sql in _MIGRATIONS:
        try:
            execute(sql)
            log.debug("Migration OK: %s", name)
        except Exception as exc:
            log.warning("Migration '%s' may already be applied: %s", name, exc)
