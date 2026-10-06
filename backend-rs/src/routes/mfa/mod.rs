//! Two-step sign-in management: TOTP enrollment, recovery codes and the tenant
//! policy. NEW routes (there is no Python twin and no Python failover): the
//! sign-in half (the challenge, the code check, the token pair) stays in
//! `backend/api/v1/auth.py` + `backend/auth/mfa.py` because auth moves in a
//! later wave.
//!
//! | Route | Who | What |
//! |---|---|---|
//! | `GET /mfa/status` | any signed-in user | own enrollment, recovery codes left, tenant policy |
//! | `POST /mfa/enroll/begin` | a session, or an `enrollment_token` | new pending secret + `otpauth://` URI |
//! | `POST /mfa/enroll/confirm` | same | first valid code activates it; returns the recovery codes ONCE |
//! | `POST /mfa/disable` | a session | re-auth with a code; refused while the tenant requires MFA |
//! | `POST /mfa/recovery-codes/regenerate` | a session | re-auth with a code; replaces every code |
//! | `GET /mfa/policy`, `PUT /mfa/policy` | admin | who is enrolled; require MFA for the tenant |
//! | `POST /mfa/users/{user_id}/reset` | admin | clear a locked-out person's enrollment |
//!
//! Properties each of these keeps, and the test that pins it:
//! * The secret is Fernet-encrypted under the key every other stored secret
//!   uses, recovery codes are stored as an HMAC, and neither the secret nor a
//!   code is ever logged or put in an audit row.
//! * Code guesses share the per-user `auth_rate_events` bucket `mfa:<user>`
//!   with Python's challenge verification (10 per 10 minutes), and an
//!   enrollment token allows 5 guesses in total.
//! * A TOTP time-step is accepted once (`last_used_step`, compare-and-set);
//!   a recovery code is burned by a compare-and-set on `used_at`.
//! * Requiring MFA is refused unless the acting admin is enrolled, so a tenant
//!   can never be locked out by its own policy; enabling it ends the sessions
//!   of password users who are not enrolled yet (they re-enter through the
//!   enrollment step of login).
//! * Social / enterprise sign-ins follow the identity provider's own MFA and
//!   are exempt; API keys are refused here outright (`Exposure::Internal`).

pub mod totp;

use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::activity::{record_event, record_event_with_reason, Event};
use crate::audit::{self, Note};
use crate::auth::{self, CurrentUser, Exposure, RequestActors, RouteAuth};
use crate::error::ApiError;
use crate::routes::ok;
use crate::state::AppState;
use crate::validation::{self, Errors, Field, StrRules};

const ISSUER: &str = "StockAI";
const CHALLENGE_MAX_ATTEMPTS: i32 = 5;
const RATE_MAX: i64 = 10;
const RATE_WINDOW_SECS: i64 = 600;

/// Internal: an API key must never read or change a person's second factor.
pub const ROUTE: RouteAuth = RouteAuth {
    exposure: Exposure::Internal("internal tag 'mfa': sign-in security: a key must never manage a second factor"),
    is_mcp: false,
};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/mfa/status", get(status))
        .route("/api/v1/mfa/enroll/begin", post(enroll_begin))
        .route("/api/v1/mfa/enroll/confirm", post(enroll_confirm))
        .route("/api/v1/mfa/disable", post(disable))
        .route("/api/v1/mfa/recovery-codes/regenerate", post(regenerate_codes))
        .route("/api/v1/mfa/policy", get(get_policy).put(put_policy))
        .route("/api/v1/mfa/users/{user_id}/reset", post(reset_user))
}

// ── Shared plumbing ──────────────────────────────────────────────────────────

fn token_hash(raw: &str) -> String {
    hex::encode(Sha256::digest(raw.as_bytes()))
}

fn bad_code() -> ApiError {
    ApiError::app("mfa_code_invalid", "Invalid or already used code.", 401, json!({}))
}

fn bad_challenge() -> ApiError {
    ApiError::app(
        "mfa_challenge_invalid",
        "This sign-in step has expired or was already used. Sign in again.",
        401,
        json!({}),
    )
}

