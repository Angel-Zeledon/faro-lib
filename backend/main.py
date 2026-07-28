"""
ForecastPlatform Backend — Enterprise SaaS API

Architecture:
    Frontend → backend/ (FastAPI, multi-tenant, auth, sessions, jobs)
             → forecasting_core/ (ML library — all intelligence lives here)

Start:
    uvicorn backend.main:app --reload --port 8001

Swagger docs:
    http://localhost:8001/docs
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.v1 import alerts as alerts_router, auth, sessions, datasets, datasources, configuration, training, forecasts, artifacts, reports, analyst, chats, users, preferences, activity, models as models_router, documents, api_keys, webhooks, schedule, inventory as inventory_router, ai_insights, demo, entitlements, tenant_data, integrations as integrations_router, planning as planning_router, whatsapp as whatsapp_router, scenarios as scenarios_router, freshness as freshness_router
from backend.errors import AppError
from backend.api.ws.training_progress import router as ws_router
from backend.config import settings
from backend.middleware.request_logger import RequestLoggerMiddleware
from backend.middleware.tenant_context import TenantContextMiddleware
from backend.workers import worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


def _recover_running_jobs() -> None:
    """On startup, mark any jobs left in RUNNING state as FAILED (orphaned by unclean shutdown)."""
    from backend.db.connection import query, execute
    from backend.sessions.service import force_status

    stuck = query(
        "SELECT id, tenant_id, session_id FROM jobs WHERE status = 'RUNNING'"
    )
    for job in stuck:
        execute(
            "UPDATE jobs SET status = 'FAILED', completed_at = NOW(), error = %s WHERE id = %s",
            ("Server restarted — job aborted", job["id"]),
        )
        try:
            force_status(job["tenant_id"], job["session_id"], "FAILED")
        except Exception:
            pass
        log.warning(f"Recovered stuck job {job['id']} for session {job['session_id']} → FAILED")

    if stuck:
        log.info(f"Recovered {len(stuck)} stuck RUNNING job(s) on startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    log.info(f"Starting ForecastPlatform v{settings.app_version}")

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set — add it to Backend/.env")

    from backend.db.connection import init_pool
    try:
        # min_conn=5 pre-warms 5 connections at startup so that concurrent requests
        # don't serialize behind the TCP+TLS handshake (3s each on Supabase us-west-2).
        # ThreadedConnectionPool holds its mutex during psycopg2.connect(), so without
        # pre-warmed connections every concurrent request serializes.
        init_pool(settings.database_url, min_conn=5, max_conn=20)
        log.info("Database connection pool initialized (5 warm connections)")
    except Exception as exc:
        log.error("DB pool init failed — server will start but DB calls will fail: %s", exc)

    # Ensure upload directory exists for binary dataset files
    from pathlib import Path
    _storage = Path("storage")
    (_storage / "datasets").mkdir(parents=True, exist_ok=True)
    (_storage / "artifacts").mkdir(parents=True, exist_ok=True)
    (_storage / "documents").mkdir(parents=True, exist_ok=True)
    log.info(f"Storage initialized at: {_storage.resolve()}")

    # Pre-create blocklist table so is_revoked() never pays DDL cost at auth time
    try:
        from backend.auth.blocklist import ensure_table
        ensure_table()
        log.info("Token blocklist table ready")
    except Exception as exc:
        log.warning("Could not pre-create blocklist table: %s", exc)

    try:
        from backend.preferences.service import ensure_table as ensure_prefs
        from backend.activity.service import ensure_table as ensure_activity
        from backend.notifications.alert_history import ensure_index as ensure_alert_index
        ensure_prefs()
        ensure_activity()
        # The alert bell reads activity_logs tenant-wide; the table only ships a
        # per-user index. Created after ensure_activity() so the table exists.
        ensure_alert_index()
        log.info("User preferences and activity log tables ready")
    except Exception as exc:
        log.warning("Could not pre-create preferences/activity tables: %s", exc)

    # A failed migration means the schema is half-built. Booting anyway hands
    # every request a database that does not match the code — in production that
    # is worse than not starting at all, so we refuse to boot (same fail-fast
    # stance as TESTING_MODE in prod). In development we log loudly and continue,
    # so a local schema hiccup does not block the whole app.
    try:
        from backend.db.migrations import run_all as run_migrations
        run_migrations()
        log.info("Schema migrations applied")
    except Exception as exc:
        if settings.environment.strip().lower() in ("production", "prod"):
            log.error("Schema migrations failed — refusing to boot: %s", exc)
            raise
        log.error(
            "Schema migrations FAILED: %s — continuing because ENVIRONMENT=%s, "
            "but the schema is not what the code expects.",
            exc, settings.environment,
        )

    _recover_running_jobs()

    worker.start()
    log.info(f"Job worker started (max_concurrent={settings.max_concurrent_jobs})")

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    log.info("Shutting down")


app = FastAPI(
    title="ForecastPlatform API",
    description=(
        "Multi-tenant SaaS forecasting platform. "
        "All ML logic lives in forecasting_core — this API is pure orchestration."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ─────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TenantContextMiddleware)
app.add_middleware(RequestLoggerMiddleware)

# ── Error envelope ─────────────────────────────────────────────────────────
# A user-facing AppError becomes a JSON error response that keeps the existing
# `detail` contract (the English fallback message, so old clients and FastAPI's
# own error shape are unaffected) and ADDS `error_code` + `error_params`. The
# frontend renders `errors.<error_code>` (localized, interpolating params) when
# present and falls back to `detail` otherwise. See backend/errors.py.


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.message,
            "error_code": exc.code,
            "error_params": exc.params,
        },
    )

# ── Routers ────────────────────────────────────────────────────────────────

_PREFIX = "/api/v1"

app.include_router(auth.router,          prefix=_PREFIX)
app.include_router(users.router,         prefix=_PREFIX)
app.include_router(sessions.router,      prefix=_PREFIX)
app.include_router(datasets.router,      prefix=_PREFIX)
app.include_router(datasources.router,   prefix=_PREFIX)
app.include_router(configuration.router, prefix=_PREFIX)
app.include_router(training.router,      prefix=_PREFIX)
app.include_router(planning_router.router, prefix=_PREFIX)
app.include_router(forecasts.router,     prefix=_PREFIX)
app.include_router(artifacts.router,     prefix=_PREFIX)
app.include_router(reports.router,       prefix=_PREFIX)
app.include_router(analyst.router,         prefix=_PREFIX)
app.include_router(chats.router,           prefix=_PREFIX)
app.include_router(preferences.router,     prefix=_PREFIX)
app.include_router(activity.router,        prefix=_PREFIX)
app.include_router(models_router.router,   prefix=_PREFIX)
app.include_router(documents.router,       prefix=_PREFIX)
app.include_router(api_keys.router,        prefix=_PREFIX)
app.include_router(webhooks.router,        prefix=_PREFIX)
app.include_router(schedule.router,        prefix=_PREFIX)
app.include_router(inventory_router.router, prefix=_PREFIX)
app.include_router(scenarios_router.router, prefix=_PREFIX)
app.include_router(ai_insights.router,     prefix=_PREFIX)
app.include_router(demo.router,            prefix=_PREFIX)
app.include_router(entitlements.router,    prefix=_PREFIX)
app.include_router(tenant_data.router,     prefix=_PREFIX)
app.include_router(integrations_router.router, prefix=_PREFIX)
app.include_router(whatsapp_router.router, prefix=_PREFIX)
app.include_router(freshness_router.router, prefix=_PREFIX)
app.include_router(alerts_router.router,    prefix=_PREFIX)
app.include_router(ws_router)


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
def health():
    from backend.db.connection import query_one
    row = query_one(
        "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('RUNNING', 'QUEUED')"
    )
    return {
        "status": "ok",
        "version": settings.app_version,
        "queued_jobs": row["n"] if row else 0,
    }
