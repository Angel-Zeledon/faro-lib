from pathlib import Path
from typing import List
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


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
    allowed_origins: List[str] = ["http://localhost:3000", "http://localhost:4000","http://localhost:5000"]

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
    # Identity used to claim jobs and to recover this instance's orphans after a
    # crash. Empty falls back to the container/host name. Give each long-lived
    # worker a FIXED id (e.g. "worker-1") so its orphaned RUNNING jobs are still
    # recognized after the container is recreated.
    worker_id: str = ""

    # Upload
    max_upload_size_mb: int = 200

    # In-app dataset editor size guard — checked from stored row_count/size_bytes
    # BEFORE reading the file, so a huge file is never loaded into memory.
    dataset_editor_max_rows: int = 50_000
    dataset_editor_max_mb: int = 10

    # ── Billing (Stripe) ────────────────────────────────────────────────────
    # All optional: with no secret key the billing endpoints report that billing
    # is not configured and every other part of the app is unaffected, exactly
    # like RESEND_API_KEY and the notification senders.
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    # Without this, webhook signatures cannot be verified — and an unverified
    # webhook is an open endpoint for raising your own plan, so the handler
    # REFUSES to run rather than trusting the body.
    stripe_webhook_secret: str = ""
    # Price IDs live in configuration, not in code: they differ between test and
    # live mode, and the plan they map to is a commercial decision, not a
    # deployable one. Empty means that plan cannot be bought yet.
    stripe_price_professional_monthly: str = ""
    stripe_price_professional_yearly: str = ""
    # Enterprise is quoted per operation, so there is deliberately no price here.

    @property
    def billing_enabled(self) -> bool:
        return bool(self.stripe_secret_key)

    # ── Testing mode ────────────────────────────────────────────────────────
    # When True, ALL commercial/business restrictions are bypassed: plan quotas,
    # rate limits, concurrent-job caps, upload-size caps and length caps. Intended
    # ONLY for load/stress/functional testing. Default False so it can never be on
    # in production by accident — flip it with TESTING_MODE=true in the env.
    testing_mode: bool = False

    # Email — Resend is the primary transport when its key is set; SMTP is the
    # fallback. With neither configured, emails are logged but not sent.
    resend_api_key: str = ""
    email_from: str = "Faro <onboarding@resend.dev>"  # resend.dev works without domain setup

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
    # Public external base URL Twilio POSTs the inbound webhook to (scheme + host,
    # e.g. "https://app.faro.com"). Twilio computes X-Twilio-Signature over the
    # PUBLIC url; behind the frontend proxy / TLS termination the backend sees an
    # internal url (request.url) that will NOT match, so signature validation
    # would always 403. When set, this is the authoritative base for rebuilding
    # the signed url; empty falls back to X-Forwarded-* headers, then request.url.
    whatsapp_webhook_base_url: str = ""
    # Temporary stopgap: when true, the conversational bot skips the LLM and
    # replies with a fast, honest generic message (confirmations still execute
    # deterministically). Set while no hosted LLM is funded — the local model is
    # too slow for a real-time WhatsApp turn. Flip back to false once
    # ANTHROPIC_API_KEY has credit and the smart bot returns automatically.
    whatsapp_bot_generic_mode: bool = False

    # External APIs
    # When set, backend/ai/local_llm.py::get_local_llm_client() returns a real
    # Anthropic-backed client instead of the local Ollama shim — every AI
    # consumer (rag_service.py, chats.py, narrator.py, narrative_service.py,
    # configuration.py's data-quality diagnosis) goes through this one
    # factory, so setting/unsetting this key alone switches all of them.
    anthropic_api_key: str = ""
    # Model used when anthropic_api_key is set — deliberately the cheapest
    # tier, since these are high-volume, low-complexity completions (chat
    # replies, narrative summaries, data-quality blurbs), not the kind of
    # task that needs a frontier model.
    anthropic_model: str = "claude-haiku-4-5-20251001"
    voyageai_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_environment: str = ""
    pinecone_index: str = ""

    # Local LLM (replaces the paid Anthropic API for text generation — see
    # backend/ai/local_llm.py). Requires Ollama running locally with this model pulled.
    local_llm_base_url: str = "http://localhost:11434"
    local_llm_model: str = "deepseek-r1"

    # Accounting integrations (Alegra + Siigo)
    integrations_secret_key: str = ""
    alegra_base_url: str = "https://api.alegra.com/api/v1"
    siigo_base_url: str = "https://api.siigo.com/v1"

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