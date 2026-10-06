//! The inventory hub (wave 3). `calc` is the pure numeric core, proven equal
//! to Python bit for bit by `tests/fixtures/inventory_calc.json`; the other
//! modules are the DB-backed pieces the migrated routes share.

#![allow(dead_code)]

pub mod calc;
pub mod events;
pub mod pydt;
pub mod scope;
pub mod stock;
pub mod validate;
pub mod warehouses;
