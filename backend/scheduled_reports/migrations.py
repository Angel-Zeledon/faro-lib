"""Schema for scheduled management reports (see `backend/scheduled_reports/`).

Additive: nothing reads these tables until somebody defines a schedule, and an
installation with no rows behaves exactly as before.

* `report_schedules`  one recurring report (weekly or monthly, an hour in the
  tenant's time zone, a fixed choice of sections).
* `report_schedule_recipients`  who gets it: people of the tenant (by user id,
  re-resolved at every send) and external addresses.
* `report_external_allowlist`  external addresses a tenant ADMIN explicitly
  allowed. A schedule may only name an external address that is on this list,
  and the send re-checks it.
* `report_schedule_runs`  one row per (schedule, period). The UNIQUE key is the guard
  that makes a restart, a second worker or a retried claim unable to send the
  same report twice: whoever inserts the row owns the period.

Time zone: `next_run_at` is a UTC instant computed from the tenant's zone, and
`anchored_tz` records WHICH zone it was computed in, so the worker can see that
the tenant moved zones since and re-anchor instead of firing at the old wall
clock (the Rust and Python timezone writers do not know about this table).
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_report_schedules",
     """CREATE TABLE IF NOT EXISTS report_schedules (
         id                    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         name                  TEXT NOT NULL,
         sections              JSONB NOT NULL,
         frequency             TEXT NOT NULL CHECK (frequency IN ('weekly', 'monthly')),
         weekday               INT CHECK (weekday BETWEEN 1 AND 7),
         day_of_month          INT CHECK (day_of_month BETWEEN 1 AND 28),
         hour                  INT NOT NULL CHECK (hour BETWEEN 0 AND 23),
         cron_expr             TEXT NOT NULL,
         anchored_tz           TEXT NOT NULL,
         enabled               BOOLEAN NOT NULL DEFAULT TRUE,
         paused_reason         TEXT,
         paused_at             TIMESTAMPTZ,
         consecutive_failures  INT NOT NULL DEFAULT 0,
         next_run_at           TIMESTAMPTZ NOT NULL,
         last_run_at           TIMESTAMPTZ,
         last_status           TEXT,
         last_error            TEXT,
         created_by            TEXT NOT NULL,
         created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CHECK ((frequency = 'weekly') = (weekday IS NOT NULL)),
         CHECK ((frequency = 'monthly') = (day_of_month IS NOT NULL))
     )"""),
    ("create_report_schedules_due_idx",
     "CREATE INDEX IF NOT EXISTS report_schedules_due_idx "
     "ON report_schedules (next_run_at) WHERE enabled"),
    ("create_report_schedules_tenant_idx",
     "CREATE INDEX IF NOT EXISTS report_schedules_tenant_idx "
     "ON report_schedules (tenant_id, created_at DESC)"),
    ("create_report_schedule_recipients",
     """CREATE TABLE IF NOT EXISTS report_schedule_recipients (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         schedule_id      TEXT NOT NULL REFERENCES report_schedules(id) ON DELETE CASCADE,
         tenant_id        TEXT NOT NULL,
         kind             TEXT NOT NULL CHECK (kind IN ('user', 'external')),
         user_id          TEXT,
         email            TEXT,
         unsubscribed_at  TIMESTAMPTZ,
         created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CHECK ((kind = 'user') = (user_id IS NOT NULL)),
         CHECK ((kind = 'external') = (email IS NOT NULL))
     )"""),
    ("create_report_recipients_user_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS report_recipients_user_uniq "
     "ON report_schedule_recipients (schedule_id, user_id) WHERE user_id IS NOT NULL"),
    ("create_report_recipients_email_uniq",
     "CREATE UNIQUE INDEX IF NOT EXISTS report_recipients_email_uniq "
     "ON report_schedule_recipients (schedule_id, email) WHERE email IS NOT NULL"),
    ("create_report_external_allowlist",
     """CREATE TABLE IF NOT EXISTS report_external_allowlist (
         tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         email       TEXT NOT NULL,
         added_by    TEXT NOT NULL,
         created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (tenant_id, email)
     )"""),
    ("create_report_schedule_runs",
     """CREATE TABLE IF NOT EXISTS report_schedule_runs (
         id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
         schedule_id       TEXT NOT NULL,
         period_key        TEXT NOT NULL,
         due_at            TIMESTAMPTZ NOT NULL,
         status            TEXT NOT NULL DEFAULT 'building'
                           CHECK (status IN ('building', 'queued', 'skipped', 'failed')),
         attempts          INT NOT NULL DEFAULT 1,
         snapshot          JSONB,
         recipients_queued INT NOT NULL DEFAULT 0,
         recipients_skipped JSONB NOT NULL DEFAULT '[]'::jsonb,
         error             TEXT,
         started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         finished_at       TIMESTAMPTZ,
         UNIQUE (schedule_id, period_key)
     )"""),
    ("create_report_schedule_runs_tenant_idx",
     "CREATE INDEX IF NOT EXISTS report_schedule_runs_tenant_idx "
     "ON report_schedule_runs (tenant_id, schedule_id, started_at DESC)"),
]
