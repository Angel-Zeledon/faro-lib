//! Recurring delivery schedules for corporate contracts ("N units every
//! month / fortnight / week from A to B"). Routes: `routes/recurring_deliveries.rs`.
//! Schema: `backend/inventory/recurring_delivery_migrations.py` (Python owns it).

pub mod dates;
pub mod materialise;
pub mod materialiser;
