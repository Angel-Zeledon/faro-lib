"""Scheduled management reports by email.

The routes (CRUD, preview, run history, unsubscribe) live in the Rust API
(`backend-rs/src/routes/scheduled_reports.rs`). Python owns the schema, the
worker pass that claims due schedules and builds the reports, and the
`scheduled_report` email kind the outbox drain sends. See
docs/rust-migration.md, "Scheduled reports".
"""
