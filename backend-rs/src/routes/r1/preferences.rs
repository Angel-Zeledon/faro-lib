//! `GET` / `PATCH /api/v1/me/preferences` (backend/api/v1/preferences.py and
//! backend/preferences/service.py): the caller's own language, theme and
//! direct-message SMS switch.
//!
//! Any signed-in role may read AND write: these are the person's own UI
//! settings, so the write is guarded by `get_current_user` alone (no analyst
//! guard, no trial read-only guard), exactly like Python. Keys are refused:
//! `preferences` is an internal tag.

use axum::body::Bytes;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::{Extension, Json};
use serde_json::{json, Value};

use crate::auth::{self, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, body_object, bool_field, str_field, Errors, Field, NO_STR_RULES};

pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("internal tag 'preferences': per-person UI preferences; a key is not a person"),
    is_mcp: false,
};

/// `_VALID_LANG` / `_VALID_THEME`, already sorted the way the 400 prints them.
const VALID_LANG: [&str; 2] = ["en", "es"];
const VALID_THEME: [&str; 2] = ["dark", "light"];

#[derive(Debug, Clone, PartialEq)]
struct Prefs {
    language: String,
    theme: String,
    dm_sms_enabled: bool,
}

impl Prefs {
    /// `_DEFAULTS`.
    fn defaults() -> Self {
        Prefs { language: "es".into(), theme: "dark".into(), dm_sms_enabled: false }
    }
    fn json(&self) -> Value {
        json!({"language": self.language, "theme": self.theme, "dm_sms_enabled": self.dm_sms_enabled})
    }
}

/// `get_preferences`: the stored row, or the defaults.
async fn load(pool: &sqlx::PgPool, tenant_id: &str, user_id: &str) -> Result<Prefs, sqlx::Error> {
    let row: Option<(String, String, bool)> = sqlx::query_as(
        "SELECT language, theme, dm_sms_enabled FROM user_preferences WHERE user_id = $1 AND tenant_id = $2",
    )
    .bind(user_id)
    .bind(tenant_id)
    .fetch_optional(pool)
    .await?;
    Ok(row
        .map(|(language, theme, dm_sms_enabled)| Prefs { language, theme, dm_sms_enabled })
        .unwrap_or_else(Prefs::defaults))
}

pub async fn get_preferences(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    Ok(ok(load(&state.pool, &user.tenant_id, &user.user_id).await?.json()))
}

/// `PreferencesUpdate`: three optional fields, `None` when absent or null.
struct Update {
    language: Option<String>,
    theme: Option<String>,
    dm_sms_enabled: Option<bool>,
}

fn some<T>(f: Field<T>) -> Option<T> {
    match f {
        Field::Value(v) => Some(v),
        _ => None,
    }
}

fn validate(obj: &serde_json::Map<String, Value>) -> Result<Update, ApiError> {
    let mut errs = Errors::default();
    let p = [Value::String("body".into())];
    let language = str_field(&mut errs, obj, &p, "language", false, true, &NO_STR_RULES);
    let theme = str_field(&mut errs, obj, &p, "theme", false, true, &NO_STR_RULES);
    let dm_sms_enabled = bool_field(&mut errs, obj, &p, "dm_sms_enabled", true);
    errs.into_result()?;
    Ok(Update { language: some(language), theme: some(theme), dm_sms_enabled: some(dm_sms_enabled) })
}

/// The handler's own checks, language first.
fn check_choices(u: &Update) -> Result<(), ApiError> {
    if let Some(l) = &u.language {
        if !VALID_LANG.contains(&l.as_str()) {
            return Err(ApiError::http(400, "Invalid language. Options: ['en', 'es']"));
        }
    }
    if let Some(t) = &u.theme {
        if !VALID_THEME.contains(&t.as_str()) {
            return Err(ApiError::http(400, "Invalid theme. Options: ['dark', 'light']"));
        }
    }
    Ok(())
}

/// `update_preferences`: every field the caller did not send keeps its
/// current value (or the default), then one upsert.
fn merge(current: Prefs, u: Update) -> Prefs {
    Prefs {
        language: u.language.unwrap_or(current.language),
        theme: u.theme.unwrap_or(current.theme),
        dm_sms_enabled: u.dm_sms_enabled.unwrap_or(current.dm_sms_enabled),
    }
}

pub async fn update_preferences(
    State(state): State<AppState>,
    Extension(actors): Extension<RequestActors>,
    headers: HeaderMap,
    bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    // FastAPI's order: JSON decode, then the auth dependency, then the model.
    let content_type = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(content_type, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let update = validate(&body_object(&body)?)?;
    check_choices(&update)?;

    let current = load(&state.pool, &user.tenant_id, &user.user_id).await?;
    let new = merge(current, update);
    sqlx::query(
        "INSERT INTO user_preferences (user_id, tenant_id, language, theme, dm_sms_enabled, updated_at)
         VALUES ($1, $2, $3, $4, $5, NOW())
         ON CONFLICT (user_id) DO UPDATE
         SET language = EXCLUDED.language, theme = EXCLUDED.theme,
             dm_sms_enabled = EXCLUDED.dm_sms_enabled, updated_at = NOW()",
    )
    .bind(&user.user_id)
    .bind(&user.tenant_id)
    .bind(&new.language)
    .bind(&new.theme)
    .bind(new.dm_sms_enabled)
    .execute(&state.pool)
    .await?;
    Ok(ok(new.json()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn obj(v: Value) -> serde_json::Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn nulls_and_absent_keep_current_values() {
        let u = validate(&obj(json!({"language": null, "dm_sms_enabled": "yes"}))).unwrap();
        let cur = Prefs { language: "en".into(), theme: "light".into(), dm_sms_enabled: false };
        assert_eq!(merge(cur, u), Prefs { language: "en".into(), theme: "light".into(), dm_sms_enabled: true });
    }

    #[test]
    fn language_is_checked_before_theme() {
        let u = validate(&obj(json!({"language": "fr", "theme": "blue"}))).unwrap();
        let e = check_choices(&u).unwrap_err();
        assert_eq!(e.body["error_code"], "language_invalid");
        let u = validate(&obj(json!({"theme": "blue"}))).unwrap();
        assert_eq!(check_choices(&u).unwrap_err().body["error_code"], "theme_invalid");
    }

    #[test]
    fn wrong_types_are_pydantic_errors_in_field_order() {
        let e = validate(&obj(json!({"dm_sms_enabled": "maybe", "language": 3}))).err().unwrap();
        let types: Vec<&str> = e.body["detail"].as_array().unwrap().iter().map(|x| x["type"].as_str().unwrap()).collect();
        assert_eq!(types, ["string_type", "bool_parsing"]);
    }
}
