from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-change-me"  # noqa: S105
DEV_WEBHOOK_SECRET = "dev-webhook-secret"  # noqa: S105


class Settings(BaseSettings):
    """Runtime configuration. Business limits live in payment_policies, not here.

    Defaults are production-safe: dev conveniences (dev login, fake readers, skipped malware
    scan) only switch on when POPS_ENV is explicitly dev or test.
    """

    model_config = SettingsConfigDict(env_prefix="POPS_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "prod"
    database_url: str = "postgresql+psycopg://postgres@127.0.0.1:5432/pops"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # inline: run the pipeline in the request (dev/tests); celery: enqueue
    pipeline_mode: Literal["inline", "celery"] = "celery"
    pipeline_max_attempts: int = 3
    stuck_after_seconds: int = 300

    jwt_secret: str = DEV_JWT_SECRET
    jwt_ttl_seconds: int = 8 * 3600
    evidence_url_ttl_seconds: int = 300

    # OIDC (Cognify identity). When set, RS256 tokens from this issuer are accepted.
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_role_claim: str = "pops_role"
    oidc_org_claim: str = "pops_org"
    oidc_require_mfa_for_finance: bool = True

    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: str = "./var/evidence"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "pops-evidence"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    max_upload_bytes: int = 10 * 1024 * 1024

    # malware scanning via clamd (INSTREAM). Required in prod.
    clamav_host: str | None = None
    clamav_port: int = 3310
    clamav_timeout_seconds: float = 30.0

    # upload rate limits (per authenticated caller, and per claimant across callers)
    rate_limit_uploads_per_minute: int = 30
    rate_limit_claims_per_claimant_per_day: int = 20

    # extraction readers: ollama | fake ; ocr: tesseract | none
    vlm_backend: Literal["ollama", "fake"] = "ollama"
    # Shadow mode (blueprint phase 4): advisory AI proposals recorded beside claims and scored
    # against human decisions. Proposals never touch claim state, so this is safe to flip on;
    # flip it off to stop recording. The AI proposer additionally obeys the guardrail kill switch.
    shadow_mode_enabled: bool = False
    # Local daemon: http://127.0.0.1:11434 (no key). Ollama Cloud: https://ollama.com + API key.
    ollama_url: str = "https://ollama.com"
    ollama_api_key: str | None = None
    ollama_model: str = "qwen3-vl:235b"
    ollama_timeout_seconds: float = 120.0
    ocr_backend: Literal["tesseract", "none"] = "tesseract"
    ocr_languages: str = "eng"
    # Phase 0 calibration without code changes: JSON files that extend or override the built-in
    # provider layouts and statement CSV profiles (see docs/configuration.md)
    provider_templates_file: str | None = None
    statement_profiles_file: str | None = None

    cognify_webhook_url: str | None = None
    cognify_webhook_secret: str = DEV_WEBHOOK_SECRET

    cors_origins: list[str] = ["http://localhost:5173"]

    # observability
    log_json: bool = True
    sentry_dsn: str | None = None
    otel_endpoint: str | None = None

    @property
    def is_dev(self) -> bool:
        return self.env in ("dev", "test")


class ConfigError(RuntimeError):
    pass


def validate_for_startup(s: Settings) -> None:
    """Refuse to boot a production process with dev-only settings."""
    if s.is_dev:
        return
    problems = []
    if s.jwt_secret == DEV_JWT_SECRET or len(s.jwt_secret) < 32:
        problems.append("POPS_JWT_SECRET must be set to a random value of 32+ characters")
    if s.cognify_webhook_url and s.cognify_webhook_secret == DEV_WEBHOOK_SECRET:
        problems.append("POPS_COGNIFY_WEBHOOK_SECRET must be set")
    if s.vlm_backend == "fake":
        problems.append("POPS_VLM_BACKEND=fake is for tests and demos only")
    if not s.clamav_host:
        problems.append("POPS_CLAMAV_HOST is required: uploads must be malware-scanned")
    if s.pipeline_mode == "inline":
        problems.append("POPS_PIPELINE_MODE=inline is for development only")
    if problems:
        raise ConfigError("unsafe production configuration:\n- " + "\n- ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
