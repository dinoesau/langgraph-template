"""Model registry: ordered factories, one entry per provider.

Order defines fallback order. Entries with missing keys are skipped so a
fresh clone without keys never fails at import time. The starting model
comes from ``DEFAULT_LLM_MODEL``; fallback reorders circularly per call.
"""

from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel

from core.config import Settings


@dataclass(frozen=True)
class ModelEntry:
    """One registry row: name plus lazy factory, never a live client."""

    name: str
    build: Callable[[], BaseChatModel]


def build_registry(settings: Settings) -> list[ModelEntry]:
    """Return available models in preference order, skipping missing keys."""
    entries: list[ModelEntry] = []
    if settings.openai_api_key.strip():
        entries.append(
            ModelEntry(
                name="default-openai",
                build=lambda: _openai(settings),
            )
        )
    if settings.anthropic_api_key.strip():
        entries.append(
            ModelEntry(
                name="fallback-anthropic",
                build=lambda: _anthropic(settings),
            )
        )
    if settings.google_api_key.strip():
        entries.append(
            ModelEntry(
                name="fallback-google",
                build=lambda: _google(settings),
            )
        )
    return entries


def ordered_registry(registry: list[ModelEntry], start: str) -> list[ModelEntry]:
    """Reorder circularly so the starting model goes first, once only."""
    if not registry:
        return []
    names = [entry.name for entry in registry]
    try:
        index = names.index(start)
    except ValueError:
        return list(registry)
    return registry[index:] + registry[:index]


def _openai(settings: Settings) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    params: dict[str, object] = {
        "model": "gpt-4o-mini",
        "api_key": SecretStr(settings.openai_api_key),
        "temperature": settings.default_llm_temperature,
        "max_retries": settings.max_llm_call_retries,
        "timeout": 20,
    }
    return ChatOpenAI(**params)  # type: ignore[arg-type]


def _anthropic(settings: Settings) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic
    from pydantic import SecretStr

    params: dict[str, object] = {
        "model": "claude-3-5-haiku-latest",
        "api_key": SecretStr(settings.anthropic_api_key),
        "temperature": settings.default_llm_temperature,
        "max_retries": settings.max_llm_call_retries,
        "timeout": 20,
    }
    return ChatAnthropic(**params)  # type: ignore[arg-type]


def _google(settings: Settings) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    params: dict[str, object] = {
        "model": "gemini-2.5-flash",
        "google_api_key": settings.google_api_key,
        "temperature": settings.default_llm_temperature,
        "max_retries": settings.max_llm_call_retries,
        "timeout": 20,
    }
    return ChatGoogleGenerativeAI(**params)
