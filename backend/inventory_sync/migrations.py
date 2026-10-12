"""Schema for inventory sync (see `backend/inventory_sync/__init__.py`).

Four tables, all tenant-scoped and cascading from `tenants`:

* `inventory_sync_profiles` — one per "keep this target in step with that
  source": the data source, the source object or saved read-only query, the
  column mapping with its transforms, the mode (full snapshot / incremental by
  watermark), the policies (missing SKUs, negatives, duplicates, unknown
  warehouses), the safety brake thresholds and the schedule. The scheduler
  lease (`lease_owner`, `lease_until`) lives on the row: claiming it is one
  UPDATE ... FOR UPDATE SKIP LOCKED, so two workers never run one profile.
* `inventory_sync_runs` — the run log. Every run, scheduled or manual, is a row
  that says what it read, created, updated, left alone and rejected, how long
  it took, which watermark it started from and reached, and the error code
  when it failed or the brake stopped it.
* `inventory_sync_keys` — the (sku, warehouse) pairs a profile has written and
  the values it last wrote. It answers "which SKUs vanished from the source"
  for the safety brake and the missing-SKU policy, and "was this field edited
  by hand since the last sync" for the conflict rule.
* `inventory_sync_staging` — the rows of the run in progress. A run first
  stages everything it read (bounded memory: batches), the brake judges the
  staged set, and only then is anything applied. A run that stops halfway
  leaves inventory exactly as it was before the apply began.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_inventory_sync_profiles",
     """CREATE TABLE IF NOT EXISTS inventory_sync_profiles (
         id                     TEXT PRIMARY KEY,
         tenant_id              TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         data_source_id         TEXT NOT NULL,
         name                   TEXT NOT NULL,
         target_entity          TEXT NOT NULL DEFAULT 'stock_levels',
         source_kind            TEXT NOT NULL,
         source_schema          TEXT,
         source_object          TEXT,
         source_query           TEXT,
         mapping                JSONB NOT NULL DEFAULT '{}'::jsonb,
         filters                JSONB NOT NULL DEFAULT '[]'::jsonb,
         default_warehouse      TEXT,
         mode                   TEXT NOT NULL DEFAULT 'full',
         watermark_column       TEXT,
         overlap_minutes        INT NOT NULL DEFAULT 10,
         watermark_value        JSONB,
         interval_minutes       INT NOT NULL DEFAULT 60,
         enabled                BOOLEAN NOT NULL DEFAULT FALSE,
         missing_policy         TEXT NOT NULL DEFAULT 'ignore',
         negative_policy        TEXT NOT NULL DEFAULT 'reject',
         duplicate_policy       TEXT NOT NULL DEFAULT 'reject',
         allow_new_warehouses   BOOLEAN NOT NULL DEFAULT FALSE,
         brake_max_vanished_pct DOUBLE PRECISION NOT NULL DEFAULT 20,
         brake_min_row_ratio    DOUBLE PRECISION NOT NULL DEFAULT 0.5,
         brake_max_rejected_pct DOUBLE PRECISION NOT NULL DEFAULT 50,
         config_hash            TEXT,
         dry_run_hash           TEXT,
         dry_run_at             TIMESTAMPTZ,
         next_run_at            TIMESTAMPTZ,
         lease_owner            TEXT,
         lease_until            TIMESTAMPTZ,
         consecutive_failures   INT NOT NULL DEFAULT 0,
         last_run_id            TEXT,
         last_status            TEXT,
         last_error_code        TEXT,
         last_success_at        TIMESTAMPTZ,
         stale_alerted_at       TIMESTAMPTZ,
         created_by             TEXT,
         created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CHECK (target_entity IN ('stock_levels')),
         CHECK (source_kind IN ('table', 'query', 'collection', 'pipeline')),
         CHECK (mode IN ('full', 'incremental')),
         CHECK (missing_policy IN ('ignore', 'mark_inactive', 'zero')),
         CHECK (negative_policy IN ('reject', 'zero')),
         CHECK (duplicate_policy IN ('reject', 'sum')),
         CHECK (interval_minutes >= 1),
         CHECK (overlap_minutes >= 0)
     )"""),
    ("create_inventory_sync_profiles_tenant_idx",
     "CREATE INDEX IF NOT EXISTS inventory_sync_profiles_tenant_idx "
     "ON inventory_sync_profiles (tenant_id)"),
    ("create_inventory_sync_profiles_due_idx",
     "CREATE INDEX IF NOT EXISTS inventory_sync_profiles_due_idx "
     "ON inventory_sync_profiles (next_run_at) WHERE enabled"),

    ("create_inventory_sync_runs",
     """CREATE TABLE IF NOT EXISTS inventory_sync_runs (
         id                TEXT PRIMARY KEY,
         tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         profile_id        TEXT NOT NULL REFERENCES inventory_sync_profiles(id) ON DELETE CASCADE,
         trigger_kind      TEXT NOT NULL,
         status            TEXT NOT NULL DEFAULT 'running',
         started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         finished_at       TIMESTAMPTZ,
         duration_ms       INT,
         rows_read         INT NOT NULL DEFAULT 0,
         rows_valid        INT NOT NULL DEFAULT 0,
         created           INT NOT NULL DEFAULT 0,
         updated           INT NOT NULL DEFAULT 0,
         unchanged         INT NOT NULL DEFAULT 0,
         rejected          INT NOT NULL DEFAULT 0,
         missing           INT NOT NULL DEFAULT 0,
         missing_applied   INT NOT NULL DEFAULT 0,
         manual_overwritten INT NOT NULL DEFAULT 0,
         error_code        TEXT,
         error_params      JSONB,
         watermark_before  JSONB,
         watermark_after   JSONB,
         rejected_sample   JSONB,
         overwritten_sample JSONB,
         brake_overridden  BOOLEAN NOT NULL DEFAULT FALSE,
         triggered_by      TEXT,
         worker_id         TEXT,
         CHECK (trigger_kind IN ('schedule', 'manual')),
         CHECK (status IN ('running', 'success', 'failed', 'braked'))
     )"""),
    ("create_inventory_sync_runs_profile_idx",
     "CREATE INDEX IF NOT EXISTS inventory_sync_runs_profile_idx "
     "ON inventory_sync_runs (profile_id, started_at DESC)"),
    ("create_inventory_sync_runs_tenant_idx",
     "CREATE INDEX IF NOT EXISTS inventory_sync_runs_tenant_idx "
     "ON inventory_sync_runs (tenant_id)"),

    ("create_inventory_sync_keys",
     """CREATE TABLE IF NOT EXISTS inventory_sync_keys (
         tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         profile_id    TEXT NOT NULL REFERENCES inventory_sync_profiles(id) ON DELETE CASCADE,
         sku           TEXT NOT NULL,
         warehouse     TEXT NOT NULL,
         synced_values JSONB NOT NULL DEFAULT '{}'::jsonb,
         last_seen_run TEXT,
         last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         inactive      BOOLEAN NOT NULL DEFAULT FALSE,
         PRIMARY KEY (profile_id, sku, warehouse)
     )"""),
    ("create_inventory_sync_keys_tenant_idx",
     "CREATE INDEX IF NOT EXISTS inventory_sync_keys_tenant_idx "
     "ON inventory_sync_keys (tenant_id, sku, warehouse)"),

    ("create_inventory_sync_staging",
     """CREATE TABLE IF NOT EXISTS inventory_sync_staging (
         run_id     TEXT NOT NULL REFERENCES inventory_sync_runs(id) ON DELETE CASCADE,
         tenant_id  TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         sku        TEXT NOT NULL,
         warehouse  TEXT NOT NULL,
         data       JSONB NOT NULL,
         duplicate  BOOLEAN NOT NULL DEFAULT FALSE,
         applied    BOOLEAN NOT NULL DEFAULT FALSE,
         PRIMARY KEY (run_id, sku, warehouse)
     )"""),
]
