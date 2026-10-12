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

# ── v2: freshness modes, stock models, health, resume, push, idempotency ─────
#
# Additive only (ALTER ... ADD COLUMN IF NOT EXISTS, CHECKs dropped and
# re-added under their default names), so an installation that ran v1 keeps
# every profile and run exactly as it was: a v1 profile reads as a
# `snapshot` model on a `scheduled` freshness, health `new`.

def _recheck(table: str, column: str, values: tuple[str, ...]) -> list[tuple[str, str]]:
    name = f"{table}_{column}_check"
    allowed = ", ".join(f"'{v}'" for v in values)
    return [
        (f"drop_{name}_v2", f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"),
        (f"add_{name}_v2", f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({column} IN ({allowed}))"),
    ]


_PROFILE_COLUMNS = (
    ("slug", "TEXT"),
    ("priority", "INT NOT NULL DEFAULT 100"),
    ("health", "TEXT NOT NULL DEFAULT 'new'"),
    ("health_reason", "TEXT"),
    ("health_params", "JSONB"),
    ("health_changed_at", "TIMESTAMPTZ"),
    ("brake_max_jump_pct", "DOUBLE PRECISION NOT NULL DEFAULT 20"),
    ("stock_model", "TEXT NOT NULL DEFAULT 'snapshot'"),
    ("model_config", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
    ("freshness", "TEXT NOT NULL DEFAULT 'scheduled'"),
    ("poll_seconds", "INT NOT NULL DEFAULT 60"),
    ("reconcile_minutes", "INT NOT NULL DEFAULT 360"),
    ("next_reconcile_at", "TIMESTAMPTZ"),
    ("strategy", "TEXT NOT NULL DEFAULT 'auto'"),
    ("cursor_value", "JSONB"),
    ("data_as_of", "TIMESTAMPTZ"),
    ("last_refresh_at", "TIMESTAMPTZ"),
    ("source_fingerprint", "TEXT"),
)
_RUN_COLUMNS = (
    ("stage_complete", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("brake_passed", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("config_hash", "TEXT"),
    ("refused_ceiling", "INT NOT NULL DEFAULT 0"),
    ("owned_elsewhere", "INT NOT NULL DEFAULT 0"),
    ("drift_count", "INT NOT NULL DEFAULT 0"),
    ("drift_sample", "JSONB"),
    ("warnings", "JSONB"),
    ("run_kind", "TEXT NOT NULL DEFAULT 'full'"),
    ("scope", "JSONB"),
    ("health_after", "TEXT"),
    ("checkpoint", "JSONB"),
)

MIGRATIONS += [
    *[(f"add_inventory_sync_profiles_{c}", f"ALTER TABLE inventory_sync_profiles ADD COLUMN IF NOT EXISTS {c} {t}")
      for c, t in _PROFILE_COLUMNS],
    *[(f"add_inventory_sync_runs_{c}", f"ALTER TABLE inventory_sync_runs ADD COLUMN IF NOT EXISTS {c} {t}")
      for c, t in _RUN_COLUMNS],
    ("inventory_sync_profiles_source_optional",
     "ALTER TABLE inventory_sync_profiles ALTER COLUMN data_source_id DROP NOT NULL"),
    *_recheck("inventory_sync_profiles", "source_kind", ("table", "query", "collection", "pipeline", "push")),
    *_recheck("inventory_sync_profiles", "stock_model", ("snapshot", "aggregate", "ledger")),
    *_recheck("inventory_sync_profiles", "freshness", ("scheduled", "continuous", "on_demand")),
    *_recheck("inventory_sync_profiles", "health", ("new", "healthy", "degraded", "paused", "blocked")),
    *_recheck("inventory_sync_profiles", "negative_policy", ("reject", "zero")),
    *_recheck("inventory_sync_runs", "trigger_kind",
              ("schedule", "manual", "refresh", "continuous", "reconcile", "push", "import")),
    *_recheck("inventory_sync_runs", "status", ("running", "receiving", "success", "failed", "braked")),
    ("create_inventory_sync_profiles_slug_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS inventory_sync_profiles_slug_uniq "
     "ON inventory_sync_profiles (tenant_id, slug) WHERE slug IS NOT NULL"),
    ("create_inventory_sync_staging_apply_idx",
     "CREATE INDEX IF NOT EXISTS inventory_sync_staging_apply_idx "
     "ON inventory_sync_staging (run_id, applied, sku, warehouse)"),
    ("create_inventory_sync_keys_owner_idx",
     "CREATE INDEX IF NOT EXISTS inventory_sync_keys_owner_idx "
     "ON inventory_sync_keys (tenant_id, sku, warehouse, profile_id)"),
    # The running balance of a ledger profile: what StockAI has computed by
    # applying movements, checked against the source's SUM on every reconcile.
    ("create_inventory_sync_ledger_balances",
     """CREATE TABLE IF NOT EXISTS inventory_sync_ledger_balances (
         tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         profile_id  TEXT NOT NULL REFERENCES inventory_sync_profiles(id) ON DELETE CASCADE,
         sku         TEXT NOT NULL,
         warehouse   TEXT NOT NULL,
         balance     DOUBLE PRECISION NOT NULL DEFAULT 0,
         updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (profile_id, sku, warehouse)
     )"""),
    # Movement ids already applied inside the overlap window: an incremental
    # read that sees a movement twice (overlap, replay, out-of-order commit)
    # applies it once. Pruned past the window.
    ("create_inventory_sync_applied_movements",
     """CREATE TABLE IF NOT EXISTS inventory_sync_applied_movements (
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         profile_id   TEXT NOT NULL REFERENCES inventory_sync_profiles(id) ON DELETE CASCADE,
         movement_id  TEXT NOT NULL,
         applied_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (profile_id, movement_id)
     )"""),
    # Push ingest: one row per batch the customer POSTed, keyed by THEIR batch
    # id, so a retried POST is answered from here instead of applied twice.
    ("create_inventory_sync_ingest_batches",
     """CREATE TABLE IF NOT EXISTS inventory_sync_ingest_batches (
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         profile_id   TEXT NOT NULL REFERENCES inventory_sync_profiles(id) ON DELETE CASCADE,
         batch_id     TEXT NOT NULL,
         run_id       TEXT,
         rows         INT NOT NULL DEFAULT 0,
         fingerprint  TEXT,
         received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (profile_id, batch_id)
     )"""),
    # Pending ledger state: a run's computed balances and consumed movement ids
    # become real only when the run succeeds (`ledger.commit`).
    ("add_inventory_sync_ledger_balances_pending_balance",
     "ALTER TABLE inventory_sync_ledger_balances ADD COLUMN IF NOT EXISTS pending_balance DOUBLE PRECISION"),
    ("add_inventory_sync_ledger_balances_pending_run",
     "ALTER TABLE inventory_sync_ledger_balances ADD COLUMN IF NOT EXISTS pending_run TEXT"),
    ("add_inventory_sync_applied_movements_pending_run",
     "ALTER TABLE inventory_sync_applied_movements ADD COLUMN IF NOT EXISTS pending_run TEXT"),
    # The high-water cursor of the last successful full / reconcile ledger
    # run: every movement at or below it is inside the running balance, so an
    # incremental read never goes below it, whatever the cursor says.
    ("add_inventory_sync_profiles_ledger_floor",
     "ALTER TABLE inventory_sync_profiles ADD COLUMN IF NOT EXISTS ledger_floor JSONB"),
    # Idempotency-Key replay store for the mutating inventory-sync routes.
    ("create_inventory_sync_idempotency",
     """CREATE TABLE IF NOT EXISTS inventory_sync_idempotency (
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         idem_key     TEXT NOT NULL,
         route        TEXT NOT NULL,
         fingerprint  TEXT NOT NULL,
         status_code  INT NOT NULL,
         response     JSONB NOT NULL,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (tenant_id, idem_key)
     )"""),
]
