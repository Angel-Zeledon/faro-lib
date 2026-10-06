//! `GET /api/v1/tenant/export` and `DELETE /api/v1/tenant`
//! (backend/api/v1/tenant_data.py over backend/tenants/data_export.py):
//! the tenant's right to take its data out and to have it erased.
//!
//! Admin only and never a key (`tenant` is an internal tag), and refused to an
//! admin limited to some warehouses (both are company totals).
//!
//! The tables are NOT re-typed here: `tenant_tables.rs` is generated from the
//! Python lists by `scripts/gen_rust_tenant_tables.py`, and the contract
//! harness fails when it is stale. So the erasure covers exactly the tables
//! Python's covers, in the same order, in one transaction, and the export has
//! exactly Python's files and columns (secrets excluded by the same column
//! lists).
//!
//! Row rendering. Python hands each row to `json.dumps(rows, default=...,
//! indent=2, ensure_ascii=False)`: `datetime` as `isoformat()`, anything the
//! encoder does not know (`Decimal`, `UUID`, `date`) as `str()`, floats as
//! `repr`, JSONB as parsed. [`Cell`] reproduces that for the column types the
//! schema uses (text, int, float8, bool, jsonb, timestamptz, date, text[]);
//! any other type is selected `::text`, which is what `str()` gives for
//! numeric, uuid and time.

use std::io::Write as _;
use std::path::{Component, Path, PathBuf};

use axum::body::{Body as HttpBody, Bytes};
use axum::extract::State;
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use chrono::{DateTime, Local, NaiveDate, NaiveDateTime, Utc};
use serde_json::{json, Map, Value};
use sqlx::postgres::PgRow;
use sqlx::{PgPool, Row};

use super::billing_access::{grants_access, SubscriptionState};
use super::tenant_tables::{DELETE_ORDER, EXPORT_SPECS, OMITTED_FROM_EXPORT, STORAGE_CATEGORIES};
use crate::audit::{self as trail, Note};
use crate::auth::{self, warehouse_scope as wscope, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::pycompat::{isoformat_date, isoformat_utc, py_strip};
use crate::pyjson;
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, str_field, Errors, Field, NO_STR_RULES};

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("internal tag 'tenant': tenant export and deletion"),
    is_mcp: false,
};

// ── Cells and Python's json.dumps(indent=2, ensure_ascii=False) ──────────────

/// One column value. `Float` carries NaN and the infinities, which a JSON
/// `Value` cannot, and which `json.dumps` writes as `NaN` / `Infinity`. `Json`
/// is a JSONB column, kept apart from `Value` because Python's `json.loads`
/// keeps integers of any size (`serde_json` would turn 10^20 into a float).
#[derive(Debug, Clone)]
enum Cell {
    Val(Value),
    Float(f64),
    Json(Pj),
}

/// A parsed JSON document as Python sees it: integers stay integers whatever
/// their size (their digits are kept as text), everything with a fraction or an
/// exponent is a float.
#[derive(Debug, Clone, PartialEq)]
enum Pj {
    Null,
    Bool(bool),
    Int(String),
    Float(f64),
    Str(String),
    Arr(Vec<Pj>),
    Obj(Vec<(String, Pj)>),
}

/// Parse the canonical JSON text Postgres prints for a json / jsonb value.
fn parse_pj(text: &str) -> Option<Pj> {
    let b = text.as_bytes();
    let mut i = 0;
    let v = pj_value(b, &mut i)?;
    pj_ws(b, &mut i);
    (i == b.len()).then_some(v)
}

fn pj_ws(b: &[u8], i: &mut usize) {
    while *i < b.len() && matches!(b[*i], b' ' | b'\t' | b'\n' | b'\r') {
        *i += 1;
    }
}

