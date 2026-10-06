"""Schema for the S&OP forecast consensus (see `docs/rust-migration.md`, "Forecast consensus").

The routes that write these tables live in Rust (`backend-rs/src/routes/consensus.rs`);
Python owns the schema and reads the one published row set (`consensus_versions`
with status 'approved') where it decides purchase demand
(`inventory/forecast_adjustment_service.py::active_by_sku`).

Every table is a ledger:

* `consensus_settings` - the tenant's rule (priority function or weighted average
  with caps). No row means "not configured": nothing can be proposed, and nothing
  is assumed.
* `consensus_members` - who speaks for which function.
* `consensus_submissions` - one function's adjustment of the statistical forecast
  for a SKU and a period, in basis points (1 bp = 0.01%) so the consensus is
  integer arithmetic and the Rust and Python implementations agree to the digit.
  Append-only: a correction is a new revision that supersedes the old row, which
  stays. A trigger refuses any other UPDATE.
* `consensus_versions` - one frozen consensus (the lines, and the rule that made
  them). The numbers never change; only the status columns move, and a partial
  unique index guarantees at most ONE published consensus per forecast.
* `consensus_version_events` - who moved a version to which status, and when.
* `consensus_evidence` - the statistical forecast next to what really sold, per
  SKU and period, written by the Python grader (it reads the dataset files) and
  read by the Rust accuracy route.

DELETE stays possible only because whole-tenant erasure must remove a tenant's
rows (`tenants/data_export.py`); nothing else in the product deletes them.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_consensus_settings",
     """CREATE TABLE IF NOT EXISTS consensus_settings (
         tenant_id        TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
         rule             TEXT NOT NULL CHECK (rule IN ('priority', 'weighted')),
         priority         JSONB NOT NULL,
         weight_sales     INT NOT NULL CHECK (weight_sales BETWEEN 0 AND 1000),
         weight_finance   INT NOT NULL CHECK (weight_finance BETWEEN 0 AND 1000),
         weight_operations INT NOT NULL CHECK (weight_operations BETWEEN 0 AND 1000),
         cap_down_bp      INT NOT NULL CHECK (cap_down_bp BETWEEN -10000 AND 0),
         cap_up_bp        INT NOT NULL CHECK (cap_up_bp BETWEEN 0 AND 100000),
         updated_by       TEXT NOT NULL,
         updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CHECK (rule = 'priority' OR weight_sales + weight_finance + weight_operations > 0)
     )"""),
    ("create_consensus_members",
     """CREATE TABLE IF NOT EXISTS consensus_members (
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         function     TEXT NOT NULL CHECK (function IN ('sales', 'finance', 'operations')),
         user_id      TEXT NOT NULL,
         assigned_by  TEXT NOT NULL,
         assigned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (tenant_id, function, user_id)
     )"""),
    ("create_consensus_submissions",
     """CREATE TABLE IF NOT EXISTS consensus_submissions (
         id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id      TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id     TEXT NOT NULL,
         sku            TEXT NOT NULL,
         function       TEXT NOT NULL CHECK (function IN ('sales', 'finance', 'operations')),
         start_date     DATE NOT NULL,
         end_date       DATE NOT NULL,
         pct_bp         INT NOT NULL CHECK (pct_bp BETWEEN -10000 AND 100000),
         reason_code    TEXT NOT NULL,
         reason_note    TEXT,
         revision       INT NOT NULL CHECK (revision >= 1),
         created_by     TEXT NOT NULL,
         created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         superseded_by  TEXT,
         superseded_at  TIMESTAMPTZ,
         CHECK (end_date >= start_date)
     )"""),
    ("create_consensus_submissions_idx",
     "CREATE INDEX IF NOT EXISTS consensus_submissions_session_idx "
     "ON consensus_submissions (tenant_id, session_id, sku, function)"),
    ("create_consensus_versions",
     """CREATE TABLE IF NOT EXISTS consensus_versions (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id        TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id       TEXT NOT NULL,
         name             TEXT NOT NULL,
         note             TEXT,
         rule             JSONB NOT NULL,
         lines            JSONB NOT NULL,
         line_count       INT NOT NULL CHECK (line_count > 0),
         sku_count        INT NOT NULL CHECK (sku_count > 0),
         status           TEXT NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed', 'approved', 'rejected', 'withdrawn', 'superseded')),
         created_by       TEXT NOT NULL,
         created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         decided_by       TEXT,
         decided_at       TIMESTAMPTZ,
         decision_comment TEXT,
         self_approved    BOOLEAN NOT NULL DEFAULT FALSE
     )"""),
    ("create_consensus_versions_idx",
     "CREATE INDEX IF NOT EXISTS consensus_versions_session_idx "
     "ON consensus_versions (tenant_id, session_id, created_at DESC)"),
    # The database, not the handler, guarantees "one published consensus per forecast".
    ("create_consensus_versions_one_published",
     "CREATE UNIQUE INDEX IF NOT EXISTS consensus_versions_one_published "
     "ON consensus_versions (tenant_id, session_id) WHERE status = 'approved'"),
    ("create_consensus_version_events",
     """CREATE TABLE IF NOT EXISTS consensus_version_events (
         id           BIGSERIAL PRIMARY KEY,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         version_id   TEXT NOT NULL REFERENCES consensus_versions(id) ON DELETE CASCADE,
         from_status  TEXT,
         to_status    TEXT NOT NULL,
         actor_id     TEXT NOT NULL,
         comment      TEXT,
         details      JSONB NOT NULL DEFAULT '{}'::jsonb,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_consensus_version_events_idx",
     "CREATE INDEX IF NOT EXISTS consensus_version_events_version_idx "
     "ON consensus_version_events (tenant_id, version_id, id)"),
    ("create_consensus_evidence",
     """CREATE TABLE IF NOT EXISTS consensus_evidence (
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         session_id   TEXT NOT NULL,
         sku          TEXT NOT NULL,
         period       DATE NOT NULL,
         base         DOUBLE PRECISION NOT NULL,
         actual       DOUBLE PRECISION NOT NULL,
         dataset_id   TEXT,
         refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (tenant_id, session_id, sku, period)
     )"""),
    # Triggers. `%%` because the migration runner formats statements with
    # psycopg2: a bare `%` is a parameter marker there (see demand_plan_migrations).
    ("create_consensus_submission_guard_fn",
     """CREATE OR REPLACE FUNCTION consensus_submission_guard() RETURNS trigger AS $$
        BEGIN
          IF OLD.superseded_by IS NOT NULL
             OR NEW.superseded_by IS NULL
             OR (to_jsonb(NEW) - 'superseded_by' - 'superseded_at')
                IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_by' - 'superseded_at') THEN
            RAISE EXCEPTION 'consensus submissions are append-only; only a supersede stamp may change (%%)', TG_TABLE_NAME
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""),
    ("create_consensus_submissions_guard",
     """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'consensus_submissions_guard') THEN
            CREATE TRIGGER consensus_submissions_guard
              BEFORE UPDATE ON consensus_submissions
              FOR EACH ROW EXECUTE FUNCTION consensus_submission_guard();
          END IF;
        END $$"""),
    ("create_consensus_version_guard_fn",
     """CREATE OR REPLACE FUNCTION consensus_version_guard() RETURNS trigger AS $$
        BEGIN
          IF (to_jsonb(NEW) - 'status' - 'decided_by' - 'decided_at'
                            - 'decision_comment' - 'self_approved')
             IS DISTINCT FROM
             (to_jsonb(OLD) - 'status' - 'decided_by' - 'decided_at'
                            - 'decision_comment' - 'self_approved') THEN
            RAISE EXCEPTION 'a consensus version is frozen; only its status may change (%%)', TG_TABLE_NAME
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql"""),
    ("create_consensus_versions_guard",
     """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'consensus_versions_guard') THEN
            CREATE TRIGGER consensus_versions_guard
              BEFORE UPDATE ON consensus_versions
              FOR EACH ROW EXECUTE FUNCTION consensus_version_guard();
          END IF;
        END $$"""),
    ("create_consensus_events_no_update",
     """CREATE OR REPLACE FUNCTION consensus_events_refuse_update() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'consensus version events are immutable (%%)', TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql"""),
    ("create_consensus_events_guard",
     """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'consensus_events_guard') THEN
            CREATE TRIGGER consensus_events_guard
              BEFORE UPDATE ON consensus_version_events
              FOR EACH ROW EXECUTE FUNCTION consensus_events_refuse_update();
          END IF;
        END $$"""),
]