fn unavailable() -> ApiError {
    ApiError::app(
        "mfa_unavailable",
        "Two-step sign-in cannot store its secret: set INTEGRATIONS_SECRET_KEY or open the configuration panel once.",
        503,
        json!({}),
    )
}

/// An optional JSON object body: empty is `{}`.
fn read_object(headers: &HeaderMap, bytes: &Bytes) -> Result<Map<String, Value>, ApiError> {
    if bytes.is_empty() {
        return Ok(Map::new());
    }
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(ct, bytes)?;
    validation::body_object(&body)
}

fn str_rules(max: usize) -> StrRules {
    StrRules { min_length: Some(1), max_length: Some(max), pattern: None }
}

/// The `code` field every re-auth body carries.
fn code_of(obj: &Map<String, Value>) -> Result<String, ApiError> {
    let mut errs = Errors::default();
    let f = validation::str_field(&mut errs, obj, &[Value::String("body".into())], "code", true, false, &str_rules(64));
    errs.into_result()?;
    match f {
        Field::Value(s) => Ok(s),
        _ => Err(ApiError::internal()),
    }
}

fn enrollment_token_of(obj: &Map<String, Value>) -> Result<Option<String>, ApiError> {
    let mut errs = Errors::default();
    let f = validation::str_field(
        &mut errs, obj, &[Value::String("body".into())], "enrollment_token", false, true, &str_rules(256),
    );
    errs.into_result()?;
    Ok(match f {
        Field::Value(s) => Some(s),
        _ => None,
    })
}

/// `_check_rate`: DELETE what left the window, COUNT, INSERT when allowed.
/// Off in testing mode, like Python.
async fn check_rate(state: &AppState, key: &str) -> Result<(), ApiError> {
    if state.settings.testing_mode {
        return Ok(());
    }
    let mut tx = state.pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext($1))").bind(format!("ratelimit:{key}")).execute(&mut *tx).await?;
    sqlx::query("DELETE FROM auth_rate_events WHERE key = $1 AND created_at < NOW() - make_interval(secs => $2)")
        .bind(key)
        .bind(RATE_WINDOW_SECS as f64)
        .execute(&mut *tx)
        .await?;
    let (n,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM auth_rate_events WHERE key = $1")
        .bind(key)
        .fetch_one(&mut *tx)
        .await?;
    let allowed = n < RATE_MAX;
    if allowed {
        sqlx::query("INSERT INTO auth_rate_events (key) VALUES ($1)").bind(key).execute(&mut *tx).await?;
    }
    tx.commit().await?;
    if allowed {
        Ok(())
    } else {
        Err(ApiError::app("too_many_attempts", "Too many attempts. Please try again later.", 429, json!({})))
    }
}

fn encrypt_secret(state: &AppState, secret: &str) -> Result<String, ApiError> {
    let f = crate::service_config::fernet(&state.settings).ok_or_else(unavailable)?;
    Ok(f.encrypt(secret.as_bytes()))
}

fn decrypt_secret(state: &AppState, enc: &str) -> Result<String, ApiError> {
    let f = crate::service_config::fernet(&state.settings).ok_or_else(unavailable)?;
    let plain = f.decrypt(enc).map_err(|_| unavailable())?;
    String::from_utf8(plain).map_err(|_| unavailable())
}

struct MfaRow {
    secret_enc: String,
    status: String,
    last_used_step: Option<i64>,
}