fn pj_value(b: &[u8], i: &mut usize) -> Option<Pj> {
    pj_ws(b, i);
    match *b.get(*i)? {
        b'n' if b[*i..].starts_with(b"null") => {
            *i += 4;
            Some(Pj::Null)
        }
        b't' if b[*i..].starts_with(b"true") => {
            *i += 4;
            Some(Pj::Bool(true))
        }
        b'f' if b[*i..].starts_with(b"false") => {
            *i += 5;
            Some(Pj::Bool(false))
        }
        b'"' => pj_string(b, i).map(Pj::Str),
        b'[' => {
            *i += 1;
            let mut items = Vec::new();
            pj_ws(b, i);
            if b.get(*i) == Some(&b']') {
                *i += 1;
                return Some(Pj::Arr(items));
            }
            loop {
                items.push(pj_value(b, i)?);
                pj_ws(b, i);
                match *b.get(*i)? {
                    b',' => *i += 1,
                    b']' => {
                        *i += 1;
                        return Some(Pj::Arr(items));
                    }
                    _ => return None,
                }
            }
        }
        b'{' => {
            *i += 1;
            let mut items = Vec::new();
            pj_ws(b, i);
            if b.get(*i) == Some(&b'}') {
                *i += 1;
                return Some(Pj::Obj(items));
            }
            loop {
                pj_ws(b, i);
                let k = pj_string(b, i)?;
                pj_ws(b, i);
                if *b.get(*i)? != b':' {
                    return None;
                }
                *i += 1;
                items.push((k, pj_value(b, i)?));
                pj_ws(b, i);
                match *b.get(*i)? {
                    b',' => *i += 1,
                    b'}' => {
                        *i += 1;
                        return Some(Pj::Obj(items));
                    }
                    _ => return None,
                }
            }
        }
        b'-' | b'0'..=b'9' => {
            let start = *i;
            while *i < b.len() && matches!(b[*i], b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9') {
                *i += 1;
            }
            let tok = std::str::from_utf8(&b[start..*i]).ok()?;
            if tok.contains(['.', 'e', 'E']) {
                Some(Pj::Float(tok.parse().ok()?))
            } else {
                // Python prints int("-0") as 0.
                let digits = if tok == "-0" { "0" } else { tok };
                Some(Pj::Int(digits.to_string()))
            }
        }
        _ => None,
    }
}

fn pj_string(b: &[u8], i: &mut usize) -> Option<String> {
    if *b.get(*i)? != b'"' {
        return None;
    }
    *i += 1;
    let mut out = String::new();
    let mut units: Vec<u16> = Vec::new();
    let flush = |units: &mut Vec<u16>, out: &mut String| {
        if !units.is_empty() {
            out.push_str(&String::from_utf16_lossy(units));
            units.clear();
        }
    };
    loop {
        let c = *b.get(*i)?;
        match c {
            b'"' => {
                *i += 1;
                flush(&mut units, &mut out);
                return Some(out);
            }
            b'\\' => {
                *i += 1;
                let e = *b.get(*i)?;
                *i += 1;
                if e == b'u' {
                    let hex = std::str::from_utf8(b.get(*i..*i + 4)?).ok()?;
                    units.push(u16::from_str_radix(hex, 16).ok()?);
                    *i += 4;
                    continue;
                }
                flush(&mut units, &mut out);
                out.push(match e {
                    b'"' => '"',
                    b'\\' => '\\',
                    b'/' => '/',
                    b'b' => '\u{08}',
                    b'f' => '\u{0c}',
                    b'n' => '\n',
                    b'r' => '\r',
                    b't' => '\t',
                    _ => return None,
                });
            }
            _ => {
                flush(&mut units, &mut out);
                // Copy one UTF-8 scalar.
                let len = match c {
                    0..=0x7f => 1,
                    0xc0..=0xdf => 2,
                    0xe0..=0xef => 3,
                    _ => 4,
                };
                out.push_str(std::str::from_utf8(b.get(*i..*i + len)?).ok()?);
                *i += len;
            }
        }
    }
}

fn dump_pj(v: &Pj, level: usize, out: &mut String) {
    match v {
        Pj::Null => out.push_str("null"),
        Pj::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Pj::Int(d) => out.push_str(d),
        Pj::Float(f) => dump_float(*f, out),
        Pj::Str(s) => dump_str(s, out),
        Pj::Arr(items) if items.is_empty() => out.push_str("[]"),
        Pj::Arr(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                pad(out, level + 1);
                dump_pj(item, level + 1, out);
            }
            pad(out, level);
            out.push(']');
        }
        Pj::Obj(items) if items.is_empty() => out.push_str("{}"),
        Pj::Obj(items) => {
            out.push('{');
            for (i, (k, item)) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                pad(out, level + 1);
                dump_str(k, out);
                out.push_str(": ");
                dump_pj(item, level + 1, out);
            }
            pad(out, level);
            out.push('}');
        }
    }
}

