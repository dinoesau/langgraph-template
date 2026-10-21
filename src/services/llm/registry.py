"""Model registry: ordered factories, one entry per provider.

Order defines fallback order. Entries with missing keys are skipped so a
fresh clone without keys never fails at import time. The starting model
comes from ``DEFAULT_LLM_MODEL``; fallback reorders circularly per call.
Raw models run a single attempt each: the service loop owns per-model
retries, backoff, and the total budget, so provider-level retries stay off.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from langchain_core.language_models.chat_models import BaseChatModel

from core.config import Settings

_T = TypeVar("_T")

PROVIDER_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class ModelEntry:
    """One registry row: name plus lazy factory, never a live client."""

    name: str
    build: Callable[[], BaseChatModel]


def rotate(items: list[_T], start_index: int) -> list[_T]:
    """Reorder circularly so the item at the index goes first, once only."""
    if not items:
        return []
    index = start_index % len(items)
    return items[index:] + items[:index]


def build_registry(settings: Settings) -> list[ModelEntry]:
    """Return available models in preference order, skipping missing keys."""
    candidates: list[tuple[str, str, Callable[[Settings], BaseChatModel]]] = [
        ("default-openai", settings.openai_api_key, _openai),
        ("fallback-anthropic", settings.anthropic_api_key, _anthropic),
        ("fallback-google", settings.google_api_key, _google),
    ]
    entries: list[ModelEntry] = []
    for name, key, factory in candidates:
        if key.strip():
            entries.append(ModelEntry(name=name, build=_lazy(factory, settings)))
    return entries


def ordered_registry(registry: list[ModelEntry], start: str) -> list[ModelEntry]:
    """Reorder circularly so the starting model goes first, once only."""
    names = [entry.name for entry in registry]
    try:
        return rotate(registry, names.index(start))
    except ValueError:
        return list(registry)


def _lazy(
    factory: Callable[[Settings], BaseChatModel], settings: Settings
) -> Callable[[], BaseChatModel]:
    """Freeze one factory plus settings into a no-arg builder for the entry."""

    def build() -> BaseChatModel:
        return factory(settings)

    return build


def _base_params(settings: Settings) -> dict[str, object]:
    """Shared provider knobs; single attempt so the service loop owns retries."""
    return {
        "temperature": settings.default_llm_temperature,
        "max_retries": 1,
        "timeout": PROVIDER_TIMEOUT_SECONDS,
    }


def _openai(settings: Settings) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    params: dict[str, object] = {
        **_base_params(settings),
        "model": "gpt-4o-mini",
        "api_key": SecretStr(settings.openai_api_key),
    }
    return ChatOpenAI(**params)  # type: ignore[arg-type]


def _anthropic(settings: Settings) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic
    from pydantic import SecretStr

    params = {
        **_base_params(settings),
        "model": "claude-3-5-haiku-latest",
        "api_key": SecretStr(settings.anthropic_api_key),
    }
    return ChatAnthropic(**params)  # type: ignore[arg-type]


def _google(settings: Settings) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    params = {
        **_base_params(settings),
        "model": "gemini-2.5-flash",
        "google_api_key": settings.google_api_key,
    }
    return ChatGoogleGenerativeAI(**params)