async fn mfa_row(state: &AppState, user_id: &str) -> Result<Option<MfaRow>, ApiError> {
    let row: Option<(String, String, Option<i64>)> =
        sqlx::query_as("SELECT secret_enc, status, last_used_step FROM user_mfa WHERE user_id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await?;
    Ok(row.map(|(secret_enc, status, last_used_step)| MfaRow { secret_enc, status, last_used_step }))
}

async fn tenant_requires(state: &AppState, tenant_id: &str) -> Result<bool, ApiError> {
    let row: Option<(bool,)> = sqlx::query_as("SELECT mfa_required FROM tenants WHERE id = $1")
        .bind(tenant_id)
        .fetch_optional(&state.pool)
        .await?;
    Ok(row.map(|r| r.0).unwrap_or(false))
}

async fn recovery_remaining(state: &AppState, user_id: &str) -> Result<i64, ApiError> {
    let (n,): (i64,) =
        sqlx::query_as("SELECT COUNT(*) FROM user_mfa_recovery_codes WHERE user_id = $1 AND used_at IS NULL")
            .bind(user_id)
            .fetch_one(&state.pool)
            .await?;
    Ok(n)
}

async fn user_email(state: &AppState, user_id: &str, tenant_id: &str) -> Result<Option<(String, String)>, ApiError> {
    let row: Option<(String, Option<String>)> =
        sqlx::query_as("SELECT email, status FROM users WHERE id = $1 AND tenant_id = $2")
            .bind(user_id)
            .bind(tenant_id)
            .fetch_optional(&state.pool)
            .await?;
    Ok(row.map(|(e, s)| (e, s.unwrap_or_else(|| "active".into()))))
}

/// `mfa.verify_second_factor`: a 6-digit code against the active enrollment
/// (step stored by compare-and-set), else a recovery code burned by
/// compare-and-set. `Some("totp" | "recovery")` or None.
async fn verify_second_factor(state: &AppState, user_id: &str, code: &str) -> Result<Option<&'static str>, ApiError> {
    let Some(row) = mfa_row(state, user_id).await? else { return Ok(None) };
    if row.status != "active" {
        return Ok(None);
    }
    if totp::looks_like_totp(code) {
        let secret = decrypt_secret(state, &row.secret_enc)?;
        let now = chrono::Utc::now().timestamp();
        let Some(step) = totp::matching_step(&secret, code, now, row.last_used_step, totp::DIGITS) else {
            return Ok(None);
        };
        let won = sqlx::query(
            "UPDATE user_mfa SET last_used_step = $1
              WHERE user_id = $2 AND (last_used_step IS NULL OR last_used_step < $1)",
        )
        .bind(step)
        .bind(user_id)
        .execute(&state.pool)
        .await?
        .rows_affected();
        return Ok(if won == 1 { Some("totp") } else { None });
    }
    let normalized = totp::normalize_recovery_code(code);
    if normalized.is_empty() {
        return Ok(None);
    }
    let burned = sqlx::query(
        "UPDATE user_mfa_recovery_codes SET used_at = NOW()
          WHERE id = (SELECT id FROM user_mfa_recovery_codes
                       WHERE user_id = $1 AND code_hash = $2 AND used_at IS NULL LIMIT 1)
            AND used_at IS NULL",
    )
    .bind(user_id)
    .bind(totp::recovery_code_hash(&state.settings.secret_key, &normalized))
    .execute(&state.pool)
    .await?
    .rows_affected();
    Ok(if burned == 1 { Some("recovery") } else { None })
}

/// Re-authentication for a sensitive change: rate-limited, then a valid code.
async fn reauth(state: &AppState, user: &CurrentUser, code: &str) -> Result<(), ApiError> {
    check_rate(state, &format!("mfa:{}", user.user_id)).await?;
    match verify_second_factor(state, &user.user_id, code).await? {
        Some(_) => Ok(()),
        None => Err(bad_code()),
    }
}

/// Who is acting: a signed-in session, or the enrollment token a user the
/// tenant requires to enrol receives at login instead of a session.
struct Actor {
    user_id: String,
    tenant_id: String,
    /// Set when the actor is an enrollment token (its hash).
    token_hash: Option<String>,
    session: Option<CurrentUser>,
}