fn dump_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            // ensure_ascii=False: only the C0 controls are escaped.
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

fn dump_float(f: f64, out: &mut String) {
    if f.is_nan() {
        out.push_str("NaN");
    } else if f.is_infinite() {
        out.push_str(if f > 0.0 { "Infinity" } else { "-Infinity" });
    } else {
        out.push_str(&pyjson::float_repr(f));
    }
}

fn pad(out: &mut String, level: usize) {
    out.push('\n');
    for _ in 0..level {
        out.push_str("  ");
    }
}

fn dump_value(v: &Value, level: usize, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                out.push_str(&i.to_string());
            } else if let Some(u) = n.as_u64() {
                out.push_str(&u.to_string());
            } else {
                dump_float(n.as_f64().unwrap_or(0.0), out);
            }
        }
        Value::String(s) => dump_str(s, out),
        Value::Array(items) if items.is_empty() => out.push_str("[]"),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                pad(out, level + 1);
                dump_value(item, level + 1, out);
            }
            pad(out, level);
            out.push(']');
        }
        Value::Object(map) if map.is_empty() => out.push_str("{}"),
        Value::Object(map) => {
            out.push('{');
            for (i, (k, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                pad(out, level + 1);
                dump_str(k, out);
                out.push_str(": ");
                dump_value(item, level + 1, out);
            }
            pad(out, level);
            out.push('}');
        }
    }
}

fn dump_cell(c: &Cell, level: usize, out: &mut String) {
    match c {
        Cell::Val(v) => dump_value(v, level, out),
        Cell::Float(f) => dump_float(*f, out),
        Cell::Json(v) => dump_pj(v, level, out),
    }
}

/// One row dict at nesting `level` (`{}` when it has no columns).
fn dump_object(row: &[(String, Cell)], level: usize, out: &mut String) {
    if row.is_empty() {
        out.push_str("{}");
        return;
    }
    out.push('{');
    for (j, (k, cell)) in row.iter().enumerate() {
        if j > 0 {
            out.push(',');
        }
        pad(out, level + 1);
        dump_str(k, out);
        out.push_str(": ");
        dump_cell(cell, level + 1, out);
    }
    pad(out, level);
    out.push('}');
}

/// `json.dumps(rows, indent=2, ensure_ascii=False)` for a list of row dicts.
fn dump_rows(rows: &[Vec<(String, Cell)>]) -> String {
    if rows.is_empty() {
        return "[]".into();
    }
    let mut out = String::from("[");
    for (i, row) in rows.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        pad(&mut out, 1);
        dump_object(row, 1, &mut out);
    }
    pad(&mut out, 0);
    out.push(']');
    out
}

// ── Reading rows ─────────────────────────────────────────────────────────────

/// Column types decoded natively; everything else is selected `::text`.
fn native(udt: &str) -> bool {
    matches!(
        udt,
        "int2" | "int4" | "int8" | "bool" | "float8" | "text" | "varchar" | "bpchar" | "name"
            | "timestamptz" | "timestamp" | "date" | "_text" | "_varchar"
    )
}

/// The select list for one spec, with each column's decoding type: a column
/// that is not natively decoded is read as text (a float4 is read as text and
/// parsed, which is what psycopg2 does with its text protocol).
async fn select_list(pool: &PgPool, table: &str, cols: &str) -> Result<(String, Vec<(String, String)>), sqlx::Error> {
    let found: Vec<(String, String)> = sqlx::query_as(
        "SELECT column_name::text, udt_name::text FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = $1 ORDER BY ordinal_position",
    )
    .bind(table)
    .fetch_all(pool)
    .await?;
    let wanted: Vec<String> = if cols.trim() == "*" {
        found.iter().map(|(n, _)| n.clone()).collect()
    } else {
        cols.split(',').map(|c| c.trim().to_string()).collect()
    };
    let mut exprs = Vec::new();
    let mut kinds = Vec::new();
    for name in wanted {
        let udt = found.iter().find(|(n, _)| *n == name).map(|(_, u)| u.clone()).unwrap_or_default();
        if native(&udt) {
            exprs.push(format!("\"{name}\""));
            kinds.push((name, udt));
        } else {
            exprs.push(format!("\"{name}\"::text AS \"{name}\""));
            let kind = match udt.as_str() {
                "float4" => "float4_text",
                "json" | "jsonb" => "json_text",
                _ => "text_cast",
            };
            kinds.push((name, kind.to_string()));
        }
    }
    Ok((exprs.join(", "), kinds))
}

