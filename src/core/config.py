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
    # S2b semantic memory plus cache. Empty host keeps the local fallback.
    cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    long_term_memory_collection_name: str = "longterm_memory"
    long_term_memory_model: str = ""
    long_term_memory_embedder_model: str = ""
    memory_distance_max: float = Field(default=2.0, ge=0.0, le=2.0)
    valkey_host: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return cached settings so env parsing happens exactly once."""
    return Settings()