async fn resolve_actor(
    state: &AppState, headers: &HeaderMap, actors: &RequestActors, obj: &Map<String, Value>, spend_attempt: bool,
) -> Result<Actor, ApiError> {
    if let Some(raw) = enrollment_token_of(obj)? {
        let hash = token_hash(&raw);
        let sql = if spend_attempt {
            "UPDATE mfa_challenges SET attempts = attempts + 1
              WHERE token_hash = $1 AND purpose = 'enroll' AND consumed_at IS NULL
                AND expires_at > NOW() AND attempts < $2
          RETURNING user_id, tenant_id"
        } else {
            "SELECT user_id, tenant_id FROM mfa_challenges
              WHERE token_hash = $1 AND purpose = 'enroll' AND consumed_at IS NULL
                AND expires_at > NOW() AND attempts < $2"
        };
        let row: Option<(String, String)> =
            sqlx::query_as(sql).bind(&hash).bind(CHALLENGE_MAX_ATTEMPTS).fetch_optional(&state.pool).await?;
        let Some((user_id, tenant_id)) = row else { return Err(bad_challenge()) };
        match user_email(state, &user_id, &tenant_id).await? {
            Some((_, status)) if status == "active" => {}
            _ => return Err(bad_challenge()),
        }
        return Ok(Actor { user_id, tenant_id, token_hash: Some(hash), session: None });
    }
    let user = auth::current_user(state, headers, ROUTE, actors).await?;
    Ok(Actor { user_id: user.user_id.clone(), tenant_id: user.tenant_id.clone(), token_hash: None, session: Some(user) })
}

async fn insert_recovery_codes(
    tx: &mut sqlx::PgConnection, state: &AppState, user_id: &str, tenant_id: &str, codes: &[String],
) -> Result<(), ApiError> {
    sqlx::query("DELETE FROM user_mfa_recovery_codes WHERE user_id = $1").bind(user_id).execute(&mut *tx).await?;
    for code in codes {
        sqlx::query("INSERT INTO user_mfa_recovery_codes (user_id, tenant_id, code_hash) VALUES ($1, $2, $3)")
            .bind(user_id)
            .bind(tenant_id)
            .bind(totp::recovery_code_hash(&state.settings.secret_key, code))
            .execute(&mut *tx)
            .await?;
    }
    Ok(())
}

fn random_failed(e: getrandom::Error) -> ApiError {
    tracing::error!(error = %e, "OS randomness unavailable");
    ApiError::internal()
}

// ── GET /mfa/status ──────────────────────────────────────────────────────────

async fn status(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let row = mfa_row(&state, &user.user_id).await?;
    let enrolled = row.as_ref().map(|r| r.status == "active").unwrap_or(false);
    let pending = row.as_ref().map(|r| r.status == "pending").unwrap_or(false);
    let required = tenant_requires(&state, &user.tenant_id).await?;
    Ok(ok(json!({
        "enrolled": enrolled,
        "pending": pending,
        "recovery_codes_remaining": if enrolled { recovery_remaining(&state, &user.user_id).await? } else { 0 },
        "required_by_tenant": required,
        "can_disable": enrolled && !required,
    })))
}

// ── POST /mfa/enroll/begin ───────────────────────────────────────────────────

async fn enroll_begin(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, headers: HeaderMap, bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let obj = read_object(&headers, &bytes)?;
    let actor = resolve_actor(&state, &headers, &actors, &obj, false).await?;
    if let Some(row) = mfa_row(&state, &actor.user_id).await? {
        if row.status == "active" {
            return Err(ApiError::app("mfa_already_enrolled", "Two-step sign-in is already on for this account.", 409, json!({})));
        }
    }
    let Some((email, _)) = user_email(&state, &actor.user_id, &actor.tenant_id).await? else {
        return Err(ApiError::http(404, "User not found"));
    };
    let secret = totp::generate_secret().map_err(random_failed)?;
    let enc = encrypt_secret(&state, &secret)?;
    // A repeated "begin" replaces the pending secret; an active row is never
    // touched (the WHERE), even if two requests race.
    let changed = sqlx::query(
        "INSERT INTO user_mfa (user_id, tenant_id, secret_enc, status, last_used_step, created_at, confirmed_at)
         VALUES ($1, $2, $3, 'pending', NULL, NOW(), NULL)
         ON CONFLICT (user_id) DO UPDATE
            SET secret_enc = EXCLUDED.secret_enc, status = 'pending', last_used_step = NULL,
                created_at = NOW(), confirmed_at = NULL
          WHERE user_mfa.status <> 'active'",
    )
    .bind(&actor.user_id)
    .bind(&actor.tenant_id)
    .bind(&enc)
    .execute(&state.pool)
    .await?
    .rows_affected();
    if changed == 0 {
        return Err(ApiError::app("mfa_already_enrolled", "Two-step sign-in is already on for this account.", 409, json!({})));
    }
    Ok(ok(json!({
        "secret": secret,
        "otpauth_uri": totp::otpauth_uri(ISSUER, &email, &secret),
        "issuer": ISSUER,
        "account": email,
        "digits": totp::DIGITS,
        "period": totp::PERIOD_SECONDS,
    })))
}

