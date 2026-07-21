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

    # Upload
    max_upload_size_mb: int = 200

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