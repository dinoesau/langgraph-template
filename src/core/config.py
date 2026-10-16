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


@lru_cache
def get_settings() -> Settings:
    """Return cached settings, validating the environment once."""
    return Settings()


settings = get_settings()
