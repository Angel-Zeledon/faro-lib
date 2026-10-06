"""Schema for outbound webhook delivery (see `backend/webhooks/service.py`).

`webhooks` already exists (migrations.py). It gains the columns that make a
hook manageable: who created it and the warehouse scope it inherited, the
failure streak that auto-disables it, and when its secret last changed.

`webhook_deliveries` IS the durable queue: an emitter inserts a row, a worker
loop claims due rows and posts them. Nothing is sent from the emitter's thread.

`webhook_transition_state` remembers which keys were ALREADY in a state at the
last daily pass (a SKU/warehouse in PEDIR_YA, a commitment at risk), so an
event is emitted when a key ENTERS the state and never again while it stays.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("add_webhooks_management_columns",
     """ALTER TABLE webhooks
          ADD COLUMN IF NOT EXISTS created_by        TEXT,
          ADD COLUMN IF NOT EXISTS warehouse_scope   JSONB,
          ADD COLUMN IF NOT EXISTS disabled_at       TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS disabled_reason   TEXT,
          ADD COLUMN IF NOT EXISTS failure_days      INT NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS last_failure_on   DATE,
          ADD COLUMN IF NOT EXISTS secret_rotated_at TIMESTAMPTZ"""),
    ("create_webhook_deliveries",
     """CREATE TABLE IF NOT EXISTS webhook_deliveries (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id        TEXT NOT NULL,
         webhook_id       TEXT NOT NULL,
         event_id         TEXT NOT NULL,
         event_type       TEXT NOT NULL,
         is_test          BOOLEAN NOT NULL DEFAULT FALSE,
         payload          TEXT NOT NULL,
         status           TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'delivered', 'failed', 'abandoned')),
         attempts         INT NOT NULL DEFAULT 0,
         last_status_code INT,
         last_error       TEXT,
         next_attempt_at  TIMESTAMPTZ,
         created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         last_attempt_at  TIMESTAMPTZ,
         delivered_at     TIMESTAMPTZ
     )"""),
    ("create_webhook_deliveries_due_idx",
     "CREATE INDEX IF NOT EXISTS webhook_deliveries_due_idx "
     "ON webhook_deliveries (next_attempt_at) WHERE status = 'pending'"),
    ("create_webhook_deliveries_hook_idx",
     "CREATE INDEX IF NOT EXISTS webhook_deliveries_hook_idx "
     "ON webhook_deliveries (webhook_id, created_at DESC)"),
    ("create_webhook_transition_state",
     """CREATE TABLE IF NOT EXISTS webhook_transition_state (
         tenant_id  TEXT NOT NULL,
         kind       TEXT NOT NULL,
         state_key  TEXT NOT NULL,
         since      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         PRIMARY KEY (tenant_id, kind, state_key)
     )"""),
]
