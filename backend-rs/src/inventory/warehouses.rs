//! `backend/inventory/warehouse_service.py`, the slice the stock writes need.

use sqlx::PgConnection;

use crate::pycompat::py_strip;

/// `DEFAULT_WAREHOUSE`.
pub const DEFAULT_WAREHOUSE: &str = "principal";

/// Zero-width and bidirectional-control code points (`_INVISIBLE_CHARS`).
const INVISIBLE: [char; 13] = [
    '\u{200B}', '\u{200C}', '\u{200D}', '\u{FEFF}',
    '\u{202A}', '\u{202B}', '\u{202C}', '\u{202D}', '\u{202E}',
    '\u{2066}', '\u{2067}', '\u{2068}', '\u{2069}',
];

/// `_strip_invisible`.
pub fn strip_invisible(text: &str) -> String {
    text.chars().filter(|c| !INVISIBLE.contains(c)).collect()
}

/// The cleaned name before any lookup: invisible characters out, then
/// `str.strip()`; empty is `None`.
pub fn clean_name(name: Option<&str>) -> Option<String> {
    let stripped = strip_invisible(name.unwrap_or(""));
    let s = py_strip(&stripped);
    if s.is_empty() { None } else { Some(s.to_string()) }
}

/// `resolve_canonical_name`: the tenant's existing spelling when one matches
/// case-insensitively, else the cleaned name, else the default warehouse.
pub async fn resolve_canonical_name(
    conn: &mut PgConnection,
    tenant_id: &str,
    name: Option<&str>,
) -> Result<String, sqlx::Error> {
    let Some(cleaned) = clean_name(name) else { return Ok(DEFAULT_WAREHOUSE.to_string()) };
    let row: Option<(String,)> = sqlx::query_as(
        "SELECT name FROM warehouses WHERE tenant_id = $1 AND LOWER(name) = LOWER($2) ORDER BY name LIMIT 1",
    )
    .bind(tenant_id)
    .bind(&cleaned)
    .fetch_optional(&mut *conn)
    .await?;
    Ok(row.map(|(n,)| n).unwrap_or(cleaned))
}

/// `get_warehouse_by_name`, as an existence check.
pub async fn exists(conn: &mut PgConnection, tenant_id: &str, name: &str) -> Result<bool, sqlx::Error> {
    let row: Option<(i32,)> =
        sqlx::query_as("SELECT 1 FROM warehouses WHERE tenant_id = $1 AND name = $2")
            .bind(tenant_id)
            .bind(name)
            .fetch_optional(&mut *conn)
            .await?;
    Ok(row.is_some())
}

/// `count_warehouses`.
pub async fn count(conn: &mut PgConnection, tenant_id: &str) -> Result<i64, sqlx::Error> {
    let (n,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM warehouses WHERE tenant_id = $1")
        .bind(tenant_id)
        .fetch_one(&mut *conn)
        .await?;
    Ok(n)
}

/// `service._ensure_warehouse`: the tenant's first warehouse becomes its default.
pub async fn ensure(conn: &mut PgConnection, tenant_id: &str, name: &str) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO warehouses (tenant_id, name, is_default)
         SELECT $1, $2, NOT EXISTS (SELECT 1 FROM warehouses WHERE tenant_id = $1)
         ON CONFLICT (tenant_id, name) DO NOTHING",
    )
    .bind(tenant_id)
    .bind(name)
    .execute(&mut *conn)
    .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_are_cleaned_like_python() {
        assert_eq!(clean_name(None), None);
        assert_eq!(clean_name(Some("  \u{200b} ")), None);
        assert_eq!(clean_name(Some(" No\u{202e}rte ")).as_deref(), Some("Norte"));
    }
}
