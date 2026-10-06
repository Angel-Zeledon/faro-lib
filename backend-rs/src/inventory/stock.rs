//! `inventory_stock` access: `service.get_stock`, `list_stock*`, `upsert_stock`
//! (THE chokepoint every stock write funnels through: ceilings, canonical
//! warehouse, provenance stamps, snapshot) and `delete_stock`.
//!
//! Python's `upsert_stock` without a shared connection runs each statement on
//! its own autocommitting connection; here the caller always passes one
//! connection (a transaction), which is only ever stronger: a half-written
//! stock row cannot be observed.

use serde_json::{json, Map, Value};
use sqlx::{PgConnection, Row};

use crate::error::ApiError;
use crate::inventory::warehouses;
use crate::limits::enforce_limit;
use crate::routes::sessions::row_json;

/// What a column holds, to type a NULL bind correctly.
#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Float,
    Int,
    Text,
}

/// Fields `upsert_stock` accepts.
const ALLOWED: [(&str, Kind); 15] = [
    ("display_name", Kind::Text),
    ("current_stock", Kind::Float),
    ("min_stock", Kind::Float),
    ("lead_time_days", Kind::Int),
    ("unit_cost", Kind::Float),
    ("moq", Kind::Float),
    ("supplier", Kind::Text),
    ("notes", Kind::Text),
    ("service_level", Kind::Float),
    ("sale_price", Kind::Float),
    ("category", Kind::Text),
    ("family", Kind::Text),
    ("brand", Kind::Text),
    ("unit_of_measure", Kind::Text),
    ("barcode", Kind::Text),
];

/// `PROVENANCE_FIELDS` and their `<field>_set_by` columns.
const PROVENANCE: [(&str, &str); 4] = [
    ("lead_time_days", "lead_time_set_by"),
    ("service_level", "service_level_set_by"),
    ("unit_cost", "unit_cost_set_by"),
    ("moq", "moq_set_by"),
];

/// `_DATASET_STOCK_MIN`: fields below the floor are dropped, not rejected.
const FLOORS: [(&str, f64); 6] = [
    ("current_stock", 0.0), ("min_stock", 0.0), ("unit_cost", 0.0), ("sale_price", 0.0),
    ("moq", 1.0), ("lead_time_days", 1.0),
];

#[derive(Debug, Clone, PartialEq)]
pub enum Val {
    Null,
    F(f64),
    I(i32),
    S(String),
}

