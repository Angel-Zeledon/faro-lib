//! Organization hierarchy: a holding with subsidiary tenants and consolidated
//! READ-ONLY views across them. Rust-only (no Python failover); the schema and
//! the Python-side rules live in `backend/organizations/`.
//!
//! * [`scope`]: who may read which tenants (the isolation core).
//! * [`views`]: the four consolidated reads' queries and roll-up rules.
//! * `routes/org.rs` and `routes/org_consolidated.rs`: the HTTP surface.

pub mod scope;
pub mod views;