fn cell_of(row: &PgRow, idx: usize, kind: &str) -> Result<Cell, sqlx::Error> {
    macro_rules! opt {
        ($t:ty, $f:expr) => {{
            let v: Option<$t> = row.try_get(idx)?;
            match v {
                None => Cell::Val(Value::Null),
                Some(x) => $f(x),
            }
        }};
    }
    Ok(match kind {
        "int2" => opt!(i16, |x| Cell::Val(json!(x))),
        "int4" => opt!(i32, |x| Cell::Val(json!(x))),
        "int8" => opt!(i64, |x| Cell::Val(json!(x))),
        "bool" => opt!(bool, |x| Cell::Val(json!(x))),
        "float8" => opt!(f64, Cell::Float),
        "text" | "varchar" | "bpchar" | "name" | "text_cast" => opt!(String, |x| Cell::Val(Value::String(x))),
        "float4_text" => {
            let v: Option<String> = row.try_get(idx)?;
            match v {
                None => Cell::Val(Value::Null),
                Some(t) => match t.as_str() {
                    "NaN" => Cell::Float(f64::NAN),
                    "Infinity" => Cell::Float(f64::INFINITY),
                    "-Infinity" => Cell::Float(f64::NEG_INFINITY),
                    other => other.parse::<f64>().map(Cell::Float).unwrap_or(Cell::Val(Value::String(t.clone()))),
                },
            }
        }
        "json_text" => {
            let v: Option<String> = row.try_get(idx)?;
            match v {
                None => Cell::Val(Value::Null),
                Some(t) => Cell::Json(
                    parse_pj(&t).ok_or_else(|| sqlx::Error::Decode("unparseable JSON text from Postgres".into()))?,
                ),
            }
        }
        "timestamptz" => opt!(DateTime<Utc>, |x: DateTime<Utc>| Cell::Val(Value::String(isoformat_utc(&x)))),
        "timestamp" => opt!(NaiveDateTime, |x: NaiveDateTime| {
            let micros = x.and_utc().timestamp_subsec_micros();
            let base = x.format("%Y-%m-%dT%H:%M:%S").to_string();
            Cell::Val(Value::String(if micros == 0 { base } else { format!("{base}.{micros:06}") }))
        }),
        "date" => opt!(NaiveDate, |x: NaiveDate| Cell::Val(Value::String(isoformat_date(&x)))),
        "_text" | "_varchar" => opt!(Vec<Option<String>>, |xs: Vec<Option<String>>| {
            Cell::Val(Value::Array(xs.into_iter().map(|s| s.map(Value::String).unwrap_or(Value::Null)).collect()))
        }),
        _ => return Err(sqlx::Error::Decode(format!("unsupported column kind {kind}").into())),
    })
}

fn rows_to_cells(rows: &[PgRow], kinds: &[(String, String)]) -> Result<Vec<Vec<(String, Cell)>>, sqlx::Error> {
    let mut out = Vec::with_capacity(rows.len());
    for r in rows {
        let mut cells = Vec::with_capacity(kinds.len());
        for (i, (name, kind)) in kinds.iter().enumerate() {
            cells.push((name.clone(), cell_of(r, i, kind)?));
        }
        out.push(cells);
    }
    Ok(out)
}

