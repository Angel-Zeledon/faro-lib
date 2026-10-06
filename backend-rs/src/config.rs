//! Configuration, read from the SAME variables as the Python app.
//!
//! `backend/config.py` is a pydantic-settings model: it reads `backend/.env`
//! and lets real environment variables win over the file, matching names
//! case-insensitively. This module does exactly that, so both services can be
//! started from one `env_file` in compose and can never disagree about the
//! JWT secret or the database.
//!
//! Only the fields the Rust routes read are typed here. Everything else stays
//! available, raw, through [`Settings::raw`], which the health report uses to
//! evaluate the service registry without a second copy of every default.

use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Settings {
    /// Every variable, upper-cased name -> raw value. `.env` first, then the
    /// process environment on top (pydantic-settings precedence).
    raw: HashMap<String, String>,
    pub secret_key: String,
    pub database_url: String,
    pub frontend_url: String,
    pub environment: String,
    pub app_version: String,
    pub testing_mode: bool,
    pub storage_path: PathBuf,
    pub integrations_secret_key: String,
    /// Rust-only: where this service listens.
    pub bind: String,
    /// Rust-only: pool size. The Python pool is 20; the two share one Postgres.
    pub db_max_connections: u32,
    /// `TRUSTED_PROXY_HOPS` (same variable and meaning as Python): how many
    /// reverse proxies append to `X-Forwarded-For` in front of this service.
    pub trusted_proxy_hops: u32,
}

#[derive(Debug)]
pub struct ConfigError(pub String);

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// pydantic's lax boolean parsing, which is what `TESTING_MODE=yes` goes
/// through on the Python side. Anything else is a boot error there too.
pub fn parse_bool(raw: &str) -> Option<bool> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "1" | "on" | "t" | "true" | "y" | "yes" => Some(true),
        "0" | "off" | "f" | "false" | "n" | "no" => Some(false),
        _ => None,
    }
}

impl Settings {
    /// Load from `ENV_FILE` (default `backend/.env` when it exists) plus the
    /// process environment.
    pub fn load() -> Result<Self, ConfigError> {
        let mut raw: HashMap<String, String> = HashMap::new();
        let env_file = std::env::var("ENV_FILE").unwrap_or_else(|_| "backend/.env".to_string());
        let path = PathBuf::from(&env_file);
        if path.exists() {
            let iter = dotenvy::from_path_iter(&path)
                .map_err(|e| ConfigError(format!("cannot read {env_file}: {e}")))?;
            for item in iter {
                let (k, v) = item.map_err(|e| ConfigError(format!("bad line in {env_file}: {e}")))?;
                raw.insert(k.to_ascii_uppercase(), v);
            }
        }
        for (k, v) in std::env::vars() {
            raw.insert(k.to_ascii_uppercase(), v);
        }
        Self::from_map(raw)
    }

    pub fn from_map(raw: HashMap<String, String>) -> Result<Self, ConfigError> {
        let required = |name: &str| -> Result<String, ConfigError> {
            raw.get(name)
                .cloned()
                .ok_or_else(|| ConfigError(format!("{name} is required (same rule as backend/config.py)")))
        };
        let secret_key = required("SECRET_KEY")?;
        let database_url = required("DATABASE_URL")?;
        let frontend_url = required("FRONTEND_URL")?;
        let environment = raw.get("ENVIRONMENT").cloned().unwrap_or_else(|| "development".into());
        let app_version = raw.get("APP_VERSION").cloned().unwrap_or_else(|| "1.0.0".into());
        let testing_mode = match raw.get("TESTING_MODE") {
            None => false,
            Some(v) => parse_bool(v)
                .ok_or_else(|| ConfigError(format!("TESTING_MODE is not a boolean: {v:?}")))?,
        };
        let env_lower = environment.trim().to_ascii_lowercase();
        if testing_mode && (env_lower == "production" || env_lower == "prod") {
            return Err(ConfigError(
                "TESTING_MODE=true is not allowed when ENVIRONMENT=production. \
                 Unset TESTING_MODE (or change ENVIRONMENT) and restart."
                    .into(),
            ));
        }
        let storage_path = raw
            .get("STORAGE_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("backend/storage"));
        let integrations_secret_key = raw.get("INTEGRATIONS_SECRET_KEY").cloned().unwrap_or_default();
        let bind = raw.get("RUST_API_BIND").cloned().unwrap_or_else(|| "127.0.0.1:8021".into());
        let db_max_connections = raw
            .get("RUST_API_DB_MAX_CONNECTIONS")
            .and_then(|v| v.parse().ok())
            .unwrap_or(10);
        let trusted_proxy_hops = match raw.get("TRUSTED_PROXY_HOPS").map(|v| v.trim()) {
            None | Some("") => 0,
            Some(v) => v
                .parse::<u32>()
                .map_err(|_| ConfigError(format!("TRUSTED_PROXY_HOPS is not a non-negative integer: {v:?}")))?,
        };
        Ok(Settings {
            raw,
            secret_key,
            database_url,
            frontend_url,
            environment,
            app_version,
            testing_mode,
            storage_path,
            integrations_secret_key,
            bind,
            db_max_connections,
            trusted_proxy_hops,
        })
    }

    /// The raw value of a variable by its environment name, if set at all.
    pub fn raw(&self, env_name: &str) -> Option<&str> {
        self.raw.get(&env_name.to_ascii_uppercase()).map(String::as_str)
    }

    /// The allowed CORS origins, the same list `backend/main.py` passes to
    /// `CORSMiddleware`.
    pub fn cors_origins(&self) -> Vec<String> {
        vec![
            self.frontend_url.clone(),
            "http://localhost:5000".into(),
            "http://localhost:3000".into(),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> HashMap<String, String> {
        let mut m = HashMap::new();
        m.insert("SECRET_KEY".into(), "s".into());
        m.insert("DATABASE_URL".into(), "postgres://x".into());
        m.insert("FRONTEND_URL".into(), "http://f".into());
        m
    }

    #[test]
    fn missing_required_field_is_a_boot_error() {
        let mut m = base();
        m.remove("SECRET_KEY");
        assert!(Settings::from_map(m).is_err());
    }

    #[test]
    fn testing_mode_is_refused_in_production() {
        let mut m = base();
        m.insert("TESTING_MODE".into(), "true".into());
        m.insert("ENVIRONMENT".into(), " Production ".into());
        let err = Settings::from_map(m).unwrap_err();
        assert!(err.0.contains("TESTING_MODE=true is not allowed"));
    }

    #[test]
    fn booleans_parse_like_pydantic() {
        assert_eq!(parse_bool("YES"), Some(true));
        assert_eq!(parse_bool("off"), Some(false));
        assert_eq!(parse_bool("maybe"), None);
    }

    #[test]
    fn defaults_match_the_python_settings() {
        let s = Settings::from_map(base()).unwrap();
        assert_eq!(s.environment, "development");
        assert_eq!(s.app_version, "1.0.0");
        assert!(!s.testing_mode);
    }
}
