from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Business limits live in payment_policies, not here."""

    model_config = SettingsConfigDict(env_prefix="POPS_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://postgres@127.0.0.1:5432/pops"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # inline: run the pipeline in the request (dev/tests); celery: enqueue
    pipeline_mode: Literal["inline", "celery"] = "inline"

    jwt_secret: str = "dev-only-change-me"  # noqa: S105
    jwt_ttl_seconds: int = 8 * 3600
    evidence_url_ttl_seconds: int = 300

    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: str = "./var/evidence"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "pops-evidence"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    max_upload_bytes: int = 10 * 1024 * 1024

    # extraction readers: ollama | fake ; ocr: tesseract | none
    vlm_backend: Literal["ollama", "fake"] = "ollama"
    # Local daemon: http://127.0.0.1:11434 (no key). Ollama Cloud: https://ollama.com + API key.
    ollama_url: str = "https://ollama.com"
    ollama_api_key: str | None = None
    ollama_model: str = "qwen3-vl:235b"
    ollama_timeout_seconds: float = 120.0
    ocr_backend: Literal["tesseract", "none"] = "tesseract"

    cognify_webhook_url: str | None = None
    cognify_webhook_secret: str = "dev-webhook-secret"  # noqa: S105

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
