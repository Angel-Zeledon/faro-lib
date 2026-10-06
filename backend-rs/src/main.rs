//! StockAI HTTP API, Rust side of the strangler-fig migration.
//!
//! Serves only the routes listed in `routes::router()`. Everything else stays
//! on the Python FastAPI app; the reverse proxy decides which service gets a
//! request (docs/rust-migration.md). Both read the same environment and the
//! same Postgres, so a route can move back to Python by changing the proxy
//! alone.

mod activity;
mod audit;
mod auth;
mod config;
mod entitlements;
mod error;
mod inventory;
mod fulfillment;
mod ip_allowlist;
mod fx;
mod fx_eval;
mod limits;
mod middleware;
mod outbox;
mod pycompat;
mod pyjson;
mod query;
mod routes;
mod service_config;
mod state;
mod validation;
mod webhook_events;

use std::sync::Arc;
use std::time::Duration;

use axum::http::{HeaderValue, Method};
use sqlx::postgres::PgPoolOptions;
use tower_http::cors::{AllowHeaders, AllowOrigin, CorsLayer};
use tracing_subscriber::EnvFilter;

use crate::config::Settings;
use crate::state::AppState;

/// `stockai-api healthcheck`: GET /health on the local bind address and exit
/// 0 on a 200. The runtime image is distroless (no shell, no curl), so the
/// container healthcheck is the binary itself.
async fn healthcheck() -> i32 {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let bind = std::env::var("RUST_API_BIND").unwrap_or_else(|_| "127.0.0.1:8021".into());
    let port = bind.rsplit(':').next().unwrap_or("8021");
    let addr = format!("127.0.0.1:{port}");
    let attempt = async {
        let mut s = tokio::net::TcpStream::connect(&addr).await.ok()?;
        s.write_all(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n").await.ok()?;
        let mut buf = vec![0u8; 64];
        let n = s.read(&mut buf).await.ok()?;
        Some(String::from_utf8_lossy(&buf[..n]).contains(" 200 "))
    };
    match tokio::time::timeout(Duration::from_secs(4), attempt).await {
        Ok(Some(true)) => 0,
        _ => 1,
    }
}

#[tokio::main]
async fn main() {
    if std::env::args().nth(1).as_deref() == Some("healthcheck") {
        std::process::exit(healthcheck().await);
    }
    // Pure arithmetic filter for the differential test against the Python
    // reference; touches no database and no network.
    if std::env::args().nth(1).as_deref() == Some("fx-eval") {
        std::process::exit(fx_eval::main());
    }
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_env("RUST_LOG").unwrap_or_else(|_| EnvFilter::new("info")))
        .with_target(true)
        .init();

    let settings = match Settings::load() {
        Ok(s) => s,
        Err(e) => {
            eprintln!("configuration error: {e}");
            std::process::exit(2);
        }
    };
    tracing::info!(version = %settings.app_version, environment = %settings.environment,
        testing_mode = settings.testing_mode, "starting stockai-api (rust)");

    // Lazy: like the Python app, the process boots without a database and
    // /health reports `degraded` instead of the service refusing to start.
    // The 10 s acquire timeout is the Python pool's wait before PoolExhausted.
    let pool = match PgPoolOptions::new()
        .max_connections(settings.db_max_connections)
        .acquire_timeout(Duration::from_secs(10))
        .connect_lazy(&settings.database_url)
    {
        Ok(p) => p,
        Err(e) => {
            eprintln!("DATABASE_URL is not usable: {e}");
            std::process::exit(2);
        }
    };

    let origins: Vec<HeaderValue> = settings
        .cors_origins()
        .into_iter()
        .filter_map(|o| HeaderValue::from_str(&o).ok())
        .collect();
    let cors = CorsLayer::new()
        .allow_origin(AllowOrigin::list(origins))
        .allow_credentials(true)
        .allow_methods([Method::GET, Method::POST, Method::PUT, Method::PATCH, Method::DELETE,
                        Method::OPTIONS, Method::HEAD])
        .allow_headers(AllowHeaders::mirror_request());

    let bind = settings.bind.clone();
    let state = AppState { settings: Arc::new(settings), pool };
    let app = routes::router()
        .layer(cors)
        .layer(axum::middleware::from_fn_with_state(state.clone(), middleware::request_context))
        .layer(axum::middleware::from_fn(middleware::reject_nul_in_path))
        .with_state(state);

    let listener = match tokio::net::TcpListener::bind(&bind).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("cannot bind {bind}: {e}");
            std::process::exit(2);
        }
    };
    tracing::info!("listening on {bind}");
    let shutdown = async {
        let _ = tokio::signal::ctrl_c().await;
        tracing::info!("shutting down");
    };
    if let Err(e) = axum::serve(listener, app.into_make_service_with_connect_info::<std::net::SocketAddr>())
        .with_graceful_shutdown(shutdown).await {
        tracing::error!(error = %e, "server error");
    }
}
