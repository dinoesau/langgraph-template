from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime settings loaded from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: Literal["development", "staging", "production", "dev", "prod"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    HOST: str = Field(default="127.0.0.1", min_length=1)
    PORT: int = Field(default=8000, ge=1, le=65535)
    POSTGRES_URI: str = "postgresql://postgres:postgres@localhost:5432/postgres"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "postgres"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    DEFAULT_LLM_MODEL: str = "default-openai"
    MAX_LLM_CALL_RETRIES: int = Field(default=3, ge=1, le=10)
    LLM_TOTAL_TIMEOUT: float = Field(default=60.0, ge=1.0, le=600.0)
    CACHE_TTL_SECONDS: int = Field(default=60, ge=1, le=3600)
    VALKEY_HOST: str = ""
    VALKEY_PORT: int = Field(default=6379, ge=1, le=65535)
    LONG_TERM_MEMORY_COLLECTION_NAME: str = Field(default="longterm_memory", min_length=1)
    LONG_TERM_MEMORY_EMBEDDER_MODEL: str = ""
    LONG_TERM_MEMORY_MODEL: str = ""
    LONG_TERM_MEMORY_TOP_K: int = Field(default=3, ge=1, le=20)


@lru_cache
def get_settings() -> Settings:
    """Return cached settings, validating the environment once."""
    return Settings()


settings = get_settings()
