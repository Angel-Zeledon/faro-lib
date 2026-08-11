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
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.v1 import alerts as alerts_router, auth, sessions, datasets, datasources, configuration, training, forecasts, artifacts, reports, analyst, chats, users, preferences, activity, models as models_router, documents, api_keys, webhooks, schedule, inventory as inventory_router, ai_insights, demo, entitlements, tenant_data, integrations as integrations_router, planning as planning_router, whatsapp as whatsapp_router, scenarios as scenarios_router, freshness as freshness_router, messages as messages_router
from backend.errors import AppError
from backend.api.ws.training_progress import router as ws_router
from backend.config import settings
from backend.middleware.machine_audit import MachineAuditMiddleware
from backend.middleware.request_logger import RequestLoggerMiddleware
from backend.middleware.tenant_context import TenantContextMiddleware
from backend.workers import worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    log.info(f"Starting ForecastPlatform v{settings.app_version}")

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set — add it to Backend/.env")

    from backend.db.connection import init_pool, pool_is_initialized
    try:
        # min_conn=5 pre-warms 5 connections at startup so that concurrent requests
        # don't serialize behind the TCP+TLS handshake (3s each on Supabase us-west-2).
        # ThreadedConnectionPool holds its mutex during psycopg2.connect(), so without
        # pre-warmed connections every concurrent request serializes.
        #
        # Guarded because init_pool REPLACES the global: a process that already
        # opened one (the standalone worker entrypoint, the test session) would
        # otherwise orphan it here, leaking its live connections for the life of
        # the process. Same check workers/__main__.py already makes.
        if pool_is_initialized():
            log.info("Database connection pool already open in this process — reusing it")
        else:
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

    # Orphan recovery moved into worker.start(): it belongs to the instance
    # that RAN the jobs, so an API redeploy cannot fail a separate worker's
    # live trainings. Which loops start here is governed by WORKER_ENABLED /
    # SCHEDULER_ENABLED — both default true, preserving single-process dev.
    worker.start()
    log.info(f"Worker components: {worker.enabled_components() or 'none (API-only instance)'}")

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
    # Swagger and ReDoc describe every route this service has — 246 of them,
    # including the internal-shaped ones nobody promises to keep stable. Served
    # unauthenticated in production that is a free map of the whole surface, and
    # it is not the API the product means to expose: that one is the short,
    # supported list in `docs/api-publica.md`.
    #
    # Kept fully open outside production, where they are the fastest way to try a
    # route by hand and there is nothing to protect.
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
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
# Added last so it wraps OUTERMOST: the actor ContextVar is set deep inside, by
# the guard, and this has to still be on the stack when the response comes back
# out to read it.
app.add_middleware(MachineAuditMiddleware)

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


def _json_safe(value):
    """Make a validation-error payload serialisable.

    FastAPI echoes the offending input back inside its 422 body. When that
    input is a bare `NaN` or `Infinity` — which `json.dumps` emits by default,
    so any Python client sends them without trying — Starlette's JSONResponse
    refuses it (`allow_nan=False`) and the 422 turns into an unhandled 500 with
    an empty body. The user is then told the SERVER broke on a request that was
    simply invalid, and CLAUDE.md's own troubleshooting note sends whoever
    debugs it looking at the frontend proxy instead.
    """
    import math as _math

    if isinstance(value, float) and not _math.isfinite(value):
        return str(value)          # "nan" / "inf" — readable, and serialisable
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        return value
    return str(value)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(exc.errors()), "error_code": "validation_error"},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """Last resort: an unexpected failure must still answer in the API's shape.

    Without this every unhandled exception answered `text/plain` /
    "Internal Server Error" — no envelope, no code, nothing the frontend could
    localize, and indistinguishable from the proxy-cannot-reach-the-backend
    symptom the team is trained to look for first. A NUL byte in a path segment
    (`/sessions/%00x/results`) was enough to trigger it on every session route,
    because psycopg2 rejects NUL in a bind parameter.

    The exception itself is logged with its traceback; the client gets a stable
    code and nothing about the internals.
    """
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred.",
            "error_code": "internal_error",
            "error_params": {},
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
from backend.api.v1 import billing as billing_router  # noqa: E402
app.include_router(billing_router.router,  prefix=_PREFIX)
from backend.api.v1 import currency as currency_router  # noqa: E402
app.include_router(currency_router.router, prefix=_PREFIX)
from backend.api.v1 import timezone as timezone_router  # noqa: E402
app.include_router(timezone_router.router, prefix=_PREFIX)
app.include_router(tenant_data.router,     prefix=_PREFIX)
app.include_router(integrations_router.router, prefix=_PREFIX)
app.include_router(whatsapp_router.router, prefix=_PREFIX)
app.include_router(freshness_router.router, prefix=_PREFIX)
app.include_router(alerts_router.router,    prefix=_PREFIX)
app.include_router(messages_router.router,  prefix=_PREFIX)
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


# ── Public-API-only mode ───────────────────────────────────────────────────
#
# Everything above mounts the whole product. When `PUBLIC_API_ONLY` is set this
# strips the app back to the endpoints in `backend/api/public_surface.py` — the
# seven a customer's own system is invited to call — plus /health, which the
# load balancer needs.
#
# Pruning after mounting rather than choosing routers up front is deliberate:
# the public list names individual (method, path) pairs, and those live in
# routers full of internal siblings. Picking routers would let an internal
# neighbour ride along, which is the exact thing this mode exists to prevent.
#
# Nothing about authentication or permissions changes. This is narrower reach,
# not a second security model: an endpoint that was reachable here is reachable
# in the same way, by the same credentials, with the same guards.
if settings.public_api_only:
    from backend.api.public_surface import PUBLIC_ENDPOINTS

    _allowed = {(m, f"{_PREFIX}{p}") for m, p in PUBLIC_ENDPOINTS}

    def _survives(route) -> bool:
        path = getattr(route, "path", None)
        if path in ("/health", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
            return True
        methods = getattr(route, "methods", None)
        if not methods or path is None:
            # Not an HTTP route (mounts, websockets): removed, because this mode
            # is about a small, stated surface and anything unexamined is not it.
            return False
        return any((m.upper(), path) in _allowed for m in methods)

    _before = len(app.router.routes)
    app.router.routes = [r for r in app.router.routes if _survives(r)]
    log.info(
        "PUBLIC_API_ONLY: serving %d of %d routes (%d public endpoints + health)",
        len(app.router.routes), _before, len(PUBLIC_ENDPOINTS),
    )
