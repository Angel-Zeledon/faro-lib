"""
Idempotent schema migrations — safe to run on every startup.
"""
import logging

from backend.db.connection import execute

log = logging.getLogger(__name__)

_MIGRATIONS = [
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
]


def run_all() -> None:
    for name, sql in _MIGRATIONS:
        try:
            execute(sql)
            log.debug("Migration OK: %s", name)
        except Exception as exc:
            log.warning("Migration '%s' may already be applied: %s", name, exc)