/// `screenshot_abspath`: the stored name must be ONE plain file name inside
/// the tenant's own feedback folder. Anything else (a path, `..`, an absolute
/// path, empty) is skipped, like Python's `ValueError`. Symlinks are not
/// resolved (Python's `.resolve()` would).
fn screenshot_path(storage: &Path, tenant_id: &str, relative: &str) -> Option<PathBuf> {
    let base = storage.join("feedback").join(tenant_id);
    let mut normal = None;
    for c in Path::new(relative).components() {
        match c {
            Component::CurDir => {}
            Component::Normal(n) if normal.is_none() => normal = Some(n.to_os_string()),
            _ => return None,
        }
    }
    normal.map(|n| base.join(n))
}

struct Export {
    tenant: Vec<(String, Cell)>,
    tables: Vec<(&'static str, Vec<Vec<(String, Cell)>>)>,
    screenshots: Vec<PathBuf>,
}

async fn load_export(state: &AppState, tenant_id: &str) -> Result<Option<Export>, sqlx::Error> {
    let pool = &state.pool;
    let (list, kinds) = select_list(pool, "tenants", "*").await?;
    let rows = sqlx::query(&format!("SELECT {list} FROM tenants WHERE id = $1")).bind(tenant_id).fetch_all(pool).await?;
    let Some(first) = rows.first() else { return Ok(None) };
    let tenant = rows_to_cells(std::slice::from_ref(first), &kinds)?.remove(0);

    let mut tables = Vec::new();
    for (stem, table, cols) in EXPORT_SPECS {
        let (list, kinds) = select_list(pool, table, cols).await?;
        let rows = sqlx::query(&format!("SELECT {list} FROM {table} WHERE tenant_id = $1"))
            .bind(tenant_id)
            .fetch_all(pool)
            .await?;
        tables.push((*stem, rows_to_cells(&rows, &kinds)?));
    }

    let shots: Vec<(String,)> = sqlx::query_as(
        "SELECT screenshot_path FROM feedback_reports WHERE tenant_id = $1 AND screenshot_path IS NOT NULL",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let mut screenshots = Vec::new();
    for (rel,) in shots {
        match screenshot_path(&state.settings.storage_path, tenant_id, &rel) {
            Some(f) if f.is_file() => screenshots.push(f),
            Some(_) => {}
            None => tracing::warn!("export: skipped a feedback screenshot: path escapes the feedback folder"),
        }
    }
    Ok(Some(Export { tenant, tables, screenshots }))
}

fn build_zip(tenant_id: &str, ex: &Export) -> std::io::Result<Vec<u8>> {
    let zerr = |e: zip::result::ZipError| std::io::Error::other(e.to_string());
    let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    let mut buf = std::io::Cursor::new(Vec::new());
    {
        let mut zf = zip::ZipWriter::new(&mut buf);
        let mut manifest_tables = Map::new();

        let mut text = String::new();
        dump_object(&ex.tenant, 0, &mut text);
        zf.start_file("tenant.json", opts).map_err(zerr)?;
        zf.write_all(text.as_bytes())?;
        manifest_tables.insert("tenant".into(), json!(1));

        for (stem, rows) in &ex.tables {
            zf.start_file(format!("{stem}.json"), opts).map_err(zerr)?;
            zf.write_all(dump_rows(rows).as_bytes())?;
            manifest_tables.insert((*stem).into(), json!(rows.len()));
        }

        let mut shots = 0;
        for f in &ex.screenshots {
            let Some(name) = f.file_name().and_then(|n| n.to_str()) else { continue };
            match std::fs::read(f) {
                Ok(bytes) => {
                    zf.start_file(format!("feedback_screenshots/{name}"), opts).map_err(zerr)?;
                    zf.write_all(&bytes)?;
                    shots += 1;
                }
                Err(e) => tracing::warn!("export: skipped a feedback screenshot: {e}"),
            }
        }

        let mut manifest = Map::new();
        manifest.insert("tenant_id".into(), json!(tenant_id));
        manifest.insert("generated_at".into(), json!(isoformat_utc(&Utc::now())));
        manifest.insert("tables".into(), Value::Object(manifest_tables));
        manifest.insert("omitted".into(), json!(OMITTED_FROM_EXPORT));
        manifest.insert("feedback_screenshots".into(), json!(shots));
        let mut text = String::new();
        dump_value(&Value::Object(manifest), 0, &mut text);
        zf.start_file("manifest.json", opts).map_err(zerr)?;
        zf.write_all(text.as_bytes())?;
        zf.finish().map_err(zerr)?;
    }
    Ok(buf.into_inner())
}

// ── Guards ───────────────────────────────────────────────────────────────────

async fn admin(state: &AppState, actors: &RequestActors, headers: &HeaderMap) -> Result<CurrentUser, ApiError> {
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    auth::require_role(&user, &["admin"])?;
    Ok(user)
}

// ── GET /tenant/export ───────────────────────────────────────────────────────

pub async fn export(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let user = admin(&state, &actors, &headers).await?;
    wscope::require_company_wide(&state.pool, &user).await?;
    let Some(ex) = load_export(&state, &user.tenant_id).await? else {
        return Err(ApiError::http(404, format!("Tenant not found: {}", user.tenant_id)));
    };
    let tenant_id = user.tenant_id.clone();
    let zip_bytes = tokio::task::spawn_blocking(move || build_zip(&tenant_id, &ex))
        .await
        .map_err(|_| ApiError::internal())?
        .map_err(|e| {
            tracing::error!(error = %e, "tenant export failed");
            ApiError::internal()
        })?;

    // The audit row is written before the body goes out, like the middleware.
    trail::record(&state, &actors, "GET", "/tenant/export", None, Note::default(), 200).await;

    let filename = format!("stockai_export_{}_{}.zip", user.tenant_id, Local::now().date_naive().format("%Y-%m-%d"));
    let mut resp = (StatusCode::OK, HttpBody::from(Bytes::from(zip_bytes))).into_response();
    let h = resp.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("application/zip"));
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"{filename}\"")) {
        h.insert(header::CONTENT_DISPOSITION, v);
    }
    Ok(resp)
}

