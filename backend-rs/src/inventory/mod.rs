//! The inventory hub (wave 3). `calc` is the pure numeric core, proven equal
//! to Python bit for bit by `tests/fixtures/inventory_calc.json`; the other
//! modules are the DB-backed pieces the migrated routes share.

pub mod calc;