/// The `data` dict of `upsert_stock`, in insertion order.
#[derive(Debug, Clone, Default)]
pub struct StockData {
    pub fields: Vec<(&'static str, Val)>,
    /// `data["warehouse"]`; `None` is "not in the dict" (then `principal`).
    pub warehouse: Option<String>,
}

impl StockData {
    pub fn set(&mut self, name: &'static str, v: Val) {
        match self.fields.iter_mut().find(|(k, _)| *k == name) {
            Some(slot) => slot.1 = v,
            None => self.fields.push((name, v)),
        }
    }
}

/// `get_stock(tenant, sku, warehouse=None)`.
pub async fn get_stock(
    conn: &mut PgConnection,
    tenant_id: &str,
    sku: &str,
    warehouse: Option<&str>,
) -> Result<Option<Map<String, Value>>, sqlx::Error> {
    let row = match warehouse {
        Some(w) => {
            sqlx::query("SELECT * FROM inventory_stock WHERE tenant_id = $1 AND sku = $2 AND warehouse = $3")
                .bind(tenant_id)
                .bind(sku)
                .bind(w)
                .fetch_optional(&mut *conn)
                .await?
        }
        None => {
            sqlx::query("SELECT * FROM inventory_stock WHERE tenant_id = $1 AND sku = $2")
                .bind(tenant_id)
                .bind(sku)
                .fetch_optional(&mut *conn)
                .await?
        }
    };
    row.map(|r| row_json(&r)).transpose()
}

/// `list_stock`.
pub async fn list_stock(conn: &mut PgConnection, tenant_id: &str) -> Result<Vec<Map<String, Value>>, sqlx::Error> {
    let rows = sqlx::query("SELECT * FROM inventory_stock WHERE tenant_id = $1 ORDER BY sku")
        .bind(tenant_id)
        .fetch_all(&mut *conn)
        .await?;
    rows.iter().map(row_json).collect()
}

/// `list_stock_warehouses`.
pub async fn list_stock_warehouses(conn: &mut PgConnection, tenant_id: &str, sku: &str) -> Result<Vec<String>, sqlx::Error> {
    let rows = sqlx::query("SELECT warehouse FROM inventory_stock WHERE tenant_id = $1 AND sku = $2 ORDER BY warehouse")
        .bind(tenant_id)
        .bind(sku)
        .fetch_all(&mut *conn)
        .await?;
    rows.iter().map(|r| r.try_get::<String, _>(0)).collect()
}

/// `count_stock`.
pub async fn count_stock(conn: &mut PgConnection, tenant_id: &str) -> Result<i64, sqlx::Error> {
    let (n,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM inventory_stock WHERE tenant_id = $1")
        .bind(tenant_id)
        .fetch_one(&mut *conn)
        .await?;
    Ok(n)
}

/// `delete_stock`: every warehouse's row of the SKU.
pub async fn delete_stock(conn: &mut PgConnection, tenant_id: &str, sku: &str) -> Result<(), sqlx::Error> {
    sqlx::query("DELETE FROM inventory_stock WHERE tenant_id = $1 AND sku = $2")
        .bind(tenant_id)
        .bind(sku)
        .execute(&mut *conn)
        .await?;
    Ok(())
}

/// `_record_snapshot`.
pub async fn record_snapshot(
    conn: &mut PgConnection,
    tenant_id: &str,
    sku: &str,
    level: f64,
    warehouse: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query("INSERT INTO inventory_snapshots (tenant_id, sku, current_stock, warehouse) VALUES ($1, $2, $3, $4)")
        .bind(tenant_id)
        .bind(sku)
        .bind(level)
        .bind(warehouse)
        .execute(&mut *conn)
        .await?;
    Ok(())
}

/// `upsert_stock(tenant, sku, data, conn, source)`. The caller owns the
/// transaction (and, for a ceiling to hold, the tenant lock).
pub async fn upsert_stock(
    pool: &sqlx::PgPool,
    conn: &mut PgConnection,
    testing_mode: bool,
    tenant_id: &str,
    sku: &str,
    data: &StockData,
    source: &str,
) -> Result<Option<Map<String, Value>>, ApiError> {
    if crate::pycompat::py_strip(sku).is_empty() {
        return Err(ApiError::app("sku_blank", "SKU cannot be blank", 422, json!({})));
    }
    // `safe`: the allowed fields, in the order `data` holds them.
    let mut safe: Vec<(&'static str, Val)> = data
        .fields
        .iter()
        .filter(|(k, _)| ALLOWED.iter().any(|(a, _)| a == k))
        .cloned()
        .collect();
    // Floors: out-of-range numerics are dropped, the rest of the call stands.
    safe.retain(|(k, v)| {
        let Some((_, floor)) = FLOORS.iter().find(|(f, _)| f == k) else { return true };
        let numeric = match v {
            Val::F(f) => Some(*f),
            Val::I(i) => Some(f64::from(*i)),
            _ => None,
        };
        match numeric {
            Some(n) if n < *floor => {
                tracing::warn!("upsert_stock: dropped out-of-range {k}={n} (floor={floor}) sku={sku} tenant={tenant_id}");
                false
            }
            _ => true,
        }
    });

    // Canonical warehouse FIRST, then the chokepoint ceilings.
    let warehouse = warehouses::resolve_canonical_name(
        conn,
        tenant_id,
        Some(data.warehouse.as_deref().unwrap_or("principal")),
    )
    .await?;
    let is_new_row = get_stock(conn, tenant_id, sku, Some(&warehouse)).await?.is_none();
    if is_new_row {
        let current = count_stock(conn, tenant_id).await?;
        enforce_limit(pool, conn, testing_mode, tenant_id, "max_skus", current, 1).await?;
    }
    if !warehouses::exists(conn, tenant_id, &warehouse).await? {
        let current = warehouses::count(conn, tenant_id).await?;
        enforce_limit(pool, conn, testing_mode, tenant_id, "max_locations", current, 1).await?;
    }

    // Provenance for exactly the tracked fields this call writes.
    const SOURCES: [&str; 5] = ["user", "file", "supplier_rule", "learned", "default"];
    if !SOURCES.contains(&source) {
        return Err(ApiError::internal());
    }
    let mut stamps: Vec<(&'static str, Val)> = Vec::new();
    for (field, col) in PROVENANCE {
        if safe.iter().any(|(k, _)| *k == field) {
            stamps.push((col, Val::S(source.to_string())));
        }
    }

    // INSERT ... ON CONFLICT (tenant_id, sku, warehouse) DO UPDATE.
    let warehouse_val = Val::S(warehouse.clone());
    let mut cols: Vec<&str> = vec!["warehouse"];
    let mut vals: Vec<(Kind, &Val)> = vec![(Kind::Text, &warehouse_val)];
    for (k, v) in safe.iter().chain(stamps.iter()) {
        cols.push(k);
        let kind = ALLOWED.iter().find(|(a, _)| a == k).map(|(_, kd)| *kd).unwrap_or(Kind::Text);
        vals.push((kind, v));
    }
    let placeholders: Vec<String> = (0..vals.len()).map(|i| format!("${}", i + 3)).collect();
    let updates: Vec<String> = cols
        .iter()
        .filter(|c| **c != "warehouse")
        .map(|c| format!("{c} = EXCLUDED.{c}"))
        .collect();
    let upd = if updates.is_empty() { String::new() } else { format!("{}, ", updates.join(", ")) };
    let sql = format!(
        "INSERT INTO inventory_stock (tenant_id, sku, {}, updated_at) VALUES ($1, $2, {}, NOW())
         ON CONFLICT (tenant_id, sku, warehouse) DO UPDATE SET {upd}updated_at = NOW()",
        cols.join(", "),
        placeholders.join(", ")
    );
    let mut q = sqlx::query(&sql).bind(tenant_id).bind(sku);
    for (kind, v) in vals {
        q = match (kind, v) {
            (Kind::Float, Val::F(f)) => q.bind(*f),
            (Kind::Float, Val::I(i)) => q.bind(f64::from(*i)),
            (Kind::Float, _) => q.bind(None::<f64>),
            (Kind::Int, Val::I(i)) => q.bind(*i),
            (Kind::Int, Val::F(f)) => q.bind(*f as i32),
            (Kind::Int, _) => q.bind(None::<i32>),
            (Kind::Text, Val::S(s)) => q.bind(s.clone()),
            (Kind::Text, _) => q.bind(None::<String>),
        };
    }
    q.execute(&mut *conn).await?;
    warehouses::ensure(conn, tenant_id, &warehouse).await?;
    let row = get_stock(conn, tenant_id, sku, Some(&warehouse)).await?;
    let level = safe.iter().find(|(k, _)| *k == "current_stock").map(|(_, v)| match v {
        Val::F(f) => *f,
        Val::I(i) => f64::from(*i),
        _ => 0.0,
    });
    if let (Some(level), Some(_)) = (level, row.as_ref()) {
        record_snapshot(conn, tenant_id, sku, level, &warehouse).await?;
    }
    Ok(row)
}
