from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    secret_key: str
    database_url: str
    frontend_url: str

    # App metadata
    app_name: str = "ForecastPlatform"
    app_version: str = "1.0.0"

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

    # Quotas
    default_max_sessions: int = 20
    default_max_skus: int = 1000

    # Upload
    max_upload_size_mb: int = 200

    # SMTP
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""

    # External APIs
    anthropic_api_key: str = ""
    voyageai_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_environment: str = ""
    pinecone_index: str = ""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()