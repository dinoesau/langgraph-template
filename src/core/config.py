"""Typed settings loaded once from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Proven configuration values, parsed once at the edge."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    # Empty keeps the loop ephemeral; set POSTGRES_URI to persist threads.
    postgres_uri: str = ""
    postgres_pool_max_size: int = Field(default=5, ge=1, le=50)
    # Semantic memory plus cache. Empty host keeps the local fallback.
    cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    valkey_host: str = ""
    # Model fallback (S3). Empty key means that provider is skipped.
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    default_llm_model: str = "default-openai"
    default_llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_llm_call_retries: int = Field(default=3, ge=1, le=10)
    llm_total_timeout_seconds: int = Field(default=60, ge=5, le=600)
    # Auth lifecycle (S4). Empty secret fails closed at token creation.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_user_expire_minutes: int = Field(default=30, ge=5, le=1440)
    jwt_session_expire_minutes: int = Field(default=60, ge=5, le=1440)


@lru_cache
def get_settings() -> Settings:
    """Return cached settings so env parsing happens exactly once."""
    return Settings()