// ── DELETE /tenant ───────────────────────────────────────────────────────────

/// Does any subscription still renew? `live` in Python: grants access and is
/// neither cancelled nor set to cancel at period end. Returns its provider.
async fn live_subscription(pool: &PgPool, tenant_id: &str) -> Result<Option<String>, sqlx::Error> {
    type Sub = (String, Option<String>, Option<DateTime<Utc>>, bool, Option<DateTime<Utc>>, Option<DateTime<Utc>>);
    let rows: Vec<Sub> = sqlx::query_as(
        "SELECT provider, status, current_period_end, cancel_at_period_end, past_due_since, ended_at
         FROM billing_subscriptions WHERE tenant_id = $1 ORDER BY updated_at DESC",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?;
    let now = Utc::now();
    for (provider, status, period_end, cancel_at_end, past_due_since, ended_at) in rows {
        let status = status.unwrap_or_default();
        let sub = SubscriptionState {
            status: status.clone(),
            current_period_end: period_end,
            cancel_at_period_end: cancel_at_end,
            past_due_since,
            ended_at,
        };
        if grants_access(&sub, now) && !(status == "canceled" || cancel_at_end) {
            return Ok(Some(provider));
        }
    }
    Ok(None)
}

/// `_delete_storage_files`: best effort, one `rmtree` per category.
fn delete_storage_files(storage: &Path, tenant_id: &str) -> Vec<String> {
    let mut removed = Vec::new();
    for category in STORAGE_CATEGORIES {
        let target = storage.join(category).join(tenant_id);
        if target.exists() {
            match std::fs::remove_dir_all(&target) {
                Ok(()) => removed.push(target.display().to_string()),
                Err(e) => tracing::warn!("Could not remove storage dir {}: {e}", target.display()),
            }
        }
    }
    removed
}

pub async fn erase(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let content_type = headers.get(header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = admin(&state, &actors, &headers).await?;
    let obj = body_object(&body)?;
    let mut errs = Errors::default();
    let confirm = str_field(&mut errs, &obj, &[Value::String("body".into())], "confirm", true, false, &NO_STR_RULES);
    errs.into_result()?;
    let Field::Value(confirm) = confirm else { return Err(ApiError::internal()) };

    wscope::require_company_wide(&state.pool, &user).await?;
    let slug: Option<(String,)> = sqlx::query_as("SELECT slug FROM tenants WHERE id = $1")
        .bind(&user.tenant_id)
        .fetch_optional(&state.pool)
        .await?;
    let Some((slug,)) = slug else { return Err(ApiError::http(404, "Tenant not found")) };

    let confirm = py_strip(&confirm);
    if confirm != slug && confirm.to_uppercase() != "DELETE" {
        return Err(ApiError::http(400, "Confirmation required: pass the tenant's slug or the literal 'DELETE'."));
    }

    if let Some(provider) = live_subscription(&state.pool, &user.tenant_id).await? {
        return Err(ApiError::app(
            "tenant_has_active_subscription",
            "Cancel the plan's subscription before erasing the account.",
            409,
            json!({"provider": provider}),
        ));
    }

    // One transaction: either the whole tenant goes or none of it does.
    let mut tx = state.pool.begin().await?;
    for table in DELETE_ORDER {
        sqlx::query(&format!("DELETE FROM {table} WHERE tenant_id = $1"))
            .bind(&user.tenant_id)
            .execute(&mut *tx)
            .await?;
    }
    sqlx::query("DELETE FROM tenants WHERE id = $1").bind(&user.tenant_id).execute(&mut *tx).await?;
    tx.commit().await?;

    let storage = state.settings.storage_path.clone();
    let tenant_id = user.tenant_id.clone();
    let removed = tokio::task::spawn_blocking(move || delete_storage_files(&storage, &tenant_id))
        .await
        .unwrap_or_default();
    tracing::warn!("[tenant] tenant={} ERASED by user={}", user.tenant_id, user.user_id);
    Ok(ok(json!({"tenant_id": user.tenant_id, "removed_storage_dirs": removed})))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(cells: Vec<(&str, Cell)>) -> Vec<(String, Cell)> {
        cells.into_iter().map(|(k, c)| (k.to_string(), c)).collect()
    }

    #[test]
    fn rows_render_like_json_dumps_indent_2() {
        let rows = vec![row(vec![
            ("id", Cell::Val(json!("a\"b\n\u{e9}"))),
            ("n", Cell::Val(json!(3))),
            ("f", Cell::Float(2.0)),
            ("nan", Cell::Float(f64::NAN)),
            ("j", Cell::Val(json!({"x": [1, 2.5], "e": {}, "l": []}))),
            ("none", Cell::Val(Value::Null)),
        ])];
        let expected = concat!(
            "[\n  {\n    \"id\": \"a\\\"b\\n\u{e9}\",\n    \"n\": 3,\n    \"f\": 2.0,\n    \"nan\": NaN,\n",
            "    \"j\": {\n      \"x\": [\n        1,\n        2.5\n      ],\n      \"e\": {},\n      \"l\": []\n    },\n",
            "    \"none\": null\n  }\n]"
        );
        assert_eq!(dump_rows(&rows), expected);
        assert_eq!(dump_rows(&[]), "[]");
    }

    #[test]
    fn json_keeps_python_integers_and_floats() {
        let v = parse_pj(
            r#"["a", 1, 2.0, 100000000000000000000, -0, 1.5e-7, {"k": [null, true, "xé😀\n"]}, {}, []]"#,
        )
        .unwrap();
        let mut out = String::new();
        dump_pj(&v, 0, &mut out);
        assert!(out.contains("\n  100000000000000000000,\n"));
        assert!(out.contains("\n  0,\n"));
        assert!(out.contains("\n  1.5e-07,\n"));
        assert!(out.contains("\"x\u{e9}\u{1f600}\\n\""));
        assert_eq!(parse_pj("[1,"), None);
    }

    #[test]
    fn screenshot_names_must_be_one_plain_file() {
        let s = Path::new("/st");
        assert_eq!(screenshot_path(s, "t1", "r1.png"), Some(PathBuf::from("/st/feedback/t1/r1.png")));
        assert_eq!(screenshot_path(s, "t1", "./r1.png"), Some(PathBuf::from("/st/feedback/t1/r1.png")));
        assert_eq!(screenshot_path(s, "t1", "../t2/r1.png"), None);
        assert_eq!(screenshot_path(s, "t1", "sub/r1.png"), None);
        assert_eq!(screenshot_path(s, "t1", "/etc/passwd"), None);
        assert_eq!(screenshot_path(s, "t1", ""), None);
    }
}
