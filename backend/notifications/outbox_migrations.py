"""Schema for the message outbox (see `backend/notifications/outbox.py`).

`outbound_messages` is the one door through which a service that must not talk
to Resend / SMTP / Twilio itself (the Rust API, `docs/rust-migration.md`, wave 2)
asks for an email or a WhatsApp message. It writes a row naming WHAT to send
(an English `kind`) and the data it needs (`params`); the Python worker loop
`outbox-drain` renders it with the existing senders and records the outcome. So
there is one sender implementation, one place that refuses the made-up
`@stockai.demo` trial addresses, and one place where "not configured" is
decided.

Additive: nothing reads this table until a writer exists, and an installation
with no rows behaves exactly as before.

`params` can hold a short-lived secret (a one-time code, a verification link).
It is scrubbed to `{}` the moment a row reaches a final state, and a row nobody
delivered by `expires_at` is abandoned and scrubbed too, so a secret never
outlives the minutes it is valid for.
"""

MIGRATIONS: list[tuple[str, str]] = [
    ("create_outbound_messages",
     """CREATE TABLE IF NOT EXISTS outbound_messages (
         id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
         tenant_id        TEXT NOT NULL,
         channel          TEXT NOT NULL CHECK (channel IN ('email', 'whatsapp')),
         kind             TEXT NOT NULL,
         recipient        TEXT NOT NULL,
         params           JSONB NOT NULL DEFAULT '{}'::jsonb,
         status           TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'sent', 'failed', 'abandoned')),
         attempts         INT NOT NULL DEFAULT 0,
         next_attempt_at  TIMESTAMPTZ,
         expires_at       TIMESTAMPTZ NOT NULL,
         last_error       TEXT,
         dedupe_key       TEXT,
         created_by       TEXT,
         created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         last_attempt_at  TIMESTAMPTZ,
         sent_at          TIMESTAMPTZ
     )"""),
    ("create_outbound_messages_due_idx",
     "CREATE INDEX IF NOT EXISTS outbound_messages_due_idx "
     "ON outbound_messages (next_attempt_at) WHERE status = 'pending'"),
    ("create_outbound_messages_tenant_idx",
     "CREATE INDEX IF NOT EXISTS outbound_messages_tenant_idx "
     "ON outbound_messages (tenant_id, created_at DESC)"),
    # One message per (tenant, dedupe key): a writer that retries its own
    # request (or two services racing on the same business event) queues it once.
    ("create_outbound_messages_dedupe_idx",
     "CREATE UNIQUE INDEX IF NOT EXISTS outbound_messages_dedupe_idx "
     "ON outbound_messages (tenant_id, dedupe_key) WHERE dedupe_key IS NOT NULL"),
]
