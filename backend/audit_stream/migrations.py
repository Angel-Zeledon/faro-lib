"""Schema for the continuous audit export (see `backend/audit_stream/service.py`).

Three additive pieces, none of which changes what a writer of `activity_logs`
has to do:

* `activity_logs.stream_xid` / `stream_seq`: stamped by COLUMN DEFAULTS, so every
  writer (the Python `log_action`, the Rust `log_action`, a future one) gets them
  without knowing they exist. `stream_xid` is the id of the writing transaction,
  `stream_seq` a global sequence. Together they are the stream's position.

  Why not `created_at`: it is the time of the INSERT, not of the COMMIT. A row
  stamped 10:00:00.001 by a transaction that commits at 10:00:05 shows up AFTER
  a reader has already advanced past 10:00:03, and a cursor on `created_at`
  would never deliver it. Reading only rows whose transaction id is below the
  snapshot's `xmin` (every such transaction has finished, committed or rolled
  back) makes the position safe: nothing can later appear behind the cursor.

  Rows written before this migration have NULL and are not streamed; their
  history is the CSV export (`GET /audit/export`). Adding a column with a
  volatile default would rewrite the whole table, so the defaults are set AFTER
  the columns exist (they only apply to new rows).

* `audit_streams`: one destination per tenant, with its cursor, lease, retry and
  failure state.
* `audit_stream_deliveries`: the delivery log, one row per attempt.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("audit_stream_add_activity_position_columns",
     """ALTER TABLE activity_logs
          ADD COLUMN IF NOT EXISTS stream_xid BIGINT,
          ADD COLUMN IF NOT EXISTS stream_seq BIGINT"""),
    ("audit_stream_create_activity_sequence",
     "CREATE SEQUENCE IF NOT EXISTS activity_logs_stream_seq"),
    ("audit_stream_activity_position_defaults",
     """ALTER TABLE activity_logs
          ALTER COLUMN stream_xid SET DEFAULT (pg_current_xact_id()::text)::bigint,
          ALTER COLUMN stream_seq SET DEFAULT nextval('activity_logs_stream_seq')"""),
    ("audit_stream_activity_position_idx",
     "CREATE INDEX IF NOT EXISTS idx_activity_stream_position "
     "ON activity_logs (tenant_id, stream_xid, stream_seq) WHERE stream_xid IS NOT NULL"),
    ("audit_stream_create_destinations",
     """CREATE TABLE IF NOT EXISTS audit_streams (
         tenant_id            TEXT PRIMARY KEY,
         url                  TEXT NOT NULL,
         secret               TEXT NOT NULL,
         enabled              BOOLEAN NOT NULL DEFAULT TRUE,
         disabled_at          TIMESTAMPTZ,
         disabled_reason      TEXT,
         cursor_xid           BIGINT NOT NULL DEFAULT 0,
         cursor_seq           BIGINT NOT NULL DEFAULT 0,
         batch_size           INT NOT NULL DEFAULT 500 CHECK (batch_size BETWEEN 1 AND 1000),
         consecutive_failures INT NOT NULL DEFAULT 0,
         failure_days         INT NOT NULL DEFAULT 0,
         last_failure_on      DATE,
         last_error           TEXT,
         last_status_code     INT,
         last_attempt_at      TIMESTAMPTZ,
         last_success_at      TIMESTAMPTZ,
         delivered_records    BIGINT NOT NULL DEFAULT 0,
         next_attempt_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         lease_until          TIMESTAMPTZ,
         lease_token          TEXT,
         test_requested_at    TIMESTAMPTZ,
         secret_rotated_at    TIMESTAMPTZ,
         created_by           TEXT,
         created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("audit_stream_destinations_due_idx",
     "CREATE INDEX IF NOT EXISTS audit_streams_due_idx "
     "ON audit_streams (next_attempt_at)"),
    ("audit_stream_create_deliveries",
     """CREATE TABLE IF NOT EXISTS audit_stream_deliveries (
         id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id     TEXT NOT NULL,
         kind          TEXT NOT NULL CHECK (kind IN ('batch', 'test')),
         status        TEXT NOT NULL CHECK (status IN ('delivered', 'failed', 'superseded')),
         records       INT NOT NULL DEFAULT 0,
         bytes         INT NOT NULL DEFAULT 0,
         first_cursor  TEXT,
         last_cursor   TEXT,
         status_code   INT,
         error         TEXT,
         duration_ms   INT,
         created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
     )"""),
    ("audit_stream_deliveries_idx",
     "CREATE INDEX IF NOT EXISTS audit_stream_deliveries_tenant_idx "
     "ON audit_stream_deliveries (tenant_id, created_at DESC)"),
]
