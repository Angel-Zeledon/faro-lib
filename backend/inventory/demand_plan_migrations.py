"""Schema for demand plan versions (see `demand_plan_service.py`).

Two tables, both append-only:

* `demand_plan_versions` - one frozen plan. Every column is written once, at
  creation, and a trigger refuses any later UPDATE: what a manager approved is
  exactly what is measured later, never a number that drifted after the fact.
* `demand_plan_version_events` - its history: one row per status change
  (draft -> submitted -> approved / rejected, approved -> superseded) or comment,
  naming who and when. The current status of a version IS its latest status
  event; there is no mutable status column to fall out of step with it.

DELETE stays possible only because whole-tenant erasure must be able to remove a
tenant's rows (`tenants/data_export.py`); nothing else in the product deletes them.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_demand_plan_versions",
     """CREATE TABLE IF NOT EXISTS demand_plan_versions (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id        TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         name             TEXT NOT NULL,
         session_id       TEXT NOT NULL,
         granularity      TEXT,
         anchor_date      DATE NOT NULL,
         first_period     DATE NOT NULL,
         last_period      DATE NOT NULL,
         horizon_periods  INT NOT NULL CHECK (horizon_periods > 0),
         sku_count        INT NOT NULL CHECK (sku_count >= 0),
         snapshot_bytes   INT NOT NULL CHECK (snapshot_bytes >= 0),
         totals           JSONB NOT NULL,
         snapshot         JSONB NOT NULL,
         note             TEXT,
         created_by       TEXT NOT NULL,
         created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("create_demand_plan_versions_idx",
     "CREATE INDEX IF NOT EXISTS demand_plan_versions_tenant_idx "
     "ON demand_plan_versions (tenant_id, created_at DESC)"),
    ("create_demand_plan_version_events",
     """CREATE TABLE IF NOT EXISTS demand_plan_version_events (
         id           BIGSERIAL PRIMARY KEY,
         tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         version_id   TEXT NOT NULL REFERENCES demand_plan_versions(id) ON DELETE CASCADE,
         kind         TEXT NOT NULL CHECK (kind IN ('status', 'comment')),
         from_status  TEXT,
         to_status    TEXT CHECK (to_status IS NULL OR to_status IN
                        ('draft', 'submitted', 'approved', 'rejected', 'superseded')),
         actor_id     TEXT NOT NULL,
         comment      TEXT,
         details      JSONB NOT NULL DEFAULT '{}'::jsonb,
         created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CHECK ((kind = 'status') = (to_status IS NOT NULL))
     )"""),
    ("create_demand_plan_version_events_idx",
     "CREATE INDEX IF NOT EXISTS demand_plan_version_events_version_idx "
     "ON demand_plan_version_events (tenant_id, version_id, id)"),
    # Immutability lives in the database, so a script or a future endpoint that
    # "just fixes one number" fails loudly instead of rewriting an approved plan.
    ("create_demand_plan_immutable_fn",
     # `%%` because the migration runner formats the statement with psycopg2: a bare
     # `%` is a parameter marker there and made this function silently NOT exist,
     # which left both tables mutable (caught by test_demand_plans on a real DB).
     """CREATE OR REPLACE FUNCTION demand_plan_refuse_update() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'demand plan versions and their events are immutable (%%)', TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql"""),
    ("create_demand_plan_versions_no_update",
     """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'demand_plan_versions_no_update') THEN
            CREATE TRIGGER demand_plan_versions_no_update
              BEFORE UPDATE ON demand_plan_versions
              FOR EACH ROW EXECUTE FUNCTION demand_plan_refuse_update();
          END IF;
        END $$"""),
    ("create_demand_plan_events_no_update",
     """DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'demand_plan_events_no_update') THEN
            CREATE TRIGGER demand_plan_events_no_update
              BEFORE UPDATE ON demand_plan_version_events
              FOR EACH ROW EXECUTE FUNCTION demand_plan_refuse_update();
          END IF;
        END $$"""),
]
