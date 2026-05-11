import os
from enum import Enum
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# CORS contract:
# - Local/test: ALLOW_ORIGIN may default to "*".
# - Staging/production: ALLOW_ORIGIN is REQUIRED, must not contain "*", and may
#   be a comma-separated list (e.g. "https://app.example.com,https://admin.example.com").
# Enforced in Settings.validate_env_config below; example values live in
# .env.staging.exemple and .env.production.exemple.
class Environment(str, Enum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


def _get_env_file() -> str:
    env = os.getenv("ENV", "local").strip()
    return f".env.{env}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_get_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ---- Core ----
    ENV: Environment = Environment.LOCAL

    # ---- CRM Database ----
    CRM_DATABASE_URL: str  # postgresql+asyncpg://...
    CRM_DB_POOL_SIZE: int = 20
    CRM_DB_MAX_OVERFLOW: int = 10
    CRM_DB_POOL_RECYCLE: int = 3600  # seconds — recycle connections after 1 hour
    CRM_DB_POOL_TIMEOUT: int = 30  # seconds — wait for available connection
    CRM_DB_SSL: bool = False  # enable SSL/TLS for database connection

    # ---- LOCAL Database ----
    LOCAL_DATABASE_URL: str  # postgresql+asyncpg://...
    LOCAL_DB_POOL_SIZE: int = 20
    LOCAL_DB_MAX_OVERFLOW: int = 10
    LOCAL_DB_POOL_RECYCLE: int = 3600  # seconds — recycle connections after 1 hour
    LOCAL_DB_POOL_TIMEOUT: int = 30  # seconds — wait for available connection
    LOCAL_DB_SSL: bool = False  # enable SSL/TLS for database connection
    # ---- NATS ----
    NATS_URL: str = ""

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_USERNAME: str = ""
    REDIS_PASSWORD: str = ""

    # ---- CORS ----
    ALLOW_ORIGIN: str = "*"

    LOGGING_TOKEN: str = ""
    LOGGING_TRACES_URL: str = ""
    LOGGING_LOGS_URL: str = ""
    LOGGING_METRICS_URL: str = ""

    @model_validator(mode="after")
    def validate_env_config(self) -> "Settings":
        if self.ENV != Environment.LOCAL:
            origins = [o.strip() for o in self.ALLOW_ORIGIN.split(",") if o.strip()]
            assert origins, (
                "ALLOW_ORIGIN is required when ENV is not local; "
                "set it explicitly in .env.{env} (no implicit fallback to '*')"
            )
            assert (
                "*" not in self.ALLOW_ORIGIN
            ), "Wildcard CORS not allowed in staging/production"
            assert (
                self.CRM_DATABASE_URL.strip()
            ), "CRM_DATABASE_URL is required when ENV is not local"
            assert (
                self.LOCAL_DATABASE_URL.strip()
            ), "LOCAL_DATABASE_URL is required when ENV is not local"
        if self.ENV in (Environment.STAGING, Environment.PRODUCTION):
            assert self.NATS_URL.strip(), "NATS_URL is required in staging/production"
        if self.ENV == Environment.PRODUCTION:
            assert self.LOGGING_TOKEN, "COCKPIT_TOKEN required for staging/production"
            assert (
                self.LOGGING_LOGS_URL
            ), "COCKPIT_LOGS_URL required for staging/production"
            assert (
                self.LOGGING_METRICS_URL
            ), "COCKPIT_METRICS_URL required for staging/production"
        return self


settings = Settings()
