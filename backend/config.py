from pathlib import Path
import json
from typing import Annotated, List
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

# How long a 6-digit OTP (password reset, password change) stays valid.
# Lives here because THREE places need the same number and they used to each
# keep their own: the two issuers agreed on 15 minutes while the email and the
# UI both announced "30 horas", so a user who believed the app came back to a
# dead code. Whoever states the duration must read it from here.
OTP_EXPIRE_MINUTES = 15
# The account-setup / email-verification LINK is a different, deliberately long
# window (users.py mints it with expires_minutes=60 * 30) — an invite has to
# survive a weekend in a spam folder.
SETUP_LINK_EXPIRE_HOURS = 30


class Settings(BaseSettings):
    secret_key: str
    database_url: str
    frontend_url: str

    # App metadata
    app_name: str = "ForecastPlatform"
    app_version: str = "1.0.0"

    # Deployment environment: development | staging | production
    environment: str = "development"

    # JWT
    access_token_expire_minutes: int = 15
    algorithm: str = "HS256"

    # CORS
    allowed_origins: Annotated[List[str], NoDecode] = ["http://localhost:3000", "http://localhost:4000","http://localhost:5000"]

    # Who may see and edit the INSTANCE-wide service configuration (the panel at
    # /instalacion). `admin` is a role INSIDE a tenant, so it cannot be the
    # answer here: every tenant that signs up gets one, and an instance-wide
    # DeepSeek key or Twilio credential does not belong to any single tenant.
    #
    # Environment-only, on purpose: the list of people who can rewrite the
    # deployment's credentials must not be editable from the screen those
    # credentials are pasted into.
    #
    # Empty means NOBODY edits instance configuration from the app and the panel
    # says so, naming this variable. It is the safe default for a multi-tenant
    # deployment; a buyer running their own single-tenant instance puts their
    # own address here and gets the panel.
    instance_admin_emails: Annotated[List[str], NoDecode] = []

    @field_validator("allowed_origins", "instance_admin_emails", mode="before")
    @classmethod
    def _list_from_env(cls, value):
        """Accept a JSON list OR a comma-separated string.

        `.env.example` documents `INSTANCE_ADMIN_EMAILS=you@example.com`, and
        pydantic-settings only decodes JSON for a list field: the documented
        form made the API crash at import on a production deploy.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            return json.loads(text)
        return [part.strip() for part in text.split(",") if part.strip()]

    # Storage
    storage_path: Path = BASE_DIR / "storage"

    # Worker
    max_concurrent_jobs: int = 2
    worker_poll_interval_seconds: float = 2.0
    # Deployment topology. Both default True so a bare `uvicorn backend.main:app`
    # keeps behaving like the single-process dev setup. In a split deployment the
    # API container sets both to false and a dedicated worker container
    # (`python -m backend.workers`) runs the loops instead.
    #   worker_enabled    — the job-claim/training loop
    #   scheduler_enabled — the cron loops (scheduled jobs, daily alerts,
    #                       integration sync, monthly snapshot). Must be true in
    #                       EXACTLY ONE instance or daily emails go out twice.
    worker_enabled: bool = True
    scheduler_enabled: bool = True
    # Serve ONLY the endpoints in `backend/api/public_surface.py` — the seven a
    # customer's own system is invited to call. Off by default, so a plain
    # `uvicorn backend.main:app` is the whole product exactly as before.
    #
    # This is what lets the public API live on its own infrastructure without a
    # second codebase: same image, `PUBLIC_API_ONLY=true`, worker and scheduler
    # off. What it buys is that the promise stops being a list somebody has to
    # respect and becomes a wall — an integration cannot reach an internal route
    # on that host even by guessing, and a UI-shaped endpoint cannot acquire a
    # user by accident.
    #
    # What it does NOT buy, and nobody should assume it does: isolation from the
    # database. Both instances still share one Postgres, so a database problem
    # takes down the customer's integration and the app together. Splitting that
    # is a different, much larger decision.
    public_api_only: bool = False
    # Identity used to claim jobs and to recover this instance's orphans after a
    # crash. Empty falls back to the container/host name. Give each long-lived
    # worker a FIXED id (e.g. "worker-1") so its orphaned RUNNING jobs are still
    # recognized after the container is recreated.
    worker_id: str = ""

    # Operations surface (`GET /service-config/ops`) — the thresholds at which a
    # reading turns "degraded". Declared in the registry's `operations` service.
    ops_queue_wait_degraded_minutes: float = 10.0
    ops_running_job_degraded_minutes: float = 180.0
    ops_worker_heartbeat_stale_seconds: float = 120.0
    ops_disk_free_min_percent: float = 10.0
    ops_backup_max_age_hours: float = 36.0
    ops_pool_saturation_percent: float = 85.0
    ops_latency_slo_ms: float = 3000.0
    ops_slow_query_ms: float = 1000.0
    # Where the nightly backup script drops its success marker, and the folder
    # it writes into. Empty = not wired: the surface says "unknown", not "ok".
    backup_status_path: str = ""
    backup_dir: str = ""

    # Upload
    max_upload_size_mb: int = 200

    # In-app dataset editor size guard — checked from stored row_count/size_bytes
    # BEFORE reading the file, so a huge file is never loaded into memory.
    dataset_editor_max_rows: int = 50_000
    dataset_editor_max_mb: int = 10

    # ── Testing mode ────────────────────────────────────────────────────────
    # When True, ALL commercial/business restrictions are bypassed: plan quotas,
    # rate limits, concurrent-job caps, upload-size caps and length caps. Intended
    # ONLY for load/stress/functional testing. Default False so it can never be on
    # in production by accident — flip it with TESTING_MODE=true in the env.
    testing_mode: bool = False

    # DeepSeek — the ONLY LLM backend. Its API is OpenAI-shaped
    # (`POST {base}/chat/completions`, bearer auth), so it needs no SDK:
    # `backend/ai/local_llm.py` speaks it over httpx.
    #
    # With no key, AI features raise `LLMNotConfigured` at the call site rather
    # than falling back to another provider. Every consumer already degrades to
    # its rule-based text on an exception, so nothing breaks — but the log names
    # the real problem instead of hiding it behind a working-but-wrong answer.
    #   deepseek_model — 'deepseek-chat' is the cheap general model.
    #                    'deepseek-reasoner' costs more and returns its
    #                    reasoning separately; the factory handles both.
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # How a tenant reaches us to ask for more room. There is no checkout: the
    # free tier's ceilings are lifted by a conversation, so these three are the
    # entire commercial surface of the product.
    #   contact_whatsapp — E.164 without '+', the way wa.me wants it ("50688887777")
    #   contact_email    — the address the "write to us" button opens
    #   upgrade_notify_email — where an in-app upgrade request is emailed;
    #                          falls back to contact_email when empty.
    # All empty by default: a button that opens an empty wa.me link is worse
    # than no button, so the UI hides the channels it has no address for.
    contact_whatsapp: str = ""
    contact_email: str = ""
    upgrade_notify_email: str = ""

    # Email — Resend is the primary transport when its key is set; SMTP is the
    # fallback. With neither configured, emails are logged but not sent.
    resend_api_key: str = ""
    email_from: str = "StockAI <onboarding@resend.dev>"  # resend.dev works without domain setup

    # SMTP (fallback transport)
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""

    # WhatsApp alerts via Twilio (optional channel for the daily inventory alert)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""  # e.g. "whatsapp:+14155238886" (Twilio sandbox)
    twilio_sms_from: str = ""       # plain E.164 sender, e.g. "+14155238886" — SMS cannot reuse the whatsapp: sender
    # Row ceiling when snapshotting a SQL query into a CSV dataset. Exceeding it
    # is a refusal, never a silent truncation.
    sql_materialize_max_rows: int = 500_000
    # Relative worsening of a session's realised WAPE over its training-time WAPE
    # at which the one in-app "forecast is degrading" alert is raised (percent).
    accuracy_degradation_threshold_pct: float = 25.0
    # Scheduled retrains in 'reforecast' mode re-forecast from the stored models
    # while the last FULL refit is younger than this many days, and refit
    # otherwise. 7 is the owner's default (docs/retraining-and-enterprise.md).
    reforecast_full_refit_days: int = 7
    # Public external base URL Twilio POSTs the inbound webhook to (scheme + host,
    # e.g. "https://app.stockai.com"). Twilio computes X-Twilio-Signature over the
    # PUBLIC url; behind the frontend proxy / TLS termination the backend sees an
    # internal url (request.url) that will NOT match, so signature validation
    # would always 403. When set, this is the authoritative base for rebuilding
    # the signed url; empty falls back to X-Forwarded-* headers, then request.url.
    whatsapp_webhook_base_url: str = ""
    # Temporary stopgap: when true, the conversational bot skips the LLM and
    # replies with a fast, honest generic message (confirmations still execute
    # deterministically). Set while no hosted LLM is funded — the local model is
    # too slow for a real-time WhatsApp turn. Flip back to false once
    # DEEPSEEK_API_KEY has credit and the smart bot returns automatically.
    whatsapp_bot_generic_mode: bool = False

    # External APIs
    # The LLM settings live further up, beside the rest of the AI config; there
    # is exactly one backend (DeepSeek) and `backend/ai/local_llm.py` is the
    # only place that reads them. `anthropic_api_key` / `local_llm_*` were
    # removed on 2026-08-23: a fallback chain meant a missing DeepSeek key
    # silently answered from somewhere else, which is the failure mode that
    # costs the most to notice.
    voyageai_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_environment: str = ""
    pinecone_index: str = ""

    # Social sign-in (Google, Microsoft, Apple). OFF unless the instance operator
    # turns the master switch on AND fills a provider's credentials — a source
    # install shows exactly the email + password form it always did. Read only
    # through `service_config.resolver.effective()`; see backend/auth/social/.
    social_login_enabled: bool = False
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    apple_oauth_service_id: str = ""
    apple_oauth_team_id: str = ""
    apple_oauth_key_id: str = ""
    apple_oauth_private_key: str = ""

    # Sales by e-mail (backend/inbound_email/). OFF until BOTH are set: the domain
    # the per-tenant addresses live on, and the shared secret the mail provider's
    # webhook is authenticated with. Environment only (see the registry).
    inbound_email_domain: str = ""
    inbound_email_secret: str = ""
    # Enterprise single sign-on (OpenID Connect, one provider per tenant). OFF
    # unless the instance operator turns it on, exactly like social login: a
    # source install shows only email + password. Each tenant's own provider
    # settings live in `sso_providers`, not here — see backend/auth/sso/.
    enterprise_sso_enabled: bool = False

    # Fernet key for every secret `/instalacion` stores. The name is
    # historical — renaming it would orphan every deployment's stored secrets.
    integrations_secret_key: str = ""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _refuse_testing_mode_in_production(self):
        # testing_mode disables quotas, rate limits and upload caps. Refusing to
        # boot beats silently running an unmetered production instance.
        if self.testing_mode and self.environment.strip().lower() in ("production", "prod"):
            raise RuntimeError(
                "TESTING_MODE=true is not allowed when ENVIRONMENT=production. "
                "Unset TESTING_MODE (or change ENVIRONMENT) and restart."
            )
        return self


settings = Settings()