// ── POST /mfa/enroll/confirm ─────────────────────────────────────────────────

async fn enroll_confirm(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, headers: HeaderMap, bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let obj = read_object(&headers, &bytes)?;
    let code = code_of(&obj)?;
    // The enrollment token spends one of its five guesses on every call.
    let actor = resolve_actor(&state, &headers, &actors, &obj, true).await?;
    check_rate(&state, &format!("mfa:{}", actor.user_id)).await?;

    let Some(row) = mfa_row(&state, &actor.user_id).await? else {
        return Err(ApiError::app("mfa_enrollment_not_started", "Start the enrollment first.", 409, json!({})));
    };
    if row.status != "pending" {
        return Err(ApiError::app("mfa_already_enrolled", "Two-step sign-in is already on for this account.", 409, json!({})));
    }
    let secret = decrypt_secret(&state, &row.secret_enc)?;
    let now = chrono::Utc::now().timestamp();
    let Some(step) = totp::matching_step(&secret, &code, now, None, totp::DIGITS) else {
        return Err(bad_code());
    };
    let codes = totp::generate_recovery_codes().map_err(random_failed)?;

    let mut tx = state.pool.begin().await?;
    // Compare-and-set on the status: two confirms cannot both activate and
    // each hand out a different set of recovery codes.
    let activated = sqlx::query(
        "UPDATE user_mfa SET status = 'active', confirmed_at = NOW(), last_used_step = $1
          WHERE user_id = $2 AND status = 'pending'",
    )
    .bind(step)
    .bind(&actor.user_id)
    .execute(&mut *tx)
    .await?
    .rows_affected();
    if activated != 1 {
        return Err(ApiError::app("mfa_already_enrolled", "Two-step sign-in is already on for this account.", 409, json!({})));
    }
    insert_recovery_codes(&mut tx, &state, &actor.user_id, &actor.tenant_id, &codes).await?;
    if let Some(hash) = &actor.token_hash {
        sqlx::query("UPDATE mfa_challenges SET consumed_at = NOW() WHERE token_hash = $1 AND consumed_at IS NULL")
            .bind(hash)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;

    record_event(&state.pool, &actor.tenant_id, &actor.user_id, Event::MfaEnrolled, Some(&actor.user_id), Map::new()).await;
    Ok(ok(json!({
        "enrolled": true,
        "recovery_codes": codes,
        // An enrollment token is not a session: the person signs in again.
        "sign_in_again": actor.session.is_none(),
    })))
}

// ── POST /mfa/disable ────────────────────────────────────────────────────────

async fn disable(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, headers: HeaderMap, bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let obj = read_object(&headers, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let code = code_of(&obj)?;
    let enrolled = mfa_row(&state, &user.user_id).await?.map(|r| r.status == "active").unwrap_or(false);
    if !enrolled {
        return Err(ApiError::app("mfa_not_enrolled", "Two-step sign-in is not on for this account.", 409, json!({})));
    }
    if tenant_requires(&state, &user.tenant_id).await? {
        return Err(ApiError::app(
            "mfa_required_by_tenant",
            "Your organization requires two-step sign-in, so it cannot be turned off.",
            403,
            json!({}),
        ));
    }
    reauth(&state, &user, &code).await?;
    let mut tx = state.pool.begin().await?;
    sqlx::query("DELETE FROM user_mfa_recovery_codes WHERE user_id = $1").bind(&user.user_id).execute(&mut *tx).await?;
    sqlx::query("DELETE FROM user_mfa WHERE user_id = $1").bind(&user.user_id).execute(&mut *tx).await?;
    tx.commit().await?;
    record_event_with_reason(
        &state.pool, &user.tenant_id, &user.user_id, Event::MfaDisabled, Some(&user.user_id), Map::new(),
        Some("mfa_disabled_by_the_user"),
    )
    .await;
    Ok(ok(json!({"enrolled": false})))
}

// ── POST /mfa/recovery-codes/regenerate ──────────────────────────────────────

async fn regenerate_codes(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, headers: HeaderMap, bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let obj = read_object(&headers, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    let code = code_of(&obj)?;
    let enrolled = mfa_row(&state, &user.user_id).await?.map(|r| r.status == "active").unwrap_or(false);
    if !enrolled {
        return Err(ApiError::app("mfa_not_enrolled", "Two-step sign-in is not on for this account.", 409, json!({})));
    }
    reauth(&state, &user, &code).await?;
    let codes = totp::generate_recovery_codes().map_err(random_failed)?;
    let mut tx = state.pool.begin().await?;
    insert_recovery_codes(&mut tx, &state, &user.user_id, &user.tenant_id, &codes).await?;
    tx.commit().await?;
    record_event(
        &state.pool, &user.tenant_id, &user.user_id, Event::MfaRecoveryCodesRegenerated, Some(&user.user_id), Map::new(),
    )
    .await;
    Ok(ok(json!({"recovery_codes": codes})))
}

// ── GET / PUT /mfa/policy ────────────────────────────────────────────────────

async fn get_policy(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_role(&user, &["admin"])?;
    let required = tenant_requires(&state, &user.tenant_id).await?;
    let rows: Vec<(String, String, Option<String>, String, bool)> = sqlx::query_as(
        "SELECT u.id, u.email, u.full_name, u.role,
                EXISTS (SELECT 1 FROM user_mfa m WHERE m.user_id = u.id AND m.status = 'active')
           FROM users u
          WHERE u.tenant_id = $1 AND COALESCE(u.status, 'active') = 'active'
          ORDER BY u.email
          LIMIT 1000",
    )
    .bind(&user.tenant_id)
    .fetch_all(&state.pool)
    .await?;
    let enrolled = rows.iter().filter(|r| r.4).count();
    let users: Vec<Value> = rows
        .into_iter()
        .map(|(id, email, full_name, role, enrolled)| {
            json!({"id": id, "email": email, "full_name": full_name, "role": role, "enrolled": enrolled})
        })
        .collect();
    Ok(ok(json!({
        "required": required,
        "enrolled_users": enrolled,
        "total_users": users.len(),
        "users": users,
    })))
}

async fn put_policy(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, headers: HeaderMap, bytes: Bytes,
) -> Result<Json<Value>, ApiError> {
    let ct = headers.get(axum::http::header::CONTENT_TYPE).and_then(|v| v.to_str().ok());
    let body = validation::read_body(ct, &bytes)?;
    let user = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_role(&user, &["admin"])?;
    let obj = validation::body_object(&body)?;
    let mut errs = Errors::default();
    let f = validation::bool_field(&mut errs, &obj, &[Value::String("body".into())], "required", false);
    let required = match f {
        Field::Value(b) => b,
        _ => {
            if errs.0.is_empty() {
                errs.push("missing", &[Value::String("body".into()), Value::String("required".into())],
                    "Field required".into(), &Value::Object(obj.clone()), None);
            }
            return Err(errs.into_result().unwrap_err());
        }
    };
    errs.into_result()?;

    let was = tenant_requires(&state, &user.tenant_id).await?;
    if required {
        // A policy that locks its own author out helps nobody.
        let admin_enrolled = mfa_row(&state, &user.user_id).await?.map(|r| r.status == "active").unwrap_or(false);
        if !admin_enrolled {
            return Err(ApiError::app(
                "mfa_admin_not_enrolled",
                "Turn on two-step sign-in for your own account before requiring it for everyone.",
                409,
                json!({}),
            ));
        }
    }
    let mut ended_sessions = 0u64;
    if was != required {
        let mut tx = state.pool.begin().await?;
        sqlx::query("UPDATE tenants SET mfa_required = $1 WHERE id = $2")
            .bind(required)
            .bind(&user.tenant_id)
            .execute(&mut *tx)
            .await?;
        if required {
            // Password users who are not enrolled yet lose their sessions, so
            // the requirement binds now and not when their refresh token
            // finally expires; they come back through the enrollment step.
            // People who sign in through an identity provider are exempt.
            let ids: Vec<(String,)> = sqlx::query_as(
                "UPDATE users SET sessions_invalid_before = NOW()
                  WHERE tenant_id = $1 AND has_password = TRUE
                    AND NOT EXISTS (SELECT 1 FROM user_mfa m WHERE m.user_id = users.id AND m.status = 'active')
              RETURNING id",
            )
            .bind(&user.tenant_id)
            .fetch_all(&mut *tx)
            .await?;
            ended_sessions = ids.len() as u64;
            let ids: Vec<String> = ids.into_iter().map(|r| r.0).collect();
            sqlx::query("DELETE FROM refresh_tokens WHERE user_id = ANY($1)").bind(&ids).execute(&mut *tx).await?;
        }
        tx.commit().await?;
        let mut d = Map::new();
        d.insert("mfa_required".into(), Value::Bool(required));
        record_event_with_reason(
            &state.pool, &user.tenant_id, &user.user_id, Event::MfaPolicyChanged, None, d,
            Some("changed_by_an_account_admin"),
        )
        .await;
    }
    audit::record(
        &state, &actors, "PUT", "/mfa/policy", None,
        Note { after: Some(json!({"mfa_required": required})), before: Some(json!({"mfa_required": was})), ..Default::default() },
        200,
    )
    .await;
    Ok(ok(json!({"required": required, "sessions_ended": ended_sessions})))
}

// ── POST /mfa/users/{user_id}/reset ──────────────────────────────────────────

async fn reset_user(
    State(state): State<AppState>, Extension(actors): Extension<RequestActors>, Path(user_id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let admin = auth::current_user(&state, &headers, ROUTE, &actors).await?;
    auth::require_role(&admin, &["admin"])?;
    // Tenant-scoped: another tenant's user is "not found", never "forbidden".
    let Some((email, _)) = user_email(&state, &user_id, &admin.tenant_id).await? else {
        return Err(ApiError::http(404, "User not found"));
    };
    if user_id == admin.user_id {
        return Err(ApiError::app(
            "mfa_reset_self_refused",
            "Use your own recovery code or the disable option to change your own second factor.",
            400,
            json!({}),
        ));
    }
    if mfa_row(&state, &user_id).await?.is_none() {
        return Err(ApiError::app("mfa_not_enrolled", "Two-step sign-in is not on for this account.", 409, json!({})));
    }
    let mut tx = state.pool.begin().await?;
    sqlx::query("DELETE FROM user_mfa_recovery_codes WHERE user_id = $1").bind(&user_id).execute(&mut *tx).await?;
    sqlx::query("DELETE FROM user_mfa WHERE user_id = $1").bind(&user_id).execute(&mut *tx).await?;
    // A reset is for a lost device. Whatever session is open under that
    // account may belong to whoever holds it, so it ends too.
    sqlx::query("DELETE FROM refresh_tokens WHERE user_id = $1").bind(&user_id).execute(&mut *tx).await?;
    sqlx::query("UPDATE users SET sessions_invalid_before = NOW() WHERE id = $1 AND tenant_id = $2")
        .bind(&user_id)
        .bind(&admin.tenant_id)
        .execute(&mut *tx)
        .await?;
    sqlx::query("DELETE FROM mfa_challenges WHERE user_id = $1").bind(&user_id).execute(&mut *tx).await?;
    tx.commit().await?;

    let mut d = Map::new();
    d.insert("email".into(), Value::String(email.clone()));
    record_event_with_reason(
        &state.pool, &admin.tenant_id, &admin.user_id, Event::MfaReset, Some(&user_id), d,
        Some("mfa_reset_by_an_account_admin"),
    )
    .await;
    audit::record(
        &state, &actors, "POST", "/mfa/users/{user_id}/reset", Some(&user_id),
        Note { label: Some(email), before: Some(json!({"mfa": "enrolled"})), after: Some(json!({"mfa": "reset"})), ..Default::default() },
        200,
    )
    .await;
    Ok(ok(json!({"reset": user_id})))
}
